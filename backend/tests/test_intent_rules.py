"""tests.test_intent_rules —— rule 模式纯算法意图解析单元测试。

覆盖 8 个 demo 场景输入文案 + 边界 / 反例。验证：
- 关键字段抽取正确（distance / companions / social_context / tags）
- schema 校验通过（不抛异常）
- D9 反向校验：产出不含 scene_type / relation_type 等禁止字段
"""

from __future__ import annotations

import pytest

from api._streams.intent_rules import parse_intent_via_rules


# ============================================================
# 8 demo 场景输入（覆盖主要 social_context）
# ============================================================


@pytest.mark.parametrize(
    "scenario_id,message,expect_social,expect_dist_min,expect_dist_max,must_have_role",
    [
        # S1 学生党 KTV 局
        (
            "S1",
            "周五晚上和室友 4 个人想去 K 歌，预算别太贵",
            "朋友热闹",
            None,
            None,
            "室友",
        ),
        # S2 兄弟撸串夜宵
        (
            "S2",
            "今晚和兄弟出来撸串喝点酒，人均 50 左右就行",
            "朋友热闹",
            None,
            None,
            "朋友",
        ),
        # S3 家庭主线
        (
            "S3",
            "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
            "家庭日常",
            None,
            5.0,
            "妻子",
        ),
        # S4 朋友 4 人
        (
            "S4",
            "今天下午想和朋友出去玩几小时，4 个人 2 男 2 女，别离家太远。",
            "朋友热闹",
            None,
            None,
            "朋友",
        ),
        # S5 情侣看展
        (
            "S5",
            "周日下午带着女朋友去看个展，顺便找个安静能聊天的地方吃饭。",
            "情侣亲密",
            None,
            None,
            "女朋友",
        ),
        # S6 闺蜜下午茶
        (
            "S6",
            "周末下午约了闺蜜想找个网红的地方拍拍照吃个下午茶。",
            "闺蜜聊天",
            None,
            None,
            "闺蜜",
        ),
        # S7 商务接待
        (
            "S7",
            "下午临时被叫去接个外地客户，对方是商务人士，帮我安排下。",
            "商务接待",
            None,
            None,
            "客户",
        ),
        # S8 独处放空
        (
            "S8",
            "这周加班加得想吐，下午想一个人安安静静待几个小时再回家。",
            "独处放空",
            None,
            None,
            None,  # 一个人 → companions 为空
        ),
    ],
)
def test_demo_scenarios(
    scenario_id: str,
    message: str,
    expect_social: str,
    expect_dist_min: float | None,
    expect_dist_max: float | None,
    must_have_role: str | None,
):
    intent = parse_intent_via_rules(message)
    assert intent.raw_input == message
    assert intent.social_context == expect_social, (
        f"{scenario_id}: 期待 social_context={expect_social}, 实际 {intent.social_context}"
    )
    if expect_dist_min is not None:
        assert intent.distance_max_km >= expect_dist_min, scenario_id
    if expect_dist_max is not None:
        assert intent.distance_max_km <= expect_dist_max, scenario_id
    if must_have_role is None:
        # 一个人场景：companions 应为空
        assert intent.companions == [], (
            f"{scenario_id}: 一个人场景 companions 应为空，实际 {intent.companions}"
        )
    else:
        roles = {c.role for c in intent.companions}
        assert must_have_role in roles, (
            f"{scenario_id}: 期待包含 role={must_have_role}, 实际 {roles}"
        )


# ============================================================
# 距离抽取边界
# ============================================================


def test_distance_explicit_3km():
    intent = parse_intent_via_rules("周日下午想带外公外婆出去走走，3 公里以内")
    assert intent.distance_max_km == 3.0


def test_distance_too_far_keyword():
    intent = parse_intent_via_rules("太远了换近一点的")
    assert intent.distance_max_km == 3.0


def test_distance_default():
    intent = parse_intent_via_rules("出去玩")
    assert intent.distance_max_km == 5.0


# ============================================================
# 时长抽取
# ============================================================


def test_duration_explicit():
    intent = parse_intent_via_rules("我想出去玩 3 小时")
    assert intent.duration_hours[0] == 3
    assert intent.duration_hours[1] >= 4


def test_duration_keyword_half_day():
    intent = parse_intent_via_rules("约个朋友半天")
    assert intent.duration_hours == [3, 5]


# ============================================================
# 同行人抽取
# ============================================================


def test_companions_alone():
    intent = parse_intent_via_rules("我一个人静静待几小时")
    assert intent.companions == []
    assert intent.social_context == "独处放空"


def test_companions_kid_with_age():
    intent = parse_intent_via_rules("和孩子出去玩，孩子 5 岁")
    kid = next((c for c in intent.companions if c.role == "孩子"), None)
    assert kid is not None
    assert kid.age == 5


def test_companions_n_friends():
    intent = parse_intent_via_rules("和朋友 6 个人出去")
    friend = next((c for c in intent.companions if c.role == "朋友"), None)
    assert friend is not None
    assert friend.count == 6
    # capacity_requirement 应推导出
    assert intent.capacity_requirement is not None
    assert intent.capacity_requirement >= 4


# ============================================================
# tag 抽取
# ============================================================


def test_dietary_synonyms():
    intent = parse_intent_via_rules("老婆减肥，找清淡的")
    assert "低脂" in intent.dietary_constraints  # 减肥 → 低脂
    assert "不辣" in intent.dietary_constraints  # 清淡 → 不辣


def test_physical_kid():
    intent = parse_intent_via_rules("带宝宝出去玩")
    assert "亲子友好" in intent.physical_constraints


def test_experience_photo():
    intent = parse_intent_via_rules("找个网红的地方拍照")
    assert "拍照友好" in intent.experience_tags
    assert "网红打卡" in intent.experience_tags


# ============================================================
# D9 反向校验：禁止字段不应出现
# ============================================================


def test_no_d9_forbidden_fields():
    """产出 dict 不应含 scene_type / relation_type / is_family 等被禁字段。"""
    intent = parse_intent_via_rules("家庭出游 5 岁孩子")
    data = intent.model_dump()
    forbidden = {"scene_type", "relation_type", "is_family", "is_friends"}
    assert not (forbidden & set(data.keys())), (
        f"产出包含 D9 禁止字段：{forbidden & set(data.keys())}"
    )


# ============================================================
# 边界
# ============================================================


def test_empty_input():
    intent = parse_intent_via_rules("")
    assert intent.raw_input == ""
    assert intent.parse_confidence < 0.5
    assert "empty_input_fallback" in intent.ambiguous_fields


def test_all_default_fields_set():
    """所有必填字段都应有合法值（schema 不抛异常）。"""
    intent = parse_intent_via_rules("随便出去走走")
    # Pydantic 已校验；这里只验关键值
    assert intent.start_time
    assert isinstance(intent.duration_hours, list) and len(intent.duration_hours) == 2
    assert intent.distance_max_km > 0
    assert intent.social_context in (
        "家庭日常",
        "老人伴助",
        "闺蜜聊天",
        "朋友热闹",
        "情侣亲密",
        "商务接待",
        "同学重聚",
        "独处放空",
        "纪念日仪式感",
    )
