"""nodes.narrate —— 暖语气文案节点。

复用 backend/agent/narrator.py 的 generate_narration。

输入：state["intent"] / state["itinerary"]
输出：state["narration"] = str
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.llm_client import get_llm_client
from agent.narrator import generate_narration


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

    text = generate_narration(
        intent=intent,
        itinerary=itinerary,
        stage="stream",
        use_llm=use_llm,
    )

    # 更新 trace.final_strategy 到「定稿状态」（覆盖 assemble 时的中间值）。
    # 判据用 fallback_chain（与 assemble_node 一致，避免漂移）：
    if itinerary.decision_trace is not None:
        chain = itinerary.decision_trace.fallback_chain
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
        itinerary.decision_trace.final_strategy = final_strategy
        # 把上一条 critic_attempt（如果存在且未 resolved）标 resolved
        # ——能走到 narrate 说明 critic 已经放行，最后一次 attempt 的反馈被消化了
        if itinerary.decision_trace.critic_attempts:
            last = itinerary.decision_trace.critic_attempts[-1]
            if not last.resolved:
                last.resolved = True

    return {"narration": text, "itinerary": itinerary}
