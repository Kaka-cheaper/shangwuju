"""tests.test_distance_seam —— c′批 任务一：距离解析收口单一接缝的单测 + 回归。

背景（诊断已实证）：execute 阶段 worker（`agent/runtime/tools/search_adapter.py`）
总是把真实 home 坐标传给 `search_pois`/`search_restaurants` Tool，Tool 收到坐标
就转发到 `data.nearby_provider.MockNearbyProvider` 对真实经纬度跑 haversine；
ILS 兜底 planner（`agent/planning/planners/ils_planner.py::_query_pois`/
`_query_restaurants`）历史上不传坐标，落到 Tool 回退分支直接读 mock 数据自带的
`distance_km`（authored）字段。当前杭州 mock 集是虚构密集小城——authored 字段
被刻意收窄到 ~5km 叙事半径，但坐标取自真实杭州西湖景区，haversine 真实距离
经常达到 8～16km，两个口径系统性不一致，同一个 intent 在两条路径下召回结果
不同（S7 商务场景 execute 召回 0/0、ILS 召回 5/6）。

修复：`data.nearby_provider.venue_distance_km` 收口成单一函数，按
`data.loader.dataset_distance_mode()` 的显式声明分派（"authored" 读 mock
distance_km 字段 / "coords" 对真实坐标跑 haversine，为未来望京真实数据集预留）；
execute 侧（`MockNearbyProvider`）与 ILS 侧（`_query_pois`/`_query_restaurants`
新增传 home 坐标）都改调这一个函数。

本文件覆盖：
1. `venue_distance_km` 纯函数单测（authored / coords / coords 缺坐标兜底）。
2. `dataset_distance_mode()` 默认值 + 非法值兜底。
3. 同一个 search_pois/search_restaurants Tool 调用，传 vs 不传 home 坐标——
   authored 模式下候选与 distance_km 必须完全一致（接缝生效的直接证据）。
4. S6 商务接待场景级回归：execute 阶段（`search_pois_for_intent`/
   `search_restaurants_for_intent`，真实 home 坐标）召回不再是空集，且与
   ILS 阶段（`_query_pois`/`_query_restaurants`）对同一实体的 distance_km
   读数一致。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.core.trace import Tracer  # noqa: E402
from agent.planning.planners.ils_planner import _query_pois, _query_restaurants  # noqa: E402
from agent.runtime.tools.search_adapter import (  # noqa: E402
    search_pois_for_intent,
    search_restaurants_for_intent,
)
from data.loader import dataset_distance_mode, load_user_profile  # noqa: E402
from data.nearby_provider import haversine_km, venue_distance_km  # noqa: E402
from schemas.domain import Location, Poi, PoiCapacity, Restaurant, RestaurantCapacity  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402
from schemas.tools import SearchPoisOutput, SearchRestaurantsOutput  # noqa: E402
from tools.registry import invoke_tool  # noqa: E402


# ============================================================
# fixture helpers（合成实体，控制 lat/lng）
# ============================================================


def _poi(*, poi_id: str = "PZ1", dist: float, lat: float | None, lng: float | None) -> Poi:
    return Poi(
        id=poi_id,
        name=f"POI-{poi_id}",
        type="公园",
        location=Location(name="测试地", lat=lat, lng=lng),
        distance_km=dist,
        opening_hours="08:00-22:00",
        rating=4.5,
        tags=[],
        suitable_for=[],
        capacity=PoiCapacity(),
    )


def _rest(*, rest_id: str = "RZ1", dist: float, lat: float | None, lng: float | None) -> Restaurant:
    return Restaurant(
        id=rest_id,
        name=f"REST-{rest_id}",
        cuisine="火锅",
        location=Location(name="测试地", lat=lat, lng=lng),
        distance_km=dist,
        opening_hours="11:00-23:00",
        avg_price=100.0,
        rating=4.3,
        capacity=RestaurantCapacity(),
        tags=[],
        suitable_for=[],
    )


def _business_intent() -> IntentExtraction:
    """S6 商务接待场景（同 test_8_scenarios.py 的 S6 定义，本文件不跨文件耦合，
    在此按同样字段值自建一份，避免依赖另一测试模块的内部字典）。"""
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[Companion(role="商务客户", count=1, is_special_role=True)],
        physical_constraints=[],
        dietary_constraints=["高人均", "有包间"],
        experience_tags=["商务体面", "礼仪感"],
        social_context="商务接待",
        raw_input="下午临时被叫去接个外地客户，对方是商务人士，帮我安排下。",
        parse_confidence=0.82,
    )


# ============================================================
# 1. venue_distance_km 纯函数单测
# ============================================================


def test_dataset_distance_mode_defaults_authored():
    """未设 env 时默认 authored（当前杭州集的语义）。"""
    assert dataset_distance_mode() == "authored"


def test_dataset_distance_mode_invalid_env_falls_back_to_authored(monkeypatch):
    monkeypatch.setenv("SHANGWUJU_DISTANCE_MODE", "not_a_real_mode")
    assert dataset_distance_mode() == "authored"


def test_venue_distance_km_authored_mode_returns_mock_field_ignoring_coords():
    """authored 模式（默认）：无论坐标是什么，返回值恒等于 venue.distance_km。"""
    poi = _poi(dist=4.2, lat=30.285, lng=120.083)
    # home 坐标故意给一个与 poi 坐标相距很远的点——authored 模式下不应影响结果。
    assert venue_distance_km(poi, 0.0, 0.0) == 4.2
    assert venue_distance_km(poi, 30.275, 120.075) == 4.2


def test_venue_distance_km_coords_mode_uses_haversine(monkeypatch):
    """coords 模式（望京真实数据集声明）：改用 haversine(home, venue.location)，
    不再读 authored distance_km 字段。"""
    monkeypatch.setenv("SHANGWUJU_DISTANCE_MODE", "coords")
    home_lat, home_lng = 30.275, 120.075
    poi = _poi(dist=999.0, lat=30.285, lng=120.083)  # authored 值故意设成不可能对上
    expected = round(haversine_km(home_lat, home_lng, 30.285, 120.083), 2)
    got = venue_distance_km(poi, home_lat, home_lng)
    assert got == expected
    assert got != 999.0


def test_venue_distance_km_coords_mode_falls_back_when_coords_missing(monkeypatch):
    """coords 模式下若 venue 本身缺坐标（防御性；authored 集允许，coords 集
    理论不该发生）——退回 distance_km，不因为一条坐标缺失就把候选丢掉。"""
    monkeypatch.setenv("SHANGWUJU_DISTANCE_MODE", "coords")
    poi = _poi(dist=3.5, lat=None, lng=None)
    assert venue_distance_km(poi, 30.275, 120.075) == 3.5


def test_venue_distance_km_dispatches_same_way_for_restaurant():
    """Restaurant 与 Poi 走同一套分派逻辑（同一个函数，同一份声明）。"""
    rest = _rest(dist=2.1, lat=30.273, lng=120.08)
    assert venue_distance_km(rest, 30.275, 120.075) == 2.1


# ============================================================
# 2. 同一 Tool 调用，传 vs 不传 home 坐标——authored 模式下必须完全一致
#    （接缝生效的直接证据：坐标存在与否不再决定读哪份距离真相）
# ============================================================


def test_search_pois_tool_same_candidates_with_or_without_home_coords():
    home = load_user_profile().home_location
    base_args = {"distance_max_km": 5.0, "limit": 20}
    out_no_coords = invoke_tool("search_pois", dict(base_args))
    out_with_coords = invoke_tool(
        "search_pois", {**base_args, "user_lat": home.lat, "user_lng": home.lng}
    )
    assert out_no_coords.success and out_with_coords.success

    a = SearchPoisOutput.model_validate(out_no_coords.output)
    b = SearchPoisOutput.model_validate(out_with_coords.output)
    ids_a = sorted(c.id for c in a.candidates)
    ids_b = sorted(c.id for c in b.candidates)
    assert ids_a == ids_b, "authored 模式下传/不传坐标应召回完全相同的候选集合"

    dist_a = {c.id: c.distance_km for c in a.candidates}
    dist_b = {c.id: c.distance_km for c in b.candidates}
    assert dist_a == dist_b, "authored 模式下同一实体两侧 distance_km 必须一致"


def test_search_restaurants_tool_same_candidates_with_or_without_home_coords():
    home = load_user_profile().home_location
    base_args = {"distance_max_km": 5.0, "limit": 20}
    out_no_coords = invoke_tool("search_restaurants", dict(base_args))
    out_with_coords = invoke_tool(
        "search_restaurants", {**base_args, "user_lat": home.lat, "user_lng": home.lng}
    )
    assert out_no_coords.success and out_with_coords.success

    a = SearchRestaurantsOutput.model_validate(out_no_coords.output)
    b = SearchRestaurantsOutput.model_validate(out_with_coords.output)
    ids_a = sorted(c.id for c in a.candidates)
    ids_b = sorted(c.id for c in b.candidates)
    assert ids_a == ids_b, "authored 模式下传/不传坐标应召回完全相同的候选集合"

    dist_a = {c.id: c.distance_km for c in a.candidates}
    dist_b = {c.id: c.distance_km for c in b.candidates}
    assert dist_a == dist_b, "authored 模式下同一实体两侧 distance_km 必须一致"


# ============================================================
# 3. S6 商务接待场景级回归：execute 侧与 ILS 侧召回一致（修复 S7 双世界）
# ============================================================


def test_execute_recall_nonempty_and_consistent_with_ils_for_business_intent():
    """修复前故障模式：execute 阶段传真实 home 坐标 → haversine 真实距离常年
    远超 authored 字段刻意收窄的 ~5km 叙事半径，S7/S6 这类默认 distance_max_km=5
    的商务场景会被距离过滤器清空；ILS 阶段不传坐标、读 authored 字段仍能召回
    ——同一 intent 两个世界。

    修复后两侧共享同一个 `venue_distance_km` 接缝：execute 阶段召回不应为空，
    且与 ILS 阶段对同一实体的 distance_km 读数完全一致。
    """
    intent = _business_intent()
    tracer = Tracer()

    ils_pois = _query_pois(intent, tracer)
    ils_rests = _query_restaurants(intent, tracer)
    exec_pois, _ = search_pois_for_intent(intent, limit=20, user_id="demo_user")
    exec_rests, _ = search_restaurants_for_intent(intent, limit=20, user_id="demo_user")

    assert exec_pois, "execute 阶段 POI 召回不应为空（修复前的真实故障模式）"
    assert exec_rests, "execute 阶段餐厅召回不应为空（修复前的真实故障模式）"
    assert ils_pois, "ILS 阶段 POI 召回不应为空（对照组，回归基线）"
    assert ils_rests, "ILS 阶段餐厅召回不应为空（对照组，回归基线）"

    ils_poi_by_id = {p.id: p for p in ils_pois}
    for p in exec_pois:
        if p.id in ils_poi_by_id:
            assert p.distance_km == ils_poi_by_id[p.id].distance_km, (
                f"{p.id} 在 execute/ILS 两侧的 distance_km 不一致：ILS 已改调 "
                "venue_distance_km，两侧应读同一份 authored 真相"
            )

    ils_rest_by_id = {r.id: r for r in ils_rests}
    for r in exec_rests:
        if r.id in ils_rest_by_id:
            assert r.distance_km == ils_rest_by_id[r.id].distance_km


# ============================================================
# 4. rule_planner（D2 兜底地板）的查询同样带 home 坐标——第三条查询路径接缝钉
#    （c′批当时只点名 execute/ILS 两条,rule_planner 留作已知缺口;本测试钉住
#    收口后的现状:authored 模式下行为不变——上面第 2 节已证明传/不传等价——
#    但 coords 模式启用时三条路径同步切换,不再有落后路径。）
# ============================================================


def test_rule_planner_queries_carry_home_coords():
    from types import SimpleNamespace

    from agent.planning.planners.rule_planner import (
        _query_pois as rule_query_pois,
        _query_restaurants as rule_query_restaurants,
    )

    home = load_user_profile().home_location
    captured: dict[str, dict] = {}

    def _fake_call(tool_name: str, args: dict):
        captured[tool_name] = args
        if tool_name == "search_pois":
            out = SearchPoisOutput(
                success=True, candidates=[_poi(dist=1.0, lat=None, lng=None)]
            )
        else:
            out = SearchRestaurantsOutput(
                success=True, candidates=[_rest(dist=1.0, lat=None, lng=None)]
            )
        return SimpleNamespace(success=True, output=out.model_dump(), reason=None)

    intent = _business_intent()
    tracer = Tracer()
    pois = rule_query_pois(intent, _fake_call, tracer)
    rests = rule_query_restaurants(intent, _fake_call, tracer)
    assert isinstance(pois, list) and isinstance(rests, list)  # 前置：第 1 级即命中

    for tool_name in ("search_pois", "search_restaurants"):
        args = captured[tool_name]
        assert args.get("user_lat") == home.lat, f"{tool_name} 未携带 home 纬度"
        assert args.get("user_lng") == home.lng, f"{tool_name} 未携带 home 经度"
