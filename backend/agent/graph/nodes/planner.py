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

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent.planning.blueprint.blueprint_llm import generate_blueprint, BlueprintGenError
from agent.graph.state import AgentState
from agent.core.llm_client import get_llm_client
from agent.planning.weights_llm import get_planning_weights

logger = logging.getLogger(__name__)


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
    # 赞锁定根治批：锁定清单透传给蓝图生成（用户消息「必须保留」段先验 +
    # 预览强制收录；critic 侧 check_pinned_presence 是硬闸兜底）。单人路径
    # 该键恒为空 → None → generate_blueprint 行为与本批之前完全一致。
    pinned = state.get("pinned_targets") or None

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
                pinned=pinned,
            )
        except BlueprintGenError as e:
            # 真因修复批 item 5：这条分支曾经完全静默——蓝图生成失败（JSON 非法/
            # 缺字段/旧字段污染/Pydantic 校验失败，见 BlueprintGenError.reason
            # 的完整枚举）只会让 blueprint=None 悄悄流到 replan_router 触发
            # fallback，日志里连一行"为什么"都没有，真因排查时只能靠猜。
            # BlueprintGenError 自带 reason/detail（+ raw_content 前 500 字），
            # 补一条 warning 摘要——不改变返回 None 交给上层 fallback 的既有
            # 行为，只是让这次失败不再无声无息。
            logger.warning(
                "[planner] generate_blueprint 失败，转 fallback：reason=%s detail=%s",
                e.reason,
                (e.detail or "")[:300],
            )
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


def _reason_rejected(candidate, selected_same_kind) -> str:
    """备选卡的拒绝理由——与选中项真实比较，不用固定阈值（分界修缮批 任务 4）。

    普查实锤：曾用固定阈值（rating<4.7→「评分较低」否则「距离更远」），从未与
    选中项比较——评分 4.8 且更近的备选会被标「距离更远」，是确定域里的假事实
    断言（解释卡直接展示给用户）。判据改为：

    - 「评分较低」仅当备选评分低于**全部**同 kind 选中项（多选中项时，只比其中
      一个低不足以断言"因为评分输了"——它仍比另一个选中项高）；
    - 「距离更远」仅当备选距离远于**全部**同 kind 选中项（同理）；
    - 两者都不成立、或该 kind 没有选中项可比（如蓝图没排餐厅）→ 中性措辞
      「综合排序略后」——诚实承认是综合打分的结果，不编造一个具体维度。
    """
    if selected_same_kind:
        if all(candidate.rating < s.rating for s in selected_same_kind):
            return f"评分较低（{candidate.rating:.1f}）"
        if all(candidate.distance_km > s.distance_km for s in selected_same_kind):
            return f"距离更远（{candidate.distance_km:.1f}km）"
    return "综合排序略后"


def _build_alternatives(blueprint, pois, restaurants):
    """从候选源 + 已选蓝图推「考虑过但未选」的 top-2 ~ top-5。

    朴素实现：blueprint 选中的目标记为 rank=1；其它按 rating 倒序填 rank 2-5。
    reason_rejected 与同 kind 选中项真实比较（见 `_reason_rejected`）。

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

    selected_pois = [p for p in pois if p.id in selected_target_ids]
    selected_rests = [r for r in restaurants if r.id in selected_target_ids]

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
            reason_rejected=_reason_rejected(p, selected_pois),
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
            reason_rejected=_reason_rejected(r, selected_rests),
        )
        alternatives.append(ac.model_dump())
        rank += 1

    return alternatives
