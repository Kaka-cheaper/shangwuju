"""tests.test_decision_trace —— Step 4：决策可解释性 schema（edge_v1）。

覆盖：
1. DecisionTrace 默认空时 is_empty() = True
2. 各子组件 CriticAttempt / AlternativeCandidate / FallbackHop 字段约束
3. Itinerary 加载老格式（无 decision_trace 字段）向后兼容
4. Itinerary 加载新格式（含 decision_trace）字段正确
5. DecisionTrace 转 dict 后 SSE payload 友好

【edge_v1 字段路径迁移（Wave 7 Task 14）】

旧测试用 `ItineraryStage` 手工拼 5 段构造合法 Itinerary。edge_v1 起 schema 改为
`ActivityNode + Hop`，且 model_validator 强制不变量（hops 长度 = nodes-1 / 首尾 home /
home duration=0），手工拼极易触发 ValidationError。

本文件改用 `assemble_from_blueprint(intent, PlanBlueprint(nodes=[...]), user_profile)`
统一构造合法 Itinerary，把不变量交给 assemble 保证。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# ============================================================
# 桥接：绕过 agent/__init__.py 副作用 import
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent import lookup_hop as _lookup_hop_mod  # noqa: E402
from agent.assemble_blueprint import assemble_from_blueprint  # noqa: E402
from agent.blueprint import (  # noqa: E402
    BlueprintNode,
    BlueprintTargetKind,
    PlanBlueprint,
)
from data.loader import load_user_profile  # noqa: E402
from schemas.decision_trace import (  # noqa: E402
    AlternativeCandidate,
    CriticAttempt,
    DecisionTrace,
    FallbackHop,
)
from schemas.intent import Companion, IntentExtraction  # noqa: E402
from schemas.itinerary import Itinerary  # noqa: E402


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(autouse=True)
def _reset_lookup_cache():
    _lookup_hop_mod.reset_cache()
    yield
    _lookup_hop_mod.reset_cache()


def _intent() -> IntentExtraction:
    """构造极简意图（assemble 当前不读它，仅作签名占位）。"""
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="测试",
        parse_confidence=0.9,
    )


def _build_minimal_itinerary(*, decision_trace: DecisionTrace | None = None) -> Itinerary:
    """通过 assemble_from_blueprint 拼装最小合法 Itinerary（用于 decision_trace 字段测试）。

    使用 mock_data 中已有的 P040（亲子博物馆）+ R001（轻语沙拉），
    routes.json 已配齐这条链路（home↔P040↔R001↔home）。
    """
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
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
        rationale="测试用最小蓝图",
    )
    itin = assemble_from_blueprint(_intent(), bp, load_user_profile())
    if decision_trace is not None:
        # Pydantic 不可变默认值时直接 model_copy 注入
        itin = itin.model_copy(update={"decision_trace": decision_trace})
    return itin


# ============================================================
# 子组件
# ============================================================

def test_critic_attempt_basic():
    a = CriticAttempt(
        attempt_n=2,
        violation_codes=["hop_infeasible", "duration_out_of_range"],
        feedback_summary="hop 时间不足以走完通勤；总时长超过用户上限",
        resolved=True,
    )
    assert a.attempt_n == 2
    assert a.resolved is True
    assert "hop_infeasible" in a.violation_codes


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
                violation_codes=["hop_infeasible"],
                feedback_summary="hop 时间不足以走完通勤",
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
# Itinerary 兼容性（edge_v1 schema）
# ============================================================

def test_itinerary_without_decision_trace_loads_ok():
    """旧客户端不提供 decision_trace 仍合法（向后兼容）。"""
    itin = _build_minimal_itinerary()
    assert itin.decision_trace is None
    # edge_v1 不变量自检
    assert itin.schema_version == "edge_v1"
    assert len(itin.hops) == len(itin.nodes) - 1
    assert itin.nodes[0].target_kind == "home"
    assert itin.nodes[-1].target_kind == "home"


def test_itinerary_with_decision_trace_loads_ok():
    dt = DecisionTrace(
        blueprint_rationale="测试",
        final_strategy="llm_first",
    )
    itin = _build_minimal_itinerary(decision_trace=dt)
    assert itin.decision_trace is not None
    assert itin.decision_trace.blueprint_rationale == "测试"


def test_itinerary_dump_includes_decision_trace_field():
    """model_dump() 含 decision_trace 字段（即使为 None）。"""
    itin = _build_minimal_itinerary()
    dumped = itin.model_dump()
    assert "decision_trace" in dumped
    assert dumped["decision_trace"] is None
    # edge_v1 字段也应在 dump 里
    assert "nodes" in dumped
    assert "hops" in dumped
    assert "schedule" in dumped
    assert dumped["schema_version"] == "edge_v1"
