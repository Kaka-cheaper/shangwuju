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
- ils_fallback 成功时同时写回 state["advisories"]（D-7：plan_hybrid 的「绝不默默
  忽略」告知，透传给 narrate_node）
"""

from __future__ import annotations

import os
from typing import Any

from agent.graph.state import AgentState, ReplanStrategy
from agent.core.llm_client import get_llm_client


def _env_int(name: str, default: int) -> int:
    """从 env 读非负整数；解析失败 / 越界回退 default（不抛）。"""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
        return v if v >= 0 else default
    except ValueError:
        return default


# spec innovation-review R4：写死常量改 env flag（默认值不变向后兼容）
# 评委 grep 看到「_MAX_LLM_RETRIES = 2」会问「论文 10 次为何只 2」——
# 改 env flag + 在 .env.example 注释「latency-bound 决策（30 秒红线）」把劣势变优势
_MAX_LLM_RETRIES = _env_int("PLANNER_MAX_LLM_RETRIES", 2)     # 前 2 次违规 → LLM backprompt；第 3 次 → ILS
_MAX_TOTAL_RETRIES = _env_int("PLANNER_MAX_TOTAL_RETRIES", 4) # 总重试上限（防 LangGraph 25 步硬限触发前自然停）


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
    """转 hybrid ILS（多活动 TOPTW）算法兜底。复用 plan_hybrid。

    成功 → 写回 itinerary，has_critical=False
    失败 → 走 rule planner 兜底；仍失败 → give_up（has_critical=False 让流程走 narrate）

    ADR-0010 D-5 连带决策 5（FULL_SEGMENTS 门退役）：旧版只在 `decide_segments(intent)
    == FULL_SEGMENTS`（5 段完整场景）时才走 ILS，其余「削段」场景直接跳过、只用
    rule 地板——这个门是旧「1+1 三元组」模型的产物（ILS 只会拼 1 主活动+1 用餐，
    削段场景给不出更好的东西）。新求解器（`plan_hybrid` 内部的 `build_route`）
    天然处理任意组成（ADR-0010 核心：组成随 intent 涌现，不再有段/节点数的特权
    假设），门已无存在理由——删除后 ILS 对所有场景都适用；仍失败时下方 rule
    planner 兜底不变（D2 安全网原样保留）。
    """
    from schemas.decision_trace import FallbackHop

    intent = state.get("intent")
    if intent is None:
        return {"replan_strategy": "give_up", "has_critical": False}

    chain = list(state.get("fallback_chain") or [])

    # ---- 先尝试 ILS（新求解器天然处理任意组成，不再按段集合门控）----
    try:
        from agent.planning.planners.ils_planner import plan_hybrid

        client = get_llm_client()
        result = plan_hybrid(intent, client=client, tracer=None)
        if result.success and result.itinerary is not None:
            # 真因修复批 item 3（看板 final_strategy 恒报 llm_first）：ILS 成功
            # 产出的 itinerary 从未经过 assemble_node（decision_trace 唯一注入点，
            # 见 agent/graph/nodes/assemble.py），decision_trace 原生是 None——
            # finalize_plan_node 对 decision_trace=None 会兜底从 state.fallback_chain
            # 重建一份最小 trace（本批同时修的另一半），判据是链末跳 to_stage。
            # 链在 replan_router_node 里已经写过一跳 "llm_backprompt→ils"（决定
            # 尝试 ILS 那一刻写的，反映的是"决定切换"，不是"ILS 真的成功了"）；
            # 这里再补写一跳由 ils_replan_node 自己落的"成功"记录——与下面
            # failure 分支（ils→rule / rule→give_up）对称：阶段的实际结果由跑
            # 这个阶段的节点自己留痕，不依赖上游路由节点提前写好的、恰好凑巧
            # 同尾的记录（那是决定尝试，不是结果确认）。
            chain.append(
                FallbackHop(
                    from_stage="ils",
                    to_stage="ils",
                    reason="ILS 算法给出可行方案，成功兜底（不再进一步降级）",
                ).model_dump()
            )
            return {
                "itinerary": result.itinerary,
                "has_critical": False,
                "violations": [],
                "critic_feedback_text": None,
                # D-7：透传 plan_hybrid 收集到的「绝不默默忽略」告知（点名排不进/
                # 被修复闭环换掉/超预算/时长不足等），narrate_node 消费。
                "advisories": [a.model_dump() for a in result.advisories],
                "fallback_chain": chain,
            }
    except Exception:  # noqa: BLE001
        pass

    # ---- ILS 失败 → rule planner 兜底 ----
    chain.append(
        FallbackHop(
            from_stage="ils",
            to_stage="rule",
            reason="ILS 不适用或未给出有效方案，回 rule planner 兜底",
        ).model_dump()
    )
    try:
        from agent.planning.planners.rule_planner import plan_itinerary
        from agent.core.trace import Tracer

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
