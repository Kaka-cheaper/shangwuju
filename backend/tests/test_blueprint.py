"""tests.test_blueprint —— Blueprint 数据结构 + 蓝图级 Critic 单元测试。

蓝图（PlanBlueprint）是 LLM 在看到 POI/餐厅候选数据后，**自主决定**段集合、
段顺序、每段时长、目标 id 的产物。下游算法（assemble_from_blueprint）按蓝图
拼出 Itinerary 时间轴；Critic 负责验证蓝图本身的合法性。

设计动机：参考 problem.md 问题 14——纯规则 planner 在 1h 反馈 / 24h 营业 / 反序就餐
等场景下都需要"补 if"，违反 LLM-Modulo（NeurIPS 2024）+ ItiNera（EMNLP 2024）的
"LLM 决主观、算法决客观"原则。

本测试覆盖 4 类场景：
- 蓝图字段约束（kind 自由 / target 一致 / 时间格式）
- 时序 critic（段无重叠 + 顺序连续）
- 营业时间 critic（用 mock 的 opening_hours 验证 target 在营业时间内）
- 时长边界 critic（蓝图总时长 ≤ duration_hours[1]+15min 容忍）
"""

from __future__ import annotations

import pytest

from agent.blueprint import (
    BlueprintStage,
    BlueprintTargetKind,
    BlueprintViolation,
    PlanBlueprint,
    run_blueprint_critics,
)
from schemas.intent import Companion, IntentExtraction


# ============================================================
# 共享 fixture
# ============================================================

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
# 维度 1：BlueprintStage 字段约束
# ============================================================

def test_blueprint_stage_minimum_fields():
    s = BlueprintStage(
        kind="主活动",
        start_time="14:00",
        duration_min=45,
    )
    assert s.target_kind == BlueprintTargetKind.NONE
    assert s.target_id is None
    assert s.kind == "主活动"


def test_blueprint_stage_with_target_must_have_id():
    """target_kind != none 时 target_id 不能为空。"""
    with pytest.raises(ValueError, match="target_id"):
        BlueprintStage(
            kind="用餐",
            start_time="18:00",
            duration_min=60,
            target_kind=BlueprintTargetKind.RESTAURANT,
            target_id=None,
        )


def test_blueprint_stage_kind_is_free_string():
    """kind 是自由文本——LLM 可以输出"夜宵" / "下午茶续杯" / "晨练" 任意中文。"""
    s = BlueprintStage(kind="夜宵小聚", start_time="22:00", duration_min=90)
    assert s.kind == "夜宵小聚"


def test_blueprint_stage_invalid_time_format():
    with pytest.raises(ValueError, match="start_time"):
        BlueprintStage(kind="主活动", start_time="2pm", duration_min=60)


def test_blueprint_stage_negative_duration():
    with pytest.raises(ValueError, match="duration_min"):
        BlueprintStage(kind="主活动", start_time="14:00", duration_min=-10)


def test_blueprint_end_time_computed():
    s = BlueprintStage(kind="主活动", start_time="14:00", duration_min=90)
    assert s.end_time() == "15:30"


def test_blueprint_end_time_crosses_hour():
    s = BlueprintStage(kind="主活动", start_time="14:45", duration_min=30)
    assert s.end_time() == "15:15"


# ============================================================
# 维度 2：PlanBlueprint 字段约束
# ============================================================

def test_blueprint_must_have_at_least_one_stage():
    with pytest.raises(ValueError, match="stages"):
        PlanBlueprint(stages=[], rationale="空蓝图")


def test_blueprint_total_minutes_computed():
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="出发", start_time="14:00", duration_min=15),
            BlueprintStage(kind="主活动", start_time="14:15", duration_min=45),
            BlueprintStage(kind="返回", start_time="15:00", duration_min=15),
        ],
        rationale="单段去玩",
    )
    assert bp.total_minutes() == 75


# ============================================================
# 维度 3：时序 critic
# ============================================================

def test_critic_temporal_passes_for_continuous_stages():
    """段连续无重叠 → critic 通过。"""
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="出发", start_time="14:00", duration_min=15),
            BlueprintStage(kind="主活动", start_time="14:15", duration_min=45),
            BlueprintStage(kind="返回", start_time="15:00", duration_min=15),
        ],
        rationale="ok",
    )
    report = run_blueprint_critics(bp, _intent([1, 1]))
    temporal = [v for v in report.violations if v.critic == "blueprint_temporal"]
    assert not temporal, f"应无时序违规，实际：{[v.message for v in temporal]}"


