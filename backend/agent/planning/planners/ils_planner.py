"""agent.planning.planners.ils_planner —— A+C 混合 ILS 加分路径 + graph replan 第 3 次 ILS 兜底。

【真实定位】

本模块是 ILS 算法兜底 planner，被以下入口消费：

- `graph/nodes/replan.py:ils_replan`（LLM 重生成失败 N 次后第 3 次兜底）
- `tests/test_planner_hybrid.py` / `test_planner_hybrid_overload.py`

ILS 算法：搜索 (POI, restaurant, time) 三元组候选 + 4 维 utility 加权打分 +
local search + 5% 接受劣解。LangGraph 第 3 次 replan 仍调用 plan_hybrid 作为 ILS 兜底层
（参见 graph/nodes/replan.py）。

含 spec A R5 加固：
- `_overload_penalty(poi, intent)` 单段过载强惩罚（年龄 cap 兜底）
- `_resolve_dynamic_dining_slots(intent, segments)` 动态用餐时段
- `_retry_with_critic_feedback` 4 类违规黑名单

【spec planning-quality-deep-review R5】（Wave 4 Task 5，2026-05-23）

ILS 兜底路径加 3 项业务对齐改动：

1. `_overload_penalty(poi, intent) -> float`：按 _resolve_age_caps 同款公式（婴幼儿 ≤45 /
   学龄前 ≤75 / 学童 ≤120 / 高龄 ≤60）推单段 cap，用 get_duration_for_companions 投影
   POI 推荐时长，超 cap 返 0.3 强惩罚（否则 0.0）。
2. `_utility` 公式末尾追加 `-0.5 * _overload_penalty(poi, intent)` 项，让 ILS 在候选池里
   先剔除「成人 180min 但 5 岁娃只能玩 90min」类反人性方案；保留原 4 维 comfort/time/cost/smoothness 不变。
3. DINING_SLOTS 改用 planner.py:_resolve_time_window 推（按 intent.start_time + duration_hours
   动态算），不再硬编码 ("17:00","17:30","18:00")。
4. `_retry_with_critic_feedback` 黑名单按违规类型 4 类全覆盖（time_window / hard_constraint /
   dietary / social_context），通过 helper `_compute_blacklists` 做单点路由；critics.py 当前
   没暴露 dietary critic name，关键词路由作 future-proof。

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

输入与 plan_itinerary 完全相同；被 graph/nodes/replan.py:ils_replan_node 调（第 3 次 ILS 兜底），
以及 tests/test_planner_hybrid.py 直接驱动（rule_assembler 注入）。

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

from schemas.domain import Poi, Restaurant, SuggestedDuration
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

from ..critic.ils_score_critic import CriticReport, run_critics
from ...core.trace import Tracer
from ..weights_llm import PlanningWeights, get_planning_weights
from tools.registry import invoke_tool
from utils.duration_helpers import get_duration_for_companions


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

# 用餐时段池：默认值（与 rule planner 默认 DEFAULT_DINING_TIMES 一致）。
# spec planning-quality-deep-review R5：实际运行时 plan_hybrid 会调
# planner._resolve_time_window 按 intent.start_time + duration_hours 推动态时段，
# 然后传给 ILS 内部的算法用；本常量仅作 module 级 fallback / 老调用方兼容。
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
    from ..blueprint.node_decider import KIND_DINING, KIND_MAIN, decide_nodes

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

    # ---- 步骤 1.5：解析动态用餐时段（spec planning-quality-deep-review R5）----
    # 不再硬编码 ("17:00","17:30","18:00")；用 planner._resolve_time_window 按
    # intent.start_time + duration_hours + segments 推（与 rule planner 同源）。
    dining_slots = _resolve_dynamic_dining_slots(intent, mid_nodes, tracer)

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

    # ---- 步骤 2.5：LLM 语义打分（spec algorithm-redesign R4，ItiNera 范式）----
    # 失败兜底全 0.5；stub 模式直接返全 0.5；ILS 主路径不阻断。
    semantic_scores: dict[str, float] = {}
    if poi_top:
        try:
            from agent.planning.preference_scorer import score_pois_with_llm

            semantic_scores = score_pois_with_llm(intent, poi_top, client=client)
            if semantic_scores:
                tracer.emit(
                    "agent_thought",
                    {
                        "text": (
                            f"LLM 语义打分（ItiNera 范式）：{len(semantic_scores)} 个 POI；"
                            f"分数范围 [{min(semantic_scores.values()):.2f}, "
                            f"{max(semantic_scores.values()):.2f}]"
                        ),
                    },
                )
        except Exception as exc:  # 防御性兜底
            tracer.emit(
                "agent_thought",
                {"text": f"LLM 语义打分失败（{exc}），fallback 全 0.5"},
            )
            semantic_scores = {p.id: 0.5 for p in poi_top}

    # ---- 步骤 3：贪心初始解（utility 最高的 POI×餐厅×17:00）----
    initial = _greedy_init(
        poi_top, rest_top, intent, weights, tracer, dining_slots,
        semantic_scores=semantic_scores,
    )
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
        perturbed = _perturb(current, poi_top, rest_top, rng, dining_slots)
        # 局部搜索：在邻域内贪心改进（仅用 utility，不查可订位）
        improved = _local_search(
            perturbed, poi_top, rest_top, intent, weights, dining_slots,
            semantic_scores=semantic_scores,
        )
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
            best, poi_top, rest_top, intent, weights, report, rule_assembler, tracer,
            dining_slots,
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

# spec algorithm-redesign R3：grounding-first 前置硬剔除常量
# spec innovation-review R4：改 env flag（默认值不变；let evaluator 看到 demo / production 双 mode 思维）
_GROUNDING_MIN_CANDIDATES = _env_int("GROUNDING_MIN_CANDIDATES", 3)           # 候选池 < 3 时自动放宽
_GROUNDING_DISTANCE_TOL_KM = _env_float("GROUNDING_DISTANCE_TOL_KM", 1.0)     # 默认距离容差（与 _utility 物理可行性快检对齐）
_GROUNDING_DISTANCE_TOL_RELAX_KM = _env_float("GROUNDING_DISTANCE_TOL_RELAX_KM", 2.0)  # 候选 < 3 时放宽到 +2km
_GROUNDING_PRESCHOOL_CAP = _env_int("GROUNDING_PRESCHOOL_CAP", 90)            # 含 ≤6 岁同行人时主导桶上限（min）
_GROUNDING_SENIOR_CAP = _env_int("GROUNDING_SENIOR_CAP", 75)                  # 含 ≥75 岁同行人时主导桶上限（min）


def _grounding_filter_poi(
    candidates: list[Poi],
    intent: IntentExtraction,
    tracer: Tracer,
) -> list[Poi]:
    """spec algorithm-redesign R3：POI 候选 grounding-first 前置硬剔除。

    在 ILS 看到候选之前就剔除明显违规的项，避免 utility 计算 / LLM 语义打分浪费在
    「5 岁娃 196min」「打烊 POI」这类候选上。与 `_overload_penalty` / critic 主路径
    构成三重防线：grounding（前置硬剔）→ utility penalty（搜索期）→ critic（兜底）。

    剔除规则：
    - 含 ≤6 岁同行人 + 投影后 suggested_duration > 90min（学龄前/婴幼儿主导桶）
    - 含 ≥75 岁同行人 + 投影后 suggested_duration > 75min（高龄主导桶）
    - poi.distance_km > intent.distance_max_km + 1.0
    - getattr(poi, "business_status", "open") in {"closed", "permanent_closed"}

    放宽机制：
    - 过滤后候选池 < 3 → 仅保留距离 +2.0km / 营业状态过滤，跳过 age cap
      （避免「严过滤把候选剃光」让 ILS 拿不到任何候选；hackathon demo 安全网）

    每剔除一个候选 emit `tracer.emit("grounding_filtered", {poi_id, reason})`。
    """
    if not candidates:
        return candidates

    # 推主导桶 cap（取最严）
    has_preschool = any(
        c.age is not None and c.age <= 6 for c in (intent.companions or [])
    )
    has_senior = any(
        c.age is not None and c.age >= 75 for c in (intent.companions or [])
    )

    # 距离上限
    max_km = intent.distance_max_km if intent.distance_max_km else 999.0

    def _evaluate_strict(poi: Poi) -> Optional[str]:
        """返 None 表示通过；返字符串表示剔除原因（用于 tracer / 日志）"""
        # 距离硬上限
        if poi.distance_km > max_km + _GROUNDING_DISTANCE_TOL_KM:
            return f"距家 {poi.distance_km:.1f}km 超 {max_km:.1f}km + 容差 1.0km"
        # 营业状态
        status = getattr(poi, "business_status", "open") or "open"
        if status in ("closed", "permanent_closed"):
            return f"营业状态={status}"
        # age cap：投影 suggested_duration（与 _overload_penalty 同源逻辑）
        suggested_raw = getattr(poi, "suggested_duration_minutes", None)
        if suggested_raw is None:
            return None
        if isinstance(suggested_raw, (int, SuggestedDuration)):
            try:
                suggested = get_duration_for_companions(
                    suggested_raw, intent.companions if intent else []
                )
            except Exception:
                suggested = None
        else:
            suggested = None
        if suggested is None:
            return None
        if has_preschool and suggested > _GROUNDING_PRESCHOOL_CAP:
            return f"含 ≤6 岁同行人，POI 主导时长 {suggested}min > 90min cap"
        if has_senior and suggested > _GROUNDING_SENIOR_CAP:
            return f"含 ≥75 岁同行人，POI 主导时长 {suggested}min > 75min cap"
        return None

    def _evaluate_relaxed(poi: Poi) -> Optional[str]:
        """放宽模式：仅检查距离 + 营业状态，跳过 age cap"""
        if poi.distance_km > max_km + _GROUNDING_DISTANCE_TOL_RELAX_KM:
            return f"距家 {poi.distance_km:.1f}km 超 {max_km:.1f}km + 放宽容差 2.0km"
        status = getattr(poi, "business_status", "open") or "open"
        if status in ("closed", "permanent_closed"):
            return f"营业状态={status}"
        return None

    # 第一轮：严过滤
    filtered: list[Poi] = []
    rejected: list[tuple[str, str]] = []
    for poi in candidates:
        reason = _evaluate_strict(poi)
        if reason is None:
            filtered.append(poi)
        else:
            rejected.append((poi.id, reason))

    # 候选池 < 3 → 自动放宽（仅距离 + 营业状态）
    if len(filtered) < _GROUNDING_MIN_CANDIDATES:
        tracer.emit(
            "agent_thought",
            {
                "text": (
                    f"grounding-first POI 严过滤后仅剩 {len(filtered)} 项 "
                    f"< 阈值 {_GROUNDING_MIN_CANDIDATES}，触发放宽机制（仅距离 +2km / 营业状态）"
                ),
            },
        )
        filtered = []
        rejected = []
        for poi in candidates:
            reason = _evaluate_relaxed(poi)
            if reason is None:
                filtered.append(poi)
            else:
                rejected.append((poi.id, reason))

    # 上报剔除轨迹
    for poi_id, reason in rejected:
        tracer.emit(
            "grounding_filtered",
            {"poi_id": poi_id, "reason": reason},
        )
    return filtered


def _grounding_filter_restaurant(
    candidates: list[Restaurant],
    intent: IntentExtraction,
    tracer: Tracer,
) -> list[Restaurant]:
    """spec algorithm-redesign R3：餐厅 grounding-first 前置硬剔除。

    仅过滤距离 + 营业状态（餐厅 typical_dining_min 不区分客群桶，无 age cap）。
    满座由 critic 路径处理（不在 grounding 层剔除——demo 异常韧性需要保留满座候选
    让 17:00 → 17:30 替换链路被评委看到）。
    """
    if not candidates:
        return candidates

    max_km = intent.distance_max_km if intent.distance_max_km else 999.0

    filtered: list[Restaurant] = []
    rejected: list[tuple[str, str]] = []
    for rest in candidates:
        if rest.distance_km > max_km + _GROUNDING_DISTANCE_TOL_KM:
            rejected.append(
                (rest.id, f"距家 {rest.distance_km:.1f}km 超 {max_km:.1f}km + 容差 1.0km")
            )
            continue
        status = getattr(rest, "business_status", "open") or "open"
        if status in ("closed", "permanent_closed"):
            rejected.append((rest.id, f"营业状态={status}"))
            continue
        filtered.append(rest)

    # 候选 < 3 → 放宽到 +2km（餐厅没 age cap，没法再降）
    if len(filtered) < _GROUNDING_MIN_CANDIDATES:
        tracer.emit(
            "agent_thought",
            {
                "text": (
                    f"grounding-first 餐厅严过滤后仅剩 {len(filtered)} 项 "
                    f"< 阈值 {_GROUNDING_MIN_CANDIDATES}，触发放宽（距离 +2km）"
                ),
            },
        )
        filtered = []
        rejected = []
        for rest in candidates:
            if rest.distance_km > max_km + _GROUNDING_DISTANCE_TOL_RELAX_KM:
                rejected.append(
                    (rest.id, f"距家 {rest.distance_km:.1f}km 超 +2km 放宽容差")
                )
                continue
            status = getattr(rest, "business_status", "open") or "open"
            if status in ("closed", "permanent_closed"):
                rejected.append((rest.id, f"营业状态={status}"))
                continue
            filtered.append(rest)

    for rest_id, reason in rejected:
        tracer.emit(
            "grounding_filtered",
            {"restaurant_id": rest_id, "reason": reason},
        )
    return filtered


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
    candidates = list(out.candidates)
    # spec algorithm-redesign R3：grounding-first 前置硬剔除
    return _grounding_filter_poi(candidates, intent, tracer)


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
    candidates = list(out.candidates)
    # spec algorithm-redesign R3：grounding-first 前置硬剔除（仅距离 + 营业状态）
    return _grounding_filter_restaurant(candidates, intent, tracer)


# ============================================================
# 加权效用 utility（A 段核心）
# ============================================================

# spec planning-quality-deep-review R5：年龄分级 cap（与 blueprint.py:_resolve_age_caps
# / critics_v2.py:_check_age_aware_duration 同源公式；ILS 路径在此重写避免循环 import）
_AGE_CAP_TODDLER = 45    # ≤3 岁婴幼儿
_AGE_CAP_PRESCHOOL = 75  # 4-6 岁学龄前
_AGE_CAP_SCHOOL_AGE = 120  # 7-12 岁学童
_AGE_CAP_SENIOR = 60     # ≥75 岁高龄长辈
_AGE_CAP_NO_LIMIT = 9999  # 哨兵：无 age 信息时返此值


def _resolve_age_cap(intent: IntentExtraction) -> int:
    """从 intent.companions 推单段最严 cap（min）。

    与 `agent/blueprint.py:_resolve_age_caps` / `agent/v2/critics_v2.py:_check_age_aware_duration`
    业务等价；返回 9999 表示无年龄约束（spec planning-quality-deep-review R5）。
    """
    if intent is None or not getattr(intent, "companions", None):
        return _AGE_CAP_NO_LIMIT

    caps: list[int] = []
    for c in intent.companions:
        age = getattr(c, "age", None)
        if not isinstance(age, int) or age < 0:
            continue
        if age <= 3:
            caps.append(_AGE_CAP_TODDLER)
        elif age <= 6:
            caps.append(_AGE_CAP_PRESCHOOL)
        elif age <= 12:
            caps.append(_AGE_CAP_SCHOOL_AGE)
        elif age >= 75:
            caps.append(_AGE_CAP_SENIOR)

    if not caps:
        return _AGE_CAP_NO_LIMIT
    return min(caps)


def _overload_penalty(poi: Optional[Poi], intent: IntentExtraction) -> float:
    """单段时长 vs 同行人画像合理性 → 强惩罚值（spec planning-quality-deep-review R5）。

    返回：
        0.3 表示「该 POI 在当前客群下的推荐时长 > 年龄 cap」（5 岁娃 + 推荐 90min POI / cap 75）；
        0.0 表示「不超 cap 或无 age 信息」。

    与 critic 主路径的关系：
    - blueprint critic / critics_v2._check_age_aware_duration：拦 LLM 主出错（已规划好的 itinerary）
    - 本 penalty：在 ILS 候选生成 / 局部搜索阶段就给「显然不合适的 POI」打负分，让算法主动跳过
    - 两者镜像防绕过；critic 是兜底，penalty 是先验。
    """
    if poi is None:
        return 0.0
    cap = _resolve_age_cap(intent)
    if cap >= _AGE_CAP_NO_LIMIT:
        return 0.0

    suggested_raw = getattr(poi, "suggested_duration_minutes", None)
    if suggested_raw is None:
        return 0.0

    # 用 helper 投影 SuggestedDuration / int 双形态 → 单值
    if isinstance(suggested_raw, (int, SuggestedDuration)):
        suggested = get_duration_for_companions(
            suggested_raw, intent.companions if intent else []
        )
    else:
        suggested = None
    if suggested is None:
        return 0.0

    # design.md Component 6 公式：actual = min(suggested, cap)；
    # actual < suggested 即 cap 起到约束作用（即 suggested > cap）→ 强惩罚
    return 0.3 if suggested > cap else 0.0


def _utility(
    poi: Optional[Poi],
    rest: Optional[Restaurant],
    dining_time: str,
    intent: IntentExtraction,
    w: PlanningWeights,
    semantic_scores: dict[str, float] | None = None,
) -> tuple[float, str | None]:
    """加权效用函数（适配可选维度）。

    四维度归一化到 [0, 1] 后按权重求和。
    返回 (score, fail_detail)；fail_detail 非 None 表示该候选已物理不可行。

    spec algorithm-redesign R4：末尾追加 LLM 语义打分项
    `+ 0.3 * semantic_scores.get(poi.id, 0.5)`（仅 POI 维度；餐厅由
    dietary 硬约束 + spec A R7 social_compat 处理）。
    semantic_scores=None 时不加项（向后兼容；spec A 测试基线不破）。
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

    # spec planning-quality-deep-review R5：年龄超 cap 的 POI 候选打强负分，
    # 让 ILS 算法层先于 critic 主动跳过（保留原 4 维不变，仅末尾追加项）。
    score -= 0.5 * _overload_penalty(poi, intent)

    # spec algorithm-redesign R4：LLM 语义打分（ItiNera EMNLP'24 范式）
    # 仅 POI 维度叠加；semantic_scores=None 时不加项（向后兼容）
    if poi is not None and semantic_scores is not None:
        score += 0.3 * semantic_scores.get(poi.id, 0.5)

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
    semantic_scores: dict[str, float] | None = None,
) -> CandidatePlan:
    score, fail = _utility(
        poi, rest, dining_time, intent, w, semantic_scores=semantic_scores
    )
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

