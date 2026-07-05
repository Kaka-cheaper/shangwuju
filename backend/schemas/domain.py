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

from typing import Literal, Optional, Union

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


class SuggestedDuration(BaseModel):
    """推荐游玩时长（按主导客群分桶）。

    spec `planning-quality-deep-review` R1 引入：把 POI 推荐时长
    从单值升级为按年龄分桶，让 LLM / critic / ILS 能区分
    「亲子博物馆 5 岁娃 60min」vs「成人独处 90min」。

    业界对标（2025 年 Google Trips / TripAdvisor / Foursquare）：
    - 亲子场景：3-6 岁桶按 Smithsonian SEEC 60-90min 业界基线
    - 老年场景：senior 桶按行业经验 ≤ 75min
    - 多代际：取最严约束（multi_gen 落到 60-75min）

    投影规则（见 backend/utils/duration_helpers.py:get_duration_for_companions）：
    - 含 ≤6 岁孩 → kid_3_6
    - 含 7-12 岁孩 → kid_7_12
    - 含 ≥75 岁老人 → senior
    - 多代际（孩+老人 / 孩+成年） → multi_gen
    - 其他 → default
    """

    model_config = ConfigDict(extra="forbid")

    default: NonNegativeInt = Field(
        ..., description="默认推荐时长（分钟）；所有客群兜底"
    )
    kid_3_6: Optional[NonNegativeInt] = Field(
        default=None,
        description="3-6 岁学龄前客群推荐时长（分钟）；行业基线 ≤ 75",
    )
    kid_7_12: Optional[NonNegativeInt] = Field(
        default=None,
        description="7-12 岁学童客群推荐时长（分钟）；行业基线 ≤ 120",
    )
    senior: Optional[NonNegativeInt] = Field(
        default=None,
        description="≥ 75 岁老人客群推荐时长（分钟）；行业基线 ≤ 75",
    )
    multi_gen: Optional[NonNegativeInt] = Field(
        default=None,
        description="多代际场景推荐时长（分钟）；取多桶最严值",
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
    suggested_duration_minutes: Optional[Union[NonNegativeInt, "SuggestedDuration"]] = Field(
        default=None,
        description=(
            "推荐游玩时长（分钟）。两种形态双兼容（spec planning-quality-deep-review R1）：\n"
            "1) int 旧形态：所有客群同一时长，向后兼容；\n"
            "2) SuggestedDuration dict 新形态：按主导客群分桶，含必填 default + 可选 "
            "kid_3_6 / kid_7_12 / senior / multi_gen。下游 LLM 透传时按 companions "
            "投影为单值（见 backend/utils/duration_helpers.py）。"
        ),
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
    typical_dining_min: Optional[NonNegativeInt] = Field(
        default=None,
        description=(
            "典型用餐时长（分钟）。spec planning-quality-deep-review R1 引入。"
            "按 cuisine 业界惯例：健康轻食 40 / 咖啡 45 / 下午茶 75 / "
            "粤菜 90 / 火锅 120；'高人均' / '私房菜' tag 各 +15。"
            "下游 BlueprintLLM 据此决定 duration_min（见 R3 prompt 消费规则）。"
        ),
    )
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
# ExtraService（附加服务）
# ============================================================


class ExtraService(BaseModel):
    """餐厅 / POI 可加购的本地生活附加服务。

    Mock 层只描述可售资源，不保存运行时订单。执行类 Tool 返回伪订单号体现
    "已下单"，不修改本文件或 mock_data。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description='形如 "XS001"')
    service_type: str = Field(..., description="服务类型，如 蛋糕 / 鲜花 / 生日布置")
    name: str = Field(..., description="服务展示名")
    target_kinds: list[Literal["restaurant", "poi"]] = Field(
        default_factory=list,
        description="可挂靠的目标类型；通常为 restaurant",
    )
    target_ids: list[str] = Field(
        default_factory=list,
        description='支持的目标 id；包含 "*" 表示该 target_kind 下通用',
    )
    price: NonNegativeFloat = Field(..., description="单价（元）")
    available: bool = Field(default=True, description="是否可售")
    inventory: NonNegativeInt = Field(default=0, description="剩余库存")
    lead_time_min: NonNegativeInt = Field(
        default=30, description="最短提前准备时间（分钟）"
    )
    description: Optional[str] = Field(default=None, description="一句话服务说明")


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


class RecentTrip(BaseModel):
    """spec algorithm-redesign R5：最近行程记录（会话内跨局召回）。

    引入 TravelAgent / TriFlow 范式的「short-term memory」：用户确认下单后由
    `execute_finalize._persist_memory_side_effect` → `memory_writer.persist_memory`
    追加一条到 `data.memory_store` 的**会话私有**档案（键=session_id，记忆身份
    读写分离批：会话即身份）；同会话下一局意图解析把最新条目注入 prompt
    （"用户上次「家庭」场景的行程：{summary}"）。

    字段约束：
    - timestamp：ISO 8601 字符串（"2026-05-24T15:30:00Z"）
    - social_context：与 IntentExtraction.social_context 同词典
    - summary：脱敏后的自然语言摘要（不含具体年龄数字 / 经纬度 / 真实地址）
    - success：用户最终是否确认下单
    """

    model_config = ConfigDict(extra="forbid")

    timestamp: str = Field(..., description="ISO 8601 时间戳")
    social_context: str = Field(..., description="场景，与 IntentExtraction.social_context 同词典")
    summary: str = Field(
        ...,
        max_length=500,
        description="脱敏摘要（不含真实年龄数字 / 地址 / 经纬度）",
    )
    success: bool = Field(default=False, description="用户最终是否确认下单")


class UserProfile(BaseModel):
    """硬编码用户画像。**绝不**包含 scene_type / relation_type 字段。

    spec algorithm-redesign R5：扩三层 schema 加召回能力（TravelAgent 范式）。
    新增 3 个字段全部 Optional 默认 None，向后兼容旧 user_profile.json（仅 4 字段）。
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(default="demo_user")
    home_location: Location
    default_budget: NonNegativeFloat = Field(
        default=300.0, description="默认预算（元）"
    )
    transport_preference: str = Field(
        default="taxi", description="交通偏好：walking / taxi / bus"
    )

    # spec algorithm-redesign R5：三层 schema 加召回（向后兼容默认 None）
    dietary_preference: Optional[str] = Field(
        default=None,
        max_length=500,
        description=(
            "自然语言饮食偏好（50-100 字段落），如「喜欢健康轻食、避免油腻、对辣度敏感」；"
            "由 intent parser 注入 prompt 让 LLM 在搜索餐厅时考虑"
        ),
    )
    social_context_history: Optional[list[str]] = Field(
        default=None,
        max_length=20,
        description="历史触发过的 social_context 列表（去重）；用于偏好统计",
    )
    recent_trips: Optional[list[RecentTrip]] = Field(
        default=None,
        max_length=10,
        description="最近行程列表（按 timestamp 降序）；上限 5 条由 memory_writer 维护",
    )
