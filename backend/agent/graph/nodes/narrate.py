"""nodes.narrate —— 暖语气文案节点。

复用 backend/agent/narrator.py 的 generate_narration。

输入：state["intent"] / state["itinerary"]
输出：state["narration"] = str
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.llm_client import get_llm_client
from agent.narrator import generate_narration


def narrate_node(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    itinerary = state.get("itinerary")

    if intent is None or itinerary is None:
        return {"narration": None}

    client = get_llm_client()
    use_llm = (
        client is not None
        and getattr(client, "provider", None) != "stub"
    )

    text = generate_narration(
        intent=intent,
        itinerary=itinerary,
        stage="stream",
        use_llm=use_llm,
    )
    return {"narration": text}
