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

    if intent is None:
        # 没 intent 无法验，直接放行（不应该走到这里）
        return {
            "violations": [],
            "has_critical": False,
            "critic_feedback_text": None,
        }

    if itinerary is None:
        # itinerary 为空 = plan 阶段失败（候选为空 / blueprint 生成失败）
        # 这是 critical 违规：必须触发 replan 让 ILS 兜底或 give_up
        return {
            "violations": [],
            "has_critical": True,
            "critic_feedback_text": (
                "行程为空（itinerary=None）：plan 阶段未能生成有效蓝图。"
                "可能原因：候选 POI/餐厅为空（约束过严或 mock 数据不覆盖）。"
                "请放宽约束重试，或切换到 ILS 算法兜底。"
            ),
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
