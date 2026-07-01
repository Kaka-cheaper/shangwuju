"""critics_v2 —— Itinerary 客观约束兜底验证层（edge_v1）。

【为什么叫 critics_v2 而非 critics】

`backend/agent/critics.py` 已存在（旧规则 critic 的内部组件，由 planner_hybrid 用）。
本模块（v2）是 LangGraph `agent/graph/nodes/critic.py` 节点 + Pydantic AI ReAct
fallback 路径共用的 Itinerary 级 critic，与旧 critic 不冲突。

【edge_v1 重构后的 critic 模型】

输入是 `Itinerary(nodes=[home, ..., home], hops=[...])`，单位天然清晰：
- nodes：「在哪里、做什么、停留多久」
- hops：「相邻两节点间怎么过去、几分钟」

旧 stage 模型把"在 home 停 N 分钟"与"home→POI 通勤 N 分钟"塞同一字段，导致
critic 双重计算触发死循环（pitfalls P1-2026-05-22-commute-critic）。重构后：
- `_check_temporal_feasibility`：from_node.end + hop.minutes + buffer ≤ to_node.start（容差 2min）
- `_check_hop_feasibility`：遍历 hops，非 in_place 段调 `lookup_hop` 取 actual_min，
  断言 `hop.minutes >= actual_min - 2`（与 assemble 共享同一函数 → 同输入同输出）
- `_check_invariants`：hops 长度 / 首尾 home / home duration=0 三条结构断言（防御性兜底）

【文件结构（spec code-modularization-refactor H6）】

本文件仅含**公开 API**：
- ViolationCode / Severity / Violation（枚举与数据结构）
- validate_itinerary / format_violations_for_llm（主入口）

具体规则实现 13 个 check_xxx 函数 → `_rules/checks.py`
共享 helper（数据加载 / 时间解析 / 节点工具）→ `_rules/helpers.py`
公共类型常量 → `_rules/types.py`

【Critic 纪律（硬性）】

- 不抛异常（违规返回 violations 列表，由调用方决定是否 ModelRetry / replan）
- 不调 LLM（critic 是算法不是 LLM）
- 不发明新 schema 模型（直接接受 Itinerary + IntentExtraction）
- field_path 字段仅供 trace / 调试使用，**format_violations_for_llm 不暴露 dot-path**
  给 LLM——LLM 只看人话「第 N 段」「目标点」（design.md 强约束）

【ADR-0008 B-1：删除 reward/feedback-mode 机制】

- `compute_reward` 已删（随 SEVERITY_WEIGHTS / CODE_WEIGHTS 一起死）
- `_get_feedback_mode` / first-only / reward 模式已删
- `format_violations_for_llm` 永远 collect-all HARD violations（简洁可预测）
"""

from __future__ import annotations

from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary

from ._rules.checks import (
    check_age_aware_duration,
    check_capacity,
    check_demo_restaurant_full,
    check_dietary,
    check_distance,
    check_duration,
    check_hop_feasibility,
    check_invariants,
    check_meal_time,
    check_nodes_incomplete,
    check_social_context,
    check_temporal_alignment,
    check_temporal_feasibility,
    check_time_parseable,
    check_tool_consistency,
)
from ._rules.helpers import safe_load_user_profile  # noqa: F401  兼容旧 import 路径
from ._rules.types import (
    Severity,
    Violation,
    ViolationCode,
)
from .context import CriticContext
from .validate import validate


# ============================================================
# 向后兼容：保留原 _check_xxx / _safe_load_xxx 私有名
# ============================================================
# 历史外部测试 / scripts 直接 import 私有名（如 `from critics_v2 import _check_age_aware_duration`），
# 拆分后通过别名指向 _rules.checks 实现，保持 API 兼容。
_check_invariants = check_invariants
_check_nodes_incomplete = check_nodes_incomplete
_check_duration = check_duration
_check_temporal_feasibility = check_temporal_feasibility  # B-2a: 已拆为 _check_time_parseable + _check_temporal_alignment
_check_time_parseable = check_time_parseable      # B-2a 新增：Stage 0 时间可解析性门
_check_temporal_alignment = check_temporal_alignment  # B-2a 新增：Stage 1 hop/buffer 对齐
_check_hop_feasibility = check_hop_feasibility
_check_distance = check_distance
_check_demo_restaurant_full = check_demo_restaurant_full
_check_dietary = check_dietary
_check_social_context = check_social_context
_check_age_aware_duration = check_age_aware_duration
_check_tool_consistency = check_tool_consistency
_check_capacity = check_capacity
_check_meal_time = check_meal_time

# 兼容旧 monkeypatch 风格的测试（test_critics_v2_hop.py 用 critic_mod.lookup_hop spy）
# critics_v2.lookup_hop 与 _rules.checks.lookup_hop 指向同一函数；
# 真正生效的是 _rules/checks.py 里的 import，但保留本属性让 monkeypatch 不抛 AttributeError。
from agent.planning.commute.lookup_hop import lookup_hop  # noqa: E402, F401


