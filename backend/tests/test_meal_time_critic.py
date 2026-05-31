"""用餐时段合理性 critic 测试（spec planning-pipeline-consolidation Task 1 / R1）。

check_meal_time：正餐节点 start_time 必须落午餐/晚餐/夜宵窗口；茶点类不约束。
"""

from __future__ import annotations

from agent.planning.critic._rules.checks import check_meal_time
from agent.planning.critic._rules.types import ViolationCode
from schemas.itinerary import ActivityNode, Hop, Itinerary


def _itin_with_restaurant(restaurant_id: str, meal_start: str) -> Itinerary:
    """构造 [home → restaurant → home] 最小行程，餐厅节点在 meal_start 开始。"""
    nodes = [
        ActivityNode(
            node_id="n0", kind="起点", target_kind="home", target_id="home",
            start_time="14:00", duration_min=0, title="出发",
        ),
        ActivityNode(
            node_id="n1", kind="用餐", target_kind="restaurant", target_id=restaurant_id,
            start_time=meal_start, duration_min=60, title=restaurant_id,
        ),
        ActivityNode(
            node_id="n2", kind="终点", target_kind="home", target_id="home",
            start_time="20:00", duration_min=0, title="回家",
        ),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="14:00",
            minutes=5, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="19:30",
            minutes=5, mode="taxi", path_type="real_route", buffer_min=0),
    ]
    return Itinerary(summary="测试", nodes=nodes, hops=hops, total_minutes=360)


def _codes(viols):
    return [v.code for v in viols]


# ---- 正餐排非饭点 → 触发 ----

def test_dinner_at_1405_triggers():
    """正餐（烧烤 R046）排 14:05 非饭点 → MEAL_TIME_UNREASONABLE（S4 实测 bug）。"""
    itin = _itin_with_restaurant("R046", "14:05")
    viols = check_meal_time(itin)
    assert ViolationCode.MEAL_TIME_UNREASONABLE in _codes(viols)


def test_hotpot_at_1500_triggers():
    """火锅（R034）排 15:00 非饭点 → 触发。"""
    itin = _itin_with_restaurant("R034", "15:00")
    viols = check_meal_time(itin)
    assert ViolationCode.MEAL_TIME_UNREASONABLE in _codes(viols)


# ---- 正餐排饭点 → 不触发 ----

def test_dinner_at_1730_ok():
    """正餐排 17:30 晚餐窗口 → 不触发。"""
    itin = _itin_with_restaurant("R046", "17:30")
    viols = check_meal_time(itin)
    assert ViolationCode.MEAL_TIME_UNREASONABLE not in _codes(viols)


def test_lunch_at_1200_ok():
    """正餐排 12:00 午餐窗口 → 不触发。"""
    itin = _itin_with_restaurant("R034", "12:00")
    viols = check_meal_time(itin)
    assert ViolationCode.MEAL_TIME_UNREASONABLE not in _codes(viols)


def test_supper_at_2130_ok():
    """烧烤排 21:30 夜宵窗口 → 不触发（S2 撸串夜宵场景）。"""
    itin = _itin_with_restaurant("R046", "21:30")
    viols = check_meal_time(itin)
    assert ViolationCode.MEAL_TIME_UNREASONABLE not in _codes(viols)


# ---- 茶点类不约束时段 ----

def test_afternoon_tea_at_1500_ok():
    """下午茶（R004）排 15:00 → 茶点类不约束，不触发。"""
    itin = _itin_with_restaurant("R004", "15:00")
    viols = check_meal_time(itin)
    assert ViolationCode.MEAL_TIME_UNREASONABLE not in _codes(viols)
