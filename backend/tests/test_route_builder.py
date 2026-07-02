"""tests.test_route_builder —— ADR-0010 D-4：锚定两段贪心插入构造。

覆盖 `agent.planning.planners.route_builder`（纯新增，不接线；D-5 才会把它接进
`plan_hybrid`/`replan`）：
1. `dining_soft_anchored`：软锚判定的两条独立触发路径（dining-focused 语境 /
   跨饭点窗+dietary）与否命题。
2. `build_route`：
   - 商务接待场景 → 软锚饭必现，且先于涌现活动被插入。
   - 5 岁家庭（relaxed）vs 朋友热闹（energetic）同池同预算 → 结构性活动数差异。
   - 下限追逐：池只够 1 个可行活动 → 不硬凑，短而好地返回。
   - 活动数封顶 ≤5。
   - pinned 插不进 → 出现在 unmet_pinned，不静默丢弃。
3. `route_to_blueprint` + 真实 `assemble_from_blueprint` + `validate_itinerary`
   端到端：排定时刻与 assemble 后的 itinerary 完全一致，critic 干净。

本文件是纯新增（D-4 铁律同 D-1/D-2/D-3）：不改 `ils_planner`/`rule_planner`/
`replan` 任何既有流程。
"""

from __future__ import annotations

import pytest

from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
from agent.planning.critic._rules.helpers import fmt_hhmm
from agent.planning.critic._rules.types import Severity
from agent.planning.critic.critics_v2 import validate_itinerary
from agent.planning.planners import route_builder as rb
from agent.planning.planners.activity_pool import Visit
from agent.planning.weights_llm import PlanningWeights
from data.loader import load_pois, load_restaurants, load_user_profile
from schemas.domain import Location, Poi, PoiCapacity, Restaurant, RestaurantCapacity
from schemas.intent import Companion, IntentExtraction


# ============================================================
# 共享 fixture helpers（风格对齐 test_activity_pool.py / test_pace_budget.py）
# ============================================================


def _intent(
    *,
    social_context: str = "家庭日常",
    companions: tuple[Companion, ...] = (),
    dietary: tuple[str, ...] = (),
    duration_hours: list[int] | None = None,
    start_time: str = "today_afternoon",
) -> IntentExtraction:
    return IntentExtraction(
        start_time=start_time,
        duration_hours=duration_hours if duration_hours is not None else [4, 6],
        distance_max_km=10.0,
        companions=list(companions),
        physical_constraints=[],
        dietary_constraints=list(dietary),
        experience_tags=[],
        social_context=social_context,
        raw_input="测试",
        parse_confidence=0.9,
        ambiguous_fields=[],
    )


def _weights(**overrides) -> PlanningWeights:
    base = dict(comfort=0.3, time=0.2, cost=0.2, smoothness=0.3, source="test")
    base.update(overrides)
    return PlanningWeights(**base)


def _poi(
    *,
    poi_id: str,
    poi_type: str,
    suggested: int = 90,
    opening: str = "08:00-22:00",
    dist: float = 3.0,
) -> Poi:
    return Poi(
        id=poi_id,
        name=f"测试 POI {poi_id}",
        type=poi_type,
        location=Location(name="测试地", lat=30.25, lng=120.15),
        distance_km=dist,
        opening_hours=opening,
        rating=4.5,
        age_range=None,
        price_range=None,
        tags=[],
        suitable_for=[],
        suggested_duration_minutes=suggested,
        capacity=PoiCapacity(daily_quota=100, available_slots=50),
    )


def _restaurant(
    *,
    rest_id: str,
    cuisine: str = "粤菜",
    opening: str = "17:00-22:00",
    dist: float = 3.0,
    dining_min: int = 60,
) -> Restaurant:
    return Restaurant(
        id=rest_id,
        name=f"测试餐厅 {rest_id}",
        cuisine=cuisine,
        location=Location(name="测试地", lat=30.25, lng=120.15),
        distance_km=dist,
        opening_hours=opening,
        avg_price=80.0,
        rating=4.3,
        typical_dining_min=dining_min,
        capacity=RestaurantCapacity(),
        tags=[],
        suitable_for=[],
    )


def _commute_uniform(minutes: int = 10):
    def fn(a: str, b: str) -> int:
        return 0 if a == b else minutes

    return fn


# ============================================================
# 1. dining_soft_anchored
# ============================================================


def test_dining_soft_anchored_business_context_alone_triggers():
    intent = _intent(social_context="商务接待", dietary=(), duration_hours=[1, 1])
    assert rb.dining_soft_anchored(intent) is True


