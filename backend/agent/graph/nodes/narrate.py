"""nodes.narrate —— 暖语气文案节点。

复用 backend/agent/narrator.py 的 generate_narration。

输入：state["intent"] / state["itinerary"] / state["critic_attempts"]
输出：
- state["narration"] = str
- state["itinerary"] = 新 Itinerary（不可变更新；trace.final_strategy 会被更新）

【spec planning-quality-deep-review R6+R7（Task 6 + Agent H P1-H6）】
- 用 itinerary.model_copy(update={"decision_trace": ...}) + 内嵌 trace.model_copy
  替代原地 mutate（旧实现直接修改 itinerary.decision_trace.final_strategy 等
  字段，副作用泄漏到上游 state，违背 LangGraph "节点返回 diff 不改输入" 原则）
- 把 state.critic_attempts 拼接成 critic_summary 字符串，喂给 narrator 触发
  「主动质疑规则」（R6 核心：让评委看到"AI 主动质疑方案"）
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.core.llm_client import get_llm_client
from agent.intent.narrator import generate_narration


def _build_critic_summary(critic_attempts: list[Any]) -> str:
    """把 state.critic_attempts 拼成一段 narrator 可消费的中文摘要。

    输出示例：
        "经过 2 次 critic 修正：第 1 次（age_duration_mismatch×2 / 已修复）→
         节点[1] 时长 165min 超 75min 上限；第 2 次（已通过）"

    空列表 → 返回空串（narrator 不触发主动质疑）。
    """
    if not critic_attempts:
        return ""

    parts: list[str] = []
    for raw in critic_attempts:
        if hasattr(raw, "model_dump"):
            d = raw.model_dump()
        elif isinstance(raw, dict):
            d = raw
        else:
            continue
        n = d.get("attempt_n", "?")
        codes = d.get("violation_codes") or []
        feedback = (d.get("feedback_summary") or "").strip()
        resolved = d.get("resolved", False)
        head = f"第 {n} 次"
        if codes:
            head += "（" + " / ".join(codes[:3])
            head += " · 已修复" if resolved else " · 未消化"
            head += "）"
        else:
            head += "（已通过）" if resolved else ""
        # 截 critic 反馈摘要（避免塞过长的 prompt）
        if feedback:
            head += f"：{feedback[:80]}"
        parts.append(head)
    return "经过 " + str(len(parts)) + " 次 critic 修正——" + "；".join(parts)


def _extract_quality_warnings(state: AgentState) -> list[str]:
    """把 state.quality_issues 转成 list[str] 喂给 narrator。

    quality_issues 由上游节点（如未来的 meta_critic）写入；当前为空。
    """
    issues = state.get("quality_issues") or []
    out: list[str] = []
    for it in issues:
        if isinstance(it, str) and it.strip():
            out.append(it.strip())
        elif isinstance(it, dict) and it.get("message"):
            out.append(str(it["message"]).strip())
    return out


def narrate_node(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    itinerary = state.get("itinerary")

    if intent is None or itinerary is None:
        return {"narration": None}

    client = get_llm_client()
    use_llm = (
        client is not None
        and getattr(client, "provider", None) != "stub"
    )

    # spec R6：把 state.critic_attempts 拼成摘要喂给 narrator
    critic_summary = _build_critic_summary(state.get("critic_attempts") or [])
    quality_warnings = _extract_quality_warnings(state)

    text = generate_narration(
        intent=intent,
        itinerary=itinerary,
        stage="stream",
        use_llm=use_llm,
        critic_summary=critic_summary,
        quality_warnings=quality_warnings,
    )

    # spec R7（Agent H P1-H6）：用 model_copy 不可变更新 itinerary，避免原地 mutate
    new_itinerary = itinerary
    if itinerary.decision_trace is not None:
        old_trace = itinerary.decision_trace
        chain = old_trace.fallback_chain
        if chain:
            last_to = chain[-1].to_stage
            mapping = {
                "give_up": "give_up",
                "ils": "ils",
                "rule": "rule",
                "llm_backprompt": "llm_backprompt",
            }
            final_strategy = mapping.get(last_to, "llm_first")
        else:
            final_strategy = "llm_first"

        # 把上一条 critic_attempt（如果存在且未 resolved）标 resolved
        # ——能走到 narrate 说明 critic 已经放行，最后一次 attempt 的反馈被消化了
        new_critic_attempts = list(old_trace.critic_attempts)
        if new_critic_attempts:
            last = new_critic_attempts[-1]
            if not last.resolved:
                new_critic_attempts[-1] = last.model_copy(update={"resolved": True})

        new_trace = old_trace.model_copy(
            update={
                "final_strategy": final_strategy,
                "critic_attempts": new_critic_attempts,
            }
        )
        new_itinerary = itinerary.model_copy(update={"decision_trace": new_trace})

    # spec algorithm-redesign R5：narrate 主逻辑末尾的副作用——回写 user_profile.json
    # 路径 B（design.md §Component 4 决策点 4）：不动 graph 拓扑（spec B 锁的编排冻结纪律）
    # 失败时 try/except 吞掉异常，不阻断 narrate 主输出
    try:
        from agent.planning.memory_writer import persist_memory

        persist_memory(state, client=client)
    except Exception as exc:
        # 防御性：永不阻断主流程
        import logging
        logging.getLogger(__name__).debug(
            "narrate_node: persist_memory side-effect failed: %s", exc
        )

    return {"narration": text, "itinerary": new_itinerary}
