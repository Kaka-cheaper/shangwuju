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

【体感编排批 P2：get_planning_weights ∥ generate_blueprint】
两次调用都可能各自触发一次真实 LLM 往返（数秒到数十秒），且零数据依赖——
`generate_blueprint` 签名不吃 `weights`（读其 `Args` 即知：只吃
intent/pois/restaurants/client/critic_feedback/user_id），`get_planning_weights`
也不读候选/蓝图，两者互不等待。本节点是同步 LangGraph 节点，用
`concurrent.futures.ThreadPoolExecutor` 起两个线程并行发起，省一轮串行
LLM 往返的挂钟时间。`OpenAICompatibleClient`（`agent/core/llm_client.py`）
底层是 `httpx.Client` 连接池 + `openai` SDK，两个线程共享同一个 client 实例
并发调 `.chat()` 是标准、受支持的用法（httpx.Client 本身线程安全）。
异常语义严格保持各自独立（不因为并行就把两条异常路径混在一起）：
- `get_planning_weights` 自身从不抛（LLM 失败会在内部走启发式兜底，
  见该函数 docstring「优先级」），线程边界不改变这一点。
- `generate_blueprint` 失败（`BlueprintGenError`）时 `blueprint=None`，
  交给 `replan_router` 处理——这条 except 分支原样保留在各自的 worker
  函数内部，不提到线程池外层合并处理。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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

    # spec interaction-experience-review：双范式分发
    # rule 模式 → 纯规则路径（不调 LLM，毫秒级出方案）
    # llm 模式（默认）→ 现行 LLM-First 路径
    mode = state.get("planner_mode")
    if mode == "rule":
        return _planner_node_rule(state, intent)

    if not pois or not restaurants:
        # 候选为空时让上层 fallback 到 ILS 或 rule（在 replan_router 处理）
        return {
            "weights": get_planning_weights(intent, client=None),
            "blueprint": None,
            "plan_attempt": (state.get("plan_attempt") or 0) + 1,
        }

    client = get_llm_client()
    feedback = state.get("critic_feedback_text")
    feedback_list = [feedback] if feedback else None
    user_id = state.get("user_id") or "demo_user"

    def _get_weights() -> Any:
        # 出权重（LLM 决定主观偏好）——从不抛，失败自带启发式兜底
        return get_planning_weights(intent, client=client)

    def _get_blueprint() -> Any:
        # 看候选 + 反馈 → 出蓝图；失败 → None，交给 replan_router 兜底
        # （这条 except 分支就地保留，不提到线程池外层——见模块 docstring「异常语义」）
        try:
            return generate_blueprint(
                intent,
                pois,
                restaurants,
                client=client,
                critic_feedback=feedback_list,
                user_id=user_id,
            )
        except BlueprintGenError:
            return None

    # 体感编排批 P2：两次独立 LLM 调用并行发起（见模块 docstring）
    with ThreadPoolExecutor(max_workers=2) as pool:
        weights_future = pool.submit(_get_weights)
        blueprint_future = pool.submit(_get_blueprint)
        weights = weights_future.result()
        blueprint = blueprint_future.result()

    # Step 8：写候选「考虑过的备选」到 alternatives（top-2 ~ top-5）
    alternatives = _build_alternatives(blueprint, pois, restaurants)

    return {
        "weights": weights,
        "blueprint": blueprint,
        "plan_attempt": (state.get("plan_attempt") or 0) + 1,
        "alternatives": alternatives,
    }


def _planner_node_rule(state: AgentState, intent) -> dict[str, Any]:
    """规则模式：直接调 plan_itinerary 出完整 itinerary，跳过 LLM 蓝图 + assemble 阶段。

    设计哲学（spec interaction-experience-review）：
    - 不调用任何 LLM（无 weights / blueprint / preference_scorer）
    - 毫秒级出方案，断网也能跑（评委 demo 现场可拔网线演示）
    - 与 LLM 模式产物完全 schema 等价：Itinerary 含 nodes + hops + schedule
    - 走完整 critic 流程：critic_node 仍可验证规则路径产出的 itinerary

    失败兜底：plan_itinerary 失败时返回 itinerary=None，由 replan_router 决定 fallback。
    """
    from agent.planning.planners.rule_planner import plan_itinerary

    try:
        result = plan_itinerary(intent)
    except Exception:  # noqa: BLE001
        # 规则路径失败极罕见（mock 数据稳定）；兜底防 demo 翻车
        return {
            "blueprint": None,
            "itinerary": None,
            "plan_attempt": (state.get("plan_attempt") or 0) + 1,
        }

    if not result.success or result.itinerary is None:
        return {
            "blueprint": None,
            "itinerary": None,
            "plan_attempt": (state.get("plan_attempt") or 0) + 1,
        }

    return {
        "blueprint": None,  # 跳过 assemble；assemble_node 看到 itinerary 已存在会 noop
        "itinerary": result.itinerary,
        "plan_attempt": (state.get("plan_attempt") or 0) + 1,
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
