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

本文件仅含**公开 API**（约 200 行）：
- ViolationCode / Severity / Violation（枚举与数据结构）
- CODE_WEIGHTS / SEVERITY_WEIGHTS（reward 权重）
- compute_reward（dense scalar）
- validate_itinerary / format_violations_for_llm（主入口）

具体规则实现 11 个 _check_xxx 函数 → `_rules/checks.py`
共享 helper（数据加载 / 时间解析 / 节点工具）→ `_rules/helpers.py`
公共类型 / 权重常量 → `_rules/types.py`

【Critic 纪律（硬性）】

- 不抛异常（违规返回 violations 列表，由调用方决定是否 ModelRetry / replan）
- 不调 LLM（critic 是算法不是 LLM）
- 不发明新 schema 模型（直接接受 Itinerary + IntentExtraction）
- field_path 字段仅供 trace / 调试使用，**format_violations_for_llm 不暴露 dot-path**
  给 LLM——LLM 只看人话「第 N 段」「目标点」（design.md 强约束）

【11 类 ViolationCode】

```
| Code                       | Severity (默认) | 触发条件                                       |
|----------------------------|----------------|-----------------------------------------------|
| INVARIANT_BROKEN           | CRITICAL       | hops 长度 / 首尾 home / home duration=0 任一违反 |
| NODES_INCOMPLETE           | CRITICAL       | mid nodes 数 < 1（行程退化为只有 home）         |
| DURATION_OUT_OF_RANGE      | CRITICAL       | total_minutes 不在 intent.duration_hours±30min |
| TIMELINE_INCONSISTENT      | CRITICAL       | hop.start 与 from_node.end / to_node.start 错位（容差 2min） |
| HOP_INFEASIBLE             | CRITICAL       | hop.minutes < lookup_hop(actual) - 2          |
| DISTANCE_EXCEEDED          | WARNING        | 单个 mid node 距家 > intent.distance_max_km   |
| RESTAURANT_FULL_UNRESOLVED | CRITICAL       | demo-aware：用餐 node start_time 满座         |
| DIETARY_VIOLATION          | WARNING        | 餐厅 node tags 不覆盖 intent.dietary_constraints |
| SOCIAL_CONTEXT_MISMATCH    | CRITICAL/WARN  | social_compat 矩阵 BLOCKING/POOR              |
| AGE_DURATION_MISMATCH      | CRITICAL       | 单段 POI 时长超出年龄 cap                       |
| TOOL_RESPONSE_INCONSISTENCY| CRITICAL       | target_id 不在候选池（hallucination）          |
| CAPACITY_REQUIREMENT_VIOLATED | CRITICAL    | ≥5 人但餐厅无大桌型                            |
```

【不负责】

