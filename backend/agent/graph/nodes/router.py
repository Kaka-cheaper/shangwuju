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

from agent.core.feedback_detector import looks_like_feedback, looks_like_feedback_strong
from agent.graph.state import AgentState, RouteKind
from agent.intent.router import classify_input, fallback_decision
from agent.core.llm_client import get_llm_client


def _looks_like_feedback_strong(state: AgentState) -> bool:
    """Layer 1 强信号：has_itinerary + 命中强信号子集（不误吞新需求）。

    用 looks_like_feedback_strong（强信号子集），区别于全集 looks_like_feedback——
    强信号词（太远 / 太赶 / 数字单位 / 以内）几乎不可能是新需求开头，命中即可
    直接判 feedback 不调 LLM；弱信号词（换 / 改）交 Layer 2 LLM 区分。
    """
    if not state.get("itinerary"):
        return False
    txt = (state.get("user_input") or "").strip()
    return looks_like_feedback_strong(txt)


def router_node(state: AgentState) -> dict[str, Any]:
    """同步节点。LLM 分类（异常时启发式兜底）。

    三层防御（spec feedback-routing-fix 重构）：
        Layer 1（强信号，不调 LLM）：has_itinerary + looks_like_feedback_strong
                  → feedback。强信号词（太远 / 太赶 / 数字单位 / 以内）几乎不可能是
                  新需求开头，直接判 feedback。弱信号词（换 / 改）不在强信号子集，下沉到 Layer 2。
        Layer 2（LLM 分类，带上下文）：classify_input(has_itinerary=...)
                  → has_itinerary 时 prompt 告知 LLM「用户已有方案」，使其能判反馈
                  （多判 ambiguous）；明确新需求仍判 planning。
        Layer 3（兜底，放宽）：has_itinerary + LLM 判非 planning → feedback。
                  去掉旧的「<15字」限制（长反馈不再漏）；planning 明确时不吞（R4 防误伤）。

    无 itinerary 的 session：全程不进任何新分支，行为与重构前一致（R6.4）。
    """
    user_input = state.get("user_input") or ""
    has_itinerary = bool(state.get("itinerary"))

    # ---- Layer 1：强信号启发式（has_itinerary + 强信号子集） ----
    if _looks_like_feedback_strong(state):
        return {
            "route_kind": "feedback",
            "router_decision": None,  # refiner 不需要 RouterDecision
        }

    # ---- Layer 2：LLM 分类（带 has_itinerary 上下文） ----
    client = get_llm_client()
    try:
        decision = classify_input(
            user_input, client=client, has_itinerary=has_itinerary
        )
    except Exception:  # noqa: BLE001
        decision = fallback_decision(user_input)

    # router 的 input_kind 与 RouteKind 字段名一致
    route_kind: RouteKind = decision.input_kind  # type: ignore[assignment]

    # ---- Layer 3：兜底（放宽：has_itinerary 且 LLM 判非 planning → feedback） ----
    # 设计动机：已有方案的上下文里，LLM 判 ambiguous/chitchat/emotional/meta/off_topic
    # 的输入更可能是反馈（用户在追加调整，不是闲聊）。去掉旧的 <15字 限制让长反馈也命中。
    # R4 防误伤：LLM 判 planning（明确新需求）不进此分支，仍走规划主路径。
    if has_itinerary and route_kind != "planning":
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
