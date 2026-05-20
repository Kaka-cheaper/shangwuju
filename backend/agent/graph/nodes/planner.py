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

from agent.blueprint_llm import generate_blueprint, BlueprintGenError
from agent.graph.state import AgentState
from agent.llm_client import get_llm_client
from agent.weights_llm import get_planning_weights


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
        )
    except BlueprintGenError:
        # 蓝图生成失败 → blueprint=None，由 replan_router 决定 fallback
        blueprint = None

    return {
        "weights": weights,
        "blueprint": blueprint,
        "plan_attempt": (state.get("plan_attempt") or 0) + 1,
    }
