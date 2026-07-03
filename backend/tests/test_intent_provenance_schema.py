"""tests.test_intent_provenance_schema —— ADR-0014 决策 1（G-1）schema 单测。

覆盖：
1. field_provenance 四值枚举——合法值放行，非法值拦截。
2. 标量字段级键 + 列表元素级键（键=值本身）共存于同一 dict。
3. Optional 兼容：不传 field_provenance 时默认 None，旧 checkpoint（JSON 里
   压根没有这个键）依然能 model_validate 通过，免迁移。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.intent import IntentExtraction


def _base_kwargs() -> dict:
    return dict(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[],
        physical_constraints=["亲子友好"],
        dietary_constraints=["不辣", "日料"],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="测试",
        parse_confidence=0.8,
    )


# ============================================================
# 1. Optional 兼容
# ============================================================


def test_field_provenance_defaults_to_none_without_migration():
    intent = IntentExtraction(**_base_kwargs())
    assert intent.field_provenance is None
    # model_dump 仍带这个键（值为 None）——前端/下游可安全判空，不必迁移旧数据
    assert "field_provenance" in intent.model_dump()


def test_old_checkpoint_json_without_key_still_validates():
    """模拟旧 checkpoint：整条 JSON 里压根没有 field_provenance 键（extra=forbid
    只拦截多余键，不要求已知 Optional 键必须出现）。"""
    payload = _base_kwargs()
    assert "field_provenance" not in payload
    intent = IntentExtraction.model_validate(payload)
    assert intent.field_provenance is None


# ============================================================
# 2. 四值枚举 + 标量/元素级键
# ============================================================


def test_field_provenance_accepts_four_values_scalar_and_list_element_keys():
    intent = IntentExtraction(
        **_base_kwargs(),
        field_provenance={
            "distance_max_km": "default",
            "social_context": "inferred",
            "dietary_constraints:不辣": "user_stated",
            "dietary_constraints:日料": "prior",
        },
    )
    assert intent.field_provenance["distance_max_km"] == "default"
    assert intent.field_provenance["social_context"] == "inferred"
    # 键=值本身：同一 dietary 列表里两个元素各自独立标注，字段级标签盖不住这种场景
    assert intent.field_provenance["dietary_constraints:不辣"] == "user_stated"
    assert intent.field_provenance["dietary_constraints:日料"] == "prior"


@pytest.mark.parametrize("value", ["user_stated", "inferred", "prior", "default"])
def test_field_provenance_four_enum_values_all_valid(value: str):
    intent = IntentExtraction(
        **_base_kwargs(), field_provenance={"distance_max_km": value}
    )
    assert intent.field_provenance["distance_max_km"] == value


def test_field_provenance_rejects_invalid_enum_value():
    with pytest.raises(ValidationError):
        IntentExtraction(
            **_base_kwargs(),
            field_provenance={"distance_max_km": "guessed"},
        )


def test_field_provenance_extra_forbid_still_rejects_unknown_top_level_field():
    """出处新增字段不应松动既有 D9 extra=forbid 防线。"""
    payload = _base_kwargs()
    payload["scene_type"] = "family"
    with pytest.raises(ValidationError):
        IntentExtraction.model_validate(payload)
