"""agent.planning.planners.llm_planner —— PLANNER_LLM_STRATEGY=function_calling 子策略。

【真实定位】

本模块是 PLANNER_LLM_STRATEGY=function_calling 的具体实现（A/B 候选）。被以下入口消费：

- `rule_planner.plan_itinerary_with_mode`（strategy="function_calling" 分支）
- `tests/test_llm_planner.py`（4 个用例验证 function_calling 整体行为）

LLM Function Calling 自主调 Tool 的旧路径，自 LangGraph 主架构上线后仍保留作为
A/B 候选；默认 strategy 是 llm_first（详见 llm_first_planner.py）。所有 graph 主路径的
新功能改动应在 `agent/graph/` 下完成；本文件仅做 bug fix + schema 适配。

与 rule_planner.plan_itinerary 的核心差异：
- rule_planner.py：规则代码写死调用顺序（rule mode，Demo 安全网）
- llm_planner.py：LLM 看 9 个 Tool spec 自己决定调哪个（评分项 2 加分点）

实现策略：
1. 用 chat_with_tools(tools=..., tool_choice="auto") 让 LLM 自由决策
2. 主循环：LLM 返 tool_calls → 后端 invoke_tool 派发 → 把结果回灌成 role=tool 消息 → 再 chat
3. 终止条件：finish_reason=stop / 总 Tool 调用 ≥ MAX_TOTAL_TOOL_CALLS / LLM 抛错
4. **fallback**：任何异常 / 决策不收敛 → 调 plan_itinerary（规则范式）兜底
   关键：fallback 后 trace 推一条 agent_thought 提示用户「LLM 失败，已切规则」
5. 收敛后用 *规则化的* `_assemble_itinerary_from_state` 拼六段 Itinerary
   ——LLM 只决定「调哪些 Tool 拿哪些候选」，时间轴拼装由后端确定性完成
   （评分项稳定性优先；LLM 只承担「选 Tool」这件最难的事）

只读契约：
- tools.registry.all_specs()  —— OpenAI Function Calling spec
- tools.registry.invoke_tool  —— 派发器
- agent.planner.plan_itinerary —— rule fallback
- agent.planner._assemble_itinerary —— 复用六段拼装逻辑
- agent.llm_client.FunctionCallingClient —— LLM 接口

不负责：
- 规则化路径（在 planner.py）
- 用户反馈合并（在 refiner.py）
- HTTP / SSE（在 main.py，B 块）
"""

from __future__ import annotations

import json
from typing import Any

from schemas.domain import Poi, Restaurant
from schemas.errors import FailureReason
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary
from schemas.tools import (
    CheckRestaurantAvailabilityOutput,
    EstimateRouteTimeOutput,
    SearchPoisOutput,
    SearchRestaurantsOutput,
)

from ...core.llm_client import FunctionCallingClient, LLMMessage
from agent.planning.planners.rule_planner import (
    DEFAULT_DINING_TIMES,
    MAX_TOOL_CALLS_PER_KIND,
    MAX_TOTAL_TOOL_CALLS,
    PlannerResult,
    _assemble_itinerary,
    plan_itinerary,
)
from .prompts.llm_planner_prompt import LLM_PLANNER_SYSTEM_PROMPT
from ...core.trace import Tracer
from tools.registry import TOOL_REGISTRY, all_specs, invoke_tool


# ============================================================
# 配置常量
# ============================================================

# 在规划阶段允许 LLM 调用的 Tool 白名单（执行类被显式排除）
_PLANNING_TOOL_NAMES = frozenset(
    [
        "get_user_profile",
        "search_pois",
        "search_restaurants",
        "check_restaurant_availability",
        "estimate_route_time",
    ]
)

# LLM 单次会话最多 chat 轮数（防 LLM 卡在 tool_calls 无限循环）
MAX_LLM_TURNS = 8


# ============================================================
# 主入口
# ============================================================

def plan_itinerary_llm(
    intent: IntentExtraction,
    *,
    client: FunctionCallingClient,
    tracer: Tracer | None = None,
) -> PlannerResult:
    """LLM Function Calling 自主规划。失败自动 fallback 到规则范式。

    返回 PlannerResult 与 plan_itinerary 同形态，外层不需感知 mode 差异。
    """
    tracer = tracer or Tracer()
    tracer.emit("intent_parsed", payload=intent.model_dump())

    # State：累积 LLM 调过的 Tool 结果，最后用来组装 Itinerary
    state: _PlanningState = _PlanningState()

    try:
        _llm_react_loop(intent, client, tracer, state)
    except Exception as e:  # noqa: BLE001
        # LLM 异常一律 fallback
        tracer.emit(
            "agent_thought",
            {"text": f"LLM 自主规划异常（{type(e).__name__}），已切回规则规划"},
        )
        return _fallback_to_rule(intent, tracer)

    # 检查 state 是否齐三要素
    if not state.is_complete():
        tracer.emit(
            "agent_thought",
            {"text": "LLM 决策未收敛（缺主 POI / 餐厅 / 路线），已切回规则规划"},
        )
        return _fallback_to_rule(intent, tracer)

    # 用规则代码组装六段 Itinerary
    itinerary = _assemble_from_state(intent, state)
    tracer.emit("itinerary_ready", payload=itinerary.model_dump())
    return PlannerResult(success=True, itinerary=itinerary, tracer=tracer)


