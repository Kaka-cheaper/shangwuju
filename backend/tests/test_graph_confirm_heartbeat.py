"""Regression tests for /chat/confirm first-event and heartbeat behavior."""

from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from api._session_store import SESSION_STORE
from api._streams.graph_confirm import _graph_confirm
from api._streams.models import ChatConfirmRequest
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary


def test_graph_confirm_stream_heartbeats_while_finalize_blocks(monkeypatch):
    from api._streams import graph_confirm as graph_confirm_module

    session_id = "sess_graph_confirm_heartbeat_test"
    intent = _make_intent()
    itinerary = _make_legal_itinerary()
    SESSION_STORE[session_id] = {
        "intent": intent.model_dump(),
        "itinerary": itinerary.model_dump(),
        "user_id": "demo_user",
    }

    def slow_finalize(state):
        time.sleep(0.2)
        return {"itinerary": state["itinerary"], "execution_tool_results": []}

    monkeypatch.setattr(graph_confirm_module, "FINALIZE_HEARTBEAT_S", 0.02)
    monkeypatch.setattr(graph_confirm_module, "execute_finalize_node", slow_finalize)

    async def run_probe():
        stream = _graph_confirm(
            ChatConfirmRequest(session_id=session_id, decision="confirm")
        )
        first = await asyncio.wait_for(anext(stream), timeout=0.05)
        second = await asyncio.wait_for(anext(stream), timeout=0.1)
        rest = [ev async for ev in stream]
        return [first, second, *rest]

    events = asyncio.run(run_probe())

    assert events[0].type.value == "agent_thought"
    assert events[1].type.value == "agent_thought"
    assert any(ev.type.value == "itinerary_ready" for ev in events)
    assert events[-1].type.value == "done"