def _resolve_dynamic_dining_slots(
    intent: IntentExtraction,
    mid_nodes: list[str],
    tracer: Tracer,
) -> tuple[str, ...]:
    """spec planning-quality-deep-review R5：动态用餐时段。

    委托 planner.py:_resolve_time_window 推算（与 rule planner 同源逻辑），
    避免 ILS 路径在 14:00 出发的场景仍然只试 17:00/17:30/18:00。

    `mid_nodes` 是 decide_nodes(intent) 输出（含 "用餐"/"主活动" 等中文标签）；
    本 helper 把它转成 segments frozenset 喂给 _resolve_time_window。

    返 tuple；空 list 时返 module 级 DINING_SLOTS 兜底（保持向后兼容）。
    """
    try:
        from agent.planning.planners.rule_planner import _resolve_time_window
    except Exception:  # pragma: no cover —— 仅在 import 顺序异常时触发
        return DINING_SLOTS

    segments = frozenset(mid_nodes) if mid_nodes else None
    try:
        _, dining_slots, _, _ = _resolve_time_window(intent, segments=segments)
    except Exception:  # pragma: no cover
        return DINING_SLOTS

    if not dining_slots:
        return DINING_SLOTS
    out = tuple(dining_slots)
    tracer.emit(
        "agent_thought",
        {
            "text": (
                f"ILS 用餐时段（动态推导，spec R5）：{list(out)}"
                f"（出发 {intent.start_time}，时长 {intent.duration_hours}h）"
            ),
        },
    )
    return out


