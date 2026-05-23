"""tests.test_decision_trace_integration —— Step 8：DecisionTrace 在 LangGraph 中的注入（edge_v1）。

覆盖：
1. planner_node 写 alternatives_considered（dict 形式）
2. critic_node 累积 critic_attempts（dict 形式）+ resolved 标记
3. replan_router_node 累积 fallback_chain
4. assemble_node 把所有累积字段聚合到 itinerary.decision_trace
5. 一次性通过场景：trace 有 weights / blueprint_rationale，无 critic_attempts / fallback

不调真 LLM；用最小 stub 蓝图。

【edge_v1 迁移（Wave 7 Task 14）】

旧测试构造 5 段 BlueprintStage 蓝图（含 BlueprintTargetKind.NONE 过程段）。edge_v1 起：
- BlueprintStage → BlueprintNode（仅 mid nodes，删除 NONE 过程段）
- PlanBlueprint.stages → PlanBlueprint.nodes
- BlueprintTargetKind 仅保留 POI / RESTAURANT
- assemble_node 自动补 home 首尾节点 + 自动算 hops
"""

from __future__ import annotations

import pytest

from agent.blueprint import BlueprintNode, BlueprintTargetKind, PlanBlueprint
from agent.graph.nodes.assemble import assemble_node
from agent.graph.nodes.planner import _build_alternatives
from agent.graph.state import make_initial_state
from agent.weights_llm import PlanningWeights
from data.loader import load_pois, load_restaurants, reset_cache
from schemas.intent import IntentExtraction


def _basic_intent() -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=[4, 6],  # type: ignore[arg-type]
        distance_max_km=10.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="测试",
        parse_confidence=0.9,
    )


def _stub_blueprint() -> PlanBlueprint:
    """构造一个简单合法的 edge_v1 蓝图（仅 mid nodes，不含首尾 home 与过程段）。"""
    return PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P011",
                duration_min=120,
            ),
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
        preferred_start_time="14:00",
        rationale="家庭日常 + 5 岁孩 → P011 亲子 + R001 健康轻食",
    )


# ============================================================
# planner._build_alternatives
# ============================================================

def test_build_alternatives_excludes_selected():
    """选中的 target_id 不进 alternatives。"""
    reset_cache()
    pois = load_pois()
    rests = load_restaurants()
    bp = _stub_blueprint()  # 选中 P011 + R001

    alternatives = _build_alternatives(bp, pois, rests)
    selected = {"P011", "R001"}
    for alt in alternatives:
        assert alt["target_id"] not in selected, (
            f"已选中 {alt['target_id']} 不应进 alternatives"
        )


def test_build_alternatives_returns_dicts_with_required_fields():
    """alternatives 返回 dict（避免 Pydantic 跨节点序列化问题）。"""
    pois = load_pois()
    rests = load_restaurants()
    bp = _stub_blueprint()

    alts = _build_alternatives(bp, pois, rests)
    assert len(alts) >= 2
    for a in alts:
        assert "target_kind" in a
        assert "target_id" in a
        assert "rank" in a
        assert "reason_rejected" in a
        assert a["rank"] >= 2  # rank=1 = 已选中


# ============================================================
# assemble_node 注入 DecisionTrace
# ============================================================

def test_assemble_injects_trace_with_rationale_and_weights():
    """assemble_node 应该把 blueprint.rationale + weights summary 写入 trace。"""
    intent = _basic_intent()
    bp = _stub_blueprint()
    weights = PlanningWeights(
        comfort=0.4, time=0.2, cost=0.2, smoothness=0.2, source="stub"
    )

    state = make_initial_state(user_input="测试", session_id="s1")
    state["intent"] = intent
    state["blueprint"] = bp
    state["weights"] = weights

    result = assemble_node(state)
    itin = result.get("itinerary")
    assert itin is not None
    assert itin.decision_trace is not None
    rationale = itin.decision_trace.blueprint_rationale or ""
    assert "P011" in rationale or "亲子" in rationale
    assert itin.decision_trace.weights_explanation  # 非空
    # 默认无 critic 命中
    assert itin.decision_trace.critic_attempts == []
    assert itin.decision_trace.fallback_chain == []
    assert itin.decision_trace.final_strategy == "llm_first"

    # edge_v1 不变量自检：assemble 应已补 home 首尾 + 自动 hops
    assert itin.schema_version == "edge_v1"
    assert len(itin.hops) == len(itin.nodes) - 1
    assert itin.nodes[0].target_kind == "home"
    assert itin.nodes[-1].target_kind == "home"


def test_assemble_includes_critic_attempts_and_fallback():
    """state 已有 critic_attempts + fallback_chain → 注入到 trace。"""
    intent = _basic_intent()
    bp = _stub_blueprint()
    weights = PlanningWeights(
        comfort=0.4, time=0.2, cost=0.2, smoothness=0.2, source="llm"
    )

    state = make_initial_state(user_input="测试", session_id="s1")
    state["intent"] = intent
    state["blueprint"] = bp
    state["weights"] = weights
    state["critic_attempts"] = [
        {
            "attempt_n": 1,
            "violation_codes": ["hop_infeasible"],
            "feedback_summary": "hop 时间不足以走完通勤",
            "resolved": True,
        }
    ]
    state["fallback_chain"] = [
        {
            "from_stage": "llm_first",
            "to_stage": "llm_backprompt",
            "reason": "critic 命中",
        }
    ]
    state["replan_strategy"] = "llm_backprompt"

    result = assemble_node(state)
    itin = result.get("itinerary")
    assert itin is not None
    trace = itin.decision_trace
    assert trace is not None
    assert len(trace.critic_attempts) == 1
    assert trace.critic_attempts[0].resolved is True
    assert len(trace.fallback_chain) == 1
    assert trace.final_strategy == "llm_backprompt"


def test_assemble_alternatives_in_trace():
    """alternatives 字段也注入。"""
    intent = _basic_intent()
    bp = _stub_blueprint()
    weights = PlanningWeights(
        comfort=0.4, time=0.2, cost=0.2, smoothness=0.2, source="stub"
    )

    state = make_initial_state(user_input="测试", session_id="s1")
    state["intent"] = intent
    state["blueprint"] = bp
    state["weights"] = weights
    state["alternatives"] = [
        {
            "target_kind": "poi",
            "target_id": "P008",
            "target_name": "测试 POI",
            "utility_score": 0.9,
            "rank": 2,
            "reason_rejected": "评分较低",
        },
    ]

    result = assemble_node(state)
    itin = result["itinerary"]
    assert itin.decision_trace is not None
    assert len(itin.decision_trace.alternatives_considered) == 1
    assert itin.decision_trace.alternatives_considered[0].target_id == "P008"


def test_assemble_no_blueprint_returns_no_itinerary():
    """blueprint=None 时应返 itinerary=None（不报错）。"""
    intent = _basic_intent()
    state = make_initial_state(user_input="测试", session_id="s1")
    state["intent"] = intent
    # 不设 blueprint
    result = assemble_node(state)
    assert result["itinerary"] is None