def test_dining_soft_anchored_false_when_no_dietary_and_no_meal_crossing():
    # 家庭日常 09:00 出发、2h 窗口 [9:00,11:00]：不跨任何饭点窗，也无 dietary。
    intent = _intent(
        social_context="家庭日常",
        dietary=(),
        duration_hours=[2, 2],
        start_time="2026-07-02T09:00",
    )
    assert rb.dining_soft_anchored(intent) is False


def test_dining_soft_anchored_true_when_crosses_dinner_window_with_dietary():
    # 16:00 出发、4h 窗口 [16:00,20:00] 完整跨过晚饭窗 [17:00,20:00]，且有 dietary。
    intent = _intent(
        social_context="家庭日常",
        dietary=("不辣",),
        duration_hours=[4, 4],
        start_time="2026-07-02T16:00",
    )
    assert rb.dining_soft_anchored(intent) is True


def test_dining_soft_anchored_false_when_crosses_dinner_window_but_no_dietary():
    # 同上出行窗，但无 dietary 信号 → 条件②的"且"不成立，不软锚。
    intent = _intent(
        social_context="家庭日常",
        dietary=(),
        duration_hours=[4, 4],
        start_time="2026-07-02T16:00",
    )
    assert rb.dining_soft_anchored(intent) is False


def test_dining_soft_anchored_false_when_only_partial_overlap_with_meal_window():
    # 出行窗 [16:30,18:30] 只与晚饭窗 [17:00,20:00] 部分重叠，不是"完整跨过"。
    intent = _intent(
        social_context="家庭日常",
        dietary=("不辣",),
        duration_hours=[2, 2],
        start_time="2026-07-02T16:30",
    )
    assert rb.dining_soft_anchored(intent) is False


# ============================================================
# 2. build_route —— 商务接待软锚：餐厅必现，且先于涌现活动被放
# ============================================================


def test_business_context_route_always_includes_restaurant_placed_before_emergent():
    poi_pool = [
        _poi(poi_id="PA", poi_type="typeA", suggested=90),
        _poi(poi_id="PB", poi_type="typeB", suggested=75),
    ]
    rest_pool = [
        _restaurant(rest_id="RA", cuisine="粤菜", opening="17:00-22:00", dining_min=90),
        _restaurant(rest_id="RB", cuisine="日料", opening="17:00-22:00", dining_min=60),
    ]
    intent = _intent(
        social_context="商务接待",
        duration_hours=[5, 7],
        start_time="2026-07-02T14:00",
    )
    result = rb.build_route(
        poi_pool,
        rest_pool,
        intent,
        _weights(),
        depart_min=14 * 60,
        commute_fn=_commute_uniform(10),
    )

    restaurant_kinds = [v for v in result.visits if v.kind == "restaurant"]
    assert restaurant_kinds, "商务接待场景必须含至少一个餐厅（软锚兜底）"
    # 软锚饭是锚点段唯一成员（无 pinned），必是插入序里的第一个
    assert result.visits[0].kind == "restaurant"


# ============================================================
# 3. build_route —— 5 岁家庭（relaxed）vs 朋友热闹（energetic）结构性差异
# ============================================================


def test_friends_energetic_selects_at_least_as_many_activities_as_family_relaxed():
    # 同池（8 个 POI + 3 家餐厅）、同预算 [4,6]h、同出发时刻、同通勤表——
    # 唯一变量是 companions/social_context 驱动的节奏档（pace）。
    pool_pois = [_poi(poi_id=f"P{i}", poi_type=f"type{i}", suggested=90) for i in range(8)]
    pool_rests = [
        _restaurant(rest_id=f"R{i}", cuisine=f"cuisine{i}", opening="10:00-22:00")
        for i in range(3)
    ]
    weights = _weights()
    commute = _commute_uniform(10)

    family_intent = _intent(
        social_context="家庭日常",
        companions=(Companion(role="孩子", age=5, count=1),),
        duration_hours=[4, 6],
    )
    friends_intent = _intent(
        social_context="朋友热闹",
        companions=(),
        duration_hours=[4, 6],
    )

    family_result = rb.build_route(
        pool_pois, pool_rests, family_intent, weights, depart_min=13 * 60, commute_fn=commute
    )
    friends_result = rb.build_route(
        pool_pois, pool_rests, friends_intent, weights, depart_min=13 * 60, commute_fn=commute
    )

    assert family_result.pace_tier == "relaxed"
    assert friends_result.pace_tier == "energetic"
    assert len(friends_result.visits) >= len(family_result.visits)


