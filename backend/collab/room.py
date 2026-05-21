"""collab.room —— 房间模型 + RoomManager + WebSocket Hub + 规划中断桥接。

核心流程：
1. 发起人 POST /room/create → RoomManager.create_room() → 返回 room_id
2. 任何人 WS /ws/{room_id} → RoomManager.join() → 广播 member_joined
3. 任何人发 constraint → Room.add_constraint() → 广播 → 中断当前规划 → 合并约束 → 重新规划
4. 任何人发 vote → Room.update_vote() → 广播 → 踩触发重规划
5. owner 发 confirm → Room.confirm() → 广播确认结果

设计取舍：
- 单进程 dict 存储（与 InMemoryRepository 一致，Demo 够用）
- asyncio.Lock per room 保证约束串行处理
- planning_task 用 asyncio.Task.cancel() 实现中断
- 规划事件通过回调广播给所有 WS 连接（复用现有 SseEvent 格式）
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import WebSocket


# ============================================================
# 数据模型
# ============================================================


@dataclass
class Member:
    """房间成员。"""
    user_id: str
    nickname: str
    role: str  # "owner" | "participant"
    ws: Optional[WebSocket] = None
    joined_at: float = field(default_factory=time.time)


@dataclass
class Constraint:
    """一条约束（来自某个成员的文本输入或投票翻译）。"""
    user_id: str
    text: str
    source: str  # "text" | "vote_dislike"
    timestamp: float = field(default_factory=time.time)
    parsed_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class Room:
    """协作房间。"""
    room_id: str
    owner_id: str
    members: dict[str, Member] = field(default_factory=dict)
    constraints: list[Constraint] = field(default_factory=list)
    votes: dict[int, dict[str, str]] = field(default_factory=dict)  # stage_idx → {user_id: "like"|"dislike"}
    current_intent_dict: Optional[dict[str, Any]] = None
    current_itinerary_dict: Optional[dict[str, Any]] = None
    planning_task: Optional[asyncio.Task] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    created_at: float = field(default_factory=time.time)
    # 被赞锁定的 stage index 集合（重规划时保留这些段）
    locked_stages: set[int] = field(default_factory=set)

    @property
    def member_list(self) -> list[dict[str, Any]]:
        return [
            {
                "user_id": m.user_id,
                "nickname": m.nickname,
                "role": m.role,
                "online": m.ws is not None,
            }
            for m in self.members.values()
        ]

    @property
    def constraint_list(self) -> list[dict[str, Any]]:
        return [
            {
                "user_id": c.user_id,
                "text": c.text,
                "source": c.source,
                "timestamp": c.timestamp,
            }
            for c in self.constraints
        ]

    def get_state_snapshot(self) -> dict[str, Any]:
        """全量状态快照（新成员加入时推送）。"""
        return {
            "type": "room_state",
            "room_id": self.room_id,
            "owner_id": self.owner_id,
            "members": self.member_list,
            "constraints": self.constraint_list,
            "votes": {str(k): v for k, v in self.votes.items()},
            "itinerary": self.current_itinerary_dict,
            "intent": self.current_intent_dict,
            "locked_stages": list(self.locked_stages),
        }


# ============================================================
# RoomManager（单例）
# ============================================================


class RoomManager:
    """房间生命周期管理 + WebSocket 广播。"""

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def create_room(self, owner_id: str, nickname: str = "发起人") -> Room:
        """创建新房间。"""
        room_id = self._generate_room_id()
        room = Room(room_id=room_id, owner_id=owner_id)
        room.members[owner_id] = Member(
            user_id=owner_id, nickname=nickname, role="owner"
        )
        self._rooms[room_id] = room
        return room

    def get_room(self, room_id: str) -> Optional[Room]:
        return self._rooms.get(room_id)

    def delete_room(self, room_id: str) -> None:
        room = self._rooms.pop(room_id, None)
        if room and room.planning_task and not room.planning_task.done():
            room.planning_task.cancel()

    def list_rooms(self) -> list[dict[str, Any]]:
        return [
            {
                "room_id": r.room_id,
                "owner_id": r.owner_id,
                "member_count": len(r.members),
                "created_at": r.created_at,
            }
            for r in self._rooms.values()
        ]

    async def join(
        self, room: Room, user_id: str, nickname: str, ws: WebSocket
    ) -> None:
        """成员加入房间。"""
        if user_id in room.members:
            # 重连：更新 ws
            room.members[user_id].ws = ws
        else:
            room.members[user_id] = Member(
                user_id=user_id, nickname=nickname, role="participant", ws=ws
            )
        # 给新成员推全量状态
        await self._send(ws, room.get_state_snapshot())
        # 广播 member_joined
        await self.broadcast(room, {
            "type": "member_joined",
            "user_id": user_id,
            "nickname": nickname,
            "role": room.members[user_id].role,
        }, exclude=user_id)

    async def leave(self, room: Room, user_id: str) -> None:
        """成员离开（WS 断开）。"""
        member = room.members.get(user_id)
        if member:
            member.ws = None
        await self.broadcast(room, {
            "type": "member_left",
            "user_id": user_id,
        })
        # 如果所有人都离线，5 分钟后清理（简化：直接标记，不做定时器）
        all_offline = all(m.ws is None for m in room.members.values())
        if all_offline:
            # Demo 场景不做延迟清理，直接保留房间（评委可能刷新页面）
            pass

    async def add_constraint(
        self, room: Room, user_id: str, text: str, source: str = "text"
    ) -> None:
        """添加约束 → 广播 → 中断当前规划 → 合并约束 → 重新规划。"""
        async with room.lock:
            constraint = Constraint(
                user_id=user_id, text=text, source=source
            )
            room.constraints.append(constraint)

            # 广播约束
            nickname = room.members.get(user_id, Member(user_id=user_id, nickname=user_id, role="participant")).nickname
            await self.broadcast(room, {
                "type": "constraint_added",
                "user_id": user_id,
                "nickname": nickname,
                "text": text,
                "source": source,
                "timestamp": constraint.timestamp,
            })

            # 中断当前规划
            if room.planning_task and not room.planning_task.done():
                room.planning_task.cancel()
                try:
                    await room.planning_task
                except (asyncio.CancelledError, Exception):
                    pass
                await self.broadcast(room, {
                    "type": "planning_aborted",
                    "reason": "new_constraint",
                    "by_user": user_id,
                })

            # 触发重规划
            await self._trigger_replan(room, trigger_user=user_id, trigger_reason="constraint_added")

    async def update_vote(
        self, room: Room, user_id: str, stage_index: int, action: str
    ) -> None:
        """更新投票 → 广播 → 踩触发重规划。"""
        async with room.lock:
            if stage_index not in room.votes:
                room.votes[stage_index] = {}
            room.votes[stage_index][user_id] = action

            # 更新锁定集合
            if action == "like":
                room.locked_stages.add(stage_index)
            elif action == "dislike":
                room.locked_stages.discard(stage_index)

            await self.broadcast(room, {
                "type": "vote_updated",
                "stage_index": stage_index,
                "user_id": user_id,
                "action": action,
                "votes": room.votes.get(stage_index, {}),
                "locked_stages": list(room.locked_stages),
            })

            # 踩 → 触发重规划
            if action == "dislike":
                # 翻译投票为约束文本
                stage_title = self._get_stage_title(room, stage_index)
                constraint_text = f"不满意第 {stage_index + 1} 段「{stage_title}」，请换一个"
                constraint = Constraint(
                    user_id=user_id, text=constraint_text, source="vote_dislike"
                )
                room.constraints.append(constraint)

                # 中断 + 重规划
                if room.planning_task and not room.planning_task.done():
                    room.planning_task.cancel()
                    try:
                        await room.planning_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    await self.broadcast(room, {
                        "type": "planning_aborted",
                        "reason": "vote_dislike",
                        "by_user": user_id,
                    })

                await self._trigger_replan(room, trigger_user=user_id, trigger_reason="vote_dislike")

    async def broadcast(
        self, room: Room, message: dict[str, Any], *, exclude: str | None = None
    ) -> None:
        """广播消息给房间内所有在线成员。"""
        disconnected: list[str] = []
        for uid, member in room.members.items():
            if uid == exclude:
                continue
            if member.ws is None:
                continue
            try:
                await member.ws.send_json(message)
            except Exception:  # noqa: BLE001
                disconnected.append(uid)
        # 清理断连的
        for uid in disconnected:
            if uid in room.members:
                room.members[uid].ws = None

    # ============================================================
    # 内部方法
    # ============================================================

    async def _trigger_replan(
        self, room: Room, *, trigger_user: str, trigger_reason: str
    ) -> None:
        """合并约束池 → 重新规划 → 广播规划事件。"""
        await self.broadcast(room, {
            "type": "planning_started",
            "trigger": trigger_reason,
            "trigger_user": trigger_user,
            "constraints_count": len(room.constraints),
        })

        # 创建规划任务
        room.planning_task = asyncio.create_task(
            self._run_planning(room)
        )

    async def _run_planning(self, room: Room) -> None:
        """执行规划并广播事件。

        策略：
        1. 合并所有约束文本为一个 feedback 字符串
        2. 如果有 current_intent → 用 refiner 合并约束
        3. 如果没有 current_intent → 用第一条约束作为初始输入走 planner
        4. 规划事件逐条广播给所有成员
        """
        try:
            # 合并约束为 feedback 文本
            merged_feedback = self._merge_constraints_text(room)

            # 决定走哪条路径
            if room.current_intent_dict is not None:
                # 有基线 intent → refiner 合并约束后重规划
                await self._replan_with_refiner(room, merged_feedback)
            else:
                # 无基线 → 用约束文本作为初始输入
                initial_input = merged_feedback or "帮我规划一个下午"
                await self._plan_fresh(room, initial_input)

        except asyncio.CancelledError:
            # 被新约束中断，正常退出
            raise
        except Exception as e:  # noqa: BLE001
            await self.broadcast(room, {
                "type": "planning_event",
                "event": {
                    "type": "stream_error",
                    "seq": 0,
                    "payload": {"reason": "planning_failed", "detail": str(e)[:200]},
                    "timestamp_ms": int(time.time() * 1000),
                },
            })

    async def _replan_with_refiner(self, room: Room, feedback: str) -> None:
        """用 refiner 合并约束后重新规划。"""
        from schemas.intent import IntentExtraction
        from agent.refiner import refine_intent

        # 还原 intent
        intent = IntentExtraction.model_validate(room.current_intent_dict)

        # refiner 合并
        result = refine_intent(intent, feedback)
        refined_intent = result.refined_intent
        room.current_intent_dict = refined_intent.model_dump()

        # 广播 refinement 结果
        await self.broadcast(room, {
            "type": "planning_event",
            "event": {
                "type": "refinement_done",
                "seq": 0,
                "payload": {
                    "refined_intent": refined_intent.model_dump(),
                    "changed_fields": result.changed_fields,
                    "refiner_note": result.refiner_note,
                },
                "timestamp_ms": int(time.time() * 1000),
            },
        })

        # 用 refined intent 重新规划
        await self._run_planner_and_broadcast(room, refined_intent)

    async def _plan_fresh(self, room: Room, user_input: str) -> None:
        """无基线时走完整规划路径。"""
        # 尝试用 LangGraph 或 ReAct agent
        try:
            from agent.graph.sse_adapter import run_graph_stream
            from agent.graph.build import get_compiled_graph
            get_compiled_graph()

            user_id = room.owner_id
            session_id = f"collab_{room.room_id}"

            async for event in run_graph_stream(
                user_input=user_input,
                session_id=session_id,
                user_id=user_id,
            ):
                # 广播每条规划事件
                await self.broadcast(room, {
                    "type": "planning_event",
                    "event": event.model_dump(),
                })
                # 捕获 itinerary_ready 和 intent_parsed
                if event.type.value == "itinerary_ready":
                    room.current_itinerary_dict = event.payload
                elif event.type.value == "intent_parsed":
                    room.current_intent_dict = event.payload

        except (ImportError, Exception):
            # LangGraph 不可用 → 走 rule planner
            await self._run_rule_planner_and_broadcast(room, user_input)

    async def _run_planner_and_broadcast(
        self, room: Room, intent: Any
    ) -> None:
        """用 intent 跑规划并广播事件。"""
        from schemas.intent import IntentExtraction
        from schemas.sse import SseEvent, SseEventType

        # 广播 intent_parsed
        await self.broadcast(room, {
            "type": "planning_event",
            "event": {
                "type": "intent_parsed",
                "seq": 0,
                "payload": intent.model_dump() if hasattr(intent, "model_dump") else intent,
                "timestamp_ms": int(time.time() * 1000),
            },
        })

        # 尝试 LangGraph
        try:
            from agent.graph.sse_adapter import run_graph_stream
            from agent.graph.build import get_compiled_graph
            get_compiled_graph()

            session_id = f"collab_{room.room_id}"
            # 构造用户输入（从 intent 的 raw_input 取）
            raw_input = intent.raw_input if hasattr(intent, "raw_input") else "重新规划"

            async for event in run_graph_stream(
                user_input=raw_input,
                session_id=session_id,
                user_id=room.owner_id,
            ):
                await self.broadcast(room, {
                    "type": "planning_event",
                    "event": event.model_dump(),
                })
                if event.type.value == "itinerary_ready":
                    room.current_itinerary_dict = event.payload
                # 检查是否被取消
                await asyncio.sleep(0)  # yield control

        except (ImportError, Exception) as e:
            # fallback: rule planner
            await self._run_rule_planner_fallback(room, intent)

    async def _run_rule_planner_fallback(self, room: Room, intent: Any) -> None:
        """Rule planner 兜底。"""
        try:
            from agent.planner import plan_itinerary
            from agent.trace import Tracer

            tracer = Tracer()
            result = plan_itinerary(intent, tracer=tracer)

            # 广播 tracer 事件
            for record in tracer.records:
                await self.broadcast(room, {
                    "type": "planning_event",
                    "event": {
                        "type": record.type,
                        "seq": 0,
                        "payload": record.payload or {},
                        "timestamp_ms": int(record.timestamp * 1000),
                    },
                })
                await asyncio.sleep(0.05)  # 模拟流式节奏

            if result.success and result.itinerary:
                room.current_itinerary_dict = result.itinerary.model_dump()
                await self.broadcast(room, {
                    "type": "planning_event",
                    "event": {
                        "type": "itinerary_ready",
                        "seq": 0,
                        "payload": result.itinerary.model_dump(),
                        "timestamp_ms": int(time.time() * 1000),
                    },
                })
        except Exception as e:  # noqa: BLE001
            await self.broadcast(room, {
                "type": "planning_event",
                "event": {
                    "type": "stream_error",
                    "seq": 0,
                    "payload": {"reason": "rule_planner_failed", "detail": str(e)[:200]},
                    "timestamp_ms": int(time.time() * 1000),
                },
            })

    async def _run_rule_planner_and_broadcast(self, room: Room, user_input: str) -> None:
        """无 LangGraph 时用 rule planner 兜底（需要先解析 intent）。"""
        try:
            from agent.intent_parser import parse_intent
            from agent.planner import plan_itinerary
            from agent.trace import Tracer

            intent = parse_intent(user_input)
            room.current_intent_dict = intent.model_dump()

            await self.broadcast(room, {
                "type": "planning_event",
                "event": {
                    "type": "intent_parsed",
                    "seq": 0,
                    "payload": intent.model_dump(),
                    "timestamp_ms": int(time.time() * 1000),
                },
            })

            tracer = Tracer()
            result = plan_itinerary(intent, tracer=tracer)

            for record in tracer.records:
                await self.broadcast(room, {
                    "type": "planning_event",
                    "event": {
                        "type": record.type,
                        "seq": 0,
                        "payload": record.payload or {},
                        "timestamp_ms": int(record.timestamp * 1000),
                    },
                })
                await asyncio.sleep(0.05)

            if result.success and result.itinerary:
                room.current_itinerary_dict = result.itinerary.model_dump()
                await self.broadcast(room, {
                    "type": "planning_event",
                    "event": {
                        "type": "itinerary_ready",
                        "seq": 0,
                        "payload": result.itinerary.model_dump(),
                        "timestamp_ms": int(time.time() * 1000),
                    },
                })

            # 广播 done
            await self.broadcast(room, {
                "type": "planning_event",
                "event": {
                    "type": "done",
                    "seq": 0,
                    "payload": {},
                    "timestamp_ms": int(time.time() * 1000),
                },
            })

        except Exception as e:  # noqa: BLE001
            await self.broadcast(room, {
                "type": "planning_event",
                "event": {
                    "type": "stream_error",
                    "seq": 0,
                    "payload": {"reason": "fresh_plan_failed", "detail": str(e)[:200]},
                    "timestamp_ms": int(time.time() * 1000),
                },
            })

    def _merge_constraints_text(self, room: Room) -> str:
        """把约束池合并为一段 feedback 文本（喂给 refiner）。"""
        if not room.constraints:
            return ""
        # 取最近 5 条约束（避免 token 爆炸）
        recent = room.constraints[-5:]
        parts = []
        for c in recent:
            nickname = room.members.get(c.user_id, Member(user_id=c.user_id, nickname=c.user_id, role="participant")).nickname
            parts.append(f"{nickname}说：{c.text}")
        return "；".join(parts)

    def _get_stage_title(self, room: Room, stage_index: int) -> str:
        """从当前行程中取某段的标题。"""
        if not room.current_itinerary_dict:
            return f"第 {stage_index + 1} 段"
        stages = room.current_itinerary_dict.get("stages", [])
        if 0 <= stage_index < len(stages):
            return stages[stage_index].get("title", f"第 {stage_index + 1} 段")
        return f"第 {stage_index + 1} 段"

    @staticmethod
    def _generate_room_id() -> str:
        """生成 6 位短 room_id（URL 友好）。"""
        raw = uuid.uuid4().hex
        return hashlib.sha1(raw.encode()).hexdigest()[:6]

    async def _send(self, ws: WebSocket, message: dict[str, Any]) -> None:
        """安全发送单条消息。"""
        try:
            await ws.send_json(message)
        except Exception:  # noqa: BLE001
            pass


# ============================================================
# 单例
# ============================================================

_manager: Optional[RoomManager] = None


def get_room_manager() -> RoomManager:
    global _manager
    if _manager is None:
        _manager = RoomManager()
    return _manager
