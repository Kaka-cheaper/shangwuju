"""test_node_detail_adjust_room_parity —— node_detail 联动补齐验收。

narrate 主流程(/chat/turn)之外,换菜与房间也各自重建 narration_payload,此前
只带 node_actions、漏了 node_detail —— 后果:换菜后那个节点的事实面板会消失。
本文件钉住补齐后的三处联动:

1. 单人 `/chat/adjust`(`api._streams.graph_adjust._graph_adjust`):AGENT_NARRATION
   带 node_detail;ITINERARY_READY 仍是纯 Itinerary dump、绝不混入 node_detail
   (extra="forbid" 契约,与 node_actions 同一负向断言)。
2. 房间换菜(`RoomManager.adjust`)广播的 agent_narration 带 node_detail。
3. 房间快照(`Room.get_state_snapshot`)带 node_detail —— 新成员 join 即可见。

关键正确性点:node_detail 反查**全量目录**(load_pois/load_restaurants),覆盖
方案里每个节点的选中实体,包括这次换菜**没动过**的节点(P040 poi 不在房间换菜
的意图窄池里)—— 若错用窄池,未改动节点会漏详情。本文件用真实 mock id 的房间
场景专门钉住"覆盖未改动节点"。

驱动手法复用既有两个验收文件的夹具,不新造 harness、不改动它们的既有测试。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from api._streams.models import AdjustActionAdjust, ChatAdjustRequest  # noqa: E402
from schemas.itinerary import Itinerary  # noqa: E402
from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension  # noqa: E402

# 单人 /chat/adjust 夹具(合成候选池 + monkeypatch load_pois/load_restaurants)
from tests.test_chat_adjust_endpoint import (  # noqa: E402
    _collect_adjust,
    _seed_thread_with_scenario,
)
from tests.test_planner_node_swap import _build_itinerary, _intent, _poi, _rest  # noqa: E402

# 房间夹具(真实 mock id：P040 poi / R001 餐厅)
from tests.test_room_adjust_and_ttl import (  # noqa: E402
    _FakeWebSocket,
    _planning_event_payloads,
    _seed_room,
)


# ============================================================
# 1) 单人 /chat/adjust：AGENT_NARRATION 带 node_detail，ITINERARY_READY 不带
# ============================================================


def test_single_user_adjust_emits_node_detail_and_keeps_itinerary_ready_pure(monkeypatch):
    session_id = "nd_parity_single"
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", tags=["高人均"])
    itinerary = _build_itinerary(intent, [poi_a, rb1], depart_min=14 * 60)
    rb_t1 = _rest(rest_id="RB_T1", cuisine="火锅", tags=["不辣"])  # 同子类 + 满足 → 换成它

    graph, config = _seed_thread_with_scenario(
        session_id, itinerary=itinerary, intent=intent,
        pois=[poi_a], restaurants=[rb1, rb_t1], monkeypatch=monkeypatch,
    )
    from tests.test_chat_adjust_endpoint import _current_state
    state = _current_state(graph, config)

    req = ChatAdjustRequest(
        session_id=session_id, node_id="RB1",
        action={"type": "adjust", "adjustment": {"dimension": "dietary", "value": "不辣"}, "label": "不辣的"},
    )
    events = _collect_adjust(req, graph=graph, config=config, state=state)

    # ITINERARY_READY 仍是纯 Itinerary dump —— node_detail 绝不污染(与 node_actions 同契约)
    ir = next(e for e in events if e.type.value == "itinerary_ready")
    Itinerary.model_validate(ir.payload)  # extra="forbid"：混入即炸
    assert "node_detail" not in ir.payload
    assert "node_actions" not in ir.payload

    # AGENT_NARRATION 带 node_detail，覆盖换菜后方案的两个节点
    narration = next(e for e in events if e.type.value == "agent_narration")
    node_detail = narration.payload.get("node_detail")
    assert isinstance(node_detail, dict) and node_detail, "换菜后 narration 应带 node_detail"
    assert node_detail["PA1"]["kind"] == "poi"
    assert node_detail["RB_T1"]["kind"] == "restaurant"
    # 事实字段确实从真实实体派生(合成 _rest avg_price=100 / _poi price_range=None→免费)
    assert node_detail["RB_T1"].get("price_text") == "¥100/人"
    assert node_detail["PA1"].get("price_text") == "免费"

    # 图状态回写也带 node_detail(换菜后读状态的下游能拿到)
    state_after = _current_state(graph, config)
    assert isinstance(state_after.get("node_detail"), dict) and state_after["node_detail"]


# ============================================================
# 2) 房间换菜广播 + 快照都带 node_detail，且覆盖未改动的 poi 节点(全量目录反查证据)
# ============================================================


def test_room_adjust_and_snapshot_carry_node_detail_covering_unchanged_poi():
    async def scenario():
        manager, room = _seed_room("owner_nd_parity")
        ws = _FakeWebSocket()
        await manager.join(room, "owner_nd_parity", "发起人", ws)
        ws.sent.clear()

        action = AdjustActionAdjust(
            adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.DIETARY, value="不辣"),
            label="不辣的",
        )
        await manager.adjust(room, "owner_nd_parity", "R001", action)

        # 广播的 agent_narration 带 node_detail
        payloads = _planning_event_payloads(ws, "agent_narration")
        nd = next((p["node_detail"] for p in payloads if "node_detail" in p), None)
        assert isinstance(nd, dict) and nd, "房间换菜广播应带 node_detail"
        # 覆盖未改动的 poi 节点 P040(不在房间换菜的意图窄池里)—— 全量目录反查的证据
        assert "P040" in nd, "未改动的 poi 节点也该有 node_detail(证明用全量目录、不随窄池漏)"
        assert nd["P040"]["kind"] == "poi"

        # 快照同样带(新成员 join 即可见 fact panel)
        snapshot = room.get_state_snapshot()
        snap_nd = snapshot.get("node_detail")
        assert isinstance(snap_nd, dict) and "P040" in snap_nd

    asyncio.run(scenario())
