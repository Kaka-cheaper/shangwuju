"""itinerary —— 最终方案输出。

`Itinerary` 是 Agent 给前端的最终交付物，对应行程卡片的渲染数据。
六段结构：出发 → 主活动 → 转场 → 用餐 → 附加 → 返回。

不负责：
- 文案生成（在 generate_share_message Tool）。
- UI 渲染（在前端组件）。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt


class ItineraryStage(BaseModel):
    """行程一段（出发 / 主活动 / 转场 / 用餐 / 附加 / 返回）。"""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        ...,
        description="出发 / 主活动 / 转场 / 用餐 / 附加 / 返回 之一（自由文本，前端渲染图标用）",
    )
    start: str = Field(..., description='形如 "14:00"')
    end: str = Field(..., description='形如 "16:30"')
    title: str = Field(..., description="一行说明，如「亲子游玩 · 森林儿童探索乐园」")
    poi_id: Optional[str] = None
    restaurant_id: Optional[str] = None
    note: Optional[str] = Field(
        default=None,
        description='附加说明，如"已为你预约 17:00 三人位"',
    )


class OrderRecord(BaseModel):
    """已为你预留清单中的一条。"""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(..., description='形如 "R20260507_001"')
    kind: str = Field(
        ..., description="餐厅预约 / 门票 / 加购服务 之一"
    )
    target_id: str = Field(..., description="对应 poi_id 或 restaurant_id")
    target_name: str
    detail: str = Field(..., description='如 "17:00 三人位"')


class Itinerary(BaseModel):
    """完整方案。前端按此渲染时间轴 + 已为你预留清单 + 转发文案。"""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., description="一句话方案摘要，如「家庭半日方案」")
    stages: list[ItineraryStage] = Field(
        ..., description="按时间排序的六段（实际可少于 6 段，但 ≥ 5 段）"
    )
    orders: list[OrderRecord] = Field(
        default_factory=list, description="已为你预留清单"
    )
    share_message: Optional[str] = Field(
        default=None, description="可一键复制的转发文案"
    )
    total_minutes: NonNegativeInt = Field(
        ..., description="总耗时（分钟）；用于校验 4-6h 约束"
    )
