"""age_caps 单一表 characterization 测试（ADR-0008 红队 X-1/R1+R2）。

【目的】

`check_age_aware_duration` 原先把「年龄→cap」表内联在函数体内（45/75/120/60 四档）。
ADR-0008 B-2b 把该表抽成 `agent/planning/critic/age_caps.py` 单一真相源，check 改读
本表。本文件在重构前后各跑一遍，钉死两件事逐字节不变：

1. `age_caps.cap_for_age` 查表结果（cap_min + tier_label）。
2. `check_age_aware_duration` 产出的 message 文案（含「婴幼儿 ≤45min」等字样）、
   `expected_range`、以及多代际取最严的 reason 文本——与重构前 checks.py 内联版本
   逐字节相同。

这是 characterization 测试（行为保持），不是新行为。
"""

from __future__ import annotations

from agent.planning.critic._rules.checks import check_age_aware_duration
from agent.planning.critic.age_caps import (
    PRESCHOOL_CAP_MIN,
    SCHOOL_AGE_CAP_MIN,
    SENIOR_CAP_MIN,
    TODDLER_CAP_MIN,
    cap_for_age,
)
from schemas.intent import Companion, IntentExtraction
from schemas.itinerary import ActivityNode, Hop, Itinerary


# ============================================================
# 1) age_caps.cap_for_age 查表单测
# ============================================================


def test_cap_for_age_toddler():
    assert cap_for_age(0) == (45, "婴幼儿")
    assert cap_for_age(3) == (45, "婴幼儿")
    assert TODDLER_CAP_MIN == 45


def test_cap_for_age_preschool():
    assert cap_for_age(4) == (75, "学龄前")
    assert cap_for_age(6) == (75, "学龄前")
    assert PRESCHOOL_CAP_MIN == 75


def test_cap_for_age_school_age():
    assert cap_for_age(7) == (120, "学童")
    assert cap_for_age(12) == (120, "学童")
    assert SCHOOL_AGE_CAP_MIN == 120


def test_cap_for_age_senior():
    assert cap_for_age(75) == (60, "高龄")
    assert cap_for_age(80) == (60, "高龄")
    assert SENIOR_CAP_MIN == 60


def test_cap_for_age_unbucketed_returns_none():
    """13-74 岁不落任何硬 cap 分桶（60-74 那档是 Phase C grounding 对齐的事，B-2b 不碰）。"""
    assert cap_for_age(13) is None
    assert cap_for_age(50) is None
    assert cap_for_age(74) is None


# ============================================================
# 2) check_age_aware_duration 端到端 characterization（逐字节不变）
# ============================================================


def _make_intent(companions: list[Companion]) -> IntentExtraction:
    return IntentExtraction(
        raw_input="测试",
        social_context="家庭日常",
        companions=companions,
        duration_hours=[3, 4],
        distance_max_km=5.0,
        start_time="14:00",
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        parse_confidence=0.95,
    )


def _itin_with_poi_duration(duration: int) -> Itinerary:
    """构造单 POI 节点行程，用于触发/不触发年龄时长 cap。"""
    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home",
                     start_time="13:50", duration_min=0, title="家"),
        ActivityNode(node_id="n1", kind="看展", target_kind="poi", target_id="P003",
                     start_time="14:00", duration_min=duration, title="测试 POI"),
        ActivityNode(node_id="n2", kind="终点", target_kind="home", target_id="home",
                     start_time="20:00", duration_min=0, title="家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="13:50",
            minutes=10, mode="walking", path_type="real_route"),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="19:50",
            minutes=10, mode="walking", path_type="real_route"),
    ]
    return Itinerary(nodes=nodes, hops=hops, summary="测试", total_minutes=600)


def test_toddler_tier_message_and_range():
    """≤3 岁：cap 45，message 含「婴幼儿 ≤45min」，expected_range=(45,45)。"""
    intent = _make_intent([Companion(role="孩子", age=2)])
    itin = _itin_with_poi_duration(60)
    violations = check_age_aware_duration(itin, intent)
    assert len(violations) == 1
    v = violations[0]
    assert "婴幼儿 ≤45min" in v.message
    assert "2 岁" in v.message
    assert v.expected_range == (45, 45)  # max(45, 45-15)=45


def test_preschool_tier_message_and_range():
    """4-6 岁：cap 75，message 含「学龄前 ≤75min」，expected_range=(60,75)。"""
    intent = _make_intent([Companion(role="孩子", age=5)])
    itin = _itin_with_poi_duration(90)
    violations = check_age_aware_duration(itin, intent)
    assert len(violations) == 1
    v = violations[0]
    assert "学龄前 ≤75min" in v.message
    assert "5 岁" in v.message
    assert v.expected_range == (60, 75)


def test_school_age_tier_message_and_range():
    """7-12 岁：cap 120，message 含「学童 ≤120min」，expected_range=(105,120)。"""
    intent = _make_intent([Companion(role="孩子", age=10)])
    itin = _itin_with_poi_duration(150)
    violations = check_age_aware_duration(itin, intent)
    assert len(violations) == 1
    v = violations[0]
    assert "学童 ≤120min" in v.message
    assert "10 岁" in v.message
    assert v.expected_range == (105, 120)


def test_senior_tier_message_and_range():
    """≥75 岁：cap 60，message 含「高龄 ≤60min」，expected_range=(45,60)。"""
    intent = _make_intent([Companion(role="父母", age=80)])
    itin = _itin_with_poi_duration(90)
    violations = check_age_aware_duration(itin, intent)
    assert len(violations) == 1
    v = violations[0]
    assert "高龄 ≤60min" in v.message
    assert "80 岁" in v.message
    assert v.expected_range == (45, 60)


def test_multi_gen_takes_strictest_reason_only_from_strictest_tier():
    """5 岁娃（cap 75）+ 80 岁老人（cap 60）→ 取 min=60；reason 只含高龄那条（cap==min_cap 过滤）。"""
    intent = _make_intent([Companion(role="孩子", age=5), Companion(role="父母", age=80)])
    itin = _itin_with_poi_duration(70)  # 70 > 60 但 <= 75，只有取最严(60)才命中
    violations = check_age_aware_duration(itin, intent)
    assert len(violations) == 1
    v = violations[0]
    assert v.expected_range == (45, 60)
    assert "高龄 ≤60min" in v.message
    assert "80 岁" in v.message
    # 5 岁的学龄前理由不应混进 message（reason_text 只取 cap==min_cap 那些）
    assert "学龄前" not in v.message


def test_no_age_returns_empty():
    intent = _make_intent([Companion(role="妻子")])
    itin = _itin_with_poi_duration(180)
    assert check_age_aware_duration(itin, intent) == []
