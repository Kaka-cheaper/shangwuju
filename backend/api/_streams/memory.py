"""Memory 累积 helper（confirm/refine 路径调用）。

从 main.py 抽出（spec code-modularization-refactor H1-final）；
V1 refine 路径退役后 _accumulate_memory_after_refine 已删除，只保留 confirm 侧：
- _collect_itinerary_tags：从已确认 itinerary 抽 tag 集
- _accumulate_memory_after_confirm：confirm 后写 accepted + visited + preferred_route
"""

from __future__ import annotations

from typing import Any


def _collect_itinerary_tags(itinerary_dict: dict[str, Any]) -> list[str]:
    """从已确认 itinerary 里抽出命中的 tag（用于 memory accept）。

    edge_v1：遍历 `itinerary.nodes`（target_kind=poi/restaurant），跳过 home。
    策略：
    - 主活动 POI 的 tags + suitable_for
    - 用餐餐厅的 tags + suitable_for
    - 去重；tag 词典外的不写入（防漂移）
    """
    from schemas.tags import (
        DIETARY_TAGS,
        EXPERIENCE_TAGS,
        PHYSICAL_TAGS,
        SOCIAL_CONTEXTS,
    )

    valid = PHYSICAL_TAGS | DIETARY_TAGS | EXPERIENCE_TAGS | SOCIAL_CONTEXTS

    out: set[str] = set()

    # 注：dict 视图里只有 target_id；从 mock_data 反查取 tags / suitable_for
    try:
        from data.loader import load_pois, load_restaurants

        pois_by_id = {p.id: p for p in load_pois()}
        rests_by_id = {r.id: r for r in load_restaurants()}
    except Exception:  # noqa: BLE001
        pois_by_id = {}
        rests_by_id = {}

    for node in itinerary_dict.get("nodes") or []:
        target_kind = node.get("target_kind")
        target_id = node.get("target_id")
        if not target_id:
            continue
        if target_kind == "home":
            # 起终点 home 不参与 tag 累积
            continue
        if target_kind == "poi":
            poi = pois_by_id.get(target_id)
            if poi is not None:
                out.update(poi.tags or [])
                out.update(poi.suitable_for or [])
        elif target_kind == "restaurant":
            rest = rests_by_id.get(target_id)
            if rest is not None:
                out.update(rest.tags or [])
                out.update(rest.suitable_for or [])

    return [t for t in out if t in valid]


def _accumulate_memory_after_confirm(
    cached: dict[str, Any],
    itinerary_dict: dict[str, Any],
) -> None:
    """confirm 后：把 itinerary 命中的 tag / 访问 id / 路径写进 user memory。

    cached 里的 user_id 由 _planner_stream 写入；缺失时跳过累积（不阻塞主流程）。

    Step 7 升级：
    - record_accepted（既有）
    - record_visited（新）：把 itinerary 中的 poi_id / restaurant_id 写入访问历史
    - record_preferred_route（新）：相邻段 (from→to) 计数 +1
    """
    user_id = cached.get("user_id")
    if not user_id:
        return
    from data.memory_store import (
        record_accepted,
        record_preferred_route,
        record_visited,
    )

    tags = _collect_itinerary_tags(itinerary_dict)
    intent = cached.get("intent") or {}
    distance = intent.get("distance_max_km")
    try:
        record_accepted(
            user_id,
            tags=tags,
            distance_km=float(distance) if distance is not None else None,
        )
    except Exception:  # noqa: BLE001
        # 累积失败不阻塞主流程
        pass

    # Step 7：visited targets
    visits: list[tuple[str, str]] = []
    nodes = itinerary_dict.get("nodes") or []
    for node in nodes:
        target_kind = node.get("target_kind")
        target_id = node.get("target_id")
        if not target_id or target_kind == "home":
            continue
        if target_kind == "poi":
            visits.append((target_id, "poi"))
        elif target_kind == "restaurant":
            visits.append((target_id, "restaurant"))
    if visits:
        try:
            record_visited(user_id, visits=visits)
        except Exception:  # noqa: BLE001
            pass

    # Step 7：preferred routes (相邻段都有 target 时)
    # edge_v1：home 节点 target_id="home"，正好用作 segments 端点；不再按 kind 文本判返回。
    segments: list[tuple[str, str]] = []
    prev_loc: str | None = None
    for node in nodes:
        cur_loc = node.get("target_id")
        if not cur_loc:
            continue
        if prev_loc and cur_loc and prev_loc != cur_loc:
            segments.append((prev_loc, cur_loc))
        prev_loc = cur_loc
    if segments:
        try:
            record_preferred_route(user_id, segments=segments)
        except Exception:  # noqa: BLE001
            pass
