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

from agent.graph.state import AgentState, RouteKind
from agent.router import classify_input, fallback_decision
from agent.llm_client import get_llm_client


def _looks_like_feedback(state: AgentState) -> bool:
    """启发式：用户输入像「对已有方案的反馈」。

    判据：
    - 已有 itinerary（说明上一轮规划过）
    - 输入含反馈关键词（"太远 / 不要 / 换 / 改 / X 公里 / 时间 / 便宜" 等）
    """
    if not state.get("itinerary"):
        return False
    txt = (state.get("user_input") or "").strip()
    if not txt:
        return False
    feedback_kw = (
        "太远", "近一点", "近点", "别走太远", "再近",
        "不要", "去掉", "换一个", "换", "改一下", "再想想",
        "不喜欢", "不太行", "不行", "不合适",
        "便宜", "贵", "再贵点", "更高级",
        "公里以内", "km以内", "公里内", "km内",
        "改成", "改为", "调到", "缩短", "延长",
        "早点", "晚点", "提前", "推迟",
    )
    return any(kw in txt for kw in feedback_kw)


def router_node(state: AgentState) -> dict[str, Any]:
    """同步节点。LLM 分类（异常时启发式兜底）。"""
    user_input = state.get("user_input") or ""

    # 反馈启发式优先：已有 itinerary + 含反馈关键词 → 直接路由 feedback
    if _looks_like_feedback(state):
        return {
            "route_kind": "feedback",
            "router_decision": None,  # refiner 不需要 RouterDecision
        }

    # 走 LLM 分类
    client = get_llm_client()
    try:
        decision = classify_input(user_input, client=client)
    except Exception:  # noqa: BLE001
        decision = fallback_decision(user_input)

    # router 的 input_kind 与 RouteKind 字段名一致
    route_kind: RouteKind = decision.input_kind  # type: ignore[assignment]

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
