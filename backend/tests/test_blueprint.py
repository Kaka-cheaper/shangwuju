"""tests.test_blueprint —— Blueprint 数据结构 + 蓝图级 Critic 单元测试（edge_v1）。

蓝图（PlanBlueprint）是 LLM 在看到 POI/餐厅候选数据后，**自主决定**节点集合、
节点顺序、每个节点停留时长、目标 id 的产物。下游算法（assemble_from_blueprint）
按蓝图自动补 home 首尾 + 自动算 hop 通勤，拼出 Itinerary 时间轴。

设计动机：参考 problem.md 问题 14 + .kiro/specs/itinerary-edge-model-refactor/design.md
——LLM-Modulo（NeurIPS 2024）+ ItiNera（EMNLP 2024）的"LLM 决主观、算法决客观"原则。

edge_v1 关键变化（vs 旧 BlueprintStage 模型）：
- 删除 `BlueprintStage`：节点不再含 start_time（系统算）；改为 `BlueprintNode`
- 删除 `BlueprintTargetKind.NONE`：通勤是 hop 不是 node，蓝图里只有 poi/restaurant
- 删除「蓝图总时长 vs intent.duration_hours」校验：下沉到 Itinerary 级 critic
- 删除「段间通勤」校验：blueprint 阶段还没有 hop，下沉到 critics_v2._check_hop_feasibility

本测试覆盖以下维度：
- BlueprintNode 字段约束（必填项 / 非负 duration / 非空 kind）
- BlueprintTargetKind 枚举仅 POI/RESTAURANT（不含 NONE）
- PlanBlueprint 字段约束（nodes 非空 / preferred_start_time 格式 / extra="forbid"）
- _temporal_critic：合法蓝图返空 / 跨 24h 报警
- _duration_critic：单段过短 / 过长 / 零停留
- _opening_hours_critic：在营业时间内通过 / 营业时间外报警 / target_id 不存在报警
- run_blueprint_critics 兼容封装（passed / to_dict / 不传 intent 也合法）
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agent.planning.blueprint.blueprint import (
    MAX_NODE_DURATION_MIN,
    MIN_NODE_DURATION_MIN,
    BlueprintNode,
    BlueprintTargetKind,
    BlueprintViolation,
    PlanBlueprint,
    _duration_critic,
    _opening_hours_critic,
    _temporal_critic,
    run_blueprint_critics,
)


# ============================================================
# 维度 1：BlueprintTargetKind 枚举
# ============================================================


def test_target_kind_only_poi_and_restaurant():
    """edge_v1 删除了 NONE：枚举只能有 POI 与 RESTAURANT。"""
    members = {m.value for m in BlueprintTargetKind}
    assert members == {"poi", "restaurant"}, (
        f"BlueprintTargetKind 应只含 poi/restaurant，实际：{members}"
    )


def test_target_kind_none_is_removed():
    """显式确认 NONE 不存在——访问 attribute / Enum lookup 都应失败。"""
    assert not hasattr(BlueprintTargetKind, "NONE")
    with pytest.raises(ValueError):
        BlueprintTargetKind("none")


# ============================================================
# 维度 2：BlueprintNode 字段约束
# ============================================================


def test_blueprint_node_minimum_fields():
    """合法 mid node：kind/target_kind/target_id/duration_min 即可。"""
    n = BlueprintNode(
        kind="主活动",
        target_kind=BlueprintTargetKind.POI,
        target_id="P040",
        duration_min=120,
    )
    assert n.kind == "主活动"
    assert n.target_kind == BlueprintTargetKind.POI
    assert n.target_id == "P040"
    assert n.duration_min == 120
    assert n.note is None


def test_blueprint_node_with_optional_note():
    """note 字段可选，给前端透传提示。"""
    n = BlueprintNode(
        kind="用餐",
        target_kind=BlueprintTargetKind.RESTAURANT,
        target_id="R001",
        duration_min=60,
        note="已预约 17:00 三人位",
    )
    assert n.note == "已预约 17:00 三人位"


def test_blueprint_node_kind_is_free_chinese():
    """kind 自由中文：夜宵 / 晨练 / 早茶 都允许。"""
    n = BlueprintNode(
        kind="夜宵小聚",
        target_kind=BlueprintTargetKind.RESTAURANT,
        target_id="R001",
        duration_min=90,
    )
    assert n.kind == "夜宵小聚"


def test_blueprint_node_negative_duration_rejected():
    """duration_min 不能负数（NonNegativeInt）。"""
    with pytest.raises(ValidationError, match="duration_min"):
        BlueprintNode(
            kind="主活动",
            target_kind=BlueprintTargetKind.POI,
            target_id="P040",
            duration_min=-10,
        )


def test_blueprint_node_empty_kind_rejected():
    """kind 不允许空字符串（min_length=1）。"""
    with pytest.raises(ValidationError):
        BlueprintNode(
            kind="",
            target_kind=BlueprintTargetKind.POI,
            target_id="P040",
            duration_min=60,
        )


def test_blueprint_node_empty_target_id_rejected():
    """target_id 不允许空字符串。"""
    with pytest.raises(ValidationError):
        BlueprintNode(
            kind="主活动",
            target_kind=BlueprintTargetKind.POI,
            target_id="",
            duration_min=60,
        )


def test_blueprint_node_extra_field_forbidden():
    """配置 extra='forbid'，防止 LLM 字段漂移混入旧 start_time。"""
    with pytest.raises(ValidationError):
        BlueprintNode(
            kind="主活动",
            target_kind=BlueprintTargetKind.POI,
            target_id="P040",
            duration_min=60,
            start_time="14:00",  # 旧字段，应被拒绝
        )


# ============================================================
# 维度 3：PlanBlueprint 字段约束
# ============================================================


def test_plan_blueprint_single_node():
    """单节点合法（如「只想吃饭」场景）。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
    )
    assert len(bp.nodes) == 1
    assert bp.preferred_start_time == "14:00"  # 默认值
    assert bp.rationale == ""