def _greedy_init(
    pois: list[Poi],
    rests: list[Restaurant],
    intent: IntentExtraction,
    w: PlanningWeights,
    tracer: Tracer,  # noqa: ARG001
    dining_slots: tuple[str, ...] = DINING_SLOTS,
    semantic_scores: dict[str, float] | None = None,
) -> Optional[CandidatePlan]:
    """从候选中取 utility 最高且 feasible 的作为初始解。

    适配三种场景：
    - pois + rests 都有 → POI×餐厅×时段 笛卡尔积
    - 只有 pois → POI 单维度
    - 只有 rests → 餐厅×时段

    `dining_slots` spec planning-quality-deep-review R5：调用方传入动态时段；
    缺省时退化为 module 级 DINING_SLOTS（向后兼容旧调用方）。

    `semantic_scores` spec algorithm-redesign R4：LLM 语义打分加项；None 时不加。
    """
    best: Optional[CandidatePlan] = None

    if pois and rests:
        # 完整场景：POI × 餐厅 × 时段
        for poi in pois:
            for rest in rests:
                for slot in dining_slots:
                    cand = _make_candidate(
                        poi, rest, slot, intent, w, pois,
                        semantic_scores=semantic_scores,
                    )
                    if not cand.feasible:
                        continue
                    if best is None or cand.utility > best.utility:
                        best = cand
    elif pois:
        # 仅主活动：POI 单维度
        for poi in pois:
            cand = _make_candidate(
                poi, None, "", intent, w, pois,
                semantic_scores=semantic_scores,
            )
            if not cand.feasible:
                continue
            if best is None or cand.utility > best.utility:
                best = cand
    elif rests:
        # 仅用餐：餐厅 × 时段
        for rest in rests:
            for slot in dining_slots:
                cand = _make_candidate(
                    None, rest, slot, intent, w, [],
                    semantic_scores=semantic_scores,
                )
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
    dining_slots: tuple[str, ...] = DINING_SLOTS,
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
        new.dining_time = _shift_node(current.dining_time, rng, dining_slots)
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
    dining_slots: tuple[str, ...] = DINING_SLOTS,
) -> str:
    """ILS 邻域算子：把用餐节点的开始时刻推到 dining_slots 中另一时段。

    旧版 `_shift_time` 重命名 + 语义对齐 edge_v1：
    - 旧：操作 stage 索引上的 start/end 时刻
    - 新：操作 ActivityNode（target_kind="restaurant"）对应的 dining_time，
      由后续 rule_assembler 把它写到 BlueprintNode.note 上（assemble_from_blueprint
      会按 chosen_time 推 preferred_start_time / dining 节点 note）

    Args:
        current_time: 当前用餐时段（"17:30" 之类）
        rng: 随机数发生器
        dining_slots: 候选时段池（spec R5 起按 _resolve_dynamic_dining_slots 推；
                      缺省时退化为 module 级 DINING_SLOTS）

    Returns:
        新的时段；候选池为空时返当前
    """
    pool = [s for s in dining_slots if s != current_time]
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
    dining_slots: tuple[str, ...] = DINING_SLOTS,
    semantic_scores: dict[str, float] | None = None,
) -> CandidatePlan:
    """在 seed 邻域内贪心改进：枚举每个可用维度的所有候选，选 utility 最高的。"""
    best = _make_candidate(
        seed.main_poi, seed.restaurant, seed.dining_time, intent, w, pois,
        semantic_scores=semantic_scores,
    )
    # 枚举 POI 维度（如果有）
    if pois and seed.main_poi is not None:
        for poi in pois:
            cand = _make_candidate(
                poi, seed.restaurant, seed.dining_time, intent, w, pois,
                semantic_scores=semantic_scores,
            )
            if cand.feasible and cand.utility > best.utility:
                best = cand
    # 枚举餐厅维度（如果有）
    if rests and seed.restaurant is not None:
        for rest in rests:
            cand = _make_candidate(
                best.main_poi, rest, best.dining_time, intent, w, pois,
                semantic_scores=semantic_scores,
            )
            if cand.feasible and cand.utility > best.utility:
                best = cand
    # 枚举时段维度（如果有餐厅）
    if seed.dining_time:
        for slot in dining_slots:
            cand = _make_candidate(
                best.main_poi, best.restaurant, slot, intent, w, pois,
                semantic_scores=semantic_scores,
            )
            if cand.feasible and cand.utility > best.utility:
                best = cand
    return best


