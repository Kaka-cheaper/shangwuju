"""tests.test_search_adapter_cuisine_rerank —— 块B-2（R2）cuisine 重排单测。

验证：用户明示 preferred_poi_types（如「烧烤」）时，编排层把 cuisine 命中的餐厅
候选稳定前置，避免被高评分但不对味的候选挤出 blueprint top-k 预览。

不验证真 LLM——只验纯函数 _rerank_by_preferred_cuisine 的排序行为。
"""

from __future__ import annotations

from agent.runtime.tools.search_adapter import _rerank_by_preferred_cuisine
from schemas.domain import Location, Restaurant, RestaurantCapacity


def _make_rest(rid: str, cuisine: str, rating: float) -> Restaurant:
    return Restaurant(
        id=rid,
        name=f"{cuisine}店{rid}",
        cuisine=cuisine,
        location=Location(name="x", lat=30.25, lng=120.16),
        distance_km=2.0,
        opening_hours="11:00-22:00",
        avg_price=60,
        rating=rating,
        capacity=RestaurantCapacity(two=True, four=True, six=True, eight=False, private_room=False),
        reservation_slots=[],
        tags=[],
        suitable_for=["朋友热闹"],
        signature_dishes=[],
        reviews=[],
    )


def test_rerank_brings_matching_cuisine_to_front() -> None:
    """preferred=['烧烤'] → 烧烤候选前置，火锅/日料退后，原相对序保留。"""
    rests = [
        _make_rest("R1", "火锅", 4.7),
        _make_rest("R2", "日料", 4.6),
        _make_rest("R3", "烧烤", 4.4),
    ]
    out = _rerank_by_preferred_cuisine(rests, ["烧烤"])
    assert out[0].cuisine == "烧烤", "烧烤候选应被提到首位"
    assert [r.id for r in out] == ["R3", "R1", "R2"], "命中前置 + 其余稳定保序"


def test_rerank_no_preference_keeps_order() -> None:
    """无 preferred_poi_types → 原序不动。"""
    rests = [_make_rest("R1", "火锅", 4.7), _make_rest("R2", "烧烤", 4.4)]
    out = _rerank_by_preferred_cuisine(rests, [])
    assert [r.id for r in out] == ["R1", "R2"]


def test_rerank_substring_bidirectional() -> None:
    """双向 substring：preferred=['串'] 命中 cuisine='串串'。"""
    rests = [_make_rest("R1", "火锅", 4.7), _make_rest("R2", "串串", 4.5)]
    out = _rerank_by_preferred_cuisine(rests, ["串"])
    assert out[0].id == "R2"


def test_rerank_no_match_keeps_order() -> None:
    """preferred 词无任何 cuisine 命中 → 原序返回。"""
    rests = [_make_rest("R1", "火锅", 4.7), _make_rest("R2", "日料", 4.5)]
    out = _rerank_by_preferred_cuisine(rests, ["烧烤"])
    assert [r.id for r in out] == ["R1", "R2"]
