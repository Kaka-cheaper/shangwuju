"""agent.graph._resilience —— 图节点【非预期】异常的输出降级阶梯（D2 safety net）。

问题命名（prior art）：graceful degradation / output degradation ladder。
LangGraph 节点是同步 `node(state) -> dict`，路由靠条件边而非异常。今天一个节点里
【没预料到】的异常会冒泡到 sse_adapter 的 try/except，变成裸 STREAM_ERROR +
DONE(has_itinerary=False)——用户看到一轮崩掉、没方案。本模块提供一个 `drain_on_error`
装饰器，在「注册时」就给节点挂上降级策略，把每一轮都落到「输出降级阶梯」上：

    planner / assemble / critic / replan 异常 → 规则地板方案（rule floor）
    finalize_plan 异常                        → 原样透传现有 itinerary（体感编排批 P1）
    narrate 异常                              → 推已通过的方案、跳过文案
    search worker 异常                        → 空候选继续

同时【绝不静默】（degrade, don't go silent）：原始异常的完整 traceback 仍 loudly 落日志。

关键纪律——LangGraph 控制流异常必须原样 re-raise：
    GraphBubbleUp 及其子类（GraphInterrupt / ParentCommand …）不是「错误」，而是
    LangGraph 用来表达 HITL interrupt / 子图命令上浮的控制流信号。把它们当普通错误
    吞掉转规则地板，会破坏 interrupt / Command 控制流。所以装饰器先于 `except Exception`
    把它们 re-raise。

地板也失败时不再兜底（honest error）：
    rule_floor 调 plan_itinerary 本身抛 → 让它 propagate（→ 诚实 STREAM_ERROR）。
    rule_floor 调 plan_itinerary 返回 success=False → re-raise 原始异常（不伪造方案）。
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable

# LangGraph 控制流异常：GraphBubbleUp 是基类，GraphInterrupt / ParentCommand 都继承它
# （已用 installed 版本的 __mro__ 核实）。显式列全，self-documenting 且对未来版本中
# 「某个控制流异常不再继承 GraphBubbleUp」的情况更鲁棒。
from langgraph.errors import GraphBubbleUp, GraphInterrupt, ParentCommand

_LOG = logging.getLogger("agent.graph.resilience")

# 控制流异常元组：捕到这些 → 原样 re-raise（绝不降级）
_CONTROL_EXCEPTIONS: tuple[type[BaseException], ...] = (
    GraphBubbleUp,
    GraphInterrupt,
    ParentCommand,
)

# "empty" 策略：每个搜索 worker 的「空结果」state-delta（与 worker 的 no-intent 分支同形）。
# 键用 worker 函数名——把降级形状的真相留在这里集中维护，emit_fanout_worker 依赖
# diff 里的 pois / restaurants / user_profile 键来合成 tool_call_end 的数量摘要。
_EMPTY_WORKER_SHAPES: dict[str, dict[str, Any]] = {
    "search_pois_worker": {"pois": [], "pois_relaxed_tags": []},
    "search_restaurants_worker": {"restaurants": [], "restaurants_relaxed_tags": []},
    "get_user_profile_worker": {"user_profile": None},
}


def drain_on_error(
    node_fn: Callable[[dict[str, Any]], dict[str, Any]],
    strategy: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """给同步图节点挂「非预期异常 → 降级 delta」的安全网。

    Args:
        node_fn: 原始节点函数 `node(state) -> dict`。
        strategy: 降级策略名，见模块 docstring。
            "rule_floor" / "emit_plan" / "empty" / "passthrough"。

    Returns:
        包装后的节点函数：正常时透传 node_fn 的返回；非预期异常时按 strategy 返回恢复 delta。
        控制流异常 / 地板自身失败 → 原样向上抛。
    """

    @functools.wraps(node_fn)
    def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        try:
            return node_fn(state)
        except _CONTROL_EXCEPTIONS:
            # LangGraph 控制流信号（interrupt / command）——绝不当错误吞，原样上浮
            raise
        except Exception:  # noqa: BLE001 —— 这正是 D2 要兜的「非预期」异常
            node_name = getattr(node_fn, "__name__", repr(node_fn))
            # loudly 落完整 traceback（.exception 自带 exc_info）——降级但不静默
            _LOG.exception(
                "node %r raised an unexpected exception; draining via strategy=%r",
                node_name,
                strategy,
            )
            delta = _recover(strategy, node_name, state)
            if delta is None:
                # 地板也无能为力（如 rule floor 返回 success=False）→ 不伪造方案，
                # 原样 re-raise 让它落到诚实 STREAM_ERROR。
                raise
            return delta

    return wrapped


def _recover(
    strategy: str, node_name: str, state: dict[str, Any]
) -> dict[str, Any] | None:
    """按 strategy 产出恢复 state-delta；返回 None 表示「无法恢复，应 re-raise 原始异常」。"""
    if strategy == "rule_floor":
        return _recover_rule_floor(state)
    if strategy == "emit_plan":
        # critic 已通过的方案照样推出，跳过文案（narration=None）
        return {"itinerary": state.get("itinerary"), "narration": None}
    if strategy == "empty":
        # 搜索 worker 降级为空候选继续（形状按 worker 名取）
        return dict(_EMPTY_WORKER_SHAPES.get(node_name, {}))
    if strategy == "passthrough":
        # finalize_plan 异常 → 原样透传现有 itinerary，跳过规则标题/pending_actions/
        # decision_trace 收尾这几项"锦上添花"的加工——graph 仍能正常流到 narrate，
        # emit_finalize_plan 照样能用这份（未加工的）itinerary 推 ITINERARY_READY，
        # 不因 finalize_plan 内部一个非预期 bug 就让用户裸看 STREAM_ERROR。
        return {"itinerary": state.get("itinerary")}
    raise ValueError(f"unknown drain strategy: {strategy!r}")


def _recover_rule_floor(state: dict[str, Any]) -> dict[str, Any] | None:
    """规则地板恢复：用纯规则 planner 直接出一个方案。

    返回的 delta 复用 critic 的 rule-mode 短路（planner_mode="rule" + has_critical=False），
    让现有条件边把它一路带到 narrate，**不**再触发 llm_backprompt 重排阶梯。

    plan_itinerary 本身抛 → 不捕获，让它 propagate（→ 诚实 STREAM_ERROR，test #7）。
    plan_itinerary 返回 success=False → 返回 None（上层 re-raise 原始异常，不伪造方案）。
    """
    from agent.planning.planners.rule_planner import plan_itinerary

    r = plan_itinerary(state["intent"])  # 抛则 propagate —— 地板也失败时诚实暴露
    if r.success and r.itinerary is not None:
        return {
            "itinerary": r.itinerary,
            "planner_mode": "rule",
            "blueprint": None,
            "has_critical": False,
            "violations": [],
        }
    return None
