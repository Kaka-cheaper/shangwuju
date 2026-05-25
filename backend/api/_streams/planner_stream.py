"""真 planner 链路：意图解析 → plan_itinerary_with_mode → 实时推送 tracer 事件。

来自 main.py 拆分（spec code-modularization-refactor H1-final）：
- _intent_via_llm：意图解析（带兜底）
- _planner_stream：主流式生成器（异步等待真 planner + 后台线程消费 Tracer）
- _record_to_sse / _tracer_to_events / _stream_tracer_events：Tracer → SseEvent 转换

设计纪律：
- 所有 LLM 调用放后台线程，主线程 yield 心跳防 8s 首字节超时
- 异常一律兜底，不让 demo 现场翻车
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from schemas import (
    Companion,
    IntentExtraction,
    SseEvent,
    SseEventType,
)

from .._session_store import SESSION_STORE
from .._sse_helpers import delay as _delay
from .._sse_helpers import now_ms as _now_ms
from .models import ChatStreamRequest


# Tracer 事件 type → SseEventType 映射
_TRACER_TO_SSE: dict[str, SseEventType] = {
    "intent_parsed": SseEventType.INTENT_PARSED,
    "tool_call_start": SseEventType.TOOL_CALL_START,
    "tool_call_end": SseEventType.TOOL_CALL_END,
    "replan_triggered": SseEventType.REPLAN_TRIGGERED,
    "agent_thought": SseEventType.AGENT_THOUGHT,
    "itinerary_ready": SseEventType.ITINERARY_READY,
    "stream_error": SseEventType.STREAM_ERROR,
}


def _tracer_to_events(tracer: Any, starting_seq: int = 0) -> list[SseEvent]:
    """把 Tracer 收集的内部事件转成 SseEvent 列表。

    未知 type 会被丢弃（不做兜底事件——避免误推）。
    """
    out: list[SseEvent] = []
    seq = starting_seq
    for record in tracer.records:
        sse_type = _TRACER_TO_SSE.get(record.type)
        if sse_type is None:
            continue
        out.append(
            SseEvent(
                type=sse_type,
                seq=seq,
                payload=dict(record.payload),
                timestamp_ms=record.timestamp_ms,
            )
        )
        seq += 1
    return out


async def _stream_tracer_events(
    events: list[SseEvent],
    *,
    delay_ms: int = 200,
) -> AsyncIterator[SseEvent]:
    """把 tracer 事件按节奏推给前端，让评委能看清每一步。"""
    for ev in events:
        yield ev
        await _delay(delay_ms)


def _intent_via_llm(message: str, *, user_id: str | None = None) -> IntentExtraction:
    """用真 LLM 客户端跑意图解析；任何失败 → 兜底家庭主场景 fixture。

    Phase 0.7：传 user_id 时 prompt 注入 persona/memory prior（"我是谁 + 学过什么"）。
    Demo 安全网：评委网络抖动或 API 限流时也能跑通。
    """
    from agent.intent.parser import parse_intent
    from agent.core.llm_client import get_llm_client

    try:
        client = get_llm_client()
        return parse_intent(message, client=client, user_id=user_id)
    except Exception:  # noqa: BLE001
        return IntentExtraction(
            start_time="today_afternoon",
            duration_hours=[4, 6],
            distance_max_km=5,
            companions=[
                Companion(role="妻子", count=1),
                Companion(role="孩子", age=5, count=1),
            ],
            physical_constraints=["亲子友好", "适合 5-10 岁"],
            dietary_constraints=["低脂", "健康轻食"],
            experience_tags=[],
            social_context="家庭日常",
            raw_input=message,
            parse_confidence=0.6,
            ambiguous_fields=["llm_unavailable_fallback"],
        )


def _record_to_sse(
    record: Any,
    seq: int,
    seen_intent_parsed: bool,
    emit_intent_event: bool,
) -> Optional[SseEvent]:
    """单条 TraceRecord → SseEvent；refine 链路要跳过 INTENT_PARSED。"""
    sse_type = _TRACER_TO_SSE.get(record.type)
    if sse_type is None:
        return None
    if sse_type == SseEventType.INTENT_PARSED:
        if not emit_intent_event or seen_intent_parsed:
            return None
    return SseEvent(
        type=sse_type,
        seq=seq,
        payload=dict(record.payload),
        timestamp_ms=record.timestamp_ms,
    )


async def _planner_stream(
    req: ChatStreamRequest,
    *,
    mode: str,
    intent_override: Optional[IntentExtraction] = None,
    starting_seq: int = 0,
    user_id: str | None = None,
) -> AsyncIterator[SseEvent]:
    """真 planner 链路：意图解析 → plan_itinerary_with_mode → 实时推送 tracer 事件。

    Phase 0.7：传 user_id 时意图解析注入 persona/memory prior；
    最终 session 也把 user_id 一并存下，confirm/refine 路径可读到。

    实时推送策略（重要）：
        plan_itinerary_with_mode 在 LLM mode 下会跑 30-60s（多轮 LLM chat）。
        若同步等它跑完才 yield，前端 SSE 解析器会触发首字节超时。
        本函数把 plan 跑在 asyncio.to_thread 后台线程，主线程消费 Tracer 订阅
        emit 的事件，通过 asyncio.Queue 实时 yield 给客户端。

    与 _stub_stream 接口对齐；refine 链路也复用本流程（intent_override / starting_seq）。
    """
    import asyncio
    import threading

    from agent.planning.planners.rule_planner import plan_itinerary_with_mode
    from agent.core.trace import TraceRecord, Tracer

    seq = starting_seq

    # ---- 意图解析判断（不立刻同步调 LLM，避免首字节超时）----
    if intent_override is not None:
        emit_intent_event = False
    else:
        emit_intent_event = True
        # 立刻发心跳：8s 首字节超时窗口内必须有字节
        yield SseEvent(
            type=SseEventType.AGENT_THOUGHT,
            seq=seq,
            payload={"text": "正在理解你的需求……"},
            timestamp_ms=_now_ms(),
        )
        seq += 1

    # ---- 准备 Tracer + 订阅队列 ----
    tracer = Tracer()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[TraceRecord | None] = asyncio.Queue()

    def _on_record(record: TraceRecord) -> None:
        # Tracer.emit 在 worker 线程触发；用 loop.call_soon_threadsafe 投入主线程队列
        loop.call_soon_threadsafe(queue.put_nowait, record)

    tracer.subscribe(_on_record)

    # ---- 后台线程：意图解析（如需要） + 跑真 planner ----
    plan_done = threading.Event()
    plan_result_holder: dict[str, Any] = {}

    def _run_plan() -> None:
        try:
            # 意图解析放后台线程，避免阻塞主线程导致首字节超时
            if intent_override is not None:
                intent = intent_override
            else:
                intent = _intent_via_llm(req.message, user_id=user_id)
                # 立刻 emit intent_parsed，让前端尽快看到结果
                tracer.emit("intent_parsed", intent.model_dump())
            plan_result_holder["intent"] = intent
            result = plan_itinerary_with_mode(intent, mode, tracer=tracer)
            plan_result_holder["result"] = result
        except Exception as e:  # noqa: BLE001
            plan_result_holder["error"] = e
        finally:
            plan_done.set()
            # 推一个 None sentinel 唤醒主消费循环（防止 queue.get() 永久阻塞）
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=_run_plan, daemon=True).start()

    # ---- 主循环：消费队列 → yield SSE ----
    seen_intent_parsed = False

    async def _drain_until_done() -> AsyncIterator[SseEvent]:
        nonlocal seq, seen_intent_parsed
        while True:
            record = await queue.get()
            if record is None:
                # plan 已结束，把剩余队列内容也清干净
                while not queue.empty():
                    extra = queue.get_nowait()
                    if extra is None:
                        continue
                    ev = _record_to_sse(extra, seq, seen_intent_parsed, emit_intent_event)
                    if ev is not None:
                        seq += 1
                        if ev.type == SseEventType.INTENT_PARSED:
                            seen_intent_parsed = True
                        yield ev
                return
            ev = _record_to_sse(record, seq, seen_intent_parsed, emit_intent_event)
            if ev is None:
                continue
            seq += 1
            if ev.type == SseEventType.INTENT_PARSED:
                seen_intent_parsed = True
            yield ev

    async for ev in _drain_until_done():
        yield ev

    # ---- 等后台线程收尾（轻量，因为 sentinel 已发）----
    plan_done.wait(timeout=2)
    if "error" in plan_result_holder:
        # 意外异常：推 stream_error
        err = plan_result_holder["error"]
        yield SseEvent(
            type=SseEventType.STREAM_ERROR,
            seq=seq,
            payload={
                "reason": "planner_failed",
                "detail": f"{type(err).__name__}: {err}",
            },
        )
        seq += 1

    # ---- 写 session ----
    intent = plan_result_holder.get("intent")
    result = plan_result_holder.get("result")
    if intent is not None and result is not None and result.itinerary is not None:
        SESSION_STORE[req.session_id] = {
            "intent": intent.model_dump(),
            "itinerary": result.itinerary.model_dump(),
            "user_id": user_id or "demo_user",
        }

    # ---- 暖心开场白（行程出炉时；真 LLM 模式调 LLM 生成有"人味"文案）----
    narration_text: str | None = None
    if intent is not None and result is not None and result.itinerary is not None:
        try:
            from agent.intent.narrator import generate_narration

            narration_text = await asyncio.to_thread(
                generate_narration,
                intent=intent,
                itinerary=result.itinerary,
                stage="stream",
                use_llm=True,  # 真 planner 路径默认走 LLM；失败自动 fallback 到模板
            )
            yield SseEvent(
                type=SseEventType.AGENT_NARRATION,
                seq=seq,
                payload={"text": narration_text, "stage": "stream"},
                timestamp_ms=_now_ms(),
            )
            seq += 1
        except Exception:  # noqa: BLE001
            # narration 失败不阻塞主流程（已经有 itinerary_ready 兜底）
            pass

    # ---- v2 ConversationStore 同步 hook（跨 turn 上下文持久）----
    if intent is not None and result is not None and result.itinerary is not None:
        try:
            from agent.runtime.orchestrator import (
                record_planning_result,
                record_refinement_result,
            )

            agent_msg = narration_text or f"已为你规划：{result.itinerary.summary}"

            if intent_override is not None:
                # refine 路径：req.message 是用户的反馈文本
                await record_refinement_result(
                    session_id=req.session_id,
                    user_id=user_id or "demo_user",
                    refined_intent=intent,
                    new_itinerary=result.itinerary,
                    feedback_text=req.message,
                    agent_message=agent_msg,
                )
            else:
                # fresh 路径：req.message 是用户原始需求
                await record_planning_result(
                    session_id=req.session_id,
                    user_id=user_id or "demo_user",
                    intent=intent,
                    itinerary=result.itinerary,
                    user_message=req.message,
                    agent_message=agent_msg,
                )
        except Exception:  # noqa: BLE001
            # v2 持久化失败不阻塞旧链路
            pass

    # ---- 推 done ----
    yield SseEvent(type=SseEventType.DONE, seq=seq, payload={})