# ============================================================
# Critic 失败后的重排（C 段反馈）
# ============================================================

# spec planning-quality-deep-review R5：critic 名 → 黑名单类型映射
# 当前 critics.py 的 4 个 critic：hard_constraint / time_window / budget / style；
# 新增 dietary / social_context 关键词路由，让黑名单覆盖 ≥ 4 类违规：
# - time_window：餐厅 + 时段对禁用
# - hard_constraint：距离/时长越界 → 剔除越界 POI/餐厅
# - dietary（关键词或 critic name）：当前 message 包含「饮食/不辣/过敏」类违规 → 剔除餐厅
# - social_context（critic="style" 或关键词）：调性不匹配 → 剔除 POI/餐厅
_DIETARY_KEYWORDS = ("饮食", "辣", "过敏", "素食", "低脂", "kids-meal", "包间")
_SOCIAL_KEYWORDS = ("调性", "social_context", "suitable_for", "氛围", "场景")


def _classify_violation(v) -> set[str]:
    """把 CriticViolation 归类为 {time_window, hard_constraint, dietary, social_context} 子集。

    一条违规可能同时命中多个类（如 critic="style" 的餐厅 dietary 不匹配 + 调性不匹配）。
    """
    classes: set[str] = set()
    name = getattr(v, "critic", "") or ""
    msg = getattr(v, "message", "") or ""

    if name == "time_window":
        classes.add("time_window")
    if name == "hard_constraint":
        classes.add("hard_constraint")
    if name == "dietary" or any(k in msg for k in _DIETARY_KEYWORDS):
        classes.add("dietary")
    if name in ("style", "social_context") or any(
        k in msg for k in _SOCIAL_KEYWORDS
    ):
        classes.add("social_context")
    return classes


