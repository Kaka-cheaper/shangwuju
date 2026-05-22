"""nodes.assemble —— 蓝图 → Itinerary 拼装节点。

复用 backend/agent/assemble_blueprint.py 的 assemble_from_blueprint。

输入：state["intent"] / state["blueprint"] /（Step 8）trace 累积字段
输出：state["itinerary"] = Itinerary（含 decision_trace）
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

    # Step 8：注入 DecisionTrace
    from schemas.decision_trace import (
        AlternativeCandidate,
        CriticAttempt,
        DecisionTrace,
        FallbackHop,
    )

    weights = state.get("weights")
    weights_explanation = ""
    if weights is not None:
        weights_explanation = weights.summary()

    # 把 dict 形式的累积字段还原为 Pydantic 对象
    critic_attempts_dicts = state.get("critic_attempts") or []
    fallback_dicts = state.get("fallback_chain") or []
    alt_dicts = state.get("alternatives") or []

    critic_attempts = [
        CriticAttempt.model_validate(d) if isinstance(d, dict) else d
        for d in critic_attempts_dicts
    ]
    fallback_chain = [
        FallbackHop.model_validate(d) if isinstance(d, dict) else d
        for d in fallback_dicts
    ]
    alternatives = [
        AlternativeCandidate.model_validate(d) if isinstance(d, dict) else d
        for d in alt_dicts
    ]

    final_strategy = "llm_first"
    if state.get("replan_strategy"):
        final_strategy = state["replan_strategy"]
    elif state.get("retry_count") and (state.get("retry_count") or 0) > 0:
        final_strategy = "llm_backprompt"

    trace = DecisionTrace(
        blueprint_rationale=blueprint.rationale or "",
        weights_explanation=weights_explanation,
        critic_attempts=critic_attempts,
        alternatives_considered=alternatives,
        fallback_chain=fallback_chain,
        final_strategy=final_strategy,
    )
    itinerary.decision_trace = trace

    return {"itinerary": itinerary}
