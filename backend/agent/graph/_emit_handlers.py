"""LangGraph 节点 → SSE 事件 emit 函数集合（spec code-modularization-refactor H3）。

每个 emit_xxx 函数对应一个 LangGraph 节点的事件转换逻辑。

- 输入：EmitContext（共享可变状态）+ node_diff（节点本次返回的 state diff）
- 输出：list[SseEvent]（推 0 条 / 1 条 / N 条都有可能）
- 副作用：可能改 ctx.seq / ctx.itinerary_emitted / ctx.chitchat_emitted

主函数 run_graph_stream 仅负责：
1. 维护一个 dispatch dict：node_name → emit_xxx 函数
2. for chunk in graph.astream() → for node_name, node_diff in chunk → 调对应 emitter
3. yield from 返回的 list

行为契约：与拆分前的 run_graph_stream 内嵌 if/elif 链完全一致。
"""

from __future__ import annotations

from typing import Any

from schemas.sse import SseEvent, SseEventType

from ._emit_context import EmitContext


def emit_router(ctx: EmitContext, diff: dict[str, Any], user_input: str) -> list[SseEvent]:
    decision = diff.get("router_decision")
    route_kind = diff.get("route_kind")
    out: list[SseEvent] = []
    if route_kind == "planning":
        out.append(ctx.emit(SseEventType.AGENT_THOUGHT, {"text": "好的，让我帮你规划一下。"}))
    elif route_kind == "feedback":
        out.append(ctx.emit(SseEventType.AGENT_THOUGHT, {"text": "收到反馈，正在调整……"}))
        # refiner 开始信号（兼容旧前端）
        out.append(
            ctx.emit(
                SseEventType.REFINEMENT_START, {"feedback_text": user_input}
            )
        )
    elif decision is not None and route_kind != "planning":
        # chitchat / confirm / clarify / defense（ADR-0011 6 标签闭集，除
        # planning/feedback 外的其余 4 类）→ 直接推
        out.append(ctx.emit(SseEventType.CHITCHAT_REPLY, decision.model_dump()))
        ctx.chitchat_emitted = True
    return out


def emit_intent(ctx: EmitContext, diff: dict[str, Any]) -> list[SseEvent]:
    intent = diff.get("intent")
    if intent is None:
        return []
    return [ctx.emit(SseEventType.INTENT_PARSED, intent.model_dump())]


def emit_refiner(ctx: EmitContext, diff: dict[str, Any]) -> list[SseEvent]:
    intent = diff.get("intent")
    if intent is None:
        return []
    return [
        ctx.emit(
            SseEventType.REFINEMENT_DONE,
            {
                "refined_intent": intent.model_dump(),
                "changed_fields": [],  # 保持向后兼容；详细字段差由前端比对
                "refiner_note": "已合并你的反馈，正在重新规划。",
            },
        ),
        # 然后用新意图重推 intent_parsed 让前端 IntentSummary 刷新
        ctx.emit(SseEventType.INTENT_PARSED, intent.model_dump()),
    ]


# 3 个 fan-out worker 共享的 group_id（让前端可识别同 fan-out 组并横向并列展示）
_FANOUT_GROUP = "fanout-execute"
_WORKER_TO_TOOL = {
    "search_pois_worker": "search_pois",
    "search_restaurants_worker": "search_restaurants",
    "get_user_profile_worker": "get_user_profile",
}


