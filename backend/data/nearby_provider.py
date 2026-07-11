"""data.nearby_provider —— 「附近搜索」抽象层（评分项「一键部署」+ 商业演进叙事）。

设计动机：
    Hackathon demo 用 mock 数据 + haversine 实时算距离；评委可以一键 docker 起，
    切到 NEARBY_PROVIDER=gaode/meituan 立刻接入真 POI 接口（接入位已留好）。

为什么不放 backend/agent/v2/tool_provider.py：
    tool_provider 是 Pydantic AI ReAct 的 8 工具抽象（已冻结）；
    本文件单独抽「附近搜索」语义，理由：
    - 真接入时高德的 PlaceSearch.searchNearBy / 美团 POI 接口签名不一样
    - 抽出来后 search_pois Tool 的「过滤逻辑」与「数据源」解耦
    - LangGraph execute 阶段 worker 可以独立替换数据源不影响其它工具

Protocol：
    search_pois_nearby(lat, lng, max_km) -> list[Poi]
    search_restaurants_nearby(lat, lng, max_km) -> list[Restaurant]
    距离已实时算好，写回到对象的 distance_km 字段（覆盖 mock 原值）。

env 切换（参考 backend/.env.example NEARBY_PROVIDER 段）：
    mock     → MockNearbyProvider（默认；mock 数据 + venue_distance_km）
    gaode    → GaodeNearbyProvider（stub，NotImplementedError 含锚点）
    meituan  → MeituanNearbyProvider（stub，同上）

【c′批 任务一：距离解析单一接缝 venue_distance_km】

诊断（已实证）：execute 阶段 worker（`agent/runtime/tools/search_adapter.py`
的 `_resolve_user_coords`）总是把真实 home 坐标传给 `search_pois`/
`search_restaurants` Tool，Tool 收到坐标就转发到本模块的
`MockNearbyProvider`，历史实现在这里直接对 `location.lat/lng` 跑 haversine；
而 ILS 兜底 planner（`agent/planning/planners/ils_planner.py::_query_pois`/
`_query_restaurants`）历史上压根不传坐标，落到 Tool 的 `else` 分支直接读
mock 数据自带的 `distance_km` 字段。两条路径因此读的不是同一份"距离真相"
——同一个 intent，execute 侧按真实坐标算出的距离，与 ILS 侧按 authored 字段
算出的距离系统性不一致（实测偏差 0.2x～5x，见 `data/loader.py::
dataset_distance_mode` 消费点 docstring），S7 商务场景因此出现"execute 召回
0/0、ILS 召回 5/6"的同 intent 双世界。

修复：`venue_distance_km` 收口成单一函数——两条路径都改调它（execute 侧走
`MockNearbyProvider`，ILS 侧新增传 home 坐标同样落进 `MockNearbyProvider`，
见 `ils_planner._query_pois`/`_query_restaurants` 改动），内部按
`data.loader.dataset_distance_mode()` 的显式声明分派，不再由"调用方传不传
坐标"这个隐式副作用决定读哪份真相。

不负责：
- tag / 容量 / 时段过滤（在 search_pois / search_restaurants Tool 内）
- 路线规划（在 estimate_route_time Tool / 前端 AMap.Driving）
"""

from __future__ import annotations

import math
import os
from typing import Protocol, runtime_checkable, Union

from data.loader import dataset_distance_mode, load_pois, load_restaurants
from schemas.domain import Poi, Restaurant


# ============================================================
# 协议
# ============================================================


@runtime_checkable
class NearbySearchProvider(Protocol):
    """附近搜索数据源协议。

    所有实现必须把每条结果的 distance_km 重写为「该 POI/餐厅到 (lat, lng) 的实际距离」，
    以便下游 Tool 直接用 distance_km 字段做过滤。
    """

    def search_pois_nearby(
        self, lat: float, lng: float, max_km: float
    ) -> list[Poi]:
        ...

    def search_restaurants_nearby(
        self, lat: float, lng: float, max_km: float
    ) -> list[Restaurant]:
        ...


