"""test_room_adjust_and_ttl —— ADR-0013 F-5「房间协商收尾」新增行为验收。

覆盖任务书四件事里除「台账进快照」外的另外三件（台账快照键集已在
`test_room_lifecycle_characterization.py::test_snapshot_key_shape_includes_demand_ledger`
钉住）：

1. 房间换菜通路（`RoomManager.adjust()`）：WS "adjust" 消息复用 F-4 单人链路
   同一个引擎 `resolve_node_swap`，房间侧现场重查候选池（不平行新建）、归名
   记账、处理期锁定广播（`node_locked`/`node_unlocked`）、串行处理。
2. 点踩收编：`update_vote` 的 dislike 分支改调 `adjust()`（本文件从 adjust()
   自身视角测；`update_vote` 的分发口径已在
   `test_room_lifecycle_characterization.py::
   test_snapshot_votes_and_locked_stages_evolve_with_update_vote` 钉住）。
3. TTL 清扫器：`RoomManager.sweep_expired_rooms()` 惰性清扫，时间可注入
   （`now=` 形参），且绝不清扫仍有在线 WS 连接的房间。

驱动手法沿用既有三个特征化文件的先例：`RoomManager` 直驱 + 假 WebSocket（记录
`send_json` 调用列表），不起真 ASGI/WS 服务；itinerary/intent 复用
`tests/test_critics_v2.py` 的 `_make_intent`/`_make_legal_itinerary`（真实 mock
数据集 id：P040 poi / R001 餐厅），换菜引擎因此走的是真实
`ils_planner._query_pois`/`_query_restaurants` 召回（不像 F-4 单人测试那样用
全合成候选池——房间版本来就没有现成候选池，任务书明写"复用同款召回"）。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any

import pytest

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from collab import RoomManager  # noqa: E402
from api._streams.models import (  # noqa: E402
    AdjustActionAdjust,
    AdjustActionAlternative,
    AdjustActionDislike,
)
from schemas.demand_ledger import LedgerEntryStatus  # noqa: E402
from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension  # noqa: E402
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary  # noqa: E402


class _FakeWebSocket:
    """同 `test_room_lifecycle_characterization.py::_FakeWebSocket`——只记录
    `send_json` 调用列表，不做真实网络 I/O，用于断言广播 payload 具体内容。
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


def _seed_room(owner_id: str) -> tuple[RoomManager, "Room"]:  # noqa: F821 - Room 仅类型注释
    manager = RoomManager()
    room = manager.create_room(owner_id=owner_id, nickname="发起人")
    room.current_intent_dict = _make_intent().model_dump()
    room.current_itinerary_dict = _make_legal_itinerary().model_dump()
    return manager, room


def _broadcast_types(ws: _FakeWebSocket) -> list[str]:
    """把送达一个成员的原始消息列表拍平成"顶层 type，`planning_event` 再展开
    一层内层 event.type"——同一份序列既能看到锁定广播又能看到规划事件类型。
    """
    out = []
    for m in ws.sent:
        if m.get("type") == "planning_event":
            out.append(f"planning_event:{m['event'].get('type')}")
        else:
            out.append(m.get("type"))
    return out


def _planning_event_payloads(ws: _FakeWebSocket, event_type: str) -> list[dict[str, Any]]:
    return [
        m["event"]["payload"]
        for m in ws.sent
        if m.get("type") == "planning_event" and m["event"].get("type") == event_type
    ]


# ============================================================
# 1. 房间换菜通路：adjust action 成功——归名记账 + 锁定广播 + 台账快照
# ============================================================


