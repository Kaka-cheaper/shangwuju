"""agent.planner_hybrid —— A+C 混合规划范式（ILS + Critic + LLM 决策）。

⚠️ 冻结声明（2026-05-22）：
    本文件是 PLANNER_LLM_STRATEGY=hybrid 的实现，自 LangGraph 主架构上线后**不再演进**。
    LangGraph 第三次 replan 仍调用 plan_hybrid 作为 ILS 兜底层（参见 graph/nodes/replan.py）。

    所有新功能改动应在 `agent/graph/` 下完成；ILS 算法内部修改可继续在本文件做。

学术依据：
- A 段（ILS 启发式）：[Vansteenwegen et al. 2009 ILS for TOPTW]、
  [Gunawan et al. 2019 Adjustment ILS for Multi-objective TOPTW]
- C 段（Critic 验证）：[Kambhampati et al. 2024 LLM-Modulo Frameworks NeurIPS],
  [Kim et al. 2024 Robust Planning with LLM-Modulo arXiv:2405.20625]
- LLM 头尾：[ItiNera EMNLP 2024 Industry] 把主观决策（权重）放给 LLM、客观搜索放给算法

整体流程：
    [LLM] 出 4 个权重 (comfort/time/cost/smoothness)
        ↓
    [Algo] 候选生成（POI top-K × Restaurant top-K × dining_slot top-K）
        ↓
    [Algo] ILS：扰动（swap POI / 换餐厅 / 移时段）+ 局部搜索 N 次
        ↓
    [Critic] HardConstraint/TimeWindow/Budget/Style 4 个 Critic 验证
        ↓
        硬违规 → 用违规反馈再跑一次 ILS（Critic backprompt to ILS，不是 LLM）
        否则 → 返回 utility 最高的方案
        ↓
    [Algo] 失败兜底：rule planner

接口：
    def plan_itinerary_hybrid(intent, *, client=None, tracer=None) -> PlannerResult

输入与 plan_itinerary 完全相同，输出也是 PlannerResult；
被 plan_itinerary_with_mode 在 mode="llm" + PLANNER_LLM_STRATEGY=hybrid 时调用。

不负责：
- 权重决策（在 weights_llm.py）
- Critic 实现（在 critics.py）
- HTTP/SSE（在 main.py）
- Tool 实现（在 tools/）
"""

from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass, field
from typing import Any, Optional

from schemas.domain import Poi, Restaurant
from schemas.errors import FailureReason
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary
from schemas.tools import (
    EstimateRouteTimeInput,
    EstimateRouteTimeOutput,
    SearchPoisInput,
    SearchPoisOutput,
    SearchRestaurantsInput,
    SearchRestaurantsOutput,
)

from .critics import CriticReport, run_critics
from .trace import Tracer
from .weights_llm import PlanningWeights, get_planning_weights
from tools.registry import invoke_tool


# ============================================================
# 配置（可被 .env 覆盖）
# ============================================================

def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


# ILS 迭代次数（评测时段：30 → ~50ms，足够 demo 实时）
ILS_ITERATIONS = _env_int("PLANNER_ILS_ITERATIONS", 30)

# 候选 top-K（每槽位保留多少候选参与组合）
CANDIDATE_TOP_K = _env_int("PLANNER_CANDIDATE_TOP_K", 5)

# 用餐时段池（与 rule planner 默认 DEFAULT_DINING_TIMES 一致）
DINING_SLOTS = ("17:00", "17:30", "18:00")

# 随机种子（reproducibility；生产可设 None）
ILS_SEED = _env_int("PLANNER_ILS_SEED", 20260517)


# ============================================================
# 候选方案（行程的中间表示，组装前的轻量结构）
# ============================================================

