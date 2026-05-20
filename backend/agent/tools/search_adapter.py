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


def search_pois_for_intent(intent: IntentExtraction, *, limit: int = 5) -> list[Poi]:
    """按 intent 调 search_pois，返回候选；失败返 []。"""
    age_in_party = sorted(
        {c.age for c in intent.companions if c.age is not None}
    )
    inp = SearchPoisInput(
        distance_max_km=intent.distance_max_km or 5.0,
        physical_constraints=list(intent.physical_constraints),
        experience_tags=list(intent.experience_tags),
        social_context=intent.social_context,
        age_in_party=list(age_in_party),
        limit=limit,
    )
    out = invoke_tool("search_pois", inp.model_dump())
    if not out or not getattr(out, "success", False):
        return []
    return list(out.candidates or [])


def search_restaurants_for_intent(
    intent: IntentExtraction, *, limit: int = 5
) -> list[Restaurant]:
    """按 intent 调 search_restaurants，返回候选；失败返 []。"""
    party_size = max(1, sum(c.count for c in intent.companions) + 1)  # +1 自己
    inp = SearchRestaurantsInput(
        distance_max_km=intent.distance_max_km or 5.0,
        dietary_constraints=list(intent.dietary_constraints),
        social_context=intent.social_context,
        capacity_requirement=str(party_size) if party_size in (2, 4, 6, 8) else None,
        limit=limit,
    )
    out = invoke_tool("search_restaurants", inp.model_dump())
    if not out or not getattr(out, "success", False):
        return []
    return list(out.candidates or [])


def get_user_profile_for_user(user_id: str) -> Optional[GetUserProfileOutput]:
    """调 get_user_profile；失败返 None。"""
    try:
        inp = GetUserProfileInput(user_id=user_id)
        out = invoke_tool("get_user_profile", inp.model_dump())
        if not out or not getattr(out, "success", False):
            return None
        return out
    except Exception:  # noqa: BLE001
        return None
