"""tests.test_search_adapter_poi_rerank —— spec narration-and-intent-fidelity R3 单测。

验证：用户明示活动诉求（preferred_poi_types，如「看展」「KTV」）时，编排层把 POI
type/name/tags 词法命中的候选稳定前置，避免被高 rating 但不对味的候选挤出
blueprint top-k 预览。

不验证真 LLM——只验纯函数 poi_desire_match / _rerank_by_preferred_poi_types。
与餐厅侧 test_search_adapter_cuisine_rerank.py 对称。
"""

from __future__ import annotations

from agent.runtime.tools.search_adapter import (
    _rerank_by_preferred_poi_types,
    poi_desire_match,
)
from schemas.domain import Location, Poi


def _make_poi(
    pid: str,
    poi_type: str,
    rating: float,
    *,
    name: str | None = None,
    tags: list[str] | None = None,
) -> Poi:
    return Poi(
        id=pid,
        name=name or f"{poi_type}{pid}",
        type=poi_type,
        location=Location(name="x", lat=30.25, lng=120.16),
        distance_km=3.0,
        opening_hours="10:00-20:00",
        rating=rating,
        tags=tags or [],
        suitable_for=["情侣亲密"],
    )


# ============================================================
# poi_desire_match 词法判定（Task3 共享 helper）
# ============================================================


def test_match_kanzhan_hits_tags() -> None:
    """「看展」命中 tags 含「看展」的 POI（P002 真实场景）。"""
    assert poi_desire_match("看展", "展览", "西溪艺术展中心", ["看展", "安静聊天"])


def test_match_zhanlan_hits_type() -> None:
    """「展览」与 type='展览' substring 命中。"""
    assert poi_desire_match("展览", "展览", "某展馆", [])


def test_match_pangyan_hits_name() -> None:
    """「攀岩」命中 name='Vertical 攀岩馆'（type=室内运动馆 不含攀岩，靠 name 兜住）。"""
    assert poi_desire_match("攀岩", "室内运动馆", "Vertical 攀岩馆", [])


def test_match_ktv_exact_type() -> None:
    assert poi_desire_match("KTV", "KTV", "麦霸欢唱KTV", [])


def test_no_match_unrelated() -> None:
    """无关诉求词不命中。"""
    assert not poi_desire_match("看展", "猫咖", "毛球先生猫咖", ["拍照友好", "热闹"])


def test_empty_desire_no_match() -> None:
    assert not poi_desire_match("", "展览", "某馆", ["看展"])
    assert not poi_desire_match("   ", "展览", "某馆", ["看展"])


# ============================================================
# _rerank_by_preferred_poi_types 排序（Task4）
# ============================================================


def test_rerank_brings_matching_poi_to_front() -> None:
    """preferred=['看展'] → 展馆候选前置，高分猫咖/甜品退后，原相对序保留。"""
    pois = [
        _make_poi("P022", "猫咖", 4.8, name="毛球先生猫咖", tags=["拍照友好"]),
        _make_poi("P012", "咖啡馆", 4.7, name="花漾咖啡", tags=["网红打卡"]),
        _make_poi("P002", "展览", 4.4, name="西溪艺术展中心", tags=["看展", "安静聊天"]),
    ]
    out = _rerank_by_preferred_poi_types(pois, ["看展"])
    assert out[0].id == "P002", "展馆应被提到首位"
    assert [p.id for p in out] == ["P002", "P022", "P012"], "命中前置 + 其余稳定保序"


def test_rerank_no_preference_keeps_order() -> None:
    """无 preferred_poi_types → 原序不动（零回归）。"""
    pois = [_make_poi("P022", "猫咖", 4.8), _make_poi("P002", "展览", 4.4, tags=["看展"])]
    out = _rerank_by_preferred_poi_types(pois, [])
    assert [p.id for p in out] == ["P022", "P002"]


def test_rerank_no_match_keeps_order() -> None:
    """preferred 词无任何 POI 命中 → 原序返回。"""
    pois = [_make_poi("P022", "猫咖", 4.8), _make_poi("P012", "咖啡馆", 4.7)]
    out = _rerank_by_preferred_poi_types(pois, ["密室"])
    assert [p.id for p in out] == ["P022", "P012"]


def test_rerank_ktv_front() -> None:
    """preferred=['KTV'] → type=KTV 前置。"""
    pois = [
        _make_poi("P022", "猫咖", 4.8),
        _make_poi("P026", "KTV", 4.3, name="麦霸欢唱KTV"),
    ]
    out = _rerank_by_preferred_poi_types(pois, ["KTV"])
    assert out[0].id == "P026"


def test_rerank_blank_prefs_keeps_order() -> None:
    """preferred 全是空白字符串 → 原序返回。"""
    pois = [_make_poi("P022", "猫咖", 4.8), _make_poi("P002", "展览", 4.4, tags=["看展"])]
    out = _rerank_by_preferred_poi_types(pois, ["", "  "])
    assert [p.id for p in out] == ["P022", "P002"]
