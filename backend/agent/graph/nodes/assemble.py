"""nodes.assemble —— 蓝图 → Itinerary 拼装节点。

复用 backend/agent/assemble_blueprint.py 的 assemble_from_blueprint。

输入：state["intent"] / state["blueprint"]
输出：state["itinerary"] = Itinerary
"""

from __future__ import annotations

from typing import Any

from agent.assemble_blueprint import assemble_from_blueprint
from agent.graph.state import AgentState


def assemble_node(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    blueprint = state.get("blueprint")

    if intent is None or blueprint is None:
        return {"itinerary": None}

    itinerary = assemble_from_blueprint(intent, blueprint)
    return {"itinerary": itinerary}