- ModelRetry / replan 触发逻辑（由 LangGraph critic_node / react_agent 决定）
- 主观文案生成（critic 输出 message 只是 LLM 修复种子，不是最终回话）
- 工具调用历史的事后分析（critic 看不到调用链，只看最终 itinerary）
- 节点级营业时间校验（在 agent.blueprint._opening_hours_critic 阶段处理）
"""

from __future__ import annotations

import os

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
    check_temporal_feasibility,
    check_tool_consistency,
)
from ._rules.helpers import safe_load_user_profile  # noqa: F401  兼容旧 import 路径
from ._rules.types import (
    CODE_WEIGHTS,
    SEVERITY_WEIGHTS,
    VALID_FEEDBACK_MODES,
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
_check_temporal_feasibility = check_temporal_feasibility
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
# 公开 API：类型 / 枚举 / 权重 re-export
# ============================================================
# 历史 import 路径 `from agent.planning.critic.critics_v2 import ViolationCode, Severity, ...`
# 通过本模块重新导出仍然成立。

__all__ = [
    "ViolationCode",
    "Severity",
    "Violation",
    "validate_itinerary",
    "format_violations_for_llm",
    # spec algorithm-redesign R1：reward 计算 + 三档反馈模式
    "SEVERITY_WEIGHTS",
    "CODE_WEIGHTS",
    "compute_reward",
]


# ============================================================
# spec algorithm-redesign R1：reward 计算 + 反馈模式
# ============================================================
#
# LLM-Modulo 范式（参考 .kiro/specs/algorithm-redesign/research/agent-3-llm-modulo）
# 把 critic 视为「dense scalar reward 提供者」，为未来 RL 路径预留挂钩；
# 当前主路径仍走 pinpoint-all（默认）的人话 backprompt。


def compute_reward(violations: list[Violation]) -> float:
    """把 violations 列表压成单个标量 reward（≤ 0，越接近 0 越好）。

    公式：``-Σ SEVERITY_WEIGHTS[v.severity] * CODE_WEIGHTS.get(v.code, 1.0)``

    用途：
    - 当前主路径不消费此值（backprompt 走 format_violations_for_llm）
    - CRITIC_FEEDBACK_MODE=reward 时由调用方读取（占位，本 spec 不接 RL）
    - 未来 spec D 若做 RL 路径实验直接 hook 此函数

    Args:
        violations: validate_itinerary 输出（可能为空）

    Returns:
        非正数标量；空列表返 0.0；多违规累加

    示例：
        - 空列表 → 0.0
        - 单条 CRITICAL + INVARIANT_BROKEN(1.5) → -1.5
        - 单条 WARNING + DISTANCE_EXCEEDED(0.8) → -0.16
        - 1 critical + 2 warning → 三者之和的负值
    """
    if not violations:
        return 0.0

    total = 0.0
    for v in violations:
        sev_w = SEVERITY_WEIGHTS.get(v.severity, 1.0)
        code_w = CODE_WEIGHTS.get(v.code, 1.0)
        total += sev_w * code_w
    return -total


def _get_feedback_mode() -> str:
    """从 env CRITIC_FEEDBACK_MODE 读三档模式，越界回退 pinpoint-all。

    三档：
    - pinpoint-all（默认）：返完整违规列表给 LLM（与 spec C 之前行为一致）
    - first-only：仅第一条 critical（节省 token 30-50%，sub-agent 实验路径）
    - reward：返空字符串（dense scalar 模式，由调用方独立调 compute_reward）

    任何其它值（typo / 大小写错乱 / 空字符串）→ fallback pinpoint-all + stderr warn。
    """
    raw = os.getenv("CRITIC_FEEDBACK_MODE", "pinpoint-all").strip().lower()
    if raw in VALID_FEEDBACK_MODES:
        return raw
    # 越界值 → fallback + stderr warning（避免静默错配置）
    import sys

    print(
        f"[critics_v2] CRITIC_FEEDBACK_MODE={raw!r} 不在 "
        f"{sorted(VALID_FEEDBACK_MODES)} 范围，回退到 pinpoint-all。",
        file=sys.stderr,
    )
    return "pinpoint-all"


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
    """跑全套 critic 检查。返回 violations 列表（可能为空）。

    顺序约定（先「结构性 / 强制性」后「语义性 / 偏好性」）：
    1. INVARIANT_BROKEN（防御性兜底）
    2. NODES_INCOMPLETE（mid 节点至少 1 个）
    3. DURATION_OUT_OF_RANGE（总时长容差）
    4. TIMELINE_INCONSISTENT（_check_temporal_feasibility）
    5. HOP_INFEASIBLE（_check_hop_feasibility）
    6. DISTANCE_EXCEEDED（warning）
    7. RESTAURANT_FULL_UNRESOLVED（demo-aware）
    8. DIETARY_VIOLATION（warning）
    9. SOCIAL_CONTEXT_MISMATCH（critical / warning 分级）
    10. AGE_DURATION_MISMATCH（spec planning-quality-deep-review R4）
    11. TOOL_RESPONSE_INCONSISTENCY（spec algorithm-redesign R2，仅 tool_results 提供时）
    12. CAPACITY_REQUIREMENT_VIOLATED（spec innovation-review M3：≥5 人桌型不够）

    Args:
        itinerary:    要校验的方案（已通过 Pydantic 构造）。
        intent:       用户意图，提供 duration_hours / distance_max_km / dietary 等约束。
        user_id:      用于查 UserProfile（含 home_location / transport_preference）。
        tool_results: 可选；包含 {"pois": list, "restaurants": list} 候选池快照，
                      用于 hallucination 防护检查。None 时跳过此检查（向后兼容）。

    Returns:
        Violation 列表；调用方据 severity 决定是否 backprompt / replan。

    【ADR-0008 Phase A：本函数已收窄为 thin shim】

    签名与返回类型保持不变（所有调用方/测试无感）；内部改走新接缝：
    1. `CriticContext.build` 一次性载入 profile / 全量 mock / tool_results 快照；
    2. `validate(itinerary, ctx)` 遍历 Check 注册表 flat collect-all（当前顺序）。
    与重构前逐字节等价——唯一区别是数据加载一次而非逐 check 加载。顺序 / 短路 /
    CRITICAL·WARNING 语义全部不变（分阶段短路与 tier 调整属 Phase B）。
    """
    ctx = CriticContext.build(intent, user_id=user_id, tool_results=tool_results)
    return validate(itinerary, ctx)


def format_violations_for_llm(violations: list[Violation]) -> str:
    """把 critical violations 格式化成给 LLM 的 backprompt 消息。

    【三档反馈模式（spec algorithm-redesign R1）】

    通过 env `CRITIC_FEEDBACK_MODE` 切换：

    - **pinpoint-all（默认）**：完整违规列表（与 spec C 之前行为一致）
    - **first-only**：仅第一条 critical（节省 token 30-50%，第二意见探索）
    - **reward**：返空字符串（dense scalar 模式，配合 compute_reward 用，
      调用方需独立调用 compute_reward 取 reward 值）

    【人话约束（design.md 强约束）】

    输出**不暴露 dot-path** 字段路径——LLM 只看「第 N 段」「目标点」「分钟」等
    自然语言。`Violation.field_path` 仅用于 trace / 调试，绝不进 LLM prompt。

    spec planning-quality-deep-review R4：
    - 若 violation 含 `expected_range=(lo, hi)`，message 末尾追加「（建议范围 lo-hi min）」
    - **不**暴露字段名 `expected_range` / `nodes[i]` 等 dot-path

    - 0 critical → 返回空字符串（调用方据此决定不 backprompt）
    - ≥1 critical → 返回中文修复 prompt（编号 + message）
    - warning 级别**不**进入此消息（避免噪声把 LLM 注意力分散）
    """
    critical = [v for v in violations if v.severity == Severity.CRITICAL]
    if not critical:
        return ""

    mode = _get_feedback_mode()
    if mode == "reward":
        # reward 模式：调用方独立调 compute_reward；此函数返空让主路径不 backprompt
        return ""

    if mode == "first-only":
        # 仅第一条 critical
        critical = critical[:1]

    lines = [f"你产出的行程方案有 {len(critical)} 处违规需要修复："]
    for i, v in enumerate(critical, 1):
        # 注意：刻意不拼接 v.field_path（design.md：不暴露 dot-path）
        msg = v.message
        if v.expected_range is not None:
            lo, hi = v.expected_range
            msg = f"{msg}（建议范围 {lo}-{hi} min）"
        lines.append(f"{i}. {msg}")
    lines.append("请按上述建议重新调用工具或调整方案，重新输出 ItineraryResponse。")
    return "\n".join(lines)
