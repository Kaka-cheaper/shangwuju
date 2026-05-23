"""验 SuggestedDuration / typical_dining_min Pydantic Union 双兼容（spec R1）。

测试矩阵：
- Poi.suggested_duration_minutes 接受 int / dict / None 三种形态
- Restaurant.typical_dining_min 字段存在且可加载
- Persona.default_pace_profile 可加载
- get_duration_for_companions helper 投影正确
"""

from __future__ import annotations

from collections import namedtuple

from schemas.domain import Poi, Restaurant, SuggestedDuration
from schemas.persona import PaceProfile, Persona, PersonaDefaultTags
from utils.duration_helpers import get_duration_for_companions

_POI_BASE = {
    "id": "P_TEST",
    "name": "测试 POI",
    "type": "亲子博物馆",
    "location": {"name": "测试地点"},
    "distance_km": 1.0,
    "opening_hours": "09:00-21:00",
    "rating": 4.5,
}

_REST_BASE = {
    "id": "R_TEST",
    "name": "测试餐厅",
    "cuisine": "健康轻食",
    "location": {"name": "测试地点"},
    "distance_km": 1.0,
    "opening_hours": "10:00-22:00",
    "avg_price": 100,
    "rating": 4.5,
}


def test_poi_accepts_int_legacy() -> None:
    """旧形态 int 可 model_validate（向后兼容）。"""
    p = Poi.model_validate({**_POI_BASE, "suggested_duration_minutes": 90})
    assert p.suggested_duration_minutes == 90


def test_poi_accepts_dict_new() -> None:
    """新形态 dict 可 model_validate（升级形态）。"""
    p = Poi.model_validate(
        {
            **_POI_BASE,
            "suggested_duration_minutes": {"default": 90, "kid_3_6": 60},
        }
    )
    assert isinstance(p.suggested_duration_minutes, SuggestedDuration)
    assert p.suggested_duration_minutes.default == 90
    assert p.suggested_duration_minutes.kid_3_6 == 60


def test_poi_accepts_none() -> None:
    """None 形态依然可加载（默认值）。"""
    p = Poi.model_validate(_POI_BASE)
    assert p.suggested_duration_minutes is None


def test_restaurant_accepts_typical_dining_min() -> None:
    """Restaurant 加 typical_dining_min 后可加载。"""
    r = Restaurant.model_validate({**_REST_BASE, "typical_dining_min": 60})
    assert r.typical_dining_min == 60


def test_restaurant_accepts_none_typical_dining_min() -> None:
    """typical_dining_min 是可选字段。"""
    r = Restaurant.model_validate(_REST_BASE)
    assert r.typical_dining_min is None


def test_persona_accepts_pace_profile() -> None:
    """Persona 加 default_pace_profile 字段后可加载。"""
    p = Persona.model_validate(
        {
            "user_id": "u_test",
            "label": "测试",
            "icon": "🧪",
            "notes": "测试 persona",
            "default_pace_profile": {
                "single_session_max_min": 75,
                "break_every_min": 45,
            },
        }
    )
    assert p.default_pace_profile is not None
    assert p.default_pace_profile.single_session_max_min == 75


def test_persona_accepts_none_pace_profile() -> None:
    """default_pace_profile 是可选字段。"""
    p = Persona.model_validate(
        {
            "user_id": "u_test",
            "label": "测试",
            "icon": "🧪",
            "notes": "测试 persona",
        }
    )
    assert p.default_pace_profile is None


# ============================================================
# duration_helpers 投影测试
# ============================================================

C = namedtuple("C", "age role")


def test_helper_int_passthrough() -> None:
    """int 形态原样返回。"""
    assert get_duration_for_companions(90, [C(age=5, role="孩子")]) == 90


def test_helper_none_returns_none() -> None:
    """None 形态返回 None。"""
    assert get_duration_for_companions(None, []) is None


def test_helper_dict_kid_3_6_dominant() -> None:
    """5 岁娃 → 走 kid_3_6 桶。"""
    sd = SuggestedDuration(default=90, kid_3_6=60, kid_7_12=75)
    assert get_duration_for_companions(sd, [C(age=5, role="孩子")]) == 60


def test_helper_dict_kid_7_12_dominant() -> None:
    """10 岁娃 → 走 kid_7_12 桶。"""
    sd = SuggestedDuration(default=90, kid_3_6=60, kid_7_12=75)
    assert get_duration_for_companions(sd, [C(age=10, role="孩子")]) == 75


def test_helper_dict_senior_dominant() -> None:
    """75 岁老人 → 走 senior 桶。"""
    sd = SuggestedDuration(default=90, senior=60)
    assert get_duration_for_companions(sd, [C(age=78, role="父母")]) == 60


def test_helper_dict_multi_gen_takes_strictest() -> None:
    """5 岁娃 + 78 岁老人 → 含 ≤6 优先（最严约束）。"""
    sd = SuggestedDuration(default=90, kid_3_6=45, senior=60, multi_gen=60)
    # 含 ≤6 岁优先取 kid_3_6
    assert (
        get_duration_for_companions(
            sd, [C(age=5, role="孩子"), C(age=78, role="父母")]
        )
        == 45
    )


def test_helper_dict_multi_gen_when_no_kid_3_6() -> None:
    """无 ≤6 岁孩 + 多代际（10 岁孩 + 老人）→ 走 multi_gen 桶。"""
    sd = SuggestedDuration(default=90, multi_gen=60)
    # kid_7_12 桶未填，回退到 multi_gen
    assert (
        get_duration_for_companions(
            sd, [C(age=10, role="孩子"), C(age=78, role="父母")]
        )
        == 60
    )


def test_helper_dict_falls_back_to_default() -> None:
    """无匹配桶 → 降级到 default。"""
    sd = SuggestedDuration(default=90)
    assert (
        get_duration_for_companions(sd, [C(age=30, role="妻子")]) == 90
    )


def test_helper_dict_empty_companions_returns_default() -> None:
    """companions 为空 → default。"""
    sd = SuggestedDuration(default=90, kid_3_6=60)
    assert get_duration_for_companions(sd, []) == 90


def test_p003_kid_5_real_mock() -> None:
    """加载真实 mock P003（亲子博物馆 default=90/kid_3_6=60），5 岁娃应投 60。"""
    from data.loader import load_pois

    pois = {p.id: p for p in load_pois()}
    p003 = pois.get("P003")
    assert p003 is not None
    assert isinstance(p003.suggested_duration_minutes, SuggestedDuration)
    assert (
        get_duration_for_companions(
            p003.suggested_duration_minutes, [C(age=5, role="孩子")]
        )
        == 60
    )


def test_persona_u_dad_pace_profile() -> None:
    """加载真实 u_dad persona（5 岁孩家庭），pace_profile 单段 ≤ 75。"""
    from data.memory_store import get_persona

    p = get_persona("u_dad")
    assert p is not None
    assert p.default_pace_profile is not None
    assert p.default_pace_profile.single_session_max_min == 75
