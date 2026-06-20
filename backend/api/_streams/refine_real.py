"""_refine_stream_real —— 真 planner 路径下的 refine 流。

refiner 合并 → plan_itinerary_with_mode 重算；事件序列与 stub 版一致。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from schemas import (
    IntentExtraction,
    RefinementInput,
    SseEvent,
    SseEventType,
)

from .._sse_helpers import delay as _delay
from .._sse_helpers import now_ms as _now_ms
from .memory import _accumulate_memory_after_refine
from .models import ChatStreamRequest
from .planner_stream import _planner_stream
from .stub_refine import _stub_refine


async def _refine_stream_real(
    req: RefinementInput,
    cached: dict[str, Any],
    *,
    mode: str,
) -> AsyncIterator[SseEvent]:
    """/chat/refine 真链路：refiner 合并 → plan_itinerary_with_mode 重算。

    事件序列（同 stub 版）：refinement_start → refinement_done → 主路径 → done
    """
    seq = 0

    def emit(type_: SseEventType, payload: dict[str, Any]) -> SseEvent:
        nonlocal seq
        ev = SseEvent(type=type_, seq=seq, payload=payload, timestamp_ms=_now_ms())
        seq += 1
        return ev

    # ---- 0: refinement_start ----
    yield emit(
        SseEventType.REFINEMENT_START,
        {"feedback_text": req.feedback_text or ""},
    )
    await _delay(180)

    # ---- 调真 refiner（A 实现）----
    original = IntentExtraction.model_validate(cached["intent"])
    try:
        from agent.intent.refiner import refine_intent, summarize_itinerary

        refinement = refine_intent(
            original,
            req.feedback_text or "",
            itinerary_summary=summarize_itinerary(cached.get("itinerary")),
        )
    except Exception:  # noqa: BLE001 — 防 LLM 抖动；走 stub refiner 兜底
        refinement = _stub_refine(original, req.feedback_text or "")

    # Phase 0.7：累积 memory rejected（推断 user 拒绝的 tag）
    refined = refinement.refined_intent
    rejected_tags: list[str] = []
    rejected_tags.extend(set(original.dietary_constraints) - set(refined.dietary_constraints))
    rejected_tags.extend(set(original.experience_tags) - set(refined.experience_tags))
    rejected_tags.extend(set(original.physical_constraints) - set(refined.physical_constraints))
    if rejected_tags:
        _accumulate_memory_after_refine(cached, rejected_tags)

    # ---- 1: refinement_done ----
    yield emit(SseEventType.REFINEMENT_DONE, refinement.model_dump())
    await _delay(220)

    # ---- 2..N: 真 planner 重跑 ----
    user_id = cached.get("user_id")
    placeholder_req = ChatStreamRequest(
        message=refinement.refined_intent.raw_input,
        session_id=req.session_id,
        user_id=user_id,
    )
    async for ev in _planner_stream(
        placeholder_req,
        mode=mode,
        intent_override=refinement.refined_intent,
        starting_seq=seq,
        user_id=user_id,
    ):
        yield ev
        seq = ev.seq + 1
