"""refine —— 反馈合并（refiner）输出的契约。

业务故事（2026-07 现况）：
- 用户对方案卡说"太远了/换个安静的"这类反馈，经 /chat/stream 的统一路由
  判为 feedback 义务 → 图内 refiner_node 调 `agent.intent.refiner.refine_intent`
- refiner 把 (原 IntentExtraction + 反馈) 合并为新 IntentExtraction，让 planner 重算
- 本模块只承载 refiner 的输出形状 `RefinementOutput`——它同时是 SSE
  `REFINEMENT_DONE` 事件的 payload（见 schemas/sse.py）

历史备注：早期存在独立的 POST /chat/refine 端点与其请求体 `RefinementInput`，
端点随反馈流并入 /chat/stream 统一路由后删除，请求体类已一并移除
（api_contract.md §10 曾记录这条文档-代码出入，现已收口）。

字段命名硬约束：
- 不引入 scene_type / relation_type 等枚举（D9）
- refined intent 必须是合法的 IntentExtraction（§5.7 D-SoT），下游 planner 不感知差异
- changed_fields 是面向人类阅读的中文字段名描述（用于前端 toast 与日志）

不负责：
- 反馈合并算法（在 backend/agent/intent/refiner.py）
- 触发路由（在 agent/routing/route_turn.py 的 feedback 义务分发）
- UI（在 frontend/）
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.intent import IntentExtraction


class RefinementOutput(BaseModel):
    """refiner 的输出 + SSE `REFINEMENT_DONE` 事件的 payload。

    `changed_fields` 是「我把距离从 5 公里改到 3 公里」这种人话描述列表，
    前端拿去做 toast 提示。下游 planner 只看 `refined_intent`，不看 changed_fields。
    """

    model_config = ConfigDict(extra="forbid")

    refined_intent: IntentExtraction = Field(
        ..., description="合并反馈后的新 IntentExtraction，必须仍合法（§5.7 D-SoT）"
    )
    changed_fields: list[str] = Field(
        default_factory=list,
        description=(
            "中文字段变更摘要列表，如 ['距离上限：5km → 3km', '加忌口：不辣']；"
            "空列表表示用户反馈无可执行调整（refiner 仅做兜底重排）"
        ),
    )
    refiner_note: Optional[str] = Field(
        default=None,
        description="refiner 自报的整体说明，前端可直接展示给用户",
    )
