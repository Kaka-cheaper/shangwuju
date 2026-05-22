"""agent.assemble_blueprint —— PlanBlueprint → Itinerary 时间轴拼装。

LLM-First Planner 把 LLM 出的蓝图（kind/start/duration/target_id 列表）转成
合法的 Itinerary 时间轴对象。这是「LLM 决主观、算法决客观」分工里的「客观」侧。

设计纪律：
- assemble_from_blueprint 是**纯函数**：仅依据 blueprint + intent 生成 Itinerary，
  不调 Tool，不验证 target_id 真实性（Critic 已验证过；本函数容忍未知 id）
- 段顺序保留 LLM 原意（不重排序）
- target_kind=poi/restaurant → 写到对应的 poi_id/restaurant_id 字段
- summary 文案按段集合自适应（避免"半日方案"硬套到 1h 单段场景）

不负责：
- LLM 调用 / Critic 验证（在 blueprint_llm.py / blueprint.py）
- 路线时间估算（蓝图里的 duration_min 已包含路程，不必再调 estimate_route_time）
"""

from __future__ import annotations

from data.loader import load_pois, load_restaurants
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary, ItineraryStage

from .blueprint import (
    BlueprintStage,
    BlueprintTargetKind,
    PlanBlueprint,
)


# ============================================================
# 标题文案
# ============================================================

def _stage_title(s: BlueprintStage) -> str:
    """根据 stage.kind / target 组装中文标题。"""
    if s.target_kind == BlueprintTargetKind.POI and s.target_id:
        poi = next((p for p in load_pois() if p.id == s.target_id), None)
        if poi:
            if s.kind == "出发":
                return f"出发前往「{poi.name}」"
            elif s.kind == "返回":
                return "回家"
            else:
                return f"{s.kind} · {poi.name}"
        # id 不存在容忍（critic 应该已经拒了，但兜底）
        return f"{s.kind}（{s.target_id}）"

    if s.target_kind == BlueprintTargetKind.RESTAURANT and s.target_id:
        rest = next((r for r in load_restaurants() if r.id == s.target_id), None)
        if rest:
            if s.kind == "出发":
                return f"出发前往「{rest.name}」"
            elif s.kind == "返回":
                return "回家"
            else:
                return f"{s.kind} · {rest.cuisine} · {rest.name}"
        return f"{s.kind}（{s.target_id}）"

    # 无 target
    if s.kind == "出发":
        return "出发"
    if s.kind == "返回":
        return "回家"
    if s.kind == "转场":
        return "转场"
    return s.kind


# ============================================================
# Summary 适配
# ============================================================

def _build_summary(blueprint: PlanBlueprint) -> str:
    """根据段集合自适应 summary 文案。"""
    has_main = any(
        s.target_kind == BlueprintTargetKind.POI for s in blueprint.stages
    )
    has_dining = any(
        s.target_kind == BlueprintTargetKind.RESTAURANT for s in blueprint.stages
    )

    # 取主体段的标题（最长 duration 的那段，作为方案标识）
    body_stages = [
        s for s in blueprint.stages
        if s.target_kind != BlueprintTargetKind.NONE
    ]
    if body_stages:
        primary = max(body_stages, key=lambda x: x.duration_min)
        primary_label = ""
        if primary.target_kind == BlueprintTargetKind.POI and primary.target_id:
            poi = next(
                (p for p in load_pois() if p.id == primary.target_id), None
            )
            primary_label = poi.name if poi else primary.target_id
        elif primary.target_kind == BlueprintTargetKind.RESTAURANT and primary.target_id:
            rest = next(
                (r for r in load_restaurants() if r.id == primary.target_id),
                None,
            )
            primary_label = rest.name if rest else primary.target_id
    else:
        primary_label = ""

    total_h = round(blueprint.total_minutes() / 60, 1)

    if has_main and has_dining:
        return f"半日方案 · {primary_label}（约 {total_h} 小时）"
    if has_dining and not has_main:
        return f"用餐方案 · {primary_label}（约 {total_h} 小时）"
    if has_main and not has_dining:
        return f"轻量方案 · {primary_label}（约 {total_h} 小时）"
    return f"短途方案（约 {total_h} 小时）"


# ============================================================
# 主入口
# ============================================================

def _resolve_coord_and_address(
    s: BlueprintStage,
) -> tuple[float | None, float | None, str | None]:
    """根据 blueprint stage 的 target_kind/target_id 取 (lat, lng, address)。

    无 target / 找不到 / 该地点没坐标 → 全部返 None。
    转场/出发/返回这种过程类段一般无 target，前端 MapOverlay 会跳过。
    """
    if s.target_kind == BlueprintTargetKind.POI and s.target_id:
        poi = next((p for p in load_pois() if p.id == s.target_id), None)
        if poi:
            loc = poi.location
            return (loc.lat, loc.lng, loc.name)
    elif s.target_kind == BlueprintTargetKind.RESTAURANT and s.target_id:
        rest = next((r for r in load_restaurants() if r.id == s.target_id), None)
        if rest:
            loc = rest.location
            return (loc.lat, loc.lng, loc.name)
    return (None, None, None)


# ============================================================
# 主入口
# ============================================================

def assemble_from_blueprint(
    intent: IntentExtraction,  # noqa: ARG001 — 保留参数为未来扩展（如附加文案）
    blueprint: PlanBlueprint,
) -> Itinerary:
    """蓝图 → Itinerary。

    返回的 Itinerary.stages 与 blueprint.stages 一一对应，时间轴严格按蓝图。
    自动注入 lat/lng/address，前端无需二次查询。
    """
    itinerary_stages: list[ItineraryStage] = []
    for s in blueprint.stages:
        poi_id = s.target_id if s.target_kind == BlueprintTargetKind.POI else None
        restaurant_id = (
            s.target_id if s.target_kind == BlueprintTargetKind.RESTAURANT else None
        )
        lat, lng, address = _resolve_coord_and_address(s)
        itinerary_stages.append(
            ItineraryStage(
                kind=s.kind,
                start=s.start_time,
                end=s.end_time(),
                title=_stage_title(s),
                poi_id=poi_id,
                restaurant_id=restaurant_id,
                lat=lat,
                lng=lng,
                address=address,
                note=s.note,
            )
        )

    summary = _build_summary(blueprint)

    return Itinerary(
        summary=summary,
        stages=itinerary_stages,
        orders=[],
        share_message=None,
        total_minutes=blueprint.total_minutes(),
    )
