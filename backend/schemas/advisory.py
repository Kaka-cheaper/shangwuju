"""schemas.advisory —— 规划器「绝不默默忽略」的结构化告知通道（ADR-0010 决策 11 / D-7）。

【这是什么问题 + 为什么 Violation 不适用】

ADR-0010 决策 11「首要 UX 铁律：绝不默默忽略用户的明确请求」要求规划器办不到
用户要求时必须告知原因，而不是静默丢弃/截断。这条语义**不能**复用既有的
`agent.planning.critic._rules.types.Violation`——两者是不同轴：

- `Violation`（critic 层）：**方案缺陷**，须 gate 修复（HARD 进修复闭环）或至少
  提醒质量问题（SOFT）。它衡量的是「这份方案本身有没有毛病」。
- `Advisory`（本模块）：**限制/建议的告知**，不 gate 任何东西——方案本身可能
  已经是「这组约束下能做到的最好结果」，advisory 只是如实说明"哪里没能完全
  如你所愿、为什么、能怎么办"（如点名的目标排不进、超出预算、时长比期望短）。
  ADR-0010 决策 11 原文明确把这条通道叫作「advisory 通道（planner → narration；
  区别于 critic 的 hard 违规——这不是缺陷，是「限制/建议」）」。

用 `Violation` 硬套会把「合理的限制告知」误标成「方案缺陷」，读者（LLM backprompt
消费方 / 未来的告警聚合）就会试图「修复」一个根本不该被修复的东西（比如
「超预算」在 ADR 定义里是软性提醒，不是要求算法必须把预算压回去）。两条通道
分轴，各自服务对应的下游动作。

【消息纪律（对齐 Violation.message 的既有纪律）】

`Advisory.message` 必须是给用户看的**自包含中文人话完整句子**（不依赖上下文、
不暴露内部字段名/id），与 `Violation.message`「必须自包含第几段、什么目标」
同一纪律——因为它最终会被原样（或与其它句子拼接）呈现在 narrator 的开场白里
（`agent.graph.nodes.narrate` → `agent.intent.narrator`），不会被二次改写。

【跨层路径（本模块被谁消费）】

`planner`（`agent.planning.planners.ils_planner.plan_hybrid` 产出
`HybridResult.advisories`）→ `state`（`agent.graph.state.AgentState.advisories`，
经 `ils_replan_node` 写入）→ `narrate`（`agent.graph.nodes.narrate` 拼进 narrator
文案）→ `SSE`（`agent.graph._emit_handlers.emit_narrate` 的 AGENT_NARRATION
payload 结构化条目）。放在 `schemas/` 而非 `agent/planning/` 下正是因为它跨越
这整条链路，不是规划层的内部概念（这一点与 `Violation` 不同——`Violation` 只在
critic↔planner 之间流转，从未越过 planner 边界）。

不负责：
- 告知的触发条件/文案措辞细节（在 `ils_planner.plan_hybrid`）。
- 呈现（narrator 模板/LLM prompt 拼接 + SSE payload 组装）。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AdvisoryCode(str, Enum):
    """advisory 触发码（ADR-0010 决策 11 三类触发的具体化）。"""

    # 点名必去的目标：resolve 到了，但排不进最终路线（时间/路线塞不下）。
    PINNED_UNSATISFIABLE = "pinned_unsatisfiable"
    # 点名必去的目标：本来排进去了，但 critic 修复闭环为了保住整体方案把它换掉了。
    PINNED_DROPPED_IN_REPAIR = "pinned_dropped_in_repair"
    # 最终方案总时长比用户期望的下限短（候选稀薄，宁可短而好，不塞次优凑数）。
    SHORTER_THAN_REQUESTED = "shorter_than_requested"
    # 最终方案总花费超出用户常用预算。
    OVER_BUDGET = "over_budget"
    # 点名必去的目标：在已召回的候选池里完全找不到匹配的实体。
    NO_MATCHING_CANDIDATES = "no_matching_candidates"

    # ---- ADR-0013 F-1：局部重解引擎（换菜/定向调整）----
    # 换菜降级到第三级「近似满足」——没找到完全符合调整请求的候选，给了同 kind
    # 内最接近的一个（仍可能不满足用户点的那个方向）。
    SWAP_DEGRADED = "swap_degraded"
    # 换掉目标节点后，钉住不动的其余节点在时间/路线上拼不到一块儿了（如中间站
    # 被抽走后两端直达通勤暴涨）——无法只动这一格，方案保持原样未变。复用 D-7
    # `PINNED_UNSATISFIABLE` 的「绝不静默、如实告知」先例语义。
    SWAP_KEPT_NODE_UNFIT = "swap_kept_node_unfit"
    # 同 kind 的候选池里连一个能塞进现有时间/路线的替代都没有——这一格彻底换不了，
    # 方案保持原样未变。
    SWAP_NO_ALTERNATIVE_FOUND = "swap_no_alternative_found"


class Advisory(BaseModel):
    """一条「限制/建议」告知——不 gate 方案，只如实说明（区别于 Violation，见模块 docstring）。"""

    model_config = ConfigDict(extra="forbid")

    code: AdvisoryCode
    message: str = Field(
        ...,
        min_length=1,
        description="给用户看的中文完整句子；必须自包含，不依赖上下文、不暴露内部字段名/id",
    )


__all__ = ["AdvisoryCode", "Advisory"]
