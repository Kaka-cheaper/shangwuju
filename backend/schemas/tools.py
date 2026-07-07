"""tools —— 9 个 Tool 的 Input / Output Pydantic 模型。

每个 Tool 一对模型（Input / Output）。Input 用于 LLM Function Calling 的参数校验，
Output 用于 Agent 接收 Tool 返回值并继续规划。

设计原则（参考 AGENTS.md §3.4 + §4.1）：
- 输入字段**只接受三类约束 tag + 同行人结构 + 距离/时长**——禁止出现 relation / scene_type
- 失败用 success=false + reason: FailureReason，**不抛业务异常**给上层
- 单 Tool 不调其他 Tool

Tool 清单：
查询类：search_pois / search_restaurants / check_restaurant_availability / estimate_route_time
执行类：reserve_restaurant / buy_ticket / order_extra_service / generate_share_message
画像  ：get_user_profile

不负责：
- Tool 实现逻辑（在 backend/tools/）。
- Mock 数据加载（在 mock_data/ + Tool 实现）。
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt

from schemas.domain import ExtraService, Poi, Restaurant, Route, UserProfile
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
    tag_provenance: Optional[dict[str, str]] = Field(
        default=None,
        description=(
            "ADR-0014 决策 2（G-2）：`physical_constraints` 里每个 tag 的出处"
            "（`{tag值: user_stated/prior/inferred/default}`），供 `tools."
            "_helpers.relax_tag_search` 的 soft tag 降级序排序用——不覆盖"
            "hard tag（hard 全程恒定过滤，出处不影响是否放宽）。调用方从 "
            "`intent.field_provenance` 经 `schemas.intent.extract_tag_"
            "provenance` 摘取；缺省 None（旧调用点未接线 / 无出处数据）时"
            "退化为按原始顺序稳定丢弃，不强行编造。"
        ),
    )
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
    anchor_terms: Optional[list[str]] = Field(
        default=None,
        description=(
            "L1 anchor-escape：用户**显式点名**的活动品类（intent.preferred_poi_types，"
            "如「看展」）。命中 anchor_terms 的候选（走 `schemas.category_vocab."
            "poi_desire_match`）在 `_non_tag_filter` 里**跳过 experience_tags + "
            "social_context 两道推断场景硬过滤**——显式诉求压过推断调性；非锚候选"
            "照旧硬过滤。距离/年龄/preferred_types/exclude 不在豁免范围内。默认 None "
            "= 无锚 = 逐字节零回归。"
        ),
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
            "Tag relaxation 实际放弃的 physical_constraints **soft** tag 列表"
            "（按丢弃顺序；hard tag 从不出现在这里，见 `tools._helpers."
            "relax_tag_search`）。空 = 严格匹配通过，或 hard tag 全程不满足"
            "（无安全候选，也不算「放宽」）；非空 = 候选源在严格 tag 下打到 0，"
            "按出处降级序渐进丢 soft tag 得到候选。"
            "ADR-0014 决策 2（G-2）：**纯调试信息**，不再驱动任何用户可见的"
            "「哪些约束被放宽了」告知——该职责已收口到出口满足度审计"
            "（`agent.planning.critic.exit_audit`，比对最终方案 vs intent 全部"
            "约束，产出 `AdvisoryCode.CONSTRAINT_RELAXED`）。本字段只留给"
            "SSE `tool_call_end`/调试面板展示实际过滤路径，不再承担告知职责。"
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
    tag_provenance: Optional[dict[str, str]] = Field(
        default=None,
        description=(
            "ADR-0014 决策 2（G-2）：`dietary_constraints` 里每个 tag 的出处"
            "（同 `SearchPoisInput.tag_provenance`，见该字段 docstring）。"
        ),
    )
    social_context: Optional[SocialContext] = None
    anchor_terms: Optional[list[str]] = Field(
        default=None,
        description=(
            "L1 anchor-escape：用户**显式点名**的餐饮品类（intent.preferred_poi_types，"
            "如「烧烤」）。命中 anchor_terms 的餐厅（走 `schemas.category_vocab."
            "restaurant_desire_match`，比对 cuisine）在 `_non_tag_filter` 里**跳过 "
            "experience_tags + social_context 两道推断场景硬过滤**；非锚候选照旧硬"
            "过滤。dietary（走 relax_tag_search）/距离/桌型/exclude 不在豁免范围内。"
            "默认 None = 无锚 = 逐字节零回归。"
        ),
    )
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
            "Tag relaxation 实际放弃的 dietary_constraints **soft** tag 列表"
            "（同 `SearchPoisOutput.relaxed_tags` 纪律：hard tag 从不出现在"
            "这里；纯调试信息，不再驱动用户可见告知，见该字段 docstring）。"
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
# T7. order_extra_service
# ============================================================

class OrderExtraServiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_type: str = Field(..., min_length=1, description="如 蛋糕 / 鲜花")
    target_kind: Literal["restaurant", "poi"] = Field(
        ..., description="附加服务挂靠目标类型"
    )
    target_id: str = Field(..., min_length=1, description="餐厅或 POI id")
    quantity: NonNegativeInt = Field(default=1, description="购买数量")
    scheduled_time: Optional[str] = Field(default=None, description='形如 "17:30"')
    recipient_note: Optional[str] = Field(
        default=None, description="给商家的简短备注，如 妈妈生日"
    )


class OrderExtraServiceOutput(ToolOutputBase):
    order_id: Optional[str] = None
    service: Optional[ExtraService] = None
    service_type: str
    target_kind: Literal["restaurant", "poi"]
    target_id: str
    quantity: Optional[NonNegativeInt] = None
    total_price: Optional[NonNegativeFloat] = None
    scheduled_time: Optional[str] = None


# ============================================================
# T8. generate_share_message
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
# T9. get_user_profile
# ============================================================

class GetUserProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(default="demo_user")


class GetUserProfileOutput(ToolOutputBase):
    profile: Optional[UserProfile] = None
