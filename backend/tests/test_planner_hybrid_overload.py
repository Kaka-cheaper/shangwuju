"""验 spec planning-quality-deep-review R5 ILS 算法兜底 utility 加 overload_penalty。

测试矩阵（4 项）：
1. 5 岁娃 + P033 类候选（default=180 / kid_3_6=90 / cap=75）→ utility 罚分 0.3
2. 同 POI 配成人客群（cap 9999）→ 无罚分（_overload_penalty 返 0.0）
3. DINING_SLOTS 跟随 _resolve_time_window 推（14:00 出发 + 3-5h 时长 → 不再硬码 17:00）
4. 黑名单 4 类全覆盖（time_window / hard_constraint / dietary / social_context）

测试设计：
- 直接调 `_overload_penalty` / `_utility` / `_resolve_dynamic_dining_slots` / `_compute_blacklists`
  四个 module 级 helper，不跑 plan_hybrid 全流程，避免 stub LLM client 等环境依赖。
- 用最小 SuggestedDuration / Poi / Restaurant fixture，不依赖 mock_data 加载。
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

from agent.critics import CriticReport, CriticViolation  # noqa: E402
from agent.planner_hybrid import (  # noqa: E402
    DINING_SLOTS,
    CandidatePlan,
    _classify_violation,
    _compute_blacklists,
    _overload_penalty,
    _resolve_age_cap,
    _resolve_dynamic_dining_slots,
    _utility,
)
from agent.trace import Tracer  # noqa: E402
from agent.weights_llm import PlanningWeights  # noqa: E402
from schemas.domain import Location, Poi, PoiCapacity, Restaurant, SuggestedDuration  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402


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
# 测试 4：黑名单 4 类全覆盖
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


def _intent_distance5() -> IntentExtraction:
    return _make_intent(
        companions=[Companion(role="妻子")],
        distance_max_km=5.0,
    )


def test_blacklist_covers_time_window_violation() -> None:
    """time_window 违规 → (餐厅 id, 时段) 入 rest_time 黑名单。"""
    failed = _make_failed_candidate(rest_id="R1", dining_time="17:30")
    report = CriticReport(
        passed=False,
        violations=[
            CriticViolation(
                critic="time_window",
                severity="hard",
                message="餐厅 R1 17:30 已满（建议改 18:00）",
            )
        ],
    )
    bl_poi, bl_rest, bl_rest_time = _compute_blacklists(
        failed, _intent_distance5(), report
    )
    assert ("R1", "17:30") in bl_rest_time
    assert "R1" not in bl_rest  # time_window 不应连餐厅本身一起拉黑


def test_blacklist_covers_hard_constraint_violation() -> None:
    """hard_constraint「总耗时」违规 → 距离接近上限的 POI/餐厅入黑名单。"""
    failed = _make_failed_candidate()
    report = CriticReport(
        passed=False,
        violations=[
            CriticViolation(
                critic="hard_constraint",
                severity="hard",
                message="总耗时 360 分钟，超过用户上限 300 分钟",
            )
        ],
    )
    bl_poi, bl_rest, _ = _compute_blacklists(failed, _intent_distance5(), report)
    # 4.5km > 5.0 - 1 = 4.0 → 应入黑名单
    assert "P_FAIL" in bl_poi
    assert "R_FAIL" in bl_rest


def test_blacklist_covers_dietary_violation_via_message_keyword() -> None:
    """dietary 违规（message 含「不辣」「过敏」「kids-meal」等关键词）→ 餐厅入黑名单。

    critics.py 当前没暴露 dietary critic name；用 message 关键词路由作 future-proof。
    """
    failed = _make_failed_candidate(rest_id="R_HOT")
    report = CriticReport(
        passed=False,
        violations=[
            CriticViolation(
                critic="hard_constraint",  # critic name 任意，靠 message 关键词路由
                severity="hard",
                message="餐厅 R_HOT 有辣味菜不符合用户「不辣」饮食约束",
            )
        ],
    )
    bl_poi, bl_rest, _ = _compute_blacklists(failed, _intent_distance5(), report)
    assert "R_HOT" in bl_rest


def test_blacklist_covers_social_context_violation() -> None:
    """social_context 违规（critic="style" 或关键词）→ POI/餐厅都入黑名单。"""
    failed = _make_failed_candidate(poi_id="P_BIZ", rest_id="R_LOUD")
    report = CriticReport(
        passed=False,
        violations=[
            CriticViolation(
                critic="style",
                severity="hard",  # 测试用强制 hard 让 _compute_blacklists 处理
                message="主活动 POI 「P_BIZ」未适配场景调性 家庭日常",
            )
        ],
    )
    bl_poi, bl_rest, _ = _compute_blacklists(failed, _intent_distance5(), report)
    assert "P_BIZ" in bl_poi
    assert "R_LOUD" in bl_rest


def test_blacklist_classification_helper_recognizes_all_4_classes() -> None:
    """spec R5：_classify_violation 能把 4 类违规都正确归类。"""
    cases = [
        (
            CriticViolation(
                critic="time_window", severity="hard", message="时段满"
            ),
            "time_window",
        ),
        (
            CriticViolation(
                critic="hard_constraint",
                severity="hard",
                message="总耗时超上限",
            ),
            "hard_constraint",
        ),
        (
            CriticViolation(
                critic="hard_constraint",
                severity="hard",
                message="餐厅含「辣」菜不符饮食",
            ),
            "dietary",
        ),
        (
            CriticViolation(
                critic="style",
                severity="hard",
                message="未适配场景调性 商务接待",
            ),
            "social_context",
        ),
    ]
    for v, expected_class in cases:
        classes = _classify_violation(v)
        assert expected_class in classes, (
            f"违规 {v.message!r} 应归类含 {expected_class}，实际 {classes}"
        )


def test_blacklist_aggregates_multiple_violations() -> None:
    """多类违规并存 → 黑名单合集覆盖全部 4 类。"""
    failed = _make_failed_candidate(poi_id="P_X", rest_id="R_Y", dining_time="17:00")
    report = CriticReport(
        passed=False,
        violations=[
            CriticViolation(
                critic="time_window", severity="hard", message="R_Y 17:00 已满"
            ),
            CriticViolation(
                critic="hard_constraint",
                severity="hard",
                message="总耗时 400 分钟，超过用户上限 300 分钟",
            ),
            CriticViolation(
                critic="hard_constraint",
                severity="hard",
                message="餐厅 R_Y 含辣味菜",
            ),
            CriticViolation(
                critic="style",
                severity="hard",
                message="POI 调性不符 social_context",
            ),
        ],
    )
    bl_poi, bl_rest, bl_rest_time = _compute_blacklists(
        failed, _intent_distance5(), report
    )
    assert ("R_Y", "17:00") in bl_rest_time  # time_window
    assert "P_X" in bl_poi  # hard_constraint 距离 + social_context
    assert "R_Y" in bl_rest  # hard_constraint + dietary + social_context
