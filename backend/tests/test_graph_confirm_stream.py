"""test_graph_confirm_stream -- /chat/confirm 真实 LangGraph finalize 流。"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from api._session_store import SESSION_STORE
from api._streams.graph_confirm import _graph_confirm
from api._streams.models import ChatConfirmRequest
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary


async def _collect(req: ChatConfirmRequest):
    return [ev async for ev in _graph_confirm(req)]


def test_graph_confirm_stream_executes_finalize_tools(monkeypatch):
    stub_client = MagicMock()
    stub_client.provider = "stub"
    monkeypatch.setattr(
        "agent.graph.nodes.execute_finalize.get_llm_client",
        lambda: stub_client,
    )
    monkeypatch.setattr(
        "agent.planning.memory_writer.persist_memory",
        lambda *args, **kwargs: True,
    )

    session_id = "sess_graph_confirm_test"
    intent = _make_intent(social_context="纪念日仪式感").model_copy(
        update={"extra_services": ["蛋糕"]}
    )
    itinerary = _make_legal_itinerary()
    SESSION_STORE[session_id] = {
        "intent": intent.model_dump(),
        "itinerary": itinerary.model_dump(),
        "user_id": "demo_user",
    }

    events = asyncio.run(
        _collect(ChatConfirmRequest(session_id=session_id, decision="confirm"))
    )

    event_types = [ev.type.value for ev in events]
    assert "tool_call_start" in event_types
    assert "itinerary_ready" in event_types
    assert event_types[-1] == "done"

    tools = [
        ev.payload.get("tool")
        for ev in events
        if ev.type.value == "tool_call_start"
    ]
    assert tools == [
        "reserve_restaurant",
        "buy_ticket",
        "order_extra_service",
        "generate_share_message",
    ]

    final_payload = next(
        ev.payload for ev in events if ev.type.value == "itinerary_ready"
    )
    order_kinds = [o["kind"] for o in final_payload["orders"]]
    assert "餐厅预约" in order_kinds
    assert "门票" in order_kinds
    assert "蛋糕加购" in order_kinds


# ============================================================
# 已确认守门（点火前小修批 任务 1；K8 探针实锤）
# ============================================================
#
# 病灶：/chat/adjust 有 CONFIRMED_ADJUST_BLOCKED 闸，/chat/confirm 没有——两个
# 端点对同一「已确认」状态守门不对称。重复点击确认 = execute_finalize 真实重放
# 预约工具，order_id 掺时间戳导致订单整体换号（K8 探针取证）。
# 期望行为：已下过单的方案再来 confirm → 不重放任何工具、不动方案层，推一条
# 业务性告知气泡（诚实说已经下过单了，想改说一声）+ done；reject/modify 语义不动。


def _seed_confirmable_session(monkeypatch, session_id: str) -> None:
    stub_client = MagicMock()
    stub_client.provider = "stub"
    monkeypatch.setattr(
        "agent.graph.nodes.execute_finalize.get_llm_client",
        lambda: stub_client,
    )
    monkeypatch.setattr(
        "agent.planning.memory_writer.persist_memory",
        lambda *args, **kwargs: True,
    )
    intent = _make_intent(social_context="纪念日仪式感").model_copy(
        update={"extra_services": ["蛋糕"]}
    )
    itinerary = _make_legal_itinerary()
    SESSION_STORE[session_id] = {
        "intent": intent.model_dump(),
        "itinerary": itinerary.model_dump(),
        "user_id": "demo_user",
    }


def test_second_confirm_is_guarded_no_tool_replay(monkeypatch):
    """重复点击确认：第二次 confirm 必须走守门——零工具重放、订单不换号、有告知。"""
    session_id = "sess_confirm_guard_double"
    _seed_confirmable_session(monkeypatch, session_id)

    events_1 = asyncio.run(
        _collect(ChatConfirmRequest(session_id=session_id, decision="confirm"))
    )
    orders_1 = next(
        ev.payload for ev in events_1 if ev.type.value == "itinerary_ready"
    )["orders"]
    assert orders_1, "铺垫：第一次 confirm 必须真实产出订单"
    ids_1 = [o["order_id"] for o in orders_1]

    events_2 = asyncio.run(
        _collect(ChatConfirmRequest(session_id=session_id, decision="confirm"))
    )
    types_2 = [ev.type.value for ev in events_2]

    # 1) 不重放任何工具、方案层不动
    assert "tool_call_start" not in types_2, f"第二次 confirm 重放了工具：{types_2}"
    assert "itinerary_ready" not in types_2, f"第二次 confirm 不该再推方案：{types_2}"
    # 2) 业务性告知气泡（诚实说已经下过单）+ done 收尾
    narr = next(
        (ev.payload for ev in events_2 if ev.type.value == "agent_narration"), None
    )
    assert narr and "下过单" in (narr.get("text") or ""), f"缺守门告知：{narr}"
    assert types_2[-1] == "done"
    # 3) 订单不换号：SESSION_STORE 里的订单还是第一次那几笔
    cached_orders = SESSION_STORE[session_id]["itinerary"]["orders"]
    assert [o["order_id"] for o in cached_orders] == ids_1


def test_confirm_unlocked_after_new_plan_snapshot(monkeypatch):
    """解锁语义：新规划回写快照（新方案无 orders）后，confirm 重新放行。

    与 adjust 守门同一解锁时机——用户说「重新规划」出新方案，/chat/turn 在
    ITINERARY_READY 时经 sync_snapshot 覆盖 itinerary 键（新方案 orders 为空），
    守门信号自然消失，无需额外解锁逻辑。
    """
    from api._session_store import sync_snapshot

    session_id = "sess_confirm_guard_unlock"
    _seed_confirmable_session(monkeypatch, session_id)
    asyncio.run(
        _collect(ChatConfirmRequest(session_id=session_id, decision="confirm"))
    )

    # 模拟 /chat/turn 新规划写点（api/chat.py::_graph_stream_with_session_sync）
    sync_snapshot(session_id, itinerary=_make_legal_itinerary().model_dump())

    events = asyncio.run(
        _collect(ChatConfirmRequest(session_id=session_id, decision="confirm"))
    )
    types = [ev.type.value for ev in events]
    assert "tool_call_start" in types, f"新方案后 confirm 应重新放行：{types}"
    assert "itinerary_ready" in types


def test_reject_after_confirm_semantics_unchanged(monkeypatch):
    """reject/modify 语义不动：守门只对 decision=confirm 生效。"""
    session_id = "sess_confirm_guard_reject"
    _seed_confirmable_session(monkeypatch, session_id)
    asyncio.run(
        _collect(ChatConfirmRequest(session_id=session_id, decision="confirm"))
    )

    events = asyncio.run(
        _collect(ChatConfirmRequest(session_id=session_id, decision="reject"))
    )
    types = [ev.type.value for ev in events]
    assert types == ["agent_thought", "done"]
    thought = events[0].payload.get("text") or ""
    assert "reject" in thought and "下过单" not in thought
