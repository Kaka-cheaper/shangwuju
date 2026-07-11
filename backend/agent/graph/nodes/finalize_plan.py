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
5. 出口满足度审计（ADR-0014 决策 2 · G-2）：`agent.planning.critic.
   exit_audit.audit_constraint_relaxation` 统一比对最终 itinerary vs
   intent 全部约束，soft 未满足产出 `AdvisoryCode.CONSTRAINT_RELAXED`
   追加进 `state.advisories`——本节点是"方案已定稿"的确定性时机，天然
   覆盖 LLM 主路径/ILS/rule 三条规划路径的最终产物，不必在三处规划入口
   各自重复比对（见该模块 docstring「这是什么问题」节）。

不负责：
- 叙事 LLM 调用 / LLM 标题 / narration 文案 / node_chips（在 narrate_node）。
- ITINERARY_READY 之外的 SSE 事件（在 `_emit_handlers.emit_finalize_plan`）。
- itinerary=None（give_up 分支且从未成功产出过方案）时的兜底文案——narrate_node
  自己的 `if intent is None or itinerary is None: return {"narration": None}`
  短路已经覆盖，本节点对同样的输入同样短路成 `{}`（无 itinerary 可定稿）。

【ADR-0011 前置核实①/决策 3：方案版本志（plan_version_log），E-2 第一块砖
第二件】方案定稿即是"新版本诞生"的确定性时机——本节点纯规则、无 LLM 调用，
比 narrate 的叙事 LLM 调用更早、更稳定地知道"这一版方案定型了"，是版本志
天然的唯一常规写手（confirm 路径的额外一笔在 `api/_streams/graph_confirm.py
::_writeback_graph_state`，见该函数 docstring）。

