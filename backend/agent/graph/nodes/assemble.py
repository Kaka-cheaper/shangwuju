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

    # final_strategy 反映「这一刻流程到了哪一步」（assemble 时还在验证）。
    # 真正定稿值由 narrate 节点决定（critic 通过或 give_up 后定型）。
    # 判据用 fallback_chain 最后一跳——只增不减、严格反映"已发生的事"，
    # 不看 retry_count（避免与 ILS 路径混淆）。
    if fallback_chain:
        last_hop = fallback_chain[-1]
        last_to = getattr(last_hop, "to_stage", None) or (
            last_hop.get("to_stage") if isinstance(last_hop, dict) else None
        )
        if last_to == "give_up":
            final_strategy = "give_up"
        elif last_to == "ils":
            final_strategy = "ils"
        elif last_to == "rule":
            final_strategy = "rule"
        elif last_to == "llm_backprompt":
            final_strategy = "llm_backprompt"
        else:
            final_strategy = "llm_first"
    elif state.get("replan_strategy") in ("llm_backprompt", "ils_fallback", "give_up"):
        # 兜底：万一 fallback_chain 没累积但 replan_strategy 设了
        rs = state["replan_strategy"]
        final_strategy = {
            "llm_backprompt": "llm_backprompt",
            "ils_fallback": "ils",
            "give_up": "give_up",
        }[rs]
    else:
        final_strategy = "llm_first"

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
