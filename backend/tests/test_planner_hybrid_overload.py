"""验 spec planning-quality-deep-review R5 ILS 算法兜底 utility 加 overload_penalty
+ ADR-0009 C-3 ViolationCode → ILS 重搜动作 映射表。

测试矩阵：
1. 5 岁娃 + P033 类候选（default=180 / kid_3_6=90 / cap=75）→ utility 罚分 0.3
2. 同 POI 配成人客群（cap 9999）→ 无罚分（_overload_penalty 返 0.0）
3. DINING_SLOTS 跟随 _resolve_time_window 推（14:00 出发 + 3-5h 时长 → 不再硬码 17:00）
4. ADR-0009 映射表：`_classify_violation`（按 ViolationCode 路由到动作桶）+
   `_compute_blacklists`（动作桶 → 具体黑名单条目，含按 field_path 定向 blame）

【ADR-0009 C-3】

旧「黑名单 4 类全覆盖（time_window / hard_constraint / dietary / social_context）」
测试矩阵用的是已删除的 `ils_score_critic.CriticViolation`（按 critic 名 + message
关键词路由）。ils_score_critic 随 ADR-0009 决策 1/3 删除，`_classify_violation` /
`_compute_blacklists` 改吃统一 critic 的 `Violation`（`.code` + `.severity` +
`.field_path`）。测试矩阵 4 整体换血为按 ViolationCode 逐码验证映射表。

测试设计：
- 直接调 `_overload_penalty` / `_utility` / `_resolve_dynamic_dining_slots` /
  `_classify_violation` / `_compute_blacklists` 等 module 级 helper，不跑 plan_hybrid
  全流程，避免 stub LLM client 等环境依赖。
- 用最小 SuggestedDuration / Poi / Restaurant fixture，不依赖 mock_data 加载。
- 定向 blame 测试用最小合法 Itinerary（不经 assemble_from_blueprint，手工拼
  ActivityNode/Hop，同 test_critics_v2.py 的 fixture 风格），只为定位 field_path
  的 "nodes[idx]" 下标，不跑真 critic 校验。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


def _install_agent_stub() -> None:
    """让 `import agent.planner_hybrid` 直接命中 backend/agent 子模块。

    复用 test_age_aware_critic.py 同款桥（避免 agent/__init__.py eager-import 老 schema 炸）。
    """
    backend_root = Path(__file__).resolve().parent.parent
    agent_dir = backend_root / "agent"

    if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
        stub = types.ModuleType("agent")
        stub.__path__ = [str(agent_dir)]
        sys.modules["agent"] = stub


_install_agent_stub()

from agent.planning.critic.critics_v2 import (  # noqa: E402
    Severity,
    Violation,
    ViolationCode,
)
from agent.planning.planners.ils_planner import (  # noqa: E402
    DINING_SLOTS,
    CandidatePlan,
    _classify_violation,
    _compute_blacklists,
    _overload_penalty,
    _resolve_age_cap,
    _resolve_dynamic_dining_slots,
    _utility,
)
from agent.core.trace import Tracer  # noqa: E402
from agent.planning.weights_llm import PlanningWeights  # noqa: E402
from schemas.domain import Location, Poi, PoiCapacity, Restaurant, SuggestedDuration  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402
from schemas.itinerary import ActivityNode, Hop, Itinerary  # noqa: E402


# ============================================================
# Fixture 工具
# ============================================================


def _make_intent(
    *,
    companions: list[Companion] | None = None,
    start_time: str = "today_afternoon",
    duration_hours: list[int] | None = None,
    distance_max_km: float = 5.0,
) -> IntentExtraction:
    return IntentExtraction(
        raw_input="测试 R5",
        social_context="家庭日常",
        companions=list(companions) if companions is not None else [],
        duration_hours=duration_hours or [3, 5],
        distance_max_km=distance_max_km,
        start_time=start_time,
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        parse_confidence=0.9,
    )


def _make_poi(
    *,
    poi_id: str = "P_TEST",
    suggested: SuggestedDuration | int | None = None,
    distance_km: float = 3.0,
    rating: float = 4.5,
) -> Poi:
    return Poi(
        id=poi_id,
        name=f"测试 POI {poi_id}",
        type="主题乐园",
        location=Location(name="测试地", lat=30.25, lng=120.15),
        distance_km=distance_km,
        opening_hours="09:00-21:00",
        rating=rating,
        suitable_for=["家庭日常"],
        suggested_duration_minutes=suggested,
        capacity=PoiCapacity(daily_quota=100, available_slots=50),
    )


def _make_weights() -> PlanningWeights:
    return PlanningWeights(
        comfort=0.4, time=0.2, cost=0.2, smoothness=0.2, source="test"
    )


# ============================================================
# 测试 1：5 岁娃 P033 类候选（default=180/kid_3_6=90/cap=75）→ utility 罚分
# ============================================================


def test_5yo_overload_penalty_hits_when_suggested_exceeds_cap() -> None:
    """5 岁娃 + 推荐时长 90min（kid_3_6 桶投影后） / cap 75min → 强惩罚 0.3。

    覆盖 design.md Component 6 公式：suggested > cap → 返 0.3。
    """
    sd = SuggestedDuration(
        default=180, kid_3_6=90, kid_7_12=120, senior=90, multi_gen=90
    )
    poi = _make_poi(poi_id="P033_LIKE", suggested=sd)
    intent = _make_intent(companions=[Companion(role="孩子", age=5, count=1)])

    # cap 由 _resolve_age_cap 取最严：5 岁 → 75
    assert _resolve_age_cap(intent) == 75
    # 投影后 suggested=90（kid_3_6 桶）；90 > 75 → 罚 0.3
    assert _overload_penalty(poi, intent) == 0.3

    # 验 _utility 末尾真的减了 0.5 * 0.3 = 0.15
    intent_no_kid = _make_intent(companions=[Companion(role="妻子", count=1)])
    w = _make_weights()
    score_with_kid, _ = _utility(poi, None, "", intent, w)
    score_no_kid, _ = _utility(poi, None, "", intent_no_kid, w)
    # 5 岁娃因 overload 被扣 0.15 分；2 次只差 penalty 一项
    assert score_no_kid - score_with_kid >= 0.149  # 浮点 buffer


# ============================================================
# 测试 2：同 POI 配成人客群 → 无罚分
# ============================================================


def test_adult_only_no_overload_penalty() -> None:
    """同样的 P033 类候选 + 妻子（无 age）→ cap=9999 不触发 → penalty 0.0。"""
    sd = SuggestedDuration(default=180, kid_3_6=90, multi_gen=90)
    poi = _make_poi(poi_id="P033_LIKE", suggested=sd)
    intent_adult = _make_intent(companions=[Companion(role="妻子", count=1)])

    assert _resolve_age_cap(intent_adult) >= 9999
    assert _overload_penalty(poi, intent_adult) == 0.0


def test_adult_explicit_age_no_overload_penalty() -> None:
    """30 岁成人（cap 不触发任何分级 → 9999） + 推荐 180min → penalty 0.0。"""
    sd = SuggestedDuration(default=180, kid_3_6=90)
    poi = _make_poi(suggested=sd)
    intent = _make_intent(companions=[Companion(role="伴侣", age=30, count=1)])

    assert _resolve_age_cap(intent) >= 9999
    assert _overload_penalty(poi, intent) == 0.0


def test_overload_penalty_int_form_kid_under_cap() -> None:
    """旧 int 形态 + 5 岁娃 + suggested=60min → suggested(60) <= cap(75) → 不罚。"""
    poi = _make_poi(suggested=60)
    intent = _make_intent(companions=[Companion(role="孩子", age=5, count=1)])
    assert _overload_penalty(poi, intent) == 0.0


def test_overload_penalty_int_form_kid_over_cap() -> None:
    """旧 int 形态 + 5 岁娃 + suggested=120min → 120 > cap 75 → 罚 0.3。"""
    poi = _make_poi(suggested=120)
    intent = _make_intent(companions=[Companion(role="孩子", age=5, count=1)])
    assert _overload_penalty(poi, intent) == 0.3


def test_overload_penalty_no_suggested_returns_zero() -> None:
    """suggested_duration_minutes=None → 无信息可比，penalty 0.0。"""
    poi = _make_poi(suggested=None)
    intent = _make_intent(companions=[Companion(role="孩子", age=5, count=1)])
    assert _overload_penalty(poi, intent) == 0.0


# ============================================================
# 测试 3：DINING_SLOTS 跟随 _resolve_time_window 推
# ============================================================


def test_dynamic_dining_slots_morning_intent() -> None:
    """09:00 出发 + 4-6h 总时长 → 推算的用餐时段 ≠ 默认 17:00 三连。

    覆盖 spec R5：不再硬编码 ("17:00","17:30","18:00")。
    """
    intent = _make_intent(start_time="today_morning", duration_hours=[4, 6])
    tracer = Tracer()
    slots = _resolve_dynamic_dining_slots(
        intent, mid_nodes=["主活动", "用餐"], tracer=tracer
    )
    # 必须返非空 tuple
    assert isinstance(slots, tuple) and len(slots) > 0
    # 不应等同于默认 DINING_SLOTS（早上出发推出来不会是 17:00）
    assert slots != DINING_SLOTS
    # 早上 09:00 出发 + 主活动 ~2h → 用餐时段在 11:00 前后
    first_slot_h = int(slots[0].split(":")[0])
    assert 10 <= first_slot_h <= 13, f"早上场景首个用餐时段应在 10-13 点，实际 {slots[0]}"


def test_dynamic_dining_slots_afternoon_intent() -> None:
    """14:00 出发 + 3-5h → 用餐时段在 16:00-18:00 区间，含若干候选。"""
    intent = _make_intent(start_time="today_afternoon", duration_hours=[3, 5])
    tracer = Tracer()
    slots = _resolve_dynamic_dining_slots(
        intent, mid_nodes=["主活动", "用餐"], tracer=tracer
    )
    assert len(slots) >= 1
    # 首个时段应 ≥ 14:00 + 主活动（保证不会回到默认 17:00 三连之外）
    first_h = int(slots[0].split(":")[0])
    first_m = int(slots[0].split(":")[1])
    first_total = first_h * 60 + first_m
    assert first_total >= 14 * 60 + 30, f"下午 14:00 出发首个用餐时段应 ≥ 14:30，实际 {slots[0]}"


def test_dynamic_dining_slots_dinner_only_segment() -> None:
    """仅有 "用餐" 段（没有主活动）→ 时段从出发后 ~30min 起算，不会偏到 17:00。"""
    intent = _make_intent(start_time="today_evening", duration_hours=[1, 2])
    tracer = Tracer()
    slots = _resolve_dynamic_dining_slots(
        intent, mid_nodes=["用餐"], tracer=tracer
    )
    assert len(slots) >= 1
    # 18:00 出发，仅用餐 → 用餐时段应在 18:00-20:00 区间
    first_h = int(slots[0].split(":")[0])
    assert 18 <= first_h <= 20, f"晚间仅用餐场景首个时段应在 18-20 点，实际 {slots[0]}"


# ============================================================
# 测试 4：ADR-0009 决策 6 —— ViolationCode → ILS 重搜动作 映射表
# ============================================================


def _make_failed_candidate(
    *,
    poi_id: str = "P_FAIL",
    rest_id: str = "R_FAIL",
    dining_time: str = "17:30",
) -> CandidatePlan:
    poi = _make_poi(poi_id=poi_id, distance_km=4.5, suggested=90)
    rest = Restaurant(
        id=rest_id,
        name="测试餐厅",
        cuisine="中餐",
        location=Location(name="测试地", lat=30.25, lng=120.15),
        distance_km=4.5,
        opening_hours="11:00-22:00",
        avg_price=120,
        rating=4.0,
        suitable_for=["家庭日常"],
    )
    return CandidatePlan(
        main_poi=poi,
        restaurant=rest,
        dining_time=dining_time,
        backup_pois=[],
    )


def _make_itinerary_for_blame(
    *, poi_id: str = "P_FAIL", rest_id: str = "R_FAIL"
) -> Itinerary:
    """最小合法 Itinerary（home/poi/restaurant/home），只为 `_blamed_target` 解析
    field_path 的 "nodes[idx]" 下标用——不跑真 critic 校验，字段值只求形状合法。
    """
    nodes = [
        ActivityNode(
            node_id="n0", kind="起点", target_kind="home", target_id="home",
            start_time="14:00", duration_min=0, title="出发",
        ),
        ActivityNode(
            node_id="n1", kind="主活动", target_kind="poi", target_id=poi_id,
            start_time="14:10", duration_min=90, title=poi_id,
        ),
        ActivityNode(
            node_id="n2", kind="用餐", target_kind="restaurant", target_id=rest_id,
            start_time="17:30", duration_min=60, title=rest_id,
        ),
        ActivityNode(
            node_id="n3", kind="终点", target_kind="home", target_id="home",
            start_time="19:00", duration_min=0, title="回家",
        ),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="14:00",
            minutes=10, mode="taxi", path_type="real_route"),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="15:40",
            minutes=10, mode="taxi", path_type="real_route"),
        Hop(hop_id="h2", from_node_id="n2", to_node_id="n3", start_time="18:30",
            minutes=10, mode="taxi", path_type="real_route"),
    ]
    return Itinerary(summary="测试用最小行程", nodes=nodes, hops=hops, total_minutes=300)


def _intent_distance5() -> IntentExtraction:
    return _make_intent(
        companions=[Companion(role="妻子")],
        distance_max_km=5.0,
    )


# ---- 4a. _classify_violation：按 ViolationCode + severity 路由到动作桶 ----


def test_classify_violation_restaurant_full_and_meal_time_route_to_restaurant_time() -> None:
    """RESTAURANT_FULL_UNRESOLVED / MEAL_TIME_UNREASONABLE → "restaurant_time" 桶
    （拉黑 (餐厅,时段)，移时段 / 自然连带换店）。"""
    for code in (ViolationCode.RESTAURANT_FULL_UNRESOLVED, ViolationCode.MEAL_TIME_UNREASONABLE):
        v = Violation(code=code, severity=Severity.HARD, message="测试")
        assert _classify_violation(v) == {"restaurant_time"}, code


def test_classify_violation_dietary_and_capacity_route_to_restaurant_swap() -> None:
    """DIETARY_VIOLATION / CAPACITY_REQUIREMENT_VIOLATED → "restaurant_swap" 桶（整店拉黑）。"""
    for code in (ViolationCode.DIETARY_VIOLATION, ViolationCode.CAPACITY_REQUIREMENT_VIOLATED):
        v = Violation(code=code, severity=Severity.HARD, message="测试")
        assert _classify_violation(v) == {"restaurant_swap"}, code


def test_classify_violation_social_hard_routes_to_directed_swap() -> None:
    v = Violation(code=ViolationCode.SOCIAL_CONTEXT_MISMATCH, severity=Severity.HARD, message="测试")
    assert _classify_violation(v) == {"directed_swap"}


def test_classify_violation_social_soft_poor_is_ignored() -> None:
    """SOCIAL_CONTEXT_MISMATCH 的 POOR（soft）档不进重搜（ADR-0009 决策 3）。"""
    v = Violation(code=ViolationCode.SOCIAL_CONTEXT_MISMATCH, severity=Severity.SOFT, message="测试")
    assert _classify_violation(v) == set()


def test_classify_violation_opening_hours_routes_to_opening_hours_bucket() -> None:
    v = Violation(code=ViolationCode.OPENING_HOURS_VIOLATION, severity=Severity.HARD, message="测试")
    assert _classify_violation(v) == {"opening_hours"}


def test_classify_violation_duration_out_of_range_routes_to_distance_lever() -> None:
    v = Violation(code=ViolationCode.DURATION_OUT_OF_RANGE, severity=Severity.HARD, message="测试")
    assert _classify_violation(v) == {"distance_lever"}


def test_classify_violation_floor_only_codes_produce_no_bucket() -> None:
    """结构码 + AGE_DURATION_MISMATCH：ILS 搜索变量不参与，不产生任何重搜桶（落 rule 地板）。"""
    floor_codes = (
        ViolationCode.INVARIANT_BROKEN,
        ViolationCode.NODES_INCOMPLETE,
        ViolationCode.TIMELINE_INCONSISTENT,
        ViolationCode.TOOL_RESPONSE_INCONSISTENCY,
        ViolationCode.HOP_INFEASIBLE,
        ViolationCode.AGE_DURATION_MISMATCH,
    )
    for code in floor_codes:
        v = Violation(code=code, severity=Severity.HARD, message="测试")
        assert _classify_violation(v) == set(), f"{code} 不应产生任何重搜桶"


def test_classify_violation_distance_exceeded_soft_is_ignored() -> None:
    v = Violation(code=ViolationCode.DISTANCE_EXCEEDED, severity=Severity.SOFT, message="测试")
    assert _classify_violation(v) == set()


# ---- 4b. _compute_blacklists：动作桶 → 具体黑名单条目（含定向 blame） ----


def test_blacklist_restaurant_full_shifts_time_not_whole_restaurant() -> None:
    """RESTAURANT_FULL_UNRESOLVED → 拉黑 (餐厅,时段)，不牵连整店——「不行则换店」
    由同一机制自然涌现（其它 (rest,slot) 组合仍在搜索空间里，不需要额外分支）。"""
    failed = _make_failed_candidate(rest_id="R1", dining_time="17:30")
    itinerary = _make_itinerary_for_blame(rest_id="R1")
    v = Violation(
        code=ViolationCode.RESTAURANT_FULL_UNRESOLVED, severity=Severity.HARD,
        message="R1 17:30 已满", field_path="nodes[2].start_time",
    )
    bl_poi, bl_rest, bl_rest_time = _compute_blacklists(
        failed, itinerary, _intent_distance5(), [v]
    )
    assert ("R1", "17:30") in bl_rest_time
    assert "R1" not in bl_rest


def test_blacklist_dietary_and_capacity_blacklist_whole_restaurant() -> None:
    for code in (ViolationCode.DIETARY_VIOLATION, ViolationCode.CAPACITY_REQUIREMENT_VIOLATED):
        failed = _make_failed_candidate(rest_id="R_SWAP")
        itinerary = _make_itinerary_for_blame(rest_id="R_SWAP")
        v = Violation(
            code=code, severity=Severity.HARD, message="测试",
            field_path="nodes[2].target_id",
        )
        _, bl_rest, _ = _compute_blacklists(failed, itinerary, _intent_distance5(), [v])
        assert "R_SWAP" in bl_rest, code


def test_blacklist_social_context_directed_to_poi_node_only() -> None:
    """SOCIAL_CONTEXT_MISMATCH 按 field_path 定向拉黑：命中 POI 节点时只拉黑 POI，
    不像旧版「POI+餐厅一起拉黑」那样连坐。"""
    failed = _make_failed_candidate(poi_id="P_BIZ", rest_id="R_OK")
    itinerary = _make_itinerary_for_blame(poi_id="P_BIZ", rest_id="R_OK")
    v = Violation(
        code=ViolationCode.SOCIAL_CONTEXT_MISMATCH, severity=Severity.HARD,
        message="测试", field_path="nodes[1].target_id",  # nodes[1] = POI
    )
    bl_poi, bl_rest, _ = _compute_blacklists(failed, itinerary, _intent_distance5(), [v])
    assert "P_BIZ" in bl_poi
    assert "R_OK" not in bl_rest


def test_blacklist_social_context_directed_to_restaurant_node_only() -> None:
    failed = _make_failed_candidate(poi_id="P_OK", rest_id="R_LOUD")
    itinerary = _make_itinerary_for_blame(poi_id="P_OK", rest_id="R_LOUD")
    v = Violation(
        code=ViolationCode.SOCIAL_CONTEXT_MISMATCH, severity=Severity.HARD,
        message="测试", field_path="nodes[2].target_id",  # nodes[2] = restaurant
    )
    bl_poi, bl_rest, _ = _compute_blacklists(failed, itinerary, _intent_distance5(), [v])
    assert "R_LOUD" in bl_rest
    assert "P_OK" not in bl_poi


def test_blacklist_social_context_falls_back_to_both_when_field_path_unresolvable() -> None:
    """field_path 解析失败（这里用 itinerary=None 模拟）→ 保守兜底：两个都拉黑，不静默漏修。"""
    failed = _make_failed_candidate(poi_id="P_X", rest_id="R_Y")
    v = Violation(
        code=ViolationCode.SOCIAL_CONTEXT_MISMATCH, severity=Severity.HARD,
        message="测试", field_path="nodes[1].target_id",
    )
    bl_poi, bl_rest, _ = _compute_blacklists(failed, None, _intent_distance5(), [v])
    assert "P_X" in bl_poi
    assert "R_Y" in bl_rest


def test_blacklist_opening_hours_restaurant_node_shifts_time() -> None:
    failed = _make_failed_candidate(rest_id="R_CLOSED", dining_time="21:30")
    itinerary = _make_itinerary_for_blame(rest_id="R_CLOSED")
    v = Violation(
        code=ViolationCode.OPENING_HOURS_VIOLATION, severity=Severity.HARD,
        message="测试", field_path="nodes[2].start_time",
    )
    bl_poi, bl_rest, bl_rest_time = _compute_blacklists(
        failed, itinerary, _intent_distance5(), [v]
    )
    assert ("R_CLOSED", "21:30") in bl_rest_time
    assert "R_CLOSED" not in bl_rest


def test_blacklist_opening_hours_poi_node_blacklists_whole_poi() -> None:
    """POI 侧 opening_hours 违规：start_time 非 ILS 变量，只能拉黑整个 POI 换。"""
    failed = _make_failed_candidate(poi_id="P_EARLY_CLOSE")
    itinerary = _make_itinerary_for_blame(poi_id="P_EARLY_CLOSE")
    v = Violation(
        code=ViolationCode.OPENING_HOURS_VIOLATION, severity=Severity.HARD,
        message="测试", field_path="nodes[1].start_time",
    )
    bl_poi, _bl_rest, bl_rest_time = _compute_blacklists(
        failed, itinerary, _intent_distance5(), [v]
    )
    assert "P_EARLY_CLOSE" in bl_poi
    assert bl_rest_time == set()


def test_blacklist_meal_time_unreasonable_shifts_time() -> None:
    failed = _make_failed_candidate(rest_id="R_OFFHOUR", dining_time="14:30")
    v = Violation(
        code=ViolationCode.MEAL_TIME_UNREASONABLE, severity=Severity.HARD,
        message="测试", field_path="nodes[2].start_time",
    )
    _bl_poi, bl_rest, bl_rest_time = _compute_blacklists(
        failed, None, _intent_distance5(), [v]
    )
    assert ("R_OFFHOUR", "14:30") in bl_rest_time
    assert "R_OFFHOUR" not in bl_rest


def test_blacklist_duration_out_of_range_blacklists_far_entities_when_near_limit() -> None:
    """弱杠杆：距离确实接近上限时才拉黑最远实体（4.5km 逼近 5.0km 上限）。"""
    intent = _intent_distance5()  # distance_max_km=5.0
    failed = _make_failed_candidate(poi_id="P_FAR", rest_id="R_FAR")  # 4.5km
    v = Violation(code=ViolationCode.DURATION_OUT_OF_RANGE, severity=Severity.HARD, message="测试")
    bl_poi, bl_rest, _ = _compute_blacklists(failed, None, intent, [v])
    assert "P_FAR" in bl_poi
    assert "R_FAR" in bl_rest


def test_blacklist_duration_out_of_range_no_action_when_not_far() -> None:
    """距离远小于上限（不是通勤过远导致超时）→ 弱杠杆不触发，空黑名单交给地板。"""
    intent = _make_intent(companions=[Companion(role="妻子")], distance_max_km=20.0)
    failed = _make_failed_candidate(poi_id="P_NEAR", rest_id="R_NEAR")  # 4.5km ≪ 20km
    v = Violation(code=ViolationCode.DURATION_OUT_OF_RANGE, severity=Severity.HARD, message="测试")
    bl_poi, bl_rest, bl_rest_time = _compute_blacklists(failed, None, intent, [v])
    assert bl_poi == set() and bl_rest == set() and bl_rest_time == set()


def test_blacklist_floor_only_codes_produce_empty_blacklist() -> None:
    """结构码 / AGE_DURATION_MISMATCH：无黑名单动作——留给 rule 地板，非 ILS 搜索变量可修。"""
    failed = _make_failed_candidate()
    for code in (
        ViolationCode.INVARIANT_BROKEN,
        ViolationCode.NODES_INCOMPLETE,
        ViolationCode.AGE_DURATION_MISMATCH,
    ):
        v = Violation(code=code, severity=Severity.HARD, message="测试")
        bl_poi, bl_rest, bl_rest_time = _compute_blacklists(
            failed, None, _intent_distance5(), [v]
        )
        assert bl_poi == set() and bl_rest == set() and bl_rest_time == set(), code


def test_blacklist_ignores_soft_violations_regardless_of_code() -> None:
    """severity=SOFT 一律不产生黑名单动作（防御性：即便调用方传入未过滤的 soft
    违规，也不会误触发重搜——ADR-0009 决策 3 的键是 (code, severity)，不是只看 code）。"""
    failed = _make_failed_candidate(rest_id="R1", dining_time="17:30")
    v = Violation(
        code=ViolationCode.RESTAURANT_FULL_UNRESOLVED, severity=Severity.SOFT, message="测试",
    )
    bl_poi, bl_rest, bl_rest_time = _compute_blacklists(
        failed, None, _intent_distance5(), [v]
    )
    assert bl_poi == set() and bl_rest == set() and bl_rest_time == set()


def test_blacklist_aggregates_multiple_violations() -> None:
    """多类违规并存 → 黑名单合集覆盖各自动作（满座移时段 + 饮食换店 + 定向社交 + 地板码不产出）。"""
    failed = _make_failed_candidate(poi_id="P_X", rest_id="R_Y", dining_time="17:00")
    itinerary = _make_itinerary_for_blame(poi_id="P_X", rest_id="R_Y")
    violations = [
        Violation(
            code=ViolationCode.RESTAURANT_FULL_UNRESOLVED, severity=Severity.HARD,
            message="R_Y 17:00 已满", field_path="nodes[2].start_time",
        ),
        Violation(
            code=ViolationCode.DIETARY_VIOLATION, severity=Severity.HARD,
            message="R_Y 含辣味菜", field_path="nodes[2].target_id",
        ),
        Violation(
            code=ViolationCode.SOCIAL_CONTEXT_MISMATCH, severity=Severity.HARD,
            message="POI 调性不符", field_path="nodes[1].target_id",
        ),
        Violation(
            code=ViolationCode.AGE_DURATION_MISMATCH, severity=Severity.HARD,
            message="地板码，不应产生黑名单动作",
        ),
    ]
    bl_poi, bl_rest, bl_rest_time = _compute_blacklists(
        failed, itinerary, _intent_distance5(), violations
    )
    assert ("R_Y", "17:00") in bl_rest_time  # RESTAURANT_FULL_UNRESOLVED
    assert "R_Y" in bl_rest  # DIETARY_VIOLATION
    assert "P_X" in bl_poi  # SOCIAL_CONTEXT_MISMATCH 定向到 POI 节点
