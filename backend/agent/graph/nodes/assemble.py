"""nodes.assemble —— 蓝图 → Itinerary 拼装节点（edge_v1）。

复用 backend/agent/assemble_blueprint.py 的 assemble_from_blueprint。

输入：
- state["intent"]
- state["blueprint"]
- state["user_profile"]：GetUserProfileOutput（execute 阶段并行 worker 写入，
  含 .profile: UserProfile）。assemble_from_blueprint 需要 user_profile.home_location
  与 user_profile.transport_preference 算 home 锚点 + hop 通勤。
- state（Step 8）trace 累积字段：critic_attempts / fallback_chain / alternatives

输出：
- state["itinerary"] = Itinerary（含 nodes/hops/schedule + decision_trace）

【字段路径】Itinerary 已是 edge_v1：含 nodes / hops / schedule，不再有 stages。
LLM 蓝图也已切到 PlanBlueprint.nodes（无 stages）。

【兜底】state.user_profile 缺失（execute worker 没跑或失败）时回落 load_user_profile()
默认画像，避免 assemble_from_blueprint 调 lookup_hop 时 transport_preference 报错。
"""

from __future__ import annotations

from typing import Any, Optional

from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
from agent.graph.state import AgentState
from data.loader import load_user_profile
from schemas.domain import UserProfile


def _resolve_user_profile(state: AgentState) -> UserProfile:
    """从 state.user_profile（GetUserProfileOutput）取出 UserProfile，缺失则用默认画像。"""
    raw = state.get("user_profile")
    profile: Optional[UserProfile] = None

    if raw is not None:
        # state.user_profile 由 get_user_profile_worker 写入 GetUserProfileOutput
        profile = getattr(raw, "profile", None)
        # 兜底：万一直接就是 UserProfile（测试场景）
        if profile is None and isinstance(raw, UserProfile):
            profile = raw

    if profile is None:
        # execute worker 失败时退回默认画像，保证 assemble 能继续推进
        profile = load_user_profile()

    return profile


def assemble_node(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    blueprint = state.get("blueprint")

    if intent is None or blueprint is None:
        return {"itinerary": None}

    user_profile = _resolve_user_profile(state)
    itinerary = assemble_from_blueprint(intent, blueprint, user_profile)

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
