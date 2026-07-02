"""collab.room —— 房间模型 + RoomManager + WebSocket Hub + 规划中断桥接。

核心流程：
1. 发起人 POST /room/create → RoomManager.create_room() → 返回 room_id
2. 任何人 WS /ws/{room_id} → RoomManager.join() → 广播 member_joined
3. 任何人发 constraint（自由打字）→ Room.add_constraint() → 先过统一路由脑子
   route_turn 判义务（ADR-0013 决策 7，"房间路由同权"）→ 归名广播给全员 + 按义务
   分流：feedback→合并约束重新规划 / planning→全新规划 / 其余→气泡广播，不动方案
4. 任何人发 vote → Room.update_vote() → 广播 → 踩触发重规划（暂未接路由，见 F-5）
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
    session_id: Optional[str] = None
    members: dict[str, Member] = field(default_factory=dict)
    constraints: list[Constraint] = field(default_factory=list)
    votes: dict[int, dict[str, str]] = field(default_factory=dict)  # stage_idx → {user_id: "like"|"dislike"}
    current_intent_dict: Optional[dict[str, Any]] = None
    current_itinerary_dict: Optional[dict[str, Any]] = None
    previous_itinerary_dict: Optional[dict[str, Any]] = None
    # 规划过程事件历史（用于新成员加入时同步 ToolTracePanel）
    planning_events_history: list[dict[str, Any]] = field(default_factory=list)
    # 对话历史（用于新成员加入时同步 ChatPanel）
    chat_messages: list[dict[str, Any]] = field(default_factory=list)
    # 前端主 store 快照（用于同步新增的 UI 组件状态）
    chat_state_snapshot: Optional[dict[str, Any]] = None
    # LLM 上下文历史（ModelMessage 格式，重规划时喂给 LLM 保持完整上下文）
    llm_context_messages: list[Any] = field(default_factory=list)
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
            "previous_itinerary": self.previous_itinerary_dict,
            "intent": self.current_intent_dict,
            "locked_stages": list(self.locked_stages),
            "planning_events": self.planning_events_history,
            "chat_messages": self.chat_messages,
            "chat_state": self.chat_state_snapshot,
            "planning_active": self.planning_task is not None and not self.planning_task.done(),
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
        """成员自由打字入口——先过统一路由脑子判义务,再房内分发（ADR-0013 决策 7）。

        病灶（治的是这个）：改造前，任何自由打字都被当成"约束"无条件塞进
        `room.constraints` 并触发全量重排——成员打"哈哈好期待"也会硬改方案。
        主聊天在 ADR-0011（一脑三壳）后已是"任何输入先过路由脑子，判出义务再分发"，
        房间此前是唯一还在裸接文本、不经判定直连重排的入口，本函数补上这层薄壳。

        义务分发表（route_turn 的 RouteKind → 房内动作）：
        - feedback              → 现有约束池 + 重排路径（原样保留；诉求台账是 F-2/F-5
                                   的事，本步不建，`room.constraints` 仍是唯一台账）
        - planning               → 全新规划（`_trigger_fresh_plan`，与单人 `_plan_fresh`
                                   同款一次性 session_id；**不**进约束池、不经 refiner
                                   合并——这是一句完整规划请求，不是"追加约束"）
        - 其余（chitchat/emotional/meta/off_topic/ambiguous，decision 必然非空）
                                 → 气泡广播：把 RouterDecision 原样以 `chitchat_reply`
                                   事件推给全员（复用主聊天已有的事件形状，前端
                                   `handleEvent` 的 `chitchat_reply` case 零改动即可渲染）；
                                   不碰方案、不碰约束池、不中断在跑的规划任务。
                                   安全婉拒（off_topic + 注入防御）与澄清引导（ambiguous +
                                   地板 chips）走的是同一条分支——它们只是 decision 内容
                                   不同，不需要单独判支。

        归名：无论义务判成什么，成员的原始发言都无条件走既有归名机制（nickname 前缀
        + `constraint_added` 事件广播 + 追加进 `room.chat_messages`）——"大家在同一个
        房间里，看得见彼此说了什么"是纯展示语义，与"这句话算不算一条可执行约束"是两回事；
        只有后者（是否写进 `room.constraints`，从而参与未来 `_merge_constraints_text`
        合并进 refiner）才按义务分流。这正是本函数要治的病：以前两者被绑死在同一段代码
        里无条件一起发生，现在拆开——展示照旧全量，约束池只收真正的 feedback。

        user_id 判断点（拍板）：route_turn 拿到的 `user_id` 是**发话成员自己的 id**
        （WS 连接携带的临时 id，owner 或 participant 皆然），不是 `room.owner_id`。
        room.py 里唯一按 user_id 查久层状态的是 confirm() 的记忆写入——那是"这趟行程
        记在谁头上"的语义，只有 owner 能触发，锚定 owner_id 合理。但这里 user_id 唯一
        的消费者是 route_turn 内部的 persona_qa（Layer 1.7，"我是谁/我的偏好"类问题）：
        它是**问话人自己是谁**的问答，不是"这个房间归谁"。若锚定 owner_id，会让
        participant 问"我的偏好是什么"时读到 owner 的画像/偏好数据答回去——这是身份
        误配（把 B 当成 A 回答）、还捎带泄漏 owner 的偏好给陌生参与者，不是"保连续性"，
        是真错误。而锚定发话者自己的 id：owner 问跟单人模式行为一致（同一个 id，画像
        自然连续，不需要特判）；participant 问则诚实降级为"默认画像，多用几次会记住你"
        —— ADR-0013 决策 6 边界本就明写"持久成员画像"不在本弧范围，这个诚实降级正是
        该边界的自然结果，不是缺陷。
        """
        async with room.lock:
            from agent.core.llm_client import get_llm_client
            from agent.routing.route_turn import route_turn

            outcome = route_turn(
                text, room.current_itinerary_dict, user_id, client=get_llm_client()
            )

            timestamp = time.time()
            nickname = room.members.get(
                user_id, Member(user_id=user_id, nickname=user_id, role="participant")
            ).nickname

            # 归名（既有机制维持，对三类义务一视同仁）
            room.chat_messages.append(
                {
                    "id": f"collab-{int(timestamp * 1000)}",
                    "role": "user",
                    "text": f"{nickname}：{text}",
                    "createdAt": int(timestamp * 1000),
                }
            )
            await self.broadcast(room, {
                "type": "constraint_added",
                "user_id": user_id,
                "nickname": nickname,
                "text": text,
                "source": source,
                "timestamp": timestamp,
            })

            if outcome.kind == "feedback":
                constraint = Constraint(
                    user_id=user_id, text=text, source=source, timestamp=timestamp
                )
                room.constraints.append(constraint)

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

                # 触发重规划（原样：合并约束池 → refiner）
                await self._trigger_replan(room, trigger_user=user_id, trigger_reason="constraint_added")

            elif outcome.kind == "planning":
                # 全新规划请求（如 canonical 场景文本 / "重新规划一个"）——不进约束池，
                # 不经 refiner 合并，直接同单人 `_plan_fresh` 路径重开一局。
                if room.planning_task and not room.planning_task.done():
                    room.planning_task.cancel()
                    try:
                        await room.planning_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    await self.broadcast(room, {
                        "type": "planning_aborted",
                        "reason": "planning_request",
                        "by_user": user_id,
                    })

                await self._trigger_fresh_plan(room, trigger_user=user_id, user_input=text)

            elif outcome.decision is not None:
                # chitchat / emotional / meta / off_topic / ambiguous → 气泡广播，
                # 不动方案、不动约束池、不中断在跑的规划任务。
                await self._broadcast_planning_event(room, {
                    "type": "chitchat_reply",
                    "seq": 0,
                    "payload": outcome.decision.model_dump(),
                    "timestamp_ms": int(timestamp * 1000),
                })
            # outcome.decision is None 且非 feedback/planning：route_turn 契约下不会
            # 发生（防御性兜底，不广播、不报错，避免未来级联变化时静默崩溃）。

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
                nickname = room.members.get(user_id, Member(user_id=user_id, nickname=user_id, role="participant")).nickname
                room.chat_messages.append(
                    {
                        "id": f"collab-{int(constraint.timestamp * 1000)}",
                        "role": "user",
                        "text": f"{nickname}：{constraint_text}",
                        "createdAt": int(constraint.timestamp * 1000),
                    }
                )
                await self.broadcast(room, {
                    "type": "constraint_added",
                    "user_id": user_id,
                    "nickname": nickname,
                    "text": constraint_text,
                    "source": "vote_dislike",
                    "timestamp": constraint.timestamp,
                })

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

    async def confirm(self, room: Room, user_id: str) -> None:
        """由房间发起人触发确认预约，并把确认阶段 SSE 事件广播给全员。

        确认流复用主 App 同一条 `_graph_confirm`（ADR-0012 决策 5：`_stub_confirm`
        专用分支已删除，分叉没有承重理由）——它只读 SESSION_STORE 投影 + 直调
        execute_finalize，不需要图 checkpoint；房间会话没有 checkpoint 时其内部
        `_writeback_graph_state` 会优雅跳过（见 graph_confirm.py 该函数 docstring）。
        confirm 阶段 narration 因此恒为快速规则文案（`execute_finalize_node` 的
        `defer_post_confirm_effects=True` 硬编码 `use_llm=False`），不再由 PLANNER_MODE
        控制真人味 LLM 文案——这是跟随主 App 已有设计的既定取舍，不是本次迁移新引入
        的降级（`mode` 形参随 `_stub_confirm` 一起退场，调用方 collab.py 同步简化）。
        """
        if user_id != room.owner_id:
            member = room.members.get(user_id)
            if member and member.ws is not None:
                await self._send(
                    member.ws,
                    {
                        "type": "error",
                        "message": "只有发起人可以确认预约",
                    },
                )
            return

        if room.planning_task and not room.planning_task.done():
            room.planning_task.cancel()
            try:
                await room.planning_task
            except (asyncio.CancelledError, Exception):
                pass

        room.planning_events_history.clear()
        room.chat_state_snapshot = None

        await self.broadcast(
            room,
            {
                "type": "planning_started",
                "trigger": "confirm",
                "trigger_user": user_id,
            },
        )

        session_id = getattr(room, "session_id", None) or f"collab_{room.room_id}"
        room.session_id = session_id

        from api._session_store import SESSION_STORE
        from api._streams.graph_confirm import _graph_confirm
        from api._streams.models import ChatConfirmRequest

        cached = SESSION_STORE.get(session_id, {})
        if room.current_itinerary_dict:
            SESSION_STORE[session_id] = {
                **cached,
                "intent": room.current_intent_dict or cached.get("intent"),
                "itinerary": room.current_itinerary_dict,
                "user_id": room.owner_id,
            }

        req = ChatConfirmRequest(session_id=session_id, decision="confirm")
        async for ev in _graph_confirm(req):
            event = ev.model_dump(mode="json")
            if event.get("type") == "itinerary_ready":
                room.current_itinerary_dict = event.get("payload")
            elif event.get("type") == "agent_narration":
                payload = event.get("payload") or {}
                text = payload.get("text")
                if text:
                    room.chat_messages.append(
                        {
                            "id": f"agent-{int(time.time() * 1000)}",
                            "role": "agent",
                            "text": text,
                            "createdAt": int(time.time() * 1000),
                        }
                    )
            await self._broadcast_planning_event(room, event)

    # ============================================================
    # 内部方法
    # ============================================================

    async def _trigger_replan(
        self, room: Room, *, trigger_user: str, trigger_reason: str
    ) -> None:
        """合并约束池 → 重新规划 → 广播规划事件。"""
        # 清空旧的规划事件历史（新一轮规划开始）
        room.previous_itinerary_dict = room.current_itinerary_dict
        room.planning_events_history.clear()
        room.chat_state_snapshot = None

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

    async def _trigger_fresh_plan(
        self, room: Room, *, trigger_user: str, user_input: str
    ) -> None:
        """route_turn 判定 planning → 全新规划（ADR-0013 决策 7）。

        与 `_trigger_replan` 的区别：`_trigger_replan` 走 `_run_planning`，那里按
        `room.current_intent_dict is not None` 二选一（有基线→refiner 合并约束；
        无基线→`_plan_fresh`）——这条分支服务的是"反馈"语义。而 route_turn 判定
        "planning" 时（如成员打出完整 canonical 场景文本、或点击"重新规划一个"）
        本身就是一句独立、完整的规划请求，语义上与"是否已有基线方案"无关，必须
        无条件走 `_plan_fresh`，不能被 `_run_planning` 的分支逻辑误判成"有基线就走
        refiner 合并"（那会把一句新规划请求硬揉成对旧方案的增量调整）。
        """
        room.previous_itinerary_dict = room.current_itinerary_dict
        room.planning_events_history.clear()
        room.chat_state_snapshot = None

        await self.broadcast(room, {
            "type": "planning_started",
            "trigger": "planning",
            "trigger_user": trigger_user,
        })

        room.planning_task = asyncio.create_task(self._plan_fresh(room, user_input))

    async def _run_planning(self, room: Room) -> None:
        """执行规划并广播事件。

        策略：
        1. 合并所有约束文本为一个 feedback 字符串
        2. 如果有 current_intent → 用 refiner 合并约束
        3. 如果没有 current_intent → 用第一条约束作为初始输入走 planner
        4. 规划事件逐条广播给所有成员
        5. 维护 llm_context_messages：每次约束和规划结果都追加到上下文
        """
        try:
            # 合并约束为 feedback 文本
            merged_feedback = self._merge_constraints_text(room)

            # 追加约束到 LLM 上下文（让 LLM 知道"用户们说了什么"）
            self._append_to_llm_context(room, role="user", content=merged_feedback)

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
            await self._broadcast_planning_event(room, {
                    "type": "stream_error",
                    "seq": 0,
                    "payload": {"reason": "planning_failed", "detail": str(e)[:200]},
                    "timestamp_ms": int(time.time() * 1000),
                })

    async def _replan_with_refiner(self, room: Room, feedback: str) -> None:
        """用 refiner 合并约束后重新规划，带完整 LLM 上下文。"""
        from schemas.intent import IntentExtraction
        from agent.intent.refiner import refine_intent

        # 还原 intent
        intent = IntentExtraction.model_validate(room.current_intent_dict)

        # 构建带上下文的 feedback（让 refiner 的 LLM 看到协作历史）
        context_summary = self._build_llm_context_summary(room)
        enriched_feedback = f"{context_summary}\n\n【本次约束】{feedback}" if context_summary else feedback

        # refiner 合并（传入带上下文的 feedback）
        result = refine_intent(intent, enriched_feedback)
        refined_intent = result.refined_intent
        room.current_intent_dict = refined_intent.model_dump()

        # 追加 refiner 结果到 LLM 上下文
        self._append_to_llm_context(
            room, role="assistant",
            content=f"已合并约束：{'; '.join(result.changed_fields or ['无变更'])}。"
                    f"调整后意图：距离{refined_intent.distance_max_km}km，"
                    f"饮食约束{list(refined_intent.dietary_constraints)}。"
        )

        # 广播 refinement 结果
        await self._broadcast_planning_event(room, {
                "type": "refinement_done",
                "seq": 0,
                "payload": {
                    "refined_intent": refined_intent.model_dump(),
                    "changed_fields": result.changed_fields,
                    "refiner_note": result.refiner_note,
                },
                "timestamp_ms": int(time.time() * 1000),
            })

        # 用 refined intent 重新规划
        await self._run_planner_and_broadcast(room, refined_intent)

    async def _plan_fresh(self, room: Room, user_input: str) -> None:
        """无基线时走完整规划路径，带 LLM 上下文。"""
        # 尝试用 LangGraph 或 ReAct agent
        try:
            from agent.graph.sse_adapter import run_graph_stream
            from agent.graph.build import get_compiled_graph
            get_compiled_graph()

            user_id = room.owner_id
            # 每次重规划用新的 session_id，避免 LangGraph checkpoint 恢复旧 state
            session_id = f"collab_{room.room_id}_{int(time.time() * 1000)}"

            async for event in run_graph_stream(
                user_input=user_input,
                session_id=session_id,
                user_id=user_id,
            ):
                # 广播每条规划事件
                await self._broadcast_planning_event(room, event.model_dump())
                # 捕获 itinerary_ready 和 intent_parsed
                if event.type.value == "itinerary_ready":
                    room.current_itinerary_dict = event.payload
                    # 追加规划结果到 LLM 上下文
                    summary = event.payload.get("summary", "行程已生成")
                    self._append_to_llm_context(room, role="assistant", content=f"已规划行程：{summary}")
                elif event.type.value == "intent_parsed":
                    room.current_intent_dict = event.payload

        except (ImportError, Exception):
            # LangGraph 不可用 → 走 rule planner
            await self._run_rule_planner_and_broadcast(room, user_input)

    async def _run_planner_and_broadcast(
        self, room: Room, intent: Any
    ) -> None:
        """用 intent 跑规划并广播事件，维护 LLM 上下文。"""
        from schemas.intent import IntentExtraction
        from schemas.sse import SseEvent, SseEventType

        # 广播 intent_parsed
        await self._broadcast_planning_event(room, {
                "type": "intent_parsed",
                "seq": 0,
                "payload": intent.model_dump() if hasattr(intent, "model_dump") else intent,
                "timestamp_ms": int(time.time() * 1000),
            })

        # 尝试 LangGraph
        try:
            from agent.graph.sse_adapter import run_graph_stream
            from agent.graph.build import get_compiled_graph
            get_compiled_graph()

            session_id = f"collab_{room.room_id}_{int(time.time() * 1000)}"
            # 构造用户输入（从 intent 的 raw_input 取）
            raw_input = intent.raw_input if hasattr(intent, "raw_input") else "重新规划"

            async for event in run_graph_stream(
                user_input=raw_input,
                session_id=session_id,
                user_id=room.owner_id,
            ):
                await self._broadcast_planning_event(room, event.model_dump())
                if event.type.value == "itinerary_ready":
                    room.current_itinerary_dict = event.payload
                    summary = event.payload.get("summary", "行程已生成")
                    self._append_to_llm_context(room, role="assistant", content=f"已重新规划行程：{summary}")
                # 检查是否被取消
                await asyncio.sleep(0)  # yield control

        except (ImportError, Exception) as e:
            # fallback: rule planner
            await self._run_rule_planner_fallback(room, intent)

    async def _run_rule_planner_fallback(self, room: Room, intent: Any) -> None:
        """Rule planner 兜底。"""
        try:
            from agent.planning.planners.rule_planner import plan_itinerary
            from agent.core.trace import Tracer

            tracer = Tracer()
            result = plan_itinerary(intent, tracer=tracer)

            # 广播 tracer 事件
            for record in tracer.records:
                await self._broadcast_planning_event(room, {
                        "type": record.type,
                        "seq": 0,
                        "payload": record.payload or {},
                        "timestamp_ms": int(record.timestamp * 1000),
                    })
                await asyncio.sleep(0.05)  # 模拟流式节奏

            if result.success and result.itinerary:
                room.current_itinerary_dict = result.itinerary.model_dump()
                await self._broadcast_planning_event(room, {
                        "type": "itinerary_ready",
                        "seq": 0,
                        "payload": result.itinerary.model_dump(),
                        "timestamp_ms": int(time.time() * 1000),
                    })
        except Exception as e:  # noqa: BLE001
            await self._broadcast_planning_event(room, {
                    "type": "stream_error",
                    "seq": 0,
                    "payload": {"reason": "rule_planner_failed", "detail": str(e)[:200]},
                    "timestamp_ms": int(time.time() * 1000),
                })

    async def _run_rule_planner_and_broadcast(self, room: Room, user_input: str) -> None:
        """无 LangGraph 时用 rule planner 兜底（需要先解析 intent）。"""
        try:
            from agent.intent.parser import parse_intent
            from agent.planning.planners.rule_planner import plan_itinerary
            from agent.core.trace import Tracer

            intent = parse_intent(user_input)
            room.current_intent_dict = intent.model_dump()

            await self._broadcast_planning_event(room, {
                    "type": "intent_parsed",
                    "seq": 0,
                    "payload": intent.model_dump(),
                    "timestamp_ms": int(time.time() * 1000),
                })

            tracer = Tracer()
            result = plan_itinerary(intent, tracer=tracer)

            for record in tracer.records:
                await self._broadcast_planning_event(room, {
                        "type": record.type,
                        "seq": 0,
                        "payload": record.payload or {},
                        "timestamp_ms": int(record.timestamp * 1000),
                    })
                await asyncio.sleep(0.05)

            if result.success and result.itinerary:
                room.current_itinerary_dict = result.itinerary.model_dump()
                await self._broadcast_planning_event(room, {
                        "type": "itinerary_ready",
                        "seq": 0,
                        "payload": result.itinerary.model_dump(),
                        "timestamp_ms": int(time.time() * 1000),
                    })

            # 广播 done
            await self._broadcast_planning_event(room, {
                    "type": "done",
                    "seq": 0,
                    "payload": {},
                    "timestamp_ms": int(time.time() * 1000),
                })

        except Exception as e:  # noqa: BLE001
            await self._broadcast_planning_event(room, {
                    "type": "stream_error",
                    "seq": 0,
                    "payload": {"reason": "fresh_plan_failed", "detail": str(e)[:200]},
                    "timestamp_ms": int(time.time() * 1000),
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

    def _append_to_llm_context(
        self, room: Room, *, role: str, content: str
    ) -> None:
        """追加一条消息到房间的 LLM 上下文历史。

        格式兼容 Pydantic AI 的 ModelMessage 序列化：
        - role="user" → 用户/参与者的约束输入
        - role="assistant" → Agent 的规划结果摘要
        - role="system" → 系统级上下文（如"以下是多人协作场景"）

        上下文窗口控制：保留最近 20 条消息（约 4000 token），
        超出时从头部裁剪（保留最新的上下文）。
        """
        MAX_CONTEXT_MESSAGES = 20
        room.llm_context_messages.append({
            "role": role,
            "content": content,
            "timestamp": time.time(),
        })
        # 裁剪：保留最近 N 条
        if len(room.llm_context_messages) > MAX_CONTEXT_MESSAGES:
            room.llm_context_messages = room.llm_context_messages[-MAX_CONTEXT_MESSAGES:]

    def _build_llm_context_summary(self, room: Room) -> str:
        """把 LLM 上下文历史构建为一段摘要文本（可喂给 refiner 或 planner 的 system prompt）。

        用途：当 refiner/planner 不直接支持 message_history 参数时，
        把上下文压缩为一段"协作背景"文本拼到 feedback 前面。
        """
        if not room.llm_context_messages:
            return ""
        parts = ["【协作上下文】"]
        for msg in room.llm_context_messages[-10:]:  # 最近 10 条
            role_label = {"user": "参与者", "assistant": "Agent", "system": "系统"}.get(msg["role"], msg["role"])
            parts.append(f"  {role_label}：{msg['content']}")
        return "\n".join(parts)

    def _get_stage_title(self, room: Room, stage_index: int) -> str:
        """从当前行程中取某段的标题。

        edge_v1：itinerary 已切换为 nodes/hops 模型，但本方法的 `stage_index` 来源
        仍是前端 `vote.stage_index`（前端时间轴的可见段编号）。dict 形式无 schema
        约束，本函数主动兼容三种情况：
        1. dict 仍带旧 `stages` 字段（legacy snapshot） → 直接读
        2. dict 含 `nodes` → 跳过首尾 home，按 mid nodes 顺序取第 stage_index 段
        3. 都没有 → 返回 fallback 文本

        前端何时切到「按 schedule entry 索引」由 Task 12 决定，本函数只保证
        edge_v1 数据下不抛异常。
        """
        if not room.current_itinerary_dict:
            return f"第 {stage_index + 1} 段"

        # 1. 兼容旧 stages 字段
        legacy_stages = room.current_itinerary_dict.get("stages")
        if isinstance(legacy_stages, list) and 0 <= stage_index < len(legacy_stages):
            return legacy_stages[stage_index].get("title", f"第 {stage_index + 1} 段")

        # 2. edge_v1：跳过首尾 home，对中间节点取 title
        nodes = room.current_itinerary_dict.get("nodes")
        if isinstance(nodes, list):
            mid_nodes = [
                n for n in nodes
                if isinstance(n, dict) and n.get("target_kind") != "home"
            ]
            if 0 <= stage_index < len(mid_nodes):
                return mid_nodes[stage_index].get("title", f"第 {stage_index + 1} 段")

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

    async def _broadcast_planning_event(self, room: Room, event: dict[str, Any]) -> None:
        """广播规划事件并存入历史（新成员加入时可回放）。"""
        msg = {"type": "planning_event", "event": event}
        room.planning_events_history.append(event)
        await self.broadcast(room, msg)


# ============================================================
# 单例
# ============================================================

_manager: Optional[RoomManager] = None


def get_room_manager() -> RoomManager:
    global _manager
    if _manager is None:
        _manager = RoomManager()
    return _manager
