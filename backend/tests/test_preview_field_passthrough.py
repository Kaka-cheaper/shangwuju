"""验 _poi_preview / _restaurant_preview / SearchPoisOutput 字段透传（spec R2）。

测试矩阵：
1. _poi_preview dict 投影：5 岁娃 → kid_3_6 桶（int 单值，不暴露 dict）
2. _poi_preview multi_gen：5 岁 + 78 岁 → 取最严
3. _restaurant_preview 含 typical_dining_min
4. _poi_preview 缺 companions 时降级到 default
5. SearchPoisOutput.effective_distance_max_km 字段存在
6. P040 真 mock 5 岁娃投影（不暴露 dict）
"""

from __future__ import annotations

from collections import namedtuple

from agent.planning.blueprint.blueprint_llm import (
    _poi_preview,
    _restaurant_preview,
    build_candidate_preview,
)
from data.loader import load_pois, load_restaurants
from schemas.domain import Poi, Restaurant, SuggestedDuration
from schemas.tools import SearchPoisOutput

C = namedtuple("C", "age role")


_POI_BASE = {
    "id": "P_TEST",
    "name": "测试亲子博物馆",
    "type": "亲子博物馆",
    "location": {"name": "测试地点"},
    "distance_km": 1.0,
    "opening_hours": "09:00-21:00",
    "rating": 4.5,
    "tags": ["亲子友好"],
    "suitable_for": [],
}

_REST_BASE = {
    "id": "R_TEST",
    "name": "测试餐厅",
    "cuisine": "粤菜",
    "location": {"name": "测试地点"},
    "distance_km": 1.0,
    "opening_hours": "10:00-22:00",
    "avg_price": 100,
    "rating": 4.5,
    "tags": [],
    "suitable_for": [],
}


def test_poi_preview_dict_projects_to_int_for_kid_3_6() -> None:
    """5 岁娃应见到 kid_3_6 桶投影（int 不是 dict）。"""
    p = Poi.model_validate(
        {**_POI_BASE, "suggested_duration_minutes": {"default": 90, "kid_3_6": 60}}
    )
    preview = _poi_preview(p, companions=[C(age=5, role="孩子")])
    assert preview["suggested_duration_minutes"] == 60
    assert isinstance(preview["suggested_duration_minutes"], int)


def test_poi_preview_multi_gen_takes_strictest() -> None:
    """5 岁 + 78 岁多代际 → 取最严（含 ≤6 优先）。"""
    p = Poi.model_validate(
        {
            **_POI_BASE,
            "suggested_duration_minutes": {
                "default": 90,
                "kid_3_6": 45,
                "senior": 60,
                "multi_gen": 60,
            },
        }
    )
    preview = _poi_preview(
        p, companions=[C(age=5, role="孩子"), C(age=78, role="父母")]
    )
    assert preview["suggested_duration_minutes"] == 45  # 含 ≤6 优先取 kid_3_6


def test_restaurant_preview_contains_typical_dining_min() -> None:
    """_restaurant_preview 应含 typical_dining_min 字段。"""
    r = Restaurant.model_validate({**_REST_BASE, "typical_dining_min": 90})
    preview = _restaurant_preview(r)
    assert preview["typical_dining_min"] == 90


def test_poi_preview_falls_back_to_default_without_companions() -> None:
    """缺 companions 时降级到 default 桶。"""
    p = Poi.model_validate(
        {**_POI_BASE, "suggested_duration_minutes": {"default": 90, "kid_3_6": 60}}
    )
    preview = _poi_preview(p, companions=None)
    assert preview["suggested_duration_minutes"] == 90


def test_search_pois_output_has_effective_distance_field() -> None:
    """SearchPoisOutput 加 effective_distance_max_km 字段（spec R2）。"""
    out = SearchPoisOutput(
        success=True,
        candidates=[],
        relaxed_tags=[],
        effective_distance_max_km=7.0,
    )
    assert out.effective_distance_max_km == 7.0
    out2 = SearchPoisOutput(success=True, candidates=[], relaxed_tags=[])
    assert out2.effective_distance_max_km is None


def test_p003_kid_5_real_mock_projects_to_60() -> None:
    """加载真实 mock P003（亲子博物馆 default=90/kid_3_6=60），5 岁娃透传 = 60。

    这是 5 岁娃博物馆 2.5h 反例的"信息源端"——验证 LLM 看到 60 而非 90。
    """
    pois = {p.id: p for p in load_pois()}
    p003 = pois.get("P003")
    assert p003 is not None
    preview = _poi_preview(p003, companions=[C(age=5, role="孩子")])
    assert preview["suggested_duration_minutes"] == 60
    # 关键：不暴露 dict 结构给 LLM
    assert not isinstance(preview["suggested_duration_minutes"], dict)


def test_build_candidate_preview_passes_companions() -> None:
    """build_candidate_preview 把 companions 透传到 _poi_preview。"""
    pois = load_pois()[:3]
    rests = load_restaurants()[:3]
    preview = build_candidate_preview(
        pois,
        rests,
        top_k=3,
        companions=[C(age=5, role="孩子")],
    )
    # 至少其中 1 个 POI 应见到投影后的 int
    int_count = sum(
        1
        for p in preview["pois"]
        if isinstance(p["suggested_duration_minutes"], int)
        or p["suggested_duration_minutes"] is None
    )
    assert int_count == len(preview["pois"]), "preview 不应暴露 dict 结构"


def test_real_mock_restaurant_preview_typical_dining_min() -> None:
    """加载真实 mock 餐厅，preview 应含 typical_dining_min 字段（int 或 None）。"""
    rests = load_restaurants()[:5]
    for r in rests:
        preview = _restaurant_preview(r)
        assert "typical_dining_min" in preview
        # mock 升级后所有 45 个餐厅都应有值（不 None）
        assert preview["typical_dining_min"] is not None
        assert isinstance(preview["typical_dining_min"], int)
