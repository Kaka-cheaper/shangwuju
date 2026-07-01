"""validate —— Check 注册表 + 单一校验入口（ADR-0008 决策 1/2）。

【这是什么问题 + 成熟做法】

校验层是「一组对计划对象的断言」，对应 **Specification 模式（Evans/Fowler）+ 显式
metacontroller**。Fowler 明确警告别用「隐式规则引擎」（链式流不可维护），单一 bounded
域用**显式注册表 + 显式遍历**更清晰。本模块即该显式 metacontroller：
- `CheckSpec`：一条 Specification 的元数据（code / stage / tier / 实现函数）。
- `REGISTRY`：**显式有序列表**，按 stage 分组，组内保留原调用顺序。
- `validate`：分阶段校验——Stage 0 结构门命中则短路，否则 Stage 1+2 collect-all。

【Phase B-1：引入分阶段短路（有意行为改变）】

ADR-0008 G4 决策：分阶段短路属 Phase B，不在 Phase A（行为保持）。
B-1 实现 ADR-0008 决策 2 的 staging + short-circuit：

- Stage 0（结构门）：check_invariants / check_nodes_incomplete /
  check_temporal_feasibility / check_tool_consistency
  → 阶段内 collect-all；命中任一违规即**短路，返回 Stage-0 违规，不再跑 Stage 1/2**。
  语义：结构破损或幻觉方案上跑语义校验是噪声，Stage-0 短路省掉这些噪声。

- Stage 1（hard 语义，gate 修复）：check_duration / check_hop_feasibility /
  check_demo_restaurant_full / check_social_context /
  check_age_aware_duration / check_capacity

- Stage 2（soft 建议，narration only）：check_distance / check_dietary /
  check_meal_time

Stage 0 无违规时，Stage 1 + Stage 2 **collect-all 跨两阶段**（soft 建议与 hard
诊断并列——LLM-Modulo 原则：soft 不 gate，但要让 LLM 看到）。

【tier 与 stage 的关系】

- dietary / meal_time：B-1 仍为 tier="soft"（B-2 升 hard），但 stage 已归到 2。
- social_context：单 check 同时产 HARD（BLOCKING）与 SOFT（POOR），stage=1（取其 gating 能力）。
- temporal_feasibility：B-1 归 Stage 0（结构门），B-2 的 G2 拆位视具体需求推后。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

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
from ._rules.types import Violation, ViolationCode
from .context import CriticContext


@dataclass(frozen=True)
class CheckSpec:
    """一条 Check 的注册元数据。

    - `code`：该 check 产出的主 ViolationCode（标识用）。
    - `stage`：0/1/2，按 ADR-0008 roster 的目标分阶段。B-1 起消费用于分阶段路由。
    - `tier`："hard"/"soft"，按当前 severity 映射。B-1 dietary/meal_time 仍 soft（B-2 升 hard）。
    - `fn`：实现函数，统一以 `fn(plan, ctx=ctx)` 调用（数据从 CriticContext 注入）。
    """

    code: ViolationCode
    stage: int
    tier: str
    fn: Callable[..., list[Violation]]


# 显式有序注册表 —— 按 stage 分组，组内顺序严格等于原调用顺序（行为保持组内顺序）。
# 调序 / 新增 / tier 变更 = 行为改变，属 Phase B-2+。
REGISTRY: list[CheckSpec] = [
    # ── Stage 0: 结构门（命中任一 → 短路，不运行 Stage 1/2） ──────────────
    CheckSpec(ViolationCode.INVARIANT_BROKEN, 0, "hard", check_invariants),
    CheckSpec(ViolationCode.NODES_INCOMPLETE, 0, "hard", check_nodes_incomplete),
    CheckSpec(ViolationCode.TIMELINE_INCONSISTENT, 0, "hard", check_temporal_feasibility),
    CheckSpec(ViolationCode.TOOL_RESPONSE_INCONSISTENCY, 0, "hard", check_tool_consistency),
    # ── Stage 1: hard 语义（gate 修复） ────────────────────────────────────
    CheckSpec(ViolationCode.DURATION_OUT_OF_RANGE, 1, "hard", check_duration),
    CheckSpec(ViolationCode.HOP_INFEASIBLE, 1, "hard", check_hop_feasibility),
    CheckSpec(ViolationCode.RESTAURANT_FULL_UNRESOLVED, 1, "hard", check_demo_restaurant_full),
    # social：BLOCKING(hard/stage1) + POOR(soft/stage2) 同 check；标其 gating 能力
    CheckSpec(ViolationCode.SOCIAL_CONTEXT_MISMATCH, 1, "hard", check_social_context),
    CheckSpec(ViolationCode.AGE_DURATION_MISMATCH, 1, "hard", check_age_aware_duration),
    CheckSpec(ViolationCode.CAPACITY_REQUIREMENT_VIOLATED, 1, "hard", check_capacity),
    # ── Stage 2: soft 建议（narration only，不 gate） ──────────────────────
    CheckSpec(ViolationCode.DISTANCE_EXCEEDED, 2, "soft", check_distance),
    # dietary / meal_time：B-1 保持 soft；B-2 升 hard 与 stage 对齐
    CheckSpec(ViolationCode.DIETARY_VIOLATION, 2, "soft", check_dietary),
    CheckSpec(ViolationCode.MEAL_TIME_UNREASONABLE, 2, "soft", check_meal_time),
]


def validate(plan: Itinerary, ctx: CriticContext) -> list[Violation]:
    """分阶段校验（ADR-0008 B-1）：Stage-0 结构门命中则短路。

    Stage 0（结构门）阶段内 collect-all；若有任何违规则立即返回，跳过 Stage 1/2。
    Stage 0 无违规时，Stage 1 + Stage 2 collect-all（两阶段合并，soft 建议与 hard
    诊断并列返回）。

    Args:
        plan: 待校验的 Itinerary。
        ctx:  CriticContext（intent / profile / 全量 mock / tool_results 快照）。

    Returns:
        Violation 列表（Severity.HARD / Severity.SOFT，阶段内按注册顺序）。
        Stage 0 短路时只含结构违规；Stage 0 通过时含 Stage 1+2 所有违规。
    """
    stage0 = [s for s in REGISTRY if s.stage == 0]
    stage1_2 = [s for s in REGISTRY if s.stage >= 1]

    # Stage 0: 结构门——collect-all，命中即短路
    s0_violations: list[Violation] = []
    for spec in stage0:
        s0_violations.extend(spec.fn(plan, ctx=ctx))

    if s0_violations:
        # 结构破损 / 幻觉方案：语义校验是噪声，直接返回结构违规
        return s0_violations

    # Stage 0 通过：Stage 1 + Stage 2 collect-all（不再短路）
    violations: list[Violation] = []
    for spec in stage1_2:
        violations.extend(spec.fn(plan, ctx=ctx))
    return violations
