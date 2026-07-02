"""_graph_confirm -- /chat/confirm 的真实 LangGraph finalize 流。

规划阶段由 /chat/turn 的 LangGraph 主路径产出 intent + itinerary 并写入
SESSION_STORE；用户点确认后，本流读取该快照，调用 graph/nodes/execute_finalize.py
的真实执行函数（不是图内节点，见下）完成预约 / 购票 / 附加服务 / 转发文案。

确认成功后三件事并行发生（ADR-0012 决策 5：两种记忆副作用不是二选一，缺一都会
断掉一条已有读者的闭环）：
- memory_writer 副作用（写 user_profile.json 的 recent_trips）作为后台任务执行；
- memory_store 标签 / 访问累积（`.memory._accumulate_memory_after_confirm`，写
  UserMemory——persona_qa / intent_parser 先验 / search_adapter 排重 / preferences
  API 的真实读者）同样作为后台任务执行，与 memory_writer 并列、互不等待；
  二者都不阻塞 tool_call / itinerary_ready / done。
- 终版方案（含 orders）与 user_decision="confirm" 在推 DONE 事件之前**同步**回写进
  LangGraph 图状态（ADR-0012 决策 2：会话跨轮真相源=图状态），使下一轮 /chat/turn
  能从图状态看到「已下单」。该 session 没有图 checkpoint（如协作房间会话）或回写
  本身失败时，记 warning 日志降级跳过——绝不影响确认结果本身。

本流现在是协作房间与主 App 唯一共用的确认实现（ADR-0012 决策 5，`_stub_confirm`
已删除）：房间确认前已把方案写进 SESSION_STORE 投影，本流只认这个端口取数，
房间会话没有图 checkpoint 时 `_writeback_graph_state` 优雅跳过（见该函数 docstring）。

不负责：
- 重新规划。
- 重新调用 LLM planner。
- 修改 graph/build.py 拓扑（execute_finalize 已从图节点退注册，函数体仍在）。
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
from .memory import _accumulate_memory_after_confirm
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

        # ADR-0012 决策 5 硬门 1：memory_store 标签/访问累积——平移 _stub_confirm
        # 的调用语义（无条件调用，不依赖 intent 是否解析成功；用 cached 里的原始
        # user_id，不是 finalize_state 兜底过的那个，见任务报告「自行拍板判断点」），
        # 与 memory_writer 并列作为后台任务执行。
        _schedule_background_memory_accumulate(
            cached=cached,
            final_itinerary_dict=final_payload,
        )

        # intent 缺省（ReAct 路径没落库）时跳过写偏好——没意图无从推画像；订单/文案/record 照常。
        if result.get("post_confirm_effects_deferred") and intent is not None:
            _schedule_background_memory_persist(
                finalize_state=finalize_state,
                intent=intent,
                final_itinerary=final_itinerary_obj,
            )

        # ADR-0012 决策 2：确认成功后把终版方案回写进图状态，时序纪律要求在 DONE
        # 事件之前**同步**完成（压竞态窗口）——不是后台任务，故不用 _schedule_* helper。
        await _writeback_graph_state(
            session_id=req.session_id,
            itinerary=final_itinerary_obj,
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


def _schedule_background_memory_accumulate(
    *,
    cached: dict[str, Any],
    final_itinerary_dict: dict[str, Any],
) -> None:
    """ADR-0012 决策 5 硬门 1：调度 memory_store 标签/访问累积（与 memory_writer 并列）。

    平移 `_stub_confirm`（已删除）里 `_accumulate_memory_after_confirm` 的调用
    语义：无条件调用——不像 memory_writer 那样要求 intent 解析成功（tag 来自
    itinerary.nodes 反查 mock_data，不依赖 IntentExtraction 对象本身），user_id
    取原始 SESSION_STORE 快照里的 `cached["user_id"]`（房间确认前已把 room.owner_id
    写进这里；缺失时 `_accumulate_memory_after_confirm` 自己短路跳过，不阻塞主流程）。
    """
    task = asyncio.create_task(
        _accumulate_memory_later(cached=cached, final_itinerary_dict=final_itinerary_dict)
    )
    _track_background_task(task)


async def _accumulate_memory_later(
    *,
    cached: dict[str, Any],
    final_itinerary_dict: dict[str, Any],
) -> None:
    try:
        await asyncio.to_thread(
            _accumulate_memory_after_confirm, cached, final_itinerary_dict
        )
    except Exception:  # noqa: BLE001
        logger.debug("graph_confirm: background memory accumulate failed", exc_info=True)


async def _writeback_graph_state(
    *,
    session_id: str,
    itinerary: Itinerary,
) -> None:
    """确认成功后把终版方案回写进 LangGraph checkpointer（ADR-0012 决策 2）。

    调用方纪律（时序）：必须在 yield DONE 事件之前 await 完这个函数——回写若晚于
    DONE，用户在确认动画期间抢发新消息可能先落盘，迟到的回写会把"确认时的旧方案"
    盖到新一轮状态上（ADR-0012 决策 2 的并发纪律；压窗后的残余竞态是已接受的
    demo 级风险，不在本函数处理）。

    失败降级（纪律）：无 checkpoint（协作房间会话没跑过图 / 未来还没来得及跑过图
    的场景）或写失败（如 redis 抖动）——记 warning 日志后静默返回，绝不向上抛出、
    绝不影响确认结果本身（投影端口 SESSION_STORE 里仍有终版方案兜底）。

    as_node 选型（自行拍板，见任务报告）：显式传 "narrate"，不用默认的 as_node=None
    自动解析——"execute_finalize" 已从图退注册，不再是合法 as_node；narrate 是
    topology 上「进入确认前」的最后一个真实节点，语义上最贴近"confirm 发生在
    narrate 之后"，且是确定性选择（不依赖 langgraph 对 versions_seen 的启发式
    消歧，避免小概率 InvalidUpdateError("Ambiguous update")）。
    """
    try:
        from agent.graph.build import get_compiled_graph

        graph = get_compiled_graph()
        config = {"configurable": {"thread_id": session_id}}
        snapshot = await graph.aget_state(config)
        if not snapshot or not snapshot.values:
            logger.info(
                "graph_confirm: session %s 无图 checkpoint（协作房间会话或未跑过图），"
                "跳过状态回写",
                session_id,
            )
            return
        await graph.aupdate_state(
            config,
            {"itinerary": itinerary, "user_decision": "confirm"},
            as_node="narrate",
        )
        logger.info(
            "graph_confirm: session %s 图状态回写成功（orders=%d，user_decision=confirm）",
            session_id,
            len(itinerary.orders or []),
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "graph_confirm: session %s 图状态回写失败，降级跳过（不影响确认结果）",
            session_id,
            exc_info=True,
        )
