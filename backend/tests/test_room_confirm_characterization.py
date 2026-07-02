"""test_room_confirm_characterization —— ADR-0012 决策 5「特征化测试先行」硬门。

问题命名：绞杀榕重构（Strangler Fig，Fowler）——协作房间确认要从专用的
`_stub_confirm` 切到主 App 也在用的 `_graph_confirm` 前，先用特征化测试
（characterization test，Feathers《Working Effectively with Legacy Code》）钉住
今天可观察的行为，migrate 完同一批断言必须仍绿，才敢说“分叉没有承重理由”。

钉住两件事（ADR-0012 决策 5）：

1. 事件序列 + itinerary_ready 携带 orders：`RoomManager.confirm()` 广播进
   `room.planning_events_history` 的 planning_event 类型必须满足——所有
   tool_call_start/tool_call_end 都先于 itinerary_ready，done 收尾；
   itinerary_ready 的 payload 含确认下单产出的 orders。

   断言刻意不比较事件类型的**完全列表**：`_graph_confirm` 比 `_stub_confirm`
   多推一条起手 `agent_thought`（"正在确认预约与加购服务……"）——这条事件主
   App 确认（`USE_LANGGRAPH=1` 时）一直会推，只是房间过去走的是专用 stub 分支
   没见过；它是纯增量、不影响下游消费——room.py 对识别不到的类型一律原样透传
   广播（`_plan_fresh`/`_run_planner_and_broadcast` 转发 `run_graph_stream`
   任意事件类型走的是同一条“未知类型也转发”逻辑），前端 ToolTracePanel 早就
   吃过 `agent_thought`（turn 的 SSE 序列本就含它）。若断言完全列表相等，会把
   这条良性新增误判成“破坏兼容”，逼着在 room.py 加一层专门过滤 agent_thought
   的适配层——这才是画蛇添足的假兼容，见任务报告“自行拍板判断点”。

2. 身份语义：房间确认的记忆副作用记在房主（`room.owner_id`）头上——房间目前
   也只允许房主触发确认（`RoomManager.confirm` 顶部守卫），这里把“记忆写谁”
   与“谁能点确认”一起钉死，回归测试到位。

驱动手法：不起真 WS 服务，直接用 `RoomManager` 类构造房间（成员 `ws=None`，
`broadcast()` 对离线成员静默跳过，不需要 mock WebSocket）；itinerary/intent
复用 tests/test_critics_v2.py 的 `_make_intent` / `_make_legal_itinerary`
（本仓库 confirm 系列测试的既定构造手法，见 test_graph_confirm_stream.py /
test_e0a_graph_confirm_writeback.py）。

迁移纪律：本文件在 room.py 切到 `_graph_confirm` 前后必须原样跑绿——除
`test_room_confirm_also_accumulates_tag_memory_under_owner` 外没有任何断言
应该因迁移而改写；唯一允许的新增是"记忆副作用新增"（recent_trips 现在也会为
房间写，见 tests/test_e0c_graph_confirm_memory_dual_track.py 的探针）。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

# ============================================================
# 过渡态桥（与 test_critics_v2 / test_graph_confirm_stream 同款）
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from collab import RoomManager  # noqa: E402
from data.memory_store import get_memory, reset_all_memory  # noqa: E402
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_memory():
    reset_all_memory()
    yield
    reset_all_memory()


def _seed_room(owner_id: str) -> tuple[RoomManager, "Room"]:  # noqa: F821 - Room 仅类型注释
    """构造一个已有 intent + 合法 itinerary 的房间（不起 WS，成员 ws=None）。"""
    manager = RoomManager()
    room = manager.create_room(owner_id=owner_id, nickname="发起人")
    room.current_intent_dict = _make_intent().model_dump()
    room.current_itinerary_dict = _make_legal_itinerary().model_dump()
    return manager, room


async def _confirm_and_drain(manager: RoomManager, room, user_id: str) -> None:
    """跑一次房间确认，并排空 graph_confirm 的后台记忆任务（若本次确认走的是它）。

    `_stub_confirm`（迁移前）不产生后台任务，这里的 drain 是无副作用的 no-op；
    `_graph_confirm`（迁移后）把两种记忆副作用都挂成 fire-and-forget 后台任务
    （ADR-0012 决策 5 硬门 1），必须显式 await 完才能断言 data.memory_store /
    user_profile.json 已落地——否则 asyncio.run 收尾时任务可能被取消，assertion
    会随机 flaky。两条分支共用同一套 drain 逻辑，测试代码迁移前后零改动。
    """
    await manager.confirm(room, user_id)
    from api._streams import graph_confirm as _gc

    pending = [t for t in _gc._BACKGROUND_TASKS if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ============================================================
# 1. 事件序列 + itinerary_ready 携带 orders
# ============================================================


def test_room_confirm_tool_calls_precede_itinerary_ready_then_done():
    owner_id = "owner_seq_test"
    manager, room = _seed_room(owner_id)

    asyncio.run(_confirm_and_drain(manager, room, owner_id))

    events = room.planning_events_history
    types_ = [e["type"] for e in events]

    assert types_, "确认应至少广播出一条 planning_event"
    assert types_[-1] == "done", f"确认流必须以 done 收尾，实际顺序={types_}"
    assert "itinerary_ready" in types_, f"确认必须产出 itinerary_ready，实际={types_}"

    itin_idx = types_.index("itinerary_ready")
    tool_call_indices = [i for i, t in enumerate(types_) if t.startswith("tool_call_")]
    assert tool_call_indices, f"确认应至少派发一个执行类工具，实际={types_}"
    assert all(i < itin_idx for i in tool_call_indices), (
        f"所有 tool_call_* 必须先于 itinerary_ready 广播，实际顺序={types_}"
    )

    itin_event = next(e for e in events if e["type"] == "itinerary_ready")
    orders = itin_event["payload"].get("orders")
    assert orders, f"itinerary_ready 必须携带确认下单的 orders，payload={itin_event['payload']}"
    order_kinds = {o["kind"] for o in orders}
    assert "餐厅预约" in order_kinds, f"应含餐厅预约订单，实际 order_kinds={order_kinds}"
    assert "门票" in order_kinds, f"应含门票订单，实际 order_kinds={order_kinds}"

    assert room.current_itinerary_dict is not None
    assert room.current_itinerary_dict.get("orders"), "room.current_itinerary_dict 应同步含 orders"


# ============================================================
# 2. 身份语义：记忆副作用记在房主头上
# ============================================================


def test_room_confirm_records_memory_under_owner_user_id():
    owner_id = "owner_identity_test"
    manager, room = _seed_room(owner_id)

    asyncio.run(_confirm_and_drain(manager, room, owner_id))

    memory = get_memory(owner_id)
    visited_ids = {v.target_id for v in memory.visited_targets}
    itinerary = _make_legal_itinerary()
    expected_ids = {
        n.target_id for n in itinerary.nodes if n.target_kind in ("poi", "restaurant")
    }
    assert expected_ids <= visited_ids, (
        f"确认后的记忆累积必须记在房主 {owner_id!r} 头上（ADR-0012 决策 5 身份语义），"
        f"期望 target_id ⊇ {expected_ids}，实际 visited_ids={visited_ids}"
    )


def test_room_confirm_rejects_non_owner_and_leaves_memory_untouched():
    """非房主发起确认被拒绝——迁移不应悄悄放宽这条既有守卫。"""
    owner_id = "owner_guard_test"
    other_id = "participant_guard_test"
    manager, room = _seed_room(owner_id)
    manager_ = manager  # noqa: F841 - 命名对齐可读性

    asyncio.run(manager.confirm(room, other_id))

    assert room.planning_events_history == [], "非房主确认不应触发任何规划事件广播"
    other_memory = get_memory(other_id)
    assert other_memory.visited_targets == [], "非房主确认不应写入任何人的记忆"
