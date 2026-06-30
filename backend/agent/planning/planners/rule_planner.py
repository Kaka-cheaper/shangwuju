"""agent.planner —— ReAct 规划主循环（Planning）。

⚠️ 冻结声明（2026-05-22）：
    本文件是 hackathon 早期的 rule-based ReAct 主路径，自 LangGraph 主架构上线后
    降级为 fallback safety-net（LangGraph 路径异常时启用）。**不再添加新功能**。

    保留理由：
    - LangGraph replan_node 第 3 次重排兜底 + collab/room.py 兜底
    - hybrid / blueprint 拼装路径复用 _assemble_itinerary helper（不可移除）
    - 单元测试 / stub 路径稳定性兜底

    所有新功能改动应在 `agent/graph/` 下完成；本文件仅做 bug fix 与必要的 schema 适配。

职责：
- 接 IntentExtraction（来自 intent_parser）
- 通过 invoke_tool 调查询类 Tool（不直接 import 单个 Tool）
- 异常 reason → 自动重规划（E1 餐厅满 / E2 票售罄 / E3 距离超限 / E4 时长超）
- 组装 Itinerary（edge_v1：home → mid nodes → home + 自动 hop；不再硬塞 5 段过程段）

执行类 Tool（reserve / buy_ticket / generate_share_message）由 executor.py 在用户确认后下发。

实现策略（MVP-1 用规则化 ReAct，避免 LLM 在循环中漂移）：
- 规则化路径：意图 → search_pois → search_restaurants → check_restaurant_availability
  → 重规划（最多 3 次）→ 组装 → 输出
- 规则化的好处：可调试、可重放、Demo 不翻车
- LLM 仅用于：意图解析（intent_parser）、行程文案润色（generate_share_message Tool）

入口：
- plan_itinerary(intent) 是规则范式入口（默认，Demo 安全网）。
- LLM 主路径走 LangGraph（agent/graph/），ILS 加分路径走 ils_planner.plan_hybrid；
  V1 按 mode 分发的双范式入口已退役删除（规划层收口）。

不负责：
- LLM 调用（仅 intent_parser 用）
- Tool 实现（在 backend/tools/）
- HTTP / SSE（在 backend/main.py）
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Any

from data.loader import load_pois, load_restaurants
from schemas.domain import Poi, Restaurant
from schemas.errors import FailureReason
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary
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

from ...core.trace import Tracer
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
    # 入口防线（pitfalls P1-2026-05-17 引申）：raw_input 含具体小时数 → 强制覆盖
    # duration_hours，作为反馈作为最高优先级约束的兜底
    intent = _enforce_intent_duration_from_raw(intent, tracer)
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

    # ---- 1.5. 决定本次行程要哪些中间节点（edge_v1：节点=intent 的函数）----
    # 旧版用 decide_segments 返「段集合」frozenset；edge_v1 起改为 decide_nodes 返「中间节点 kind 列表」。
    # 此处仍保留 segments 内部命名（兼容下游 _resolve_time_window / _assemble_itinerary 实现细节），
    # 但语义已等同于 ALWAYS_INCLUDED ∪ {主活动?} ∪ {用餐?} ∪ {转场?}。
    from ..blueprint.node_decider import decide_segments, explain_segments
    segments = decide_segments(intent)
    tracer.emit("agent_thought", {"text": explain_segments(intent, segments)})

    needs_main = "主活动" in segments
    needs_dining = "用餐" in segments

    # ---- 2. 查询 POI 候选（按需）----
    main_poi: Poi | None = None
    backup_pois: list[Poi] = []
    if needs_main:
        pois = _query_pois(intent, _call, tracer)
        if isinstance(pois, FailureReason):
            return _abort(tracer, pois, "POI 候选为空，约束过严")
        if not pois:
            return _abort(tracer, FailureReason.EMPTY_CANDIDATES, "POI 候选为空")
        main_poi = pois[0]
        backup_pois = pois[1:4]

    # ---- 3. 查询餐厅候选（按需）----
    chosen_restaurant: Restaurant | None = None
    chosen_time: str | None = None
    depart_time, dining_slots, main_minutes, dining_minutes = _resolve_time_window(
        intent, segments=segments
    )

    if needs_dining:
        restaurants = _query_restaurants(intent, _call, tracer)
        if isinstance(restaurants, FailureReason):
            return _abort(tracer, restaurants, "餐厅候选为空，约束过严")
        if not restaurants:
            return _abort(tracer, FailureReason.EMPTY_CANDIDATES, "餐厅候选为空")

        # ---- 4. 餐厅可用性 + 重规划（E1）----
        chosen_restaurant, chosen_time = _negotiate_dining(
            restaurants, intent, _call, tracer, dining_slots=dining_slots
        )
        if chosen_restaurant is None:
            return _abort(
                tracer,
                FailureReason.RESTAURANT_FULL,
                "所有候选餐厅与时段组合均无空位",
            )

        # ---- 4.5 二次裁段（pitfalls P2-2026-05-17 修复）----
        # 仅在「短场景」（duration_hours 上限 ≤ 2h）启用：用户说"只有 1-2 小时"时严格守约
        # 长场景（家庭半日 3-5h 等）容忍合理等待 + 路程，不触发裁段
        if max(intent.duration_hours) <= 2:
            max_minutes = max(intent.duration_hours) * 60 + 15  # 15min 容忍
            depart_h, depart_m = depart_time.split(":")
            depart_total = int(depart_h) * 60 + int(depart_m)
            chosen_h, chosen_m = chosen_time.split(":")
            chosen_total = int(chosen_h) * 60 + int(chosen_m)
            finish_total = chosen_total + dining_minutes + 15  # 15min 回家估算
            actual_span = finish_total - depart_total

            if actual_span > max_minutes:
                tracer.emit(
                    "agent_thought",
                    {
                        "text": (
                            f"时间约束兜底：最早可订 {chosen_time}，到 "
                            f"{finish_total // 60:02d}:{finish_total % 60:02d} 才结束，"
                            f"超过 {max(intent.duration_hours)}h 上限。"
                            f"裁掉用餐段以满足时间约束，建议改约更晚时段或下次预约餐厅。"
                        ),
                    },
                )
                chosen_restaurant = None
                chosen_time = None
                segments = frozenset(s for s in segments if s != "用餐") | (
                    {"主活动"} if not needs_main else set()
                )
                needs_dining = False
                # 若原本只剩用餐 → 现在裁掉变只剩出发/返回；补回主活动让用户至少有去处
                if not needs_main and "主活动" in segments:
                    needs_main = True
                    pois = _query_pois(intent, _call, tracer)
                    if not isinstance(pois, FailureReason) and pois:
                        main_poi = pois[0]
                        backup_pois = pois[1:4]
                # 重新算 time_window
                depart_time, dining_slots, main_minutes, dining_minutes = (
                    _resolve_time_window(intent, segments=segments)
                )

    # ---- 5. 路线时间（按 segments 决定查哪些）----
    home_to_poi = _estimate(_call, "home", main_poi.id) if main_poi else 0
    poi_to_rest = (
        _estimate(_call, main_poi.id, chosen_restaurant.id)
        if (main_poi and chosen_restaurant)
        else 0
    )
    if chosen_restaurant:
        rest_to_home = _estimate(_call, chosen_restaurant.id, "home")
    elif main_poi:
        rest_to_home = _estimate(_call, main_poi.id, "home")
    else:
        rest_to_home = 0  # 极端兜底（理论上 segments 总会含 main 或 dining 之一）

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
        segments=segments,
    )
    tracer.emit("itinerary_ready", payload=itinerary.model_dump())
    return PlannerResult(success=True, itinerary=itinerary, tracer=tracer)


# ============================================================
# 内部步骤
# ============================================================

# ============================================================
# 入口防线：raw_input 兜底（pitfalls P1-2026-05-17 引申）
# ============================================================

def _enforce_intent_duration_from_raw(
    intent: IntentExtraction, tracer: Tracer
) -> IntentExtraction:
    """从 raw_input 提取精确小时数，强制覆盖 intent.duration_hours。

    动机：
    - 用户原话「反馈应该是最前面的一个约束」
    - refiner 把反馈拼到 raw_input 末尾（"...（用户反馈：只有一个小时）"）
      但 LLM 可能漂移导致 duration_hours 字段不完全反映 raw_input 的精确数字
    - 即使 _enforce_duration_consistency 在 refiner 出口已对齐，依然给最稳的入口防线

    策略：
    - intent.raw_input 含「N 小时」/「N 个小时」/「N 到 M 小时」 → 抽出 (lo, hi)
    - 与 intent.duration_hours 不一致 → 强制覆盖
    - 推一条 agent_thought 到 trace，让评委可见这个"高优先级反馈"的执行

    对正常 plan（无反馈句的 raw_input）→ 提取返回 None → 不动 intent。
    """
    if not intent.raw_input:
        return intent

    from ...intent.refiner import _extract_duration_from_feedback

    extracted = _extract_duration_from_feedback(intent.raw_input)
    if extracted is None:
        return intent

    current = tuple(intent.duration_hours)
    if current == extracted:
        return intent

    # 强制覆盖
    fixed = intent.model_copy(update={"duration_hours": list(extracted)})
    tracer.emit(
        "agent_thought",
        {
            "text": (
                f"反馈最高优先级约束：raw_input 含 {list(extracted)} 小时，"
                f"覆盖原 duration_hours {list(current)} → {list(extracted)}"
            ),
        },
    )
    return fixed


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
    """给关键节点估打车时间。

    优先用 routes.json（mock 内手工调过的时间）；兜底用 haversine 距离 + 平均车速。
    再 fallback 到固定 15 分钟。

    设计动机（2026-05-22 重构）：
        - routes.json 是 56 条手工随机数，存在大量 from→to 没覆盖的情况
        - 缺路线时返 15 分钟会让 stage 间「打车约 15 分钟」无意义
        - 现在改为：mock 命中走 mock；mock 没命中且双方都有坐标 → haversine 估算
        - 平均车速取 25 km/h（杭州市区拥堵实测中位数），向上取整到分钟
    """
    result = call(
        "estimate_route_time",
        EstimateRouteTimeInput(from_location=from_id, to_location=to_id).model_dump(),
    )
    if result.success:
        out = EstimateRouteTimeOutput.model_validate(result.output)
        if out.route is not None:
            mode_value = (
                out.route.taxi_minutes
                or out.route.walking_minutes
                or out.route.bus_minutes
            )
            if mode_value:
                return mode_value

    # mock 没命中 → 从坐标估算
    haversine_minutes = _estimate_minutes_by_haversine(from_id, to_id)
    if haversine_minutes is not None:
        return haversine_minutes

    return 15


# ============================================================
# 坐标估算辅助（routes.json fallback；2026-05-22 新增）
# ============================================================

# 平均车速（km/h）—— 杭州市区拥堵实测中位数
_AVG_TAXI_SPEED_KMH = 25.0
# 起步耗时（分钟）—— 上车 / 下车 / 等红绿灯固定耗时
_TAXI_BASE_MINUTES = 4


def _haversine_km(
    lat1: float, lng1: float, lat2: float, lng2: float
) -> float:
    """两个经纬度点间的球面距离（km）。"""
    r = 6371.0  # 地球半径
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(
        dlmb / 2
    ) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def _coord_of(loc_id: str) -> tuple[float, float] | None:
    """根据 home / poi_id / restaurant_id 取坐标 (lat, lng)。无坐标返 None。"""
    if loc_id == "home":
        # demo_user 的家 —— 见 mock_data/user_profile.json
        return (30.275, 120.075)
    for p in load_pois():
        if p.id == loc_id:
            if p.location.lat is not None and p.location.lng is not None:
                return (p.location.lat, p.location.lng)
            return None
    for r in load_restaurants():
        if r.id == loc_id:
            if r.location.lat is not None and r.location.lng is not None:
                return (r.location.lat, r.location.lng)
            return None
    return None


def _estimate_minutes_by_haversine(
    from_id: str, to_id: str
) -> int | None:
    """两点都查得到坐标 → haversine 距离 / 平均车速 + 起步耗时。否则 None。"""
    a = _coord_of(from_id)
    b = _coord_of(to_id)
    if a is None or b is None:
        return None
    km = _haversine_km(a[0], a[1], b[0], b[1])
    if km <= 0:
        return _TAXI_BASE_MINUTES
    minutes = int(round(km / _AVG_TAXI_SPEED_KMH * 60)) + _TAXI_BASE_MINUTES
    # 限定 [3, 90] 分钟（防极端值）
    return max(3, min(minutes, 90))


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


def _resolve_time_window(
    intent: IntentExtraction,
    segments: frozenset[str] | None = None,
) -> tuple[str, list[str], int, int]:
    """从 intent 推导：出发时间、可选用餐时段、主活动时长、用餐时长。

    Phase 0.10.2（pitfalls P2-2026-05-17 修复）：
    新增 segments 参数。当段集合不含「主活动」或「用餐」时，对应时长设为 0，
    避免用 30min 下限把 1 小时反馈拉成 1.5h+ 总行程。

    返回：
        (depart_time, dining_slots, main_minutes, dining_minutes)

    规则：
    - depart_time：从 intent.start_time 抽出小时；解析不出按 14:00 兜底
    - dining_slots：基于 depart + 路程 + 主活动时长，给 5 个候选时段
        例：14:00 出发 / 主活动 2h → 用餐尝试 16:30 / 17:00 / 17:30
            14:00 出发 / 无主活动（直接吃）→ 用餐尝试 14:30 / 15:00 / 15:30
    - main_minutes / dining_minutes：按段集合 + 总时长 + 比例分配
        含主活动 + 含用餐 → 4:3 比例
        仅主活动 / 仅用餐 → 全部时长给该段
        不含 → 0
    """
    # ---- 出发时间 ----
    depart_hour = _parse_start_time_hour(intent.start_time)
    if depart_hour is None:
        depart_time = DEFAULT_DEPART_TIME
    else:
        depart_time = f"{depart_hour:02d}:00"

    # ---- 总时长 ----
    lo, hi = intent.duration_hours
    total_hours = max(0.5, (lo + hi) / 2.0)
    total_minutes = int(total_hours * 60)

    # 段集合（兼容旧调用：不传 segments 等同于完整 5 段）
    has_main = segments is None or "主活动" in segments
    has_dining = segments is None or "用餐" in segments

    # ---- 时长分配（pitfalls P2 修复：1h 场景不再被 30min 下限拉爆）----
    # 路程 buffer：含转场段时扣 30，否则扣 15（仅出发 + 单段 + 返回）
    transit_buffer = 30 if (has_main and has_dining) else 15
    activity_pool = max(15, total_minutes - transit_buffer)

    if has_main and has_dining:
        # 4:3 分配，但下限随段池线性下降（1h 场景 main=24/dining=18，不再硬卡 30）
        main_minutes = max(15, int(activity_pool * 4 / 7))
        dining_minutes = max(15, int(activity_pool * 3 / 7))
    elif has_main:
        main_minutes = max(15, activity_pool)
        dining_minutes = 0
    elif has_dining:
        main_minutes = 0
        dining_minutes = max(15, activity_pool)
    else:
        main_minutes = 0
        dining_minutes = 0

    # 上限：单段不超过默认值
    main_minutes = min(main_minutes, DEFAULT_MAIN_ACTIVITY_MINUTES)
    dining_minutes = min(dining_minutes, DEFAULT_DINING_MINUTES)

    # ---- 用餐候选时段 ----
    # 起点：出发时间 + 路上（home→target）+ 主活动（如有）+ 转场缓冲
    if has_main:
        earliest_dining_minutes = main_minutes + 30  # 含转场 + 路上
    else:
        earliest_dining_minutes = 15  # 直接到餐厅，仅出发路程
    h, m = depart_time.split(":")
    base_minutes = int(h) * 60 + int(m) + earliest_dining_minutes
    base_minutes = ((base_minutes + 29) // 30) * 30  # 对齐 30 分钟

    dining_slots: list[str] = []
    for i in range(5):
        t = base_minutes + i * 30
        if t >= 24 * 60:
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
    main_poi: Poi | None,
    chosen_restaurant: Restaurant | None,
    chosen_time: str | None,
    home_to_poi: int,
    poi_to_rest: int,
    rest_to_home: int,
    party_size: int,
    backup_pois: list[Poi],
    depart_time: str = DEFAULT_DEPART_TIME,
    main_activity_minutes: int = DEFAULT_MAIN_ACTIVITY_MINUTES,
    dining_minutes: int = DEFAULT_DINING_MINUTES,
    segments: frozenset[str] | None = None,
    intent: IntentExtraction | None = None,
    user_profile: Any | None = None,
) -> Itinerary:
    """组装行程（edge_v1：home → mid nodes → home + 自动 hop）。

    实现策略（Wave 5 Task 9 重写）：
        rule planner 不再自己拼时间轴 + 手写 5 段 ItineraryStage；
        改为构造一个最小 PlanBlueprint（mid nodes 列表）后调
        `agent.assemble_blueprint.assemble_from_blueprint`，由它统一负责：

        - 自动补 home 首尾节点（target_kind="home"，duration=0）
        - 自动调 lookup_hop 计算节点间通勤分钟（mock routes.json + haversine 三级降级）
        - 自动派生 schedule 视图

        这样 rule / hybrid / llm-first 三种规划路径产物完全一致，前端只需渲染 nodes + hops。

    映射（旧 stages → 新 mid nodes）：
        - has_main:    BlueprintNode(target_kind="poi", target_id=main_poi.id, duration=main_activity_minutes)
        - has_dining:  BlueprintNode(target_kind="restaurant", target_id=chosen_restaurant.id, duration=dining_minutes)
        - 旧「出发 / 转场 / 返回」过程段：edge_v1 由自动 hop 表达，不再是 node

    对 chosen_time 的处理（餐厅可订时段对齐）：
        - 若同时有主活动 + 用餐：把 chosen_time 写入 dining node 的 note；
          同时把 main 时长延长到「让餐厅自然到达时刻 ≥ chosen_time」（用户在 POI 多停留）
        - 若仅有用餐：把 preferred_start_time 直接设为 chosen_time（去除路上延迟，让 dining 准时开始）
        - 若 chosen_time 不存在或自然到达更晚：维持 dining_minutes 不变

    向后兼容（旧调用方签名不变）：
        - 老调用方（不传 segments）+ 主 POI/餐厅都给 → 走旧 5 段语义（mid: 主活动 + 用餐）
        - 老调用方 + 缺其一 → 抛 ValueError（早暴露 bug 而非默默退化）

    新增参数：
        intent: 透传给 assemble_from_blueprint（当前未读，留作扩展余地）；
                调用方未传时构造一个最小占位 IntentExtraction，避免改 hybrid_assembler 签名
        user_profile: home_location 来源；未传时调 load_user_profile() 读 mock_data
    """
    from data.loader import load_user_profile
    from schemas.domain import UserProfile

    from ..blueprint.assemble_blueprint import assemble_from_blueprint
    from ..blueprint.blueprint import BlueprintNode, BlueprintTargetKind, PlanBlueprint

    # 决定 segments：缺省时按可用 POI/餐厅推导
    if segments is None:
        if main_poi is not None and chosen_restaurant is not None:
            segments = frozenset({"出发", "主活动", "转场", "用餐", "返回"})
        else:
            raise ValueError(
                "_assemble_itinerary: 缺省 segments 时 main_poi 与 chosen_restaurant 必须都给；"
                "若需要削段请显式传 segments"
            )

    # 校验：segments 与 POI/餐厅不一致时早抛
    if "主活动" in segments and main_poi is None:
        raise ValueError("segments 含「主活动」但 main_poi 为空")
    if "用餐" in segments and chosen_restaurant is None:
        raise ValueError("segments 含「用餐」但 chosen_restaurant 为空")

    has_main = "主活动" in segments
    has_dining = "用餐" in segments
    if not (has_main or has_dining):
        raise ValueError("segments 必须至少含「主活动」或「用餐」之一")

    # ---- 构造 mid nodes ----
    mid_nodes: list[BlueprintNode] = []

    # POI 持续时间补偿：当 chosen_time > 自然到达餐厅时刻，把"等待时间"塞进主活动
    # （用户在 POI 多停留，避免餐厅前长时间空 hop 浪费）
    poi_duration = main_activity_minutes
    if has_main and has_dining and chosen_time is not None:
        try:
            depart_min_int = _parse_hhmm_to_min(depart_time)
            chosen_min_int = _parse_hhmm_to_min(chosen_time)
            # 自然到达餐厅时刻：出发 + home→POI + POI 停留 + POI→餐厅 + 5min buffer（与 assemble 一致）
            natural_arrive_rest_min = (
                depart_min_int
                + home_to_poi
                + main_activity_minutes
                + poi_to_rest
                + 5  # buffer_min（与 assemble_from_blueprint 默认一致）
            )
            if chosen_min_int > natural_arrive_rest_min:
                poi_duration += chosen_min_int - natural_arrive_rest_min
        except (ValueError, AttributeError):
            # chosen_time 异常 → 不做补偿，note 里仍写 chosen_time，时间轴自然推进
            pass

    if has_main:
        assert main_poi is not None  # narrowing for type checker
        mid_nodes.append(
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id=main_poi.id,
                duration_min=poi_duration,
                note=None,
            )
        )

    if has_dining:
        assert chosen_restaurant is not None
        dining_note = (
            f"已为你预留 {chosen_time}（{party_size} 人）"
            if chosen_time
            else f"{party_size} 人"
        )
        mid_nodes.append(
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id=chosen_restaurant.id,
                duration_min=dining_minutes,
                note=dining_note,
            )
        )

    # ---- 决定 preferred_start_time ----
    # 仅有用餐场景：用 chosen_time 反推（让餐厅准点开始；assemble 内会扣掉 home→餐厅 commute）
    # 其它场景：维持 depart_time（与旧实现一致）
    preferred_start = depart_time
    if has_dining and not has_main and chosen_time:
        # 估算 home→restaurant 的通勤；调用方传入的 rest_to_home 当作粗略代理（出发回家对称）
        commute_to_rest = max(0, rest_to_home or 0)
        try:
            chosen_min_int = _parse_hhmm_to_min(chosen_time)
            start_min = max(0, chosen_min_int - commute_to_rest)
            preferred_start = f"{start_min // 60:02d}:{start_min % 60:02d}"
        except (ValueError, AttributeError):
            preferred_start = depart_time

    # ---- 构造 PlanBlueprint + 调 assemble_from_blueprint ----
    blueprint = PlanBlueprint(
        nodes=mid_nodes,
        preferred_start_time=preferred_start,
        rationale="rule planner 启发式（POI/餐厅候选筛选 + 时段协商）",
    )

    # user_profile / intent fallback：rule planner 调用时已有 user_profile in scope，
    # 但 _assemble_itinerary 历史签名没暴露；此处用 load_user_profile() 兜底
    if user_profile is None:
        user_profile = load_user_profile()

    # intent 当前不被 assemble_from_blueprint 实际读取（# noqa: ARG001），
    # 但签名是必填位置参数；调用方未传时构造一个最小占位
    if intent is None:
        intent = IntentExtraction(
            start_time=depart_time,
            duration_hours=[3, 5],
            distance_max_km=5.0,
            companions=[],
            physical_constraints=[],
            dietary_constraints=[],
            experience_tags=[],
            social_context="家庭日常",
            raw_input="",
            parse_confidence=0.0,
        )

    itinerary = assemble_from_blueprint(intent, blueprint, user_profile)

    # ---- 重写 summary：小红书风格大标题（信息全 = 串联所有主要站点）----
    # 旧实现：「半日方案 · A → B；备选 POI」「轻量方案 · A（X 小时左右）」——带方案前缀、
    # 漏站（only main_poi/chosen_restaurant）。改成遍历全部主要站点的口语一句话标题，
    # 与 assemble_from_blueprint / narrator 三层口径一致（复用同一 title builder）。
    from agent.intent.narrator import build_template_title

    summary = build_template_title(intent, itinerary)
    return itinerary.model_copy(update={"summary": summary})


def _parse_hhmm_to_min(t: str) -> int:
    """形如 "14:30" → 870；不合法时抛 ValueError。"""
    if not t or ":" not in t:
        raise ValueError(f"非法时间字符串：{t!r}")
    h, m = t.split(":", 1)
    return int(h) * 60 + int(m)


def _diff_minutes(start: str, end: str) -> int:
    h1, m1 = start.split(":")
    h2, m2 = end.split(":")
    return (int(h2) - int(h1)) * 60 + (int(m2) - int(m1))