@dataclass
class CandidatePlan:
    """ILS 搜索空间内的一个点。

    支持三种场景：
    - 完整（主活动+用餐）：main_poi + restaurant + dining_time 都有值
    - 仅主活动：main_poi 有值，restaurant=None，dining_time=""
    - 仅用餐：restaurant + dining_time 有值，main_poi=None
    """

    main_poi: Optional[Poi] = None
    restaurant: Optional[Restaurant] = None
    dining_time: str = ""
    backup_pois: list[Poi] = field(default_factory=list)
    # 计算缓存
    utility: float = 0.0
    feasible: bool = True
    fail_detail: Optional[str] = None


# ============================================================
# 入口
# ============================================================

@dataclass
class HybridResult:
    """planner_hybrid 内部结果（不是公共 API；上层包成 PlannerResult）。"""

    success: bool
    itinerary: Optional[Itinerary] = None
    weights: Optional[PlanningWeights] = None
    critic_report: Optional[CriticReport] = None
    failure_reason: Optional[FailureReason] = None
    failure_detail: Optional[str] = None


def plan_hybrid(
    intent: IntentExtraction,
    *,
    client: Any | None = None,
    tracer: Optional[Tracer] = None,
    rule_assembler=None,
) -> HybridResult:
    """A+C 混合规划主流程。

    rule_assembler: 复用 planner.py 已有的 _assemble_itinerary / _resolve_time_window /
                    _estimate 工具函数；通过函数注入避免循环依赖。

    返回 HybridResult；上层 planner_with_mode 把它包成 PlannerResult。

    Phase 0.10（pitfalls P1-2026-05-17）：
    若 segment_decider 决定本次要削段（少于 5 段），ILS 的笛卡尔积不再适用——
    削段场景直接上抛失败让上层 fallback rule planner（rule 已支持按 segments 拼）。
    这样 hybrid 仍是 5 段场景的加分项；削段场景由 rule 兜底（demo 不翻车）。
    """
    tracer = tracer or Tracer()
    rng = random.Random(ILS_SEED)

    # ---- 步骤 0：决定中间节点集合（edge_v1：从 segment 视角切到 node 视角）----
    # decide_nodes 返回中间节点 kind 列表（["主活动", "用餐"] / ["主活动"] / ["用餐"]）；
    # 旧 decide_segments 仍兼容，但 ILS 内部直接看 nodes 更清晰。
    from .node_decider import KIND_DINING, KIND_MAIN, decide_nodes

    mid_nodes = decide_nodes(intent)
    needs_poi = KIND_MAIN in mid_nodes
    needs_dining = KIND_DINING in mid_nodes

    # ---- 步骤 1：LLM 出权重 ----
    weights = get_planning_weights(intent, client=client)
    tracer.emit(
        "agent_thought",
        {
            "text": (
                f"ILS 节点决策：mid_nodes={mid_nodes}（POI={'需要' if needs_poi else '跳过'}"
                f"，餐厅={'需要' if needs_dining else '跳过'}）；"
                f"权重（{weights.source}）：{weights.summary()}"
            ),
        },
    )

    # ---- 步骤 2：候选生成（按需搜索）----
    pois: list[Poi] = []
    restaurants: list[Restaurant] = []

    if needs_poi:
        pois = _query_pois(intent, tracer)
        if not pois:
            return HybridResult(
                success=False,
                failure_reason=FailureReason.EMPTY_CANDIDATES,
                failure_detail="ILS 阶段：POI 候选为空",
                weights=weights,
            )

    if needs_dining:
        restaurants = _query_restaurants(intent, tracer)
        if not restaurants:
            return HybridResult(
                success=False,
                failure_reason=FailureReason.EMPTY_CANDIDATES,
                failure_detail="ILS 阶段：餐厅候选为空",
                weights=weights,
            )

    # 至少要有一个维度的候选
    if not pois and not restaurants:
        return HybridResult(
            success=False,
            failure_reason=FailureReason.EMPTY_CANDIDATES,
            failure_detail="ILS 阶段：POI 和餐厅候选均为空",
            weights=weights,
        )

    poi_top = pois[:CANDIDATE_TOP_K] if pois else []
    rest_top = restaurants[:CANDIDATE_TOP_K] if restaurants else []

    # ---- 步骤 3：贪心初始解（utility 最高的 POI×餐厅×17:00）----
    initial = _greedy_init(poi_top, rest_top, intent, weights, tracer)
    if initial is None:
        return HybridResult(
            success=False,
            failure_reason=FailureReason.EMPTY_CANDIDATES,
            failure_detail="ILS 阶段：贪心初始化失败",
            weights=weights,
        )

    best = initial
    best_score = best.utility
    tracer.emit(
        "agent_thought",
        {
            "text": (
                f"贪心初始解：POI={best.main_poi.id} / 餐厅={best.restaurant.id} / "
                f"时段={best.dining_time} / utility={best_score:.3f}"
            ),
        },
    )

    # ---- 步骤 4：ILS 迭代（扰动 + 局部搜索）----
    current = best
    for i in range(ILS_ITERATIONS):
        # 扰动：随机换 POI / 换餐厅 / 移时段三选一
        perturbed = _perturb(current, poi_top, rest_top, rng)
        # 局部搜索：在邻域内贪心改进（仅用 utility，不查可订位）
        improved = _local_search(perturbed, poi_top, rest_top, intent, weights)
        s = improved.utility
        if s > best_score:
            best, best_score = improved, s
            tracer.emit(
                "agent_thought",
                {
                    "text": (
                        f"ILS 迭代 {i+1}/{ILS_ITERATIONS}：发现更优解 "
                        f"utility {best_score:.3f}（POI={best.main_poi.id} / "
                        f"餐厅={best.restaurant.id} / 时段={best.dining_time}）"
                    ),
                },
            )
        # 接受准则：始终接受改进，5% 接受劣解（避免局部最优）
        if s > current.utility or rng.random() < 0.05:
            current = improved

    # ---- 步骤 5：组装 Itinerary（用 rule planner 的 helper）----
    if rule_assembler is None:
        return HybridResult(
            success=False,
            failure_reason=FailureReason.UPSTREAM_FAILURE,
            failure_detail="rule_assembler 未注入",
            weights=weights,
        )
    itinerary = rule_assembler(intent, best, tracer)
    if itinerary is None:
        return HybridResult(
            success=False,
            failure_reason=FailureReason.UPSTREAM_FAILURE,
            failure_detail="ILS 选定方案后 rule_assembler 拼装失败",
            weights=weights,
        )

    # ---- 步骤 6：Critic 验证（C 段）----
    report = run_critics(itinerary, intent)
    tracer.emit(
        "agent_thought",
        {
            "text": (
                f"Critic 验证：passed={report.passed}，soft_score={report.soft_score:.2f}，"
                f"违规 {len(report.violations)} 条"
            ),
        },
    )
    for v in report.violations:
        tracer.emit(
            "agent_thought",
            {"text": f"[{v.severity.upper()}] {v.critic}: {v.message}"},
        )

    if not report.passed:
        # 硬违规 → 触发一次基于 critic 反馈的重排
        tracer.emit(
            "replan_triggered",
            {
                "reason": "critic_hard_violation",
                "from_tool": "critics",
                "action": "retry_with_critic_feedback",
                "violations": [v.message for v in report.hard_violations()],
            },
        )
        retried = _retry_with_critic_feedback(
            best, poi_top, rest_top, intent, weights, report, rule_assembler, tracer
        )
        if retried is not None:
            return HybridResult(
                success=True,
                itinerary=retried,
                weights=weights,
                critic_report=run_critics(retried, intent),
            )
        # 重排仍失败 → 失败上抛，让上层 fallback 到 rule planner
        return HybridResult(
            success=False,
            failure_reason=FailureReason.UPSTREAM_FAILURE,
            failure_detail=(
                "Critic 硬违规：" + "；".join(v.message for v in report.hard_violations())
            ),
            weights=weights,
            critic_report=report,
        )

    return HybridResult(
        success=True,
        itinerary=itinerary,
        weights=weights,
        critic_report=report,
    )


