"""agent.blueprint —— LLM-First Planner 的"行程蓝图"数据结构（edge_v1）。

【为什么需要蓝图】（参考 problem.md 问题 14 / pitfalls.md P1-2026-05-17）

历史包袱：rule planner 把"5 段写死 + 14:00 起 + POI→餐厅顺序"当默认，
导致 24h 营业餐厅、夜宵、早茶、单段方案被强行套到下午局模板，违反
LLM-Modulo（Kambhampati NeurIPS 2024）"LLM 决主观、算法决客观"原则。

【edge_v1 的本质转变】

旧 BlueprintStage（已删）：`kind / start_time / duration_min / target_kind / target_id`
- LLM 既要选目标，又要算时刻、又要算通勤——典型职责漂移
- target_kind="none" 用来表达"出发 / 转场 / 返回"过程段，与 hop 概念重叠

新 BlueprintNode：`kind / target_kind / target_id / duration_min / note`
- LLM 只决定 **「在哪里、做什么、停留多久」**（mid nodes）
- 系统（assemble_from_blueprint）自动补 home 首尾节点 + 自动按 routes.json 算 hop 通勤
- target_kind 只允许 poi / restaurant，**没有 NONE 过程段**——通勤是 hop 不是 node

【蓝图级 Critic 已删除（ADR-0009 决策 8 / Phase C-5）】

本文件曾有一套 PlanBlueprint 级 critic（`run_blueprint_critics` +
`_temporal_critic` / `_duration_critic` / `_opening_hours_critic` /
`_age_aware_duration_critic`），但确认**无生产调用者**（`planner_llm_first`
随 ADR-0007 删除，`generate_blueprint` 从不调用）后，随 ADR-0009 一并删除。
其中仍有价值的能力已迁移/取代：
- 营业时间判定 → 移植到 `agent/planning/critic/_rules/helpers.py`
  （`_is_in_business_hours`）+ `checks.py`（`check_opening_hours`），作用对象
  换成已 assemble 的 Itinerary（有真实 hop 到达时间，比蓝图阶段的粗略推算准）。
- 年龄上限 → 单一真相源 `agent/planning/critic/age_caps.py`，供 critic /
  组装器 / ILS grounding 共读。

不负责：
- LLM 调用与 prompt（在 agent/blueprint_llm.py / agent/prompts/blueprint_prompt.py）
- 蓝图→Itinerary 拼装（在 agent/assemble_blueprint.py）
- Itinerary 级别校验（在 agent/planning/critic/critics_v2.py）
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, field_validator


# ============================================================
# 时间格式校验正则（HH:MM）
# ============================================================

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


# ============================================================
# 节点目标类型（删除了 NONE）
# ============================================================


class BlueprintTargetKind(str, Enum):
    """蓝图节点的目标实体类型。

    edge_v1 移除了 NONE：通勤过程在新模型里是 hop（边），不是 node（节点）。
    LLM 蓝图里只输出"具体目标"——POI 或餐厅。
    """

    POI = "poi"
    RESTAURANT = "restaurant"


# ============================================================
# BlueprintNode（LLM 输出契约）
# ============================================================


class BlueprintNode(BaseModel):
    """LLM 输出的中间节点契约。

    LLM 只决定 `target_id + duration_min + kind`，**不决定时间、不决定通勤**。
    首尾的 home 节点由 assemble_from_blueprint 自动补；
    节点间的 hop（通勤）由 lookup_hop 自动算。
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        ...,
        min_length=1,
        description='节点性质中文标签：主活动 / 用餐 / 夜宵 / 自由 / 早茶 / 晨练 等自由文本',
    )
    target_kind: BlueprintTargetKind = Field(
        ..., description="节点目标类型：poi / restaurant（不允许 home / 过程段）"
    )
    target_id: str = Field(
        ..., min_length=1, description="对应 mock_data.pois.id / mock_data.restaurants.id"
    )
    duration_min: NonNegativeInt = Field(
        ..., description="在该节点的停留时长（分钟，不含通勤）；建议 ≥10 ≤300"
    )
    note: Optional[str] = Field(
        default=None,
        description='给前端的补充提示文案，如"已预约 17:00 三人位"',
    )
    not_before_start: Optional[str] = Field(
        default=None,
        description=(
            '节点最早开始时刻 "HH:MM"（如餐厅预约 chosen_time）。'
            "assemble_from_blueprint 在自然到达早于此刻时，把节点开始推迟到此刻"
            "（差额为餐前空闲/休息），让排定时刻与 note/reservation 自洽"
            "（ADR-0009 决策 2·乙）。默认 None=不约束，LLM 路径不设即 no-op。"
        ),
    )


# ============================================================
# PlanBlueprint（LLM 输出的完整蓝图）
# ============================================================


class PlanBlueprint(BaseModel):
    """LLM 输出的完整行程蓝图（mid nodes 列表）。

    **不**含首尾 home（assemble 自动加），**不**含 hops（assemble 自动算）。
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[BlueprintNode] = Field(
        ...,
        min_length=1,
        description="按时间顺序排列的中间节点（mid nodes，不含 home 首尾）",
    )
    preferred_start_time: str = Field(
        default="14:00",
        description='蓝图整体偏好的开始时刻 HH:MM（assemble 算时间从此刻起）',
    )
    rationale: str = Field(
        default="", description="LLM 对方案的简短中文 rationale（用于 DecisionTrace）"
    )

    @field_validator("preferred_start_time")
    @classmethod
    def _check_start_time_format(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError(
                f"preferred_start_time 必须是 HH:MM 格式，实际 {v!r}"
            )
        return v