def test_critic_temporal_catches_overlap():
    """段重叠 → 硬违规。"""
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="主活动", start_time="14:00", duration_min=60),
            BlueprintStage(kind="用餐", start_time="14:30", duration_min=60),
        ],
        rationale="重叠",
    )
    report = run_blueprint_critics(bp, _intent([2, 2]))
    overlaps = [v for v in report.violations if v.critic == "blueprint_temporal"]
    assert overlaps, "应捕获时序重叠"
    assert any("重叠" in v.message for v in overlaps)


def test_critic_temporal_catches_out_of_order():
    """段时间倒序 → 硬违规。"""
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="出发", start_time="15:00", duration_min=15),
            BlueprintStage(kind="返回", start_time="14:00", duration_min=15),
        ],
        rationale="倒序",
    )
    report = run_blueprint_critics(bp, _intent([1, 1]))
    assert any(v.critic == "blueprint_temporal" for v in report.violations)


# ============================================================
# 维度 4：时长边界 critic
# ============================================================

def test_critic_duration_passes_within_limit():
    """蓝图总时长在 duration_hours 上限 + 15min 容忍内 → 通过。"""
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="出发", start_time="14:00", duration_min=15),
            BlueprintStage(kind="主活动", start_time="14:15", duration_min=45),
            BlueprintStage(kind="返回", start_time="15:00", duration_min=15),
        ],
        rationale="1h 内",
    )
    report = run_blueprint_critics(bp, _intent([1, 1]))
    duration_v = [v for v in report.violations if v.critic == "blueprint_duration"]
    assert not duration_v


def test_critic_duration_catches_exceed():
    """蓝图总时长超 duration_hours[1]+15min → 硬违规。"""
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="出发", start_time="14:00", duration_min=15),
            BlueprintStage(kind="主活动", start_time="14:15", duration_min=180),
            BlueprintStage(kind="返回", start_time="17:15", duration_min=15),
        ],
        rationale="3h 但用户要 1h",
    )
    report = run_blueprint_critics(bp, _intent([1, 1]))
    assert any(v.critic == "blueprint_duration" for v in report.violations)


# ============================================================
# 维度 5：营业时间 critic（依赖 mock_data）
# ============================================================

def test_critic_opening_hours_passes_for_in_business():
    """R001 营业 10:30-21:30；蓝图用餐 17:30 在营业时间内 → 通过。"""
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(
                kind="用餐",
                start_time="17:30",
                duration_min=60,
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
            ),
        ],
        rationale="ok",
    )
    report = run_blueprint_critics(bp, _intent([1, 1]))
    opening = [v for v in report.violations if v.critic == "blueprint_opening_hours"]
    assert not opening, f"R001 17:30 应在营业时间内，实际：{opening}"


def test_critic_opening_hours_catches_closed():
    """R001 营业 10:30-21:30；蓝图用餐 06:00 不在营业时间 → 硬违规。"""
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(
                kind="早餐",
                start_time="06:00",
                duration_min=60,
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
            ),
        ],
        rationale="bad",
    )
    report = run_blueprint_critics(bp, _intent([1, 1]))
    opening = [v for v in report.violations if v.critic == "blueprint_opening_hours"]
    assert opening, "R001 06:00 应捕获营业时间违规"


def test_critic_opening_hours_unknown_target_id():
    """target_id 不存在 → 硬违规。"""
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(
                kind="用餐",
                start_time="18:00",
                duration_min=60,
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R_NOT_EXIST",
            ),
        ],
        rationale="bad id",
    )
    report = run_blueprint_critics(bp, _intent([2, 2]))
    assert any(
        v.critic == "blueprint_opening_hours" and "未找到" in v.message
        for v in report.violations
    )


# ============================================================
# 维度 6：聚合 report
# ============================================================

def test_report_passed_when_no_hard_violations():
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="出发", start_time="14:00", duration_min=15),
            BlueprintStage(kind="主活动", start_time="14:15", duration_min=45),
            BlueprintStage(kind="返回", start_time="15:00", duration_min=15),
        ],
        rationale="ok",
    )
    report = run_blueprint_critics(bp, _intent([1, 1]))
    assert report.passed


def test_report_failed_when_any_hard_violation():
    bp = PlanBlueprint(
        stages=[
            BlueprintStage(kind="主活动", start_time="14:00", duration_min=60),
            BlueprintStage(kind="用餐", start_time="14:30", duration_min=60),
        ],
        rationale="重叠",
    )
    report = run_blueprint_critics(bp, _intent([2, 2]))
    assert not report.passed
    assert any(v.severity == "hard" for v in report.violations)


def test_report_to_dict_serializable():
    """report 应能直接 JSON 序列化（trace 推送用）。"""
    import json

    bp = PlanBlueprint(
        stages=[BlueprintStage(kind="出发", start_time="14:00", duration_min=10)],
        rationale="测试",
    )
    report = run_blueprint_critics(bp, _intent([1, 1]))
    d = report.to_dict()
    json.dumps(d)  # 不抛异常即合格
