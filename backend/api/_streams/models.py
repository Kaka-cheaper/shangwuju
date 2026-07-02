"""Chat 端点 Request 模型（spec code-modularization-refactor H1-final）。

抽出原因：main.py 不再保留任何 BaseModel 定义；端点 schema 与 SSE 实现同 package。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from schemas.node_adjustment import NodeAdjustment


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
    # 让 reserve_restaurant / buy_ticket / order_extra_service 等执行类工具仅能在该白名单内派发。
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


# ============================================================
# POST /chat/adjust（ADR-0013 F-4：单人节点调整入口）
# ============================================================
#
# 三种 action——「点击即生效，无预览」（ADR-0013 决策 2 原文）：结构化指令，
# 不过 LLM 路由。三者殊途同归都喂给同一个引擎
# `agent.planning.planners.node_swap.resolve_node_swap`（见
# api/_streams/graph_adjust.py），本层只负责契约形状 + 判别式校验。


class AdjustActionAdjust(BaseModel):
    """点击「定向调整按钮」（`schemas.node_chip.NodeChip`）——回传其核心载荷
    `adjustment`，与展示时同一份 `NodeAdjustment` 形状（不另起一套点击 payload）。

    `label`：可选，chip 按钮文案（`NodeChip.label` 同一口径，≤8 字）——诉求
    台账 `LedgerEntry.source_text` 优先取它（最贴近"用户点的这句话"的记账
    语义）；前端省略时由消费方按 dimension/value 合成一句兜底描述（不因为
    缺这个可选字段就拒绝请求——它只影响记账文案，不影响换菜本身能否执行）。
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["adjust"] = "adjust"
    adjustment: NodeAdjustment
    label: Optional[str] = Field(
        default=None,
        max_length=8,
        description="点击的 chip 按钮文案（NodeChip.label 同一口径）；用作诉求台账 source_text，缺省由后端按维度合成",
    )


class AdjustActionAlternative(BaseModel):
    """点击「具名备选」（`agent.planning.planners.node_swap.AlternativeOption`）
    ——直接指定要换成的目标实体 `target_id`，构造无维度的定向换（见
    api/_streams/graph_adjust.py 的候选池收窄手法：保证换成的就是这一个，
    不是同池里恰好评分更高的另一个）。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["alternative"] = "alternative"
    target_id: str = Field(..., min_length=1, description="AlternativeOption.target_id（POI/Restaurant 实体 id）")


class AdjustActionDislike(BaseModel):
    """点踩——无方向局部重解（ADR-0013 决策 4「点踩收编为无方向局部重解」）。
    单人 UI 暂不发出这个 action（单人模式没有"点踩"入口），协议先立好给
    F-5 房间侧复用（房间点踩不等多数，一人踩立刻换）。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["dislike"] = "dislike"


AdjustAction = Annotated[
    Union[AdjustActionAdjust, AdjustActionAlternative, AdjustActionDislike],
    Field(discriminator="type"),
]


class ChatAdjustRequest(BaseModel):
    """POST /chat/adjust 请求体（ADR-0013 F-4）。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1, max_length=128)
    node_id: str = Field(
        ...,
        min_length=1,
        description=(
            "ActivityNode.target_id——同 schemas.node_chip.NodeChip.node_id / "
            "resolve_node_swap(target_node_id=...) 同一口径，前端从节点行直接透传"
        ),
    )
    action: AdjustAction
