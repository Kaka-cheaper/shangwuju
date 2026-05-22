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
    # 直接带坐标，前端 MapOverlay 不再需要二次查询 /poi-locations
    # 真接入美团 POI 时，POI 接口直接返坐标，schema 形态不变
    lat: Optional[float] = Field(
        default=None, description="该段地点纬度（无关联 POI/餐厅时为 null）"
    )
    lng: Optional[float] = Field(
        default=None, description="该段地点经度（无关联 POI/餐厅时为 null）"
    )
    address: Optional[str] = Field(
        default=None, description="详细地址 / 地名（如「西溪天街」），用于地图 InfoWindow"
    )
    note: Optional[str] = Field(
        default=None,
        description='附加说明，如"已为你预约 17:00 三人位"',
    )
    commute_minutes_required: Optional[NonNegativeInt] = Field(
        default=None,
        description=(
            "从上一段终点到本段起点所需的实际通勤分钟数（按用户 transport_preference 取值）。"
            "由 commute critic 写入；前端可在时间轴上显示「打车 13 分钟」气泡。"
            "首段或上一段无目标点时为 None。"
        ),
    )
    commute_mode: Optional[str] = Field(
        default=None,
        description='通勤方式，与 commute_minutes_required 对应：walking / taxi / bus / haversine_estimated。',
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
