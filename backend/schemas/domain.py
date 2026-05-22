"""domain —— 业务实体：POI / Restaurant / Route / UserProfile。

字段最低集来自 `docs/01-requirements/架构选型.md` D4。
含义：
- POI = 活动地点
- Restaurant = 餐厅
- Route = 两点间预估通勤时间
- UserProfile = 硬编码的用户画像（家位置 / 默认预算 / 交通偏好）
- Review = UGC 评论（赛题 06 原文要求「结合点评 POI 数据 / 用户评价语料」）

不负责：
- 持久化、CRUD（Mock 数据是只读快照）。
- 业务过滤算法（在 Tool 层）。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt


class Location(BaseModel):
    """坐标，用于路线计算与距离展示。Demo 不做真实地图，所以只存语义型坐标。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="地名/区域名，如「武林广场」")
    lat: Optional[float] = Field(default=None, description="可选，纬度")
    lng: Optional[float] = Field(default=None, description="可选，经度")


# ============================================================
# Review（UGC 评论）
# ============================================================

class Review(BaseModel):
    """单条 UGC 评论。

    用于：
    - LLM 蓝图 prompt 注入「真实用户怎么说」，让 rationale 可以引用
    - ItineraryCard 评论 chip 让评委一眼看到「评分背后的语义」
    - 真接入大众点评 / 美团 UGC 时，schema 形态保持一致

    设计取向（参考赛题 06 原文 + 大众点评 review schema）：
    - text 中文原文必须 ≥10 字（避免空泛"很好"）
    - tag_evidence 列出「这条评论支持哪些 tag」，下游 LLM 可直接引用
    - user_age_bucket 用银发/80后/90后/00后/学生 五档（粒度对齐主流广告投放）
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=10, description="中文评论原文")
    rating: float = Field(..., ge=1, le=5, description="该评论用户给的评分")
    user_age_bucket: str = Field(
        ...,
        description="用户年龄段：银发 / 80后 / 90后 / 00后 / 学生",
    )
    tag_evidence: list[str] = Field(
        default_factory=list,
        description="评论文本支持的 tag（如「亲子友好」「适合老人」），下游可引用",
    )
    visited_at: Optional[str] = Field(
        default=None, description='形如 "2026-04-15"；可选'
    )
    helpful_count: NonNegativeInt = Field(
        default=0, description="该评论被多少人标记「有用」（用于 top-N 排序）"
    )


# ============================================================
# POI（活动地点）
# ============================================================

class PoiCapacity(BaseModel):
    """POI 容量与库存（用于异常 E2 触发）。"""

    model_config = ConfigDict(extra="forbid")

    daily_quota: Optional[NonNegativeInt] = Field(
        default=None, description="每日总配额；None 表示无限制"
    )
    available_slots: NonNegativeInt = Field(
        default=0, description="今日剩余库存；0 触发售罄异常"
    )


class Poi(BaseModel):
    """活动地点。tags + suitable_for 是过滤的主战场。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description='形如 "P001"')
    name: str
    type: str = Field(..., description="类型，如 公园 / 展览 / 密室 / 书店")
    location: Location
    distance_km: NonNegativeFloat = Field(
        ..., description="距离用户家的预估直线距离（km）"
    )
    opening_hours: str = Field(..., description='形如 "09:00-21:00"')
    rating: float = Field(..., ge=0, le=5)
    age_range: Optional[list[NonNegativeInt]] = Field(
        default=None,
        description="[min_age, max_age]；亲子 POI 用",
    )
    price_range: Optional[list[NonNegativeFloat]] = Field(
        default=None,
        description="[min, max] 价格区间；None 表示免费",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="混合三类 tag 词典；过滤时与意图 tag 求交集",
    )
    suitable_for: list[str] = Field(
        default_factory=list,
        description="适用 social_context 的子集，如 [家庭日常, 老人伴助]",
    )
    capacity: PoiCapacity = Field(default_factory=PoiCapacity)
    suggested_duration_minutes: Optional[NonNegativeInt] = Field(
        default=None,
        description="推荐游玩时长（分钟）；用于行程时间轴拼装",
    )
    reviews: list[Review] = Field(
        default_factory=list,
        description=(
            "UGC 评论列表（赛题 06 原文要求）。空列表向后兼容；"
            "Step 3 之后所有 Demo POI 应至少 2 条。"
        ),
    )


