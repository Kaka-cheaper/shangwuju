"""nodes.router —— LangGraph 输入域路由节点。

复用 backend/agent/router.py 的 LLM 6 类分类逻辑。

输入：state["user_input"]
输出：state.update({"router_decision": ..., "route_kind": ...})

路由结果决定下一节点：
- planning  → intent_node → planner → ...
- feedback  → refiner_node（前提：state.intent / state.itinerary 已存在）
- 其他 5 类 → chitchat_node
"""

from __future__ import annotations

from typing import Any

from agent.feedback_detector import looks_like_feedback
from agent.graph.state import AgentState, RouteKind
from agent.router import classify_input, fallback_decision
from agent.llm_client import get_llm_client


def _looks_like_feedback(state: AgentState) -> bool:
    """启发式：用户输入像「对已有方案的反馈」。

    判据：
    - 已有 itinerary（说明上一轮规划过）
    - 输入命中 agent.feedback_detector.looks_like_feedback（关键词 + 数字单位 + "以内"）
    """
    if not state.get("itinerary"):
        return False
    txt = (state.get("user_input") or "").strip()
    return looks_like_feedback(txt)


def router_node(state: AgentState) -> dict[str, Any]:
    """同步节点。LLM 分类（异常时启发式兜底）。

    多层防御策略（避免短反馈被误判为新需求）：
        Layer 1（强信号启发式）：has_itinerary + 命中 feedback_detector 关键词 → 直接 feedback
        Layer 2（LLM 分类）：调 agent.router.classify_input
        Layer 3（弱信号兜底）：has_itinerary + 输入 <15 字 + LLM 判非 feedback 类
                              → 改判 feedback（用户的短输入有上下文，必是反馈）

    Layer 3 的设计动机（problem.md 问题再现）：
        用户输入「一个小时以内」，LLM router 看不到上一轮 itinerary，可能判 ambiguous /
        planning（如不带数字的「再近一点」），导致路由到 chitchat 推暖心气泡，丢失上下文。
        Layer 3 用「已有方案」+「短输入」推断这必是反馈。
    """
    user_input = state.get("user_input") or ""
    has_itinerary = bool(state.get("itinerary"))

    # ---- Layer 1：强信号启发式（关键词 / 数字单位 / 「以内」短句） ----
    if _looks_like_feedback(state):
        return {
            "route_kind": "feedback",
            "router_decision": None,  # refiner 不需要 RouterDecision
        }

    # ---- Layer 2：LLM 分类 ----
    client = get_llm_client()
    try:
        decision = classify_input(user_input, client=client)
    except Exception:  # noqa: BLE001
        decision = fallback_decision(user_input)

    # router 的 input_kind 与 RouteKind 字段名一致
    route_kind: RouteKind = decision.input_kind  # type: ignore[assignment]

    # ---- Layer 3：弱信号兜底 ----
    # 已有方案 + 短输入（< 15 字）+ LLM 没判 planning 主路径 → 倾向 feedback
    # 排除 LLM 判 planning 的情况：用户明确发起新需求（即使有上一轮 itinerary）
    if (
        has_itinerary
        and len(user_input.strip()) < 15
        and route_kind in ("ambiguous", "chitchat")
    ):
        return {
            "route_kind": "feedback",
            "router_decision": None,
        }

    return {
        "router_decision": decision,
        "route_kind": route_kind,
    }


def route_after_router(state: AgentState) -> str:
    """conditional edge 函数。返回下一节点名。"""
    kind = state.get("route_kind")
    if kind == "planning":
        return "intent"
    if kind == "feedback":
        return "refiner"
    # chitchat / meta / emotional / off_topic / ambiguous
    return "chitchat"