def test_adjust_action_success_records_ledger_with_attribution_and_broadcasts_lock_sequence():
    async def scenario():
        manager, room = _seed_room("owner_adjust_test")
        owner_ws = _FakeWebSocket()
        await manager.join(room, "owner_adjust_test", "发起人", owner_ws)
        p_ws = _FakeWebSocket()
        await manager.join(room, "participant_adjust_test", "小李", p_ws)
        owner_ws.sent.clear()

        action = AdjustActionAdjust(
            adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.DIETARY, value="不辣"),
            label="不辣的",
        )
        await manager.adjust(room, "participant_adjust_test", "R001", action)

        # 广播形状：node_locked → 规划事件（itinerary_ready + agent_narration）→ node_unlocked
        types_ = _broadcast_types(owner_ws)
        assert types_ == [
            "node_locked",
            "planning_event:itinerary_ready",
            "planning_event:agent_narration",
            "node_unlocked",
        ], f"锁定态广播形状不符，实际={types_}"

        lock_msg = owner_ws.sent[0]
        assert lock_msg == {
            "type": "node_locked",
            "node_id": "R001",
            "by_user": "participant_adjust_test",
            "nickname": "小李",
        }
        unlock_msg = owner_ws.sent[-1]
        assert unlock_msg == {"type": "node_unlocked", "node_id": "R001"}

        # 归名说明：文案里点名是谁提的（不是 F-4 单人版含糊的"你"）
        narration = _planning_event_payloads(owner_ws, "agent_narration")[0]
        assert "小李" in narration["text"]
        assert "换成" in narration["text"]
        assert "node_actions" in narration
        assert "demand_ledger" in narration

        # 方案真的换了（R001 是这个 itinerary 里唯一的 restaurant 节点）
        new_ids = [n["target_id"] for n in room.current_itinerary_dict["nodes"]]
        assert "R001" not in new_ids
        assert "P040" in new_ids, "poi 节点不应被这次餐厅调整波及"

        # 诉求台账：归名记账（member_id/nickname），维度匹配
        assert len(room.demand_ledger) == 1
        entry = room.demand_ledger[0]
        assert entry["member_id"] == "participant_adjust_test"
        assert entry["nickname"] == "小李"
        assert entry["adjustment"]["dimension"] == "dietary"
        assert entry["source_text"] == "不辣的"

        # 台账已接入快照（同 test_room_lifecycle_characterization 钉住的键集）
        snapshot = room.get_state_snapshot()
        assert len(snapshot["demand_ledger"]) == 1
        assert snapshot["demand_ledger"][0]["nickname"] == "小李"

    asyncio.run(scenario())


def test_adjust_action_tier1_satisfied_marks_ledger_satisfied():
    """`不辣` 命中同子类满足（tier 1/2）时，诉求应标 SATISFIED——同 F-4
    `mark_satisfied` 消费口径（房间版复用同一个 helper，见 room.py::
    `_resolve_and_broadcast_adjust`）。"""

    async def scenario():
        manager, room = _seed_room("owner_tier_test")
        ws = _FakeWebSocket()
        await manager.join(room, "owner_tier_test", "发起人", ws)

        action = AdjustActionAdjust(
            adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.DIETARY, value="不辣")
        )
        await manager.adjust(room, "owner_tier_test", "R001", action)

        assert room.demand_ledger[0]["status"] == LedgerEntryStatus.SATISFIED.value

    asyncio.run(scenario())


# ============================================================
# 2. 具名备选：候选池收窄——点哪个真的换成哪个，不记账
# ============================================================


def test_adjust_alternative_swaps_to_exactly_chosen_target_and_does_not_record_ledger():
    async def scenario():
        manager, room = _seed_room("owner_alt_test")
        ws = _FakeWebSocket()
        await manager.join(room, "owner_alt_test", "发起人", ws)
        ws.sent.clear()

        # R017（本帮菜）是真实 mock 目录里与 R001 不同菜系的另一家餐厅
        # （见任务实现时的候选池探针：_query_restaurants(_make_intent()) 命中）。
        action = AdjustActionAlternative(target_id="R017")
        await manager.adjust(room, "owner_alt_test", "R001", action)

        new_ids = [n["target_id"] for n in room.current_itinerary_dict["nodes"]]
        assert "R017" in new_ids
        assert "R001" not in new_ids
        assert room.demand_ledger == [], "具名备选是「选定」不是「诉求」，不记账（同 F-4 口径）"

        narration = _planning_event_payloads(ws, "agent_narration")[0]
        assert "发起人" in narration["text"]

    asyncio.run(scenario())


# ============================================================
# 3. 点踩：不记账 + 归名说明（与 update_vote 的分发口径互补覆盖）
# ============================================================


def test_adjust_dislike_action_does_not_record_ledger_and_narrates_with_nickname():
    async def scenario():
        manager, room = _seed_room("owner_dislike_test")
        ws = _FakeWebSocket()
        await manager.join(room, "owner_dislike_test", "老王", ws)
        ws.sent.clear()

        await manager.adjust(room, "owner_dislike_test", "P040", AdjustActionDislike())

        types_ = _broadcast_types(ws)
        assert types_[0] == "node_locked"
        assert types_[-1] == "node_unlocked"
        assert room.demand_ledger == [], "点踩收编为无方向局部重解，不是诉求，不记账"

        new_ids = [n["target_id"] for n in room.current_itinerary_dict["nodes"]]
        assert "P040" not in new_ids
        assert "R001" in new_ids, "餐厅节点不应被这次 poi 点踩波及"

        narration = _planning_event_payloads(ws, "agent_narration")[0]
        assert "老王" in narration["text"]
        assert "踩" in narration["text"]

    asyncio.run(scenario())