def emit_fanout_worker(
    ctx: EmitContext, node_name: str, diff: dict[str, Any]
) -> list[SseEvent]:
    """3 个搜索 worker（fan-out 并行组）→ 合成 tool_call_start + tool_call_end。

    spec innovation-review R1：加 group_id 让前端可识别同 fan-out 组并横向并列展示。
    """
    tool_name = _WORKER_TO_TOOL[node_name]
    out: list[SseEvent] = [
        ctx.emit(
            SseEventType.TOOL_CALL_START,
            {
                "tool": tool_name,
                "input": {},
                "group_id": _FANOUT_GROUP,
                "parallel": True,
            },
        )
    ]
    # 合成 end（结果数量摘要）
    out_summary: dict[str, Any] = {"success": True}
    if "pois" in diff:
        out_summary["count"] = len(diff["pois"])
    elif "restaurants" in diff:
        out_summary["count"] = len(diff["restaurants"])
    elif "user_profile" in diff:
        out_summary["found"] = diff["user_profile"] is not None
    # Step 6：tag relaxation 透传（split per worker key）
    relaxed = (
        diff.get("pois_relaxed_tags") or diff.get("restaurants_relaxed_tags") or []
    )
    if relaxed:
        out_summary["relaxed_tags"] = list(relaxed)
    out.append(
        ctx.emit(
            SseEventType.TOOL_CALL_END,
            {
                "tool": tool_name,
                "output": out_summary,
                "duration_ms": 0,
                "group_id": _FANOUT_GROUP,
                "parallel": True,
            },
        )
    )
    return out


def emit_planner(ctx: EmitContext, diff: dict[str, Any]) -> list[SseEvent]:
    weights = diff.get("weights")
    blueprint = diff.get("blueprint")
    attempt = diff.get("plan_attempt", 1)
    out: list[SseEvent] = []
    # plan_attempt > 1 说明这是 critic backprompt 重做
    if attempt > 1:
        # critic_feedback_text 在 state 中而非 diff 中——
        # diff 是 planner 节点本次返回的字段，若 planner 不更新它，
        # 这里读 None 也无妨；至少把 attempt 信号推出去
        out.append(
            ctx.emit(
                SseEventType.CRITIC_FIX_ATTEMPT,
                {
                    "attempt": attempt,
                    "feedback_text": "（详见上一条 critic_violations）",
                },
            )
        )
    if weights is not None:
        out.append(
            ctx.emit(
                SseEventType.AGENT_THOUGHT,
                {"text": f"出 plan 第 {attempt} 次（权重 {weights.summary()}）"},
            )
        )
    if blueprint is not None and weights is not None:
        # edge_v1：蓝图里只有 mid nodes（不含 home 首尾）。
        out.append(
            ctx.emit(
                SseEventType.AGENT_THOUGHT,
                {
                    "text": (
                        f"蓝图 {len(blueprint.nodes)} 个节点：{blueprint.rationale[:80]}"
                    ),
                },
            )
        )
    return out


def emit_critic(ctx: EmitContext, diff: dict[str, Any]) -> list[SseEvent]:
    has_critical = diff.get("has_critical")
    violations = diff.get("violations") or []
    if has_critical:
        # 1. 推 CRITIC_VIOLATIONS 让前端可视化每条违规（红色卡片）
        violation_dicts = []
        for v in violations:
            try:
                # critics_v2.Violation 是 Pydantic BaseModel
                violation_dicts.append(v.model_dump())
            except AttributeError:
                # 兜底：手工取属性（防 Violation 类型升级时漂）
                violation_dicts.append(
                    {
                        "code": getattr(
                            getattr(v, "code", None),
                            "value",
                            str(getattr(v, "code", "")),
                        ),
                        "severity": getattr(
                            getattr(v, "severity", None),
                            "value",
                            str(getattr(v, "severity", "")),
                        ),
                        "message": getattr(v, "message", str(v)),
                        "field_path": getattr(v, "field_path", ""),
                    }
                )
        # 仅推 hard（soft 不进 SSE，避免噪声）
        # ADR-0008 B-1 把 severity 枚举从 CRITICAL/WARNING 改名为 HARD/SOFT，
        # 序列化后的字面值是 Severity.HARD.value == "hard"（不是 "critical"）；
        # 此处曾漏改导致 critical_only 恒为空、CRITIC_VIOLATIONS 恒空 payload
        # （评委看板空白的根因）。
        critical_only = [
            d for d in violation_dicts if d.get("severity") == "hard"
        ]
        # 真因修复批 item 4（看板 bug：fix_attempt 恒为 1）：critic_node 自己的
        # diff 从不含 plan_attempt——那是 planner 节点专属字段（见 planner.py
        # 的返回 dict），critic 只读它，不重新写它，`diff.get("plan_attempt")`
        # 因此对 critic 这个 diff 永远是 None。ctx.last_plan_attempt 是
        # EmitContext 为 DONE payload 累积的同一个值（_emit_context.py
        # update_accum_from_diff），run_graph_stream 主循环里 planner 节点总在
        # critic 节点之前完成一次 astream chunk（各自独立的 LangGraph 步，
        # 见 sse_adapter.py 的 dispatch 循环），累积必然已经是最新值——直接读它，
        # 不必奢望 critic 自己的 diff 里凭空多出一个它从未写过的字段。
        attempt = ctx.last_plan_attempt or 1
        return [
            ctx.emit(
                SseEventType.CRITIC_VIOLATIONS,
                {"violations": critical_only, "fix_attempt": attempt},
            ),
            # 2. 兼容旧前端：再推一条 REPLAN_TRIGGERED
            ctx.emit(
                SseEventType.REPLAN_TRIGGERED,
                {
                    "reason": "critic_hard_violation",
                    "from_tool": "critics_v2",
                    "violations": [
                        d.get("message", "") for d in critical_only[:3]
                    ],
                },
            ),
        ]
    return [
        ctx.emit(
            SseEventType.AGENT_THOUGHT,
            {"text": f"方案验证通过（{len(violations)} 条提示）。"},
        )
    ]


