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


_MAX_LLM_RETRIES = 2     # 前 2 次违规 → LLM backprompt；第 3 次 → ILS
_MAX_TOTAL_RETRIES = 4   # 总重试上限（防 LangGraph 25 步硬限触发前自然停）


def replan_router_node(state: AgentState) -> dict[str, Any]:
    """决定下一次重排策略。

    硬上限（防死循环，P1 2026-05-23）：
        retry_count > _MAX_TOTAL_RETRIES → give_up，不再尝试。
        即使 build.py 的 _route_after_ils 已经把 ILS → narrate 切断了循环，
        这里再加一层兜底：万一未来重新接回 critic，retry_count 也会硬刹停。
    """
    retry_count = (state.get("retry_count") or 0) + 1
    strategy: ReplanStrategy

    if retry_count > _MAX_TOTAL_RETRIES:
        strategy = "give_up"
    elif retry_count <= _MAX_LLM_RETRIES:
        strategy = "llm_backprompt"
    else:
        strategy = "ils_fallback"

    # Step 8：累积 fallback_chain 一跳
    from schemas.decision_trace import FallbackHop

    chain = list(state.get("fallback_chain") or [])
    if strategy == "llm_backprompt":
        hop = FallbackHop(
            from_stage="llm_first" if retry_count == 1 else "llm_backprompt",
            to_stage="llm_backprompt",
            reason=f"critic 命中违规，第 {retry_count} 次让 LLM 修正重出蓝图",
        )
    elif strategy == "ils_fallback":
        hop = FallbackHop(
            from_stage="llm_backprompt",
            to_stage="ils",
            reason=f"LLM {_MAX_LLM_RETRIES} 次仍未通过 critic，切 ILS 算法兜底",
        )
    else:  # give_up
        hop = FallbackHop(
            from_stage="ils",
            to_stage="give_up",
            reason=f"重排已达 {_MAX_TOTAL_RETRIES} 次上限，保留当前方案",
        )
    chain.append(hop.model_dump())

    return {
        "retry_count": retry_count,
        "replan_strategy": strategy,
        "fallback_chain": chain,
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
    失败 → 走 rule planner 兜底；仍失败 → give_up（has_critical=False 让流程走 narrate）
    """
    from schemas.decision_trace import FallbackHop

    intent = state.get("intent")
    if intent is None:
        return {"replan_strategy": "give_up", "has_critical": False}

    chain = list(state.get("fallback_chain") or [])

    # ---- 先尝试 ILS（仅 5 段完整场景适用）----
    ils_success = False
    try:
        from agent.segment_decider import FULL_SEGMENTS, decide_segments
        from agent.planner_hybrid import plan_hybrid

        segments = decide_segments(intent)
        if segments == FULL_SEGMENTS:
            # 5 段场景：走 ILS
            from agent.planner import _assemble_itinerary as rule_assembler
            client = get_llm_client()
            result = plan_hybrid(
                intent,
                client=client,
                tracer=None,
                rule_assembler=_RULE_ASSEMBLER_ADAPTER,
            )
            if result.success and result.itinerary is not None:
                return {
                    "itinerary": result.itinerary,
                    "has_critical": False,
                    "violations": [],
                    "critic_feedback_text": None,
                }
        # 削段场景：ILS 不适用，跳到 rule planner 兜底
    except Exception:  # noqa: BLE001
        pass

    # ---- ILS 失败或不适用 → rule planner 兜底 ----
    chain.append(
        FallbackHop(
            from_stage="ils",
            to_stage="rule",
            reason="ILS 不适用或未给出有效方案，回 rule planner 兜底",
        ).model_dump()
    )
    try:
        from agent.planner import plan_itinerary
        from agent.trace import Tracer

        tracer = Tracer()
        rule_result = plan_itinerary(intent, tracer=tracer)
        if rule_result.success and rule_result.itinerary is not None:
            return {
                "itinerary": rule_result.itinerary,
                "has_critical": False,
                "violations": [],
                "critic_feedback_text": None,
                "replan_strategy": "give_up",  # 标记已用完所有策略
                "fallback_chain": chain,
            }
    except Exception:  # noqa: BLE001
        pass

    # ---- 全部失败 → give_up，不再循环 ----
    chain.append(
        FallbackHop(
            from_stage="rule",
            to_stage="give_up",
            reason="rule planner 也未能产出方案，停止重试",
        ).model_dump()
    )
    return {
        "replan_strategy": "give_up",
        "has_critical": False,
        "fallback_chain": chain,
    }


def _RULE_ASSEMBLER_ADAPTER(intent: Any, candidate: Any, tracer: Any) -> Optional[Any]:
    """planner_hybrid.plan_hybrid 期待的 rule_assembler 签名（intent, CandidatePlan, tracer）。

    适配 CandidatePlan 的可选字段（main_poi / restaurant 可能为 None）。
    """
    try:
        from agent.planner import plan_itinerary
        from agent.trace import Tracer

        # 直接用 rule planner 跑完整流程（它内部会根据 segment_decider 决定段集合）
        t = tracer if isinstance(tracer, Tracer) else Tracer()
        result = plan_itinerary(intent, tracer=t)
        if result.success and result.itinerary:
            return result.itinerary
        return None
    except Exception:  # noqa: BLE001
        return None