# ============================================================
# State：累积 Tool 结果
# ============================================================

class _PlanningState:
    """LLM 调过的 Tool 结果累积。final 用于组装 Itinerary。"""

    def __init__(self) -> None:
        self.main_poi: Poi | None = None
        self.backup_pois: list[Poi] = []
        self.chosen_restaurant: Restaurant | None = None
        self.chosen_time: str | None = None
        self.home_to_poi: int | None = None
        self.poi_to_rest: int | None = None
        self.rest_to_home: int | None = None
        self.tool_call_counts: dict[str, int] = {}
        self.total_calls: int = 0

    def is_complete(self) -> bool:
        return all(
            [
                self.main_poi is not None,
                self.chosen_restaurant is not None,
                self.chosen_time is not None,
                self.home_to_poi is not None,
            ]
        )

    def can_call(self, tool: str) -> tuple[bool, str | None]:
        if tool not in _PLANNING_TOOL_NAMES:
            return False, f"Tool {tool} 不在规划阶段白名单"
        if self.tool_call_counts.get(tool, 0) >= MAX_TOOL_CALLS_PER_KIND:
            return False, f"Tool {tool} 调用已达上限 {MAX_TOOL_CALLS_PER_KIND}"
        if self.total_calls >= MAX_TOTAL_TOOL_CALLS:
            return False, f"总 Tool 调用已达上限 {MAX_TOTAL_TOOL_CALLS}"
        return True, None

    def record(self, tool: str) -> None:
        self.tool_call_counts[tool] = self.tool_call_counts.get(tool, 0) + 1
        self.total_calls += 1


# ============================================================
# ReAct 循环
# ============================================================

def _llm_react_loop(
    intent: IntentExtraction,
    client: FunctionCallingClient,
    tracer: Tracer,
    state: _PlanningState,
) -> None:
    """LLM ↔ Tool 来回最多 MAX_LLM_TURNS 轮。

    每轮：
    1. chat_with_tools 让 LLM 决定下一步
    2. 如果返 tool_calls → 派发并把结果回灌
    3. 如果返 stop → 检查 state 是否齐全 → break
    """
    # 过滤 tool spec：只暴露规划阶段允许的 Tool
    tools_spec = [
        spec for spec in all_specs() if spec["function"]["name"] in _PLANNING_TOOL_NAMES
    ]

    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=LLM_PLANNER_SYSTEM_PROMPT),
        LLMMessage(
            role="user",
            content=(
                f"已抽取的意图约束（IntentExtraction）：\n"
                f"{intent.model_dump_json()}\n\n"
                f"请按系统提示开始自主规划。"
            ),
        ),
    ]

    for turn in range(MAX_LLM_TURNS):
        resp = client.chat_with_tools(
            messages,
            tools=tools_spec,
            temperature=0.2,
            tool_choice="auto",
        )

        # 推一条思考事件（content 不强制）
        if resp.content:
            tracer.emit("agent_thought", {"text": resp.content[:200]})

        # 没 tool_calls → LLM 决定停手
        if not resp.tool_calls:
            return

        # 把 assistant 消息（含 tool_calls）也加入 messages，否则 OpenAI 协议会丢失上下文
        messages.append(
            LLMMessage(
                role="assistant",
                content=resp.content,
                tool_calls=resp.tool_calls,
            )
        )

        # 派发每个 tool_call
        for call in resp.tool_calls:
            call_id = call.get("id") or ""
            fn = call.get("function") or {}
            tool_name = fn.get("name") or ""
            args_raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except Exception:  # noqa: BLE001
                args = {}

            allowed, deny_reason = state.can_call(tool_name)
            if not allowed:
                tool_output = {
                    "success": False,
                    "reason": "upstream_failure",
                    "detail": deny_reason,
                }
                _emit_call(tracer, tool_name, args, tool_output, duration_ms=0)
                messages.append(
                    LLMMessage(
                        role="tool",
                        tool_call_id=call_id,
                        name=tool_name,
                        content=json.dumps(tool_output, ensure_ascii=False),
                    )
                )
                continue

            state.record(tool_name)
            tracer.emit("tool_call_start", {"tool": tool_name, "input": args})
            result = invoke_tool(tool_name, args)
            tool_output = {
                "success": result.success,
                "reason": result.reason.value if result.reason else None,
                "output": result.output,
            }
            tracer.emit(
                "tool_call_end",
                {
                    "tool": tool_name,
                    "output": result.output,
                    "success": result.success,
                    "reason": result.reason.value if result.reason else None,
                    "duration_ms": result.duration_ms,
                },
            )
            _accumulate_state(state, tool_name, args, result, tracer)

            messages.append(
                LLMMessage(
                    role="tool",
                    tool_call_id=call_id,
                    name=tool_name,
                    content=json.dumps(tool_output, ensure_ascii=False),
                )
            )

        # 早停：state 齐三要素就不再让 LLM 多调
        if state.is_complete():
            return


