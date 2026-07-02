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
        # chitchat / meta / emotional / off_topic / ambiguous → 直接推
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
        attempt = diff.get("plan_attempt") or 1
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


def emit_narrate(ctx: EmitContext, diff: dict[str, Any]) -> list[SseEvent]:
    """narrate 节点是流程的真正终点：critic 通过 → narrate / replan give_up → narrate。

    只在这里推一次 ITINERARY_READY，让前端拿到的就是定稿（含完整 trace）。

    ADR-0013 F-3：`node_actions`（narrate_node 算好的「节点调整按钮 + 具名
    备选」，见 `agent.graph.nodes.narrate._build_node_actions`）挂在
    **AGENT_NARRATION** payload 的兄弟字段（与 D-7 的 messages 同一"附加通道"
    先例）。【深审改址(主代理),原挂 ITINERARY_READY 兄弟字段——集成实测炸雷:
    该 payload 存在隐含契约"整体=Itinerary dump"——chat.py 会话同步把它整体
    镜像进 SESSION_STORE 投影端口,确认流(graph_confirm)/房间快照拿它
    `Itinerary.model_validate`(extra_forbidden)反序列化,兄弟字段直接
    ValidationError 炸掉确认。ITINERARY_READY 保持纯 Itinerary dump;一切
    附加通道走 AGENT_NARRATION,那里的 payload 无人反序列化成模型。】
    本函数只做"有内容才加字段"的组装,不重算业务逻辑。
    """
    text = diff.get("narration")
    # 从最新 state 取 itinerary 推前端（narrate 自己不改 itinerary）
    final_itin = diff.get("itinerary") or (
        ctx.last_state.get("itinerary") if ctx.last_state else None
    )
    out: list[SseEvent] = []
    if final_itin is not None and not ctx.itinerary_emitted:
        payload: dict[str, Any] = (
            final_itin.model_dump() if hasattr(final_itin, "model_dump") else final_itin
        )
        out.append(ctx.emit(SseEventType.ITINERARY_READY, payload))
        ctx.itinerary_emitted = True
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
