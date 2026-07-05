"""critic 公开类型 / 枚举 / 常量（独立 module 避免循环 import）。

critics_v2.py 与 _rules/checks.py 都从这里 import：
- critics_v2.py: 提供入口 validate_itinerary / format_violations_for_llm
- _rules/checks.py: 提供 13 个 check_xxx 实现

把类型从 critics_v2.py 抽出避免循环 import：
critics_v2 → checks（要调 check_xxx）→ critics_v2（要拿 ViolationCode / Severity）
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# 枚举与数据结构
# ============================================================


class ViolationCode(str, Enum):
    """edge_v1 critic 触发码。

    与 schemas/errors.py 的 FailureReason 解耦——FailureReason 是
    Tool 失败原因，ViolationCode 是 Itinerary 级别的违规分类。

    edge_v1 重命名映射：
    - STAGES_INCOMPLETE → NODES_INCOMPLETE
    - COMMUTE_INFEASIBLE → HOP_INFEASIBLE
    - 新增 INVARIANT_BROKEN
    """

    INVARIANT_BROKEN = "invariant_broken"
    NODES_INCOMPLETE = "nodes_incomplete"
    DURATION_OUT_OF_RANGE = "duration_out_of_range"
    TIMELINE_INCONSISTENT = "timeline_inconsistent"
    HOP_INFEASIBLE = "hop_infeasible"
    DISTANCE_EXCEEDED = "distance_exceeded"
    RESTAURANT_FULL_UNRESOLVED = "restaurant_full_unresolved"
    DIETARY_VIOLATION = "dietary_violation"
    PHYSICAL_VIOLATION = "physical_violation"  # ADR-0014 决策 2（G-2）：与 DIETARY_VIOLATION 对称，见 check_physical
    SOCIAL_CONTEXT_MISMATCH = "social_context_mismatch"
    AGE_DURATION_MISMATCH = "age_duration_mismatch"  # spec planning-quality-deep-review R4
    TOOL_RESPONSE_INCONSISTENCY = "tool_response_inconsistency"  # spec algorithm-redesign R2
    CAPACITY_REQUIREMENT_VIOLATED = "capacity_requirement_violated"  # spec innovation-review M3
    MEAL_TIME_UNREASONABLE = "meal_time_unreasonable"  # spec planning-pipeline-consolidation R1
    OPENING_HOURS_VIOLATION = "opening_hours_violation"  # ADR-0008 B-2b：营业时间检查（新增）
    BUDGET_EXCEEDED = "budget_exceeded"  # ADR-0014 决策 3（G-3）：见 check_budget
    PINNED_ENTITY_MISSING = "pinned_entity_missing"  # 赞锁定根治批：锁定实体缺席（见 check_pinned_presence）


class Severity(str, Enum):
    """违规等级（ADR-0008 B-1：CRITICAL→HARD / WARNING→SOFT）。

    - HARD：进修复闭环（驱动 backprompt / replan）；接受与否由 hard 层决定
    - SOFT：只建议（narration），不 gate——方案仍可上呈
    """

    HARD = "hard"
    SOFT = "soft"


class Violation(BaseModel):
    """一条违规记录（ADR-0008 B-1）。

    `message` 是给 LLM / 用户看的中文修复建议（必须自包含完整定位信息）；
    `field_path` 是 dot-path 风格的内部定位（如 "hops[2]" / "nodes[1].duration_min"），
    **仅用于 trace / 调试**——不暴露给 LLM（design.md 强约束）。
    `node_ref` / `hint` 为 B-2 引入的可执行违规字段（B-1 留空，B-2 填充）。
    """

    model_config = ConfigDict(extra="forbid")

    code: ViolationCode
    severity: Severity
    message: str = Field(
        ...,
        description="给 LLM / 用户看的中文修复建议；必须自包含「第几段、什么目标」",
    )
    field_path: str = Field(
        default="",
        description='内部 dot-path 定位，如 "hops[2]" / "nodes[1]"；不进 LLM prompt',
    )
    expected_range: Optional[tuple[int, int]] = Field(
        default=None,
        description=(
            "建议收敛区间 (lo, hi)。spec planning-quality-deep-review R4 引入。"
            "format_violations_for_llm 拼成「建议范围 lo-hi min」自然语言喂回 LLM——"
            "**不**暴露字段名 expected_range / nodes[i] / dot-path 给 LLM。"
        ),
    )
    node_ref: Optional[str] = Field(
        default=None,
        description="节点定位（B-2 填充，B-1 留空）：对应 node_id，供 replan 定向修复",
    )
    hint: Optional[str] = Field(
        default=None,
        description="修复 hint（B-2 填充，B-1 留空）：VAL 风格的可执行修复建议",
    )


# ============================================================
# 常量（critic 内部用）
# ============================================================

# 时序容差（分钟）：hop / temporal feasibility 的浮动窗口
TEMPORAL_TOLERANCE_MIN: int = 2

# hop_feasibility 容差（分钟）：hop.minutes 允许比 actual_min 少 2min
HOP_FEASIBILITY_TOLERANCE_MIN: int = 2

# 默认时长容差（分钟）：[lo*60 - 30, hi*60 + 30]
DURATION_TOLERANCE_MIN: int = 30

# distance critic 容差（km）
DISTANCE_TOLERANCE_KM: float = 0.5


# (ADR-0008 B-1: SEVERITY_WEIGHTS / CODE_WEIGHTS / VALID_FEEDBACK_MODES 随 reward/feedback-mode 机制一起删除)
# (ADR-0008 B-2b O11: DEMO_FULL_TIME 孤儿常量已删——check_demo_restaurant_full 早改查
#  mock reservation_slots 真值，不再依赖写死的 17:00)