# ============================================================
# 候选生成（直接调真 Tool；trace 里也会留下 tool_call_start/end）
# ============================================================

def _query_pois(intent: IntentExtraction, tracer: Tracer) -> list[Poi]:
    args = SearchPoisInput(
        distance_max_km=intent.distance_max_km,
        physical_constraints=list(intent.physical_constraints),
        experience_tags=list(intent.experience_tags),
        social_context=intent.social_context,
        age_in_party=[c.age for c in intent.companions if c.age is not None] or None,
        limit=20,
    ).model_dump()
    tracer.emit("tool_call_start", {"tool": "search_pois", "input": args})
    res = invoke_tool("search_pois", args)
    tracer.emit(
        "tool_call_end",
        {
            "tool": "search_pois",
            "output": res.output,
            "success": res.success,
            "reason": res.reason.value if res.reason else None,
            "duration_ms": res.duration_ms,
        },
    )
    if not res.success:
        return []
    out = SearchPoisOutput.model_validate(res.output)
    return list(out.candidates)


def _query_restaurants(intent: IntentExtraction, tracer: Tracer) -> list[Restaurant]:
    args = SearchRestaurantsInput(
        distance_max_km=intent.distance_max_km,
        dietary_constraints=list(intent.dietary_constraints),
        experience_tags=list(intent.experience_tags),
        social_context=intent.social_context,
        capacity_requirement=intent.capacity_requirement,
        limit=20,
    ).model_dump()
    tracer.emit("tool_call_start", {"tool": "search_restaurants", "input": args})
    res = invoke_tool("search_restaurants", args)
    tracer.emit(
        "tool_call_end",
        {
            "tool": "search_restaurants",
            "output": res.output,
            "success": res.success,
            "reason": res.reason.value if res.reason else None,
            "duration_ms": res.duration_ms,
        },
    )
    if not res.success:
        return []
    out = SearchRestaurantsOutput.model_validate(res.output)
    return list(out.candidates)


