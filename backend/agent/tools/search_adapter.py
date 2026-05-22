"""agent.tools.search_adapter —— 把 IntentExtraction 转成 ToolInput 调工具。

execute 阶段的 worker 调它：

    pois = search_pois_for_intent(intent)
    rests = search_restaurants_for_intent(intent)

不抛异常：失败/空集返回空 list（让 replan 去判断）。

不发明 schema —— 直接复用 schemas/tools.py 的 Input/Output。
"""

from __future__ import annotations

from typing import Optional

from schemas.domain import Poi, Restaurant
from schemas.intent import IntentExtraction
from schemas.tools import (
    GetUserProfileInput,
    GetUserProfileOutput,
    SearchPoisInput,
    SearchRestaurantsInput,
)
from tools.registry import invoke_tool


def _resolve_user_coords(user_id: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """从 user_profile 取 home_location 的 lat/lng；缺省返 (None, None)。

    NearbySearchProvider 拿到 (lat, lng) 才能实时算距离；缺省时 search_pois /
    search_restaurants 会回退到 mock 数据预填的 distance_km 字段。
    """
    if not user_id:
        return (None, None)
    try:
        out = invoke_tool(
            "get_user_profile", GetUserProfileInput(user_id=user_id).model_dump()
        )
        if not out or not getattr(out, "success", False):
            return (None, None)
        profile = GetUserProfileOutput.model_validate(out.output).profile
        if profile is None:
            return (None, None)
        loc = profile.home_location
        return (loc.lat, loc.lng)
    except Exception:  # noqa: BLE001
        return (None, None)


def search_pois_for_intent(
    intent: IntentExtraction,
    *,
    limit: int = 5,
    user_id: Optional[str] = None,
) -> list[Poi]:
    """按 intent 调 search_pois，返回候选；失败返 []。

    user_id 提供时从 user_profile 取 home_location 作 NearbyProvider 的查询基准。
    """
    age_in_party = sorted(
        {c.age for c in intent.companions if c.age is not None}
    )
    user_lat, user_lng = _resolve_user_coords(user_id)
    inp = SearchPoisInput(
        distance_max_km=intent.distance_max_km or 5.0,
        physical_constraints=list(intent.physical_constraints),
        experience_tags=list(intent.experience_tags),
        social_context=intent.social_context,
        age_in_party=list(age_in_party),
        user_lat=user_lat,
        user_lng=user_lng,
        limit=limit,
    )
    out = invoke_tool("search_pois", inp.model_dump())
    if not out or not getattr(out, "success", False):
        return []
    candidates = (out.output or {}).get("candidates") or []
    # output 是 dict, candidates 内可能是 dict 也可能是 Poi 对象
    result: list[Poi] = []
    for c in candidates:
        if isinstance(c, Poi):
            result.append(c)
        elif isinstance(c, dict):
            try:
                result.append(Poi.model_validate(c))
            except Exception:  # noqa: BLE001
                continue
    return result


def search_restaurants_for_intent(
    intent: IntentExtraction,
    *,
    limit: int = 5,
    user_id: Optional[str] = None,
) -> list[Restaurant]:
    """按 intent 调 search_restaurants，返回候选；失败返 []。

    user_id 提供时从 user_profile 取 home_location 作 NearbyProvider 的查询基准。
    """
    party_size = max(1, sum(c.count for c in intent.companions) + 1)  # +1 自己
    user_lat, user_lng = _resolve_user_coords(user_id)
    inp = SearchRestaurantsInput(
        distance_max_km=intent.distance_max_km or 5.0,
        dietary_constraints=list(intent.dietary_constraints),
        social_context=intent.social_context,
        capacity_requirement=str(party_size) if party_size in (2, 4, 6, 8) else None,
        user_lat=user_lat,
        user_lng=user_lng,
        limit=limit,
    )
    out = invoke_tool("search_restaurants", inp.model_dump())
    if not out or not getattr(out, "success", False):
        return []
    candidates = (out.output or {}).get("candidates") or []
    result: list[Restaurant] = []
    for c in candidates:
        if isinstance(c, Restaurant):
            result.append(c)
        elif isinstance(c, dict):
            try:
                result.append(Restaurant.model_validate(c))
            except Exception:  # noqa: BLE001
                continue
    return result


def get_user_profile_for_user(user_id: str) -> Optional[GetUserProfileOutput]:
    """调 get_user_profile；失败返 None。"""
    try:
        inp = GetUserProfileInput(user_id=user_id)
        out = invoke_tool("get_user_profile", inp.model_dump())
        if not out or not getattr(out, "success", False):
            return None
        try:
            return GetUserProfileOutput.model_validate(out.output)
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None
