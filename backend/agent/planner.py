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
- 后续 MVP-2 可选切换成 chat_with_tools 让 LLM 自己 ReAct（保留接口位）

不负责：
- LLM 调用（仅 intent_parser 用）
- Tool 实现（在 backend/tools/）
- HTTP / SSE（在 backend/main.py）
"""

from __future__ import annotations

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

# 一次会话内总 Tool 调用上限
MAX_TOTAL_TOOL_CALLS = 12

# 标准用餐时段（按演示场景集 §S1）
DEFAULT_DINING_TIMES = ["17:00", "17:30", "18:00"]

# 出发时间默认值
DEFAULT_DEPART_TIME = "14:00"

# 每段活动默认时长
MAIN_ACTIVITY_MINUTES = 120
DINING_MINUTES = 90
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

    def _bumped(name: str) -> bool:
        counters[name] = counters.get(name, 0) + 1
        if counters[name] > MAX_TOOL_CALLS_PER_KIND:
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
    chosen_restaurant, chosen_time = _negotiate_dining(
        restaurants, intent, _call, tracer
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
    """查询 POI；空集时放宽距离 +2km 重试 1 次。"""
    age_in_party = [c.age for c in intent.companions if c.age is not None]

    def _do(distance: float) -> SearchPoisOutput:
        result = call(
            "search_pois",
            SearchPoisInput(
                distance_max_km=distance,
                physical_constraints=list(intent.physical_constraints),
                experience_tags=list(intent.experience_tags),
                social_context=intent.social_context,
                age_in_party=age_in_party or None,
                preferred_types=list(intent.preferred_poi_types),
            ).model_dump(),
        )
        return SearchPoisOutput.model_validate(result.output) if result.success else SearchPoisOutput(
            success=False, reason=result.reason
        )

    out = _do(intent.distance_max_km)
    if out.success and out.candidates:
        return list(out.candidates)
    if out.reason in (FailureReason.EMPTY_CANDIDATES, None):
        # 重规划：放宽距离 +2km
        tracer.emit(
            "replan_triggered",
            {"reason": FailureReason.EMPTY_CANDIDATES.value, "from_tool": "search_pois", "action": "loosen_distance"},
        )
        loosened = _do(intent.distance_max_km + 2)
        if loosened.success and loosened.candidates:
            return list(loosened.candidates)
        return FailureReason.EMPTY_CANDIDATES
    return out.reason or FailureReason.UPSTREAM_FAILURE


def _query_restaurants(
    intent: IntentExtraction,
    call,
    tracer: Tracer,
) -> list[Restaurant] | FailureReason:
    """查询餐厅；空集时放宽距离 +2km 重试 1 次。"""

    def _do(distance: float) -> SearchRestaurantsOutput:
        result = call(
            "search_restaurants",
            SearchRestaurantsInput(
                distance_max_km=distance,
                dietary_constraints=list(intent.dietary_constraints),
                experience_tags=list(intent.experience_tags),
                social_context=intent.social_context,
                capacity_requirement=intent.capacity_requirement,
            ).model_dump(),
        )
        return SearchRestaurantsOutput.model_validate(result.output) if result.success else SearchRestaurantsOutput(
            success=False, reason=result.reason
        )

    out = _do(intent.distance_max_km)
    if out.success and out.candidates:
        return list(out.candidates)
    if out.reason in (FailureReason.EMPTY_CANDIDATES, None):
        tracer.emit(
            "replan_triggered",
            {"reason": FailureReason.EMPTY_CANDIDATES.value, "from_tool": "search_restaurants", "action": "loosen_distance"},
        )
        loosened = _do(intent.distance_max_km + 2)
        if loosened.success and loosened.candidates:
            return list(loosened.candidates)
        return FailureReason.EMPTY_CANDIDATES
    return out.reason or FailureReason.UPSTREAM_FAILURE


def _negotiate_dining(
    restaurants: list[Restaurant],
    intent: IntentExtraction,
    call,
    tracer: Tracer,
) -> tuple[Restaurant | None, str | None]:
    """对每家餐厅依序尝试默认时段，命中即返。

    异常恢复策略（E1）：
    - 第一家 17:00 满 → 切 17:30
    - 第一家全满 → 切第二家
    - 全部组合失败 → (None, None)
    """
    party_size = max(1, sum(c.count for c in intent.companions) or 1)

    for idx, rest in enumerate(restaurants[:3]):  # 最多尝试前 3 家
        for time_slot in DEFAULT_DINING_TIMES:
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
) -> Itinerary:
    depart = DEFAULT_DEPART_TIME
    arrive_poi = _add_minutes(depart, home_to_poi)
    leave_poi = _add_minutes(arrive_poi, MAIN_ACTIVITY_MINUTES)
    arrive_rest = _add_minutes(leave_poi, poi_to_rest + TRANSFER_BUFFER_MINUTES)
    # 餐厅约 chosen_time，但若 arrive_rest > chosen_time 则采用 arrive_rest
    dining_start = max(arrive_rest, chosen_time)
    dining_end = _add_minutes(dining_start, DINING_MINUTES)
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
            end=arrive_rest,
            title=f"前往「{chosen_restaurant.name}」",
            note=f"打车约 {poi_to_rest} 分钟",
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