def test_plan_blueprint_multi_node():
    """多节点合法（标准 POI + 用餐场景）。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=165,
            ),
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
        preferred_start_time="14:00",
        rationale="经典下午局：先逛后吃",
    )
    assert len(bp.nodes) == 2
    assert bp.rationale.startswith("经典")


def test_plan_blueprint_empty_nodes_rejected():
    """nodes 列表不能为空（min_length=1）。"""
    with pytest.raises(ValidationError):
        PlanBlueprint(nodes=[])


def test_plan_blueprint_invalid_start_time_format():
    """preferred_start_time 必须 HH:MM。"""
    with pytest.raises(ValidationError, match="preferred_start_time"):
        PlanBlueprint(
            nodes=[
                BlueprintNode(
                    kind="用餐",
                    target_kind=BlueprintTargetKind.RESTAURANT,
                    target_id="R001",
                    duration_min=60,
                ),
            ],
            preferred_start_time="2pm",
        )


def test_plan_blueprint_extra_field_forbidden():
    """蓝图层级也禁止旧字段透传（如旧 stages 字段）。"""
    with pytest.raises(ValidationError):
        PlanBlueprint(
            nodes=[
                BlueprintNode(
                    kind="用餐",
                    target_kind=BlueprintTargetKind.RESTAURANT,
                    target_id="R001",
                    duration_min=60,
                ),
            ],
            stages=[],  # 旧字段
        )


# ============================================================
# 维度 4：_temporal_critic
# ============================================================


def test_temporal_critic_passes_for_continuous_nodes():
    """合法蓝图（按累加 duration 自然不重叠）→ critic 返空 list。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=60,
            ),
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
        preferred_start_time="14:00",
    )
    violations = _temporal_critic(bp)
    assert violations == [], f"应无时序违规，实际：{violations}"


def test_temporal_critic_single_node_passes():
    """单节点不可能重叠 → 返空 list。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
    )
    assert _temporal_critic(bp) == []


def test_temporal_critic_catches_24h_overflow():
    """累加 duration 末尾溢出 24:00 → 报警。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=300,  # 5h
            ),
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=300,  # 又 5h，累计 10h
            ),
        ],
        preferred_start_time="22:00",  # 22:00 + 10h = 08:00 次日
    )
    violations = _temporal_critic(bp)
    assert violations, "应捕获跨 24:00 溢出"
    assert any("24:00" in v for v in violations)


