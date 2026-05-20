"""nodes.critic —— Itinerary 客观约束验证节点（Plan-and-Execute 中 Evaluator 阶段）。

复用 backend/agent/v2/critics_v2.py 的 validate_itinerary。

输入：state["intent"] / state["itinerary"]
输出：
- state["violations"] = list[Violation]
- state["has_critical"] = bool
- state["critic_feedback_text"] = str（仅在 has_critical=True 时填，给 planner backprompt）
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.v2.critics_v2 import (
    Severity,
    format_violations_for_llm,
    validate_itinerary,
)


def critic_node(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    itinerary = state.get("itinerary")

    if intent is None or itinerary is None:
        # 没东西可验，直接 has_critical=False 让流程继续（rule fallback）
        return {
            "violations": [],
            "has_critical": False,
            "critic_feedback_text": None,
        }

    violations = validate_itinerary(itinerary, intent)
    has_critical = any(v.severity == Severity.CRITICAL for v in violations)
    feedback = format_violations_for_llm(violations) if has_critical else None

    return {
        "violations": violations,
        "has_critical": has_critical,
        "critic_feedback_text": feedback,
    }


def route_after_critic(state: AgentState) -> str:
    """conditional edge：critic 后走 narrate 还是 replan。"""
    if state.get("has_critical"):
        return "replan_router"
    return "narrate"
