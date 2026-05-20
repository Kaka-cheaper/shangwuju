"""nodes.replan —— 双层 Replan 决策节点（Plan-and-Execute 的 Optimizer 阶段）。

策略：
- 第 1-2 次违规 → llm_backprompt（回到 planner_node 让 LLM 看 critic 反馈重出蓝图）
- 第 3 次或 LLM 重试用尽 → ils_fallback（转 hybrid ILS 算法兜底；复用 planner_hybrid）
- ILS 也失败 → give_up（最终 fallback 到 rule planner，不走 LangGraph 内）

复用 backend/agent/planner_hybrid.py 的 plan_hybrid。

输入：
- state["retry_count"]
- state["plan_attempt"]
- state["intent"] / state["itinerary"]（hybrid 兜底要这俩）

输出：
- state["replan_strategy"] = "llm_backprompt" / "ils_fallback" / "give_up"
- state["retry_count"] += 1（对外可见的累计次数）
- 触发 ils_fallback 时直接把新 itinerary 写回 state["itinerary"]，绕过 planner→assemble
"""

from __future__ import annotations

from typing import Any, Optional

from agent.graph.state import AgentState, ReplanStrategy
from agent.llm_client import get_llm_client


_MAX_LLM_RETRIES = 2  # 前 2 次违规 → LLM backprompt；第 3 次 → ILS


def replan_router_node(state: AgentState) -> dict[str, Any]:
    retry_count = (state.get("retry_count") or 0) + 1
    strategy: ReplanStrategy

    if retry_count <= _MAX_LLM_RETRIES:
        strategy = "llm_backprompt"
    else:
        strategy = "ils_fallback"

    return {
        "retry_count": retry_count,
        "replan_strategy": strategy,
    }


def route_after_replan(state: AgentState) -> str:
    """conditional edge：按 strategy 决定下一节点。"""
    s = state.get("replan_strategy")
    if s == "llm_backprompt":
        return "planner"  # 回到 planner，带着 critic_feedback_text
    if s == "ils_fallback":
        return "ils_replan"
    return "narrate"  # give_up：用当前（不完美）方案继续走流程


def ils_replan_node(state: AgentState) -> dict[str, Any]:
    """转 hybrid ILS 算法兜底。复用 plan_hybrid + rule_assembler。

    成功 → 写回 itinerary，has_critical=False
    失败 → has_critical 仍 True，让流程降级（最终走 narrate 出当前方案，不阻塞 demo）
    """
    intent = state.get("intent")
    if intent is None:
        return {"replan_strategy": "give_up", "has_critical": False}

    try:
        # 延迟 import 防循环依赖
        from agent.planner import _assemble_itinerary as rule_assembler
        from agent.planner_hybrid import plan_hybrid
    except ImportError:
        return {"replan_strategy": "give_up", "has_critical": False}

    client = get_llm_client()

    # plan_hybrid 内部跑 ILS + critic；返回 HybridResult
    try:
        result = plan_hybrid(
            intent,
            client=client,
            tracer=None,  # graph 自有追踪（trace_span / sse_adapter）
            rule_assembler=_RULE_ASSEMBLER_ADAPTER,
        )
    except Exception:  # noqa: BLE001
        return {"replan_strategy": "give_up", "has_critical": False}

    if result.success and result.itinerary is not None:
        return {
            "itinerary": result.itinerary,
            "has_critical": False,
            "violations": [],
            "critic_feedback_text": None,
        }

    return {
        "replan_strategy": "give_up",
        "has_critical": False,  # 不再阻塞，让流程继续到 narrate
    }


def _RULE_ASSEMBLER_ADAPTER(intent: Any, candidate: Any, tracer: Any) -> Optional[Any]:
    """planner_hybrid.plan_hybrid 期待的 rule_assembler 签名（intent, CandidatePlan, tracer）。

    现在 ils_replan 兜底直接复用 rule planner 的 _assemble_itinerary 同结构 helper。
    candidate 来自 planner_hybrid.CandidatePlan（含 main_poi / restaurant / dining_time）。
    """
    try:
        from agent.planner import _assemble_itinerary

        return _assemble_itinerary(
            intent=intent,
            main_poi=candidate.main_poi,
            chosen_restaurant=candidate.restaurant,
            dining_time=candidate.dining_time,
            backup_pois=candidate.backup_pois,
            tracer=tracer,
        )
    except Exception:  # noqa: BLE001
        return None
