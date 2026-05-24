"""nodes.critic —— Itinerary 客观约束验证节点（Plan-and-Execute 中 Evaluator 阶段）。

复用 backend/agent/v2/critics_v2.py 的 validate_itinerary。

输入：state["intent"] / state["itinerary"]
输出：
- state["violations"] = list[Violation]
- state["has_critical"] = bool
- state["critic_feedback_text"] = str（仅在 has_critical=True 时填，给 planner backprompt）
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.planning.critic.critics_v2 import (
    Severity,
    format_violations_for_llm,
    validate_itinerary,
)


def critic_node(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    itinerary = state.get("itinerary")

    if intent is None:
        # 没 intent 无法验，直接放行（不应该走到这里）
        return {
            "violations": [],
            "has_critical": False,
            "critic_feedback_text": None,
        }

    if itinerary is None:
        # itinerary 为空 = plan 阶段失败（候选为空 / blueprint 生成失败）
        # 这是 critical 违规：必须触发 replan 让 ILS 兜底或 give_up
        return {
            "violations": [],
            "has_critical": True,
            "critic_feedback_text": (
                "行程为空（itinerary=None）：plan 阶段未能生成有效蓝图。"
                "可能原因：候选 POI/餐厅为空（约束过严或 mock 数据不覆盖）。"
                "请放宽约束重试，或切换到 ILS 算法兜底。"
            ),
        }

    violations = validate_itinerary(
        itinerary,
        intent,
        user_id=state.get("user_id") or "demo_user",
        # spec algorithm-redesign R2：透传候选池快照给 _check_tool_consistency
        # 检查 itinerary.nodes[*].target_id 是否在 execute 阶段并行写入的 pois / restaurants 里
        # （execute_node 把 search_pois / search_restaurants 结果写到 state.pois / state.restaurants）
        tool_results={
            "pois": state.get("pois") or [],
            "restaurants": state.get("restaurants") or [],
        },
    )
    has_critical = any(v.severity == Severity.CRITICAL for v in violations)

    # spec interaction-experience-review：规则模式产出的 itinerary 已经过 plan_itinerary
    # 内部的 5 级降级 + dining_slots 试探，不应再走 LLM-Modulo critic backprompt 闭环——
    # 这是「规则模式 = 不调用大模型」承诺的一部分。critic violations 仍然记录给 trace 看见，
    # 但 has_critical 强制为 False 让流程走 narrate 不再回 planner。
    mode = state.get("planner_mode")
    if mode == "rule" and has_critical:
        # 仍记录 violations 让 DecisionTraceCard 能看到「规则路径过 critic 时这些维度可改进」
        # 但不触发 backprompt（rule 路径产物即终态）
        has_critical = False

    feedback = format_violations_for_llm(violations) if has_critical else None

    # Step 8：累积 critic_attempts 到 trace
    from schemas.decision_trace import CriticAttempt

    prev_attempts = list(state.get("critic_attempts") or [])
    attempt_n = len(prev_attempts) + 1

    # 把上一次 attempt 标 resolved（如果它存在）：意思是上一次给 LLM 的反馈被消化了
    if prev_attempts and not has_critical:
        last = dict(prev_attempts[-1])
        last["resolved"] = True
        prev_attempts[-1] = last

    if has_critical:
        # 抽 violation code 字符串；同 attempt 内重复 code 合计数（"commute_infeasible×2"）
        # 避免前端 React 同 key 警告，也让评委一眼看到一个 attempt 里几条违规
        from collections import Counter

        raw_codes = [
            getattr(getattr(v, "code", None), "value", str(getattr(v, "code", "")))
            for v in violations
            if v.severity == Severity.CRITICAL
        ]
        code_counter = Counter(raw_codes)
        critical_codes = [
            f"{code}×{n}" if n > 1 else code
            for code, n in code_counter.items()
        ]
        attempt_dict = CriticAttempt(
            attempt_n=attempt_n,
            violation_codes=critical_codes,
            feedback_summary=(feedback or "")[:200],
            resolved=False,
        ).model_dump()
        prev_attempts.append(attempt_dict)

    return {
        "violations": violations,
        "has_critical": has_critical,
        "critic_feedback_text": feedback,
        "critic_attempts": prev_attempts,
    }


def route_after_critic(state: AgentState) -> str:
    """conditional edge：critic 后走 narrate 还是 replan。"""
    if state.get("has_critical"):
        return "replan_router"
    return "narrate"