# ============================================================
# Restaurant（餐厅）
# ============================================================

class RestaurantCapacity(BaseModel):
    """各种桌型是否可用。Capacity 描述「桌位类型存在性」，
    具体某天某时是否有空看 reservation_slots。

    `populate_by_name=True` 让 `model_dump()` 输出的字段名（two/four/...）
    与 alias（"2"/"4"/...）都能反向 model_validate——
    避免 invoke_tool 二次校验时炸（pitfalls P2-预埋 alias 漂移）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    two: bool = Field(default=True, alias="2")
    four: bool = Field(default=True, alias="4")
    six: bool = Field(default=False, alias="6")
    eight: bool = Field(default=False, alias="8")
    private_room: bool = Field(default=False)


class ReservationSlot(BaseModel):
    """某时段的预约状态（用于异常 E1 触发）。"""

    model_config = ConfigDict(extra="forbid")

    time: str = Field(..., description='形如 "17:00"')
    available: bool = Field(..., description="是否可订；false 触发 E1")
    queue_minutes: NonNegativeInt = Field(
        default=0, description="预估排队分钟数；available=false 时此字段无意义"
    )


class Restaurant(BaseModel):
    """餐厅。capacity / reservation_slots 是异常埋点的主战场。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description='形如 "R001"')
    name: str
    cuisine: str = Field(..., description="菜系，如 健康轻食 / 粤菜 / 日料")
    location: Location
    distance_km: NonNegativeFloat
    opening_hours: str
    avg_price: NonNegativeFloat = Field(..., description="人均价格（元）")
    rating: float = Field(..., ge=0, le=5)
    capacity: RestaurantCapacity = Field(default_factory=RestaurantCapacity)
    reservation_slots: list[ReservationSlot] = Field(
        default_factory=list,
        description="按时段列表；至少包含 17:00 / 17:30 / 18:00",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="混合三类 tag；过滤主战场",
    )
    suitable_for: list[str] = Field(
        default_factory=list,
        description="适用 social_context 的子集",
    )
    signature_dishes: list[str] = Field(
        default_factory=list,
        description="招牌菜 2-3 道，用于行程文案",
    )
    recommendation_reason: Optional[str] = Field(
        default=None,
        description="推荐理由，一句话说明为什么选这家",
    )
    reviews: list[Review] = Field(
        default_factory=list,
        description=(
            "UGC 评论列表（赛题 06 原文要求）。空列表向后兼容；"
            "Step 3 之后所有 Demo 餐厅应至少 2 条。"
        ),
    )


# ============================================================
# Route（路线时间）
# ============================================================

class Route(BaseModel):
    """A → B 的预估通勤时间（多种交通方式）。"""

    model_config = ConfigDict(extra="forbid")

    from_location: str = Field(..., description='形如 "P001" 或 "home"')
    to_location: str
    walking_minutes: Optional[NonNegativeInt] = None
    taxi_minutes: Optional[NonNegativeInt] = None
    bus_minutes: Optional[NonNegativeInt] = None


# ============================================================
# UserProfile（用户画像）
# ============================================================

class UserProfile(BaseModel):
    """硬编码用户画像。**绝不**包含 scene_type / relation_type 字段。"""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(default="demo_user")
    home_location: Location
    default_budget: NonNegativeFloat = Field(
        default=300.0, description="默认预算（元）"
    )
    transport_preference: str = Field(
        default="taxi", description="交通偏好：walking / taxi / bus"
    )