# ============================================================
# 加权效用 utility（A 段核心）
# ============================================================

def _utility(
    poi: Optional[Poi],
    rest: Optional[Restaurant],
    dining_time: str,
    intent: IntentExtraction,
    w: PlanningWeights,
) -> tuple[float, str | None]:
    """加权效用函数（适配可选维度）。

    四维度归一化到 [0, 1] 后按权重求和。
    返回 (score, fail_detail)；fail_detail 非 None 表示该候选已物理不可行。
    """
    # ---- comfort：标签匹配 + 评分 + 年龄适配 ----
    poi_tag_hit = len(set(poi.tags) & set(intent.physical_constraints)) if poi else 0
    rest_tag_hit = len(set(rest.tags) & set(intent.dietary_constraints)) if rest else 0

    poi_rating = poi.rating if poi else 0
    rest_rating = rest.rating if rest else 0
    rating_count = (1 if poi else 0) + (1 if rest else 0)
    rating_score = (poi_rating + rest_rating) / (rating_count * 5.0) if rating_count else 0.5

    age_penalty = 1.0
    if poi and intent.companions and poi.age_range:
        ages = [c.age for c in intent.companions if c.age is not None]
        if ages:
            lo, hi = poi.age_range
            if not all(lo <= a <= hi for a in ages):
                age_penalty = 0.4

    phys_denom = max(1, len(intent.physical_constraints) or 1)
    diet_denom = max(1, len(intent.dietary_constraints) or 1)
    comfort = (
        0.5 * rating_score
        + 0.25 * min(1.0, poi_tag_hit / phys_denom)
        + 0.25 * min(1.0, rest_tag_hit / diet_denom)
    ) * age_penalty

    # ---- time：距离短 / 总耗时短的代理（距离指数衰减）----
    distances = []
    if poi:
        distances.append(poi.distance_km)
    if rest:
        distances.append(rest.distance_km)
    avg_dist = sum(distances) / len(distances) if distances else 3.0
    time_score = math.exp(-max(0, avg_dist - 3) ** 2 / 8)

    # ---- cost：人均成本越低越好 ----
    poi_unit = (poi.price_range[0] if poi and poi.price_range else 0) or 0
    rest_price = rest.avg_price if rest else 0
    cost_per_person = float(poi_unit) + float(rest_price)
    cost_score = math.exp(-max(0, cost_per_person - 200) ** 2 / 90000)

    # ---- smoothness：POI 与餐厅距离（同区为佳）+ social_context 命中 ----
    if poi and rest:
        inter_distance = abs(poi.distance_km - rest.distance_km)
    else:
        inter_distance = 0
    smooth_distance = math.exp(-inter_distance ** 2 / 4)

    poi_ctx_match = (intent.social_context in poi.suitable_for) if poi else 0
    rest_ctx_match = (intent.social_context in rest.suitable_for) if rest else 0
    ctx_match = 0.5 + 0.25 * poi_ctx_match + 0.25 * rest_ctx_match
    smoothness = 0.5 * smooth_distance + 0.5 * ctx_match

    score = (
        w.comfort * comfort
        + w.time * time_score
        + w.cost * cost_score
        + w.smoothness * smoothness
    )

    # 物理可行性快检
    fail = None
    if poi and poi.distance_km > intent.distance_max_km + 1.0:
        fail = f"POI {poi.id} 距离 {poi.distance_km:g}km 超限"
    elif rest and rest.distance_km > intent.distance_max_km + 1.0:
        fail = f"餐厅 {rest.id} 距离 {rest.distance_km:g}km 超限"
    elif rest:
        party = max(1, sum(c.count for c in intent.companions) or 1)
        if party >= 6 and not rest.capacity.six and not rest.capacity.eight:
            fail = f"餐厅 {rest.id} 桌型不支持 {party} 人"

    return score, fail


