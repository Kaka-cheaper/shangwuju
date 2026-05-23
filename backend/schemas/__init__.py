"""schemas —— 跨层契约的唯一权威定义。

职责：
- 集中存放 Pydantic v2 模型，作为 Agent / Tool / 前端 / Mock 数据 四方共享的接口契约。
- 锁定意图抽取 schema（`intent.py`，对应 `需求分析.md` §5.7 D-SoT）。
- 锁定三类 tag 词典与失败原因枚举，杜绝下游"发明 tag"或"自创 reason 字符串"。

不负责：
- 具体业务逻辑（Tool 的过滤算法、Agent 的规划策略）。
- I/O 操作（不读 mock_data、不调 LLM API）。
- 任何与运行时状态相关的逻辑。
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
from schemas.refine import (
    RefinementInput,
    RefinementOutput,
)
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
    "RefinementInput",
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
    "GenerateShareMessageInput",
    "GenerateShareMessageOutput",
    # sse
    "SseEventType",
    "SseEvent",
]
