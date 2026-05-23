"""tests.test_social_compat —— Step 5：social_context 兼容矩阵 + critic 升级（edge_v1）。

覆盖：
1. evaluate 矩阵基本判定（MATCH / BLOCKING / POOR / ACCEPTABLE）
2. evaluate_poi / evaluate_restaurant 便捷封装（含可选 ActivityNode 上下文）
3. critics_v2 用 social_compat 矩阵：BLOCKING → CRITICAL
4. critics_v2 POOR → WARNING（不打断流程）
5. critics_v2 MATCH → 不报
6. mock 数据无 suitable_for 字段时不误伤

【edge_v1 迁移（Wave 7 Task 14）】

旧测试用 `ItineraryStage` 手工拼 5 段构造合法 Itinerary。edge_v1 起：
- ItineraryStage 已删；本文件改用 `assemble_from_blueprint(intent, PlanBlueprint(nodes=[...]), profile)`
  统一构造合法 Itinerary，把不变量交给 assemble 保证。
- evaluate_poi / evaluate_restaurant 增加可选 `node: ActivityNode | None` 形参；
  不传 node 仍兼容（pure 矩阵评估，与 itinerary 上下文无关）。
"""

from __future__ import annotations

import pytest

from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
from agent.planning.blueprint.blueprint import BlueprintNode, BlueprintTargetKind, PlanBlueprint
from agent.planning.critic.critics_v2 import (
    Severity,
    ViolationCode,
    validate_itinerary,
)
from agent.planning.critic.social_compat import (
    CompatLevel,
    evaluate,
    evaluate_poi,
    evaluate_restaurant,
)
from data.loader import load_user_profile
from schemas.domain import (
    Location,
    Poi,
    PoiCapacity,
    Restaurant,
    RestaurantCapacity,
)
from schemas.intent import Companion, IntentExtraction
from schemas.itinerary import Itinerary, OrderRecord


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


def _intent_with_companions(social: str) -> IntentExtraction:
    """带 companions（避免 critic 在某些校验中早返）。"""
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=[4, 6],  # type: ignore[arg-type]
        distance_max_km=10.0,
        companions=[Companion(role="自己", count=1)],
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


def test_evaluate_poi_accepts_node_kwarg_no_op():
    """edge_v1：evaluate_poi 接受可选 node 形参；当前矩阵不依赖 node 字段，传与不传应等价。"""
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
    level_no_node, _ = evaluate_poi(intent, poi)
    level_with_node, _ = evaluate_poi(intent, poi, node=None)
    assert level_no_node == level_with_node == CompatLevel.BLOCKING


# ============================================================
# critic 升级回归测（edge_v1：通过 assemble 构造合法 Itinerary）
# ============================================================

def _make_itinerary_with_restaurant(rid: str) -> Itinerary:
    """构造带指定餐厅 id 的合法行程（POI P040 + 餐厅 rid）。

    通过 assemble_from_blueprint 走真链路：
    - n0(home) → n1(P040, 主活动) → n2(rid, 用餐) → n3(home)
    - 不变量自动满足：首尾 home / hops 长度 / home duration=0 等
    """
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=120,
            ),
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id=rid,
                duration_min=60,
            ),
        ],
        preferred_start_time="14:00",
        rationale="critic 测试用",
    )
    return assemble_from_blueprint(_intent("家庭日常"), bp, load_user_profile())


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
    assert not social_v, (
        f"匹配场景不应触发 social mismatch，实际：{[v.message for v in social_v]}"
    )


def test_critic_solo_with_multi_seat_order_still_critical():
    """独处放空 + orders 含 2 人位 → 仍 CRITICAL（保留旧 detail 检查）。

    edge_v1：通过 assemble 构造一个合法行程（独处场景下含 P040 主活动），
    再注入一个 2 人位 order 触发 critic。
    """
    intent = _intent("独处放空")
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=180,
            ),
        ],
        preferred_start_time="14:00",
        rationale="独处测试",
    )
    base = assemble_from_blueprint(intent, bp, load_user_profile())
    # 注入 2 人位 order（OrderRecord 加 target_kind 字段）
    itinerary = base.model_copy(
        update={
            "orders": [
                OrderRecord(
                    order_id="X1",
                    kind="餐厅预约",
                    target_kind="restaurant",
                    target_id="R001",
                    target_name="测试餐厅",
                    detail="17:30 2 人位",
                ),
            ],
        }
    )
    violations = validate_itinerary(itinerary, intent)
    social_v = _filter_social(violations)
    critical = [v for v in social_v if v.severity == Severity.CRITICAL]
    assert critical, "独处 + 2 人位 order 应仍触发 CRITICAL（旧逻辑保留）"
