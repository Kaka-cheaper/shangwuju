"""tests.test_planner_hybrid —— A+C 混合规划范式回归测试。

W2 owner = A 同学的 planner.py 主体；本测试是 owner=A 的 P2 加分项扩展，
仅在 PLANNER_LLM_STRATEGY=hybrid（默认）路径上跑。

覆盖维度：
1. weights_llm 启发式兜底正确性（按 social_context 给出合理权重）
2. critics 4 个 Critic 的硬/软违规分流
3. utility 函数对距离 / 评分 / cost 的敏感性
4. 端到端：构造 mock LLM client → plan_itinerary_with_mode("llm") → hybrid 路径
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent.critics import (
    CriticReport,
    CriticViolation,
    run_critics,
)
from agent.planner import plan_itinerary_with_mode
from agent.weights_llm import (
    PlanningWeights,
    _heuristic_weights,
    get_planning_weights,
)
from schemas.intent import Companion, IntentExtraction
from schemas.itinerary import Itinerary, ItineraryStage


# ============================================================
# 共享：构造 IntentExtraction
# ============================================================

def _intent(
    *,
    social_context: str = "家庭日常",
    distance_max_km: float = 5.0,
    physical: tuple[str, ...] = ("亲子友好", "适合 5-10 岁"),
    dietary: tuple[str, ...] = ("低脂", "健康轻食"),
    companions: tuple[Companion, ...] = (
        Companion(role="妻子", count=1),
        Companion(role="孩子", age=5, count=1),
    ),
    raw_input: str = "今天下午想和老婆孩子出去玩几个小时",
    capacity: int | None = None,
) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=distance_max_km,
        companions=list(companions),
        physical_constraints=list(physical),
        dietary_constraints=list(dietary),
        experience_tags=[],
        social_context=social_context,
        capacity_requirement=capacity,
        raw_input=raw_input,
        parse_confidence=0.85,
        ambiguous_fields=[],
    )


# ============================================================
# 1. weights_llm 启发式兜底
# ============================================================

def test_heuristic_weights_family_emphasizes_comfort():
    """家庭场景：comfort 应是最高的维度。"""
    w = _heuristic_weights(_intent(social_context="家庭日常"))
    assert w.source == "stub"
    assert pytest.approx(w.comfort + w.time + w.cost + w.smoothness, abs=0.01) == 1.0
    assert w.comfort >= max(w.time, w.cost, w.smoothness), (
        f"家庭场景 comfort 应最高：{w.summary()}"
    )


def test_heuristic_weights_business_emphasizes_time():
    """商务接待：time 高、cost 极低（公司报销）。"""
    w = _heuristic_weights(_intent(social_context="商务接待"))
    assert w.time >= max(w.comfort, w.cost), f"商务场景 time 应较高：{w.summary()}"
    assert w.cost <= 0.15


def test_heuristic_weights_old_companion_boosts_comfort():
    """同行老人：comfort 应被进一步提升。"""
    base = _heuristic_weights(_intent(social_context="家庭日常"))
    with_old = _heuristic_weights(
        _intent(
            social_context="家庭日常",
            companions=(
                Companion(role="外公", age=70, count=1, is_special_role=True),
                Companion(role="外婆", age=68, count=1, is_special_role=True),
            ),
        )
    )
    assert with_old.comfort >= base.comfort, "同行老人时 comfort 应不低于基线"


def test_heuristic_weights_keyword_cost_boost():
    """raw_input 含「便宜」「学生」→ cost 权重上调。"""
    base = _heuristic_weights(_intent(raw_input="今天下午带朋友吃饭"))
    boosted = _heuristic_weights(_intent(raw_input="便宜一点的店，我们都是学生"))
    assert boosted.cost > base.cost


def test_get_planning_weights_falls_back_to_heuristic_when_no_client():
    """client=None 时走启发式兜底。"""
    w = get_planning_weights(_intent(), client=None)
    assert w.source == "stub"
    assert pytest.approx(w.comfort + w.time + w.cost + w.smoothness, abs=0.01) == 1.0


def test_planning_weights_normalize_zero_input():
    """全 0 权重应被兜底为合理分布。"""
    w = PlanningWeights(comfort=0, time=0, cost=0, smoothness=0).normalize()
    assert pytest.approx(w.comfort + w.time + w.cost + w.smoothness, abs=0.01) == 1.0


# ============================================================
# 2. 4 个 Critic 的硬/软违规分流
# ============================================================

def _itinerary(
    *,
    stages_kinds: tuple[str, ...] = ("出发", "主活动", "转场", "用餐", "返回"),
    poi_id: str = "P001",
    restaurant_id: str = "R001",
    dining_time: str = "17:30",
    total_minutes: int = 280,
) -> Itinerary:
    """构造合法 Itinerary 占位。"""
    # 按 kind 给每段静态时间；用餐段 start 用 dining_time
    base_times = {
        "出发": ("14:00", "14:25"),
        "主活动": ("14:25", "16:25"),
        "转场": ("16:25", dining_time),
        "用餐": (dining_time, "19:00"),
        "返回": ("19:00", "19:10"),
        "附加": ("19:10", "20:00"),
    }
    stages: list[ItineraryStage] = []
    for k in stages_kinds:
        start, end = base_times.get(k, ("12:00", "12:30"))
        stages.append(
            ItineraryStage(
                kind=k,
                start=start,
                end=end,
                title=f"测试段-{k}",
                poi_id=poi_id if k in ("出发", "主活动") else None,
                restaurant_id=restaurant_id if k == "用餐" else None,
            )
        )
    return Itinerary(
        summary="测试方案",
        stages=stages,
        orders=[],
        share_message=None,
        total_minutes=total_minutes,
    )


def test_critic_passes_clean_plan():
    """合法 plan：R001 17:30 是可订时段（mock 数据）。"""
    intent = _intent()
    plan = _itinerary(restaurant_id="R001", dining_time="17:30")
    report = run_critics(plan, intent)
    assert report.passed, f"合法 plan 应通过；违规：{[v.message for v in report.violations]}"


def test_critic_catches_unavailable_slot():
    """R001 17:00 在 mock 数据是 available=false → 应硬违规。"""
    intent = _intent()
    plan = _itinerary(restaurant_id="R001", dining_time="17:00")
    report = run_critics(plan, intent)
    assert not report.passed
    msgs = [v.message for v in report.hard_violations()]
    assert any("17:00" in m and "已满" in m for m in msgs), msgs


def test_critic_catches_missing_stage():
    """缺「用餐」段 → 硬违规（hard_constraint 段缺失）。"""
    intent = _intent()
    plan = _itinerary(stages_kinds=("出发", "主活动", "转场", "返回"))
    report = run_critics(plan, intent)
    assert not report.passed
    assert any(
        v.critic == "hard_constraint" and "缺失" in v.message
        for v in report.violations
    )


def test_critic_style_soft_violation_for_mismatched_context():
    """商务场景但用了家庭餐厅 R001 → 软违规（不阻断）。"""
    intent = _intent(social_context="商务接待")
    plan = _itinerary(restaurant_id="R001", dining_time="17:30")
    report = run_critics(plan, intent)
    style_violations = [v for v in report.violations if v.critic == "style"]
    assert any(v.severity == "soft" for v in style_violations)
    # soft 不卡 passed，但 soft_score 会被扣
    assert report.soft_score < 1.0


def test_critic_total_minutes_overflow_hard():
    """总耗时显著超过用户 [3,5]h 上限 → 硬违规。"""
    intent = _intent()  # 最大 5h = 300min
    plan = _itinerary(total_minutes=400)  # 400 > 300+30 容忍
    report = run_critics(plan, intent)
    assert not report.passed


# ============================================================
# 3. utility 函数敏感性
# ============================================================

def test_utility_distance_decreases_with_far_pois():
    """远 POI 的 utility 应低于近 POI。"""
    from agent.planner_hybrid import _utility
    from schemas.domain import Location, Poi, PoiCapacity, Restaurant, RestaurantCapacity

    rest = Restaurant(
        id="R001",
        name="测试餐厅",
        cuisine="健康轻食",
        location=Location(name="近"),
        distance_km=2.0,
        opening_hours="10:00-22:00",
        avg_price=80,
        rating=4.5,
        capacity=RestaurantCapacity.model_validate(
            {"2": True, "4": True, "6": False, "8": False, "private_room": False}
        ),
        reservation_slots=[],
        tags=["低脂", "健康轻食"],
        suitable_for=["家庭日常"],
    )
    near_poi = Poi(
        id="P_NEAR",
        name="近的",
        type="亲子",
        location=Location(name="A"),
        distance_km=2.0,
        opening_hours="09:00-18:00",
        rating=4.5,
        tags=["亲子友好", "适合 5-10 岁"],
        suitable_for=["家庭日常"],
        capacity=PoiCapacity(daily_quota=100, available_slots=50),
    )
    far_poi = near_poi.model_copy(update={"id": "P_FAR", "distance_km": 9.0})

    intent = _intent()
    w = _heuristic_weights(intent)
    near_score, _ = _utility(near_poi, rest, "17:30", intent, w)
    far_score, _ = _utility(far_poi, rest, "17:30", intent, w)
    assert near_score > far_score, (
        f"近 POI utility 应高于远 POI：near={near_score:.3f} far={far_score:.3f}"
    )


# ============================================================
# 4. 端到端 Mock LLM client 跑 hybrid 路径
# ============================================================

@dataclass
class _MockLLMResponse:
    content: str
    tool_calls: list = None  # type: ignore[assignment]
    finish_reason: str = "stop"
    raw: dict = None  # type: ignore[assignment]


class _MockLLMClient:
    """模拟非 stub 的 LLM client，触发 hybrid 路径。"""

    provider = "mock"
    model = "mock-model"

    def __init__(self, weights_json: str):
        self._weights_json = weights_json

    def chat(self, messages, *, temperature=0.3, response_format=None):
        return _MockLLMResponse(content=self._weights_json)


def test_hybrid_end_to_end_with_mock_client():
    """hybrid 路径整链路：mock LLM → 权重 → ILS → Critic → 出方案。"""
    intent = _intent()
    client = _MockLLMClient(
        weights_json=(
            '{"comfort": 0.5, "time": 0.2, "cost": 0.15, "smoothness": 0.15, '
            '"rationale": "家庭场景重舒适"}'
        ),
    )
    result = plan_itinerary_with_mode(intent, "llm", llm_client=client)
    assert result.success, (
        f"hybrid 应成功；失败原因：{result.failure_detail}"
    )
    assert result.itinerary is not None

    # 必备段都在
    kinds = {s.kind for s in result.itinerary.stages}
    for k in ("出发", "主活动", "转场", "用餐", "返回"):
        assert k in kinds, f"缺段 {k}"

    # Trace 含 hybrid 标志：weights agent_thought + Critic agent_thought
    thoughts = [r for r in result.tracer.records if r.type == "agent_thought"]
    assert any(
        "权重" in t.payload.get("text", "") for t in thoughts
    ), "应含权重相关 agent_thought"


def test_hybrid_falls_back_to_rule_when_llm_returns_garbage():
    """LLM 返回非法 JSON → weights_llm 兜底启发式 → hybrid 仍能跑通。"""
    intent = _intent()
    client = _MockLLMClient(weights_json="<<<这不是 JSON>>>")
    result = plan_itinerary_with_mode(intent, "llm", llm_client=client)
    assert result.success
    # 启发式兜底标记 source=fallback
    weights_thought = next(
        (
            r for r in result.tracer.records
            if r.type == "agent_thought" and "权重" in r.payload.get("text", "")
        ),
        None,
    )
    assert weights_thought is not None


def test_hybrid_uses_stub_client_falls_back_to_rule():
    """stub client 走 rule 兼容路径（避免引入回归）。"""
    from agent.llm_client_stub import StubLLMClient

    intent = _intent()
    result = plan_itinerary_with_mode(intent, "llm", llm_client=StubLLMClient())
    assert result.success
    fallback_msgs = [
        r for r in result.tracer.records
        if r.type == "agent_thought" and "规则" in r.payload.get("text", "")
    ]
    assert fallback_msgs, "stub client 应走 rule 兼容路径"