def _make_candidate(
    poi: Optional[Poi],
    rest: Optional[Restaurant],
    dining_time: str,
    intent: IntentExtraction,
    w: PlanningWeights,
    backup: list[Poi],
) -> CandidatePlan:
    score, fail = _utility(poi, rest, dining_time, intent, w)
    return CandidatePlan(
        main_poi=poi,
        restaurant=rest,
        dining_time=dining_time,
        backup_pois=backup[:3],
        utility=score,
        feasible=fail is None,
        fail_detail=fail,
    )


# ============================================================
# 贪心初始 + ILS
# ============================================================

def _greedy_init(
    pois: list[Poi],
    rests: list[Restaurant],
    intent: IntentExtraction,
    w: PlanningWeights,
    tracer: Tracer,  # noqa: ARG001
) -> Optional[CandidatePlan]:
    """从候选中取 utility 最高且 feasible 的作为初始解。

    适配三种场景：
    - pois + rests 都有 → POI×餐厅×时段 笛卡尔积
    - 只有 pois → POI 单维度
    - 只有 rests → 餐厅×时段
    """
    best: Optional[CandidatePlan] = None

    if pois and rests:
        # 完整场景：POI × 餐厅 × 时段
        for poi in pois:
            for rest in rests:
                for slot in DINING_SLOTS:
                    cand = _make_candidate(poi, rest, slot, intent, w, pois)
                    if not cand.feasible:
                        continue
                    if best is None or cand.utility > best.utility:
                        best = cand
    elif pois:
        # 仅主活动：POI 单维度
        for poi in pois:
            cand = _make_candidate(poi, None, "", intent, w, pois)
            if not cand.feasible:
                continue
            if best is None or cand.utility > best.utility:
                best = cand
    elif rests:
        # 仅用餐：餐厅 × 时段
        for rest in rests:
            for slot in DINING_SLOTS:
                cand = _make_candidate(None, rest, slot, intent, w, [])
                if not cand.feasible:
                    continue
                if best is None or cand.utility > best.utility:
                    best = cand

    return best


