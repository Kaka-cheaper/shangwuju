"""agent.lookup_hop —— 边解析层（assemble + critic 共用）。

【职责】
单一函数收口「from_id, to_id → (minutes, mode, path_type)」三级降级，
被 `assemble_from_blueprint` 与 `critics_v2._check_hop_feasibility` 共同调用；
对同一 (from, to, transport_pref) 输入**永远返回相同结果**（确定性，无随机），
保证 critic 与 assemble 看到的通勤分钟一致，不会一边查 mock 一边猜距离。

【三级降级】

```
1 级 / from == to       → (0, "virtual", "in_place")          —— 同地复用
2 级 / routes.json 命中 → (min, transport_pref, "real_route") —— Mock 路网真值
3 级 / haversine 直线   → (est, "haversine_estimated", "estimated") —— 双端坐标可解
4 级 / 全失败兜底       → (15, transport_pref, "estimated")    —— 数据缺失保守值
```

【模式速度常量（design.md / Requirement 4 R4.3）】
- 步行 5 km/h
- 出租 25 km/h
- 公交 18 km/h
- 路网折算系数 1.3（直线距离 × 1.3 ≈ 实际路径距离）
- 兜底分钟 15（够远但不离谱，让流程能继续）

【一致性来源】
- `mock_data/routes.json` 加载后建索引，模块级单次缓存（`_route_index`）。
- POI/Restaurant 坐标查询通过 `data.loader.load_pois() / load_restaurants()`
  本身已带 `lru_cache`，无需再缓存。
- 同一 (from_id, to_id, transport_pref) 输入命中相同分支、同一数值 → 同一结果。

【约定】
- `from_id == "home"` 或 `to_id == "home"` 时使用 `user_profile.home_location` 解析坐标；
  routes.json 中以字面量 `"home"` 出现的边可直接命中 2 级。
- POI id 以 `P` 开头，Restaurant id 以 `R` 开头；其它前缀视为未知，跳过 3 级直接 4 级。
- transport_pref 在 routes.json 对应字段为 None 时降级到 3 级（不静默回退到其它交通方式）。

【不负责】
- 不调 LLM、不调外部 API、不抛异常（最坏返回 4 级兜底）。
- 不缓存计算结果（输入决定输出，纯函数；调用频次不大无需 memoize）。
- 不做反向边查询（设计明确：从 from 到 to 找不到就降级，保持确定性）。
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Literal, Optional

from data.loader import load_pois, load_restaurants, load_routes
from schemas.domain import UserProfile
from schemas.itinerary import HopMode, HopPathType


# ============================================================
# 速度 / 折算常量
# ============================================================

WALKING_KMH: float = 5.0
"""步行速度（km/h），用于 haversine 估算。"""

TAXI_KMH: float = 25.0
"""出租车速度（km/h），用于 haversine 估算。"""

BUS_KMH: float = 18.0
"""公交速度（km/h），用于 haversine 估算。"""

ROAD_FACTOR: float = 1.3
"""路网折算系数：直线距离 × 1.3 ≈ 实际路径长度。"""

FALLBACK_MIN: int = 15
"""4 级兜底分钟数：数据全缺时返回这个值让流程能继续。"""

EARTH_RADIUS_KM: float = 6371.0
"""地球平均半径（km），haversine 公式用。"""


_TRANSPORT_PREFS: tuple[str, ...] = ("walking", "taxi", "bus")


# ============================================================
# 模块级缓存（同进程一次加载）
# ============================================================


@lru_cache(maxsize=1)
def _route_index() -> dict[tuple[str, str], dict[str, Optional[int]]]:
    """构建 (from_location, to_location) → {transport: minutes} 索引。

    从 `data.loader.load_routes()` 一次性物化为 dict，避免每次调用线性扫描 217 条记录。
    返回 dict 中的 transport key 严格为 walking / taxi / bus，对应 Route 的三个分钟字段；
    任一字段在 routes.json 中为 None 时此处也保留 None。
    """
    idx: dict[tuple[str, str], dict[str, Optional[int]]] = {}
    for r in load_routes():
        idx[(r.from_location, r.to_location)] = {
            "walking": r.walking_minutes,
            "taxi": r.taxi_minutes,
            "bus": r.bus_minutes,
        }
    return idx


@lru_cache(maxsize=1)
def _poi_coord_index() -> dict[str, tuple[float, float]]:
    """POI id → (lat, lng) 索引；缺坐标的 POI 不入表。"""
    out: dict[str, tuple[float, float]] = {}
    for p in load_pois():
        if p.location.lat is not None and p.location.lng is not None:
            out[p.id] = (p.location.lat, p.location.lng)
    return out


@lru_cache(maxsize=1)
def _restaurant_coord_index() -> dict[str, tuple[float, float]]:
    """Restaurant id → (lat, lng) 索引；缺坐标的餐厅不入表。"""
    out: dict[str, tuple[float, float]] = {}
    for r in load_restaurants():
        if r.location.lat is not None and r.location.lng is not None:
            out[r.id] = (r.location.lat, r.location.lng)
    return out


def reset_cache() -> None:
    """测试用：清空模块级 lru_cache（如 mock 数据被换掉时）。

    若调用方 monkeypatch 替换了某个被装饰函数，对应 cache_clear 不存在 → 静默跳过。
    """
    for fn in (_route_index, _poi_coord_index, _restaurant_coord_index):
        clear = getattr(fn, "cache_clear", None)
        if clear is not None:
            clear()


# ============================================================
# 辅助函数
# ============================================================


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两点间球面直线距离（km）。

    标准 haversine 公式；输入纬度/经度均为 WGS-84 度数。
    """
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_KM * c


