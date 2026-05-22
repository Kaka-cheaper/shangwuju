"""tests.test_social_compat —— Step 5：social_context 兼容矩阵 + critic 升级。

覆盖：
1. evaluate 矩阵基本判定（MATCH / BLOCKING / POOR / ACCEPTABLE）
2. evaluate_poi / evaluate_restaurant 便捷封装
3. critics_v2 用 social_compat 矩阵：BLOCKING → CRITICAL
4. critics_v2 POOR → WARNING（不打断流程）
5. critics_v2 MATCH → 不报
6. mock 数据无 suitable_for 字段时不误伤
"""

from __future__ import annotations

from agent.v2.critics_v2 import (
    Severity,
    ViolationCode,
    validate_itinerary,
)
from agent.v2.social_compat import (
    CompatLevel,
    evaluate,
    evaluate_poi,
    evaluate_restaurant,
)
from schemas.domain import (
    Location,
    Poi,
    PoiCapacity,
    Restaurant,
    RestaurantCapacity,
)
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary, ItineraryStage, OrderRecord


# ============================================================
# fixture
# ============================================================

def _intent(social: str) -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=[4, 6],  # type: ignore[arg-type]
        distance_max_km=10.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context=social,
        raw_input="测试",
        parse_confidence=0.9,
    )


def _filter_social(violations):
    return [
        v for v in violations
        if v.code == ViolationCode.SOCIAL_CONTEXT_MISMATCH
    ]


# ============================================================
# 矩阵单测
# ============================================================

def test_evaluate_match_when_social_in_suitable_for():
    level, _ = evaluate("家庭日常", ["家庭日常", "亲子"])
    assert level == CompatLevel.MATCH


def test_evaluate_blocking_solo_vs_family():
    level, reason = evaluate("独处放空", ["家庭日常"])
    assert level == CompatLevel.BLOCKING
    assert reason  # 必带原因


def test_evaluate_blocking_family_vs_business():
    level, reason = evaluate("家庭日常", ["商务接待"])
    assert level == CompatLevel.BLOCKING
    assert "商务" in reason or "成人" in reason


def test_evaluate_blocking_elderly_vs_party():
    level, _ = evaluate("老人伴助", ["朋友热闹"])
    assert level == CompatLevel.BLOCKING


def test_evaluate_poor_couple_vs_family():
    level, _ = evaluate("情侣亲密", ["家庭日常"])
    assert level == CompatLevel.POOR


def test_evaluate_acceptable_default():
    """无规则命中 → ACCEPTABLE。"""
    level, _ = evaluate("朋友热闹", ["独处放空"])
    # 矩阵未声明此组合 → 默认通过（mock 数据可能漏字段）
    assert level == CompatLevel.ACCEPTABLE


def test_evaluate_acceptable_when_no_suitable_for():
    """候选 suitable_for 空 → ACCEPTABLE。"""
    level, _ = evaluate("家庭日常", [])
    assert level == CompatLevel.ACCEPTABLE


def test_evaluate_acceptable_when_no_input_social():
    level, _ = evaluate("", ["家庭日常"])
    assert level == CompatLevel.ACCEPTABLE


# ============================================================
# 便捷封装
# ============================================================

def test_evaluate_poi_uses_poi_suitable_for():
    poi = Poi(
        id="P_TEST",
        name="测试",
        type="测试",
        location=Location(name="测试", lat=30.27, lng=120.15),
        distance_km=1.0,
        opening_hours="09:00-21:00",
        rating=4.5,
        suitable_for=["家庭日常"],
    )
    intent = _intent("独处放空")
    level, reason = evaluate_poi(intent, poi)
    assert level == CompatLevel.BLOCKING
    assert reason


def test_evaluate_restaurant_uses_restaurant_suitable_for():
    rest = Restaurant(
        id="R_TEST",
        name="测试",
        cuisine="测试",
        location=Location(name="测试", lat=30.27, lng=120.15),
        distance_km=1.0,
        opening_hours="11:00-22:00",
        avg_price=100,
        rating=4.5,
        suitable_for=["商务接待"],
        capacity=RestaurantCapacity(),
    )
    intent = _intent("家庭日常")
    level, _ = evaluate_restaurant(intent, rest)
    assert level == CompatLevel.BLOCKING


# ============================================================
# critic 升级回归测
# ============================================================

def _make_itinerary_with_restaurant(rid: str) -> Itinerary:
    """构造带指定餐厅 id 的 5 段标准合法行程，最后段留 30min buffer 防 commute critic 误伤。"""
    return Itinerary(
        summary="x",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:30", title="出发"),
            ItineraryStage(kind="主活动", start="14:30", end="16:00", title="活动"),
            ItineraryStage(kind="转场", start="16:00", end="17:30", title="转场"),
            ItineraryStage(
                kind="用餐",
                start="17:30",
                end="18:30",
                title="餐厅",
                restaurant_id=rid,
            ),
            ItineraryStage(kind="返回", start="19:00", end="19:30", title="回家"),
        ],
        total_minutes=330,
    )


def test_critic_blocking_social_triggers_critical():
    """家庭日常场景指向商务接待餐厅 → CRITICAL。

    使用真 mock 餐厅 R008（金樽商务日料会所，suitable_for=["商务接待", "纪念日仪式感"]）。
    """
    intent = _intent("家庭日常")
    itinerary = _make_itinerary_with_restaurant("R008")

    violations = validate_itinerary(itinerary, intent)
    social_v = _filter_social(violations)
    critical = [v for v in social_v if v.severity == Severity.CRITICAL]

    assert critical, (
        f"家庭日常 + 商务接待餐厅应触发 CRITICAL，实际 social violations："
        f"{[(v.severity, v.message) for v in social_v]}"
    )


def test_critic_match_no_violation():
    """家庭日常 + 适合家庭餐厅 → 不报。

    R001 = 轻语沙拉（suitable_for 含 家庭日常）。
    """
    intent = _intent("家庭日常")
    itinerary = _make_itinerary_with_restaurant("R001")

    violations = validate_itinerary(itinerary, intent)
    social_v = _filter_social(violations)
    assert not social_v, f"匹配场景不应触发 social mismatch，实际：{[v.message for v in social_v]}"


def test_critic_solo_with_multi_seat_order_still_critical():
    """独处放空 + orders 含 2 人位 → 仍 CRITICAL（保留旧 detail 检查）。"""
    intent = _intent("独处放空")
    itinerary = Itinerary(
        summary="x",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:30", title="出发"),
            ItineraryStage(kind="主活动", start="14:30", end="16:00", title="活动"),
            ItineraryStage(kind="转场", start="16:00", end="17:30", title="转场"),
            ItineraryStage(
                kind="用餐",
                start="17:30",
                end="18:30",
                title="餐厅",
                restaurant_id=None,
            ),
            ItineraryStage(kind="返回", start="19:00", end="19:30", title="回家"),
        ],
        orders=[
            OrderRecord(
                order_id="X1",
                kind="餐厅预约",
                target_id="R001",
                target_name="测试餐厅",
                detail="17:30 2 人位",
            ),
        ],
        total_minutes=330,
    )
    violations = validate_itinerary(itinerary, intent)
    social_v = _filter_social(violations)
    critical = [v for v in social_v if v.severity == Severity.CRITICAL]
    assert critical, "独处 + 2 人位 order 应仍触发 CRITICAL（旧逻辑保留）"