# ============================================================
# 工具：haversine 球面距离
# ============================================================


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两个经纬度点的球面距离（km）。"""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(
        dlmb / 2
    ) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


# ============================================================
# 距离解析单一接缝（c′批 任务一；见模块 docstring「距离解析单一接缝」节）
# ============================================================


def venue_distance_km(
    venue: Union[Poi, Restaurant], home_lat: float, home_lng: float
) -> float:
    """home→venue 距离的单一真相源——按 `data.loader.dataset_distance_mode()`
    的显式声明分派，execute 侧（`MockNearbyProvider`）与 ILS 侧
    （`agent/planning/planners/ils_planner.py::_query_pois`/`_query_restaurants`，
    经由本模块的 provider 调用）现在都改调这一个函数，不再各自决定"该不该算
    真实坐标"。

    - `"authored"`（默认）：直接返回 `venue.distance_km`——杭州归档集（存量
      测试加载）里这个手写字段才是产品叙事的距离真相；望京活集（顶层
      mock_data/，2026-07-10 起的现场演示集）该字段已按真值坐标 haversine
      重算、与坐标一致（见 data/loader.py「距离真相源」声明），本分支对两套
      数据集都正确。`home_lat`/`home_lng` 在这个分支里不参与计算（仅为保持
      两分支同签名，调用方不需要按模式分叉调用方式）。
    - `"coords"`（保留给未来「坐标可信但 distance_km 不再维护」的数据集）：
      对 `venue.location` 与 `(home_lat, home_lng)` 跑 haversine 实时算。
      若 `venue.location` 缺坐标（防御性；authored 集允许坐标缺失，coords 集
      理论上不该发生）——退回 `venue.distance_km`，不因为一条坐标缺失就把
      整个候选丢掉。

    Returns:
        距离（km），coords 分支保留 2 位小数（与历史 haversine 写回精度一致）。
    """
    if dataset_distance_mode() == "coords":
        loc = venue.location
        if loc.lat is not None and loc.lng is not None:
            return round(haversine_km(home_lat, home_lng, loc.lat, loc.lng), 2)
    return venue.distance_km


# ============================================================
# Mock 实现（默认；venue_distance_km + mock 数据）
# ============================================================


class MockNearbyProvider:
    """从 mock_data/{pois,restaurants}.json 按 `venue_distance_km` 解析距离。

    坐标存在与否不再决定"能不能进结果"——distance 真相源已经收口到
    `venue_distance_km`（authored 模式下压根不需要坐标；见该函数 docstring）。
    """

    def search_pois_nearby(
        self, lat: float, lng: float, max_km: float
    ) -> list[Poi]:
        out: list[Poi] = []
        for p in load_pois():
            km = venue_distance_km(p, lat, lng)
            if km > max_km:
                continue
            # 写回 distance_km：authored 模式下值不变（本就是同一个字段）；
            # coords 模式下覆盖成本次实时算好的真实距离。
            p_copy = p.model_copy(update={"distance_km": km})
            out.append(p_copy)
        return out

    def search_restaurants_nearby(
        self, lat: float, lng: float, max_km: float
    ) -> list[Restaurant]:
        out: list[Restaurant] = []
        for r in load_restaurants():
            km = venue_distance_km(r, lat, lng)
            if km > max_km:
                continue
            r_copy = r.model_copy(update={"distance_km": km})
            out.append(r_copy)
        return out


# ============================================================
# 高德实现（stub；真接入时这里改）
# ============================================================


class GaodeNearbyProvider:
    """高德 Web 服务 PlaceSearch.searchNearBy 接入位（stub）。

    真接入时改 search_pois_nearby / search_restaurants_nearby 的实现：
        async def search_pois_nearby(self, lat, lng, max_km):
            url = "https://restapi.amap.com/v3/place/around"
            params = {
                "key": os.environ["AMAP_REST_KEY"],
                "location": f"{lng},{lat}",
                "radius": int(max_km * 1000),
                "types": "060000|070000|110000",   # 美食 / 商场 / 旅游景点
                "extensions": "all",
                "page_size": 25,
            }
            data = await aiohttp_get_json(url, params)
            return [_map_amap_to_poi(p) for p in data["pois"]]

    schema 映射建议：
        高德 location ("lng,lat") → Poi.location.lat/lng
        高德 distance（米）→ Poi.distance_km = distance / 1000
        高德 biz_ext.opentime → Poi.opening_hours
        高德 biz_ext.cost → Poi.price_range（解析）
        其它 tag / suitable_for 字段需查 high-level 类型词典或本地映射表

    详见 docs/06-business/01-数据源切换路径.md。
    """

    def search_pois_nearby(
        self, lat: float, lng: float, max_km: float  # noqa: ARG002
    ) -> list[Poi]:
        raise NotImplementedError(
            "GaodeNearbyProvider.search_pois_nearby 未实现。"
            "Hackathon demo 阶段请用 NEARBY_PROVIDER=mock；"
            "真接入步骤见 docs/06-business/01-数据源切换路径.md。"
        )

    def search_restaurants_nearby(
        self, lat: float, lng: float, max_km: float  # noqa: ARG002
    ) -> list[Restaurant]:
        raise NotImplementedError(
            "GaodeNearbyProvider.search_restaurants_nearby 未实现。"
            "Hackathon demo 阶段请用 NEARBY_PROVIDER=mock；"
            "真接入步骤见 docs/06-business/01-数据源切换路径.md。"
        )


# ============================================================
# 美团实现（stub；预留位置）
# ============================================================


class MeituanNearbyProvider:
    """美团 POI 接入位（stub）。

    真接入时按业务方提供的接口规范实现；schema 映射建议同高德 stub。
    详见 docs/06-business/01-数据源切换路径.md。
    """

    def search_pois_nearby(
        self, lat: float, lng: float, max_km: float  # noqa: ARG002
    ) -> list[Poi]:
        raise NotImplementedError(
            "MeituanNearbyProvider.search_pois_nearby 未实现。"
            "Hackathon demo 阶段请用 NEARBY_PROVIDER=mock。"
        )

    def search_restaurants_nearby(
        self, lat: float, lng: float, max_km: float  # noqa: ARG002
    ) -> list[Restaurant]:
        raise NotImplementedError(
            "MeituanNearbyProvider.search_restaurants_nearby 未实现。"
            "Hackathon demo 阶段请用 NEARBY_PROVIDER=mock。"
        )


# ============================================================
# 工厂
# ============================================================


_VALID = {"mock", "gaode", "meituan"}


def get_nearby_provider() -> NearbySearchProvider:
    """从 NEARBY_PROVIDER env 解析数据源；默认 mock。"""
    name = (os.getenv("NEARBY_PROVIDER") or "mock").strip().lower()
    if name not in _VALID:
        raise ValueError(
            f"NEARBY_PROVIDER 非法值: {name!r}（允许 {sorted(_VALID)}）"
        )
    if name == "gaode":
        return GaodeNearbyProvider()
    if name == "meituan":
        return MeituanNearbyProvider()
    return MockNearbyProvider()


__all__ = [
    "NearbySearchProvider",
    "MockNearbyProvider",
    "GaodeNearbyProvider",
    "MeituanNearbyProvider",
    "get_nearby_provider",
    "haversine_km",
    "venue_distance_km",
]
