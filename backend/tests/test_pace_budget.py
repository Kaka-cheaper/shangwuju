"""tests.test_pace_budget —— ADR-0010 D-3：节奏 / slack / 区间填充模型。

覆盖 `agent.planning.planners.pace_budget`：
1. `pace(intent)`：companions/social_context 信号 → relaxed/medium/energetic，
   混合信号取最受限。
2. `slack_fraction(pace)`：每档留白比例（含未知档防御性回退）。
3. `interval_fill_targets(intent, pace)`：区间填充参数换算。

本文件是纯新增（D-3 铁律同 D-1）：不改任何既有 planner 流程。
"""

from __future__ import annotations

import pytest

from agent.planning.planners import pace_budget as pb
from schemas.intent import Companion, IntentExtraction


def _intent(
    *,
    social_context: str = "家庭日常",
    companions: tuple[Companion, ...] = (),
    duration_hours: list[int] = None,
) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=duration_hours if duration_hours is not None else [3, 5],
        distance_max_km=10.0,
        companions=list(companions),
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context=social_context,
        raw_input="测试",
        parse_confidence=0.9,
        ambiguous_fields=[],
    )


# ============================================================
# 1. pace(intent)
# ============================================================


def test_pace_defaults_to_medium_with_no_signal():
    intent = _intent(social_context="家庭日常", companions=())
    assert pb.pace(intent) == pb.PACE_MEDIUM


def test_pace_relaxed_when_toddler_companion():
    intent = _intent(companions=(Companion(role="孩子", age=3, count=1),))
    assert pb.pace(intent) == pb.PACE_RELAXED


def test_pace_relaxed_when_young_child_companion_at_boundary_age_6():
    intent = _intent(companions=(Companion(role="孩子", age=6, count=1),))
    assert pb.pace(intent) == pb.PACE_RELAXED


def test_pace_medium_when_child_age_7_not_relaxed_boundary():
    """7 岁不落 relaxed 的 ≤6 岁分桶（与 age_caps 学童档 7-12 边界一致的信号集合）。"""
    intent = _intent(companions=(Companion(role="孩子", age=7, count=1),))
    assert pb.pace(intent) == pb.PACE_MEDIUM


def test_pace_relaxed_when_senior_companion():
    intent = _intent(companions=(Companion(role="外公", age=75, count=1),))
    assert pb.pace(intent) == pb.PACE_RELAXED


def test_pace_medium_when_senior_just_below_threshold():
    intent = _intent(companions=(Companion(role="长辈", age=74, count=1),))
    assert pb.pace(intent) == pb.PACE_MEDIUM


@pytest.mark.parametrize("ctx", ["独处放空", "老人伴助"])
def test_pace_relaxed_social_contexts(ctx):
    intent = _intent(social_context=ctx, companions=())
    assert pb.pace(intent) == pb.PACE_RELAXED


def test_pace_energetic_for_friends_gathering():
    intent = _intent(social_context="朋友热闹", companions=(Companion(role="朋友", age=28, count=2),))
    assert pb.pace(intent) == pb.PACE_ENERGETIC


def test_pace_medium_for_couple_context_not_in_relaxed_or_energetic_set():
    """情侣亲密未命中 ADR-0010 D-3 点名的 relaxed/energetic 信号集合 → medium
    （见 pace_budget.py 判断点 1：S3 的"不排满"由 D-4 涌现，不靠本函数）。"""
    intent = _intent(social_context="情侣亲密", companions=(Companion(role="伴侣", age=29, count=1),))
    assert pb.pace(intent) == pb.PACE_MEDIUM


def test_pace_mixed_signal_takes_most_restrictive_relaxed_over_energetic():
    """幼童同行 + 朋友热闹语境（冲突组合）→ 取最受限 relaxed（与 age cap 取最严
    同精神，ADR-0010 决策 10）。"""
    intent = _intent(
        social_context="朋友热闹",
        companions=(Companion(role="孩子", age=3, count=1),),
    )
    assert pb.pace(intent) == pb.PACE_RELAXED


def test_pace_handles_none_intent_gracefully():
    """防御性：intent=None 不应抛异常，回退中等档。"""
    assert pb.pace(None) == pb.PACE_MEDIUM


# ============================================================
# 2. slack_fraction(pace)
# ============================================================


def test_slack_fraction_relaxed_highest():
    assert pb.slack_fraction(pb.PACE_RELAXED) == pb.SLACK_FRACTION_RELAXED


def test_slack_fraction_medium():
    assert pb.slack_fraction(pb.PACE_MEDIUM) == pb.SLACK_FRACTION_MEDIUM


def test_slack_fraction_energetic_lowest():
    assert pb.slack_fraction(pb.PACE_ENERGETIC) == pb.SLACK_FRACTION_ENERGETIC


def test_slack_fraction_monotonic_relaxed_gt_medium_gt_energetic():
    assert pb.SLACK_FRACTION_RELAXED > pb.SLACK_FRACTION_MEDIUM > pb.SLACK_FRACTION_ENERGETIC > 0


def test_slack_fraction_unknown_tier_falls_back_to_medium():
    assert pb.slack_fraction("not_a_real_tier") == pb.SLACK_FRACTION_MEDIUM


# ============================================================
# 3. interval_fill_targets(intent, pace)
# ============================================================


def test_interval_fill_targets_lo_hi_in_minutes():
    intent = _intent(duration_hours=[3, 5])
    targets = pb.interval_fill_targets(intent, pb.PACE_MEDIUM)
    assert targets.lo_min == 180
    assert targets.hi_min == 300


def test_interval_fill_targets_activity_budget_uses_hi_and_slack_fraction():
    intent = _intent(duration_hours=[3, 5])
    targets = pb.interval_fill_targets(intent, pb.PACE_MEDIUM)
    expected = round(300 * (1 - pb.SLACK_FRACTION_MEDIUM))
    assert targets.activity_budget_min == expected


def test_interval_fill_targets_relaxed_leaves_more_slack_than_energetic():
    """同一 intent，relaxed 档的 activity_budget 应显著小于 energetic 档
    （relaxed 留白多 → 活动预算少）。"""
    intent = _intent(duration_hours=[4, 6])
    relaxed = pb.interval_fill_targets(intent, pb.PACE_RELAXED)
    energetic = pb.interval_fill_targets(intent, pb.PACE_ENERGETIC)
    assert relaxed.activity_budget_min < energetic.activity_budget_min


def test_interval_fill_targets_hi_min_unaffected_by_pace():
    """hi_min 是硬性外壳（D-2 budget_min 的数据来源），不随节奏档变化。"""
    intent = _intent(duration_hours=[4, 6])
    for tier in (pb.PACE_RELAXED, pb.PACE_MEDIUM, pb.PACE_ENERGETIC):
        assert pb.interval_fill_targets(intent, tier).hi_min == 360
        assert pb.interval_fill_targets(intent, tier).lo_min == 240


def test_interval_fill_targets_returns_frozen_dataclass_with_named_fields():
    intent = _intent(duration_hours=[3, 5])
    targets = pb.interval_fill_targets(intent, pb.PACE_MEDIUM)
    assert isinstance(targets, pb.IntervalFillTargets)
    with pytest.raises(Exception):
        targets.lo_min = 999  # frozen：不可变