def _perturb(
    current: CandidatePlan,
    pois: list[Poi],
    rests: list[Restaurant],
    rng: random.Random,
) -> CandidatePlan:
    """随机扰动当前解（edge_v1：邻域操作针对 nodes，不再针对 stages）。

    操作目标：
    - `_swap_node`（POI 维）：换中间节点 i（target_kind="poi"）的 target_id；
      对应旧 `_swap_poi` 的语义
    - `_swap_node`（餐厅维）：换中间节点 j（target_kind="restaurant"）的 target_id；
      对应旧 `_swap_rest` 的语义
    - `_shift_node`（时段维）：把用餐节点的 dining_time 推到下一时段；
      对应旧 `_shift_time` 的语义

    这里把三类操作内联在 `_perturb` 里以避免函数指针的 overhead；
    `_swap_node` / `_shift_node` 命名作为概念暴露给 design.md / R7 验收。

    选择策略：
    - 有 POI + 有餐厅 → 三选一（_swap_node POI / _swap_node 餐厅 / _shift_node）
    - 只有 POI → _swap_node POI
    - 只有餐厅 → 二选一（_swap_node 餐厅 / _shift_node）
    """
    ops: list[str] = []
    if pois and len(pois) > 1 and current.main_poi is not None:
        ops.append("swap_node_poi")
    if rests and len(rests) > 1 and current.restaurant is not None:
        ops.append("swap_node_restaurant")
    if rests and current.dining_time:
        ops.append("shift_node")
    # 兜底：如果没有可扰动的维度，直接返回原解
    if not ops:
        return current

    op = rng.choice(ops)
    new = CandidatePlan(
        main_poi=current.main_poi,
        restaurant=current.restaurant,
        dining_time=current.dining_time,
        backup_pois=current.backup_pois,
    )
    if op == "swap_node_poi" and pois:
        new.main_poi = _swap_node(
            current.main_poi, pois, rng, target_kind="poi"
        )
    elif op == "swap_node_restaurant" and rests:
        new.restaurant = _swap_node(
            current.restaurant, rests, rng, target_kind="restaurant"
        )
    elif op == "shift_node":
        new.dining_time = _shift_node(current.dining_time, rng)
    return new


# ============================================================
# ILS 邻域算子（edge_v1：node 操作；R7 / Task 9）
# ============================================================


def _swap_node(
    current_target: Poi | Restaurant | None,
    candidates: list[Poi] | list[Restaurant],
    rng: random.Random,
    *,
    target_kind: str,
) -> Poi | Restaurant | None:
    """ILS 邻域算子：把指定 target_kind 的节点 target_id 换成另一个候选。

    旧版 `_swap_poi` / `_swap_rest` 的合并：通过 `target_kind="poi" / "restaurant"`
    控制操作目标，逻辑相同（在候选池中随机选一个不同于当前的）。

    Args:
        current_target: 当前节点的 target 实体（main_poi 或 restaurant）；
                        None 时直接随机返一个候选
        candidates: 候选池（已被 _query_pois / _query_restaurants 排序）
        rng: 随机数发生器（reproducibility）
        target_kind: "poi" / "restaurant"，仅作日志/调试用，不参与算法

    Returns:
        新的 target 实体；候选池为空或仅含当前 target 时返 None / 当前
    """
    _ = target_kind  # 显式忽略；保留参数让调用点一目了然
    pool = [c for c in candidates if current_target is None or c.id != current_target.id]
    if not pool:
        return current_target
    return rng.choice(pool)


def _shift_node(
    current_time: str,
    rng: random.Random,
) -> str:
    """ILS 邻域算子：把用餐节点的开始时刻推到 DINING_SLOTS 中另一时段。

    旧版 `_shift_time` 重命名 + 语义对齐 edge_v1：
    - 旧：操作 stage 索引上的 start/end 时刻
    - 新：操作 ActivityNode（target_kind="restaurant"）对应的 dining_time，
      由后续 rule_assembler 把它写到 BlueprintNode.note 上（assemble_from_blueprint
      会按 chosen_time 推 preferred_start_time / dining 节点 note）

    Args:
        current_time: 当前用餐时段（"17:30" 之类）
        rng: 随机数发生器

    Returns:
        新的时段；候选池为空时返当前
    """
    pool = [s for s in DINING_SLOTS if s != current_time]
    if not pool:
        return current_time
    return rng.choice(pool)


