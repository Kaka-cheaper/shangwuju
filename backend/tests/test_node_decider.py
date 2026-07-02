"""tests.test_node_decider —— "节点 kind = intent 函数" 决策回归。

每个用例都对应演示场景集 §四 / 用户截图 / pitfalls.md P1-2026-05-17 的潜伏场景。

ADR-0010 D-8：本文件原直接测试 `decide_segments`（旧「段集合」兼容别名，已随
D-8 连同 `FULL_SEGMENTS`/`ALWAYS_INCLUDED`/`explain_segments` 一并删除——生产
侧唯一调用方 `rule_planner.py` 已迁移到 `decide_nodes` 正名）。断言逐条翻译到
`decide_nodes`（返回中间节点 kind 的 list，如 `["主活动"]`/`["用餐"]`/
`["主活动","用餐"]`），行为覆盖不变；`decide_segments` 只是在这层 list 外面
包一层 frozenset + 塞两个从不被下游查询的过程段占位符（"出发"/"返回"/"转场"），
翻译前后判定结果逐场景比对一致。
"""

from __future__ import annotations

import pytest

from agent.planning.blueprint.node_decider import (
    KIND_DINING,
    KIND_MAIN,
    decide_nodes,
    explain_nodes,
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
    nodes = decide_nodes(_intent(duration=[1, 1]))
    assert nodes == [KIND_MAIN], nodes


def test_one_hour_with_dietary_keeps_dining_only():
    """1 小时 + 有饮食偏好 → 直接吃饭，不去 POI。"""
    nodes = decide_nodes(_intent(duration=[1, 1], dietary=("低脂",)))
    assert nodes == [KIND_DINING], nodes


def test_one_hour_dining_focused_context_keeps_dining():
    """商务接待 + 1 小时 → 直接安排用餐（公司报销，公差不需要先逛）。"""
    nodes = decide_nodes(_intent(duration=[1, 1], social="商务接待"))
    assert KIND_DINING in nodes
    assert KIND_MAIN not in nodes


# ============================================================
# 2. 短场景（90-180min）
# ============================================================

def test_two_hour_with_dietary_keeps_both_nodes():
    """2 小时 + dietary → 主活动 + 用餐 两个中间节点。"""
    nodes = decide_nodes(_intent(duration=[2, 2], dietary=("健康轻食",)))
    assert nodes == [KIND_MAIN, KIND_DINING], nodes


def test_two_hour_solo_keeps_only_main():
    """独处放空 2 小时 + 无 dietary → 单纯 1 个 POI。"""
    nodes = decide_nodes(_intent(duration=[2, 2], social="独处放空"))
    assert nodes == [KIND_MAIN]


def test_short_no_dietary_keeps_only_main():
    """2 小时 + 无 dietary（普通"出去玩"）→ 单段 POI。"""
    nodes = decide_nodes(_intent(duration=[2, 2], social="家庭日常"))
    assert nodes == [KIND_MAIN]


def test_short_business_under_threshold_dining_only():
    """商务接待 + 2 小时（120min < 150 阈值）→ 单段用餐。"""
    nodes = decide_nodes(_intent(duration=[2, 2], social="商务接待"))
    assert nodes == [KIND_DINING]


def test_short_business_at_threshold_keeps_both():
    """商务接待 + 2-3 小时（上限 180min ≥ 阈值）→ 主活动+用餐两节点。"""
    nodes = decide_nodes(_intent(duration=[2, 3], social="商务接待"))
    assert nodes == [KIND_MAIN, KIND_DINING]


# ============================================================
# 3. 中长场景（≥ 180min，主活动+用餐两节点为默认）
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
def test_full_demo_scenarios_keep_both_nodes(
    scenario: str, duration: list[int], social: str, dietary: tuple[str, ...]
):
    """演示场景集 §三 全 8 主线场景（除 S7 独处放空外）维持主活动+用餐两节点。"""
    nodes = decide_nodes(_intent(duration=duration, social=social, dietary=dietary))
    assert nodes == [KIND_MAIN, KIND_DINING], f"{scenario} 应维持两节点，实际 {nodes}"


def test_long_solo_keeps_only_main():
    """S7 独处放空 4h → 单段（不强塞用餐）。"""
    nodes = decide_nodes(_intent(duration=[2, 4], social="独处放空"))
    assert nodes == [KIND_MAIN]


def test_long_solo_with_dietary_falls_back_to_both():
    """独处放空但用户提了"想吃下午茶" → 还是给两节点（用户主动说要吃）。"""
    nodes = decide_nodes(
        _intent(duration=[2, 4], social="独处放空", dietary=("下午茶",))
    )
    assert nodes == [KIND_MAIN, KIND_DINING]


# ============================================================
# 4. explain_nodes 诊断文案
# ============================================================


def test_explain_nodes_contains_duration_and_social():
    intent = _intent(duration=[1, 1], social="家庭日常")
    nodes = decide_nodes(intent)
    explanation = explain_nodes(intent, nodes)
    assert "1h" in explanation
    assert "家庭日常" in explanation
    assert "极短" in explanation
