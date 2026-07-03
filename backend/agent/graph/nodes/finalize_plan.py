"""nodes.finalize_plan —— 方案定稿节点（体感编排批 P1："先出方案，后出文案"）。

【这是什么问题】感知延迟优化（perceived latency / progressive disclosure）。
真实 LLM 下，`narrate_node` 一次性做了两件事：① 方案定稿的收尾（规则标题 /
确认动作清单 / decision_trace 收尾）；② 叙事 LLM 调用（标题润色 / 暖语气文案 /
节点 chips，数秒到数十秒）。旧拓扑里 `ITINERARY_READY` 只在 `narrate_node`
跑完（含②）后才推——但方案本身在 critic 放行 / replan give_up / ils 成功的
那一刻就已经定型，用户被迫多等一整次 LLM 往返才看到方案卡，这段等待与"方案
是否定稿"毫无关系，纯属拓扑把两件不相关的事拴在了一起。

【改法】把①从 `narrate_node` 拆出成独立节点 `finalize_plan`，插在"入 narrate
的三条边"（critic 通过 / replan give_up / ils 成功）与 `narrate` 之间：

    critic 通过 ─┐
    replan give_up ─┼→ finalize_plan → narrate → END
    ils 成功 ─┘

`finalize_plan` 全程不调 LLM（纯规则 + 数据搬运），耗时可忽略；graph 每完成
一个节点就会产出一次 state diff（LangGraph `stream_mode="updates"`），
`emit_finalize_plan`（见 `_emit_handlers.py`）借此在 `finalize_plan` 完成的
瞬间推 `ITINERARY_READY`（纯 Itinerary dump + 规则标题 + pending_actions），
不必等后面的 `narrate` 把叙事 LLM 跑完——这是本节点存在的唯一理由。

【职责（从 narrate_node 现状拆分而来，非空想新增）】
1. `pending_actions`：`execute_finalize.build_confirm_actions` 原样挪来
   （纯规则，读 intent + itinerary 算全 confirm 期要 replay 的工具调用清单，
   与叙事无关，之前挂在 narrate 只是因为"narrate 是规划链最后一个节点"这个
   历史巧合，不是真业务依赖）。
2. 规则标题：`agent.intent.narrator.build_template_title`（现成的规则版小
   红书标题构造器，assemble/rule_planner 已在用同一个）写回 `itinerary.
   summary`——保证 `ITINERARY_READY` 推送时已经是一句人话标题，不依赖
   assemble/rule_planner/ils 各自是否记得把 summary 写好（单一收口点）。
   narrate 后面还会尝试用 LLM 产出更精彩的标题替换它（见 narrate.py），
   两者不冲突：这里给的是"能用"的地板，narrate 给的是"精彩"的天花板。
3. `decision_trace` 收尾里"不依赖叙事"的部分：`final_strategy` 判定（读
   `fallback_chain` 最后一跳）+ 把上一条未 resolved 的 `critic_attempt`
   标 resolved（能走到这里说明 critic 已放行，反馈已被消化）。这段逻辑
   原样从 narrate.py 挪来，字面不变——只是执行时机提前。

不负责：
- 叙事 LLM 调用 / LLM 标题 / narration 文案 / node_chips（在 narrate_node）。
- ITINERARY_READY 之外的 SSE 事件（在 `_emit_handlers.emit_finalize_plan`）。
- itinerary=None（give_up 分支且从未成功产出过方案）时的兜底文案——narrate_node
  自己的 `if intent is None or itinerary is None: return {"narration": None}`
  短路已经覆盖，本节点对同样的输入同样短路成 `{}`（无 itinerary 可定稿）。
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState


def finalize_plan_node(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    itinerary = state.get("itinerary")

    if intent is None or itinerary is None:
        # 与 narrate_node 的早退条件对称（见本文件 docstring「不负责」）——
        # 没有方案可定稿，交给 narrate 自己处理这个边界（它也是 no-op）。
        return {}

    update_fields: dict[str, Any] = {}

    # ---- 2. 规则标题（现成 builder，不重写文案逻辑）----
    from agent.intent.narrator import build_template_title

    rule_title = build_template_title(intent, itinerary)
    if rule_title and rule_title.strip() and rule_title.strip() != (itinerary.summary or "").strip():
        update_fields["summary"] = rule_title.strip()

    # ---- 3. decision_trace 收尾（叙事无关部分，原样从 narrate.py 挪来）----
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
        # ——能走到 finalize_plan 说明 critic 已经放行，最后一次 attempt 的反馈被消化了。
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
        update_fields["decision_trace"] = new_trace

    new_itinerary = itinerary.model_copy(update=update_fields)

    # ---- 1. pending_actions（工具前移，纯规则，原样从 narrate.py 挪来）----
    from agent.graph.nodes.execute_finalize import build_confirm_actions

    new_itinerary = new_itinerary.model_copy(
        update={"pending_actions": build_confirm_actions(new_itinerary, intent)}
    )

    return {"itinerary": new_itinerary}