# ============================================================
# State 累积 + 异常重规划事件
# ============================================================

def _accumulate_state(
    state: _PlanningState,
    tool_name: str,
    args: dict[str, Any],
    result: Any,
    tracer: Tracer,
) -> None:
    """从 Tool 输出抽出关键候选填进 state。"""
    if not result.success:
        # E1 触发事件
        if tool_name == "check_restaurant_availability" and result.reason == FailureReason.RESTAURANT_FULL:
            tracer.emit(
                "replan_triggered",
                {
                    "reason": FailureReason.RESTAURANT_FULL.value,
                    "from_tool": tool_name,
                    "restaurant_id": args.get("restaurant_id"),
                    "time": args.get("time"),
                    "action": "llm_self_replan",
                },
            )
        return

    if tool_name == "search_pois":
        out = SearchPoisOutput.model_validate(result.output)
        if out.candidates and state.main_poi is None:
            state.main_poi = out.candidates[0]
            state.backup_pois = list(out.candidates[1:4])
    elif tool_name == "search_restaurants":
        # 只记到 state；具体哪家由后续 check_availability 选定
        out = SearchRestaurantsOutput.model_validate(result.output)
        # 暂存第一家作为兜底；真正 chosen_restaurant 由 availability 命中决定
        if out.candidates and state.chosen_restaurant is None:
            # 不立即敲定，只是缓存第一家以备 LLM 漏调 availability 时兜底
            pass
    elif tool_name == "check_restaurant_availability":
        out = CheckRestaurantAvailabilityOutput.model_validate(result.output)
        if out.available and state.chosen_restaurant is None:
            # LLM 调 availability 时只传了 restaurant_id，要回头找 Restaurant 对象
            rest_id = args.get("restaurant_id")
            from data.loader import load_restaurants

            rest = next(
                (r for r in load_restaurants() if r.id == rest_id), None
            )
            if rest is not None:
                state.chosen_restaurant = rest
                state.chosen_time = out.time
    elif tool_name == "estimate_route_time":
        out = EstimateRouteTimeOutput.model_validate(result.output)
        if out.route is None:
            return
        minutes = (
            out.route.taxi_minutes
            or out.route.walking_minutes
            or out.route.bus_minutes
            or 15
        )
        f, t = args.get("from_location"), args.get("to_location")
        if f == "home" and state.main_poi and t == state.main_poi.id:
            state.home_to_poi = minutes
        elif (
            state.main_poi
            and state.chosen_restaurant
            and f == state.main_poi.id
            and t == state.chosen_restaurant.id
        ):
            state.poi_to_rest = minutes
        elif state.chosen_restaurant and f == state.chosen_restaurant.id and t == "home":
            state.rest_to_home = minutes


def _emit_call(
    tracer: Tracer,
    tool: str,
    args: dict[str, Any],
    output: dict[str, Any],
    *,
    duration_ms: int,
) -> None:
    tracer.emit("tool_call_start", {"tool": tool, "input": args})
    tracer.emit(
        "tool_call_end",
        {
            "tool": tool,
            "output": output,
            "success": False,
            "reason": output.get("reason"),
            "duration_ms": duration_ms,
        },
    )


# ============================================================
# Itinerary 组装（复用 planner._assemble_itinerary）
# ============================================================

def _assemble_from_state(intent: IntentExtraction, state: _PlanningState) -> Itinerary:
    party_size = sum(c.count for c in intent.companions) or 1
    # 对 LLM 漏调的路线时间走兜底（15min）
    return _assemble_itinerary(
        main_poi=state.main_poi,  # type: ignore[arg-type]
        chosen_restaurant=state.chosen_restaurant,  # type: ignore[arg-type]
        chosen_time=state.chosen_time or DEFAULT_DINING_TIMES[1],
        home_to_poi=state.home_to_poi or 15,
        poi_to_rest=state.poi_to_rest or 15,
        rest_to_home=state.rest_to_home or 15,
        party_size=party_size,
        backup_pois=state.backup_pois,
    )


# ============================================================
# Fallback
# ============================================================

def _fallback_to_rule(intent: IntentExtraction, tracer: Tracer) -> PlannerResult:
    """LLM 失败时走规则范式。沿用同一个 tracer，让前端看到完整事件链。"""
    return plan_itinerary(intent, tracer=tracer)