_STRATEGY_TO_LABEL = {
    "llm_backprompt": ("llm_first", "llm_backprompt", "LLM 修正重出"),
    "ils_fallback": ("llm_first", "ils", "LLM 失败，切换 ILS 算法兜底"),
    "give_up": ("ils", "rule", "ILS 也失败，回 rule planner 兜底"),
}


def emit_replan_router(ctx: EmitContext, diff: dict[str, Any]) -> list[SseEvent]:
    strategy = diff.get("replan_strategy")
    out: list[SseEvent] = []
    # 推 PLAN_FALLBACK 让前端展示降级链路
    if strategy in _STRATEGY_TO_LABEL:
        from_, to_, reason = _STRATEGY_TO_LABEL[strategy]
        out.append(
            ctx.emit(SseEventType.PLAN_FALLBACK, {"from": from_, "to": to_, "reason": reason})
        )
    out.append(
        ctx.emit(SseEventType.AGENT_THOUGHT, {"text": f"切换重排策略：{strategy}"})
    )
    return out


_STAGE_LABEL_FOR_FALLBACK = {
    "rule": "rule planner 安全兜底",
    "give_up": "保留当前最佳方案（已尝试所有策略）",
}


def emit_ils_replan(ctx: EmitContext, diff: dict[str, Any]) -> list[SseEvent]:
    out: list[SseEvent] = [
        ctx.emit(SseEventType.AGENT_THOUGHT, {"text": "ILS 算法兜底重排中……"})
    ]
    # spec execution-quality-review M2：把 ils_replan_node 写回的
    # fallback_chain 增量推 PLAN_FALLBACK，让评委看到「ILS → rule」/「rule → give_up」整链
    new_chain = diff.get("fallback_chain") or []
    if len(new_chain) > len(ctx.last_fallback_chain):
        # 仅推增量（避免重复）
        for hop_dict in new_chain[len(ctx.last_fallback_chain):]:
            from_stage = hop_dict.get("from_stage", "ils")
            to_stage = hop_dict.get("to_stage", "give_up")
            reason = hop_dict.get("reason", "")
            out.append(
                ctx.emit(
                    SseEventType.PLAN_FALLBACK,
                    {"from": from_stage, "to": to_stage, "reason": reason},
                )
            )
            # 同时推一条 agent_thought 让评委看到中文文案
            stage_label = _STAGE_LABEL_FOR_FALLBACK.get(to_stage, to_stage)
            out.append(
                ctx.emit(
                    SseEventType.AGENT_THOUGHT,
                    {"text": f"已切换 {stage_label}"},
                )
            )
    # 如果 ils_replan 写回了 itinerary（rule 兜底成功），推进度提示
    if diff.get("itinerary") is not None and not diff.get("has_critical", True):
        out.append(
            ctx.emit(
                SseEventType.AGENT_THOUGHT,
                {"text": "兜底方案已就绪，进入文案生成"},
            )
        )
    return out


