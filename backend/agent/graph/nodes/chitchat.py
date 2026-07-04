"""nodes.chitchat —— 闲聊回话节点。

router 已经在 RouterDecision 里出了 reply_text + tone + cta_chips；
本节点把它们写到 State 的 chitchat_reply_* 字段，然后直接 END。

【chitchat_chips 必须存 dict，不能存活的 CtaChip 对象】
姊妹字段 give_up_chips（replan.py）/ node_actions 里的 chips（narrate.py）都是
存 `CtaChip.model_dump()` 之后的 dict，不是活对象——这不是随意的写法差异，是
跨 turn checkpoint 序列化的硬约束：`agent/graph/build.py::_build_checkpoint_serde`
给 InMemorySaver 传了显式 msgpack 类型白名单（`RouterDecision` 在表上，但
`CtaChip` 单独作为 state 顶层字段的元素类型出现时不在表上）。白名单外的顶层
类型会被 `JsonPlusSerializer` 判定为 "Blocked deserialization"（strict 分支），
跨 turn 读回时静默降级还原成 dict、只留一条 WARNING。现存进 dict 而不是把
CtaChip 也补进白名单，是延续 give_up_chips/node_actions 已有纪律，不引入
第二种"活对象存 state"的先例。
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
        "chitchat_chips": [c.model_dump() for c in (decision.cta_chips or [])],
    }
