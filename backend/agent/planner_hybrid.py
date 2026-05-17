"""agent.planner_hybrid —— A+C 混合规划范式（ILS + Critic + LLM 决策）。

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
    """ILS 搜索空间内的一个点。"""

    main_poi: Poi
    restaurant: Restaurant
    dining_time: str
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

    # ---- 步骤 0：决定段集合（pitfalls P1-2026-05-17）----
    from .segment_decider import FULL_SEGMENTS, decide_segments
    segments = decide_segments(intent)
    if segments != FULL_SEGMENTS:
        # 削段场景直接交还 rule（hybrid 的 ILS 假设 POI×餐厅 笛卡尔积）
        weights = get_planning_weights(intent, client=client)
        tracer.emit(
            "agent_thought",
            {
                "text": (
                    f"段决策：本次仅需 {sorted(segments)}，"
                    "hybrid ILS 不适用，已转交规则 planner（仍走 segments-aware 拼装）"
                ),
            },
        )
        return HybridResult(
            success=False,
            failure_reason=FailureReason.UPSTREAM_FAILURE,
            failure_detail="segments_reduced_fallback_to_rule",
            weights=weights,
        )

    # ---- 步骤 1：LLM 出权重 ----
    weights = get_planning_weights(intent, client=client)
    tracer.emit(
        "agent_thought",
        {
            "text": (
                f"目标函数权重（{weights.source}）：{weights.summary()}；"
                f"理由：{weights.rationale or '(无)'}"
            ),
        },
    )

    # ---- 步骤 2：候选生成 ----
    pois = _query_pois(intent, tracer)
    if not pois:
        return HybridResult(
            success=False,
            failure_reason=FailureReason.EMPTY_CANDIDATES,
            failure_detail="ILS 阶段：POI 候选为空",
            weights=weights,
        )
    restaurants = _query_restaurants(intent, tracer)
    if not restaurants:
        return HybridResult(
            success=False,
            failure_reason=FailureReason.EMPTY_CANDIDATES,
            failure_detail="ILS 阶段：餐厅候选为空",
            weights=weights,
        )

    poi_top = pois[:CANDIDATE_TOP_K]
    rest_top = restaurants[:CANDIDATE_TOP_K]

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
    poi: Poi,
    rest: Restaurant,
    dining_time: str,  # noqa: ARG001 — 当前仅用于扰动多样性
    intent: IntentExtraction,
    w: PlanningWeights,
) -> tuple[float, str | None]:
    """加权效用函数。

    四维度归一化到 [0, 1] 后按权重求和。
    返回 (score, fail_detail)；fail_detail 非 None 表示该候选已物理不可行。
    """
    # ---- comfort：标签匹配 + 评分 + 年龄适配 ----
    poi_tag_hit = len(set(poi.tags) & set(intent.physical_constraints))
    rest_tag_hit = len(set(rest.tags) & set(intent.dietary_constraints))
    rating_score = (poi.rating + rest.rating) / 10.0  # 各 [0, 1]，平均后 [0, 1]
    age_penalty = 1.0
    if intent.companions and poi.age_range:
        ages = [c.age for c in intent.companions if c.age is not None]
        if ages:
            lo, hi = poi.age_range
            if not all(lo <= a <= hi for a in ages):
                age_penalty = 0.4
    comfort = (
        0.5 * rating_score
        + 0.25 * min(1.0, poi_tag_hit / max(1, len(intent.physical_constraints) or 1))
        + 0.25 * min(1.0, rest_tag_hit / max(1, len(intent.dietary_constraints) or 1))
    ) * age_penalty

    # ---- time：距离短 / 总耗时短的代理（距离指数衰减）----
    avg_dist = (poi.distance_km + rest.distance_km) / 2.0
    # 距离 ≤ 3 km 满分，5 km 0.5，>=10 km 趋零
    time_score = math.exp(-max(0, avg_dist - 3) ** 2 / 8)

    # ---- cost：人均成本越低越好 ----
    party = max(1, sum(c.count for c in intent.companions) or 1)
    poi_unit = (poi.price_range[0] if poi.price_range else 0) or 0
    cost_per_person = float(poi_unit) + float(rest.avg_price)
    # 200 元 / 人以下满分，500 元 0.5，>=1000 趋零
    cost_score = math.exp(-max(0, cost_per_person - 200) ** 2 / 90000)

    # ---- smoothness：POI 与餐厅距离（同区为佳）+ social_context 命中 ----
    inter_distance = abs(poi.distance_km - rest.distance_km)  # km；越小越好
    smooth_distance = math.exp(-inter_distance ** 2 / 4)
    ctx_match = (
        0.5
        + 0.25 * (intent.social_context in poi.suitable_for)
        + 0.25 * (intent.social_context in rest.suitable_for)
    )
    smoothness = 0.5 * smooth_distance + 0.5 * ctx_match

    score = (
        w.comfort * comfort
        + w.time * time_score
        + w.cost * cost_score
        + w.smoothness * smoothness
    )

    # 物理可行性快检（避免选明显不适合的）
    fail = None
    if poi.distance_km > intent.distance_max_km + 1.0:
        fail = f"POI {poi.id} 距离 {poi.distance_km:g}km 超 intent.distance_max_km {intent.distance_max_km:g}km"
    elif rest.distance_km > intent.distance_max_km + 1.0:
        fail = f"餐厅 {rest.id} 距离 {rest.distance_km:g}km 超 intent.distance_max_km {intent.distance_max_km:g}km"
    elif party >= 6 and not rest.capacity.six and not rest.capacity.eight:
        fail = f"餐厅 {rest.id} 桌型不支持 {party} 人"

    return score, fail


def _make_candidate(
    poi: Poi,
    rest: Restaurant,
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
    """从 top-K 笛卡尔积中取 utility 最高且 feasible 的作为初始解。"""
    best: Optional[CandidatePlan] = None
    for poi in pois:
        for rest in rests:
            for slot in DINING_SLOTS:
                cand = _make_candidate(poi, rest, slot, intent, w, pois)
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
    """随机扰动当前解：换 POI / 换餐厅 / 移时段 三选一。"""
    op = rng.choice(("swap_poi", "swap_rest", "shift_time"))
    new = CandidatePlan(
        main_poi=current.main_poi,
        restaurant=current.restaurant,
        dining_time=current.dining_time,
        backup_pois=current.backup_pois,
    )
    if op == "swap_poi" and len(pois) > 1:
        new.main_poi = rng.choice([p for p in pois if p.id != current.main_poi.id])
    elif op == "swap_rest" and len(rests) > 1:
        new.restaurant = rng.choice([r for r in rests if r.id != current.restaurant.id])
    else:
        new.dining_time = rng.choice(
            [s for s in DINING_SLOTS if s != current.dining_time]
        )
    return new


def _local_search(
    seed: CandidatePlan,
    pois: list[Poi],
    rests: list[Restaurant],
    intent: IntentExtraction,
    w: PlanningWeights,
) -> CandidatePlan:
    """在 seed 邻域内贪心改进：枚举每维度的所有候选，选 utility 最高的。"""
    best = _make_candidate(
        seed.main_poi, seed.restaurant, seed.dining_time, intent, w, pois
    )
    # 枚举 POI 维度
    for poi in pois:
        cand = _make_candidate(
            poi, seed.restaurant, seed.dining_time, intent, w, pois
        )
        if cand.feasible and cand.utility > best.utility:
            best = cand
    # 枚举餐厅维度
    for rest in rests:
        cand = _make_candidate(
            best.main_poi, rest, best.dining_time, intent, w, pois
        )
        if cand.feasible and cand.utility > best.utility:
            best = cand
    # 枚举时段维度
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