def emit_assemble(ctx: EmitContext, diff: dict[str, Any]) -> list[SseEvent]:
    """assemble 只是中间状态（critic 还要验），不推 ITINERARY_READY。

    最终方案由 narrate 节点统一推送（critic 通过或 give_up 后才是定稿）。
    这里只做一次状态提示，让前端 dock 知道蓝图已拼好正在验证。
    """
    itin = diff.get("itinerary")
    if itin is None:
        return []
    out: list[SseEvent] = []
    # 兜底警示：edge_v1 节点缺坐标（assemble 找不到对应 mock 数据）
    # 只检查 target_kind ∈ {poi, restaurant}（home 节点本来就无坐标）
    miss_coord_count = sum(
        1
        for n in itin.nodes
        if n.target_kind in ("poi", "restaurant")
        and (n.lat is None or n.lng is None)
    )
    if miss_coord_count > 0:
        out.append(
            ctx.emit(
                SseEventType.AGENT_THOUGHT,
                {
                    "text": (
                        f"⚠ 有 {miss_coord_count} 个节点未能定位坐标"
                        f"（mock 数据可能未覆盖该 id），"
                        f"地图上对应节点不会标注。"
                    ),
                },
            )
        )
    out.append(
        ctx.emit(
            SseEventType.AGENT_THOUGHT,
            {"text": "蓝图已拼成行程草稿，正在验证可行性……"},
        )
    )
    return out


def emit_finalize_plan(ctx: EmitContext, diff: dict[str, Any]) -> list[SseEvent]:
    """finalize_plan 节点（体感编排批 P1："先出方案，后出文案"）：critic 通过 /
    replan give_up / ils 成功三条路径统一先到这里，再进 narrate。

    在这里推 ITINERARY_READY——不必等后面的 narrate 把叙事 LLM（数秒到数十秒）
    跑完，方案本身在 `finalize_plan` 完成的这一刻就已经定稿（含规则标题、
    pending_actions、decision_trace 收尾，见 `agent.graph.nodes.finalize_plan`
    docstring）。narrate 后面即便还会用 LLM 换一个更精彩的标题，也只走
    AGENT_NARRATION 的 `title` 兄弟字段更新前端已展示的文本，不重推 READY。

    契约不变：ITINERARY_READY 仍是纯 Itinerary dump（见 `emit_narrate` 曾经的
    深审教训——该 payload 有"整体=Itinerary dump"的隐含契约，chat.py 会话同步
    把它整体镜像进 SESSION_STORE 投影端口，确认流/房间快照拿
    `Itinerary.model_validate`（extra_forbidden）反序列化，混入兄弟字段会直接
    炸掉确认）。本函数只做"有 itinerary 才推"的组装，不重算业务逻辑。
    """
    itin = diff.get("itinerary")
    out: list[SseEvent] = []
    if itin is not None and not ctx.itinerary_emitted:
        payload: dict[str, Any] = (
            itin.model_dump() if hasattr(itin, "model_dump") else itin
        )
        out.append(ctx.emit(SseEventType.ITINERARY_READY, payload))
        ctx.itinerary_emitted = True
    return out