# ============================================================
# 维度 5：_duration_critic
# ============================================================


def test_duration_critic_passes_for_normal_nodes():
    """所有 node 时长在合理区间 → 返空 list。"""
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
    )
    assert _duration_critic(bp) == []


def test_duration_critic_catches_too_short():
    """单段过短（5min）→ 报警。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=5,  # 低于 MIN=10
            ),
        ],
    )
    violations = _duration_critic(bp)
    assert violations, f"应捕获过短停留，实际：{violations}"
    assert any(str(MIN_NODE_DURATION_MIN) in v for v in violations)


def test_duration_critic_catches_too_long():
    """单段过长（500min）→ 报警。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=500,  # 高于 MAX=300
            ),
        ],
    )
    violations = _duration_critic(bp)
    assert violations, f"应捕获过长停留，实际：{violations}"
    assert any(str(MAX_NODE_DURATION_MIN) in v for v in violations)


def test_duration_critic_catches_zero_duration():
    """duration_min=0（NonNegativeInt 允许）→ 报警（mid node 不应零停留）。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=0,
            ),
        ],
    )
    violations = _duration_critic(bp)
    assert violations, "应捕获零停留"


# ============================================================
# 维度 6：_opening_hours_critic（依赖 mock_data）
# ============================================================


def test_opening_hours_passes_for_in_business():
    """R001 营业 10:30-21:30；蓝图 14:00 + 用餐 60min = 14:00-15:00 在营业时间内 → 通过。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
        preferred_start_time="14:00",
    )
    violations = _opening_hours_critic(bp)
    assert violations == [], f"R001 14:00-15:00 应在营业时间内，实际：{violations}"


def test_opening_hours_catches_closed():
    """R001 营业 10:30-21:30；蓝图 06:00 早餐 60min 不在营业时间 → 报警。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="早餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
        preferred_start_time="06:00",
    )
    violations = _opening_hours_critic(bp)
    assert violations, "R001 06:00 应捕获营业时间违规"
    assert any("营业时间" in v or "营业" in v for v in violations)


def test_opening_hours_catches_unknown_target_id():
    """target_id 在 mock_data 中不存在 → 报警。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R_NOT_EXIST",
                duration_min=60,
            ),
        ],
    )
    violations = _opening_hours_critic(bp)
    assert violations, "应捕获未知 target_id"
    assert any("未找到" in v for v in violations)


# ============================================================
# 维度 7：run_blueprint_critics 兼容封装
# ============================================================


def test_run_blueprint_critics_passes_for_legal_blueprint():
    """合法蓝图 → report.passed=True。"""
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
    )
    report = run_blueprint_critics(bp)
    assert report.passed, f"合法蓝图应 pass，实际违规：{report.violations}"
    assert report.violations == []


def test_run_blueprint_critics_fails_when_any_hard_violation():
    """单段过短即 hard 违规 → passed=False。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=5,
            ),
        ],
    )
    report = run_blueprint_critics(bp)
    assert not report.passed
    assert all(v.severity == "hard" for v in report.violations)
    assert all(isinstance(v, BlueprintViolation) for v in report.violations)


def test_run_blueprint_critics_intent_param_optional():
    """edge_v1 三个 critic 内部不再使用 intent，签名保留兼容旧调用方但参数可缺省。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=60,
            ),
        ],
    )
    # 不传 intent
    report = run_blueprint_critics(bp)
    assert report.passed
    # 传 None 也合法
    report2 = run_blueprint_critics(bp, None)
    assert report2.passed


def test_run_blueprint_critics_to_dict_serializable():
    """report.to_dict 应能直接 JSON 序列化（DecisionTrace 推送用）。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
    )
    report = run_blueprint_critics(bp)
    json.dumps(report.to_dict())  # 不抛异常即合格
