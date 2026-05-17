"""agent.planner —— ReAct 规划主循环（Planning）。

职责：
- 接 IntentExtraction（来自 intent_parser）
- 通过 invoke_tool 调查询类 Tool（不直接 import 单个 Tool）
- 异常 reason → 自动重规划（E1 餐厅满 / E2 票售罄 / E3 距离超限 / E4 时长超）
- 组装 Itinerary（六段结构：出发/主活动/转场/用餐/附加/返回）

执行类 Tool（reserve / buy_ticket / generate_share_message）由 executor.py 在用户确认后下发。

实现策略（MVP-1 用规则化 ReAct，避免 LLM 在循环中漂移）：
- 规则化路径：意图 → search_pois → search_restaurants → check_restaurant_availability
  → 重规划（最多 3 次）→ 组装 → 输出
- 规则化的好处：可调试、可重放、Demo 不翻车
- LLM 仅用于：意图解析（intent_parser）、行程文案润色（generate_share_message Tool）

双范式入口（Phase 0.6 新增）：
- plan_itinerary_with_mode(intent, mode) 是统一入口，按 mode 分发：
    - mode="rule" → 本文件的 plan_itinerary（默认，Demo 安全网）
    - mode="llm"  → llm_planner.plan_itinerary_llm（LLM 自主决策，评分项 2 加分）
- mode 解析见 schemas/planner_mode.py

不负责：
- LLM 调用（仅 intent_parser 用）
- Tool 实现（在 backend/tools/）
- HTTP / SSE（在 backend/main.py）
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from schemas.domain import Poi, Restaurant
from schemas.errors import FailureReason
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary, ItineraryStage
from schemas.tools import (
    CheckRestaurantAvailabilityInput,
    CheckRestaurantAvailabilityOutput,
    EstimateRouteTimeInput,
    EstimateRouteTimeOutput,
    GetUserProfileInput,
    GetUserProfileOutput,
    SearchPoisInput,
    SearchPoisOutput,
    SearchRestaurantsInput,
    SearchRestaurantsOutput,
)

from .trace import Tracer
from tools.registry import ToolInvocationResult, invoke_tool


# ============================================================
# 配置常量
# ============================================================

# 一次会话内单 Tool 最大调用次数（pitfalls P3-预埋 LLM 过度规划防御）
MAX_TOOL_CALLS_PER_KIND = 3

# 查询类 Tool 单独的更高上限（多级降级重试用）
MAX_TOOL_CALLS_FOR_SEARCH = 5

# 一次会话内总 Tool 调用上限
# 注：check_restaurant_availability 在最坏情况下要试 3 餐厅 × 5 时段 = 15 次第一轮
# + 第二轮兜底再扫每家自带 slots（约 6×3=18）= 33 次；
# 加上 user_profile + search_pois + search_restaurants + 3 estimate_route_time = 6
# 总共最多 39 次，给 45 留点 buffer
MAX_TOTAL_TOOL_CALLS = 45

# 标准用餐时段（按演示场景集 §S1）
# 注意：这是「下午局」的默认晚餐时段；实际时段会由 _resolve_time_window 根据 intent.start_time 动态推导
DEFAULT_DINING_TIMES = ["17:00", "17:30", "18:00"]

# 出发时间默认值（intent.start_time 解析失败时兜底）
DEFAULT_DEPART_TIME = "14:00"

# 每段活动默认时长（分钟）
# 注意：实际时长由 _resolve_time_window 根据 intent.duration_hours 动态推导
DEFAULT_MAIN_ACTIVITY_MINUTES = 120
DEFAULT_DINING_MINUTES = 90
# 行程总时长的硬下限（防 duration_hours 异常给出 [0,0]）
MIN_MAIN_ACTIVITY_MINUTES = 30
MIN_DINING_MINUTES = 30
TRANSFER_BUFFER_MINUTES = 5


# ============================================================
# 数据结构
# ============================================================

@dataclass
class PlannerResult:
    """规划结果。Itinerary 是给前端的；trace 是给 SSE 推流的。"""

    success: bool
    itinerary: Itinerary | None = None
    failure_reason: FailureReason | None = None
    failure_detail: str | None = None
    tracer: Tracer = field(default_factory=Tracer)


# ============================================================
# 主入口
# ============================================================

def plan_itinerary(
    intent: IntentExtraction,
    *,
    tracer: Tracer | None = None,
) -> PlannerResult:
    """规则化 ReAct 主循环。

    流程：
    1. emit intent_parsed
    2. get_user_profile（取 home 位置）
    3. search_pois（按 physical/experience tag 过滤）
        - 失败 → 放宽距离重试 1 次
    4. search_restaurants（按 dietary tag 过滤）
        - 失败 → 放宽距离重试 1 次
    5. 选定 1 家 POI + 1-3 家餐厅备选
    6. 循环 check_restaurant_availability：DEFAULT_DINING_TIMES × 备选餐厅 顺序尝试
        - 命中 available=true → break
        - 全部 false → empty_candidates
    7. estimate_route_time（home→POI、POI→Restaurant、Restaurant→home）
    8. 组装 Itinerary 六段
    9. emit itinerary_ready
    """
    tracer = tracer or Tracer()
    tracer.emit("intent_parsed", payload=intent.model_dump())

    counters: dict[str, int] = {}

    # 查询类 Tool 走更高上限（多级降级重试 + 多时段尝试）；其他 Tool 仍走原 3 次
    # check_restaurant_availability 也归此列：dining_slots 从 intent.duration 推导
    # 后可能产出 5 个候选时段，3 餐厅 × 5 时段 = 15 次潜在调用（首个命中即返）
    _SEARCH_TOOLS = {
        "search_pois",
        "search_restaurants",
        "check_restaurant_availability",
    }
    # check_availability 单独给更高上限（最坏 3 餐厅 × 5 时段 + 第二轮兜底扫餐厅自带 slots 约 6×3 = 33）
    MAX_TOOL_CALLS_FOR_AVAILABILITY = 30

    def _bumped(name: str) -> bool:
        counters[name] = counters.get(name, 0) + 1
        # 查询类 Tool 走更高上限（多级降级重试）；其他 Tool 仍走原 3 次
        if name == "check_restaurant_availability":
            cap = MAX_TOOL_CALLS_FOR_AVAILABILITY
        elif name in _SEARCH_TOOLS:
            cap = MAX_TOOL_CALLS_FOR_SEARCH
        else:
            cap = MAX_TOOL_CALLS_PER_KIND
        if counters[name] > cap:
            return True
        if sum(counters.values()) > MAX_TOTAL_TOOL_CALLS:
            return True
        return False

    def _call(tool: str, args: dict[str, Any]) -> ToolInvocationResult:
        if _bumped(tool):
            tracer.emit(
                "stream_error",
                {"reason": "tool_quota_exceeded", "tool": tool},
            )
            return ToolInvocationResult(
                tool=tool,
                success=False,
                reason=FailureReason.UPSTREAM_FAILURE,
                error_detail=f"{tool} 调用次数超限",
            )
        tracer.emit("tool_call_start", {"tool": tool, "input": args})
        result = invoke_tool(tool, args)
        tracer.emit(
            "tool_call_end",
            {
                "tool": tool,
                "output": result.output,
                "success": result.success,
                "reason": result.reason.value if result.reason else None,
                "duration_ms": result.duration_ms,
            },
        )
        return result

    # ---- 1. 用户画像 ----
    profile_result = _call(
        "get_user_profile",
        GetUserProfileInput().model_dump(),
    )
    if not profile_result.success:
        return _abort(tracer, profile_result.reason, "无法加载用户画像")
    user_profile = GetUserProfileOutput.model_validate(profile_result.output).profile
    if user_profile is None:
        return _abort(tracer, FailureReason.NOT_FOUND, "用户画像为空")

    # ---- 2. 查询 POI 候选 ----
    pois = _query_pois(intent, _call, tracer)
    if isinstance(pois, FailureReason):
        return _abort(tracer, pois, "POI 候选为空，约束过严")
    if not pois:
        return _abort(tracer, FailureReason.EMPTY_CANDIDATES, "POI 候选为空")

    main_poi = pois[0]
    backup_pois = pois[1:4]

    # ---- 3. 查询餐厅候选 ----
    restaurants = _query_restaurants(intent, _call, tracer)
    if isinstance(restaurants, FailureReason):
        return _abort(tracer, restaurants, "餐厅候选为空，约束过严")
    if not restaurants:
        return _abort(tracer, FailureReason.EMPTY_CANDIDATES, "餐厅候选为空")

    # ---- 4. 餐厅可用性 + 重规划（E1）----
    # Phase 0.8.1：从 intent 推导时间窗，让用餐时段反映 start_time + duration
    depart_time, dining_slots, main_minutes, dining_minutes = _resolve_time_window(
        intent
    )
    chosen_restaurant, chosen_time = _negotiate_dining(
        restaurants, intent, _call, tracer, dining_slots=dining_slots
    )
    if chosen_restaurant is None:
        return _abort(
            tracer,
            FailureReason.RESTAURANT_FULL,
            "所有候选餐厅与时段组合均无空位",
        )

    # ---- 5. 路线时间 ----
    home_to_poi = _estimate(_call, "home", main_poi.id)
    poi_to_rest = _estimate(_call, main_poi.id, chosen_restaurant.id)
    rest_to_home = _estimate(_call, chosen_restaurant.id, "home")

    # ---- 6. 组装 Itinerary ----
    party_size = sum(c.count for c in intent.companions) or 1
    itinerary = _assemble_itinerary(
        main_poi=main_poi,
        chosen_restaurant=chosen_restaurant,
        chosen_time=chosen_time,
        home_to_poi=home_to_poi,
        poi_to_rest=poi_to_rest,
        rest_to_home=rest_to_home,
        party_size=party_size,
        backup_pois=backup_pois,
        depart_time=depart_time,
        main_activity_minutes=main_minutes,
        dining_minutes=dining_minutes,
    )
    tracer.emit("itinerary_ready", payload=itinerary.model_dump())
    return PlannerResult(success=True, itinerary=itinerary, tracer=tracer)


# ============================================================
# 内部步骤
# ============================================================

def _abort(tracer: Tracer, reason: FailureReason | None, detail: str) -> PlannerResult:
    tracer.emit(
        "stream_error",
        {"reason": reason.value if reason else "unknown", "detail": detail},
    )
    return PlannerResult(
        success=False,
        failure_reason=reason,
        failure_detail=detail,
        tracer=tracer,
    )


def _query_pois(
    intent: IntentExtraction,
    call,
    tracer: Tracer,
) -> list[Poi] | FailureReason:
    """查询 POI；空集时多级降级重试。

    降级链（顺序尝试）：
    1. 原约束（distance + tags + social_context + preferred_types）
    2. 放宽距离 +2km
    3. 剥 preferred_types（用户没明示 POI 类型时该字段是 prior 注入，可去）
    4. 剥 physical_constraints + experience_tags（prior 注入的偏好让步）
    5. 仅按 distance + social_context（最宽松，仍有调性匹配）
    任一级命中候选立即返回。
    """
    age_in_party = [c.age for c in intent.companions if c.age is not None]

    def _do(
        distance: float,
        *,
        physical: list[str] | None = None,
        experience: list[str] | None = None,
        preferred_types: list[str] | None = None,
        social_context: str | None = None,
    ) -> SearchPoisOutput:
        result = call(
            "search_pois",
            SearchPoisInput(
                distance_max_km=distance,
                physical_constraints=physical
                if physical is not None
                else list(intent.physical_constraints),
                experience_tags=experience
                if experience is not None
                else list(intent.experience_tags),
                social_context=social_context
                if social_context is not None
                else intent.social_context,
                age_in_party=age_in_party or None,
                preferred_types=preferred_types
                if preferred_types is not None
                else list(intent.preferred_poi_types),
            ).model_dump(),
        )
        return SearchPoisOutput.model_validate(result.output) if result.success else SearchPoisOutput(
            success=False, reason=result.reason
        )

    # 第 1 级：原约束
    out = _do(intent.distance_max_km)
    if out.success and out.candidates:
        return list(out.candidates)

    # 仅 EMPTY_CANDIDATES 才走降级；其他错误直接返
    if out.reason not in (FailureReason.EMPTY_CANDIDATES, None):
        return out.reason or FailureReason.UPSTREAM_FAILURE

    # 第 2 级：放宽距离 +2km
    tracer.emit(
        "replan_triggered",
        {
            "reason": FailureReason.EMPTY_CANDIDATES.value,
            "from_tool": "search_pois",
            "action": "loosen_distance",
        },
    )
    out = _do(intent.distance_max_km + 2)
    if out.success and out.candidates:
        return list(out.candidates)

    # 第 3 级：剥 preferred_types
    if intent.preferred_poi_types:
        tracer.emit(
            "replan_triggered",
            {
                "reason": FailureReason.EMPTY_CANDIDATES.value,
                "from_tool": "search_pois",
                "action": "drop_preferred_types",
            },
        )
        out = _do(intent.distance_max_km + 2, preferred_types=[])
        if out.success and out.candidates:
            return list(out.candidates)

    # 第 4 级：剥 physical + experience tag（prior 注入的偏好让步）
    if intent.physical_constraints or intent.experience_tags:
        tracer.emit(
            "replan_triggered",
            {
                "reason": FailureReason.EMPTY_CANDIDATES.value,
                "from_tool": "search_pois",
                "action": "drop_optional_tags",
            },
        )
        out = _do(
            intent.distance_max_km + 2,
            physical=[],
            experience=[],
            preferred_types=[],
        )
        if out.success and out.candidates:
            return list(out.candidates)

    # 第 5 级：仅 distance + social_context（最宽松，仍有调性匹配）
    tracer.emit(
        "replan_triggered",
        {
            "reason": FailureReason.EMPTY_CANDIDATES.value,
            "from_tool": "search_pois",
            "action": "minimal_constraint",
        },
    )
    out = _do(
        intent.distance_max_km + 4,
        physical=[],
        experience=[],
        preferred_types=[],
    )
    if out.success and out.candidates:
        return list(out.candidates)

    return FailureReason.EMPTY_CANDIDATES


def _query_restaurants(
    intent: IntentExtraction,
    call,
    tracer: Tracer,
) -> list[Restaurant] | FailureReason:
    """查询餐厅；空集时多级降级重试。

    降级链（顺序尝试）：
    1. 原约束（distance + dietary + experience + social_context + capacity）
    2. 放宽距离 +2km
    3. 剥 experience_tags（prior 注入的弱约束）
    4. 剥 dietary 中 prior 注入的偏好（保留用户明示的）
    5. 仅 distance + social_context（最宽松，但仍调性匹配）
    """

    def _do(
        distance: float,
        *,
        dietary: list[str] | None = None,
        experience: list[str] | None = None,
        social_context: str | None = None,
        capacity: int | None = None,
    ) -> SearchRestaurantsOutput:
        result = call(
            "search_restaurants",
            SearchRestaurantsInput(
                distance_max_km=distance,
                dietary_constraints=dietary
                if dietary is not None
                else list(intent.dietary_constraints),
                experience_tags=experience
                if experience is not None
                else list(intent.experience_tags),
                social_context=social_context
                if social_context is not None
                else intent.social_context,
                capacity_requirement=capacity
                if capacity is not None
                else intent.capacity_requirement,
            ).model_dump(),
        )
        return SearchRestaurantsOutput.model_validate(result.output) if result.success else SearchRestaurantsOutput(
            success=False, reason=result.reason
        )

    # 第 1 级
    out = _do(intent.distance_max_km)
    if out.success and out.candidates:
        return list(out.candidates)
    if out.reason not in (FailureReason.EMPTY_CANDIDATES, None):
        return out.reason or FailureReason.UPSTREAM_FAILURE

    # 第 2 级：放宽距离
    tracer.emit(
        "replan_triggered",
        {
            "reason": FailureReason.EMPTY_CANDIDATES.value,
            "from_tool": "search_restaurants",
            "action": "loosen_distance",
        },
    )
    out = _do(intent.distance_max_km + 2)
    if out.success and out.candidates:
        return list(out.candidates)

    # 第 3 级：剥 experience
    if intent.experience_tags:
        tracer.emit(
            "replan_triggered",
            {
                "reason": FailureReason.EMPTY_CANDIDATES.value,
                "from_tool": "search_restaurants",
                "action": "drop_experience",
            },
        )
        out = _do(intent.distance_max_km + 2, experience=[])
        if out.success and out.candidates:
            return list(out.candidates)

    # 第 4 级：剥 dietary（仅当有 dietary）
    if intent.dietary_constraints:
        tracer.emit(
            "replan_triggered",
            {
                "reason": FailureReason.EMPTY_CANDIDATES.value,
                "from_tool": "search_restaurants",
                "action": "drop_dietary",
            },
        )
        out = _do(intent.distance_max_km + 2, experience=[], dietary=[])
        if out.success and out.candidates:
            return list(out.candidates)

    # 第 5 级：最宽松
    tracer.emit(
        "replan_triggered",
        {
            "reason": FailureReason.EMPTY_CANDIDATES.value,
            "from_tool": "search_restaurants",
            "action": "minimal_constraint",
        },
    )
    out = _do(
        intent.distance_max_km + 4,
        experience=[],
        dietary=[],
        capacity=None,
    )
    if out.success and out.candidates:
        return list(out.candidates)

    return FailureReason.EMPTY_CANDIDATES


def _negotiate_dining(
    restaurants: list[Restaurant],
    intent: IntentExtraction,
    call,
    tracer: Tracer,
    *,
    dining_slots: list[str] | None = None,
) -> tuple[Restaurant | None, str | None]:
    """对每家餐厅依序尝试候选时段，命中即返。

    Args:
        dining_slots: 用餐尝试时段列表；None 时退化为 DEFAULT_DINING_TIMES。
            实际由 plan_itinerary 通过 _resolve_time_window 从 intent.start_time
            + duration_hours 推导（不再硬编码 17:00/17:30/18:00）。

    异常恢复策略（E1）：
    - 第一家 17:00 满 → 切 17:30
    - 第一家全满 → 切第二家
    - 候选时段都试完仍不命中 → 把每家餐厅自带的 available 时段也试一遍兜底
      （应对 mock 时段稀疏的极端 case）
    - 全部组合失败 → (None, None)
    """
    party_size = max(1, sum(c.count for c in intent.companions) or 1)
    slots = dining_slots or DEFAULT_DINING_TIMES

    # 第一轮：用推算时段 × 前 3 家
    for idx, rest in enumerate(restaurants[:3]):  # 最多尝试前 3 家
        for time_slot in slots:
            result = call(
                "check_restaurant_availability",
                CheckRestaurantAvailabilityInput(
                    restaurant_id=rest.id,
                    time=time_slot,
                    party_size=party_size,
                ).model_dump(),
            )
            output = CheckRestaurantAvailabilityOutput.model_validate(
                result.output
            ) if result.success else None

            # 触发了 E1
            if not result.success and result.reason == FailureReason.RESTAURANT_FULL:
                tracer.emit(
                    "replan_triggered",
                    {
                        "reason": FailureReason.RESTAURANT_FULL.value,
                        "from_tool": "check_restaurant_availability",
                        "restaurant_id": rest.id,
                        "time": time_slot,
                        "action": "try_next_slot_or_restaurant",
                    },
                )
                continue

            if output and output.available:
                return rest, time_slot

            # success=true 但 available=false（Tool 实现把"软失败"写在 success=true 里的情况）
            if output and not output.available:
                tracer.emit(
                    "replan_triggered",
                    {
                        "reason": FailureReason.RESTAURANT_FULL.value,
                        "from_tool": "check_restaurant_availability",
                        "restaurant_id": rest.id,
                        "time": time_slot,
                        "action": "try_next_slot_or_restaurant",
                    },
                )
                continue

    # 第二轮兜底：推算时段都没命中，扫描每家餐厅自带的 available slots
    # （应对 mock 时段稀疏的极端 case，如 S8 粤菜的 sunday_lunch 推算 14:30 但只有 17:30/18:00 有空位）
    for rest in restaurants[:3]:
        slots_in_data = sorted(
            (s for s in rest.reservation_slots if s.available),
            key=lambda s: s.time,
        )
        for slot in slots_in_data:
            if slot.time in slots:
                continue  # 第一轮已试过
            result = call(
                "check_restaurant_availability",
                CheckRestaurantAvailabilityInput(
                    restaurant_id=rest.id,
                    time=slot.time,
                    party_size=party_size,
                ).model_dump(),
            )
            if result.success:
                output = CheckRestaurantAvailabilityOutput.model_validate(result.output)
                if output.available:
                    return rest, slot.time

    return None, None


def _estimate(call, from_id: str, to_id: str) -> int:
    """给关键节点估打车时间；失败兜底 15 分钟。"""
    result = call(
        "estimate_route_time",
        EstimateRouteTimeInput(from_location=from_id, to_location=to_id).model_dump(),
    )
    if not result.success:
        return 15
    out = EstimateRouteTimeOutput.model_validate(result.output)
    if out.route is None:
        return 15
    # 取打车 > 步行 > 公交的优先级
    return out.route.taxi_minutes or out.route.walking_minutes or out.route.bus_minutes or 15


# ============================================================
# 行程组装
# ============================================================

# 输入域 → 默认出发时间（小时，24h）
# 用户没明示具体时间时按这个表选
_TIME_OF_DAY_DEPART_HOUR = {
    "morning": 9,
    "noon": 12,
    "lunch": 12,
    "afternoon": 14,
    "evening": 18,
    "dinner": 18,
    "night": 19,
}


def _parse_start_time_hour(start_time: str | None) -> int | None:
    """从 intent.start_time 抽出小时数（24h）。

    支持的输入形态（按 §5.7 D-SoT）：
    - ISO-like："2026-05-09T14:00" / "2026-05-09 14:00"
    - 口语标签："today_afternoon" / "sunday_evening" / "weekend_morning" 等
    - 单纯口语："morning" / "evening"

    解析不出 → 返 None，调用方按默认 14:00 兜底。
    """
    if not start_time:
        return None
    text = start_time.strip().lower()

    # ISO-like: 取 T 或空格后的 HH
    for sep in ("t", " "):
        if sep in text:
            tail = text.split(sep, 1)[1]
            if ":" in tail:
                try:
                    h = int(tail.split(":", 1)[0])
                    if 0 <= h <= 23:
                        return h
                except ValueError:
                    pass

    # 口语标签：扫描关键词
    # 注意子串重叠：必须先扫描更长 / 更具体的关键词
    # 例：「afternoon」含「noon」子串，若先扫 noon 会把 afternoon 误判为 12 点
    keywords_ordered = [
        ("afternoon", 14),
        ("morning", 9),
        ("evening", 18),
        ("dinner", 18),
        ("night", 19),
        ("lunch", 12),
        ("noon", 12),
    ]
    for kw, h in keywords_ordered:
        if kw in text:
            return h

    return None


def _resolve_time_window(intent: IntentExtraction) -> tuple[str, list[str], int, int]:
    """从 intent 推导：出发时间、可选用餐时段、主活动时长、用餐时长。

    返回：
        (depart_time, dining_slots, main_minutes, dining_minutes)

    规则：
    - depart_time：从 intent.start_time 抽出小时；解析不出按 14:00 兜底
    - dining_slots：基于 depart + 主活动时长 + 转场，给 3 个候选时段（每隔 30 分钟）
        例：14:00 出发 / 主活动 2h → 用餐尝试 16:30 / 17:00 / 17:30
            18:00 出发 / 主活动 1h → 用餐尝试 19:30 / 20:00 / 20:30
    - main_minutes / dining_minutes：按 duration_hours 的中点 × 比例（主活动:用餐 ≈ 4:3）
        总时长 1h → 主 30 + 餐 30
        总时长 5h → 主 150 + 餐 90
        总时长 ≥ 5h → 维持上限 main 150 / dining 90（防过长）
    """
    # ---- 出发时间 ----
    depart_hour = _parse_start_time_hour(intent.start_time)
    if depart_hour is None:
        depart_time = DEFAULT_DEPART_TIME
    else:
        depart_time = f"{depart_hour:02d}:00"

    # ---- 总时长 ----
    # duration_hours 是 [min, max]，取中点
    lo, hi = intent.duration_hours
    total_hours = max(0.5, (lo + hi) / 2.0)
    total_minutes = int(total_hours * 60)

    # 总时长里要扣两段路程 buffer（约 30 分钟）剩余分给主活动 + 用餐
    activity_pool = max(60, total_minutes - 30)

    # 主活动:用餐 = 4:3
    main_minutes = max(MIN_MAIN_ACTIVITY_MINUTES, int(activity_pool * 4 / 7))
    dining_minutes = max(MIN_DINING_MINUTES, int(activity_pool * 3 / 7))

    # 上限：单段不超过默认值（防 duration 给 [10, 12] 把 demo 拉太长）
    main_minutes = min(main_minutes, DEFAULT_MAIN_ACTIVITY_MINUTES)
    dining_minutes = min(dining_minutes, DEFAULT_DINING_MINUTES)

    # ---- 用餐候选时段 ----
    # 假设主活动后立刻去吃饭；预估转场 + 路上 30 分钟
    earliest_dining_minutes = main_minutes + 30
    h, m = depart_time.split(":")
    base_minutes = int(h) * 60 + int(m) + earliest_dining_minutes
    # 对齐到下一个整 30 分钟
    base_minutes = ((base_minutes + 29) // 30) * 30

    # 给 5 个候选时段（每隔 30 分钟），让 _negotiate_dining 有更宽的尝试空间
    # （mock 时段密度有限：常见的是整点 + 半点；多给候选能避免 NOT_FOUND 直接 fail）
    dining_slots: list[str] = []
    for i in range(5):
        t = base_minutes + i * 30
        if t >= 24 * 60:  # 跨日防御
            break
        dining_slots.append(f"{t // 60:02d}:{t % 60:02d}")

    if not dining_slots:
        dining_slots = list(DEFAULT_DINING_TIMES)

    return depart_time, dining_slots, main_minutes, dining_minutes


def _add_minutes(time_str: str, minutes: int) -> str:
    """形如 "14:00" + 30 → "14:30"。简化实现，不处理跨日。"""
    h, m = time_str.split(":")
    total = int(h) * 60 + int(m) + minutes
    return f"{total // 60:02d}:{total % 60:02d}"


def _assemble_itinerary(
    *,
    main_poi: Poi,
    chosen_restaurant: Restaurant,
    chosen_time: str,
    home_to_poi: int,
    poi_to_rest: int,
    rest_to_home: int,
    party_size: int,
    backup_pois: list[Poi],
    depart_time: str = DEFAULT_DEPART_TIME,
    main_activity_minutes: int = DEFAULT_MAIN_ACTIVITY_MINUTES,
    dining_minutes: int = DEFAULT_DINING_MINUTES,
) -> Itinerary:
    """组装六段行程。

    新增参数（Phase 0.8.1，修复硬编码 14-19 时间窗 bug）：
        depart_time: 出发时间，从 intent.start_time 推导
        main_activity_minutes: 主活动时长，从 intent.duration_hours 按比例推导
        dining_minutes: 用餐时长，同上
    缺省走兼容默认值（14:00 / 120 / 90），不破现有测试。
    """
    depart = depart_time
    arrive_poi = _add_minutes(depart, home_to_poi)
    leave_poi = _add_minutes(arrive_poi, main_activity_minutes)
    arrive_rest = _add_minutes(leave_poi, poi_to_rest + TRANSFER_BUFFER_MINUTES)
    # 若 arrive_rest 早于 chosen_time（比如用户说 1 小时，活动结束才 14:30 但餐厅最早只有 17:00 空位）：
    # 用餐起点采用 chosen_time，但要让"转场"段延长到 chosen_time，避免出现 14:30-17:00 空白窗口
    dining_start = chosen_time if chosen_time > arrive_rest else arrive_rest
    transfer_end = dining_start  # 转场段直接拉到 dining_start
    dining_end = _add_minutes(dining_start, dining_minutes)
    home_back = _add_minutes(dining_end, rest_to_home)

    stages = [
        ItineraryStage(
            kind="出发",
            start=depart,
            end=arrive_poi,
            title=f"出发前往「{main_poi.name}」",
            poi_id=main_poi.id,
            note=f"打车约 {home_to_poi} 分钟",
        ),
        ItineraryStage(
            kind="主活动",
            start=arrive_poi,
            end=leave_poi,
            title=f"{main_poi.type} · {main_poi.name}",
            poi_id=main_poi.id,
        ),
        ItineraryStage(
            kind="转场",
            start=leave_poi,
            end=transfer_end,
            title=f"前往「{chosen_restaurant.name}」",
            note=(
                f"打车约 {poi_to_rest} 分钟"
                if transfer_end == arrive_rest
                else f"打车约 {poi_to_rest} 分钟，到达后稍作休息等到用餐时段"
            ),
        ),
        ItineraryStage(
            kind="用餐",
            start=dining_start,
            end=dining_end,
            title=f"{chosen_restaurant.cuisine} · {chosen_restaurant.name}",
            restaurant_id=chosen_restaurant.id,
            note=f"已为你预留 {chosen_time}（{party_size} 人）",
        ),
        ItineraryStage(
            kind="返回",
            start=dining_end,
            end=home_back,
            title="回家",
            note=f"打车约 {rest_to_home} 分钟",
        ),
    ]

    total_minutes = _diff_minutes(depart, home_back)
    backup_summary = (
        f"；备选 POI：{', '.join(p.name for p in backup_pois[:2])}"
        if backup_pois
        else ""
    )
    summary = (
        f"半日方案 · {main_poi.name} → {chosen_restaurant.name}{backup_summary}"
    )

    return Itinerary(
        summary=summary,
        stages=stages,
        orders=[],  # MVP-1 在 executor 里追加
        share_message=None,
        total_minutes=total_minutes,
    )


def _diff_minutes(start: str, end: str) -> int:
    h1, m1 = start.split(":")
    h2, m2 = end.split(":")
    return (int(h2) - int(h1)) * 60 + (int(m2) - int(m1))


# ============================================================
# 双范式入口（Phase 0.6 新增）
# ============================================================

def plan_itinerary_with_mode(
    intent: IntentExtraction,
    mode: str | None = None,
    *,
    tracer: Tracer | None = None,
    llm_client: Any | None = None,
) -> PlannerResult:
    """按 mode 分发到 rule / llm 两套规划。

    mode 解析优先级（详见 schemas/planner_mode.py）：
        显式参数 > PLANNER_MODE 环境变量 > "rule" 默认值

    LLM 模式内部由 PLANNER_LLM_STRATEGY 决定具体实现：
        - "hybrid"           ：A+C 混合（ILS + Critic + LLM 决策；评分项 2 主推；默认）
        - "function_calling" ：纯 LLM Function Calling 自主调 Tool（旧实现）
    任一 LLM 路径失败时自动 fallback 到 rule。

    若 mode="llm" 但 llm_client 为 None，自动用 get_llm_client() 取（B 块端点会显式注入）。
    """
    from schemas.planner_mode import normalize_mode, current_env_mode

    resolved = normalize_mode(mode) if mode else current_env_mode()

    if resolved == "llm":
        # 提前确保 tracer 存在，让 LLM 分支的 agent_thought（fallback / stub 提示）
        # 都能落到 result.tracer 里被 SSE 网关 / 测试断言消费
        tracer = tracer or Tracer()

        client = llm_client
        if client is None:
            from .llm_client import get_llm_client
            try:
                client = get_llm_client()
            except (ValueError, RuntimeError):
                # 缺 API key / base_url → 降级到 rule，而非抛异常让 demo 翻车
                tracer.emit(
                    "agent_thought",
                    {"text": "LLM 客户端不可用（缺 API Key 或 base_url），已切回规则规划"},
                )
                return plan_itinerary(intent, tracer=tracer)

        # 当 client 是 stub 时，意味着无真 LLM 决策能力——保持 rule 行为以确保 demo 单测稳定
        # （Hybrid / Function Calling 都需要真 LLM 出权重 / 决策）
        if getattr(client, "provider", None) == "stub":
            tracer.emit(
                "agent_thought",
                {"text": "LLM 客户端为 stub 模式，无主观决策能力，已切回规则规划"},
            )
            return plan_itinerary(intent, tracer=tracer)

        strategy = (os.getenv("PLANNER_LLM_STRATEGY", "hybrid") or "hybrid").strip().lower()

        if strategy == "function_calling":
            from .llm_planner import plan_itinerary_llm
            return plan_itinerary_llm(intent, client=client, tracer=tracer)

        # 默认 hybrid：A+C 混合
        return _plan_with_hybrid(intent, client=client, tracer=tracer)

    # 默认 rule
    return plan_itinerary(intent, tracer=tracer)


# ============================================================
# A+C 混合范式适配器
# ============================================================

def _plan_with_hybrid(
    intent: IntentExtraction,
    *,
    client: Any,
    tracer: Tracer | None,
) -> PlannerResult:
    """把 planner_hybrid 的输出包成 PlannerResult；失败时 fallback 到 rule。

    rule_assembler：闭包内调 rule planner 的 _estimate / _resolve_time_window /
    _assemble_itinerary 三个 helper，让 hybrid 不需要重写时间轴拼装逻辑。
    """
    from .planner_hybrid import plan_hybrid, CandidatePlan

    tracer = tracer or Tracer()
    tracer.emit("intent_parsed", payload=intent.model_dump())

    def _hybrid_assembler(
        intent_: IntentExtraction,
        candidate: "CandidatePlan",
        local_tracer: Tracer,
    ):
        """把 ILS 选出来的 candidate 跑路线估算 + 时间窗 + 组装。"""
        # 用最简包装让 _estimate 复用现有 trace 接口
        def _call_route(tool: str, args: dict[str, Any]):
            local_tracer.emit("tool_call_start", {"tool": tool, "input": args})
            res = invoke_tool(tool, args)
            local_tracer.emit(
                "tool_call_end",
                {
                    "tool": tool,
                    "output": res.output,
                    "success": res.success,
                    "reason": res.reason.value if res.reason else None,
                    "duration_ms": res.duration_ms,
                },
            )
            return res

        depart_time, _, main_minutes, dining_minutes = _resolve_time_window(intent_)
        home_to_poi = _estimate(_call_route, "home", candidate.main_poi.id)
        poi_to_rest = _estimate(
            _call_route, candidate.main_poi.id, candidate.restaurant.id
        )
        rest_to_home = _estimate(_call_route, candidate.restaurant.id, "home")
        party_size = max(1, sum(c.count for c in intent_.companions) or 1)
        return _assemble_itinerary(
            main_poi=candidate.main_poi,
            chosen_restaurant=candidate.restaurant,
            chosen_time=candidate.dining_time,
            home_to_poi=home_to_poi,
            poi_to_rest=poi_to_rest,
            rest_to_home=rest_to_home,
            party_size=party_size,
            backup_pois=candidate.backup_pois,
            depart_time=depart_time,
            main_activity_minutes=main_minutes,
            dining_minutes=dining_minutes,
        )

    result = plan_hybrid(
        intent,
        client=client,
        tracer=tracer,
        rule_assembler=_hybrid_assembler,
    )

    if result.success and result.itinerary is not None:
        # 把 critic_report / weights 写到 trace 末尾，方便前端展示
        if result.weights is not None:
            tracer.emit("agent_thought", {
                "text": f"采用权重：{result.weights.summary()}",
                "weights": result.weights.to_dict(),
            })
        if result.critic_report is not None:
            tracer.emit("agent_thought", {
                "text": (
                    f"Critic 通过；soft_score={result.critic_report.soft_score:.2f}"
                ),
                "critic_report": result.critic_report.to_dict(),
            })
        tracer.emit("itinerary_ready", payload=result.itinerary.model_dump())
        return PlannerResult(success=True, itinerary=result.itinerary, tracer=tracer)

    # Hybrid 失败 → fallback 到 rule（不让 demo 翻车）
    tracer.emit(
        "agent_thought",
        {
            "text": (
                f"A+C 混合规划失败：{result.failure_detail or '未知原因'}；"
                "已自动切回规则规划"
            ),
        },
    )
    return plan_itinerary(intent, tracer=tracer)
