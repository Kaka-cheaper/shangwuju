"""nodes.router —— LangGraph 输入域路由节点（V3 adapter）。

V3 退薄：业务级联已迁至 agent.routing.route_turn（T2）。
本模块只做 graph adapter：把 AgentState 展平 → 调 route_turn → 展平回 dict。

route_after_router 不动。

测试 monkeypatch 兼容性说明：
  现有测试通过 monkeypatch.setattr(router_mod, "classify_input", ...) 和
  monkeypatch.setattr(router_mod, "get_llm_client", ...) 注入 stub LLM。
  为保兼容，两个名字保留在本模块命名空间，router_node 调用它们并经由
  classify_fn 参数传入 route_turn，使 stub 仍能生效。
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState, RouteKind  # noqa: F401 RouteKind re-exported for any importers
from agent.intent.router import classify_input
from agent.core.llm_client import get_llm_client
from agent.routing.route_turn import route_turn


def router_node(state: AgentState) -> dict[str, Any]:
    """V3 adapter：展平 state → route_turn → 展平 RouteOutcome 为 dict。"""
    outcome = route_turn(
        utterance=state.get("user_input") or "",
        itinerary=state.get("itinerary"),
        user_id=state.get("user_id"),
        client=get_llm_client(),
        classify_fn=classify_input,
    )
    return {"route_kind": outcome.kind, "router_decision": outcome.decision}


def route_after_router(state: AgentState) -> str:
    """conditional edge 函数。返回下一节点名。"""
    kind = state.get("route_kind")
    if kind == "planning":
        return "intent"
    if kind == "feedback":
        return "refiner"
    # chitchat / meta / emotional / off_topic / ambiguous
    return "chitchat"
