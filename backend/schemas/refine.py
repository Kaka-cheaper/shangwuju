"""refine —— 用户拒绝方案 + 反馈 → 重规划的契约。

业务故事：
- 用户面对 Itinerary 卡片，可以选 [确认] / [拒绝并说明原因] / [取消]
- 选「拒绝并说明原因」时，前端把反馈文本（可空）发到 POST /chat/refine
- refiner 把 (原 IntentExtraction + 反馈) 合并为新 IntentExtraction，让 planner 重算
- 完整事件序列详见 backend/api_contract.md §7

字段命名硬约束：
- 不引入 scene_type / relation_type 等枚举（D9）
- refined intent 必须是合法的 IntentExtraction（§5.7 D-SoT），下游 planner 不感知差异
- changed_fields 是面向人类阅读的中文字段名描述（用于前端 toast 与日志）

不负责：
- 反馈合并算法（在 backend/agent/refiner.py，A 块实现）
- HTTP 端点（在 backend/main.py，B 块实现）
- UI（在 frontend/，C 块实现）
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.intent import IntentExtraction


class RefinementInput(BaseModel):
    """前端 POST /chat/refine 的请求体。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(
        ..., description="与 /chat/stream 同一会话；后端用此找原 intent + last itinerary"
    )
    feedback_text: str = Field(
        default="",
        description=(
            "用户反馈（可空）。空时 refiner 走默认调整策略（如降级距离 / 替换备选）；"
            "非空时 LLM 把反馈合并进原 intent。"
        ),
    )


class RefinementOutput(BaseModel):
    """refiner 的输出 + /chat/refine SSE 中 `refinement_done` 事件的 payload。

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
