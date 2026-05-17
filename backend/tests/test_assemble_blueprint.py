"""tests.test_assemble_blueprint —— 蓝图→Itinerary 拼装。

assemble_from_blueprint(intent, blueprint) 把 LLM 出的 PlanBlueprint 拼装为
合法 Itinerary（schemas/itinerary.py 的 ItineraryStage 列表）。

约束：
- 输出 stages 与 blueprint.stages 一一对应（顺序、kind、时间、target_id 全保留）
- target_kind=poi → ItineraryStage.poi_id
- target_kind=restaurant → ItineraryStage.restaurant_id
- summary 适配：根据段集合自动写"半日方案"/"轻量方案"/"用餐方案"
- total_minutes = blueprint.total_minutes()
"""

from __future__ import annotations

import pytest

from agent.assemble_blueprint import assemble_from_blueprint
from agent.blueprint import (
    BlueprintStage,
    BlueprintTargetKind,
    PlanBlueprint,
)
from data.loader import load_pois, load_restaurants
from schemas.intent import Companion, IntentExtraction
from schemas.itinerary import Itinerary


def _intent(duration: list[int] = [1, 1]) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=list(duration),
        distance_max_km=5,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="独处放空",
        raw_input="只有一个小时",
        parse_confidence=0.9,
    )


# ============================================================
# 基础：3 段 / 5 段 / 单段
# ============================================================

def test_assemble_three_stage_dining_only():
    """单段用餐场景：出发 + 用餐 + 返回。"""
    rests = load_restaurants()
    target = next(r for r in rests if r.id == "R001")
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="出发", start_time="14:00", duration_min=15),
            BlueprintStage(
                kind="用餐",
                start_time="14:15",
                duration_min=60,
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
            ),
            BlueprintStage(kind="返回", start_time="15:15", duration_min=15),
        ],
        rationale="单段去吃",
    )
    itin = assemble_from_blueprint(_intent([1, 2]), bp)
    assert isinstance(itin, Itinerary)
    assert len(itin.stages) == 3
    assert {s.kind for s in itin.stages} == {"出发", "用餐", "返回"}
    dining = next(s for s in itin.stages if s.kind == "用餐")
    assert dining.restaurant_id == "R001"
    assert dining.title  # 含餐厅名


def test_assemble_solo_main_only():
    """独处沉浸：出发 + 主活动 + 返回（仅 POI）。"""
    pois = load_pois()
    target = next(p for p in pois if "独处舒缓" in p.tags)
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="出发", start_time="14:00", duration_min=15),
            BlueprintStage(
                kind="主活动",
                start_time="14:15",
                duration_min=120,
                target_kind=BlueprintTargetKind.POI,
                target_id=target.id,
            ),
            BlueprintStage(kind="返回", start_time="16:15", duration_min=15),
        ],
        rationale="独处去图书馆",
    )
    itin = assemble_from_blueprint(_intent([2, 3]), bp)
    main = next(s for s in itin.stages if s.kind == "主活动")
    assert main.poi_id == target.id


def test_assemble_full_five_stage():
    """完整 5 段：出发+主活动+转场+用餐+返回。"""
    pois = load_pois()
    rests = load_restaurants()
    poi = pois[0]
    rest = rests[0]
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="出发", start_time="14:00", duration_min=15),
            BlueprintStage(
                kind="主活动",
                start_time="14:15",
                duration_min=120,
                target_kind=BlueprintTargetKind.POI,
                target_id=poi.id,
            ),
            BlueprintStage(kind="转场", start_time="16:15", duration_min=15),
            BlueprintStage(
                kind="用餐",
                start_time="16:30",
                duration_min=60,
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id=rest.id,
            ),
            BlueprintStage(kind="返回", start_time="17:30", duration_min=15),
        ],
        rationale="完整 4h",
    )
    itin = assemble_from_blueprint(_intent([3, 4]), bp)
    assert len(itin.stages) == 5


# ============================================================
# 反序：餐厅 → POI
# ============================================================

def test_assemble_reverse_order_dining_then_poi():
    """先吃饭再看展：用餐在主活动之前。"""
    pois = load_pois()
    rests = load_restaurants()
    poi = next(p for p in pois if "看展" in p.tags)
    rest = rests[0]
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="出发", start_time="11:30", duration_min=15),
            BlueprintStage(
                kind="用餐",
                start_time="11:45",
                duration_min=60,
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id=rest.id,
            ),
            BlueprintStage(kind="转场", start_time="12:45", duration_min=15),
            BlueprintStage(
                kind="主活动",
                start_time="13:00",
                duration_min=120,
                target_kind=BlueprintTargetKind.POI,
                target_id=poi.id,
            ),
            BlueprintStage(kind="返回", start_time="15:00", duration_min=15),
        ],
        rationale="先吃饭再看展",
    )
    itin = assemble_from_blueprint(_intent([3, 4]), bp)
    # 顺序保留
    kinds_in_order = [s.kind for s in itin.stages]
    assert kinds_in_order == ["出发", "用餐", "转场", "主活动", "返回"]


# ============================================================
# Note 透传 + summary 适配
# ============================================================

def test_assemble_note_transmitted():
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(
                kind="夜宵",
                start_time="22:00",
                duration_min=60,
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                note="24h 营业的烤串店",
            )
        ],
        rationale="夜宵",
    )
    itin = assemble_from_blueprint(_intent([1, 1]), bp)
    assert itin.stages[0].note and "24h" in itin.stages[0].note


def test_summary_for_dining_only():
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(
                kind="用餐",
                start_time="14:00",
                duration_min=60,
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
            )
        ],
        rationale="只去吃饭",
    )
    itin = assemble_from_blueprint(_intent([1, 1]), bp)
    assert "用餐方案" in itin.summary or "R001" in itin.summary or "餐厅" in itin.summary


def test_summary_for_main_only():
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(
                kind="主活动",
                start_time="14:00",
                duration_min=60,
                target_kind=BlueprintTargetKind.POI,
                target_id="P001",
            )
        ],
        rationale="去玩",
    )
    itin = assemble_from_blueprint(_intent([1, 1]), bp)
    assert "P001" in itin.summary or "轻量方案" in itin.summary


# ============================================================
# total_minutes
# ============================================================

def test_total_minutes_matches_blueprint():
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="出发", start_time="14:00", duration_min=15),
            BlueprintStage(kind="主活动", start_time="14:15", duration_min=45),
            BlueprintStage(kind="返回", start_time="15:00", duration_min=15),
        ],
        rationale="ok",
    )
    itin = assemble_from_blueprint(_intent([1, 1]), bp)
    assert itin.total_minutes == 75


# ============================================================
# 不存在的 target_id：拼装时容忍（但 critic 会拦）
# ============================================================

def test_unknown_target_id_still_assembles():
    """assemble 不验证 id 真实性；那是 critic 的职责。"""
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(
                kind="主活动",
                start_time="14:00",
                duration_min=60,
                target_kind=BlueprintTargetKind.POI,
                target_id="P_NOT_EXIST",
            )
        ],
        rationale="bad id",
    )
    itin = assemble_from_blueprint(_intent([1, 1]), bp)
    assert itin.stages[0].poi_id == "P_NOT_EXIST"
