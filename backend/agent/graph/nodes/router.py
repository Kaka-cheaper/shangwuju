"""nodes.router —— LangGraph 输入域路由节点（V3 adapter；ADR-0011 E-2-c 更新）。

V3 退薄：业务级联已迁至 agent.routing.route_turn。
本模块只做 graph adapter：把 AgentState 展平 → 调 route_turn → 展平回 dict。

route_after_router 不动（ADR-0011 前置核实②核实：本函数只显式判
"planning"/"feedback"，其余 catch-all 送 chitchat，6→7 塌缩对本函数零改动）。

测试 monkeypatch 兼容性说明：
  现有测试通过 monkeypatch.setattr(router_mod, "classify_turn", ...) 和
  monkeypatch.setattr(router_mod, "get_llm_client", ...) 注入 stub 判定。
  为保兼容，两个名字保留在本模块命名空间，router_node 调用它们并经由
  classify_fn 参数传入 route_turn，使 stub 仍能生效（E-2-c 前是
  `classify_input`，塌缩为统一脑子后改为 `classify_turn`，签名也随之改变：
  `classify_turn(context_text, user_input, has_itinerary, *, client) -> RouteJudgment | None`）。

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
from agent.context.sources import GraphStateSource
from agent.routing.brain import classify_turn
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
        context_source=GraphStateSource(state),
        classify_fn=classify_turn,
        # 读写分离批：session_id 是累积记忆的键（persona_qa 的会话私有偏好），
        # user_id 只再承担画像模板（共享只读）。
        session_id=state.get("session_id"),
        # 点火前小修批 任务 3：Layer 1.8 QA 的方案级答复器材料——intent 供
        # why_rationale 组「实体×意图命中」句；node_actions（narrate 写入图
        # 状态的 EPISODE_SCOPED 字段）供 alternatives 报预验证具名备选，惰性
        # 闭包只在该字段命中时被调用。
        intent=state.get("intent"),
        node_actions_provider=lambda: state.get("node_actions") or {},
    )

    result: dict[str, Any] = {"route_kind": outcome.kind, "router_decision": outcome.decision}

    # ---- 会话日志（messages 通道，SESSION_SCOPED，add_messages 现成 reducer）----
    # 是否命中壳1 直接读 `outcome.injection_blocked`（ADR-0011 E-2-c 新增字段）——
    # 不再重新调用一次 `detect_injection`。E-2-a 那批 route_turn.py 不在改动
    # 范围内，只能在这里重算一遍；E-2-c 把 route_turn.py 纳入范围后，让
    # route_turn 把 Layer 0 判定结果通过 RouteOutcome 原样带出来，两处重复判定
    # 收敛成一处（壳1 判一次，其余消费方读字段，不二次调用）。
    logged_text = (
        _INJECTION_LOG_PLACEHOLDER
        if outcome.injection_blocked
        else _sanitize_for_log(raw_input)
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
    # chitchat / confirm / clarify / defense（ADR-0011 6 标签闭集里除
    # planning/feedback 外的其余 4 类，catch-all 送 chitchat 节点渲染气泡）
    return "chitchat"
