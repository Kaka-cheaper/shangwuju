"""tests.test_planner_alternatives_reason —— 分界修缮批 任务 4：解释卡拒绝理由
必须与选中项真实比较。

病灶（全后端 LLM/规则分界普查实锤，planner.py:193-197,214-217）：备选
`reason_rejected` 曾用固定阈值（rating<4.7→「评分较低」否则「距离更远」），
从未与选中项比较——评分 4.8 且更近的备选会被标「距离更远」，是确定域里的
假事实断言（解释卡直接展示给用户）。

判据（钉死"理由与两项字段事实一致"）：
- 「评分较低」仅当备选评分低于**全部**同 kind 选中项；
- 「距离更远」仅当备选距离远于**全部**同 kind 选中项；
- 两者都不成立（或该 kind 没有选中项可比）→ 中性措辞（不编造具体维度）。

fixture 风格对齐 test_planner_node_swap.py（自建实体，确定性）。blueprint 只被
`_build_alternatives` 读 `.nodes[].target_id`，用 SimpleNamespace 桩即可。
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.graph.nodes.planner import _build_alternatives
from schemas.domain import Location, Poi, PoiCapacity, Restaurant, RestaurantCapacity


def _poi(poi_id: str, *, rating: float, dist: float) -> Poi:
    return Poi(
        id=poi_id,
        name=f"POI-{poi_id}",
        type="公园",
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=dist,
        opening_hours="08:00-22:00",
        rating=rating,
        age_range=None,
        price_range=None,
        tags=[],
        suitable_for=[],
        suggested_duration_minutes=60,
        capacity=PoiCapacity(daily_quota=100, available_slots=50),
    )


def _rest(rest_id: str, *, rating: float, dist: float) -> Restaurant:
    return Restaurant(
        id=rest_id,
        name=f"REST-{rest_id}",
        cuisine="火锅",
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=dist,
        opening_hours="11:00-23:00",
        avg_price=100.0,
        rating=rating,
        typical_dining_min=60,
        capacity=RestaurantCapacity(),
        tags=[],
        suitable_for=[],
    )


def _blueprint(*selected_ids: str):
    return SimpleNamespace(nodes=[SimpleNamespace(target_id=i) for i in selected_ids])


def _reason_of(alternatives: list[dict], target_id: str) -> str:
    return next(a["reason_rejected"] for a in alternatives if a["target_id"] == target_id)


def test_better_rated_and_closer_alternative_gets_neutral_reason():
    """评分更高（4.8 > 选中 4.6）且更近（2.0 < 3.0）的备选——两个维度的
    断言都不成立，必须用中性措辞，绝不能标「距离更远」（修复前的假断言）。"""
    pois = [_poi("P_SEL", rating=4.6, dist=3.0), _poi("P_ALT", rating=4.8, dist=2.0)]
    rests = [_rest("R_SEL", rating=4.5, dist=4.0)]
    alts = _build_alternatives(_blueprint("P_SEL", "R_SEL"), pois, rests)

    reason = _reason_of(alts, "P_ALT")
    assert "距离更远" not in reason, f"P_ALT 比选中项更近，标「距离更远」是假断言：{reason}"
    assert "评分较低" not in reason, f"P_ALT 比选中项评分更高，标「评分较低」是假断言：{reason}"


def test_lower_rated_alternative_reason_mentions_rating():
    """评分确实低于选中项（4.2 < 4.6）→「评分较低」成立，且带真实数值。"""
    pois = [_poi("P_SEL", rating=4.6, dist=3.0), _poi("P_ALT", rating=4.2, dist=2.0)]
    rests = [_rest("R_SEL", rating=4.5, dist=4.0)]
    alts = _build_alternatives(_blueprint("P_SEL", "R_SEL"), pois, rests)

    reason = _reason_of(alts, "P_ALT")
    assert "评分较低" in reason and "4.2" in reason


def test_farther_alternative_with_high_rating_reason_mentions_distance():
    """评分不低于选中项（4.9 ≥ 4.5）但确实更远（8.0 > 4.0）→「距离更远」成立。"""
    pois = [_poi("P_SEL", rating=4.6, dist=3.0)]
    rests = [_rest("R_SEL", rating=4.5, dist=4.0), _rest("R_FAR", rating=4.9, dist=8.0)]
    alts = _build_alternatives(_blueprint("P_SEL", "R_SEL"), pois, rests)

    reason = _reason_of(alts, "R_FAR")
    assert "距离更远" in reason and "8.0" in reason


def test_no_selected_entity_of_kind_yields_neutral_reason():
    """blueprint 没选任何餐厅（该 kind 无可比对象）→ 不做维度断言，中性措辞。"""
    pois = [_poi("P_SEL", rating=4.6, dist=3.0)]
    rests = [_rest("R_ONLY", rating=4.9, dist=2.0)]
    alts = _build_alternatives(_blueprint("P_SEL"), pois, rests)

    reason = _reason_of(alts, "R_ONLY")
    assert "距离更远" not in reason and "评分较低" not in reason
