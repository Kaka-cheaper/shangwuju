"""tests.test_decision_trace —— Step 4：决策可解释性 schema。

覆盖：
1. DecisionTrace 默认空时 is_empty() = True
2. 各子组件 CriticAttempt / AlternativeCandidate / FallbackHop 字段约束
3. Itinerary 加载老格式（无 decision_trace 字段）向后兼容
4. Itinerary 加载新格式（含 decision_trace）字段正确
5. DecisionTrace 转 dict 后 SSE payload 友好
"""

from __future__ import annotations

import pytest

from schemas.decision_trace import (
    AlternativeCandidate,
    CriticAttempt,
    DecisionTrace,
    FallbackHop,
)
from schemas.itinerary import Itinerary, ItineraryStage


# ============================================================
# 子组件
# ============================================================

def test_critic_attempt_basic():
    a = CriticAttempt(
        attempt_n=2,
        violation_codes=["commute_infeasible", "duration_out_of_range"],
        feedback_summary="第 5 段开始时间不足以走完通勤；总时长超过用户上限",
        resolved=True,
    )
    assert a.attempt_n == 2
    assert a.resolved is True
    assert "commute_infeasible" in a.violation_codes


def test_critic_attempt_attempt_n_must_be_positive():
    with pytest.raises(Exception):
        CriticAttempt(
            attempt_n=0,
            violation_codes=[],
            feedback_summary="x",
        )


def test_alternative_candidate_required_reason():
    """reason_rejected 必填，不允许空。"""
    ac = AlternativeCandidate(
        target_kind="restaurant",
        target_id="R005",
        target_name="某家餐厅",
        utility_score=0.72,
        rank=2,
        reason_rejected="距离更远（4.8km vs 选中候选 0.6km）",
    )
    assert ac.rank == 2
    assert "距离" in ac.reason_rejected


def test_alternative_candidate_rank_must_be_positive():
    with pytest.raises(Exception):
        AlternativeCandidate(
            target_kind="poi",
            target_id="P001",
            target_name="x",
            rank=0,
            reason_rejected="x",
        )


def test_fallback_hop_basic():
    h = FallbackHop(
        from_stage="llm_first",
        to_stage="ils",
        reason="LLM 三次未通过 critic，切 ILS 算法兜底",
    )
    assert h.from_stage == "llm_first"
    assert h.to_stage == "ils"


# ============================================================
# DecisionTrace 主体
# ============================================================

def test_decision_trace_default_is_empty():
    """默认构造的 DecisionTrace 应该 is_empty() = True。"""
    dt = DecisionTrace()
    assert dt.is_empty() is True
    assert dt.final_strategy == "llm_first"


def test_decision_trace_not_empty_when_filled():
    dt = DecisionTrace(
        blueprint_rationale="用户家庭日常 + 5 岁孩 → 选亲子友好 + 低脂的组合",
        weights_explanation="重舒适 0.35 / 重时长 0.30 / 重花销 0.20 / 重顺滑 0.15",
    )
    assert dt.is_empty() is False


def test_decision_trace_serialize_roundtrip():
    """trace 转 dict 后能回来。"""
    dt = DecisionTrace(
        blueprint_rationale="rationale",
        weights_explanation="weights",
        critic_attempts=[
            CriticAttempt(
                attempt_n=1,
                violation_codes=["commute_infeasible"],
                feedback_summary="第 5 段需要 15 分钟通勤",
                resolved=True,
            ),
        ],
        alternatives_considered=[
            AlternativeCandidate(
                target_kind="restaurant",
                target_id="R005",
                target_name="某餐厅",
                rank=2,
                reason_rejected="距离更远",
            ),
        ],
        fallback_chain=[
            FallbackHop(
                from_stage="llm_first",
                to_stage="llm_backprompt",
                reason="critic 命中违规",
            ),
        ],
        final_strategy="llm_backprompt",
    )
    dumped = dt.model_dump()
    restored = DecisionTrace.model_validate(dumped)
    assert restored.final_strategy == "llm_backprompt"
    assert len(restored.critic_attempts) == 1
    assert len(restored.alternatives_considered) == 1


# ============================================================
# Itinerary 兼容性
# ============================================================

def test_itinerary_without_decision_trace_loads_ok():
    """旧客户端不提供 decision_trace 仍合法（向后兼容）。"""
    itin = Itinerary(
        summary="测试",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:30", title="出发"),
            ItineraryStage(kind="主活动", start="14:30", end="16:00", title="活动"),
            ItineraryStage(kind="转场", start="16:00", end="16:30", title="转场"),
            ItineraryStage(kind="用餐", start="16:30", end="17:30", title="用餐"),
            ItineraryStage(kind="返回", start="17:30", end="18:00", title="回家"),
        ],
        total_minutes=240,
    )
    assert itin.decision_trace is None


def test_itinerary_with_decision_trace_loads_ok():
    dt = DecisionTrace(
        blueprint_rationale="测试",
        final_strategy="llm_first",
    )
    itin = Itinerary(
        summary="测试",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:30", title="出发"),
            ItineraryStage(kind="主活动", start="14:30", end="16:00", title="活动"),
            ItineraryStage(kind="转场", start="16:00", end="16:30", title="转场"),
            ItineraryStage(kind="用餐", start="16:30", end="17:30", title="用餐"),
            ItineraryStage(kind="返回", start="17:30", end="18:00", title="回家"),
        ],
        total_minutes=240,
        decision_trace=dt,
    )
    assert itin.decision_trace is not None
    assert itin.decision_trace.blueprint_rationale == "测试"


def test_itinerary_dump_includes_decision_trace_field():
    """model_dump() 含 decision_trace 字段（即使为 None）。"""
    itin = Itinerary(
        summary="x",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:30", title="出发"),
            ItineraryStage(kind="主活动", start="14:30", end="16:00", title="活动"),
            ItineraryStage(kind="转场", start="16:00", end="16:30", title="转场"),
            ItineraryStage(kind="用餐", start="16:30", end="17:30", title="用餐"),
            ItineraryStage(kind="返回", start="17:30", end="18:00", title="回家"),
        ],
        total_minutes=240,
    )
    dumped = itin.model_dump()
    assert "decision_trace" in dumped
    assert dumped["decision_trace"] is None