def _resolve_coord(
    target_id: str, user_profile: UserProfile
) -> Optional[tuple[float, float]]:
    """按 target_id 解析 (lat, lng)；解析失败返回 None。

    - `target_id == "home"` → user_profile.home_location.lat/lng
    - 以 `P` 开头 → POI 坐标表
    - 以 `R` 开头 → Restaurant 坐标表
    - 其它前缀或坐标缺失 → None
    """
    if target_id == "home":
        loc = user_profile.home_location
        if loc.lat is None or loc.lng is None:
            return None
        return (loc.lat, loc.lng)
    if target_id.startswith("P"):
        return _poi_coord_index().get(target_id)
    if target_id.startswith("R"):
        return _restaurant_coord_index().get(target_id)
    return None


def _speed_kmh_for(mode: str) -> float:
    """transport_pref → 速度（km/h）。未识别的偏好降级到 taxi。"""
    if mode == "walking":
        return WALKING_KMH
    if mode == "bus":
        return BUS_KMH
    # taxi 或未知（如 "haversine_estimated" 不会进这里）
    return TAXI_KMH


# ============================================================
# 主函数
# ============================================================


def lookup_hop(
    from_id: str,
    to_id: str,
    transport_pref: Literal["walking", "taxi", "bus"],
    user_profile: UserProfile,
) -> tuple[int, HopMode, HopPathType]:
    """边解析三级降级。

    Args:
        from_id: 起点 target_id（POI/Restaurant id 或 "home"）。
        to_id: 终点 target_id。
        transport_pref: 交通偏好；越界值视作 "taxi"（不抛异常）。
        user_profile: 含 home_location 坐标的用户画像。

    Returns:
        (minutes, mode, path_type) 三元组：
        - minutes: 通勤分钟数（NonNegativeInt 范围内整数）
        - mode: HopMode（实际匹配的交通方式或 "virtual"/"haversine_estimated"）
        - path_type: HopPathType（"real_route" / "estimated" / "in_place"）

    Examples:
        >>> lookup_hop("P001", "P001", "taxi", profile)
        (0, "virtual", "in_place")

        >>> lookup_hop("home", "P001", "taxi", profile)
        (13, "taxi", "real_route")  # 命中 routes.json

        >>> lookup_hop("P001", "home", "taxi", profile)
        (~minutes, "haversine_estimated", "estimated")  # routes 无反向边，走 haversine

        >>> lookup_hop("UNKNOWN", "GHOST", "taxi", profile)
        (15, "taxi", "estimated")  # 兜底
    """
    # 兜底交通偏好（防止 LLM 输出 "drive" 之类非法值）
    pref: str = transport_pref if transport_pref in _TRANSPORT_PREFS else "taxi"

    # ---------- 1 级：from == to → in_place ----------
    if from_id == to_id:
        return (0, "virtual", "in_place")

    # ---------- 2 级：routes.json 命中 ----------
    routes = _route_index()
    edge = routes.get((from_id, to_id))
    if edge is not None:
        minutes = edge.get(pref)
        if minutes is not None and minutes >= 0:
            # transport_pref 字段为正值即采纳；mode 用 transport_pref（real_route 总是 walking/taxi/bus）
            return (int(minutes), pref, "real_route")  # type: ignore[return-value]
        # 命中边但当前 transport_pref 字段为空 → 不静默换交通方式，降级到 3 级 haversine

    # ---------- 3 级：haversine 估算 ----------
    coord_from = _resolve_coord(from_id, user_profile)
    coord_to = _resolve_coord(to_id, user_profile)
    if coord_from is not None and coord_to is not None:
        km = _haversine_km(coord_from[0], coord_from[1], coord_to[0], coord_to[1])
        speed = _speed_kmh_for(pref)
        # 估算分钟 = 直线距离 × 路网折算 / 速度 × 60；最小 1 分钟避免 0 分钟通勤显得诡异
        est = max(1, int(round(km * ROAD_FACTOR / speed * 60)))
        return (est, "haversine_estimated", "estimated")

    # ---------- 4 级：保守兜底 ----------
    return (FALLBACK_MIN, pref, "estimated")  # type: ignore[return-value]
