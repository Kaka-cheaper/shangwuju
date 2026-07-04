"""nodes.refiner —— 反馈合并节点。

复用 backend/agent/refiner.py 的 refine_intent。

输入：
- state["intent"]（原意图）
- state["user_input"]（用户反馈，已被 router_node 路由为 feedback）
输出：state["intent"] = 新意图（含 raw_input 拼接的反馈）

后续：refiner 节点不接 planner —— 由 build.py 把 refiner 接到 execute（重新搜候选）。
这是 Plan-and-Execute 的标准做法：约束变了 → 重 plan，但 plan 仍要看新候选。

【ADR-0012 决策 4：字段生命周期表】反馈 = 新规划事件的一种触发方式，和
intent_node（新需求触发）共用 agent.graph.state.reset_for_new_episode() 生成
的同一份 EPISODE_SCOPED 重置 diff——itinerary/blueprint/critic 状态/advisories/
候选池等全部清零，让流程从 execute 重新搜候选 → plan → critic 走一遍干净的。
合并顺序：reset diff 先铺底，refiner 自己的业务输出（intent）后覆盖，
绝不能让 reset 把刚精炼出的 intent 冲掉。

【ADR-0011 决策 3：refiner 切片，2026-07-03 新增】
经会话上下文打包器（`agent.context.pack_routing_context`）取「方案版本志 +
台账生效条目」切片，喂进 `refine_intent` 的 LLM 上下文——闭合一个已知窗口：
用户先点了某个节点的定向调整按钮（写进诉求台账），随后又说"重新规划一个"
这类全量反馈，refiner 走 LLM 整体重解 intent，此前完全看不到台账，等于把
刚点的诉求当没发生过。现在 refiner_node 每次都把「此前的有效诉求」递给
LLM，让它在整体重解时继续尊重这些已记账的诉求。见
`agent.context.packer.render_demand_recap` 与 `refiner_prompt.build_user_message`
的 `ledger_recap` 参数 docstring。
"""

from __future__ import annotations

from typing import Any

from agent.context import GraphStateSource, pack_routing_context, render_demand_recap
from agent.graph.state import AgentState, reset_for_new_episode
from agent.core.llm_client import get_llm_client
from agent.intent.refiner import refine_intent, summarize_itinerary


def refiner_node(state: AgentState) -> dict[str, Any]:
    original = state.get("intent")
    feedback_text = state.get("user_input") or ""

    if original is None or not feedback_text:
        return {}

    client = get_llm_client()
    routing_ctx = pack_routing_context(GraphStateSource(state))
    ledger_recap = render_demand_recap(routing_ctx) or None
    output = refine_intent(
        original=original,
        feedback_text=feedback_text,
        client=client,
        # session-no-new-request：把上一版行程摘要也喂进去，让 LLM 据"被拒的这份方案"判断
        itinerary_summary=summarize_itinerary(state.get("itinerary")),
        ledger_recap=ledger_recap,
    )

    # 重置部分（EPISODE_SCOPED 全集）先铺底，业务输出（intent）后覆盖——见模块 docstring。
    # refinement_changed_fields/note:随 diff 带给 emit_refiner 装进 REFINEMENT_DONE
    # (修复前该 payload 的 changed_fields 恒硬编码 [],前端 toast 拿不到真实变更)。
    return {
        **reset_for_new_episode(),
        "intent": output.refined_intent,
        # getattr 防御:多处测试以 SimpleNamespace 垫桩 refine_intent,只带 refined_intent
        "refinement_changed_fields": list(getattr(output, "changed_fields", None) or []),
        "refinement_note": getattr(output, "refiner_note", None),
    }
