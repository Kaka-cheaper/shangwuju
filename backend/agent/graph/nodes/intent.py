"""nodes.intent —— 意图抽取节点。

复用 backend/agent/intent_parser.py 的 parse_intent —— 它已含 persona prior 注入。

输入：state["user_input"]
输出：state["intent"] = IntentExtraction
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.intent.parser import parse_intent
from agent.core.llm_client import get_llm_client


def intent_node(state: AgentState) -> dict[str, Any]:
    client = get_llm_client()
    user_input = state.get("user_input") or ""
    user_id = state.get("user_id") or "demo_user"

    intent = parse_intent(user_input, client=client, user_id=user_id)
    return {"intent": intent}
