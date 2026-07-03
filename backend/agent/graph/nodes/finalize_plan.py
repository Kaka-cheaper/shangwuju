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

_FINAL_STRATEGY_BY_LAST_HOP: dict[str, str] = {
    "give_up": "give_up",
    "ils": "ils",
    "rule": "rule",
    "llm_backprompt": "llm_backprompt",
}
"""fallback_chain 最后一跳 to_stage → final_strategy 的判据（单一映射表，
两处消费——decision_trace 已存在时的收尾、decision_trace 缺失时的兜底重建——
共享同一份，不重复定义两套容易漂移的映射）。未命中键（如从未 fallback 过）
落 "llm_first"。"""


def _final_strategy_from_chain(chain: list[Any]) -> str:
    """从 fallback_chain（FallbackHop 列表）算 final_strategy：读链末跳
    `to_stage`，只增不减、严格反映"已发生的事"（与 assemble_node 同一判据，
    见该文件同名逻辑）。空链 → "llm_first"（主路径一次过，从未降级）。
    """
    if not chain:
        return "llm_first"
    last_to = getattr(chain[-1], "to_stage", None)
    return _FINAL_STRATEGY_BY_LAST_HOP.get(last_to, "llm_first")


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
        final_strategy = _final_strategy_from_chain(old_trace.fallback_chain)

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
    else:
        # 真因修复批 item 3（看板 bug：ILS 一次成功也报 final_strategy=llm_first）。
        #
        # decision_trace 唯一注入点是 assemble_node（planner→assemble→critic 这
        # 条链，见 agent/graph/nodes/assemble.py 模块 docstring）——但 ILS 成功
        # 分支（replan.py:ils_replan_node）直接把 plan_hybrid 产出的 itinerary
        # 写回 state，从不经过 assemble_node，itinerary.decision_trace 因此原生
        # 是 None。这不是"没有决策历史"，只是"没人写过"：state.fallback_chain
        # 是 EPISODE_SCOPED 字段，跨节点持续累积，即使 itinerary 侧没挂 trace，
        # 链本身是完整的——从它重建一份最小 DecisionTrace 顶上，final_strategy
        # 判据与「decision_trace 已存在」分支同一张映射表（_final_strategy_from_chain），
        # 不重新发明一套规则。
        #
        # 之前的行为：本分支完全不存在 → update_fields 不含 decision_trace →
        # itinerary.decision_trace 保持 None → sse_adapter 读不到 trace →
        # DONE payload 的 final_strategy 落到它自己的默认值 "llm_first"——
        # 无论方案实际是 ILS 兜底还是 rule 兜底出的，看板都显示"LLM 一次过"。
        fallback_dicts = state.get("fallback_chain") or []
        if fallback_dicts:
            from schemas.decision_trace import DecisionTrace, FallbackHop

            chain_objs = [
                FallbackHop.model_validate(d) if isinstance(d, dict) else d
                for d in fallback_dicts
            ]
            update_fields["decision_trace"] = DecisionTrace(
                fallback_chain=chain_objs,
                final_strategy=_final_strategy_from_chain(chain_objs),
            )
        # fallback_chain 也空（从未 replan 过，如 critic 一次通过、或
        # planner_mode="rule" 全程没触发降级）→ 保持 decision_trace=None：
        # 没有任何决策历史可展示，前端本就该隐藏卡片（DecisionTrace.is_empty()
        # 语义），不为空链硬造一个 final_strategy="llm_first" 的假 trace。

    new_itinerary = itinerary.model_copy(update=update_fields)

    # ---- 1. pending_actions（工具前移，纯规则，原样从 narrate.py 挪来）----
    from agent.graph.nodes.execute_finalize import build_confirm_actions

    new_itinerary = new_itinerary.model_copy(
        update={"pending_actions": build_confirm_actions(new_itinerary, intent)}
    )

    return {"itinerary": new_itinerary}
