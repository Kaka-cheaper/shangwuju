"""spec algorithm-redesign R3：ils_planner grounding-first 前置硬剔除单测。

测试覆盖（≥ 5 项 + 集成）：
- 5 岁娃 + P033（180min default 投影 kid_3_6=90） → P033 留下；改造 fixture 让 suggested>90 触发剔除
- 70 岁外婆 P040 推荐 90min senior 桶 → 触发 senior cap（< 75）剔除
- 候选池 < 3 时自动放宽（仅距离 + 营业状态）
- 距离超 +1km 容差 → 严过滤剔除
- business_status="closed" → 剔除
- restaurant 满座（available=False）保留（满座由 critic 处理；grounding 不剔）
- tracer.emit("grounding_filtered") 记录正确
- 候选池为空时直接返空（向后兼容）

不消费真 LLM；不依赖 invoke_tool；用 mock Poi/Restaurant 直接调 _grounding_filter_poi/_restaurant。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# 复用过渡态桥
if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.core.trace import Tracer  # noqa: E402
from agent.planning.planners.ils_planner import (  # noqa: E402
    _grounding_filter_poi,
    _grounding_filter_restaurant,
)
from schemas.domain import (  # noqa: E402
    Location,
    Poi,
    PoiCapacity,
    Restaurant,
    RestaurantCapacity,
    SuggestedDuration,
)
from schemas.intent import Companion, IntentExtraction  # noqa: E402


# ============================================================
# fixture builders
# ============================================================


def _make_poi(
    poi_id: str,
    *,
    distance_km: float = 3.0,
    suggested_default: int = 60,
    suggested_kid_3_6: int | None = None,
    suggested_kid_7_12: int | None = None,
    suggested_senior: int | None = None,
    suggested_multi_gen: int | None = None,
    business_status: str = "open",
) -> Poi:
    """构造最小有效 Poi。"""
    poi = Poi(
        id=poi_id,
        name=f"Test {poi_id}",
        type="测试",
        location=Location(name="测试", lat=30.0, lng=120.0),
        distance_km=distance_km,
        opening_hours="09:00-21:00",
        rating=4.5,
        suggested_duration_minutes=SuggestedDuration(
            default=suggested_default,
            kid_3_6=suggested_kid_3_6,
            kid_7_12=suggested_kid_7_12,
            senior=suggested_senior,
            multi_gen=suggested_multi_gen,
        ),
    )
    # business_status 不在 schema 中，用 attr 注入（_grounding_filter_poi 用 getattr 兜底）
    if business_status != "open":
        object.__setattr__(poi, "business_status", business_status)
    return poi


def _make_restaurant(
    rest_id: str,
    *,
    distance_km: float = 3.0,
    business_status: str = "open",
) -> Restaurant:
    rest = Restaurant(
        id=rest_id,
        name=f"Test {rest_id}",
        cuisine="测试",
        location=Location(name="测试", lat=30.0, lng=120.0),
        distance_km=distance_km,
        opening_hours="11:00-22:00",
        rating=4.5,
        avg_price=80.0,
        capacity=RestaurantCapacity(
            **{"2": True, "4": True, "6": False, "8": False}
        ),
    )
    if business_status != "open":
        object.__setattr__(rest, "business_status", business_status)
    return rest


def _make_intent_with_preschool(distance_max_km: float = 10.0) -> IntentExtraction:
    """构造含 5 岁娃同行人的 intent（学龄前主导）"""
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=[4, 6],
        distance_max_km=distance_max_km,
        companions=[Companion(role="孩子", age=5, count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="带 5 岁娃出去玩",
        parse_confidence=0.9,
    )


def _make_intent_with_senior(distance_max_km: float = 10.0) -> IntentExtraction:
    """含 70 岁外婆"""
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=[4, 6],
        distance_max_km=distance_max_km,
        companions=[Companion(role="外婆", age=78, count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="老人伴助",
        raw_input="带外婆出去",
        parse_confidence=0.9,
    )


def _make_intent_solo(distance_max_km: float = 10.0) -> IntentExtraction:
    """成人独处，不触发 age cap"""
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=[3, 5],
        distance_max_km=distance_max_km,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="独处放空",
        raw_input="一个人出去",
        parse_confidence=0.9,
    )


# ============================================================
# 测试 1：5 岁娃 + 投影后 > 90min → POI 被剔除
# ============================================================


def test_preschool_kid_filters_long_duration_poi():
    """5 岁娃 + POI suggested(kid_3_6=120) > 90min cap → 严过滤剔除"""
    intent = _make_intent_with_preschool()
    # 候选 5 个：1 个超 cap，4 个合规（确保过滤后仍 ≥ 3 不触发放宽）
    candidates = [
        _make_poi("P_LONG", suggested_default=180, suggested_kid_3_6=120),  # 120 > 90 cap
        _make_poi("P_OK_1", suggested_default=60, suggested_kid_3_6=60),
        _make_poi("P_OK_2", suggested_default=75, suggested_kid_3_6=75),
        _make_poi("P_OK_3", suggested_default=80, suggested_kid_3_6=80),
        _make_poi("P_OK_4", suggested_default=90, suggested_kid_3_6=90),  # = 90 不超
    ]
    tracer = Tracer()
    filtered = _grounding_filter_poi(candidates, intent, tracer)
    filtered_ids = {p.id for p in filtered}
    assert "P_LONG" not in filtered_ids, "P_LONG 投影 120min 超 90min cap 应被剔除"
    assert {"P_OK_1", "P_OK_2", "P_OK_3", "P_OK_4"}.issubset(filtered_ids)


# ============================================================
# 测试 2：70 岁外婆 + senior 桶 > 75min → 剔除
# ============================================================


def test_senior_companion_filters_long_duration_poi():
    """70 岁外婆 + POI suggested(senior=90) > 75min senior cap → 剔除"""
    intent = _make_intent_with_senior()
    candidates = [
        _make_poi("P_TIRING", suggested_default=120, suggested_senior=90),  # 90 > 75
        _make_poi("P_OK_1", suggested_default=60, suggested_senior=60),
        _make_poi("P_OK_2", suggested_default=75, suggested_senior=75),
        _make_poi("P_OK_3", suggested_default=70, suggested_senior=70),
    ]
    tracer = Tracer()
    filtered = _grounding_filter_poi(candidates, intent, tracer)
    filtered_ids = {p.id for p in filtered}
    assert "P_TIRING" not in filtered_ids, "P_TIRING senior 桶 90min 超 75min cap 应剔除"


# ============================================================
# 测试 3：候选池 < 3 触发放宽（仅距离 + 营业状态）
# ============================================================


def test_relaxation_when_pool_too_small():
    """5 岁娃 + 大部分候选都超 cap → 严过滤剔到 < 3 → 放宽（跳过 age cap）"""
    intent = _make_intent_with_preschool()
    # 4 个 POI，3 个 kid_3_6=120 超 cap，1 个合规
    candidates = [
        _make_poi("P_LONG_1", suggested_default=180, suggested_kid_3_6=120),
        _make_poi("P_LONG_2", suggested_default=180, suggested_kid_3_6=110),
        _make_poi("P_LONG_3", suggested_default=180, suggested_kid_3_6=100),
        _make_poi("P_OK_1", suggested_default=60, suggested_kid_3_6=60),
    ]
    tracer = Tracer()
    filtered = _grounding_filter_poi(candidates, intent, tracer)
    # 严过滤后只剩 1 个 < 3 → 放宽 → 4 个全部回到候选池（距离 / 营业状态都没问题）
    assert len(filtered) == 4, f"放宽后应回到 4 个候选，实际 {len(filtered)}"
    # tracer 应有 agent_thought 记录放宽
    relaxation_thoughts = [
        r for r in tracer.records
        if r.type == "agent_thought" and "放宽" in r.payload.get("text", "")
    ]
    assert relaxation_thoughts, "应 emit agent_thought 提示放宽机制触发"


# ============================================================
# 测试 4：距离超 +1km 容差 → 严过滤剔除
# ============================================================


def test_distance_exceeded_filtered():
    """成人独处 + distance_max_km=5，POI 距离 6.5km 超 (5+1=6km) → 剔除"""
    intent = _make_intent_solo(distance_max_km=5.0)
    candidates = [
        _make_poi("P_FAR", distance_km=6.5),  # 6.5 > 5+1
        _make_poi("P_OK_1", distance_km=4.0),
        _make_poi("P_OK_2", distance_km=4.5),
        _make_poi("P_OK_3", distance_km=5.5),  # 5.5 ≤ 5+1
    ]
    tracer = Tracer()
    filtered = _grounding_filter_poi(candidates, intent, tracer)
    filtered_ids = {p.id for p in filtered}
    assert "P_FAR" not in filtered_ids
    assert {"P_OK_1", "P_OK_2", "P_OK_3"}.issubset(filtered_ids)


# ============================================================
# 测试 5：business_status=closed 剔除
# ============================================================


def test_closed_poi_filtered():
    """business_status="closed" → 严过滤剔除（即使距离 / age cap 都满足）"""
    intent = _make_intent_solo()
    candidates = [
        _make_poi("P_CLOSED", business_status="closed"),
        _make_poi("P_OK_1"),
        _make_poi("P_OK_2"),
        _make_poi("P_OK_3"),
    ]
    tracer = Tracer()
    filtered = _grounding_filter_poi(candidates, intent, tracer)
    filtered_ids = {p.id for p in filtered}
    assert "P_CLOSED" not in filtered_ids
    assert len(filtered) == 3


# ============================================================
# 测试 6：tracer.emit("grounding_filtered") 记录正确
# ============================================================


def test_tracer_emits_grounding_filtered_events():
    """每个被剔除的候选 emit 一条 grounding_filtered（含 poi_id + reason）"""
    intent = _make_intent_with_preschool()
    candidates = [
        _make_poi("P_LONG", suggested_default=180, suggested_kid_3_6=120),
        _make_poi("P_OK_1", suggested_default=60, suggested_kid_3_6=60),
        _make_poi("P_OK_2", suggested_default=75, suggested_kid_3_6=75),
        _make_poi("P_OK_3", suggested_default=80, suggested_kid_3_6=80),
    ]
    tracer = Tracer()
    _grounding_filter_poi(candidates, intent, tracer)
    grounding_events = [
        r for r in tracer.records if r.type == "grounding_filtered"
    ]
    assert len(grounding_events) == 1, f"应剔除 1 个，实际 {len(grounding_events)}"
    payload = grounding_events[0].payload
    assert payload.get("poi_id") == "P_LONG"
    assert "reason" in payload
    reason = payload["reason"]
    assert "120min" in reason and "90min cap" in reason


# ============================================================
# 测试 7：餐厅 grounding-first（距离 + 营业，无 age cap）
# ============================================================


def test_restaurant_grounding_filters_distance_only():
    """餐厅过滤仅看距离 + 营业，不看 age cap"""
    intent = _make_intent_with_preschool(distance_max_km=4.0)
    candidates = [
        _make_restaurant("R_FAR", distance_km=6.0),  # 6 > 4+1=5
        _make_restaurant("R_OK_1", distance_km=3.0),
        _make_restaurant("R_OK_2", distance_km=4.5),  # 4.5 ≤ 4+1
        _make_restaurant("R_OK_3", distance_km=2.0),
    ]
    tracer = Tracer()
    filtered = _grounding_filter_restaurant(candidates, intent, tracer)
    filtered_ids = {r.id for r in filtered}
    assert "R_FAR" not in filtered_ids
    assert {"R_OK_1", "R_OK_2", "R_OK_3"}.issubset(filtered_ids)


def test_restaurant_grounding_keeps_full_for_critic():
    """餐厅满座（available_slots=0）不在 grounding 层剔除——满座由 critic 处理

    设计：保留满座候选让 17:00 → 17:30 替换链路被评委看到（demo 异常韧性）
    """
    intent = _make_intent_solo()
    # 即使满座也保留（grounding 不看 reservation_slots）
    rest = _make_restaurant("R_FULL", distance_km=2.0)
    rest_open = _make_restaurant("R_OPEN", distance_km=2.5)
    rest_2 = _make_restaurant("R_2", distance_km=3.0)
    tracer = Tracer()
    filtered = _grounding_filter_restaurant([rest, rest_open, rest_2], intent, tracer)
    filtered_ids = {r.id for r in filtered}
    assert "R_FULL" in filtered_ids, "满座餐厅不应被 grounding 剔除（由 critic 处理）"


# ============================================================
# 测试 8：空候选池向后兼容
# ============================================================


def test_empty_candidates_returns_empty():
    """空候选 → 返空（不崩、不放宽）"""
    intent = _make_intent_solo()
    tracer = Tracer()
    assert _grounding_filter_poi([], intent, tracer) == []
    assert _grounding_filter_restaurant([], intent, tracer) == []


# ============================================================
# 测试 9：solo 场景不触发 age cap（只过滤距离 / 营业）
# ============================================================


def test_solo_intent_no_age_cap_applied():
    """成人独处 → 没 age 同行人 → POI 即使 default=200min 也不剔除"""
    intent = _make_intent_solo()
    candidates = [
        _make_poi("P_LONG", suggested_default=200),  # 即使长也不剔
        _make_poi("P_OK_1"),
        _make_poi("P_OK_2"),
    ]
    tracer = Tracer()
    filtered = _grounding_filter_poi(candidates, intent, tracer)
    filtered_ids = {p.id for p in filtered}
    assert "P_LONG" in filtered_ids, "无年龄约束时不应触发 age cap"
