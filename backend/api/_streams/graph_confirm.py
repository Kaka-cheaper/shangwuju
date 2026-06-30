"""_graph_confirm -- /chat/confirm 的真实 LangGraph finalize 流。

规划阶段由 /chat/turn 的 LangGraph 主路径产出 intent + itinerary 并写入
SESSION_STORE；用户点确认后，本流读取该快照，调用 graph/nodes/execute_finalize.py
的真实执行节点完成预约 / 购票 / 附加服务 / 转发文案。
确认成功后，memory_writer 与 ConversationStore 记录作为后台副作用执行，
不阻塞 tool_call / itinerary_ready / done。

不负责：
- 重新规划。
- 重新调用 LLM planner。
- 修改 graph/build.py 拓扑。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from agent.graph.nodes.execute_finalize import (
    _persist_memory_side_effect,
    execute_finalize_node,
)
from schemas import IntentExtraction, Itinerary, SseEvent, SseEventType

from .._session_store import SESSION_STORE
from .._sse_helpers import now_ms as _now_ms
from .models import ChatConfirmRequest


FINALIZE_HEARTBEAT_S = 1.5
logger = logging.getLogger(__name__)
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


async def _graph_confirm(req: ChatConfirmRequest) -> AsyncIterator[SseEvent]:
    seq = 0

    def emit(type_: SseEventType, payload: dict[str, Any] | None = None) -> SseEvent:
        nonlocal seq
        ev = SseEvent(
            type=type_,
            seq=seq,
            payload=payload or {},
            timestamp_ms=_now_ms(),
        )
        seq += 1
        return ev

    if req.decision != "confirm":
        yield emit(
            SseEventType.AGENT_THOUGHT,
            {"text": f"已收到 {req.decision}，本次不执行预约。"},
        )
        yield emit(SseEventType.DONE)
        return

    cached = SESSION_STORE.get(req.session_id)
    # 工具前移后 confirm 只需 itinerary（动作清单挂在它上面）；intent 缺省不再算错（拆 ReAct 断点）。
    if not cached or not cached.get("itinerary"):
        yield emit(
            SseEventType.STREAM_ERROR,
            {
                "reason": "session_not_found",
                "detail": f"session not found or no itinerary: {req.session_id}",
            },
        )
        yield emit(SseEventType.DONE)
        return

    try:
        intent_raw = cached.get("intent")
        intent = IntentExtraction.model_validate(intent_raw) if intent_raw else None
        itinerary = Itinerary.model_validate(cached["itinerary"])
    except Exception as exc:  # noqa: BLE001
        yield emit(
            SseEventType.STREAM_ERROR,
            {
                "reason": "invalid_session_snapshot",
                "detail": f"{type(exc).__name__}: {str(exc)[:200]}",
            },
        )
        yield emit(SseEventType.DONE)
        return

    yield emit(SseEventType.AGENT_THOUGHT, {"text": "正在确认预约与加购服务……"})

    finalize_state = {
        "intent": intent,
        "itinerary": itinerary,
        "user_decision": "confirm",
        "user_id": cached.get("user_id") or req.user_id or "demo_user",
        "session_id": req.session_id,
        "defer_post_confirm_effects": True,
    }
    task = asyncio.create_task(asyncio.to_thread(execute_finalize_node, finalize_state))

    heartbeat_count = 0
    while True:
        try:
            result = await asyncio.wait_for(
                asyncio.shield(task), timeout=FINALIZE_HEARTBEAT_S
            )
            break
        except asyncio.TimeoutError:
            heartbeat_count += 1
            yield emit(
                SseEventType.AGENT_THOUGHT,
                {
                    "text": (
                        "正在等待预约 / 下单 / 转发文案完成……"
                        if heartbeat_count == 1
                        else "确认执行还在进行中……"
                    )
                },
            )
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception as exc:  # noqa: BLE001
            yield emit(
                SseEventType.STREAM_ERROR,
                {
                    "reason": "finalize_failed",
                    "detail": f"{type(exc).__name__}: {str(exc)[:200]}",
                },
            )
            yield emit(SseEventType.DONE)
            return

    for item in result.get("execution_tool_results") or []:
        tool = item.get("tool")
        if not tool:
            continue
        yield emit(
            SseEventType.TOOL_CALL_START,
            {"tool": tool, "input": item.get("input") or {}},
        )
        yield emit(
            SseEventType.TOOL_CALL_END,
            {
                "tool": tool,
                "output": item.get("output") or {},
                "duration_ms": item.get("duration_ms") or 0,
            },
        )

    final_itinerary = result.get("itinerary")
    final_itinerary_obj: Itinerary | None = None
    if final_itinerary is not None:
        final_payload = (
            final_itinerary.model_dump()
            if hasattr(final_itinerary, "model_dump")
            else final_itinerary
        )
        final_itinerary_obj = Itinerary.model_validate(final_payload)
        SESSION_STORE[req.session_id] = {**cached, "itinerary": final_payload}
        yield emit(SseEventType.ITINERARY_READY, final_payload)

        # intent 缺省（ReAct 路径没落库）时跳过写偏好——没意图无从推画像；订单/文案/record 照常。
        if result.get("post_confirm_effects_deferred") and intent is not None:
            _schedule_background_memory_persist(
                finalize_state=finalize_state,
                intent=intent,
                final_itinerary=final_itinerary_obj,
            )

        _schedule_background_confirm_record(
            session_id=req.session_id,
            user_id=cached.get("user_id") or req.user_id or "demo_user",
            final_itinerary=final_itinerary_obj,
            agent_message=result.get("narration") or "已完成下单。",
        )

    if result.get("narration"):
        yield emit(
            SseEventType.AGENT_NARRATION,
            {"text": result["narration"], "stage": "confirm"},
        )

    if result.get("memory_status") is not None:
        yield emit(SseEventType.MEMORY_PERSISTED, result["memory_status"])

    yield emit(SseEventType.DONE)


def _schedule_background_memory_persist(
    *,
    finalize_state: dict[str, Any],
    intent: IntentExtraction,
    final_itinerary: Itinerary,
) -> None:
    task = asyncio.create_task(
        _persist_memory_later(
            finalize_state=finalize_state,
            intent=intent,
            final_itinerary=final_itinerary,
        )
    )
    _track_background_task(task)


def _schedule_background_confirm_record(
    *,
    session_id: str,
    user_id: str,
    final_itinerary: Itinerary,
    agent_message: str,
) -> None:
    task = asyncio.create_task(
        _record_confirm_later(
            session_id=session_id,
            user_id=user_id,
            final_itinerary=final_itinerary,
            agent_message=agent_message,
        )
    )
    _track_background_task(task)


def _track_background_task(task: asyncio.Task[None]) -> None:
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _persist_memory_later(
    *,
    finalize_state: dict[str, Any],
    intent: IntentExtraction,
    final_itinerary: Itinerary,
) -> None:
    out_state: dict[str, Any] = {}
    try:
        await asyncio.to_thread(
            _persist_memory_side_effect,
            finalize_state,
            intent,
            final_itinerary,
            out_state,
        )
        if out_state.get("memory_status") is not None:
            logger.info(
                "graph_confirm: memory persisted in background: %s",
                out_state["memory_status"],
            )
    except Exception:  # noqa: BLE001
        logger.debug("graph_confirm: background memory persist failed", exc_info=True)


async def _record_confirm_later(
    *,
    session_id: str,
    user_id: str,
    final_itinerary: Itinerary,
    agent_message: str,
) -> None:
    try:
        from agent.runtime.conversation import record_confirm_result

        await record_confirm_result(
            session_id=session_id,
            user_id=user_id,
            final_itinerary=final_itinerary,
            agent_message=agent_message,
        )
    except Exception:  # noqa: BLE001
        logger.debug("graph_confirm: background confirm record failed", exc_info=True)
