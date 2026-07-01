"""validate —— Check 注册表 + 单一校验入口（ADR-0008 决策 1/2 的 Phase A 骨架）。

【这是什么问题 + 成熟做法】

校验层是「一组对计划对象的断言」，对应 **Specification 模式（Evans/Fowler）+ 显式
metacontroller**。Fowler 明确警告别用「隐式规则引擎」（链式流不可维护），单一 bounded
域用**显式注册表 + 显式遍历**更清晰。本模块即该显式 metacontroller：
- `CheckSpec`：一条 Specification 的元数据（code / stage / tier / 实现函数）。
- `REGISTRY`：**显式有序列表**，顺序 == 旧 `validate_itinerary` 调用顺序。
- `validate`：遍历注册表，flat collect-all。

【Phase A 边界：stage / tier 是惰性元数据，本阶段不消费】

ADR-0008 的目标是分阶段 hard/soft 短路（Stage 0 结构门短路 → Stage 1 hard gate →
Stage 2 soft 建议）。但 Phase A 是**纯结构迁移、行为逐字节保持**：
- `validate` 仍 **flat collect-all、不短路、当前顺序**——与旧 `validate_itinerary` 等价。
- `stage`（0/1/2，按 ADR-0008 roster 目标分阶段）与 `tier`（hard/soft）**已声明但不读取**。
- `tier` 按**当前 severity** 1:1 映射：CRITICAL-check → "hard"，WARNING-check → "soft"。
  Phase B 才翻转：据 stage 短路、据 tier 决定接受、并把 dietary/meal_time 等升级为 hard。

【stage/tier 标注里的已知「目标 vs 现状」错配（Phase B 收口）】

- `dietary` / `meal_time`：`stage=1`（roster 目标归 hard 语义阶段），但 `tier="soft"`
  （现状 severity 是 WARNING）。Phase B 把 tier 升 hard 与 stage 对齐。
- `social_context`：单 check 同时产 CRITICAL（BLOCKING，stage 1 hard）与 WARNING
  （POOR，roster 归 stage 2 soft）。Phase A 标 `stage=1, tier="hard"`（取其 gating 能力），
  POOR 子情形仍在同一 check 内产出，不受影响（元数据惰性）。
- `nodes_incomplete`：现状是「count≥3」结构门（stage 0）。Phase B 将以 target_kind
  完整性（stage 1 hard）替换其语义，但 Phase A 不动其判定。
- `temporal_feasibility`：现状一个 check 同时覆盖「时间可解析」（roster stage 0）与
  「hop/buffer 对齐 TIMELINE_INCONSISTENT」（roster stage 1 hard）。Phase A 标 `stage=1`
  （其发射码 TIMELINE_INCONSISTENT 的归属），G2 的拆位留待 Phase B。
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
    - `stage`：0/1/2，按 ADR-0008 roster 的目标分阶段。**Phase A 不消费**。
    - `tier`："hard"/"soft"，按**当前** severity 1:1 映射。**Phase A 不消费**。
    - `fn`：实现函数，统一以 `fn(plan, ctx=ctx)` 调用（数据从 CriticContext 注入）。
    """

    code: ViolationCode
    stage: int
    tier: str
    fn: Callable[..., list[Violation]]


# 显式有序注册表 —— 顺序严格等于旧 validate_itinerary 的调用顺序，
# 保证违规列表逐字节一致（行为保持）。新增/调序 = 行为改变，属 Phase B。
REGISTRY: list[CheckSpec] = [
    CheckSpec(ViolationCode.INVARIANT_BROKEN, 0, "hard", check_invariants),
    CheckSpec(ViolationCode.NODES_INCOMPLETE, 0, "hard", check_nodes_incomplete),
    CheckSpec(ViolationCode.DURATION_OUT_OF_RANGE, 1, "hard", check_duration),
    CheckSpec(ViolationCode.TIMELINE_INCONSISTENT, 1, "hard", check_temporal_feasibility),
    CheckSpec(ViolationCode.HOP_INFEASIBLE, 1, "hard", check_hop_feasibility),
    CheckSpec(ViolationCode.DISTANCE_EXCEEDED, 2, "soft", check_distance),
    CheckSpec(ViolationCode.RESTAURANT_FULL_UNRESOLVED, 1, "hard", check_demo_restaurant_full),
    # dietary：stage 目标=1（hard 语义），tier=现状 WARNING→soft（Phase B 升 hard）
    CheckSpec(ViolationCode.DIETARY_VIOLATION, 1, "soft", check_dietary),
    # social：BLOCKING(hard/stage1) + POOR(soft/stage2) 同 check；标其 gating 能力
    CheckSpec(ViolationCode.SOCIAL_CONTEXT_MISMATCH, 1, "hard", check_social_context),
    CheckSpec(ViolationCode.AGE_DURATION_MISMATCH, 1, "hard", check_age_aware_duration),
    CheckSpec(ViolationCode.TOOL_RESPONSE_INCONSISTENCY, 0, "hard", check_tool_consistency),
    CheckSpec(ViolationCode.CAPACITY_REQUIREMENT_VIOLATED, 1, "hard", check_capacity),
    # meal_time：stage 目标=1，tier=现状 WARNING→soft（Phase B 升 hard）
    CheckSpec(ViolationCode.MEAL_TIME_UNREASONABLE, 1, "soft", check_meal_time),
]


def validate(plan: Itinerary, ctx: CriticContext) -> list[Violation]:
    """跑注册表里全部 Check，flat collect-all，当前顺序。

    Phase A：**不读 stage/tier、不短路、不分 tier**——逐字节等价于旧 validate_itinerary。
    数据经 `ctx` 一次性注入各 check（不再逐 check 重复 safe_load_*）。

    Args:
        plan: 待校验的 Itinerary。
        ctx:  CriticContext（intent / profile / 全量 mock / tool_results 快照）。

    Returns:
        Violation 列表（CRITICAL/WARNING，与重构前同一 Violation 类型与顺序）。
    """
    violations: list[Violation] = []
    for spec in REGISTRY:
        violations.extend(spec.fn(plan, ctx=ctx))
    return violations
