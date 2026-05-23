"""tools —— 8 个 Tool 的 Input / Output Pydantic 模型。

每个 Tool 一对模型（Input / Output）。Input 用于 LLM Function Calling 的参数校验，
Output 用于 Agent 接收 Tool 返回值并继续规划。

设计原则（参考 AGENTS.md §3.4 + §4.1）：
- 输入字段**只接受三类约束 tag + 同行人结构 + 距离/时长**——禁止出现 relation / scene_type
- 失败用 success=false + reason: FailureReason，**不抛业务异常**给上层
- 单 Tool 不调其他 Tool

Tool 清单：
查询类：search_pois / search_restaurants / check_restaurant_availability / estimate_route_time
执行类：reserve_restaurant / buy_ticket / generate_share_message
画像  ：get_user_profile

可选（MVP-2 才上）：
- order_extra_service（暂未在本文件落 schema，待 P1 实现时补）

不负责：
- Tool 实现逻辑（在 backend/tools/）。
- Mock 数据加载（在 mock_data/ + Tool 实现）。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt

from schemas.domain import Poi, Restaurant, Route, UserProfile
from schemas.errors import FailureReason
from schemas.tags import (
    DietaryTag,
    ExperienceTag,
    PhysicalTag,
    SocialContext,
)


# ============================================================
# 共享基类
# ============================================================

class ToolOutputBase(BaseModel):
    """所有 Tool 输出的共同字段。"""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(..., description="是否成功；失败时 reason 必填")
    reason: Optional[FailureReason] = Field(
        default=None, description="失败原因；success=true 时为 None"
    )


# ============================================================
# T1. search_pois
# ============================================================

class SearchPoisInput(BaseModel):
    """查询 POI。距离 + tag + suitable_for 三层过滤。"""

    model_config = ConfigDict(extra="forbid")

    distance_max_km: float = Field(default=5.0, ge=0, le=100)
    physical_constraints: list[PhysicalTag] = Field(default_factory=list)
    experience_tags: list[ExperienceTag] = Field(default_factory=list)
    social_context: Optional[SocialContext] = Field(
        default=None, description="若提供则按 suitable_for 过滤"
    )
    age_in_party: Optional[list[NonNegativeInt]] = Field(
        default=None,
        description="同行人年龄列表；用于亲子场景过滤 age_range",
    )
    preferred_types: list[str] = Field(
        default_factory=list, description="如 [展览, 美术馆]"
    )
    user_lat: Optional[float] = Field(
        default=None,
        description=(
            "用户当前位置纬度。提供时走 NearbySearchProvider 实时算距离；"
            "缺省时回退到 mock 数据预填的 distance_km 字段（向后兼容）。"
        ),
    )
    user_lng: Optional[float] = Field(
        default=None, description="用户当前位置经度，与 user_lat 配套"
    )
    exclude_visited_ids: list[str] = Field(
        default_factory=list,
        description=(
            "需要排除的 target_id 列表（来自 UserMemory.recently_visited_ids）。"
            "Step 7：避免推荐用户最近 30 天访问过的 POI/餐厅。"
            "调用方负责传入；本 Tool 不查 memory store（保持纯过滤 Tool 职责）。"
        ),
    )
    limit: NonNegativeInt = Field(default=10, le=50)


class SearchPoisOutput(ToolOutputBase):
    candidates: list[Poi] = Field(default_factory=list)
    relaxed_tags: list[str] = Field(
        default_factory=list,
        description=(
            "Tag relaxation 实际放弃的 physical_constraints tag 列表（按丢弃顺序）。"
            "空 = 严格匹配通过；非空 = 候选源在严格 tag 下打到 0，按软优先级"
            "渐进放宽得到候选。LLM 应在 rationale 中解释这一点。"
        ),
    )
    effective_distance_max_km: Optional[float] = Field(
        default=None,
        description=(
            "实际生效的距离上限（公里）。spec planning-quality-deep-review R2 引入。"
            "若 search 内部因 0 候选触发距离放宽（用户原 5km → 兜底 +2km），"
            "此字段记录实际放宽到的距离；LLM 应在 rationale 显式说明。"
            "为 None 时表示距离严格匹配未放宽（向后兼容）。"
        ),
    )


# ============================================================
# T2. search_restaurants
# ============================================================

class SearchRestaurantsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distance_max_km: float = Field(default=5.0, ge=0, le=100)
    dietary_constraints: list[DietaryTag] = Field(default_factory=list)
    experience_tags: list[ExperienceTag] = Field(default_factory=list)
    social_context: Optional[SocialContext] = None
    capacity_requirement: Optional[NonNegativeInt] = Field(
        default=None,
        description="同行 ≥4 人时按桌型过滤",
    )
    require_private_room: bool = Field(default=False)
    user_lat: Optional[float] = Field(
        default=None,
        description=(
            "用户当前位置纬度。提供时走 NearbySearchProvider 实时算距离；"
            "缺省时回退到 mock 数据预填的 distance_km 字段（向后兼容）。"
        ),
    )
    user_lng: Optional[float] = Field(
        default=None, description="用户当前位置经度，与 user_lat 配套"
    )
    exclude_visited_ids: list[str] = Field(
        default_factory=list,
        description=(
            "需要排除的餐厅 id 列表（来自 UserMemory.recently_visited_ids）。"
            "Step 7：避免推荐用户最近 30 天访问过的餐厅。"
        ),
    )
    limit: NonNegativeInt = Field(default=10, le=50)


class SearchRestaurantsOutput(ToolOutputBase):
    candidates: list[Restaurant] = Field(default_factory=list)
    relaxed_tags: list[str] = Field(
        default_factory=list,
        description=(
            "Tag relaxation 实际放弃的 dietary_constraints tag 列表（按丢弃顺序）。"
            "空 = 严格匹配；非空 = 严格 tag 下打到 0，按软优先级渐进放宽得到候选。"
        ),
    )


# ============================================================
# T3. check_restaurant_availability
# ============================================================

class CheckRestaurantAvailabilityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    restaurant_id: str
    time: str = Field(..., description='形如 "17:00"')
    party_size: NonNegativeInt = Field(default=2)


class CheckRestaurantAvailabilityOutput(ToolOutputBase):
    restaurant_id: str
    time: str
    available: bool = Field(default=False)
    queue_minutes: NonNegativeInt = Field(default=0)
    suggested_alternative_time: Optional[str] = Field(
        default=None, description="推荐改约时间，如 17:30"
    )


# ============================================================
# T4. estimate_route_time
# ============================================================

class EstimateRouteTimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_location: str = Field(..., description='POI/Restaurant id 或 "home"')
    to_location: str


class EstimateRouteTimeOutput(ToolOutputBase):
    route: Optional[Route] = None


# ============================================================
# T5. reserve_restaurant
# ============================================================

class ReserveRestaurantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    restaurant_id: str
    time: str
    party_size: NonNegativeInt
    extra_notes: Optional[str] = None


class ReserveRestaurantOutput(ToolOutputBase):
    order_id: Optional[str] = None
    restaurant_id: str
    confirmed_time: Optional[str] = None
    confirmed_party_size: Optional[NonNegativeInt] = None


# ============================================================
# T6. buy_ticket
# ============================================================

class BuyTicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    poi_id: str
    quantity: NonNegativeInt = Field(default=1)
    visitor_ages: Optional[list[NonNegativeInt]] = None


class BuyTicketOutput(ToolOutputBase):
    order_id: Optional[str] = None
    poi_id: str
    quantity: Optional[NonNegativeInt] = None
    total_price: Optional[NonNegativeFloat] = None


# ============================================================
# T7. generate_share_message
# ============================================================

class GenerateShareMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    itinerary_summary: str = Field(..., description="行程摘要")
    social_context: SocialContext = Field(
        ..., description="决定文案调性（家长口吻 / 闺蜜亲昵 / 商务正式）"
    )
    audience: Optional[str] = Field(
        default=None,
        description="转发对象，如 妻子 / 朋友群 / 客户 / 自己",
    )


class GenerateShareMessageOutput(ToolOutputBase):
    message: Optional[str] = Field(
        default=None, description="可一键复制的口语文案"
    )


# ============================================================
# T8. get_user_profile
# ============================================================

class GetUserProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(default="demo_user")


class GetUserProfileOutput(ToolOutputBase):
    profile: Optional[UserProfile] = None
