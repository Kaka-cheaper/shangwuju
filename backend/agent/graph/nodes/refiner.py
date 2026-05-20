"""nodes.refiner —— 反馈合并节点。

复用 backend/agent/refiner.py 的 refine_intent。

输入：
- state["intent"]（原意图）
- state["user_input"]（用户反馈，已被 router_node 路由为 feedback）
输出：state["intent"] = 新意图（含 raw_input 拼接的反馈）

后续：refiner 节点不接 planner —— 由 build.py 把 refiner 接到 execute（重新搜候选）。
这是 Plan-and-Execute 的标准做法：约束变了 → 重 plan，但 plan 仍要看新候选。
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.llm_client import get_llm_client
from agent.refiner import refine_intent


def refiner_node(state: AgentState) -> dict[str, Any]:
    original = state.get("intent")
    feedback_text = state.get("user_input") or ""

    if original is None or not feedback_text:
        return {}

    client = get_llm_client()
    output = refine_intent(
        original=original,
        feedback_text=feedback_text,
        client=client,
    )

    # 重置 plan / critic 状态：让流程从 execute 重新搜候选 → plan → critic
    return {
        "intent": output.refined_intent,
        "blueprint": None,
        "itinerary": None,
        "violations": [],
        "has_critical": False,
        "critic_feedback_text": None,
        "retry_count": 0,
        "plan_attempt": 0,
        "user_decision": None,
        "refine_feedback": feedback_text,
        # 候选数据失效，让 execute 重新搜
        "pois": [],
        "restaurants": [],
        "routes": [],
    }
