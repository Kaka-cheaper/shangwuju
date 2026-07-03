"""nodes.router —— LangGraph 输入域路由节点（V3 adapter）。

V3 退薄：业务级联已迁至 agent.routing.route_turn（T2）。
本模块只做 graph adapter：把 AgentState 展平 → 调 route_turn → 展平回 dict。

route_after_router 不动。

测试 monkeypatch 兼容性说明：
  现有测试通过 monkeypatch.setattr(router_mod, "classify_input", ...) 和
  monkeypatch.setattr(router_mod, "get_llm_client", ...) 注入 stub LLM。
  为保兼容，两个名字保留在本模块命名空间，router_node 调用它们并经由
  classify_fn 参数传入 route_turn，使 stub 仍能生效。

【ADR-0011 前置核实①：会话日志基础设施，E-2 第一块砖】
本节点是"轮次日志"（messages 通道，见 agent/graph/state.py 字段注释）的唯一
写入点——router_node 每轮无条件跑一次（route_kind/router_decision 的天然
唯一写手，TURN_SCOPED），天然是记录"用户这轮说了什么 + agent 这轮气泡回了
什么"的单一时机：chitchat 类 decision 的 reply_text 本节点已经算出，不必在
chitchat_node 再重复读一遍 router_decision 才能补写日志（chitchat_node 不在
本次任务书范围内，也没有必要为了写日志新增一个读者）。narrate_node 是
planning/feedback 轮"agent 侧真正说了什么"的记录者（叙事文案），两者合起来
覆盖全部路由分支，不重不漏。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from agent.graph.state import AgentState, RouteKind  # noqa: F401 RouteKind re-exported for any importers
from agent.core.injection_detector import detect_injection
from agent.intent.router import classify_input
from agent.core.llm_client import get_llm_client
from agent.routing.route_turn import route_turn

# 【护栏2·消毒纪律】壳1 拦截（注入）的轮次不把攻击原文回灌会话日志——占位
# 文本本身不含任何用户输入片段（同 route_turn._safe_refusal_decision 的
# R4.2 纪律：拒绝文案不回显攻击内容）。消毒在写入时做（此刻壳1 verdict 已知，
# ADR-0011 护栏4 原文）。
_INJECTION_LOG_PLACEHOLDER = "[该输入因安全原因被拦截]"

# 【护栏·超长粘贴截断】会话日志是 E-2 打包器的原始素材，单条消息本身也要有
# 边界防御——上限与 `ChatStreamRequest.message` 的 HTTP 层 `max_length=500`
# （api/_streams/models.py）取同一个数，不是另起一个随意挑的常量：HTTP 校验
# 已经挡住大多数越界输入，这里是"写入会话日志"这一步的第二道防线（未来其他
# 调用方——如协作房间的消息处理器——不一定都经过那层 HTTP 校验）。
_MAX_LOGGED_INPUT_CHARS = 500
_TRUNCATION_MARK = "……[超长粘贴已截断]"


def _sanitize_for_log(raw_input: str) -> str:
    """写入 messages 前的截断消毒（不含注入判定——那是 injection_blocked 分支）。"""
    text = raw_input or ""
    if len(text) <= _MAX_LOGGED_INPUT_CHARS:
        return text
    return text[:_MAX_LOGGED_INPUT_CHARS] + _TRUNCATION_MARK


def router_node(state: AgentState) -> dict[str, Any]:
    """V3 adapter：展平 state → route_turn → 展平 RouteOutcome 为 dict。"""
    raw_input = state.get("user_input") or ""
    outcome = route_turn(
        utterance=raw_input,
        itinerary=state.get("itinerary"),
        user_id=state.get("user_id"),
        client=get_llm_client(),
        classify_fn=classify_input,
    )

    result: dict[str, Any] = {"route_kind": outcome.kind, "router_decision": outcome.decision}

    # ---- 会话日志（messages 通道，SESSION_SCOPED，add_messages 现成 reducer）----
    # 是否命中壳1 与 route_turn.py Layer 0 内部判据同一条件（is_injection and
    # severity=="high"）——本节点范围纪律限定不改 route_turn.py（并行批次 c′
    # 正改的相邻文件之外的边界；那批不含 route_turn.py，但本次任务书明确划定
    # "你的地盘"只有 graph/nodes(router/narrate/finalize_plan) + state.py +
    # graph_confirm.py + 测试，route_turn.py 不在其中），代价是这条件在两处
    # 各写一份；detect_injection 零 LLM、纯正则、无副作用，重算一次成本可
    # 忽略，不是这里的权衡重点（详见任务报告"拍板点"）。
    verdict = detect_injection(raw_input)
    injection_blocked = verdict.is_injection and verdict.severity == "high"
    logged_text = (
        _INJECTION_LOG_PLACEHOLDER if injection_blocked else _sanitize_for_log(raw_input)
    )
    new_messages: list[Any] = [HumanMessage(content=logged_text)]

    # 气泡回复（chitchat 类 decision 的 reply_text）同轮写 AIMessage——排除
    # planning/feedback：这两类的 decision.reply_text（如有）是给"降级路径"用
    # 的内部占位文案（如 make_planning_decision 走 canonical 短路时的固定
    # 文案），从不展示给用户（chitchat_node 只在其余 route_kind 上被路由
    # 到，见 route_after_router），写进日志反而会记录一句用户根本没看到的
    # "假发言"。narrate_node 才是 planning/feedback 轮真正"agent 说了什么"
    # 的记录者（叙事文案，见 nodes/narrate.py）。
    if outcome.kind not in ("planning", "feedback") and outcome.decision is not None:
        new_messages.append(AIMessage(content=outcome.decision.reply_text))

    result["messages"] = new_messages
    return result


def route_after_router(state: AgentState) -> str:
    """conditional edge 函数。返回下一节点名。"""
    kind = state.get("route_kind")
    if kind == "planning":
        return "intent"
    if kind == "feedback":
        return "refiner"
    # chitchat / meta / emotional / off_topic / ambiguous
    return "chitchat"