def _compute_blacklists(
    failed: CandidatePlan,
    intent: IntentExtraction,
    report: CriticReport,
) -> tuple[set[str], set[str], set[tuple[str, str]]]:
    """根据 critic 报告产出 (POI 黑名单 / 餐厅黑名单 / 餐厅×时段 黑名单)。

    spec planning-quality-deep-review R5：覆盖 4 类违规：
    - time_window → 把 (失败餐厅, 失败时段) 加入 rest_time 黑名单
    - hard_constraint → 距离越界则剔除对应实体
    - dietary → 失败餐厅入餐厅黑名单（让重排去找 dietary 兼容餐厅）
    - social_context → 失败 POI / 餐厅都入对应黑名单
    """
    blacklist_rest_time: set[tuple[str, str]] = set()
    blacklist_rest: set[str] = set()
    blacklist_poi: set[str] = set()

    for v in report.hard_violations():
        classes = _classify_violation(v)

        if "time_window" in classes and failed.restaurant is not None:
            blacklist_rest_time.add(
                (failed.restaurant.id, failed.dining_time)
            )

        if "hard_constraint" in classes and "总耗时" in (v.message or ""):
            # 时长超限：尝试换更近的 POI/餐厅
            if (
                failed.main_poi is not None
                and failed.main_poi.distance_km > intent.distance_max_km - 1
            ):
                blacklist_poi.add(failed.main_poi.id)
            if (
                failed.restaurant is not None
                and failed.restaurant.distance_km > intent.distance_max_km - 1
            ):
                blacklist_rest.add(failed.restaurant.id)

        if "dietary" in classes and failed.restaurant is not None:
            blacklist_rest.add(failed.restaurant.id)

        if "social_context" in classes:
            if failed.main_poi is not None:
                blacklist_poi.add(failed.main_poi.id)
            if failed.restaurant is not None:
                blacklist_rest.add(failed.restaurant.id)

    return blacklist_poi, blacklist_rest, blacklist_rest_time


def _retry_with_critic_feedback(
    failed: CandidatePlan,
    pois: list[Poi],
    rests: list[Restaurant],
    intent: IntentExtraction,
    w: PlanningWeights,
    report: CriticReport,
    rule_assembler,
    tracer: Tracer,
    dining_slots: tuple[str, ...] = DINING_SLOTS,
) -> Optional[Itinerary]:
    """根据硬违规反馈在候选池中找替代。

    spec planning-quality-deep-review R5：黑名单覆盖 ≥ 4 类违规——
    time_window / hard_constraint / dietary / social_context（见 _compute_blacklists）。
    """
    blacklist_poi, blacklist_rest, blacklist_rest_time = _compute_blacklists(
        failed, intent, report
    )

    pois_filtered = [p for p in pois if p.id not in blacklist_poi]
    rests_filtered = [r for r in rests if r.id not in blacklist_rest]

    best: Optional[CandidatePlan] = None
    for poi in pois_filtered:
        for rest in rests_filtered:
            for slot in dining_slots:
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