# ============================================================
# 4. build_route —— 下限追逐：池只够 1 活动 → 不硬凑，短而好
# ============================================================


def test_insufficient_pool_returns_short_route_without_forcing():
    lone_restaurant = _restaurant(rest_id="RONLY", opening="17:00-22:00", dining_min=60)
    intent = _intent(duration_hours=[6, 8])  # lo=360min，远超单活动能撑起的时长

    result = rb.build_route(
        [], [lone_restaurant], intent, _weights(), depart_min=17 * 60, commute_fn=_commute_uniform(10)
    )

    assert len(result.visits) == 1
    assert result.schedule.total_minutes < result.fill_targets.lo_min


# ============================================================
# 5. build_route —— 活动数封顶 ≤5
# ============================================================


def test_activity_count_never_exceeds_cap_even_with_abundant_feasible_pool():
    abundant_pois = [
        _poi(poi_id=f"P{i}", poi_type=f"type{i}", suggested=30) for i in range(10)
    ]
    intent = _intent(social_context="朋友热闹", duration_hours=[6, 10])

    result = rb.build_route(
        abundant_pois, [], intent, _weights(), depart_min=8 * 60, commute_fn=_commute_uniform(5)
    )

    assert len(result.visits) == rb.MAX_ACTIVITIES


# ============================================================
# 6. build_route —— pinned 插不进 → 出现在 unmet_pinned，不静默
# ============================================================


def test_infeasible_pinned_visit_surfaces_in_unmet_pinned_not_silently_dropped():
    impossible_pin = Visit(
        kind="poi",
        target_id="P_GHOST",
        duration_min=60,
        windows=[],  # 空窗 = 物理不可行（D-1 语义）
        base_score=0.9,
        category="ghost",
        cost=0.0,
        entity=None,  # type: ignore[arg-type]
    )
    intent = _intent(duration_hours=[4, 6])

    result = rb.build_route(
        [], [], intent, _weights(), depart_min=13 * 60, commute_fn=_commute_uniform(10),
        pinned=[impossible_pin],
    )

    assert impossible_pin in result.unmet_pinned
    assert impossible_pin not in result.visits


# ============================================================
# 7. route_to_blueprint 端到端：assemble + critic 复检
# ============================================================


def test_route_to_blueprint_end_to_end_matches_assemble_and_passes_critic():
    """用真实 mock 数据（商务 POI + 商务餐厅）验证「构造→组装→复检」三层对齐：
    - assemble 后每个节点的 start_time 与 RouteSchedule 排定的分钟数完全一致
      （not_before_start 钉窗机制的正确性证据，见模块 docstring 判断点 7/8）。
    - 统一 critic `validate_itinerary` 干净（无违规）。
    """
    all_pois = {p.id: p for p in load_pois()}
    all_rests = {r.id: r for r in load_restaurants()}
    profile = load_user_profile()

    business_pois = [all_pois[pid] for pid in ("P016", "P051", "P052", "P053", "P041")]
    business_rests = [all_rests[rid] for rid in ("R012", "R019", "R008", "R038")]

    intent = _intent(
        social_context="商务接待",
        companions=(Companion(role="客户", count=2, is_special_role=True),),
        duration_hours=[5, 7],
        start_time="2026-07-02T14:00",
    )
    depart_min = 14 * 60

    result = rb.build_route(
        business_pois,
        business_rests,
        intent,
        _weights(),
        depart_min=depart_min,
        commute_fn=rb.make_commute_fn(profile),
    )
    assert result.schedule.scheduled, "本场景应至少构造出一个可行活动（商务软锚饭兜底）"

    blueprint = rb.route_to_blueprint(result.schedule, intent, depart_min)
    itinerary = assemble_from_blueprint(intent, blueprint, profile)

    # ---- 时刻对齐：assemble 独立重算的每个节点 start_time 应与排程一致 ----
    mid_nodes = itinerary.nodes[1:-1]
    assert len(mid_nodes) == len(result.schedule.scheduled)
    for sv, node in zip(result.schedule.scheduled, mid_nodes):
        assert node.start_time == fmt_hhmm(sv.start_min)
        assert node.target_id == sv.visit.target_id

    # ---- 统一 critic 复检干净：不含 HARD 违规 ----
    violations = validate_itinerary(itinerary, intent)
    hard_violations = [v for v in violations if v.severity == Severity.HARD]
    assert hard_violations == []
