"""营业时间 critic 测试（ADR-0008 B-2b 新增：G3 移植 + None-guard）。

check_opening_hours：POI/餐厅节点排定的 [start_time, start_time+duration_min] 时段
是否完整落在目标 opening_hours 内。判定逻辑（_is_in_business_hours）从死代码
blueprint._opening_hours_critic 逐字节移植而来（见 helpers.py），但作用对象换成
**已 assemble 的 Itinerary**（真实 node.start_time，含 hop 通勤耗时），因此是精确版，
而非 blueprint 那层「不含 hop 耗时」的粗略推算的重复实现。

覆盖：营业内通过 / 营业外触发 HARD / start_time 不可解析跳过（None-guard）/
目标缺失跳过（反幻觉留给 Stage 0 check_tool_consistency）/ 跨日营业放行 /
空 opening_hours 放行。
"""

from __future__ import annotations

from agent.planning.critic._rules.checks import check_opening_hours
from agent.planning.critic._rules.types import Severity, ViolationCode
from agent.planning.critic.context import CriticContext
from schemas.itinerary import ActivityNode, Hop, Itinerary


def _itin_with_poi(poi_id: str, start: str, duration: int) -> Itinerary:
    """构造 [home → poi → home] 最小行程。"""
    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home",
                     start_time="08:00", duration_min=0, title="出发"),
        ActivityNode(node_id="n1", kind="主活动", target_kind="poi", target_id=poi_id,
                     start_time=start, duration_min=duration, title=poi_id),
        ActivityNode(node_id="n2", kind="终点", target_kind="home", target_id="home",
                     start_time="23:00", duration_min=0, title="回家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="08:00",
            minutes=5, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="22:00",
            minutes=5, mode="taxi", path_type="real_route", buffer_min=0),
    ]
    return Itinerary(summary="测试", nodes=nodes, hops=hops, total_minutes=900)


def _itin_with_restaurant(rid: str, start: str, duration: int) -> Itinerary:
    """构造 [home → restaurant → home] 最小行程。"""
    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home",
                     start_time="08:00", duration_min=0, title="出发"),
        ActivityNode(node_id="n1", kind="用餐", target_kind="restaurant", target_id=rid,
                     start_time=start, duration_min=duration, title=rid),
        ActivityNode(node_id="n2", kind="终点", target_kind="home", target_id="home",
                     start_time="23:00", duration_min=0, title="回家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="08:00",
            minutes=5, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="22:00",
            minutes=5, mode="taxi", path_type="real_route", buffer_min=0),
    ]
    return Itinerary(summary="测试", nodes=nodes, hops=hops, total_minutes=900)


def _codes(viols):
    return [v.code for v in viols]


# ---- 营业内通过 ----

def test_within_business_hours_passes():
    """P040 营业 09:30-17:30，14:00-15:00 完整落在内 → 不触发。"""
    itin = _itin_with_poi("P040", "14:00", 60)
    viols = check_opening_hours(itin)
    assert ViolationCode.OPENING_HOURS_VIOLATION not in _codes(viols)


# ---- 营业外触发 HARD ----

def test_outside_business_hours_triggers_hard():
    """P040 营业到 17:30，17:00-18:00 超出打烊 30min → HARD。"""
    itin = _itin_with_poi("P040", "17:00", 60)
    viols = check_opening_hours(itin)
    hits = [v for v in viols if v.code == ViolationCode.OPENING_HOURS_VIOLATION]
    assert hits, f"P040 17:00-18:00 应超出 09:30-17:30 营业时间；实际 {_codes(viols)}"
    assert all(v.severity == Severity.HARD for v in hits)
    assert "target_id" not in hits[0].message  # 不暴露字段名
    assert hits[0].field_path == "nodes[1].start_time"


# ---- start_time 不可解析 → 跳过（None-guard，防重蹈 O4） ----

def test_unparseable_start_time_skips():
    """start_time 非法格式（parse_hhmm 返回 None）→ 跳过，交给 Stage 0 check_time_parseable。"""
    itin = _itin_with_poi("P040", "非法时间", 60)
    viols = check_opening_hours(itin)  # 不应抛异常
    assert ViolationCode.OPENING_HOURS_VIOLATION not in _codes(viols)


# ---- 目标缺失 → 跳过 ----

def test_missing_target_skips():
    """target_id 不在全量 mock 池 → 跳过（幻觉诊断留给 Stage 0 check_tool_consistency，
    这里再报是重复）。"""
    itin = _itin_with_poi("P_NOT_EXIST", "10:00", 60)
    viols = check_opening_hours(itin)
    assert ViolationCode.OPENING_HOURS_VIOLATION not in _codes(viols)


# ---- 跨日营业 → 放行 ----

def test_cross_day_opening_hours_passes():
    """R046 营业 18:00-03:00（跨日：close_t <= open_t）→ 简化放行，任意时段不触发。"""
    itin = _itin_with_restaurant("R046", "20:00", 600)
    viols = check_opening_hours(itin)
    assert ViolationCode.OPENING_HOURS_VIOLATION not in _codes(viols)


# ---- 空 opening_hours → 放行 ----

def test_empty_opening_hours_passes():
    """opening_hours 为空字符串（无约束）→ 放行。"""

    class _FakeTarget:
        def __init__(self) -> None:
            self.id = "FAKE1"
            self.name = "无营业时间约束的测试点"
            self.opening_hours = ""

    itin = _itin_with_poi("FAKE1", "03:00", 60)
    ctx = CriticContext(pois=[_FakeTarget()])
    viols = check_opening_hours(itin, ctx=ctx)
    assert ViolationCode.OPENING_HOURS_VIOLATION not in _codes(viols)