# ============================================================
# 公开 API：类型 / 枚举 re-export
# ============================================================
# 历史 import 路径 `from agent.planning.critic.critics_v2 import ViolationCode, Severity, ...`
# 通过本模块重新导出仍然成立。

__all__ = [
    "ViolationCode",
    "Severity",
    "Violation",
    "validate_itinerary",
    "format_violations_for_llm",
]


# ============================================================
# 主入口
# ============================================================


def validate_itinerary(
    itinerary: Itinerary,
    intent: IntentExtraction,
    *,
    user_id: str = "demo_user",
    tool_results: dict | None = None,
) -> list[Violation]:
    """跑全套 critic 检查（分阶段）。返回 violations 列表（可能为空）。

    顺序约定（ADR-0008 B-2a 分阶段）：
    Stage 0（结构门，命中短路）：
      1. INVARIANT_BROKEN（防御性兜底）
      2. NODES_INCOMPLETE（按 decide_nodes→target_kind 判，B-2a B1 修订）
      3. TIMELINE_INCONSISTENT / check_time_parseable（时间可解析性，G2 拆位）
      4. TOOL_RESPONSE_INCONSISTENCY（hallucination 防护）
    Stage 1（hard 语义，gate 修复）：
      5. DURATION_OUT_OF_RANGE（总时长容差）
      6. HOP_INFEASIBLE（_check_hop_feasibility）
      7. TIMELINE_INCONSISTENT / check_temporal_alignment（hop/buffer 对齐，G2 拆位）
      8. RESTAURANT_FULL_UNRESOLVED（demo-aware）
      9. SOCIAL_CONTEXT_MISMATCH（critical / warning 分级）
      10. AGE_DURATION_MISMATCH（spec planning-quality-deep-review R4）
      11. CAPACITY_REQUIREMENT_VIOLATED（spec innovation-review M3：≥5 人桌型不够）
      12. DIETARY_VIOLATION（B-2a 升 HARD，gate 修复）
      13. MEAL_TIME_UNREASONABLE（B-2a 升 HARD，gate 修复）
    Stage 2（soft 建议，narration only）：
      14. DISTANCE_EXCEEDED（warning）

    Args:
        itinerary:    要校验的方案（已通过 Pydantic 构造）。
        intent:       用户意图，提供 duration_hours / distance_max_km / dietary 等约束。
        user_id:      用于查 UserProfile（含 home_location / transport_preference）。
        tool_results: 可选；包含 {"pois": list, "restaurants": list} 候选池快照，
                      用于 hallucination 防护检查。None 时跳过此检查（向后兼容）。

    Returns:
        Violation 列表；调用方据 severity 决定是否 backprompt / replan。
        Stage 0 有违规时只含结构/幻觉违规（短路），否则含 Stage 1+2 所有违规。
    """
    ctx = CriticContext.build(intent, user_id=user_id, tool_results=tool_results)
    return validate(itinerary, ctx)


def format_violations_for_llm(violations: list[Violation]) -> str:
    """把 hard violations 格式化成给 LLM 的 backprompt 消息。

    【ADR-0008 B-1：永远 collect-all HARD violations】

    删除 first-only / reward 反馈模式分支（+ `_get_feedback_mode` + CRITIC_FEEDBACK_MODE env）。
    简化为：collect-all HARD → 拼成一条 backprompt。

    【人话约束（design.md 强约束）】

    输出**不暴露 dot-path** 字段路径——LLM 只看「第 N 段」「目标点」「分钟」等
    自然语言。`Violation.field_path` 仅用于 trace / 调试，绝不进 LLM prompt。

    spec planning-quality-deep-review R4：
    - 若 violation 含 `expected_range=(lo, hi)`，message 末尾追加「（建议范围 lo-hi min）」
    - **不**暴露字段名 `expected_range` / `nodes[i]` 等 dot-path

    - 0 hard → 返回空字符串（调用方据此决定不 backprompt）
    - ≥1 hard → 返回中文修复 prompt（编号 + message）
    - soft 级别**不**进入此消息（避免噪声把 LLM 注意力分散）
    """
    hard = [v for v in violations if v.severity == Severity.HARD]
    if not hard:
        return ""

    lines = [f"你产出的行程方案有 {len(hard)} 处违规需要修复："]
    for i, v in enumerate(hard, 1):
        # 注意：刻意不拼接 v.field_path（design.md：不暴露 dot-path）
        msg = v.message
        if v.expected_range is not None:
            lo, hi = v.expected_range
            msg = f"{msg}（建议范围 {lo}-{hi} min）"
        lines.append(f"{i}. {msg}")
    lines.append("请按上述建议重新调用工具或调整方案，重新输出 ItineraryResponse。")
    return "\n".join(lines)
