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

【record_rejected 接线（用户偏好面板全环方案 §9/§11.1/§14.2，全仓负向记忆
输入的唯一挂载点）】
`record_rejected` 此前全仓零业务调用（只有测试直调）。方案 §9 坐实：只有
refine（本节点）产出结构化的 `original` vs `refined_intent` 两个
`IntentExtraction` 对象，字段集差（`dietary_constraints`/`physical_constraints`/
`experience_tags` 三个受控词典字段，各自 `original - refined`）是唯一干净、
无需从自由文本 `changed_fields` 反解析的负向信号；swap（点踩/换菜，
`node_swap.py`）**不接**这条通道——tag 级信号不可靠（探针实证会过度拒绝，
如点踩"小湘阁"误伤"热闹/社交"），swap 的负向意图归诉求台账
（`demand_ledger`），不进 `rejected_tags` 累积。

词典守卫（§14.2 H3 哨兵）：字段集差先过 `DIETARY_TAGS | PHYSICAL_TAGS |
EXPERIENCE_TAGS` 受控词典再记，词典外值丢弃 + `logger.warning`（防 LLM 产出
词典外 tag 污染 `compute_priors` 打分）。守卫放在本调用点（而非
`record_rejected` 内部）：§9 已定"只 refine 接"后调用点唯一，不破坏
`record_accepted`/`record_rejected` 的既有契约对称。

session_id 可得性（§11.1 坐实）：`agent/graph/state.py:121` 确认
`session_id` 是 `AgentState` 的 SESSION_SCOPED 一等字段，本节点内
`state.get("session_id")` 可直接取得，不需要退到图外层。

正向对偶不做（§12）：refine 新增的 tag **不**同步 `record_accepted`——
accepted 维持"仅 confirm 记"的既有唯一时机（refine 后的新方案仍走完整
confirm 循环，新 tag 会在 confirm 时被 `_collect_itinerary_tags` 自然记到，
不遗漏；refine 只是"改了要求"，不是"确认下单"，抢记违反"记忆绑定确认动作"
不变量）。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.context import GraphStateSource, pack_routing_context, render_demand_recap
from agent.graph.state import AgentState, reset_for_new_episode
from agent.core.llm_client import get_llm_client
from agent.intent.refiner import refine_intent, summarize_itinerary
from data.memory_store import record_rejected
from schemas.tags import DIETARY_TAGS, EXPERIENCE_TAGS, PHYSICAL_TAGS

logger = logging.getLogger(__name__)

_VALID_TAGS = DIETARY_TAGS | PHYSICAL_TAGS | EXPERIENCE_TAGS

# 集合差的字段名清单——与 IntentExtraction 的三个受控 tag 字段一一对应
# （schemas/intent.py:129/138/148），不含 companions（自由文本，无先验注入
# 通道，方案 §7 已排除）。
_TAG_DIFF_FIELDS = ("dietary_constraints", "physical_constraints", "experience_tags")


def _dropped_tags(original: Any, refined: Any) -> list[str]:
    """算 `original` 相对 `refined` 在三个受控字段上掉了哪些 tag（方案 §9.1）。

    只看"被移除的值"（原有、refine 后没有），不看新增的（新增走 §12"不做
    正向对偶"的既定结论，不在本函数职责内）。词典外值在这里先原样收集，由
    调用方（`refiner_node`）过词典守卫后再决定是否喂给 `record_rejected`
    （守卫归属见模块 docstring §14.2）。
    """
    dropped: list[str] = []
    for field in _TAG_DIFF_FIELDS:
        before = set(getattr(original, field, None) or [])
        after = set(getattr(refined, field, None) or [])
        dropped.extend(sorted(before - after))
    return dropped


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

    refined_intent = output.refined_intent

    # record_rejected 接线：字段集差 → 词典守卫 → 记账（模块 docstring
    # 「record_rejected 接线」节）。永不阻断主流程——记忆累积是旁挂副作用，
    # 不该因为记账失败让 refine 本身翻车。
    session_id = state.get("session_id")
    if session_id and refined_intent is not None:
        try:
            dropped_raw = _dropped_tags(original, refined_intent)
            dropped = [t for t in dropped_raw if t in _VALID_TAGS]
            skipped = [t for t in dropped_raw if t not in _VALID_TAGS]
            if skipped:
                logger.warning(
                    "refiner_node: record_rejected 收到词典外 tag，已丢弃：%s",
                    skipped,
                )
            if dropped:
                record_rejected(session_id, tags=dropped)
        except Exception as exc:  # noqa: BLE001
            logger.warning("refiner_node: record_rejected 失败（不阻断主流程）：%s", exc)

    # 重置部分（EPISODE_SCOPED 全集）先铺底，业务输出（intent）后覆盖——见模块 docstring。
    # refinement_changed_fields/note:随 diff 带给 emit_refiner 装进 REFINEMENT_DONE
    # (修复前该 payload 的 changed_fields 恒硬编码 [],前端 toast 拿不到真实变更)。
    return {
        **reset_for_new_episode(),
        "intent": refined_intent,
        # getattr 防御:多处测试以 SimpleNamespace 垫桩 refine_intent,只带 refined_intent
        "refinement_changed_fields": list(getattr(output, "changed_fields", None) or []),
        "refinement_note": getattr(output, "refiner_note", None),
    }
