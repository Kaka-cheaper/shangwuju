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
- 房间规划走稳定持久 graph 线程 `collab_{room_id}`（房间重排根治批）：planning
  义务重开一局、feedback 义务注入+续跑都落同一线程，messages/版本志/台账跨轮
  延续，与单人多轮会话同构（见 `_replan_with_refiner` docstring）
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
    # 被赞锁定的 stage index 集合——**前端展示投影**（room_state / vote_updated
    # 广播的既有契约，前端按可见段下标高亮）。真值在下面的 `locked_targets`；
    # 重排/换菜后由 `RoomManager._sync_locks_with_itinerary` 按新方案重投影
    # （赞锁定根治批：下标绑定"这一版方案的这一格"，方案换血后旧下标指向新
    # 方案是张冠李戴——既害展示，更害下一轮反馈把锁翻译到错误实体上）。
    locked_stages: set[int] = field(default_factory=set)
    # 实体级锁登记（赞锁定根治批）——赞锁定的**权威真值**：target_id →
    # {"kind": "poi"|"restaurant", "name": 节点展示名, "lockers": [user_id...]}。
    # 为什么按实体不按下标：锁的语义是"保住这个地方"，不是"保住第 N 格"——
    # 重排后同一实体可能换位置（下标失效），下标级存储会让第二轮反馈把锁
    # 翻译到别的实体头上（真错误，不是显示瑕疵）。写入在 `update_vote`
    # （like 登记 + 归名 / dislike 注销，点赞那一刻 stage_index 对
    # current_itinerary_dict 的解析最准确）；消费在 `_replan_with_refiner`
    # （翻译成图状态 pinned_targets 注入 + 出口检查归名告知）；收敛在
    # `_sync_locks_with_itinerary`（方案变更后剔除已消失实体 + 重投影
    # locked_stages）。不进 `get_state_snapshot()`——快照键集有特征化测试钉着，
    # 前端消费的是 locked_stages 投影，归名告知走 agent_narration 气泡。
    locked_targets: dict[str, dict[str, Any]] = field(default_factory=dict)
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

        评委体验修复（2026-07-03）新增 `node_actions`——历史现状 + 当时理由：
        ADR-0013 F-3 把 node_actions 定成"narrate 节点既有 LLM 调用搭车产出"，
        只在规划事件流（`itinerary_ready`/`agent_narration` 的兄弟字段，见
        `agent.graph.nodes.narrate` 模块 docstring）里下发；F-5 建房间快照时
        只顾上把 F-2 台账接进来（上一段），没有覆盖"中途加入者压根没经过这
        条事件流，永远等不到这批按钮"这条路径——ADR-0013 落地状态节因此记了
        一笔已知留痕"房间中途加入者在下一次换菜前看不到按钮"，一直挂到评委
        中途扫码进房、真的看见自己手机上一个调整按钮都没有，才从"待办"变成
        "演示事故"。修复：`current_itinerary_dict`/`current_intent_dict` 均非
        空时按 `_snapshot_node_actions()` 现算并塞入（该方法内部说明候选池
        口径选型 + 零 LLM 调用的模板路径 + 异常兜底"不拖垮 join"）。
        """
        from schemas.demand_ledger import LedgerEntry, ledger_for_display

        ledger_entries = [LedgerEntry.model_validate(d) for d in self.demand_ledger]
        snapshot: dict[str, Any] = {
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
        node_actions = self._snapshot_node_actions()
        if node_actions:
            snapshot["node_actions"] = node_actions
        return snapshot

    def _snapshot_node_actions(self) -> dict[str, dict[str, Any]]:
        """`get_state_snapshot()` 专用：现算 node_actions（见该方法 docstring
        "评委体验修复"节的历史记载）。

        【候选池口径选型】跟随 `RoomManager._resolve_and_broadcast_adjust`——
        房间目前唯一的换菜路径——既有选择：`agent.planning.planners.
        ils_planner._query_pois`/`_query_restaurants` 现场查（同 intent、同
        grounding 过滤），**不是** `api/_streams/graph_adjust.py` 改用的全量
        目录 `data.loader.load_pois`/`load_restaurants`。两者解决的是不同的
        历史包袱：单人版当年改全量目录是为了绕开"execute 阶段搜索 worker 的
        窄池覆盖不了 LLM 蓝图选中实体"这个坑（见该文件模块 docstring「候选池
        来源」），房间版从来没有"execute 阶段窄池"这段历史——`_query_pois`/
        `_query_restaurants` 一直是房间侧唯一用过的口径，没有理由在同一个
        房间内引入第二套并存的候选池来源。

        【组装手法】与 `_resolve_and_broadcast_adjust` 换菜成功后重算
        node_actions 同一先例：`generate_template_node_chips`（模板路径，
        零 LLM 调用、纯函数生成 chips）+ `_build_node_actions`（chips +
        `feasible_alternatives` 组装，同样是纯函数评分，不额外调用 LLM）——
        "现算"这件事本身不该因为走了 LLM 而变贵/变慢，加入房间是高频动作。

        【异常兜底】intent/itinerary 反序列化失败、`feasible_alternatives`
        对某节点之外更大范围的未预料异常等，都不能让 `get_state_snapshot()`
        整体失败——`join()` 里 `await self._send(ws, room.get_state_
        snapshot())` 一旦抛异常，新成员的整条 WS 连接都建立不起来，比"没有
        调整按钮"这个待修的原问题严重得多。失败时返回空字典，调用方按既有
        "无内容不加字段"纪律（同 `_resolve_and_broadcast_adjust`/
        `_graph_adjust` 的 `if node_actions:`）省略这个键。
        """
        if not self.current_itinerary_dict or not self.current_intent_dict:
            return {}
        try:
            from agent.core.trace import Tracer
            from agent.graph.nodes.narrate import _build_node_actions
            from agent.intent.narrator import generate_template_node_chips
            from agent.planning.planners.ils_planner import _query_pois, _query_restaurants
            from schemas import IntentExtraction, Itinerary

            itinerary = Itinerary.model_validate(self.current_itinerary_dict)
            intent = IntentExtraction.model_validate(self.current_intent_dict)
            tracer = Tracer()
            pois = _query_pois(intent, tracer)
            restaurants = _query_restaurants(intent, tracer)
            node_chips = generate_template_node_chips(itinerary, intent, pois, restaurants)
            return _build_node_actions(itinerary, intent, pois, restaurants, node_chips)
        except Exception:  # noqa: BLE001
            logger.warning(
                "get_state_snapshot 组装 node_actions 失败，快照将省略该字段：room_id=%s",
                self.room_id, exc_info=True,
            )
            return {}


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

        义务分发表（route_turn 的 RouteKind → 房内动作；ADR-0011 E-2-c 6 标签闭集：
        planning/feedback/chitchat/confirm/clarify/defense）：
        - feedback              → 现有约束池 + 重排路径（原样保留；诉求台账是 F-2/F-5
                                   的事，本步不建，`room.constraints` 仍是唯一台账）
        - planning               → 全新规划（`_trigger_fresh_plan`，与单人 `_plan_fresh`
                                   同款一次性 session_id；**不**进约束池、不经 refiner
                                   合并——这是一句完整规划请求，不是"追加约束"）
        - 其余（chitchat/confirm/clarify/defense，decision 必然非空）
                                 → 气泡广播：把 RouterDecision 原样以 `chitchat_reply`
                                   事件推给全员（复用主聊天已有的事件形状，前端
                                   `handleEvent` 的 `chitchat_reply` case 零改动即可渲染）；
                                   不碰方案、不碰约束池、不中断在跑的规划任务。
                                   安全婉拒（defense，含注入防御）与澄清引导（clarify +
                                   地板 chips）、确认引导（confirm + 确认预约 chip）走的
                                   是同一条分支——它们只是 decision 内容不同，不需要
                                   单独判支。

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
            from agent.context.sources import RoomSource
            from agent.core.llm_client import get_llm_client
            from agent.routing.route_turn import route_turn

            outcome = route_turn(
                text,
                room.current_itinerary_dict,
                user_id,
                client=get_llm_client(),
                # ADR-0011 决策 3 底座无关增补：房间与单人主聊天共用同一个打包器，
                # 只是来源实现不同（RoomSource 读 Room 既有字段，见 agent/context/sources.py）。
                context_source=RoomSource(room),
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

        赞锁定根治批（曾经的"纯展示"声明就此作废）：`locked_stages` 不再只是
        展示态——like 同时把实体级锁登记进 `room.locked_targets`（归名：谁赞的，
        见该字段 docstring），下一轮反馈重排时经 `_replan_with_refiner` 翻译成
        图状态 `pinned_targets`，由蓝图 LLM「必须保留」先验 + critic 硬判据 +
        `plan_hybrid(pinned=...)` 全阶梯承接；保不住必有归名告知（L0）。
        "踩只解锁本段"的既有保证原样保留（dislike 同步注销实体级锁）。
        方案尚未产出时 like 只更新下标集合（无实体可锁，登记跳过——纯展示，
        与旧行为一致；等方案出来后这类空挂下标会被 `_sync_locks_with_itinerary`
        收敛掉）。

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

            # 更新锁定集合（下标投影）+ 实体级锁登记（真值，见方法 docstring）
            if action == "like":
                room.locked_stages.add(stage_index)
                node = self._stage_node(room, stage_index)
                if node is not None and node.get("target_id"):
                    entry = room.locked_targets.setdefault(
                        node["target_id"],
                        {
                            "kind": node.get("target_kind"),
                            "name": node.get("title") or node["target_id"],
                            "lockers": [],
                        },
                    )
                    if user_id not in entry["lockers"]:
                        entry["lockers"].append(user_id)
            elif action == "dislike":
                room.locked_stages.discard(stage_index)
                node = self._stage_node(room, stage_index)
                if node is not None and node.get("target_id"):
                    room.locked_targets.pop(node["target_id"], None)

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
        # 赞锁定根治批：换菜是成员归名的显式公开动作（node_locked 广播 + 归名
        # 说明），不是引擎静默行为——被换走的实体若曾被赞锁定，锁随实体消失
        # 收敛（对不存在实体的锁是悬空引用）；locked_stages 同步重投影。
        self._sync_locks_with_itinerary(room)

        session_id = room.session_id or f"collab_{room.room_id}"
        from api._session_store import sync_snapshot

        sync_snapshot(session_id, itinerary=new_itinerary.model_dump())

        new_title = node_title(new_itinerary, result.swapped_to or "")
        base_text = self._build_room_narration(action, nickname, old_title, new_title, adjustment)
        # 文案修缮批（G1 实锤，房间侧同款）：降级换菜时确认句+最接近告知合并
        # 成一句诚实告知（归名版），店名不再说两遍——与单人 SSE 路径共用同一
        # 收口（见 api/_streams/graph_adjust.py::compose_swap_success_narration
        # docstring；理想归属地 node_swap_support 待收口批搬家）。
        from agent.planning.planners.node_swap_support import adjustment_descriptor
        from api._streams.graph_adjust import compose_swap_success_narration

        narration_text = compose_swap_success_narration(
            base_text,
            advisory_dicts,
            old_title=old_title,
            new_title=new_title,
            descriptor=adjustment_descriptor(adjustment) if adjustment is not None else "",
            requester=nickname,
        )

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
        # 赞锁定根治批：planning 义务=重开一局，旧方案的赞锁定随旧方案作废——
        # 与图状态 pinned_targets 的 EPISODE_SCOPED 重置（intent_node）对齐；
        # 陈旧锁若留着，下一轮反馈会把它翻译到全新方案的无关实体上（真错误）。
        room.locked_targets.clear()
        room.locked_stages.clear()

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
        2. 如果有 current_intent → 持久线程注入+续跑（`_replan_with_refiner`，
           房间重排根治批：refiner 直调 + aupdate_state + astream(None)）
        3. 如果没有 current_intent → 用约束文本作为初始输入走 `_plan_fresh`
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
        """反馈轮重排——持久 graph 线程「状态注入 + 续跑」（房间重排根治批，方案 d）。

        【病灶（治的是这个）】旧实现是降维复刻：图外调 `refine_intent` 合并反馈，
        再把合并后文本塞进**全新一次性** graph session（`collab_{room_id}_{ts}`）跑
        完整规划。五类损失：① 路由义务误判——新 session 的 router 对合成文本重新
        判义务，真实 LLM 实测会判成非规划，整轮不出方案（点火冒烟 H3 实锤）；
        ② 合并精度——refiner 只看到拼接文本，看不到图状态里的活上下文（版本志/
        台账切片）；③ 出处链断；④ 诉求台账不延续；⑤ 版本志断。

        【根治】房间维护稳定持久线程（thread_id=`collab_{room_id}`，同 `_plan_fresh`），
        反馈轮用 LangGraph 原生 `aupdate_state(as_node="refiner")` 注入"反馈已合并"
        状态，`astream(None)` 从 refiner 出边续跑——与单人反馈轮走同一条管线，router
        不再执行（义务判定在房间层 `add_constraint` 的 route_turn 已经做过，不给它
        第二次误判的机会）。配方经 `scripts/spike_room_resume.py` 实证：终态 43 键
        与正常单人反馈轮全等、核心事件逐字节等价、连续注入稳定；固化断言见
        `tests/test_room_persistent_resume.py`（金标准对比 + 取消坑自愈）。

        【赞锁定根治批】注入 values 额外携带 `pinned_targets`（实体级锁登记
        `room.locked_targets` 的翻译，plain dict 形态；见步骤 3.5 注释），续跑
        管线全阶梯承接（蓝图先验/critic 硬判据/plan_hybrid 原生保护）；续跑
        结束后本方法做锁定出口终检——没保住的归名告知、锁登记按新方案收敛
        （步骤 8）。

        【注入禁写清单（spike 实锤，违反即炸）】
        - `intent` 必须是活 IntentExtraction 对象，绝不 `model_dump()`——spike 实测
          dict-intent 让 planner 崩 AttributeError、连 rule_floor 兜底一起崩，且
          checkpoint 从此永久污染；
        - `plan_version_log` 绝不写（operator.add 通道，写了=追加垃圾）；
        - `demand_ledger` 绝不写非空值（merge 语义"非空=整体替换"，会顶掉台账）；
        - 旧 itinerary / 旧 episode 值绝不写（必须让 reset diff 的 None 落进去）；
        - 白名单外 Pydantic 对象绝不注入——失败模式是**无声类型擦除**（读回静默变
          dict、零告警）；若未来注入需要新业务类型，必须同步补
          `agent/graph/build.py::_build_checkpoint_serde` 白名单并写测试钉住。
        """
        from langchain_core.messages import HumanMessage

        from agent.graph.build import get_compiled_graph
        from agent.graph.nodes.refiner import refiner_node
        from agent.graph.sse_adapter import run_graph_resume_stream
        from schemas.intent import IntentExtraction
        from schemas.itinerary import Itinerary

        session_id = f"collab_{room.room_id}"
        graph = get_compiled_graph()
        config: dict[str, Any] = {"configurable": {"thread_id": session_id}}

        # ---- 步骤 1：先读——refiner 要看到旧 intent + 被拒的旧 itinerary ----
        snap = await graph.aget_state(config)
        base: dict[str, Any] = dict(snap.values) if snap is not None and snap.values else {}

        # 线程冷启动垫底（spike 未测、本批实证补上，见 test_room_persistent_resume）：
        # 房间基线可能来自 HTTP 建房带入的 SESSION_STORE 快照 / 测试 fixture 直塞
        # dict——此时 collab_{room_id} 线程还没有任何 checkpoint。用房间投影还原出
        # **活的** Pydantic 对象垫进 base（IntentExtraction/Itinerary 均在 serde
        # 白名单内，且 intent 会随注入落 checkpoint——见禁写清单第一条）。
        if base.get("intent") is None and room.current_intent_dict:
            base["intent"] = IntentExtraction.model_validate(room.current_intent_dict)
        # refiner 的"被拒的上一版"摘要素材：房间投影优先，checkpoint 兜底。
        # current_itinerary_dict 是"当前展示给成员的方案"的权威——两类场景下它比
        # 图状态新：① 节点换菜 `adjust()` 只改投影、不回写图状态（与单人
        # `/chat/adjust` 的 aupdate_state 回写不同）；② 中途取消坑（本批显式处理）：
        # 上一轮反馈在注入后、续跑完成前被 planning_task.cancel() 打断，线程停在
        # "episode 已 reset（itinerary=None）、方案未产出、next 非空"的中间态，
        # 图状态里根本没有 itinerary 可读，refiner 会丢失判断素材。
        # 纪律：只进 refiner 的读取层（base），绝不注入回图状态（注入 values 里
        # itinerary 恒为 reset diff 的 None，见禁写清单）。
        if room.current_itinerary_dict:
            try:
                base["itinerary"] = Itinerary.model_validate(room.current_itinerary_dict)
            except Exception:  # noqa: BLE001 —— 摘要素材是判断辅料，坏投影不拦重排
                logger.warning(
                    "room replan: current_itinerary_dict 反序列化失败，refiner 退用"
                    "checkpoint 里的上一版（可能略旧）：room_id=%s",
                    room.room_id, exc_info=True,
                )
        if base.get("intent") is None:
            # 防御兜底：无基线 intent（调用方 _run_planning 的分支保证不会到这）——
            # refiner 无从合并，退回全新规划，不让反馈静默丢失。
            await self._plan_fresh(room, feedback or "帮我规划一个下午")
            return

        # ---- 步骤 2：叠本轮反馈层 ----
        base["user_input"] = feedback
        base["messages"] = list(base.get("messages") or []) + [HumanMessage(content=feedback)]

        # ---- 步骤 3：直调真节点函数（图外可调，spike 实证与图内零漂移）----
        # 绝不手工挑子集：diff 含 reset_for_new_episode() 全部 EPISODE_SCOPED 键 +
        # 精炼后 intent + refinement_changed_fields/note，漏键=旧 episode 残值污染
        # 新一轮。asyncio.to_thread：refiner 内部是同步 LLM 调用（真实模式数秒），
        # 不能挂死房间事件循环（广播/其它成员消息全停）——同 graph_confirm 对
        # execute_finalize_node 的既有先例。
        refiner_diff = await asyncio.to_thread(refiner_node, base)
        refined_intent = refiner_diff.get("intent")
        if refined_intent is None:
            # refiner_node 对 intent/feedback 缺失返回 {}——同上防御兜底
            await self._plan_fresh(room, feedback or "帮我规划一个下午")
            return

        # ---- 步骤 3.5：锁定清单翻译（赞锁定根治批）----
        # 实体级锁登记（update_vote 归名写入）→ 图状态 pinned_targets。注入形态
        # 是 plain dict {"kind","target_id","name"}（serde 白名单外 Pydantic 对象
        # 会无声类型擦除，见 docstring 禁写清单最后一条；plain dict 免白名单）；
        # lockers（谁锁的）留在房间侧 locked_pins 供出口检查归名——引擎不需要
        # "谁锁的"，房间概念不泄漏进规划层。无锁时注入空列表，与单人反馈轮
        # reset_for_new_episode() 的零值逐字节一致（金标准对比测试的前提）。
        locked_pins = self._locked_pin_entries(room)

        # ---- 步骤 4+5：注入（as_node="refiner" = "router 判 feedback + refiner
        # 跑完"两节点合起来对 state 的全部写入；禁写清单见 docstring）----
        values: dict[str, Any] = {
            **refiner_diff,
            "user_input": feedback,       # finalize_plan 的版本志 snippet 读它
            "route_kind": "feedback",     # 版本志 trigger 判据 + 事件生命周期语义
            "router_decision": None,      # Layer 1 强信号路径 decision 本就是 None
            "messages": [HumanMessage(content=feedback)],  # add_messages 通道：追加会话日志
            # SESSION_SCOPED 透传（同 make_initial_state 对这两键"确认非重置"的语义）：
            # 冷启动线程上它们从未被写过，续跑链路的读者（profile worker 等）需要之。
            "user_id": room.owner_id,
            "session_id": session_id,
            # 赞锁定根治批：锁定清单（见上方步骤 3.5 注释；覆盖 refiner_diff 里
            # reset 出的空值）。消费者：planner_node（蓝图用户消息先验）/
            # critic_node（硬判据）/ ils_replan_node（plan_hybrid 原生保护）。
            "pinned_targets": [
                {"kind": p["kind"], "target_id": p["target_id"], "name": p["name"]}
                for p in locked_pins
            ],
        }
        await graph.aupdate_state(config, values, as_node="refiner")

        # 房间投影同步（与旧实现同一时机：合并定稿即更新，不等续跑出方案）
        room.current_intent_dict = refined_intent.model_dump()
        changed_fields = list(refiner_diff.get("refinement_changed_fields") or [])
        refiner_note = refiner_diff.get("refinement_note") or "已合并你的反馈，正在重新规划。"

        # llm_context 三件套（本批不清退，路演后分期删）：保持既有摘要记账
        self._append_to_llm_context(
            room, role="assistant",
            content=f"已合并约束：{'; '.join(changed_fields or ['无变更'])}。"
                    f"调整后意图：距离{refined_intent.distance_max_km}km，"
                    f"饮食约束{list(refined_intent.dietary_constraints)}。"
        )

        # ---- 步骤 6：合成补发 4 条前奏事件 ----
        # 续跑不再执行 router/refiner，这两个节点在单人 SSE 里的事件由房间侧按
        # 同一 payload 形状补齐（emit_router feedback 分支 + emit_refiner，见
        # agent/graph/_emit_handlers.py）：前端 dispatchPlanningEvent 靠它们
        # 清屏/更新意图面板，中途加入者靠 planning_events_history 回放重建快照，
        # 缺一条链就断。
        prelude: list[tuple[str, dict[str, Any]]] = [
            ("agent_thought", {"text": "收到反馈，正在调整……"}),
            ("refinement_start", {"feedback_text": feedback}),
            (
                "refinement_done",
                {
                    "refined_intent": refined_intent.model_dump(),
                    "changed_fields": changed_fields,
                    "refiner_note": refiner_note,
                },
            ),
            # 新意图重推 intent_parsed，前端 IntentSummary 靠它刷新（同 emit_refiner）
            ("intent_parsed", refined_intent.model_dump()),
        ]
        for event_type, payload in prelude:
            await self._broadcast_planning_event(room, {
                "type": event_type,
                "seq": 0,
                "payload": payload,
                "timestamp_ms": int(time.time() * 1000),
            })

        # ---- 步骤 7：astream(None) 续跑，走现有 emit dispatch ----
        # 顺手收集引擎侧 pinned 相关 advisory code（emit_narrate 的 messages
        # 附加通道），给步骤 8 的出口归名告知提供"为什么没保住"的原因短句。
        seen_advisory_codes: list[str] = []
        async for event in run_graph_resume_stream(session_id=session_id, user_input=feedback):
            await self._broadcast_planning_event(room, event.model_dump())
            if event.type.value == "itinerary_ready":
                room.current_itinerary_dict = event.payload
                summary = event.payload.get("summary", "行程已生成")
                self._append_to_llm_context(
                    room, role="assistant", content=f"已重新规划行程：{summary}"
                )
            elif event.type.value == "agent_narration" and isinstance(event.payload, dict):
                for m in event.payload.get("messages") or []:
                    if isinstance(m, dict) and m.get("kind") == "advisory" and m.get("code"):
                        # code 按 .value 归一化：房间广播走 model_dump()（python
                        # mode），Advisory.code 是活的 AdvisoryCode 枚举实例而非
                        # plain str（同 build.py serde 白名单注释记载的 Enum 字段
                        # 现象）；str(枚举) 是 "AdvisoryCode.X" 不是码值，直接
                        # str 会让下方原因匹配永远落空（stub 点火实测踩中）。
                        code = m["code"]
                        seen_advisory_codes.append(str(getattr(code, "value", code)))

        # ---- 步骤 8：锁定出口检查 + 锁收敛（赞锁定根治批）----
        # 对"最终交付给成员的方案"做锁定实体成员资格终检：没保住的逐个归名
        # 告知（谁锁的、什么、为什么——L0 绝不静默丢锁，全路径覆盖的唯一收口，
        # 见 _announce_lost_locks docstring）；然后按新方案收敛锁登记 + 重投影
        # locked_stages（下一轮反馈的锁翻译依赖它，见 _sync_locks_with_itinerary）。
        if locked_pins:
            final_ids = set(self._itinerary_mid_target_index(room.current_itinerary_dict))
            lost = [p for p in locked_pins if p["target_id"] not in final_ids]
            if lost:
                await self._announce_lost_locks(room, lost, seen_advisory_codes)
        self._sync_locks_with_itinerary(room)

    async def _plan_fresh(self, room: Room, user_input: str) -> None:
        """全新规划路径（无基线开局 / planning 义务重开一局），带 LLM 上下文。"""
        # 尝试用 LangGraph 或 ReAct agent
        try:
            from agent.graph.sse_adapter import run_graph_stream
            from agent.graph.build import get_compiled_graph
            get_compiled_graph()

            user_id = room.owner_id
            # 房间重排根治批：稳定持久线程。历史实现是一次性 `collab_{room_id}_{ts}`
            # ——当年注释写"避免 LangGraph checkpoint 恢复旧 state"，如今整句反转为
            # 特性：恢复旧 state 正是我们要的。反馈轮 `_replan_with_refiner` 对同一
            # 线程注入+续跑，messages/plan_version_log/demand_ledger 从此跨轮延续；
            # planning 义务重开一局也走同一线程，与单人多轮会话同构（router 判
            # 新需求时 intent_node 自己会做 episode 级重置，不需要靠换线程隔离）。
            session_id = f"collab_{room.room_id}"

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

            # 赞锁定根治批：新方案落地后收敛锁登记（正常入口 `_trigger_fresh_plan`
            # 已清空，这里是防御性收口——本方法还被 `_replan_with_refiner` 的
            # 兜底分支/`_run_planning` 无基线分支直调，那些路径不经清空）。
            self._sync_locks_with_itinerary(room)

        except (ImportError, Exception):
            # LangGraph 不可用 → 走 rule planner
            await self._run_rule_planner_and_broadcast(room, user_input)

    # （房间重排根治批删除记录）`_run_planner_and_broadcast` + `_run_rule_planner_
    # fallback` 已整体删除：前者是旧"图外 refine 后把合成 raw_input 重进全新一次性
    # graph session"路径的后半段（唯一调用方 `_replan_with_refiner` 已改为持久线程
    # 注入+续跑，见该方法 docstring），后者是前者的专属兜底，随之成为死代码。

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


    def _stage_target_id(self, room: Room, stage_index: int) -> Optional[str]:
        """`update_vote` 点踩收编（F-5）用：把 `stage_index`（前端时间轴的可见段
        编号——跳过首尾 home 的 mid nodes 顺序）
        翻译成 `adjust()` 需要的 `target_id`（`ActivityNode.target_id`）。

        （历史注：曾有产出展示标题的姊妹方法 `_get_stage_title`，ADR-0013 留痕记为死代码，2026-07-04 已删——它产出的是"展示
        用标题"，本方法产出的是"引擎定位用 id"，两者消费方不同（前者给旧的
        约束文案合成，后者给新的局部重解引擎），故意不复用同一个返回值语义。
        找不到（尚未出方案 / index 越界）返回 `None`，调用方按"静默丢弃"处理。
        """
        node = self._stage_node(room, stage_index)
        return node.get("target_id") if node is not None else None

    def _stage_node(self, room: Room, stage_index: int) -> Optional[dict[str, Any]]:
        """把可见段下标解析成当前方案里的节点 dict（赞锁定根治批抽出：
        `_stage_target_id` 只要 id，`update_vote` 的实体级锁登记还要
        target_kind/title——同一份"跳过首尾 home 的 mid nodes 顺序"口径）。"""
        if not room.current_itinerary_dict:
            return None
        nodes = room.current_itinerary_dict.get("nodes")
        if not isinstance(nodes, list):
            return None
        mid_nodes = [n for n in nodes if isinstance(n, dict) and n.get("target_kind") != "home"]
        if 0 <= stage_index < len(mid_nodes):
            return mid_nodes[stage_index]
        return None

    # ============================================================
    # 赞锁定根治批：锁 ↔ 方案 一致性 + 出口归名告知
    # ============================================================

    @staticmethod
    def _itinerary_mid_target_index(itinerary_dict: Optional[dict[str, Any]]) -> dict[str, int]:
        """当前方案 mid 节点的 target_id → 可见段下标（同 `_stage_node` 口径；
        同一实体出现多次时取首个下标）。方案缺失/畸形 → 空 dict。"""
        if not itinerary_dict:
            return {}
        nodes = itinerary_dict.get("nodes")
        if not isinstance(nodes, list):
            return {}
        out: dict[str, int] = {}
        idx = 0
        for n in nodes:
            if not isinstance(n, dict) or n.get("target_kind") == "home":
                continue
            tid = n.get("target_id")
            if tid and tid not in out:
                out[tid] = idx
            idx += 1
        return out

    def _sync_locks_with_itinerary(self, room: Room) -> None:
        """方案变更后的锁收敛：`locked_targets` 剔除已不在方案里的实体，
        `locked_stages` 按新方案重投影到新下标。

        调用时机 = 一切"当前方案换了一版"的收口点：反馈重排出方案后
        （`_replan_with_refiner` 出口检查之后——检查要用收敛前的登记判断谁丢了）、
        节点换菜成功后（`_resolve_and_broadcast_adjust`）、全新规划出方案后
        （`_plan_fresh`，此时登记多半已被 `_trigger_fresh_plan` 清空，重投影
        天然为空）。已知展示时差：前端只在 `room_state` 快照与 `vote_updated`
        事件里刷新 locked_stages，重投影结果要等下一次这两类消息才可见——
        本批不新增 WS 事件类型（前端契约不动），数据层先保证正确。
        """
        id_to_idx = self._itinerary_mid_target_index(room.current_itinerary_dict)
        room.locked_targets = {
            tid: entry for tid, entry in room.locked_targets.items() if tid in id_to_idx
        }
        room.locked_stages = {id_to_idx[tid] for tid in room.locked_targets}

    def _locked_pin_entries(self, room: Room) -> list[dict[str, Any]]:
        """把实体级锁登记翻译成"本轮重排要保护什么"的清单（含归名 lockers，
        供出口检查点名；注入图状态前由调用方剥掉 lockers——引擎不需要"谁锁的"，
        房间概念不进规划层）。防御性过滤：只保护当前方案里真实存在的实体
        （登记与方案的同步靠 `_sync_locks_with_itinerary`，这里再兜一层）。"""
        id_to_idx = self._itinerary_mid_target_index(room.current_itinerary_dict)
        out: list[dict[str, Any]] = []
        for tid, entry in room.locked_targets.items():
            if tid not in id_to_idx or entry.get("kind") not in ("poi", "restaurant"):
                continue
            out.append(
                {
                    "kind": entry["kind"],
                    "target_id": tid,
                    "name": entry.get("name") or tid,
                    "lockers": list(entry.get("lockers") or []),
                }
            )
        return out

    # 引擎侧 pinned 相关 advisory code → 出口归名告知里的原因短句。
    _PIN_LOSS_REASON_BY_CODE: dict[str, str] = {
        "no_matching_candidates": "新的条件下候选里找不到它（可能被距离或筛选条件排除了）",
        "pinned_unsatisfiable": "时间和路线里实在塞不进",
        "pinned_dropped_in_repair": "为了解决别的冲突被换掉了",
    }

    async def _announce_lost_locks(
        self, room: Room, lost: list[dict[str, Any]], seen_advisory_codes: list[str]
    ) -> None:
        """出口归名告知（L0「绝不默默忽略」的房间侧收口）：锁定实体没能留在
        新方案里 → 逐个点名"谁锁的、什么没保住、为什么"。

        为什么在房间侧再兜一层而不是全信引擎 advisory：引擎的告知通道
        （plan_hybrid advisories → narrate → messages）覆盖 ILS 路径与
        rule/give_up 补产（见 replan.py::_pinned_missing_advisories），但
        ①它不知道昵称（归名是房间概念，刻意不进规划层）；②修复阶梯若在
        backprompt 一级就以"critic 放行了别的方案"收场（理论上 critic 硬判据
        不放行缺锁方案，但 drain_on_error 的 rule_floor 降级等旁路仍可能交付
        缺锁方案且无 advisory）——房间对"最终交付给成员的方案"做成员资格
        终检，是唯一能同时保证归名与全路径覆盖的位置。原因短句优先采用本轮
        续跑流里实际出现过的引擎 advisory code（如实转述"为什么"），没有就
        用诚实的通用句。
        """
        reason = ""
        for code in seen_advisory_codes:
            if code in self._PIN_LOSS_REASON_BY_CODE:
                reason = self._PIN_LOSS_REASON_BY_CODE[code]
                break
        for entry in lost:
            locker_names = [
                room.members[uid].nickname if uid in room.members else uid
                for uid in (entry.get("lockers") or [])
            ]
            who = "、".join(locker_names) if locker_names else "有人"
            clause = reason or "新的要求下实在排不进这一版"
            await self._broadcast_planning_event(room, {
                "type": "agent_narration",
                "seq": 0,
                "payload": {
                    "text": (
                        f"{who}锁定的「{entry.get('name') or entry.get('target_id')}」"
                        f"这轮没保住——{clause}。想留它的话再说一声，我下一轮优先把它排回去。"
                    ),
                    "stage": "stream",
                },
                "timestamp_ms": int(time.time() * 1000),
            })

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
