"""tests.test_segment_decider —— "段=intent 函数" 决策回归。

每个用例都对应演示场景集 §四 / 用户截图 / pitfalls.md P1-2026-05-17 的潜伏场景。
"""

from __future__ import annotations

import pytest

from agent.segment_decider import (
    ALWAYS_INCLUDED,
    FULL_SEGMENTS,
    decide_segments,
    explain_segments,
)
from schemas.intent import Companion, IntentExtraction


def _intent(
    *,
    duration: list[int] = [3, 5],
    social: str = "家庭日常",
    dietary: tuple[str, ...] = (),
    physical: tuple[str, ...] = (),
    companions: tuple[Companion, ...] = (),
) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=list(duration),
        distance_max_km=5,
        companions=list(companions),
        physical_constraints=list(physical),
        dietary_constraints=list(dietary),
        experience_tags=[],
        social_context=social,
        raw_input="测试",
        parse_confidence=0.9,
    )


# ============================================================
# 1. 极短场景（< 90min）
# ============================================================

def test_one_hour_no_dietary_drops_dining():
    """截图根因：1 小时 + 无饮食偏好 → 只去 POI，不吃饭。"""
    seg = decide_segments(_intent(duration=[1, 1]))
    assert seg == frozenset({"出发", "主活动", "返回"}), seg


def test_one_hour_with_dietary_keeps_dining_only():
    """1 小时 + 有饮食偏好 → 直接吃饭，不去 POI。"""
    seg = decide_segments(_intent(duration=[1, 1], dietary=("低脂",)))
    assert seg == frozenset({"出发", "用餐", "返回"}), seg


def test_one_hour_dining_focused_context_keeps_dining():
    """商务接待 + 1 小时 → 直接安排用餐（公司报销，公差不需要先逛）。"""
    seg = decide_segments(_intent(duration=[1, 1], social="商务接待"))
    assert "用餐" in seg
    assert "主活动" not in seg


# ============================================================
# 2. 短场景（90-180min）
# ============================================================

def test_two_hour_with_dietary_keeps_three_segments():
    """2 小时 + dietary → 主活动 + 转场 + 用餐 三段。"""
    seg = decide_segments(_intent(duration=[2, 2], dietary=("健康轻食",)))
    assert seg == frozenset({"出发", "主活动", "转场", "用餐", "返回"}), seg


def test_two_hour_solo_keeps_only_main():
    """独处放空 2 小时 + 无 dietary → 单纯 1 个 POI。"""
    seg = decide_segments(_intent(duration=[2, 2], social="独处放空"))
    assert seg == frozenset({"出发", "主活动", "返回"})


def test_short_no_dietary_keeps_only_main():
    """2 小时 + 无 dietary（普通"出去玩"）→ 单段 POI。"""
    seg = decide_segments(_intent(duration=[2, 2], social="家庭日常"))
    assert seg == frozenset({"出发", "主活动", "返回"})


def test_short_business_under_threshold_dining_only():
    """商务接待 + 2 小时（120min < 150 阈值）→ 单段用餐。"""
    seg = decide_segments(_intent(duration=[2, 2], social="商务接待"))
    assert seg == frozenset({"出发", "用餐", "返回"})


def test_short_business_at_threshold_keeps_three():
    """商务接待 + 2.5 小时（150min ≥ 阈值）→ 三段（主活动+用餐）。"""
    seg = decide_segments(_intent(duration=[2, 3], social="商务接待"))
    assert seg == frozenset({"出发", "主活动", "转场", "用餐", "返回"})


# ============================================================
# 3. 中长场景（≥ 180min，5 段保留）
# ============================================================

@pytest.mark.parametrize(
    "scenario,duration,social,dietary",
    [
        ("S1 家庭", [3, 5], "家庭日常", ("低脂", "健康轻食")),
        ("S2 朋友", [3, 5], "朋友热闹", ()),
        ("S3 情侣", [4, 6], "情侣亲密", ()),
        ("S4 老人", [3, 5], "老人伴助", ("软烂",)),
        ("S5 闺蜜", [3, 4], "闺蜜聊天", ("下午茶", "甜品")),
        ("S6 商务", [3, 5], "商务接待", ("高人均", "有包间")),
        ("S8 纪念日", [3, 4], "纪念日仪式感", ("粤菜",)),
    ],
)
def test_full_demo_scenarios_keep_full_segments(
    scenario: str, duration: list[int], social: str, dietary: tuple[str, ...]
):
    """演示场景集 §三 全 8 主线场景维持 5 段，向后兼容。"""
    seg = decide_segments(
        _intent(duration=duration, social=social, dietary=dietary)
    )
    assert seg == FULL_SEGMENTS, f"{scenario} 应维持 5 段，实际 {seg}"


def test_long_solo_keeps_only_main():
    """S7 独处放空 4h → 单段（不强塞用餐）。"""
    seg = decide_segments(_intent(duration=[2, 4], social="独处放空"))
    assert seg == frozenset({"出发", "主活动", "返回"})


def test_long_solo_with_dietary_falls_back_to_full():
    """独处放空但用户提了"想吃下午茶" → 还是给 5 段（用户主动说要吃）。"""
    seg = decide_segments(
        _intent(duration=[2, 4], social="独处放空", dietary=("下午茶",))
    )
    assert seg == FULL_SEGMENTS


# ============================================================
# 4. 不变性约束
# ============================================================

@pytest.mark.parametrize(
    "duration,social,dietary",
    [
        ([1, 1], "家庭日常", ()),
        ([2, 3], "情侣亲密", ()),
        ([4, 6], "纪念日仪式感", ("粤菜",)),
        ([1, 1], "独处放空", ()),
    ],
)
def test_always_includes_depart_and_return(
    duration: list[int], social: str, dietary: tuple[str, ...]
):
    seg = decide_segments(_intent(duration=duration, social=social, dietary=dietary))
    assert ALWAYS_INCLUDED.issubset(seg)


def test_explain_segments_contains_duration_and_social():
    intent = _intent(duration=[1, 1], social="家庭日常")
    seg = decide_segments(intent)
    explanation = explain_segments(intent, seg)
    assert "1h" in explanation
    assert "家庭日常" in explanation
    assert "极短" in explanation
