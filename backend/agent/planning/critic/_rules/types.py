"""critics_v2 公开类型 / 枚举 / 权重常量（独立 module 避免循环 import）。

critics_v2.py 与 _rules/checks.py 都从这里 import：
- critics_v2.py: 提供入口 validate_itinerary / format_violations_for_llm
- _rules/checks.py: 提供 11 个 _check_xxx 实现

把类型从 critics_v2.py 抽出避免循环 import：
critics_v2 → checks（要调 _check_xxx）→ critics_v2（要拿 ViolationCode / Severity）
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
    SOCIAL_CONTEXT_MISMATCH = "social_context_mismatch"
    AGE_DURATION_MISMATCH = "age_duration_mismatch"  # spec planning-quality-deep-review R4
    TOOL_RESPONSE_INCONSISTENCY = "tool_response_inconsistency"  # spec algorithm-redesign R2
    CAPACITY_REQUIREMENT_VIOLATED = "capacity_requirement_violated"  # spec innovation-review M3
    MEAL_TIME_UNREASONABLE = "meal_time_unreasonable"  # spec planning-pipeline-consolidation R1


class Severity(str, Enum):
    """违规等级。

    - CRITICAL：必须 backprompt / replan；调用方应把 violation 转成 prompt 让 LLM 重做
    - WARNING ：方案可继续上呈，但日志/调试时需关注（如 mock 数据本身的轻微偏差）
    """

    CRITICAL = "critical"
    WARNING = "warning"


class Violation(BaseModel):
    """一条违规记录。

    `message` 是给 LLM / 用户看的中文修复建议（必须自包含完整定位信息）；
    `field_path` 是 dot-path 风格的内部定位（如 "hops[2]" / "nodes[1].duration_min"），
    **仅用于 trace / 调试**——不暴露给 LLM（design.md 强约束）。
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

# demo-aware 17:00 满座埋点
DEMO_FULL_TIME: str = "17:00"


# ============================================================
# spec algorithm-redesign R1：reward 权重
# ============================================================
#
# LLM-Modulo 范式（参考 .kiro/specs/algorithm-redesign/research/agent-3-llm-modulo）
# 把 critic 视为「dense scalar reward 提供者」，为未来 RL 路径预留挂钩。
#
# 权重设计（来自 .kiro/specs/algorithm-redesign/design.md §Components 决策点 1）：
# - SEVERITY_WEIGHTS：CRITICAL 是 WARNING 的 5 倍——反映 critical 必须 replan，
#   warning 仅日志的实际影响差距
# - CODE_WEIGHTS：macro 级（结构性、节点完整性、时序、tool 幻觉）取 1.5，
#   细粒度（饮食、距离）取 0.8，其余 1.0——避免「100 个 warning 加起来反而比
#   1 个 critical 还重」的逆优先级失败模式

SEVERITY_WEIGHTS: dict[Severity, float] = {
    Severity.CRITICAL: 1.0,
    Severity.WARNING: 0.2,
}

# 注意：CODE_WEIGHTS 用 dict.get(code, 1.0) 兜底——新加 ViolationCode 时不必同步更新
CODE_WEIGHTS: dict[ViolationCode, float] = {
    ViolationCode.INVARIANT_BROKEN: 1.5,
    ViolationCode.NODES_INCOMPLETE: 1.5,
    ViolationCode.TIMELINE_INCONSISTENT: 1.5,
    ViolationCode.TOOL_RESPONSE_INCONSISTENCY: 1.5,  # spec algorithm-redesign R2：hallucination 等同 macro 级
    ViolationCode.CAPACITY_REQUIREMENT_VIOLATED: 1.5,  # spec innovation-review M3：≥5 人桌型不够等同 macro 级
    ViolationCode.DIETARY_VIOLATION: 0.8,
    ViolationCode.DISTANCE_EXCEEDED: 0.8,
}

# 反馈模式有效值（_get_feedback_mode 校验用）
VALID_FEEDBACK_MODES = frozenset({"pinpoint-all", "first-only", "reward"})
