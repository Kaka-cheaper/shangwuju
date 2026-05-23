"""agent.graph.sse_adapter —— LangGraph astream → 现有 SseEvent 序列。

让 main.py 的 /chat/turn 端点直接拿到与旧 ReAct 路径完全一致的事件序列：
intent_parsed / tool_call_start / tool_call_end / replan_triggered / agent_thought /
chitchat_reply / itinerary_ready / agent_narration / done / stream_error。

LangGraph astream 模式 = "updates"：每个节点完成后产出 {node_name: state_diff}。
本适配层订阅 updates，按节点名映射到 SSE 事件。

关键映射：
- router 完成 → 如果是 chitchat 类，推 chitchat_reply + done
- intent 完成 → 推 intent_parsed
- search_*_worker 完成 → 推 tool_call_start + tool_call_end（合成）
- planner 完成 → 推 agent_thought（plan rationale）
- critic 完成 + has_critical → 推 replan_triggered
- assemble 完成 + 有 itinerary → 推 itinerary_ready
- narrate 完成 → 推 agent_narration
- 流结束 → 推 done
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

from agent.graph.build import get_compiled_graph
from agent.graph.state import AgentState, make_initial_state
from schemas.sse import SseEvent, SseEventType


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ev(seq: int, type_: SseEventType, payload: dict[str, Any] | None = None) -> SseEvent:
    return SseEvent(
        type=type_,
        seq=seq,
        payload=payload or {},
        timestamp_ms=_now_ms(),
    )


# ============================================================
# 核心：astream → SseEvent 流
# ============================================================

async def run_graph_stream(
    *,
    user_input: str,
    session_id: str,
    user_id: str = "demo_user",
    scenario_id: str | None = None,
) -> AsyncIterator[SseEvent]:
    """跑一次 LangGraph，按节点完成顺序推送 SseEvent。

    main.py 直接 yield 本生成器的结果即可。
    """
    graph = get_compiled_graph()
    initial = make_initial_state(
        user_input=user_input,
        user_id=user_id,
        session_id=session_id,
        scenario_id=scenario_id,
    )
    config: dict[str, Any] = {"configurable": {"thread_id": session_id}}

    seq = 0

    # 心跳（防 8s 首字节超时）
    yield _ev(seq, SseEventType.AGENT_THOUGHT, {"text": "正在理解你的需求……"})
    seq += 1

    # 跟踪是否已经推过 itinerary_ready / chitchat_reply（避免重复）
    itinerary_emitted = False
    chitchat_emitted = False
    last_state: AgentState | None = None

    try:
        async for chunk in graph.astream(
            initial, config=config, stream_mode="updates"
        ):
            # chunk 形如 {"router": {...}} 或 {"search_pois_worker": {...}}
            for node_name, node_diff in chunk.items():
                if node_diff is None:
                    continue

                # ---- router ----
                if node_name == "router":
                    decision = node_diff.get("router_decision")
                    route_kind = node_diff.get("route_kind")
                    if route_kind == "planning":
                        yield _ev(
                            seq,
                            SseEventType.AGENT_THOUGHT,
                            {"text": "好的，让我帮你规划一下。"},
                        )
                        seq += 1
                    elif route_kind == "feedback":
                        yield _ev(
                            seq,
                            SseEventType.AGENT_THOUGHT,
                            {"text": "收到反馈，正在调整……"},
                        )
                        seq += 1
                        # refiner 开始信号（兼容旧前端）
                        yield _ev(
                            seq,
                            SseEventType.REFINEMENT_START,
                            {"feedback_text": user_input},
                        )
                        seq += 1
                    elif decision is not None and route_kind != "planning":
                        # chitchat / meta / emotional / off_topic / ambiguous → 直接推
                        yield _ev(
                            seq,
                            SseEventType.CHITCHAT_REPLY,
                            decision.model_dump(),
                        )
                        seq += 1
                        chitchat_emitted = True

                # ---- intent ----
                elif node_name == "intent":
                    intent = node_diff.get("intent")
                    if intent is not None:
                        yield _ev(
                            seq,
                            SseEventType.INTENT_PARSED,
                            intent.model_dump(),
                        )
                        seq += 1

                # ---- refiner ----
                elif node_name == "refiner":
                    intent = node_diff.get("intent")
                    if intent is not None:
                        yield _ev(
                            seq,
                            SseEventType.REFINEMENT_DONE,
                            {
                                "refined_intent": intent.model_dump(),
                                "changed_fields": [],  # 保持向后兼容；详细字段差由前端比对
                                "refiner_note": "已合并你的反馈，正在重新规划。",
                            },
                        )
                        seq += 1
                        # 然后用新意图重推 intent_parsed 让前端 IntentSummary 刷新
                        yield _ev(
                            seq,
                            SseEventType.INTENT_PARSED,
                            intent.model_dump(),
                        )
                        seq += 1

                # ---- 4 个搜索 worker → 合成 tool_call_start + tool_call_end ----
                elif node_name in (
                    "search_pois_worker",
                    "search_restaurants_worker",
                    "get_user_profile_worker",
                ):
                    tool_name = {
                        "search_pois_worker": "search_pois",
                        "search_restaurants_worker": "search_restaurants",
                        "get_user_profile_worker": "get_user_profile",
                    }[node_name]
                    yield _ev(
                        seq,
                        SseEventType.TOOL_CALL_START,
                        {"tool": tool_name, "input": {}},
                    )
                    seq += 1
                    # 合成 end（结果数量摘要）
                    out_summary: dict[str, Any] = {"success": True}
                    if "pois" in node_diff:
                        out_summary["count"] = len(node_diff["pois"])
                    elif "restaurants" in node_diff:
                        out_summary["count"] = len(node_diff["restaurants"])
                    elif "user_profile" in node_diff:
                        out_summary["found"] = node_diff["user_profile"] is not None
                    # Step 6：tag relaxation 透传（split per worker key）
                    relaxed = (
                        node_diff.get("pois_relaxed_tags")
                        or node_diff.get("restaurants_relaxed_tags")
                        or []
                    )
                    if relaxed:
                        out_summary["relaxed_tags"] = list(relaxed)
                    yield _ev(
                        seq,
                        SseEventType.TOOL_CALL_END,
                        {"tool": tool_name, "output": out_summary, "duration_ms": 0},
                    )
                    seq += 1

                # ---- planner ----
                elif node_name == "planner":
                    weights = node_diff.get("weights")
                    blueprint = node_diff.get("blueprint")
                    attempt = node_diff.get("plan_attempt", 1)
                    # plan_attempt > 1 说明这是 critic backprompt 重做
                    if attempt > 1:
                        # critic_feedback_text 在 state 中而非 diff 中——
                        # diff 是 planner 节点本次返回的字段，若 planner 不更新它，
                        # 这里读 None 也无妨；至少把 attempt 信号推出去
                        yield _ev(
                            seq,
                            SseEventType.CRITIC_FIX_ATTEMPT,
                            {
                                "attempt": attempt,
                                "feedback_text": "（详见上一条 critic_violations）",
                            },
                        )
                        seq += 1
                    if weights is not None:
                        yield _ev(
                            seq,
                            SseEventType.AGENT_THOUGHT,
                            {
                                "text": (
                                    f"出 plan 第 {attempt} 次（权重 {weights.summary()}）"
                                ),
                            },
                        )
                        seq += 1
                    if blueprint is not None and weights is not None:
                        # edge_v1：蓝图里只有 mid nodes（不含 home 首尾）。
                        yield _ev(
                            seq,
                            SseEventType.AGENT_THOUGHT,
                            {
                                "text": (
                                    f"蓝图 {len(blueprint.nodes)} 个节点：{blueprint.rationale[:80]}"
                                ),
                            },
                        )
                        seq += 1

                # ---- critic ----
                elif node_name == "critic":
                    has_critical = node_diff.get("has_critical")
                    violations = node_diff.get("violations") or []
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
                                        "code": getattr(getattr(v, "code", None), "value", str(getattr(v, "code", ""))),
                                        "severity": getattr(getattr(v, "severity", None), "value", str(getattr(v, "severity", ""))),
                                        "message": getattr(v, "message", str(v)),
                                        "field_path": getattr(v, "field_path", ""),
                                    }
                                )
                        # 仅推 critical（warning 不进 SSE，避免噪声）
                        critical_only = [
                            d for d in violation_dicts
                            if d.get("severity") == "critical"
                        ]
                        attempt = node_diff.get("plan_attempt") or 1
                        yield _ev(
                            seq,
                            SseEventType.CRITIC_VIOLATIONS,
                            {
                                "violations": critical_only,
                                "fix_attempt": attempt,
                            },
                        )
                        seq += 1
                        # 2. 兼容旧前端：再推一条 REPLAN_TRIGGERED
                        yield _ev(
                            seq,
                            SseEventType.REPLAN_TRIGGERED,
                            {
                                "reason": "critic_hard_violation",
                                "from_tool": "critics_v2",
                                "violations": [
                                    d.get("message", "") for d in critical_only[:3]
                                ],
                            },
                        )
                        seq += 1
                    else:
                        yield _ev(
                            seq,
                            SseEventType.AGENT_THOUGHT,
                            {"text": f"方案验证通过（{len(violations)} 条提示）。"},
                        )
                        seq += 1

                # ---- replan_router ----
                elif node_name == "replan_router":
                    strategy = node_diff.get("replan_strategy")
                    # 推 PLAN_FALLBACK 让前端展示降级链路
                    strategy_to_label = {
                        "llm_backprompt": ("llm_first", "llm_backprompt", "LLM 修正重出"),
                        "ils_fallback": ("llm_first", "ils", "LLM 失败，切换 ILS 算法兜底"),
                        "give_up": ("ils", "rule", "ILS 也失败，回 rule planner 兜底"),
                    }
                    if strategy in strategy_to_label:
                        from_, to_, reason = strategy_to_label[strategy]
                        yield _ev(
                            seq,
                            SseEventType.PLAN_FALLBACK,
                            {"from": from_, "to": to_, "reason": reason},
                        )
                        seq += 1
                    yield _ev(
                        seq,
                        SseEventType.AGENT_THOUGHT,
                        {"text": f"切换重排策略：{strategy}"},
                    )
                    seq += 1

                # ---- ils_replan ----
                elif node_name == "ils_replan":
                    yield _ev(
                        seq,
                        SseEventType.AGENT_THOUGHT,
                        {"text": "ILS 算法兜底重排中……"},
                    )
                    seq += 1

                # ---- assemble ----
                # 注：不推 ITINERARY_READY—— assemble 只是中间状态（critic 还要验），
                # 最终方案由 narrate 节点统一推送（critic 通过或 give_up 后才是定稿）。
                # 这里只做一次状态提示，让前端 dock 知道蓝图已拼好正在验证。
                elif node_name == "assemble":
                    itin = node_diff.get("itinerary")
                    if itin is not None:
                        # 兜底警示：edge_v1 节点缺坐标（assemble 找不到对应 mock 数据）
                        # 只检查 target_kind ∈ {poi, restaurant}（home 节点本来就无坐标）
                        miss_coord_count = sum(
                            1
                            for n in itin.nodes
                            if n.target_kind in ("poi", "restaurant")
                            and (n.lat is None or n.lng is None)
                        )
                        if miss_coord_count > 0:
                            yield _ev(
                                seq,
                                SseEventType.AGENT_THOUGHT,
                                {
                                    "text": (
                                        f"⚠ 有 {miss_coord_count} 个节点未能定位坐标"
                                        f"（mock 数据可能未覆盖该 id），"
                                        f"地图上对应节点不会标注。"
                                    ),
                                },
                            )
                            seq += 1
                        yield _ev(
                            seq,
                            SseEventType.AGENT_THOUGHT,
                            {"text": "蓝图已拼成行程草稿，正在验证可行性……"},
                        )
                        seq += 1

                # ---- narrate ----
                # narrate 节点是流程的真正终点：critic 通过 → narrate / replan give_up → narrate。
                # 只在这里推一次 ITINERARY_READY，让前端拿到的就是定稿（含完整 trace）。
                elif node_name == "narrate":
                    text = node_diff.get("narration")
                    # 从最新 state 取 itinerary 推前端（narrate 自己不改 itinerary）
                    final_itin = node_diff.get("itinerary") or (
                        last_state.get("itinerary") if last_state else None
                    )
                    if final_itin is not None and not itinerary_emitted:
                        yield _ev(
                            seq,
                            SseEventType.ITINERARY_READY,
                            final_itin.model_dump() if hasattr(final_itin, "model_dump") else final_itin,
                        )
                        seq += 1
                        itinerary_emitted = True
                    if text:
                        yield _ev(
                            seq,
                            SseEventType.AGENT_NARRATION,
                            {"text": text, "stage": "stream"},
                        )
                        seq += 1

                # ---- execute_finalize ----
                elif node_name == "execute_finalize":
                    itin = node_diff.get("itinerary")
                    if itin is not None:
                        yield _ev(
                            seq,
                            SseEventType.ITINERARY_READY,
                            itin.model_dump(),
                        )
                        seq += 1

                # 累积 last_state（用于 done 时的最终 itinerary 兜底）
                last_state = node_diff

    except Exception as e:  # noqa: BLE001
        yield _ev(
            seq,
            SseEventType.STREAM_ERROR,
            {"reason": "graph_execution_failed", "detail": str(e)[:200]},
        )
        seq += 1

    # 流结束
    yield _ev(seq, SseEventType.DONE, {})
