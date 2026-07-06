"""tests.test_blueprint —— Blueprint 数据结构单元测试（edge_v1）。

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

注：蓝图级 critic（_temporal_critic / _duration_critic / _opening_hours_critic /
run_blueprint_critics）已确认无生产调用者，随 ADR-0009 决策 8（Phase C-5）删除，
原维度 4-7 的对应测试一并删除（intentional，被测对象已不存在）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.planning.blueprint.blueprint import (
    BlueprintNode,
    BlueprintTargetKind,
    PlanBlueprint,
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
    assert bp.plan_reason == ""  # 信任带 §四③：Optional 默认空串，不破坏既有构造


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


def test_plan_blueprint_plan_reason_optional_and_independent_of_rationale():
    """§四③ plan_reason 与既有 rationale 各自独立字段，互不覆盖/互不联动。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=120,
            ),
        ],
        rationale="经典下午局：先逛后吃",
        plan_reason="用户同行年轻人多，所以先用 KTV 带动气氛",
    )
    assert bp.rationale == "经典下午局：先逛后吃"
    assert bp.plan_reason == "用户同行年轻人多，所以先用 KTV 带动气氛"


def test_plan_blueprint_old_payload_without_plan_reason_still_validates():
    """旧 LLM 输出 / 未升级的 mock 数据没有 plan_reason 键也能 model_validate。"""
    payload = {
        "nodes": [
            {
                "kind": "用餐",
                "target_kind": "restaurant",
                "target_id": "R001",
                "duration_min": 60,
            }
        ],
        "rationale": "旧格式",
    }
    bp = PlanBlueprint.model_validate(payload)
    assert bp.plan_reason == ""


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
