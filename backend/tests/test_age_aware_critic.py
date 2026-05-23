"""验 spec planning-quality-deep-review R4 年龄感知 critic 双路径镜像。

测试矩阵：
1. 5 岁娃 90min POI → blueprint critic 命中（cap 75 + expected_range 60-75）
2. 70 岁老人 75min POI → blueprint critic 命中（cap 60 + expected_range 45-60）
3. 多代际（5 岁娃 + 78 岁老人）→ 取最严（cap 60，含 ≤6 优先 75，再被 senior 60 压最严）
4. 无 age 时 critic 降级 no-op
5. format_violations_for_llm 拼自然语言「建议范围 X-Y min」+ 不暴露字段名
6. blueprint critic 与 critics_v2 镜像等价（同输入 → 都命中或都不命中）
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


def _install_agent_stub() -> None:
    """复用既有 agent 命名空间桥（避免 agent/__init__.py eager-import 老 schema 炸）。"""
    backend_root = Path(__file__).resolve().parent.parent
    agent_dir = backend_root / "agent"
    v2_dir = agent_dir / "v2"

    if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
        stub = types.ModuleType("agent")
        stub.__path__ = [str(agent_dir)]
        sys.modules["agent"] = stub
    if "agent.v2" not in sys.modules or not hasattr(sys.modules["agent.v2"], "__path__"):
        v2_stub = types.ModuleType("agent.v2")
        v2_stub.__path__ = [str(v2_dir)]
        sys.modules["agent.v2"] = v2_stub


_install_agent_stub()

from agent.planning.blueprint.blueprint import (  # noqa: E402
    BlueprintNode,
    BlueprintTargetKind,
    PlanBlueprint,
    _age_aware_duration_critic,
    _resolve_age_caps,
    run_blueprint_critics,
)
from agent.planning.critic.critics_v2 import (  # noqa: E402
    Severity,
    ViolationCode,
    _check_age_aware_duration,
    format_violations_for_llm,
    Violation,
)
from schemas.intent import Companion, IntentExtraction  # noqa: E402
from schemas.itinerary import ActivityNode, Itinerary, Hop  # noqa: E402


def _make_intent(companions: list[Companion]) -> IntentExtraction:
    return IntentExtraction(
        raw_input="测试",
        social_context="家庭日常",
        companions=companions,
        duration_hours=[3, 4],
        distance_max_km=5.0,
        start_time="14:00",
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        parse_confidence=0.95,
    )


def _make_blueprint(target_id: str, duration: int, kind: str = "看展") -> PlanBlueprint:
    return PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind=kind,
                target_kind=BlueprintTargetKind.POI,
                target_id=target_id,
                duration_min=duration,
            )
        ],
        preferred_start_time="14:00",
        rationale="test",
    )


# ============================================================
# 1) blueprint critic：5 岁娃 90min 命中
# ============================================================


def test_blueprint_critic_5yo_90min_hits() -> None:
    intent = _make_intent([Companion(role="孩子", age=5)])
    bp = _make_blueprint("P003", 90)

    violations = _age_aware_duration_critic(bp, intent)
    assert len(violations) == 1
    v = violations[0]
    assert v.critic == "blueprint_age_aware_duration"
    assert v.severity == "hard"
    assert v.expected_range == (60, 75)  # max(45, 75-15)=60, hi=75
    assert "5 岁" in v.message and "90" in v.message


# ============================================================
# 2) blueprint critic：70 岁老人 75min（不命中——cap 是 60-74 不约束）
# ============================================================


def test_blueprint_critic_70yo_does_not_hit() -> None:
    """70 岁不在硬 cap 区间（≥75 才触发），75min 不命中。"""
    intent = _make_intent([Companion(role="父母", age=70)])
    bp = _make_blueprint("P003", 75)
    violations = _age_aware_duration_critic(bp, intent)
    assert violations == []


def test_blueprint_critic_78yo_75min_hits() -> None:
    """78 岁老人 75min → cap 60 命中。"""
    intent = _make_intent([Companion(role="父母", age=78)])
    bp = _make_blueprint("P003", 75)
    violations = _age_aware_duration_critic(bp, intent)
    assert len(violations) == 1
    assert violations[0].expected_range == (45, 60)  # max(45, 60-15)=45, hi=60
    assert "78 岁" in violations[0].message


# ============================================================
# 3) 多代际取最严
# ============================================================


def test_resolve_age_caps_multi_gen_takes_strictest() -> None:
    """5 岁娃（学龄前 75）+ 78 岁老人（高龄 60）→ 取 min=60。"""
    intent = _make_intent(
        [Companion(role="孩子", age=5), Companion(role="父母", age=78)]
    )
    cap, reasons = _resolve_age_caps(intent)
    assert cap == 60
    # 78 岁是触发最严的原因
    assert any("78" in r for r in reasons)


def test_blueprint_critic_multi_gen_70min_hits_senior_cap() -> None:
    """5 岁娃（cap 75）+ 78 岁老人（cap 60），70min POI → 命中（70 > 60）。"""
    intent = _make_intent(
        [Companion(role="孩子", age=5), Companion(role="父母", age=78)]
    )
    bp = _make_blueprint("P003", 70)
    violations = _age_aware_duration_critic(bp, intent)
    assert len(violations) == 1
    assert violations[0].expected_range == (45, 60)


# ============================================================
# 4) 无 age 时 critic 降级
# ============================================================


def test_critic_no_age_returns_empty() -> None:
    """同行人没填 age → critic 不报。"""
    intent = _make_intent([Companion(role="妻子")])
    bp = _make_blueprint("P003", 180)  # 180min 主活动也不报
    violations = _age_aware_duration_critic(bp, intent)
    assert violations == []


def test_critic_no_companions_returns_empty() -> None:
    """没 companions → critic 不报。"""
    intent = _make_intent([])
    bp = _make_blueprint("P003", 200)
    violations = _age_aware_duration_critic(bp, intent)
    assert violations == []


# ============================================================
# 5) format_violations_for_llm 自然语言
# ============================================================


def test_format_violations_renders_expected_range_natural_language() -> None:
    """format 输出含「建议范围 X-Y min」，**不**含字段名。"""
    v = Violation(
        code=ViolationCode.AGE_DURATION_MISMATCH,
        severity=Severity.CRITICAL,
        message="第 1 段 90 分钟超出年龄约束（含 5 岁孩）",
        field_path="nodes[1].duration_min",
        expected_range=(60, 75),
    )
    text = format_violations_for_llm([v])
    assert "建议范围 60-75 min" in text
    # 不暴露字段名
    assert "expected_range" not in text
    assert "nodes[1]" not in text
    assert "duration_min" not in text
    assert "field_path" not in text


def test_format_violations_no_expected_range_no_extra_text() -> None:
    """无 expected_range 的 violation → format 不加「建议范围」段。"""
    v = Violation(
        code=ViolationCode.DURATION_OUT_OF_RANGE,
        severity=Severity.CRITICAL,
        message="总时长超上限",
    )
    text = format_violations_for_llm([v])
    assert "建议范围" not in text


# ============================================================
# 6) blueprint critic vs critics_v2 镜像等价
# ============================================================


def test_critics_mirror_equivalence_5yo_90min() -> None:
    """同输入 5 岁娃 + 90min POI，blueprint critic 与 critics_v2 都命中。"""
    intent = _make_intent([Companion(role="孩子", age=5)])

    # blueprint 路径
    bp = _make_blueprint("P003", 90)
    bp_violations = _age_aware_duration_critic(bp, intent)
    assert len(bp_violations) == 1

    # critics_v2 路径（构造一个最简 Itinerary 含 1 个 90min POI）
    nodes = [
        ActivityNode(
            node_id="n_home_start",
            kind="家",
            target_kind="home",
            target_id="home",
            start_time="13:50",
            duration_min=0,
            title="家",
        ),
        ActivityNode(
            node_id="n_1",
            kind="看展",
            target_kind="poi",
            target_id="P003",
            title="测试 POI",
            start_time="14:00",
            duration_min=90,
        ),
        ActivityNode(
            node_id="n_home_end",
            kind="家",
            target_kind="home",
            target_id="home",
            start_time="15:40",
            duration_min=0,
            title="家",
        ),
    ]
    hops = [
        Hop(
            hop_id="h_0",
            from_node_id="n_home_start",
            to_node_id="n_1",
            start_time="13:50",
            minutes=10,
            mode="walking",
            path_type="real_route",
        ),
        Hop(
            hop_id="h_1",
            from_node_id="n_1",
            to_node_id="n_home_end",
            start_time="15:30",
            minutes=10,
            mode="walking",
            path_type="real_route",
        ),
    ]
    itin = Itinerary(nodes=nodes, hops=hops, summary="测试", total_minutes=180)
    v2_violations = _check_age_aware_duration(itin, intent)
    assert len(v2_violations) == 1
    assert v2_violations[0].expected_range == bp_violations[0].expected_range


def test_critics_mirror_equivalence_no_age() -> None:
    """同输入无 age：两路径都 no-op。"""
    intent = _make_intent([Companion(role="妻子")])

    bp = _make_blueprint("P003", 180)
    bp_violations = _age_aware_duration_critic(bp, intent)
    assert bp_violations == []

    nodes = [
        ActivityNode(
            node_id="n_home_start",
            kind="家",
            target_kind="home",
            target_id="home",
            start_time="13:50",
            duration_min=0,
            title="家",
        ),
        ActivityNode(
            node_id="n_1",
            kind="看展",
            target_kind="poi",
            target_id="P003",
            title="测试 POI",
            start_time="14:00",
            duration_min=180,
        ),
        ActivityNode(
            node_id="n_home_end",
            kind="家",
            target_kind="home",
            target_id="home",
            start_time="17:10",
            duration_min=0,
            title="家",
        ),
    ]
    hops = [
        Hop(
            hop_id="h_0",
            from_node_id="n_home_start",
            to_node_id="n_1",
            start_time="13:50",
            minutes=10,
            mode="walking",
            path_type="real_route",
        ),
        Hop(
            hop_id="h_1",
            from_node_id="n_1",
            to_node_id="n_home_end",
            start_time="17:00",
            minutes=10,
            mode="walking",
            path_type="real_route",
        ),
    ]
    itin = Itinerary(nodes=nodes, hops=hops, summary="测试", total_minutes=180)
    v2_violations = _check_age_aware_duration(itin, intent)
    assert v2_violations == []


# ============================================================
# 7) run_blueprint_critics 整合年龄 critic
# ============================================================


def test_run_blueprint_critics_includes_age_critic() -> None:
    """run_blueprint_critics 把 _age_aware_duration_critic 接入主流程。"""
    intent = _make_intent([Companion(role="孩子", age=5)])
    bp = _make_blueprint("P003", 100)
    report = run_blueprint_critics(bp, intent)
    assert not report.passed
    age_v = [
        v for v in report.violations if v.critic == "blueprint_age_aware_duration"
    ]
    assert len(age_v) == 1
    assert age_v[0].expected_range == (60, 75)


def test_run_blueprint_critics_no_intent_no_age_critic() -> None:
    """无 intent 时不跑年龄 critic（向后兼容旧调用）。"""
    bp = _make_blueprint("P003", 200)
    report = run_blueprint_critics(bp, intent=None)
    age_v = [
        v for v in report.violations if v.critic == "blueprint_age_aware_duration"
    ]
    assert age_v == []