# ============================================================
# 4. 边界：节点已不在方案 / 方案尚不存在——告知而不是崩连接
# ============================================================


def test_adjust_target_node_not_in_plan_narrates_without_raising():
    """并发下方案可能在点击的同时被换过——`node_id` 定位不到时必须降级为告知，
    绝不能让异常冒泡到 WS 层（那会被 `ws_collab` 的外层 except 误判为断线，
    对应触发 `manager.leave()`，是"因为一次换菜边界情况就把人踢下线"的真事故，
    见 `RoomManager.adjust` docstring）。
    """

    async def scenario():
        manager, room = _seed_room("owner_missing_node_test")
        ws = _FakeWebSocket()
        await manager.join(room, "owner_missing_node_test", "发起人", ws)
        ws.sent.clear()

        # 不应抛异常
        await manager.adjust(room, "owner_missing_node_test", "NOT_A_REAL_NODE", AdjustActionDislike())

        types_ = _broadcast_types(ws)
        assert types_ == ["node_locked", "planning_event:agent_narration", "node_unlocked"]
        narration = _planning_event_payloads(ws, "agent_narration")[0]
        assert narration["text"]  # 有告知文案，不是空气
        # 方案原样未变（room.current_itinerary_dict 仍是种子数据）
        assert [n["target_id"] for n in room.current_itinerary_dict["nodes"]] == [
            "home", "P040", "R001", "home",
        ]

    asyncio.run(scenario())


def test_adjust_before_any_plan_exists_narrates_without_raising():
    async def scenario():
        manager = RoomManager()
        room = manager.create_room(owner_id="owner_no_plan_test", nickname="发起人")
        ws = _FakeWebSocket()
        await manager.join(room, "owner_no_plan_test", "发起人", ws)
        ws.sent.clear()

        await manager.adjust(room, "owner_no_plan_test", "whatever", AdjustActionDislike())

        types_ = _broadcast_types(ws)
        assert types_ == ["node_locked", "planning_event:agent_narration", "node_unlocked"]

    asyncio.run(scenario())


# ============================================================
# 5. 串行：room.lock 保证同一房间多次调整请求排队处理，不交叉
# ============================================================


def test_concurrent_adjust_requests_are_serialized_not_interleaved():
    async def scenario():
        manager, room = _seed_room("owner_serial_test")
        ws = _FakeWebSocket()
        await manager.join(room, "owner_serial_test", "发起人", ws)
        ws.sent.clear()

        await asyncio.gather(
            manager.adjust(room, "owner_serial_test", "R001", AdjustActionDislike()),
            manager.adjust(room, "owner_serial_test", "P040", AdjustActionDislike()),
        )

        types_ = _broadcast_types(ws)
        # 两次各自的 node_locked/node_unlocked 严格配对，不交叉
        # （某次的 unlocked 必须在下一次的 locked 之前出现）
        depth = 0
        for t in types_:
            if t == "node_locked":
                depth += 1
                assert depth == 1, f"锁定态发生交叉（同时有多个节点在处理中），序列={types_}"
            elif t == "node_unlocked":
                depth -= 1
        assert depth == 0
        assert types_.count("node_locked") == 2
        assert types_.count("node_unlocked") == 2

        # 两个节点都真的各自换了（串行处理，后一个基于前一个的结果继续解）
        new_ids = [n["target_id"] for n in room.current_itinerary_dict["nodes"]]
        assert "R001" not in new_ids
        assert "P040" not in new_ids

    asyncio.run(scenario())


# ============================================================
# 6. TTL 清扫器（ADR-0013 决策 6）：惰性 + 时间可注入 + 在线连接护栏
# ============================================================


def test_sweep_expired_rooms_removes_idle_room_with_no_active_connections():
    manager = RoomManager()
    room = manager.create_room(owner_id="owner_ttl_expire_test")
    room.last_activity_at -= manager.ROOM_TTL_SECONDS + 1  # 模拟"很久以前"的最后活动

    swept = manager.sweep_expired_rooms()

    assert swept == [room.room_id]
    assert manager.get_room(room.room_id) is None
    assert room.room_id not in {r["room_id"] for r in manager.list_rooms()}


