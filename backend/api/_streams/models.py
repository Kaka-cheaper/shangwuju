"""Chat 端点 Request 模型（spec code-modularization-refactor H1-final）。

抽出原因：main.py 不再保留任何 BaseModel 定义；端点 schema 与 SSE 实现同 package。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(..., min_length=1, max_length=500)
    session_id: str = Field(..., min_length=1, max_length=128)
    scenario_id: Optional[str] = None
    # Phase 0.7：可选；缺省时按 X-User-Id header > "demo_user" 兜底
    user_id: Optional[str] = Field(default=None, max_length=64)


class ChatConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1, max_length=128)
    decision: str = Field(..., pattern="^(confirm|reject|modify)$")
    modifications: Optional[dict[str, Any]] = None
    user_id: Optional[str] = Field(default=None, max_length=64)
    # spec execution-quality-review R2：execution Tool 的 hallucination 防护白名单
    # 规划阶段 ItineraryReady 中所有 target_id 由 backend 写入；前端在 confirm 时回传
    # 让 reserve_restaurant / buy_ticket 等执行类工具仅能在该白名单内派发。
    # 攻击向量：LLM 在多轮反馈中编造 R999 / 用代词指代触发执行类工具调错对象。
    # 设计：可选字段（向后兼容），缺省时不做白名单校验（demo 短路径不破）。
    allowed_restaurant_ids: Optional[list[str]] = Field(
        default=None,
        description=(
            "前端从 ItineraryReady 收到的合法餐厅 ID 集合；"
            "传入后 reserve_restaurant 仅能在该集合内派发"
        ),
    )
    allowed_poi_ids: Optional[list[str]] = Field(
        default=None,
        description=(
            "前端从 ItineraryReady 收到的合法 POI ID 集合；"
            "传入后 buy_ticket 仅能在该集合内派发"
        ),
    )
