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
        Layer 3（兜底，仅 ambiguous）：has_itinerary + LLM 判 ambiguous → feedback。
                  classify_input 在 has_itinerary 时注入 FEEDBACK_CONTEXT_HINT，引导 LLM
                  把真反馈措辞判为 ambiguous；故只接管 ambiguous。chitchat/meta/emotional/
                  off_topic 保持各自语义走 chitchat 气泡（修复「你好」被误判反馈重规划的 bug）。

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

    # ---- Layer 3：兜底（has_itinerary 且 LLM 判 ambiguous → feedback） ----
    # 设计动机：已有方案的上下文里，classify_input 注入了 FEEDBACK_CONTEXT_HINT，
    # 它明确引导 LLM 把「真反馈措辞」（太赶/想轻松点/换个活动/不太好）判为 ambiguous。
    # 所以 Layer 3 只接管 ambiguous——这是真反馈落的桶。
    #
    # 【修复用户观察的 bug】旧实现是 `route_kind != "planning"` 一律转 feedback，
    # 把 chitchat（你好）/ meta（你能做什么）/ off_topic（无关话题）也吞成 feedback
    # 触发重规划——明显错误：这些是有明确社交语义的输入，应保持闲聊气泡。
    # emotional（情绪表达）同理保持共情闲聊，不强转反馈。
    # R4 防误伤：planning（明确新需求）走规划主路径；R1：长反馈靠 LLM 判 ambiguous 命中此分支。
    if has_itinerary and route_kind == "ambiguous":
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
