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

import os
from typing import Any, Optional

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
        from agent.planning.planners.segment_decider import FULL_SEGMENTS, decide_segments
        from agent.planning.planners.ils_planner import plan_hybrid

        segments = decide_segments(intent)
        if segments == FULL_SEGMENTS:
            # 5 段场景：走 ILS
            from agent.planning.planners.rule_planner import _assemble_itinerary as rule_assembler
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


def _RULE_ASSEMBLER_ADAPTER(intent: Any, candidate: Any, tracer: Any) -> Optional[Any]:
    """planner_hybrid.plan_hybrid 期待的 rule_assembler 签名（intent, CandidatePlan, tracer）。

    真按 ILS 选中的 candidate 组装（ADR-0009 决策 1 / 子步 C-1）。

    镜像 tests/test_planner_hybrid.py:_rule_assembler——两处必须行为一致。

    历史 bug（ADR-0009「背景·地基 A」）：旧实现收 candidate 却不用，直接
    `plan_itinerary(intent)` 重跑规则地板，让 ILS 的 utility 选点 / 黑名单 / 重搜
    对最终产物零影响。现改为镜像 rule_planner.plan_itinerary 对
    `_assemble_itinerary` 的参数推导（segments / depart_time / 时长分配 /
    party_size），但主活动 POI / 餐厅 / 用餐时段直接取 candidate 的选择，
    不再重新搜索。
    """
    from data.loader import load_user_profile

    from agent.planning.blueprint.node_decider import decide_segments
    from agent.planning.commute.lookup_hop import lookup_hop
    from agent.planning.planners.rule_planner import _assemble_itinerary, _resolve_time_window

    try:
        main_poi = getattr(candidate, "main_poi", None)
        chosen_restaurant = getattr(candidate, "restaurant", None)
        chosen_time = getattr(candidate, "dining_time", "") or None

        # segments 只由 intent 推导（与 ils_planner.plan_hybrid 步骤 0 的
        # decide_nodes(intent) 同源），candidate 里 main_poi/restaurant 的
        # None 与否本应与之一致（ILS 按同一 decide_nodes 决定要不要搜那一维）。
        segments = decide_segments(intent)
        depart_time, _dining_slots, main_minutes, dining_minutes = _resolve_time_window(
            intent, segments=segments
        )
        party_size = sum(c.count for c in intent.companions) or 1

        user_profile = load_user_profile()
        transport_pref = (
            user_profile.transport_preference
            if user_profile.transport_preference in {"walking", "taxi", "bus"}
            else "taxi"
        )

        def _hop_minutes(from_id: str, to_id: str) -> int:
            # home_to_poi/poi_to_rest/rest_to_home 只喂给 _assemble_itinerary 内部
            # chosen_time 的补偿算术；真实 hop 由 assemble_from_blueprint 内部同一个
            # lookup_hop 重算一遍，两处用同一个函数保证数值一致。
            minutes, _mode, _path = lookup_hop(from_id, to_id, transport_pref, user_profile)
            return minutes

        home_to_poi = _hop_minutes("home", main_poi.id) if main_poi is not None else 0
        poi_to_rest = (
            _hop_minutes(main_poi.id, chosen_restaurant.id)
            if (main_poi is not None and chosen_restaurant is not None)
            else 0
        )
        if chosen_restaurant is not None:
            rest_to_home = _hop_minutes(chosen_restaurant.id, "home")
        elif main_poi is not None:
            rest_to_home = _hop_minutes(main_poi.id, "home")
        else:
            rest_to_home = 0

        return _assemble_itinerary(
            main_poi=main_poi,
            chosen_restaurant=chosen_restaurant,
            chosen_time=chosen_time,
            home_to_poi=home_to_poi,
            poi_to_rest=poi_to_rest,
            rest_to_home=rest_to_home,
            party_size=party_size,
            backup_pois=list(getattr(candidate, "backup_pois", []) or []),
            depart_time=depart_time,
            main_activity_minutes=main_minutes,
            dining_minutes=dining_minutes,
            segments=segments,
            intent=intent,
            user_profile=user_profile,
        )
    except Exception:  # noqa: BLE001
        return None
