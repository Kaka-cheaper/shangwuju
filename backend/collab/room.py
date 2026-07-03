"""collab.room —— 房间模型 + RoomManager + WebSocket Hub + 规划中断桥接。

核心流程：
1. 发起人 POST /room/create → RoomManager.create_room() → 返回 room_id
2. 任何人 WS /ws/{room_id} → RoomManager.join()（首次广播 member_joined，
   重连广播 member_reconnected）
3. 任何人发 constraint（自由打字）→ Room.add_constraint() → 先过统一路由脑子
   route_turn 判义务（ADR-0013 决策 7，"房间路由同权"）→ 归名广播给全员 + 按义务
   分流：feedback→合并约束重新规划 / planning→全新规划 / 其余→气泡广播，不动方案
4. 任何人发 vote → Room.update_vote() → 广播；踩（dislike）收编进 RoomManager.adjust()
   的节点级局部重解（ADR-0013 决策 4/Q5，F-5），不再触发全量重排
5. 任何人发 adjust（节点行定向调整按钮 / 具名备选）→ RoomManager.adjust()：
   room.lock 内串行 → 候选池现场重查 → resolve_node_swap → 归名台账 + 归名说明 →
   node_locked/node_unlocked 广播全员可见处理态（ADR-0013 F-5）
6. owner 发 confirm → Room.confirm() → 广播确认结果
7. RoomManager.sweep_expired_rooms()：50min 空闲 TTL 惰性清扫（ADR-0013 决策 6，
   F-5）——房间对象连诉求台账一起蒸发；绝不清扫仍有在线 WS 连接的房间

设计取舍：
- 单进程 dict 存储（与 InMemoryRepository 一致，Demo 够用）
- asyncio.Lock per room 保证约束/调整串行处理
- planning_task 用 asyncio.Task.cancel() 实现中断
- 规划事件通过回调广播给所有 WS 连接（复用现有 SseEvent 格式）
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)


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
    # ADR-0013 决策 6 / F-5：房间 TTL 清扫的计时基准——任何成员发起的动作
    # （join/leave/add_constraint/update_vote/confirm/adjust）都刷新它；
    # `RoomManager.sweep_expired_rooms` 用它判定"最后活动"是否已超过 50min。
    # 不进 `get_state_snapshot()`——纯内部记账字段，前端无消费理由。
    last_activity_at: float = field(default_factory=time.time)
    # 被赞锁定的 stage index 集合（重规划时保留这些段）
    locked_stages: set[int] = field(default_factory=set)
    # 诉求台账（ADR-0013 决策 3 / F-2，F-5 接线落地）：list[dict]（schemas.
    # demand_ledger.LedgerEntry.model_dump()）——房间侧的台账存储位，生命周期
    # 随房间本身（房间销毁即销毁，`RoomManager.sweep_expired_rooms` 的 TTL 清扫
    # 连它一起蒸发）。写入（`schemas.demand_ledger.record_demand`/
    # `mark_satisfied`）在 `RoomManager.adjust()`；消费（喂给 F-1 引擎的
    # `ledger_slice`/前端台账面板）分别在 `adjust()` 内部与 `get_state_snapshot()`
    # 的 `ledger_for_display` 投影。
    demand_ledger: list[dict[str, Any]] = field(default_factory=list)
    # c′批 任务二（L0 禁令 2「绝不默默让已下单方案与订单脱钩」）：房间版
    # 「已确认下单」信号——单人模式读图状态 `user_decision == "confirm"`
    # （见 api/_streams/graph_adjust.py 同名守门；两侧共享的告知文案
    # CONFIRMED_ADJUST_BLOCKED_MESSAGE 收在
    # agent/planning/planners/node_swap_support.py），房间没有图 checkpoint，
    # 需要一个自己的一等信号。**没有**复用 `current_itinerary_dict.get(
    # "orders")` 非空这个更廉价的代理信号——它对"全免费活动、confirm 阶段
    # 一个订单都不产生"的方案会漏判（假阴性：明明已确认，orders 却是空
    # 列表），confirm() 是唯一真正执行了"确认"这个动作的地方，直接在那里
    # 置位比从下游数据反推更可靠。仅在 `RoomManager.confirm()` 收到
    # `itinerary_ready`（即 execute_finalize 真正跑完）时置 True；`_trigger_
    # replan`/`_trigger_fresh_plan`（新一轮规划开始，房间侧「新 episode」）
    # 时重置回 False——语义上与单人图状态 `user_decision` 的 EPISODE_SCOPED
    # 重置对齐。
    confirmed: bool = False

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
        """全量状态快照（新成员加入时推送）。

        F-5 新增 `demand_ledger`（F-2 拍板落地时刻——见 F-2 阶段本字段
        docstring"本步只提供存储位……不接入 get_state_snapshot 等既有流程"，
        接入的正是这里）：投影用 `ledger_for_display`（同 F-4 单人 `/chat/adjust`
        的 `agent_narration.demand_ledger` 同一投影口径），让新加入者也能看到
        房间已经攒下的协商台账，不必等下一次换菜事件才补齐。
        """
        from schemas.demand_ledger import LedgerEntry, ledger_for_display

        ledger_entries = [LedgerEntry.model_validate(d) for d in self.demand_ledger]
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
            "demand_ledger": ledger_for_display(ledger_entries),
            "confirmed": self.confirmed,
        }


# ============================================================
# RoomManager（单例）
# ============================================================


class RoomManager:
    """房间生命周期管理 + WebSocket 广播。"""

    # ADR-0013 决策 6：50 分钟空闲 TTL，从"最后活动"起算（见 `Room.last_activity_at`）。
    # 类属性而非模块级常量——测试可 `manager.ROOM_TTL_SECONDS = ...` 局部覆盖，
    # 不需要 monkeypatch 整个模块。
    ROOM_TTL_SECONDS: float = 50 * 60

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def create_room(self, owner_id: str, nickname: str = "发起人") -> Room:
        """创建新房间。"""
        self.sweep_expired_rooms()
        room_id = self._generate_room_id()
        room = Room(room_id=room_id, owner_id=owner_id)
        room.members[owner_id] = Member(
            user_id=owner_id, nickname=nickname, role="owner"
        )
        self._rooms[room_id] = room
        return room

    def get_room(self, room_id: str) -> Optional[Room]:
        self.sweep_expired_rooms()
        return self._rooms.get(room_id)

    def delete_room(self, room_id: str) -> None:
        room = self._rooms.pop(room_id, None)
        if room and room.planning_task and not room.planning_task.done():
            room.planning_task.cancel()

    def list_rooms(self) -> list[dict[str, Any]]:
        self.sweep_expired_rooms()
        return [
            {
                "room_id": r.room_id,
                "owner_id": r.owner_id,
                "member_count": len(r.members),
                "created_at": r.created_at,
            }
            for r in self._rooms.values()
        ]

    def sweep_expired_rooms(self, *, now: Optional[float] = None) -> list[str]:
        """惰性 TTL 清扫（ADR-0013 决策 6/F-5）——销毁=房间对象连台账全蒸发。

        【机制选型：惰性 vs 定时器，选惰性】`RoomManager` 是进程内单例（模块级
        `_manager`），若改用 `asyncio.create_task` 起一个常驻定时协程，其生命
        周期会绑死在"第一次创建它时恰好在跑的那个事件循环"上——本仓库测试套件
        大量用 `asyncio.run()`（每次开一个新循环、返回前强制 cancel 掉残留任务，
        见 `test_room_lifecycle_characterization.py` 等多处 docstring 对这一点
        的反复强调），常驻任务跨 `asyncio.run()` 边界存活会在下一次测试的新循环
        里变成"绑在已关闭循环上的僵尸任务"，引入与本次改造目标无关的 flaky。
        惰性清扫无需常驻协程/无需 FastAPI lifespan 挂钩（main.py 本轮由并行任务
        改动中，不额外争用）——挂在每次外部交互的入口（`create_room`/`get_room`/
        `list_rooms`，覆盖 HTTP 建房、WS 连接、管理态查询三条所有"有人在跟系统
        交互"的路径）上顺手做一次全量扫描，代价是 O(房间数) 的 dict 遍历，demo
        规模下可忽略；`now` 可注入，供测试确定性驱动（不依赖真实 sleep）。

        【护栏】只清扫"最后活动超过 TTL" **且** "当前无任何在线 WS 连接"的房间——
        后一条硬性兜底防止长连接活跃的房间被误杀（评委开着房间讲 PPT，中途没有
        触发任何 `last_activity_at` 刷新点，但连接仍在，绝不能被清扫掉）。
        """
        ts = now if now is not None else time.time()
        expired = [
            room_id
            for room_id, room in self._rooms.items()
            if ts - room.last_activity_at > self.ROOM_TTL_SECONDS
            and all(m.ws is None for m in room.members.values())
        ]
        for room_id in expired:
            self.delete_room(room_id)
        return expired

    async def join(
        self, room: Room, user_id: str, nickname: str, ws: WebSocket
    ) -> None:
        """成员加入房间。

        F-5 生命周期疑点处置（任务书"重连凭证"节，2026-07-03 拍板）：
        1. **重连时更新昵称**——原实现只更新 `ws`、无条件丢弃重连传入的新昵称
           （特征化测试曾钉死这个现状为"疑似异味"）。临时身份语义下"改名"应该
           生效（localStorage id 只是断线重连凭证，不锁定昵称），且不破坏"同对象
           契约"——只改 `Member.nickname` 字段，`Member` 对象本身不重建、`role`
           不重置，重连前后 `is` 同一对象的既有保证原样保留。
        2. **重连不再广播 `member_joined`**——原实现无条件对首次加入/重连都广播
           `member_joined`，前端 `handleWsMessage` 的对应 case 是无条件 `push`
           进 `members` 数组（不做 `user_id` 去重），每次重连都会在其他成员本地
           的成员列表里追加一条重复行（"重连刷屏"，不是比喻，是真实的列表重复
           bug）。改为：首次加入广播 `member_joined`（新增一行）；重连广播
           `member_reconnected`（更新既有行的 `online`/`nickname`，见
           `frontend/lib/collab-store.ts` 对应 case）——语义上"老王回来了"和
           "来了个新人小明"是两种不同的事件，值得用两个类型区分，而不是让前端
           靠"这个 user_id 是不是已经在列表里"反推事件语义。
        """
        room.last_activity_at = time.time()
        if user_id in room.members:
            # 重连：更新 ws + 昵称，Member 对象本身不重建（role 不变）
            member = room.members[user_id]
            member.ws = ws
            member.nickname = nickname
            await self._send(ws, room.get_state_snapshot())
            await self.broadcast(room, {
                "type": "member_reconnected",
                "user_id": user_id,
                "nickname": nickname,
                "role": member.role,
            }, exclude=user_id)
        else:
            room.members[user_id] = Member(
                user_id=user_id, nickname=nickname, role="participant", ws=ws
            )
            await self._send(ws, room.get_state_snapshot())
            await self.broadcast(room, {
                "type": "member_joined",
                "user_id": user_id,
                "nickname": nickname,
                "role": room.members[user_id].role,
            }, exclude=user_id)

    async def leave(self, room: Room, user_id: str) -> None:
        """成员离开（WS 断开）。"""
        room.last_activity_at = time.time()
        member = room.members.get(user_id)
        if member:
            member.ws = None
        await self.broadcast(room, {
            "type": "member_left",
            "user_id": user_id,
        })
        # 全员离线后的销毁交给 `sweep_expired_rooms`（ADR-0013 决策 6 / F-5）——
        # 惰性 TTL 清扫会在下一次任意外部交互时发现并清掉；这里不再需要任何
        # "标记待清理"的占位注释（F-5 之前的现状，见特征化测试对本行为的钉住）。

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
            room.last_activity_at = time.time()
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
        """更新投票 → 广播 →（踩）收编进节点级局部重解。

        ADR-0013 决策 4/Q5「点踩收编」（F-5 拍板落地）：改造前，踩一段会翻译成
        一句"不满意第 N 段，请换一个"塞进约束池、中断在跑规划、触发全量重排——
        正是 ADR-0013 背景节点名的病灶（"嫌一个节点 → 整个方案洗牌"）。改造后，
        踩直接走 `adjust()`（房间版局部重解引擎，同 WS "adjust" 消息复用的同一
        入口，`action=dislike`）：立刻换、只动这一格，不再合成约束文本、不再进
        `room.constraints`、不再中断/触发 `_trigger_replan`。

        `locked_stages` 语义不受此次改造影响——仍是纯展示态的"赞锁定"集合（无
        下游消费者按它门控重排，见任务报告"深审重点"），"踩只解锁本段"的既有
        保证（`discard(stage_index)` 只影响这一个 index）原样保留。

        `adjust()` 自己会重新 `async with room.lock` 串行——必须等本方法自己的
        `async with room.lock` 块结束（投票记录+广播已完成）才能调用，否则
        `asyncio.Lock` 不可重入会死锁。
        """
        target_id: Optional[str] = None
        async with room.lock:
            room.last_activity_at = time.time()
            if stage_index not in room.votes:
                room.votes[stage_index] = {}
            room.votes[stage_index][user_id] = action

            # 更新锁定集合（纯展示态，见方法 docstring）
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

            if action == "dislike":
                target_id = self._stage_target_id(room, stage_index)

        if action == "dislike" and target_id is not None:
            from api._streams.models import AdjustActionDislike

            await self.adjust(room, user_id, target_id, AdjustActionDislike())
        # target_id is None（当前方案里定位不到这一段，如尚未出方案）——静默丢弃，
        # 不广播、不报错：UI 不应该出现指向不存在节点的踩按钮，这是防御性兜底。

    # ============================================================
    # 房间版节点调整（ADR-0013 F-5）——WS "adjust" 消息 + 点踩收编共用入口
    # ============================================================

    async def adjust(
        self,
        room: Room,
        user_id: str,
        node_id: str,
        action: Any,
    ) -> None:
        """房间版节点换菜——复用 F-4 单人链路的同一个引擎
        `agent.planning.planners.node_swap.resolve_node_swap`（见
        `api/_streams/graph_adjust.py` 模块 docstring）。`action` 是 F-4 既有的
        判别式协议对象（`api._streams.models.AdjustActionAdjust` /
        `AdjustActionAlternative` / `AdjustActionDislike`），由 WS 层
        （`api/collab.py`）按同一份 pydantic schema 校验后传入——三个入口
        （节点行的定向调整按钮 / 具名备选 / 点踩）到这里已经殊途同归。

        与单人版三处必然差异（房间是长连接多人会话，不是一次性 SSE 请求）：
        1. **候选池现场重查**：房间没有 LangGraph 图 checkpoint 里现成缓存的
           `pois`/`restaurants`（那是单人会话规划时"顺手"存下的），复用
           `agent.planning.planners.ils_planner._query_pois`/`_query_restaurants`
           同款真实召回（同 intent、同 grounding 过滤），不平行发明第二套查询。
        2. **全程串行**：`room.lock` 保证同一房间的多个调整请求排队处理，后到的
           基于前一个的结果重解（ADR-0013 决策 6）。
        3. **归名 + 处理期锁定广播**：诉求台账记 `member_id`/`nickname`；处理期
           先广播 `node_locked`（全员可见该节点 Shimmer 处理中），成功/失败都
           以 `node_unlocked`收尾（`finally`，任何异常路径都不会让节点卡死锁定）。

        业务性失败（无可换候选 / 保留节点排不到一块儿）与调用方契约违反
        （`node_id` 并发下已失效等 `ValueError`）在这里**都**降级为告知气泡，
        不像 F-4 SSE 那样把契约违反交给外层兜底转 `stream_error`——房间 WS 是
        长连接会话，任何未捕获异常都会被 `api/collab.py::ws_collab` 的外层
        `except Exception` 当成断线处理触发 `manager.leave()`，那是"因为一次
        换菜的边界情况就把人踢下线"的真事故，比多做一层防御性收窄严重得多。
        """
        async with room.lock:
            room.last_activity_at = time.time()
            member = room.members.get(user_id)
            nickname = member.nickname if member else user_id

            await self.broadcast(room, {
                "type": "node_locked",
                "node_id": node_id,
                "by_user": user_id,
                "nickname": nickname,
            })
            try:
                await self._resolve_and_broadcast_adjust(room, user_id, nickname, node_id, action)
            except Exception:  # noqa: BLE001
                # 纵深防御：`_resolve_and_broadcast_adjust` 内部已经把已知的业务失败/
                # 契约违反都收窄成告知气泡，这里兜的是"未预料的真 bug"——同样不能
                # 冒泡（见方法 docstring），退化为一句通用告知，把异常记日志供事后排查。
                logger.exception("room adjust 未预料异常：room_id=%s node_id=%s", room.room_id, node_id)
                await self._broadcast_planning_event(room, {
                    "type": "agent_narration",
                    "seq": 0,
                    "payload": {"text": "这一步出了点意外，方案维持不变，麻烦稍后再试。", "stage": "stream"},
                    "timestamp_ms": int(time.time() * 1000),
                })
            finally:
                await self.broadcast(room, {"type": "node_unlocked", "node_id": node_id})

    async def _resolve_and_broadcast_adjust(
        self, room: Room, user_id: str, nickname: str, node_id: str, action: Any
    ) -> None:
        """`adjust()` 的实际换菜逻辑——拆成独立方法只是为了让 `finally` 解锁
        与"到底怎么换"两件事在视觉上分开，不代表可以脱离 `adjust()` 单独调用
        （依赖调用方已持有 `room.lock` 且已广播 `node_locked`）。
        """
        from agent.core.trace import Tracer
        from agent.graph.nodes.narrate import _build_node_actions
        from agent.intent.narrator import generate_template_node_chips
        from agent.planning.planners.ils_planner import _query_pois, _query_restaurants
        from agent.planning.planners.node_swap import resolve_node_swap
        from agent.planning.planners.node_swap_support import (
            CONFIRMED_ADJUST_BLOCKED_MESSAGE,
            compose_narration_text,
            find_entity,
            narrow_pool_to_single_alternative,
            node_title,
            synthesize_source_text,
            target_kind,
        )
        from api._streams.models import AdjustActionAdjust, AdjustActionAlternative, AdjustActionDislike
        from schemas import IntentExtraction, Itinerary
        from schemas.demand_ledger import (
            LedgerEntry,
            NodeRef,
            active_adjustments,
            ledger_for_display,
            mark_satisfied,
            record_demand,
        )

        async def _narrate_bubble(text: str) -> None:
            await self._broadcast_planning_event(room, {
                "type": "agent_narration",
                "seq": 0,
                "payload": {"text": text, "stage": "stream"},
                "timestamp_ms": int(time.time() * 1000),
            })

        if room.current_itinerary_dict is None or room.current_intent_dict is None:
            await _narrate_bubble("现在还没有可以调整的方案，先让大家一起规划出一个吧。")
            return

        # ---- L0 禁令 2 守门：已确认下单的方案不静默换菜（见 Room.confirmed /
        # CONFIRMED_ADJUST_BLOCKED_MESSAGE docstring）----
        if room.confirmed:
            await _narrate_bubble(CONFIRMED_ADJUST_BLOCKED_MESSAGE)
            return

        itinerary = Itinerary.model_validate(room.current_itinerary_dict)
        intent = IntentExtraction.model_validate(room.current_intent_dict)
        ledger = [LedgerEntry.model_validate(d) for d in room.demand_ledger]

        kind = target_kind(itinerary, node_id)
        if kind is None:
            # 契约违反级别的边界（节点已不在方案里）——房间长连接下降级为告知，
            # 不抛异常（见 `adjust()` docstring）。多是并发下"方案在你点击的同时
            # 已被别的操作换过"的正常竞态，不是真正的程序错误。
            await _narrate_bubble("这个节点好像已经不在当前方案里了，方案可能刚被别的操作换过，刷新后再试试？")
            return

        old_title = node_title(itinerary, node_id)
        node_ref = NodeRef(kind=kind, target_id=node_id)  # type: ignore[arg-type]

        tracer = Tracer()
        pois = _query_pois(intent, tracer)
        restaurants = _query_restaurants(intent, tracer)

        updated_ledger = ledger
        adjustment = None

        try:
            if isinstance(action, AdjustActionDislike):
                result = resolve_node_swap(
                    itinerary, intent, pois, restaurants,
                    target_node_id=node_id,
                    adjustment=None,
                    ledger_slice=active_adjustments(ledger, node_ref=node_ref),
                )

            elif isinstance(action, AdjustActionAdjust):
                adjustment = action.adjustment
                source_text = (action.label or "").strip() or synthesize_source_text(adjustment)
                new_entry = LedgerEntry(
                    member_id=user_id,
                    nickname=nickname,
                    node_ref=node_ref,
                    adjustment=adjustment,
                    source_text=source_text,
                )
                ledger_slice = active_adjustments(ledger, node_ref=node_ref)
                result = resolve_node_swap(
                    itinerary, intent, pois, restaurants,
                    target_node_id=node_id,
                    adjustment=adjustment,
                    ledger_slice=ledger_slice,
                )
                updated_ledger = record_demand(ledger, new_entry)
                if result.success and result.degrade_tier in (1, 2):
                    updated_ledger = mark_satisfied(
                        updated_ledger, member_id=user_id, node_ref=node_ref, dimension=adjustment.dimension
                    )

            else:  # AdjustActionAlternative
                assert isinstance(action, AdjustActionAlternative)
                chosen_entity = find_entity(kind, action.target_id, pois, restaurants)
                if chosen_entity is None:
                    await _narrate_bubble("这个备选好像已经不在候选里了，我再帮你看看还有什么可以换。")
                    return
                call_pois, call_rests = narrow_pool_to_single_alternative(itinerary, pois, restaurants, kind, chosen_entity)
                result = resolve_node_swap(
                    itinerary, intent, call_pois, call_rests,
                    target_node_id=node_id,
                    adjustment=None,
                    ledger_slice=(),
                )
        except ValueError:
            # 契约违反（如 target_node_id 并发下已失效）——同上，房间长连接下
            # 降级为告知，不冒泡（见 `adjust()` docstring「三」）。
            await _narrate_bubble("这一步暂时没能处理，方案维持不变，麻烦稍后再试。")
            return

        # ---- 业务性失败：方案不动，只告知 ----
        if not result.success:
            if isinstance(action, AdjustActionAdjust):
                # 诉求依然记账（换不成不代表用户不再想要——同 F-4 语义）。
                room.demand_ledger = [e.model_dump() for e in updated_ledger]
            message = result.advisories[0].message if result.advisories else "这一步暂时没能调整成功，方案维持不变。"
            await _narrate_bubble(message)
            return

        # ---- 成功：更新房间状态 + SESSION_STORE 投影 + 归名说明 + 广播 ----
        new_itinerary = result.new_itinerary
        node_chips = generate_template_node_chips(new_itinerary, intent, pois, restaurants)
        node_actions = _build_node_actions(new_itinerary, intent, pois, restaurants, node_chips)
        advisory_dicts = [a.model_dump() for a in result.advisories]

        room.current_itinerary_dict = new_itinerary.model_dump()
        room.demand_ledger = [e.model_dump() for e in updated_ledger]

        session_id = room.session_id or f"collab_{room.room_id}"
        from api._session_store import sync_snapshot

        sync_snapshot(session_id, itinerary=new_itinerary.model_dump())

        new_title = node_title(new_itinerary, result.swapped_to or "")
        base_text = self._build_room_narration(action, nickname, old_title, new_title, adjustment)
        narration_text = compose_narration_text(base_text, advisory_dicts)

        narration_payload: dict[str, Any] = {"text": narration_text, "stage": "stream"}
        if advisory_dicts:
            narration_payload["messages"] = [
                {"kind": "advisory", "code": a.get("code"), "text": a.get("message")}
                for a in advisory_dicts
                if a.get("message")
            ]
        if node_actions:
            narration_payload["node_actions"] = node_actions
        ledger_display = ledger_for_display(updated_ledger)
        if ledger_display:
            narration_payload["demand_ledger"] = ledger_display

        await self._broadcast_planning_event(room, {
            "type": "itinerary_ready",
            "seq": 0,
            "payload": new_itinerary.model_dump(),
            "timestamp_ms": int(time.time() * 1000),
        })
        await self._broadcast_planning_event(room, {
            "type": "agent_narration",
            "seq": 0,
            "payload": narration_payload,
            "timestamp_ms": int(time.time() * 1000),
        })

    def _build_room_narration(
        self,
        action: Any,
        nickname: str,
        old_title: str,
        new_title: str,
        adjustment: Optional[Any],
    ) -> str:
        """房间版换菜说明——归名（"按{nickname}的要求…"），区别于 F-4 单人版
        `api/_streams/graph_adjust.py::_build_success_narration` 的"按你的
        要求"（房间是多人场景，必须点名是谁提的，不能含糊成"你"）。
        """
        from agent.planning.planners.node_swap_support import adjustment_descriptor
        from api._streams.models import AdjustActionAdjust, AdjustActionAlternative

        if isinstance(action, AdjustActionAdjust) and adjustment is not None:
            descriptor = adjustment_descriptor(adjustment)
            return f"按{nickname}的要求，把「{old_title}」换成了「{new_title}」，{descriptor}。"
        if isinstance(action, AdjustActionAlternative):
            return f"已经按{nickname}选的，把「{old_title}」换成了「{new_title}」。"
        return f"{nickname}点了个踩，已经把「{old_title}」换掉了，换成了「{new_title}」，看看这个怎么样。"

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

        room.last_activity_at = time.time()
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
                # c′批 任务二：execute_finalize 真正跑完（下单/预约/加购落地）
                # 才置位——只有走到这里才代表"确认"这个动作真正发生过，不是
                # 请求一发出就乐观置位（若 _graph_confirm 半路失败/落
                # stream_error，不会走到这个分支，confirmed 保持原值）。
                room.confirmed = True
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
        # c′批 任务二：新一轮规划事件开始 = 房间侧的「新 episode」——旧方案的
        # 确认状态不再适用于即将产出的新方案，重置解除调整守门（同单人图状态
        # `user_decision` 经 `reset_for_new_episode()` 的重置时机对齐）。
        room.confirmed = False

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

        E-1 缺口修复(ADR-0011 落地状态节有案):canonical「重新规划一个」这五个字
        不含任何需求要素,语义=「重做我的需求」——有基线 intent 时替换为其
        raw_input 再开新局,否则零上下文新 session 的 router 会把这句判成陪聊
        (本文件测试 5b 曾钉住该退化行为,修复后断言已翻转)。
        """
        from agent.intent.prompts.router_prompt import FLOOR_REPLAN_SEND

        if user_input == FLOOR_REPLAN_SEND:
            original_raw = (room.current_intent_dict or {}).get("raw_input") or ""
            if original_raw:
                user_input = original_raw

        room.previous_itinerary_dict = room.current_itinerary_dict
        room.planning_events_history.clear()
        room.chat_state_snapshot = None
        room.confirmed = False  # c′批 任务二：新 episode 开始，同 `_trigger_replan`

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

    def _stage_target_id(self, room: Room, stage_index: int) -> Optional[str]:
        """`update_vote` 点踩收编（F-5）用：把 `stage_index`（前端时间轴的可见段
        编号，与 `_get_stage_title` 同一口径——跳过首尾 home 的 mid nodes 顺序）
        翻译成 `adjust()` 需要的 `target_id`（`ActivityNode.target_id`）。

        与 `_get_stage_title` 是姊妹方法而非合并改造它——那个方法产出的是"展示
        用标题"，本方法产出的是"引擎定位用 id"，两者消费方不同（前者给旧的
        约束文案合成，后者给新的局部重解引擎），故意不复用同一个返回值语义。
        找不到（尚未出方案 / index 越界）返回 `None`，调用方按"静默丢弃"处理。
        """
        if not room.current_itinerary_dict:
            return None
        nodes = room.current_itinerary_dict.get("nodes")
        if not isinstance(nodes, list):
            return None
        mid_nodes = [n for n in nodes if isinstance(n, dict) and n.get("target_kind") != "home"]
        if 0 <= stage_index < len(mid_nodes):
            return mid_nodes[stage_index].get("target_id")
        return None

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
