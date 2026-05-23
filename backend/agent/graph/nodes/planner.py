"""nodes.planner —— LLM-First Plan 节点（plan-and-execute 中的 plan 阶段）。

复用 backend/agent/blueprint_llm.py 的 generate_blueprint + agent/weights_llm.py。

输入：
- state["intent"]
- state["pois"] / state["restaurants"]（execute 阶段已并行搜出候选）
- state.get("critic_feedback_text")（重试时的 backprompt 反馈）

输出：
- state["weights"] = PlanningWeights
- state["blueprint"] = PlanBlueprint
- state["plan_attempt"] += 1
"""

from __future__ import annotations

from typing import Any

from agent.planning.blueprint.blueprint_llm import generate_blueprint, BlueprintGenError
from agent.graph.state import AgentState
from agent.core.llm_client import get_llm_client
from agent.planning.weights_llm import get_planning_weights


def planner_node(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    if intent is None:
        raise ValueError("planner_node 需要 state.intent，但没找到")

    pois = state.get("pois") or []
    restaurants = state.get("restaurants") or []

    if not pois or not restaurants:
        # 候选为空时让上层 fallback 到 ILS 或 rule（在 replan_router 处理）
        return {
            "weights": get_planning_weights(intent, client=None),
            "blueprint": None,
            "plan_attempt": (state.get("plan_attempt") or 0) + 1,
        }

    client = get_llm_client()

    # 1. 出权重（LLM 决定主观偏好）
    weights = get_planning_weights(intent, client=client)

    # 2. 看候选 + 反馈 → 出蓝图
    feedback = state.get("critic_feedback_text")
    feedback_list = [feedback] if feedback else None

    blueprint = None
    try:
        blueprint = generate_blueprint(
            intent,
            pois,
            restaurants,
            client=client,
            critic_feedback=feedback_list,
            user_id=state.get("user_id") or "demo_user",
        )
    except BlueprintGenError:
        # 蓝图生成失败 → blueprint=None，由 replan_router 决定 fallback
        blueprint = None

    # Step 8：写候选「考虑过的备选」到 alternatives（top-2 ~ top-5）
    alternatives = _build_alternatives(blueprint, pois, restaurants)

    return {
        "weights": weights,
        "blueprint": blueprint,
        "plan_attempt": (state.get("plan_attempt") or 0) + 1,
        "alternatives": alternatives,
    }


def _build_alternatives(blueprint, pois, restaurants):
    """从候选源 + 已选蓝图推「考虑过但未选」的 top-2 ~ top-5。

    朴素实现：blueprint 选中的目标记为 rank=1；其它按 rating 倒序填 rank 2-5。

    Returns:
        list[AlternativeCandidate] —— 用 model_dump 后的 dict（避免 LangGraph
        TypedDict + 业务对象的循环引用问题）
    """
    from schemas.decision_trace import AlternativeCandidate

    selected_target_ids: set[str] = set()
    if blueprint is not None:
        for s in blueprint.nodes:
            if s.target_id:
                selected_target_ids.add(s.target_id)

    alternatives: list[dict] = []
    rank = 2

    # POI 备选（最多 2 条）
    poi_alts = sorted(
        [p for p in pois if p.id not in selected_target_ids],
        key=lambda p: p.rating,
        reverse=True,
    )[:2]
    for p in poi_alts:
        ac = AlternativeCandidate(
            target_kind="poi",
            target_id=p.id,
            target_name=p.name,
            utility_score=round(float(p.rating) / 5.0, 3),
            rank=rank,
            reason_rejected=(
                f"评分较低（{p.rating:.1f}）" if p.rating < 4.7
                else f"距离更远（{p.distance_km:.1f}km）"
            ),
        )
        alternatives.append(ac.model_dump())
        rank += 1

    # 餐厅备选（最多 2 条）
    rest_alts = sorted(
        [r for r in restaurants if r.id not in selected_target_ids],
        key=lambda r: r.rating,
        reverse=True,
    )[:2]
    for r in rest_alts:
        ac = AlternativeCandidate(
            target_kind="restaurant",
            target_id=r.id,
            target_name=r.name,
            utility_score=round(float(r.rating) / 5.0, 3),
            rank=rank,
            reason_rejected=(
                f"评分较低（{r.rating:.1f}）" if r.rating < 4.7
                else f"距离更远（{r.distance_km:.1f}km）"
            ),
        )
        alternatives.append(ac.model_dump())
        rank += 1

    return alternatives
