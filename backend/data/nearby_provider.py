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
    mock     → MockNearbyProvider（默认；mock 数据 + haversine）
    gaode    → GaodeNearbyProvider（stub，NotImplementedError 含锚点）
    meituan  → MeituanNearbyProvider（stub，同上）

不负责：
- tag / 容量 / 时段过滤（在 search_pois / search_restaurants Tool 内）
- 路线规划（在 estimate_route_time Tool / 前端 AMap.Driving）
"""

from __future__ import annotations

import math
import os
from typing import Protocol, runtime_checkable

from data.loader import load_pois, load_restaurants
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
# Mock 实现（默认；haversine + mock 数据）
# ============================================================


class MockNearbyProvider:
    """从 mock_data/{pois,restaurants}.json 用 haversine 实时算距离。"""

    def search_pois_nearby(
        self, lat: float, lng: float, max_km: float
    ) -> list[Poi]:
        out: list[Poi] = []
        for p in load_pois():
            if p.location.lat is None or p.location.lng is None:
                continue
            km = haversine_km(lat, lng, p.location.lat, p.location.lng)
            if km > max_km:
                continue
            # 写回 distance_km（覆盖 mock 原 home→POI 字段，保证下游过滤准确）
            p_copy = p.model_copy(update={"distance_km": round(km, 2)})
            out.append(p_copy)
        return out

    def search_restaurants_nearby(
        self, lat: float, lng: float, max_km: float
    ) -> list[Restaurant]:
        out: list[Restaurant] = []
        for r in load_restaurants():
            if r.location.lat is None or r.location.lng is None:
                continue
            km = haversine_km(lat, lng, r.location.lat, r.location.lng)
            if km > max_km:
                continue
            r_copy = r.model_copy(update={"distance_km": round(km, 2)})
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
]
