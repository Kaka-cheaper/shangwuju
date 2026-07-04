"""agent.graph.sse_adapter —— LangGraph astream → 现有 SseEvent 序列。

让 main.py 的 /chat/turn 端点直接拿到与旧 ReAct 路径完全一致的事件序列：
intent_parsed / tool_call_start / tool_call_end / replan_triggered / agent_thought /
chitchat_reply / itinerary_ready / agent_narration / done / stream_error。

LangGraph astream 模式 = "updates"：每个节点完成后产出 {node_name: state_diff}。
本适配层订阅 updates，按节点名映射到 SSE 事件。

文件结构（spec code-modularization-refactor H3）：
- _emit_context.py   EmitContext 可变状态容器（seq / 累积变量 / emit 工厂）
- _emit_handlers.py  每个 LangGraph 节点 → SSE 事件的 emit_xxx 函数
- 本文件             run_graph_stream 主函数：dispatch + yield from + 顶部心跳 / 末尾 DONE

【spec planning-quality-deep-review R6+R7（Task 6 + Agent H P0-H2）】
- DONE event payload 携带 6 字段总结：
  final_strategy / plan_attempts / critic_attempt_count / fallback_hops_count /
  total_ms / has_itinerary
  让前端 / 评委一眼看到本轮 turn 的关键统计（对应 demo 评分项「Agent 行为可见性」）
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from agent.graph.build import get_compiled_graph
from agent.graph.state import make_initial_state
from schemas.sse import SseEvent, SseEventType

from ._emit_context import EmitContext, make_event, now_ms
from ._emit_handlers import (
    emit_assemble,
    emit_critic,
    emit_fanout_worker,
    emit_finalize_plan,
    emit_ils_replan,
    emit_intent,
    emit_narrate,
    emit_planner,
    emit_refiner,
    emit_replan_router,
    emit_router,
)


# ============================================================
# 节点名 → emit 函数 dispatch（fan-out worker 三个共享 emit_fanout_worker）
# ============================================================

_FANOUT_WORKERS = {
    "search_pois_worker",
    "search_restaurants_worker",
    "get_user_profile_worker",
}


# ============================================================
# 核心：astream → SseEvent 流
# ============================================================


async def run_graph_stream(
    *,
    user_input: str,
    session_id: str,
    user_id: str = "demo_user",
    scenario_id: str | None = None,
    planner_mode: str | None = None,
) -> AsyncIterator[SseEvent]:
    """跑一次 LangGraph，按节点完成顺序推送 SseEvent。

    main.py 直接 yield 本生成器的结果即可。

    Args:
        planner_mode: "rule" / "llm" / None。
            - "rule" 走纯规则路径（不调 LLM；毫秒级出方案；spec interaction-experience-review）
            - "llm"  走 LLM-First Planner（默认；让大模型自己拿主意）
            - None   保持 LangGraph 主架构默认（向后兼容；当前等同 "llm"）
    """
    graph = get_compiled_graph()
    initial = make_initial_state(
        user_input=user_input,
        user_id=user_id,
        session_id=session_id,
        scenario_id=scenario_id,
        planner_mode=planner_mode,
    )
    config: dict[str, Any] = {"configurable": {"thread_id": session_id}}

    ctx = EmitContext()

    # 心跳（防 8s 首字节超时）
    yield ctx.emit(SseEventType.AGENT_THOUGHT, {"text": "正在理解你的需求……"})

    async for ev in _drive_graph_stream(graph, initial, config, user_input, ctx):
        yield ev


async def run_graph_resume_stream(
    *,
    session_id: str,
    user_input: str,
) -> AsyncIterator[SseEvent]:
    """续跑入口（房间重排根治批，2026-07-04）：对已注入状态的线程 `astream(None)`。

    LangGraph 语义：initial=None = "从该线程 checkpoint 的 next 节点续跑"，不重置
    任何 state。调用方（collab/room.py::_replan_with_refiner）已用
    `aupdate_state(as_node="refiner")` 把"反馈已合并"的状态写进线程——router/refiner
    都不再执行，义务判定在房间层 route_turn 已经做过，不给全新 session 的 router
    第二次误判的机会（点火冒烟 H3 实锤的病灶）。配方可行性经
    scripts/spike_room_resume.py 实证（终态与单人反馈轮全等、核心事件逐字节等价）。

    与 run_graph_stream 的差异只有两处：
    - initial=None（续跑，不构造 make_initial_state）；
    - 不推「正在理解你的需求……」心跳——那条心跳填的是单人首轮 router 起跑前的
      首字节空窗；续跑场景调用方在进入本函数前已合成广播 4 条前奏事件
      （agent_thought/refinement_start/refinement_done/intent_parsed），空窗不存在，
      再推一条"正在理解"反而出现在"已合并完反馈"之后，时序话术自相矛盾。

    user_input 仅供共享 dispatch 的 emit_router 分支签名使用（续跑不会经过 router，
    该分支实际不触发），不为此分叉第二份 dispatch。
    """
    graph = get_compiled_graph()
    config: dict[str, Any] = {"configurable": {"thread_id": session_id}}
    ctx = EmitContext()
    async for ev in _drive_graph_stream(graph, None, config, user_input, ctx):
        yield ev


async def _drive_graph_stream(
    graph: Any,
    initial: Any,
    config: dict[str, Any],
    user_input: str,
    ctx: EmitContext,
) -> AsyncIterator[SseEvent]:
    """astream → dispatch → DONE 的共享主体（run_graph_stream 原封抽出，行为不变；
    抽出的唯一动机是让续跑入口 run_graph_resume_stream 复用同一份 dispatch，
    不出现第二份手工同步的 if/elif 链）。"""
    try:
        async for chunk in graph.astream(
            initial, config=config, stream_mode="updates"
        ):
            # chunk 形如 {"router": {...}} 或 {"search_pois_worker": {...}}
            for node_name, node_diff in chunk.items():
                if node_diff is None:
                    continue

                # ---- dispatch 到对应 emit 函数 ----
                if node_name == "router":
                    events = emit_router(ctx, node_diff, user_input)
                elif node_name == "intent":
                    events = emit_intent(ctx, node_diff)
                elif node_name == "refiner":
                    events = emit_refiner(ctx, node_diff)
                elif node_name in _FANOUT_WORKERS:
                    events = emit_fanout_worker(ctx, node_name, node_diff)
                elif node_name == "planner":
                    events = emit_planner(ctx, node_diff)
                elif node_name == "critic":
                    events = emit_critic(ctx, node_diff)
                elif node_name == "replan_router":
                    events = emit_replan_router(ctx, node_diff)
                elif node_name == "ils_replan":
                    events = emit_ils_replan(ctx, node_diff)
                elif node_name == "assemble":
                    events = emit_assemble(ctx, node_diff)
                elif node_name == "finalize_plan":
                    events = emit_finalize_plan(ctx, node_diff)
                elif node_name == "narrate":
                    events = emit_narrate(ctx, node_diff)
                else:
                    # 未识别节点：跳过事件转换，但仍累积统计
                    events = []

                for ev in events:
                    yield ev

                # 累积 DONE payload 需要的字段（含未识别节点也要更新 last_state）
                ctx.update_accum_from_diff(node_diff)

    except Exception as e:  # noqa: BLE001
        # 防御性：把完整 traceback 写日志，避免只看到「detail = 截断后的无意义碎片」
        # 历史教训：用户截图显示 "graph_execution_failed: MEMORY_PERSISTED" 这种碎片
        # 难以定位真因（实际是 backend dev 进程未重启 / Python 模块编译缓存）
        import logging
        import traceback as _tb

        logging.getLogger(__name__).exception(
            "graph stream raised: %s: %s", type(e).__name__, str(e)[:200]
        )
        # SSE detail 写完整 type + message（不再只截 str(e) —— 会把
        # MEMORY_PERSISTED 这种枚举名 / KeyError 的 key 名暴露当成"错误内容"误导）
        detail = f"{type(e).__name__}: {str(e)[:300]}"
        # 加 1 行 traceback 摘要（最近 1 帧函数名 + 行号），仍控制在 SSE payload 体积内
        try:
            tb_summary = _tb.format_exc(limit=1).splitlines()[-2:]
            tb_short = " | ".join(s.strip() for s in tb_summary)
            detail = f"{detail} @ {tb_short[:200]}"
        except Exception:  # pragma: no cover
            pass
        yield ctx.emit(
            SseEventType.STREAM_ERROR,
            {"reason": "graph_execution_failed", "detail": detail[:500]},
        )

    # 流结束（spec R6：DONE payload 加 6 字段总结）
    final_strategy = "llm_first"
    has_itinerary = False
    if ctx.final_itinerary is not None:
        has_itinerary = True
        trace = getattr(ctx.final_itinerary, "decision_trace", None)
        if trace is not None:
            final_strategy = (
                getattr(trace, "final_strategy", "llm_first") or "llm_first"
            )
            # 优先用 trace 上的 fallback_chain（已和最终 itinerary 一致）
            trace_chain = getattr(trace, "fallback_chain", None)
            if trace_chain:
                ctx.last_fallback_chain = list(trace_chain)
            trace_attempts = getattr(trace, "critic_attempts", None)
            if trace_attempts:
                ctx.last_critic_attempts = list(trace_attempts)

    done_payload = {
        "final_strategy": final_strategy,
        "plan_attempts": ctx.last_plan_attempt,
        "critic_attempt_count": len(ctx.last_critic_attempts),
        "fallback_hops_count": len(ctx.last_fallback_chain),
        "total_ms": now_ms() - ctx.start_ms,
        "has_itinerary": has_itinerary,
    }
    yield ctx.emit(SseEventType.DONE, done_payload)


# ============================================================
# 向后兼容：保留旧的 _now_ms / _ev 名字（被外部 test 引用的话不破）
# ============================================================

_now_ms = now_ms


def _ev(seq: int, type_: SseEventType, payload: dict[str, Any] | None = None) -> SseEvent:
    """旧版 _ev 工厂（向后兼容）。新代码直接用 EmitContext.emit。"""
    return make_event(seq, type_, payload)
