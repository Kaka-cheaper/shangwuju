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
