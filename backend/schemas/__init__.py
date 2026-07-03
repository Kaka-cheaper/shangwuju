"""schemas —— 跨层契约的唯一权威定义。

职责：
- 集中存放 Pydantic v2 模型，作为 Agent / Tool / 前端 / Mock 数据 四方共享的接口契约。
- 锁定意图抽取 schema（`intent.py`，对应 `需求分析.md` §5.7 D-SoT）。
- 锁定三类 tag 词典与失败原因枚举，杜绝下游"发明 tag"或"自创 reason 字符串"。

不负责：
- 具体业务逻辑（Tool 的过滤算法、Agent 的规划策略）。
- I/O 操作（不读 mock_data、不调 LLM API）。
- 任何与运行时状态相关的逻辑。

【13 文件分层导航（自底向上）】

```
[基础常量层]                       无依赖底座，被所有其他文件 import
- tags.py 139行                    4 类 tag 词典 + Literal 类型（PhysicalTag / DietaryTag / ExperienceTag / SocialContext）
- errors.py 34行                   FailureReason 9 个失败枚举

[核心契约层]                       业务核心数据结构（Agent 编排层 + Tool 层 + 前端共享）
- intent.py 171行                  IntentExtraction（§5.7 D-SoT 唯一权威）+ Companion + PaceProfile
- domain.py 347行                  POI / Restaurant / Route / UserProfile / Review / RecentTrip / Location
- itinerary.py 287行               ActivityNode / Hop / Itinerary（edge_v1 模型，业内通用）

[扩展层]                           相对独立的子领域
- persona.py 252行                 Persona / UserMemory / PaceProfile（Phase 0.7 个性化 prior）
- decision_trace.py 153行          CriticAttempt / AlternativeCandidate / FallbackHop / DecisionTrace（评审可见性）

[API 契约层]                       跨 4 层架构边界（HTTP / SSE / Tool I/O）
- tools.py 274行                   9 个 Tool 的 Input / Output（OpenAI Function Calling spec 来源）
- sse.py 90行                      SseEventType 枚举 + SseEvent（前端 EventSource 消费契约）
- router.py 103行                  InputKind 6 类输入域路由 + RouterDecision + CtaChip
- refine.py                        RefinementOutput（refiner 输出 + REFINEMENT_DONE payload）
- planner_mode.py 66行             rule / llm 切换 + os.getenv 解析 helper（含 resolve_planner_mode）

[入口层]
- __init__.py 150行                re-export 整理（本文件）
```

【依赖单向流动】

底座（tags / errors）→ 核心（intent / domain / itinerary）→ 扩展（persona / decision_trace）→ API 契约（tools / sse / router / refine / planner_mode）

无循环依赖；新加 Pydantic 模型时按归属层放对应文件。如果你在「不知道放哪」时
犹豫超过 2 分钟，先查上表对应层级——99% 情况下答案明确。
"""

from schemas.tags import (
    PHYSICAL_TAGS,
    DIETARY_TAGS,
    EXPERIENCE_TAGS,
    SOCIAL_CONTEXTS,
    PhysicalTag,
    DietaryTag,
    ExperienceTag,
    SocialContext,
)
from schemas.errors import FailureReason
from schemas.intent import Companion, IntentExtraction
from schemas.domain import (
    Location,
    PoiCapacity,
    Poi,
    RestaurantCapacity,
    ReservationSlot,
    Restaurant,
    ExtraService,
    Route,
    UserProfile,
)
from schemas.itinerary import (
    ActivityNode,
    Hop,
    ScheduleEntry,
    OrderRecord,
    Itinerary,
    NodeTargetKind,
    HopMode,
    HopPathType,
)
from schemas.refine import RefinementOutput
from schemas.router import (
    InputKind,
    CtaChip,
    RouterDecision,
)
from schemas.planner_mode import (
    PlannerMode,
    DEFAULT_MODE as DEFAULT_PLANNER_MODE,
    normalize_mode as normalize_planner_mode,
    resolve_planner_mode,
    current_env_mode,
)
from schemas.tools import (
    SearchPoisInput,
    SearchPoisOutput,
    SearchRestaurantsInput,
    SearchRestaurantsOutput,
    CheckRestaurantAvailabilityInput,
    CheckRestaurantAvailabilityOutput,
    EstimateRouteTimeInput,
    EstimateRouteTimeOutput,
    GetUserProfileInput,
    GetUserProfileOutput,
    ReserveRestaurantInput,
    ReserveRestaurantOutput,
    BuyTicketInput,
    BuyTicketOutput,
    OrderExtraServiceInput,
    OrderExtraServiceOutput,
    GenerateShareMessageInput,
    GenerateShareMessageOutput,
)
from schemas.sse import (
    SseEventType,
    SseEvent,
)

__all__ = [
    # tags
    "PHYSICAL_TAGS",
    "DIETARY_TAGS",
    "EXPERIENCE_TAGS",
    "SOCIAL_CONTEXTS",
    "PhysicalTag",
    "DietaryTag",
    "ExperienceTag",
    "SocialContext",
    # errors
    "FailureReason",
    # intent
    "Companion",
    "IntentExtraction",
    # domain
    "Location",
    "PoiCapacity",
    "Poi",
    "RestaurantCapacity",
    "ReservationSlot",
    "Restaurant",
    "ExtraService",
    "Route",
    "UserProfile",
    # itinerary (edge_v1)
    "ActivityNode",
    "Hop",
    "ScheduleEntry",
    "OrderRecord",
    "Itinerary",
    "NodeTargetKind",
    "HopMode",
    "HopPathType",
    # refine + planner mode (Phase 0.6)
    "RefinementOutput",
    # router (Phase 0.8)
    "InputKind",
    "CtaChip",
    "RouterDecision",
    "PlannerMode",
    "DEFAULT_PLANNER_MODE",
    "normalize_planner_mode",
    "resolve_planner_mode",
    "current_env_mode",
    # tools
    "SearchPoisInput",
    "SearchPoisOutput",
    "SearchRestaurantsInput",
    "SearchRestaurantsOutput",
    "CheckRestaurantAvailabilityInput",
    "CheckRestaurantAvailabilityOutput",
    "EstimateRouteTimeInput",
    "EstimateRouteTimeOutput",
    "GetUserProfileInput",
    "GetUserProfileOutput",
    "ReserveRestaurantInput",
    "ReserveRestaurantOutput",
    "BuyTicketInput",
    "BuyTicketOutput",
    "OrderExtraServiceInput",
    "OrderExtraServiceOutput",
    "GenerateShareMessageInput",
    "GenerateShareMessageOutput",
    # sse
    "SseEventType",
    "SseEvent",
]