条目形状 {version_n, summary, trigger, timestamp} 与选型见 `_version_log_
entry` 的实现与其内联注释；trigger 判据是本任务的一个显式拍板点（未强制
清单化，报告里会说明；**深审改判,主代理**）：trigger 只取**入口维度**——
`route_kind == "feedback"`（经 refiner_node 走增量调整）→ "feedback"，
否则（经 intent_node 走全新解析，含会话首轮与"重新规划一个"）→ "first"。
子代理原方案让 `replan_strategy` 抢答（记"这版怎么解出来的"），被改判：
求解路径这个事实已经住在 `itinerary.decision_trace.final_strategy`（真因
修复批刚收口的那条链），版本志再存一份=同一事实两处存放、必然漂移——
版本志的语义轴是"用户视角这版因何而生"，求解器内幕归 trace。
"""

from __future__ import annotations

from typing import Any

from agent.graph._emit_context import now_ms
from agent.graph.state import AgentState

# 版本志"一行人话" summary 里引用的原话片段长度上限——与 route_turn.py 里
# `utterance[:40]`（注入日志截断）同一数量级的既有先例，供人读的一行摘要，
# 不是完整存档（完整原话已经在 messages 通道里，见 nodes/router.py）。
_SUMMARY_SNIPPET_CHARS = 40


def _snippet(text: str) -> str:
    t = (text or "").strip()
    if len(t) <= _SUMMARY_SNIPPET_CHARS:
        return t
    return t[:_SUMMARY_SNIPPET_CHARS] + "…"


def _version_log_trigger(state: AgentState) -> str:
    """版本志 trigger 判据——只反映入口维度,见模块 docstring「显式拍板点」的改判说明。"""
    return "feedback" if state.get("route_kind") == "feedback" else "first"


def _version_log_entry(state: AgentState, *, version_n: int) -> dict[str, Any]:
    """构造本次方案定稿要追加的版本志条目（纯 dict，免 serde 白名单）。

    summary 的"首轮"/"反馈轮"两种措辞对应 route_kind（是否 == "feedback"）
    ——反馈原话就取 state.user_input：refiner_node 不改写 user_input（它把
    反馈原文和旧 intent 一起喂给 LLM 产出新 intent，但不回写 state["user_
    input"]），finalize_plan 运行时 state.user_input 仍是本轮（refiner 那轮）
    用户敲的原话，核实见 `agent/graph/nodes/refiner.py::refiner_node`。
    """
    raw = _snippet(state.get("user_input") or "")
    if state.get("route_kind") == "feedback":
        summary = f"v{version_n}: 应『{raw}』调整"
    else:
        summary = f"v{version_n}: 按『{raw}』出方案"
    return {
        "version_n": version_n,
        "summary": summary,
        "trigger": _version_log_trigger(state),
        "timestamp": now_ms(),
    }

_FOLD_QUALITY_WARNING_THRESHOLD_MIN = 90
"""首段折叠量 ≥ 该阈值时写一条 quality_warnings（拍板项 P1）：差额巨大说明
真正的问题是规划本身没填满时段（该修的是蓝图/涌现层，不是折叠层）——折叠
不设硬上限（诚实优先），但异常不静默吞掉，把"计划比你的出发时段晚开场较多"
暴露给 narrator。"""


def _fold_minutes(state: AgentState, itinerary: Any) -> int:
    """现算首段折叠量（分钟）：blueprint 原 preferred_start_time 与折叠后
    nodes[0].start_time 的差值。

    零管道设计（方案 1.29）：折叠量不需要 assemble 外传元数据——本节点 state
    里同时有 blueprint（原出发时刻）与 itinerary（折叠后出发时刻），差值现场
    可算，零 schema/零事件改动。

    防御边界：仅当 state.blueprint 存在且确为本 itinerary 的拼装来源时结果
    有意义——调用方以 `itinerary.decision_trace is not None` 为门（该 trace
    只由 assemble_node 注入，即 LLM/backprompt 主路径；ILS/rule 路径的
    state.blueprint 可能是早前失败轮的陈值，不做折叠说明——其 trace 本就
    没有 blueprint_rationale 可追加，assemble 内部的 logger.info 一行仍覆盖
    全部三路径的排障需求）。解析失败/差值非正/差值离谱（≥24h）一律返 0。
    """
    blueprint = state.get("blueprint")
    if blueprint is None or not getattr(itinerary, "nodes", None):
        return 0
    try:
        from agent.planning.blueprint.assemble_blueprint import _parse_hhmm

        delta = _parse_hhmm(itinerary.nodes[0].start_time) - _parse_hhmm(
            blueprint.preferred_start_time
        )
    except (ValueError, AttributeError):
        return 0
    if delta <= 0 or delta >= 24 * 60:
        return 0
    return delta


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

    # fold 可观测性（I1 折叠批，方案 1.29 / ADR-0017）：折叠量本节点现算，
    # 差值 >0 时 ① trace 的 blueprint_rationale 追加一句人话说明（评委追问
    # "为什么 19:55 出发"时讲解可指认）；② 差额 ≥90min 时写 quality_warnings
    # （拍板项 P1——差额巨大是"规划没填满时段"的信号，不静默）。
    # 注意追加的是 decision_trace 的 rationale 副本（定稿阶段），不动
    # state.blueprint 本体——emit_planner 推信任带的 blueprint.rationale[:80]
    # 发生在折叠语义确定之前，不会提早泄漏折叠时刻（方案 1.34-W3）。
    fold_min = 0

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

        fold_min = _fold_minutes(state, itinerary)
        new_rationale = old_trace.blueprint_rationale
        if fold_min > 0:
            blueprint = state.get("blueprint")
            fold_note = (
                f"出发时刻按首站可订时刻折叠 {fold_min} 分钟"
                f"（{blueprint.preferred_start_time}→{itinerary.nodes[0].start_time}），"
                "出门即行程、不在店门口干等。"
            )
            new_rationale = (
                f"{new_rationale} {fold_note}".strip() if new_rationale else fold_note
            )

        new_trace = old_trace.model_copy(
            update={
                "final_strategy": final_strategy,
                "critic_attempts": new_critic_attempts,
                "blueprint_rationale": new_rationale,
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

    # ---- 4. 方案版本志（ADR-0011 前置核实①：本节点是版本志的常规写手）----
    # operator.add 归并器：这里只返回**本轮新增的这一条**，历史条目由 reducer
    # 拼接保留（见 agent/graph/state.py plan_version_log 字段注释）。
    existing_log = state.get("plan_version_log") or []
    version_n = len(existing_log) + 1
    version_entry = _version_log_entry(state, version_n=version_n)

    # ---- 5. 出口满足度审计（ADR-0014 决策 2 · G-2：单点挂点）----
    # 方案在这里已经定稿（决策 trace / pending_actions 都已收尾）——是"最终
    # itinerary vs intent 全部约束"这个比对唯一该发生的时机（比对搜索期中间
    # 尝试没有意义，见 `agent.planning.critic.exit_audit` 模块 docstring）。
    # 天然覆盖 LLM 主路径 / ILS / rule 三条规划路径的最终产物——它们都会经过
    # 本节点才进 narrate，不需要在三处规划入口各自重复这个比对。
    # 追加而非覆盖：state.advisories 此刻可能已有 ils_replan_node 写入的
    # D-7 告知（PINNED_UNSATISFIABLE 等），本轮新增的 CONSTRAINT_RELAXED 只
    # 追加在其后，narrate.py 的 _extract_advisories/_merge_advisories 读到的
    # 是包含两者的完整列表。
    from agent.planning.critic.exit_audit import audit_constraint_relaxation

    existing_advisories = list(state.get("advisories") or [])
    new_advisories = audit_constraint_relaxation(new_itinerary, intent)
    if new_advisories:
        existing_advisories = existing_advisories + [
            a.model_dump() for a in new_advisories
        ]

    out: dict[str, Any] = {
        "itinerary": new_itinerary,
        "plan_version_log": [version_entry],
        "advisories": existing_advisories,
    }

    # ---- 6. fold 巨额折叠的质量信号（拍板项 P1）----
    # quality_issues 是普通覆盖字段（无 reducer），读现值合并后整体返回——
    # 与上面 advisories 的追加姿势一致。intent_node 之外本节点是第二写手
    # （见 state.py 字段注释）。
    if fold_min >= _FOLD_QUALITY_WARNING_THRESHOLD_MIN:
        existing_quality = list(state.get("quality_issues") or [])
        existing_quality.append(
            f"计划比原定出发时段晚开场较多（出发时刻按首站可订时刻折叠了 "
            f"{fold_min} 分钟）——时段可能没有被填满"
        )
        out["quality_issues"] = existing_quality

    return out
