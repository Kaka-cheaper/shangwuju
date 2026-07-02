"""validate —— Check 注册表 + 单一校验入口（ADR-0008 决策 1/2）。

【这是什么问题 + 成熟做法】

校验层是「一组对计划对象的断言」，对应 **Specification 模式（Evans/Fowler）+ 显式
metacontroller**。Fowler 明确警告别用「隐式规则引擎」（链式流不可维护），单一 bounded
域用**显式注册表 + 显式遍历**更清晰。本模块即该显式 metacontroller：
- `CheckSpec`：一条 Specification 的元数据（code / stage / tier / 实现函数）。
- `REGISTRY`：**显式有序列表**，按 stage 分组，组内保留原调用顺序。
- `validate`：分阶段校验——Stage 0 结构门命中则短路，否则 Stage 1+2 collect-all。

【Phase B-2a：tier 升级 + 节点完整性重设计 + 时间检查拆位（有意行为改变）】

ADR-0008 B-2a 对 B-1 的增量改变：

- Stage 0（结构门）：check_invariants / check_nodes_incomplete /
  check_time_parseable（拆自原 check_temporal_feasibility 的解析部分）/
  check_tool_consistency
  → 阶段内 collect-all；命中任一违规即**短路，返回 Stage-0 违规，不再跑 Stage 1/2**。

- Stage 1（hard 语义，gate 修复）：check_duration / check_hop_feasibility /
  check_temporal_alignment（拆自原 check_temporal_feasibility 的对齐部分）/
  check_demo_restaurant_full / check_opening_hours（B-2b 新增）/
  check_social_context / check_age_aware_duration / check_capacity /
  check_dietary（B-2a 升 HARD）/ check_meal_time（B-2a 升 HARD）

- Stage 2（soft 建议，narration only）：check_distance

Stage 0 无违规时，Stage 1 + Stage 2 **collect-all 跨两阶段**（soft 建议与 hard
诊断并列——LLM-Modulo 原则：soft 不 gate，但要让 LLM 看到）。

【Phase B-2b：营业时间检查移植（新增，非 tier 调整）】

`check_opening_hours` 从死代码 blueprint._opening_hours_critic 移植营业时间判定
逻辑（`_is_in_business_hours`），作用对象改为已 assemble 的 Itinerary（真实
node.start_time，含 hop 通勤耗时），注册在 Stage 1 hard——填补 ADR-0008 背景诊断
指出的「营业时间校验生产无任何实现」漏检。

【ADR-0010 D-3：check_duration 拆向（修订 ADR-0008 tier 表，intentional 行为改变）】

`check_duration` 从"越界一律 HARD"改为"超长 HARD / 不足 SOFT"——单 check 同时
产两种 severity，注册模式与 `check_social_context` 相同（tier 标签取其 gating
能力，仍标 "hard"，因为它仍能产出 HARD）。**本次改动不需要动 REGISTRY 的这一行
注册**，也不需要动 `ils_planner._classify_violation`——两处消费方都已经按
`Violation.severity` 分派（`HybridCriticReport.passed` 只看 HARD；
`_classify_violation` 对非 HARD 一律先返回空集合，不看 code），severity 降级
后行为自动跟着对；具体推理见 `_rules/checks.py:check_duration` docstring。

【tier 与 stage 的关系】

- dietary / meal_time：B-2a 升 hard，归 Stage 1（gate 修复）。
- social_context：单 check 同时产 HARD（BLOCKING）与 SOFT（POOR），stage=1（取其 gating 能力）。
- duration（ADR-0010 D-3）：单 check 同时产 HARD（超长）与 SOFT（不足），stage=1，
  与 social_context 同一注册模式。
- temporal_feasibility (G2 拆位)：可解析 → Stage 0 check_time_parseable；
  hop/buffer 对齐 → Stage 1 check_temporal_alignment。
- nodes_incomplete (B1 修订)：改按 decide_nodes→target_kind 判，不按自由文本 kind。
- opening_hours (B-2b 新增)：Stage 1 hard，None-guard 防重蹈 O4 的 TypeError。
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
    check_opening_hours,
    check_social_context,
    check_temporal_alignment,
    check_temporal_feasibility,  # noqa: F401  保留兼容：critics_v2 别名仍用
    check_time_parseable,
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
# 调序 / 新增 / tier 变更 = 有意行为改变，记录在 ADR-0008 B-2a。
REGISTRY: list[CheckSpec] = [
    # ── Stage 0: 结构门（命中任一 → 短路，不运行 Stage 1/2） ──────────────
    CheckSpec(ViolationCode.INVARIANT_BROKEN, 0, "hard", check_invariants),
    # B-2a: check_nodes_incomplete 改按 decide_nodes→target_kind 判（非自由文本 kind）
    CheckSpec(ViolationCode.NODES_INCOMPLETE, 0, "hard", check_nodes_incomplete),
    # B-2a G2 拆位：时间可解析 → Stage 0 结构门（原 check_temporal_feasibility 拆出）
    CheckSpec(ViolationCode.TIMELINE_INCONSISTENT, 0, "hard", check_time_parseable),
    CheckSpec(ViolationCode.TOOL_RESPONSE_INCONSISTENCY, 0, "hard", check_tool_consistency),
    # ── Stage 1: hard 语义（gate 修复） ────────────────────────────────────
    CheckSpec(ViolationCode.DURATION_OUT_OF_RANGE, 1, "hard", check_duration),
    CheckSpec(ViolationCode.HOP_INFEASIBLE, 1, "hard", check_hop_feasibility),
    # B-2a G2 拆位：hop/buffer 对齐 → Stage 1 hard（原 check_temporal_feasibility 拆出）
    CheckSpec(ViolationCode.TIMELINE_INCONSISTENT, 1, "hard", check_temporal_alignment),
    CheckSpec(ViolationCode.RESTAURANT_FULL_UNRESOLVED, 1, "hard", check_demo_restaurant_full),
    # B-2b G3：营业时间检查移植自死的 blueprint._opening_hours_critic（新增，非 tier 调整）
    CheckSpec(ViolationCode.OPENING_HOURS_VIOLATION, 1, "hard", check_opening_hours),
    # social：BLOCKING(hard/stage1) + POOR(soft/stage2) 同 check；标其 gating 能力
    CheckSpec(ViolationCode.SOCIAL_CONTEXT_MISMATCH, 1, "hard", check_social_context),
    CheckSpec(ViolationCode.AGE_DURATION_MISMATCH, 1, "hard", check_age_aware_duration),
    CheckSpec(ViolationCode.CAPACITY_REQUIREMENT_VIOLATED, 1, "hard", check_capacity),
    # B-2a: dietary / meal_time 升 HARD → 移入 Stage 1，驱动修复闭环
    CheckSpec(ViolationCode.DIETARY_VIOLATION, 1, "hard", check_dietary),
    CheckSpec(ViolationCode.MEAL_TIME_UNREASONABLE, 1, "hard", check_meal_time),
    # ── Stage 2: soft 建议（narration only，不 gate） ──────────────────────
    CheckSpec(ViolationCode.DISTANCE_EXCEEDED, 2, "soft", check_distance),
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
