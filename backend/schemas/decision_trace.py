"""decision_trace —— Agent 决策可解释性数据结构（Step 4 + Step 7）。

【为什么需要】

评分项 1（场景理解 20%）+ 评分项 2（Tool 编排合理性 25%）的核心是：
LLM 不能是黑盒——评委要看到「Agent 为什么选 R007 而不是 R008」、
「critic 第几次让 LLM 修正」、「权重为什么这样分」。

把这些散落在各节点的"决策痕迹"聚合到一个标准对象，前端 DecisionTraceCard
统一渲染折叠卡，evals 系统也能直接消费做 A/B 对比。

【字段语义】

- blueprint_rationale: LLM 自报的「这次行程为什么这样安排」
- weights_explanation : 4 维 utility 权重（comfort / time / cost / smoothness）的中文摘要
- critic_attempts    : 每次 critic 命中 + LLM 修正描述（按发生顺序）
- alternatives_considered: top-N 候选打分；每条标 reason_rejected
- fallback_chain     : Plan-and-Execute 4 级 fallback 实际走过的链路

【与 schemas/sse.py 的关系】

decision_trace 是「最终交付物的元数据」，挂在 Itinerary 上随 itinerary_ready 推流。
sse.py 的 CRITIC_VIOLATIONS / PLAN_FALLBACK 是「实时事件流」。
两者互补：实时事件让评委看到过程；trace 让评委可回看完整决策链。

不负责：
- LLM 调用 / Critic 实现
- 前端渲染（在 components/DecisionTraceCard.tsx）
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat


# ============================================================
# Critic 修正历史
# ============================================================

class CriticAttempt(BaseModel):
    """一次 critic 命中 + LLM 修正的元数据。"""

    model_config = ConfigDict(extra="forbid")

    attempt_n: int = Field(..., ge=1, description="第几次重试（从 1 开始）")
    violation_codes: list[str] = Field(
        default_factory=list,
        description="本次命中的违规码，如 ['commute_infeasible', 'duration_out_of_range']",
    )
    feedback_summary: str = Field(
        ...,
        description="给 LLM 的修正建议摘要（一段中文）",
    )
    resolved: bool = Field(
        default=False,
        description="本次修正是否消除了所有 critical 违规",
    )


# ============================================================
# 候选解释性
# ============================================================

class AlternativeCandidate(BaseModel):
    """一条「考虑过但未选」的候选。"""

    model_config = ConfigDict(extra="forbid")

    target_kind: str = Field(..., description="poi / restaurant")
    target_id: str
    target_name: str
    utility_score: Optional[NonNegativeFloat] = Field(
        default=None, description="效用打分（rating × tag 命中 × 距离衰减等）；越高越好"
    )
    rank: int = Field(..., ge=1, description="在所有候选中的排名（1 = 最高）")
    reason_rejected: str = Field(
        ...,
        description=(
            "未选原因：「距离更远 / 评分较低 / social_context 不匹配 / 已访问过 / "
            "餐厅时段满座」之一，必须中文一句话"
        ),
    )


# ============================================================
# Fallback 链
# ============================================================

class FallbackHop(BaseModel):
    """4 级 fallback 链每跳一次的记录。"""

    model_config = ConfigDict(extra="forbid")

    from_stage: str = Field(
        ..., description="llm_first / llm_backprompt / ils / rule"
    )
    to_stage: str
    reason: str = Field(..., description="降级原因，如「LLM 三次未通过 critic」")


# ============================================================
# 主体
# ============================================================

class DecisionTrace(BaseModel):
    """Agent 决策可解释性聚合——挂在 Itinerary 上随响应一起返回。"""

    model_config = ConfigDict(extra="forbid")

    blueprint_rationale: str = Field(
        default="",
        description='LLM 自报"为什么这样规划"；空串表示 LLM 没给出',
    )
    weights_explanation: str = Field(
        default="",
        description="4 维 utility 权重的中文摘要，如「重舒适 0.35 / 重时长 0.30 / 重花销 0.20 / 重顺滑 0.15」",
    )
    critic_attempts: list[CriticAttempt] = Field(
        default_factory=list,
        description="按时间顺序的 critic 修正历史；空列表表示一次过",
    )
    alternatives_considered: list[AlternativeCandidate] = Field(
        default_factory=list,
        description="top-N 候选的元数据，按 rank 升序",
    )
    fallback_chain: list[FallbackHop] = Field(
        default_factory=list,
        description="实际走过的 fallback 链；空列表表示主路径走通",
    )
    final_strategy: str = Field(
        default="llm_first",
        description="最终方案来源：llm_first / llm_backprompt / ils / rule / give_up",
    )

    def is_empty(self) -> bool:
        """决策痕迹是否完全空白（用于前端隐藏卡片）。"""
        return (
            not self.blueprint_rationale
            and not self.weights_explanation
            and not self.critic_attempts
            and not self.alternatives_considered
            and not self.fallback_chain
        )


__all__ = [
    "CriticAttempt",
    "AlternativeCandidate",
    "FallbackHop",
    "DecisionTrace",
]