def emit_narrate(ctx: EmitContext, diff: dict[str, Any]) -> list[SseEvent]:
    """narrate 节点：叙事 LLM（narration/LLM 标题/node_chips）+ AGENT_NARRATION 组装。

    体感编排批 P1："先出方案，后出文案"——ITINERARY_READY 已由上一个节点
    `finalize_plan`（见 `emit_finalize_plan`）推送，本函数不再重推（
    `ctx.itinerary_emitted` 由它置位）。narrate 只在这里推 AGENT_NARRATION。

    ADR-0013 F-3：`node_actions`（narrate_node 算好的「节点调整按钮 + 具名
    备选」，见 `agent.graph.nodes.narrate._build_node_actions`）挂在
    **AGENT_NARRATION** payload 的兄弟字段（与 D-7 的 messages 同一"附加通道"
    先例）。【深审改址(主代理),原挂 ITINERARY_READY 兄弟字段——集成实测炸雷:
    该 payload 存在隐含契约"整体=Itinerary dump"——chat.py 会话同步把它整体
    镜像进 SESSION_STORE 投影端口,确认流(graph_confirm)/房间快照拿它
    `Itinerary.model_validate`(extra_forbidden)反序列化,兄弟字段直接
    ValidationError 炸掉确认。ITINERARY_READY 保持纯 Itinerary dump;一切
    附加通道走 AGENT_NARRATION,那里的 payload 无人反序列化成模型。】

    体感编排批 P1（新增）：`title`——narrate_node 可能用 LLM 换出一个比
    `finalize_plan` 的规则标题更精彩的版本（写进它自己 diff 里的
    `itinerary.summary`）。ITINERARY_READY 已经推过一次（携带规则标题），
    这里不重推整份方案，只在 summary 确实变了时，把新标题作为 `title` 兄弟
    字段挂上，前端据此原地更新已展示的方案卡大标题（不需要 AgentState 新增
    顶层字段——`itinerary` 本身已是声明过的 state 字段，比较"这次 diff 里的
    summary" 与 "ctx.final_itinerary 里累积的上一版 summary"（即 finalize_plan
    留下的值——dispatch 顺序保证 `ctx.final_itinerary` 在 narrate 的 emit 跑
    时还没被 narrate 自己的 diff 更新，见 `sse_adapter.run_graph_stream`）
    就够，不必在 narrate_node 的返回 diff 里另开一个"title"顶层键）。

    本函数只做"有内容才加字段"的组装,不重算业务逻辑。
    """
    text = diff.get("narration")
    out: list[SseEvent] = []
    if text:
        narration_payload: dict[str, Any] = {"text": text, "stage": "stream"}
        # D-7（ADR-0010 决策 11 / ADR-0011 决策 5「统一 agent 消息面」）：
        # narrate_node 把 state.advisories 原样透传进自己的 diff（见 narrate.py），
        # 这里转成前端可渲染的结构化条目——形状故意与「统一 agent 消息面」对齐
        # （kind/code/text 三要素），future 澄清消息可复用同一个列表字段。
        advisories = diff.get("advisories") or []
        if advisories:
            narration_payload["messages"] = [
                {"kind": "advisory", "code": a.get("code"), "text": a.get("message")}
                for a in advisories
                if a.get("message")
            ]
        # ADR-0013 F-3:节点调整按钮+具名备选(改址说明见本函数 docstring)
        node_actions = diff.get("node_actions")
        if node_actions:
            narration_payload["node_actions"] = node_actions
        # ADR-0014 决策 2（G-2）配套三件：真正"hard 卡死"（itinerary=None，
        # 见 `nodes.narrate.narrate_node` 的 give_up 兜底分支）时的放宽建议
        # chips——"无内容不加字段"同一纪律，只在非空时才挂。
        give_up_chips = diff.get("give_up_chips")
        if give_up_chips:
            narration_payload["chips"] = give_up_chips
        # 体感编排批 P1：LLM 标题更新（说明见本函数 docstring）
        new_summary = getattr(diff.get("itinerary"), "summary", None)
        prev_summary = getattr(ctx.final_itinerary, "summary", None)
        if new_summary and new_summary != prev_summary:
            narration_payload["title"] = new_summary
        out.append(ctx.emit(SseEventType.AGENT_NARRATION, narration_payload))
    # 注：MEMORY_PERSISTED 推送已迁到确认流（2026-05-25）——execute_finalize_node
    # 产出 memory_status，由 api/_streams/graph_confirm.py 直接拼 SSE 推送，不再
    # 经图节点 emit（execute_finalize 已退注册，见 emit_execute_finalize 删除说明）
    # 产品语义：用户确认预约后才记住偏好；方案就绪不应触发
    return out


# 注：emit_execute_finalize 已删除（ADR-0012 决策 2「结构诚实」）——execute_finalize
# 已从图节点退注册（build.py 不再 add_node），这个 emit 函数只为图内节点事件准备，
# 节点退注册后是永远走不到的死分支。确认流（/chat/confirm）自己拼 SSE 事件
# （见 api/_streams/graph_confirm.py），不经过 run_graph_stream / 本 dispatch。
