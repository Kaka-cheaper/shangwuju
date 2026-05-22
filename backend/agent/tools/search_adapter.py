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


def _resolve_excluded_visited_ids(
    user_id: Optional[str], *, kind: str
) -> list[str]:
    """从 UserMemory 取最近 30 天访问过的 target_id（按 kind 过滤）。

    Args:
        user_id: 用户 id；空则返空 list
        kind: 'poi' 或 'restaurant'

    失败兜底返空——不应影响主路径。
    """
    if not user_id:
        return []
    try:
        from data.memory_store import get_memory

        memory = get_memory(user_id)
        if not memory.visited_targets:
            return []
        recent_ids = set(memory.recently_visited_ids(within_days=30))
        # 仅返指定 kind 的
        return [
            r.target_id
            for r in memory.visited_targets
            if r.target_id in recent_ids and r.target_kind == kind
        ]
    except Exception:  # noqa: BLE001
        return []


def search_pois_for_intent(
    intent: IntentExtraction,
    *,
    limit: int = 5,
    user_id: Optional[str] = None,
) -> tuple[list[Poi], list[str]]:
    """按 intent 调 search_pois，返回 (候选, relaxed_tags)；失败返 ([], [])。

    user_id 提供时：
    - 从 user_profile 取 home_location 作 NearbyProvider 的查询基准
    - 从 UserMemory 取最近 30 天访问过的 POI id 排除（Step 7 个性化记忆）
    Step 6：tag relaxation 透出 relaxed_tags 让上层（execute_worker / sse_adapter）
    把放宽路径透传给前端 / LLM。
    """
    age_in_party = sorted(
        {c.age for c in intent.companions if c.age is not None}
    )
    user_lat, user_lng = _resolve_user_coords(user_id)
    excluded_ids = _resolve_excluded_visited_ids(user_id, kind="poi")
    inp = SearchPoisInput(
        distance_max_km=intent.distance_max_km or 5.0,
        physical_constraints=list(intent.physical_constraints),
        experience_tags=list(intent.experience_tags),
        social_context=intent.social_context,
        age_in_party=list(age_in_party),
        user_lat=user_lat,
        user_lng=user_lng,
        exclude_visited_ids=excluded_ids,
        limit=limit,
    )
    out = invoke_tool("search_pois", inp.model_dump())
    if not out or not getattr(out, "success", False):
        # 即使失败仍尝试取 relaxed_tags（让上层知道哪些被放过）
        relaxed = (out.output or {}).get("relaxed_tags") if out else []
        return [], list(relaxed or [])
    output_dict = out.output or {}
    candidates = output_dict.get("candidates") or []
    relaxed = output_dict.get("relaxed_tags") or []
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
    return result, list(relaxed)


def search_restaurants_for_intent(
    intent: IntentExtraction,
    *,
    limit: int = 5,
    user_id: Optional[str] = None,
) -> tuple[list[Restaurant], list[str]]:
    """按 intent 调 search_restaurants，返回 (候选, relaxed_tags)；失败返 ([], [])。

    user_id 提供时：
    - 从 user_profile 取 home_location 作 NearbyProvider 的查询基准
    - 从 UserMemory 取最近 30 天访问过的餐厅 id 排除（Step 7）
    """
    party_size = max(1, sum(c.count for c in intent.companions) + 1)  # +1 自己
    user_lat, user_lng = _resolve_user_coords(user_id)
    excluded_ids = _resolve_excluded_visited_ids(user_id, kind="restaurant")
    inp = SearchRestaurantsInput(
        distance_max_km=intent.distance_max_km or 5.0,
        dietary_constraints=list(intent.dietary_constraints),
        social_context=intent.social_context,
        capacity_requirement=party_size if party_size in (2, 4, 6, 8) else None,
        user_lat=user_lat,
        user_lng=user_lng,
        exclude_visited_ids=excluded_ids,
        limit=limit,
    )
    out = invoke_tool("search_restaurants", inp.model_dump())
    if not out or not getattr(out, "success", False):
        relaxed = (out.output or {}).get("relaxed_tags") if out else []
        return [], list(relaxed or [])
    output_dict = out.output or {}
    candidates = output_dict.get("candidates") or []
    relaxed = output_dict.get("relaxed_tags") or []
    result: list[Restaurant] = []
    for c in candidates:
        if isinstance(c, Restaurant):
            result.append(c)
        elif isinstance(c, dict):
            try:
                result.append(Restaurant.model_validate(c))
            except Exception:  # noqa: BLE001
                continue
    return result, list(relaxed)


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