def test_sweep_expired_rooms_preserves_room_with_active_ws_connection():
    async def scenario():
        manager = RoomManager()
        room = manager.create_room(owner_id="owner_ttl_active_conn_test")
        ws = _FakeWebSocket()
        await manager.join(room, "owner_ttl_active_conn_test", "发起人", ws)
        room.last_activity_at -= manager.ROOM_TTL_SECONDS + 1

        swept = manager.sweep_expired_rooms()

        assert swept == [], "仍有在线 WS 连接的房间绝不能被清扫（演示护栏）"
        assert manager.get_room(room.room_id) is not None

    asyncio.run(scenario())


def test_sweep_expired_rooms_preserves_room_within_ttl_window():
    manager = RoomManager()
    room = manager.create_room(owner_id="owner_ttl_fresh_test")

    swept = manager.sweep_expired_rooms()

    assert swept == []
    assert manager.get_room(room.room_id) is not None


def test_sweep_expired_rooms_time_is_injectable_for_deterministic_testing():
    """`now=` 形参供测试确定性驱动——不依赖真实 sleep 50 分钟。"""
    manager = RoomManager()
    room = manager.create_room(owner_id="owner_ttl_injected_test")
    baseline = room.last_activity_at

    # 注入一个"刚好卡在 TTL 门槛内"的时间——不应清扫
    assert manager.sweep_expired_rooms(now=baseline + manager.ROOM_TTL_SECONDS) == []
    assert manager.get_room(room.room_id) is not None

    # 注入一个"明确超过 TTL"的时间——应清扫
    swept = manager.sweep_expired_rooms(now=baseline + manager.ROOM_TTL_SECONDS + 1)
    assert swept == [room.room_id]


def test_sweep_destroys_room_object_and_its_demand_ledger_together():
    """销毁=房间对象连台账全蒸发（ADR-0013 决策 6 原文）。"""
    manager = RoomManager()
    room = manager.create_room(owner_id="owner_ttl_ledger_test")
    room.demand_ledger.append({
        "member_id": "owner_ttl_ledger_test",
        "adjustment": {"dimension": "distance", "value": "closer"},
        "source_text": "更近的",
    })
    room_id = room.room_id

    manager.sweep_expired_rooms(now=room.last_activity_at + manager.ROOM_TTL_SECONDS + 1)

    assert manager.get_room(room_id) is None, "房间对象整体蒸发，台账不会游离存在"


def test_room_activity_refreshes_last_activity_at():
    """任何成员操作（join/adjust 等）都应刷新 `last_activity_at`——TTL 清扫的
    计时基准是"最后活动"，不是"创建时间"。"""

    async def scenario():
        manager, room = _seed_room("owner_activity_refresh_test")
        room.last_activity_at = 0.0  # 模拟"创建后长期无人互动"

        ws = _FakeWebSocket()
        await manager.join(room, "participant_activity_refresh_test", "小赵", ws)
        assert room.last_activity_at > 0.0, "join() 应刷新 last_activity_at"

        room.last_activity_at = 0.0
        await manager.adjust(room, "owner_activity_refresh_test", "P040", AdjustActionDislike())
        assert room.last_activity_at > 0.0, "adjust() 应刷新 last_activity_at"

    asyncio.run(scenario())


# ============================================================
# 7. WS 层 action 协议校验（api/collab.py）：三种 action 判别式都能正确解析
# ============================================================


def test_ws_adjust_action_adapter_discriminates_all_three_action_shapes():
    """`api/collab.py::_ADJUST_ACTION_ADAPTER` 是 WS "adjust" 消息 `action` 字段
    的唯一校验入口——复用 F-4 单人 `/chat/adjust` 同一份判别式 schema
    （`AdjustActionAdjust`/`AdjustActionAlternative`/`AdjustActionDislike`），
    本测试钉住三种形状都能正确判别，且非法形状会报可读错误而不是裸异常。
    """
    from pydantic import ValidationError

    from api.collab import _ADJUST_ACTION_ADAPTER

    adjust_action = _ADJUST_ACTION_ADAPTER.validate_python(
        {"type": "adjust", "adjustment": {"dimension": "price", "value": "cheaper"}, "label": "便宜点的"}
    )
    assert isinstance(adjust_action, AdjustActionAdjust)

    alt_action = _ADJUST_ACTION_ADAPTER.validate_python({"type": "alternative", "target_id": "R017"})
    assert isinstance(alt_action, AdjustActionAlternative)

    dislike_action = _ADJUST_ACTION_ADAPTER.validate_python({"type": "dislike"})
    assert isinstance(dislike_action, AdjustActionDislike)

    with pytest.raises(ValidationError):
        _ADJUST_ACTION_ADAPTER.validate_python({"type": "not_a_real_action"})
