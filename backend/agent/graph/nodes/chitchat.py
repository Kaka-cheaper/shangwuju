"""nodes.chitchat —— 闲聊回话节点。

router 已经在 RouterDecision 里出了 reply_text + tone + cta_chips；
本节点把它们写到 State 的 chitchat_reply_* 字段，然后直接 END。
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState


def chitchat_node(state: AgentState) -> dict[str, Any]:
    decision = state.get("router_decision")
    if decision is None:
        return {
            "chitchat_reply_text": "我是晌午局的下午行程小助手，告诉我你下午想做什么吧～",
            "chitchat_tone": "warm",
            "chitchat_chips": [],
        }
    return {
        "chitchat_reply_text": decision.reply_text,
        "chitchat_tone": decision.tone,
        "chitchat_chips": list(decision.cta_chips or []),
    }