# ============================================================
# 局部搜索（贪心改进，邻域内枚举）
# ============================================================



def _local_search(
    seed: CandidatePlan,
    pois: list[Poi],
    rests: list[Restaurant],
    intent: IntentExtraction,
    w: PlanningWeights,
) -> CandidatePlan:
    """在 seed 邻域内贪心改进：枚举每个可用维度的所有候选，选 utility 最高的。"""
    best = _make_candidate(
        seed.main_poi, seed.restaurant, seed.dining_time, intent, w, pois
    )
    # 枚举 POI 维度（如果有）
    if pois and seed.main_poi is not None:
        for poi in pois:
            cand = _make_candidate(
                poi, seed.restaurant, seed.dining_time, intent, w, pois
            )
            if cand.feasible and cand.utility > best.utility:
                best = cand
    # 枚举餐厅维度（如果有）
    if rests and seed.restaurant is not None:
        for rest in rests:
            cand = _make_candidate(
                best.main_poi, rest, best.dining_time, intent, w, pois
            )
            if cand.feasible and cand.utility > best.utility:
                best = cand
    # 枚举时段维度（如果有餐厅）
    if seed.dining_time:
        for slot in DINING_SLOTS:
            cand = _make_candidate(
                best.main_poi, best.restaurant, slot, intent, w, pois
            )
            if cand.feasible and cand.utility > best.utility:
                best = cand
    return best


# ============================================================
# Critic 失败后的重排（C 段反馈）
# ============================================================

def _retry_with_critic_feedback(
    failed: CandidatePlan,
    pois: list[Poi],
    rests: list[Restaurant],
    intent: IntentExtraction,
    w: PlanningWeights,
    report: CriticReport,
    rule_assembler,
    tracer: Tracer,
) -> Optional[Itinerary]:
    """根据硬违规反馈在候选池中找替代。

    策略：
    - time_window 违规 → 把出错的 (餐厅 id, 时段) 从候选剔除，重新跑 _greedy_init
    - hard_constraint 违规（段缺失 / 总时长越界）→ 由 rule_assembler 自动重组装
                       （本函数仅换 POI/餐厅，再交还给 rule_assembler 试一次）
    """
    blacklist_rest_time: set[tuple[str, str]] = set()
    blacklist_rest: set[str] = set()
    blacklist_poi: set[str] = set()
    for v in report.hard_violations():
        if v.critic == "time_window":
            # field_hint 形如 "stages.用餐.start=17:00"
            blacklist_rest_time.add((failed.restaurant.id, failed.dining_time))
        elif v.critic == "hard_constraint" and "总耗时" in v.message:
            # 时长超限：尝试换更近的 POI/餐厅
            if failed.main_poi.distance_km > intent.distance_max_km - 1:
                blacklist_poi.add(failed.main_poi.id)
            if failed.restaurant.distance_km > intent.distance_max_km - 1:
                blacklist_rest.add(failed.restaurant.id)

    pois_filtered = [p for p in pois if p.id not in blacklist_poi]
    rests_filtered = [r for r in rests if r.id not in blacklist_rest]

    best: Optional[CandidatePlan] = None
    for poi in pois_filtered:
        for rest in rests_filtered:
            for slot in DINING_SLOTS:
                if (rest.id, slot) in blacklist_rest_time:
                    continue
                cand = _make_candidate(poi, rest, slot, intent, w, pois_filtered)
                if not cand.feasible:
                    continue
                if best is None or cand.utility > best.utility:
                    best = cand
    if best is None:
        return None

    tracer.emit(
        "agent_thought",
        {
            "text": (
                f"基于 Critic 反馈的重排：选 POI={best.main_poi.id} / "
                f"餐厅={best.restaurant.id} / 时段={best.dining_time}"
            ),
        },
    )
    return rule_assembler(intent, best, tracer)
