"""Chat 端点：/chat/confirm + /chat/turn。

来自 main.py 拆分（spec code-modularization-refactor H1-final）；
V1 legacy /chat/stream + /chat/refine 已退役删除，turn 唯一走 V3 LangGraph。
所有 SSE 流实现细节在 api/_streams/* 子模块；本文件仅定义 2 个 router 端点。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from schemas import (
    SseEventType,
    resolve_planner_mode,
)

from ._session_store import SESSION_STORE, resolve_user_id, sync_snapshot
from ._sse_helpers import safe_stream
from ._streams.models import ChatConfirmRequest, ChatStreamRequest
from ._streams.graph_confirm import _graph_confirm

router = APIRouter()


@router.post(
    "/chat/confirm",
    tags=["小团接入"],
    summary="确认下单：派发预约餐厅、买票、生成转发文案 3 个执行类工具",
)
async def chat_confirm(req: ChatConfirmRequest, request: Request) -> EventSourceResponse:
    """用户确认行程方案 → 后端 replay 规划期锁定的确认动作清单。

    防 hallucination（工具前移，真实防线）：
        - 规划期 finalize_plan 把 confirm 要调的工具 + 参数（含目标 id）算好
          锁进 Itinerary.pending_actions；confirm 期 replay_confirm_actions
          忠实回放、不读 intent 不重新决策——执行与所见一致，LLM 在 confirm
          轮没有编造目标的通道（见 agent/graph/nodes/execute_finalize.py）。
        - 曾在此宣称的 allowed_*_ids 白名单校验从未实现（协议死字段，全仓零
          消费），已随 ChatConfirmRequest 一并删除（分界修缮批 任务 6）。

    SSE 序列：
        reserve_restaurant → buy_ticket（如有 POI 票）→ order_extra_service（如有蛋糕/鲜花）→ generate_share_message →
        itinerary_ready（含 orders + share_message）→ agent_narration → done

    确认恒走 `_graph_confirm`（ADR-0012 决策 5：`USE_LANGGRAPH` 开关与专用的
    `_stub_confirm` 已一并退役——协作房间也切到了同一条流，见 collab/room.py）。
    它把 memory_writer 记忆回写 + memory_store 标签/访问累积都放到后台执行（预约
    成功不等真实 LLM narrator / 两套记忆写入）；终版方案 + user_decision="confirm"
    则在推 DONE 事件前同步回写进图 checkpoint（ADR-0012 决策 2），
    见 graph_confirm._writeback_graph_state。
    """
    mode = resolve_planner_mode(
        header_value=request.headers.get("X-Planner-Mode"),
        env_value=os.getenv("PLANNER_MODE"),
    )
    inner = _graph_confirm(req)
    return EventSourceResponse(
        safe_stream(inner),
        media_type="text/event-stream",
        headers={"X-Planner-Mode": mode},
    )


@router.post(
    "/chat/turn",
    tags=["小团接入"],
    summary="对话主入口(推荐):自动识别新需求 vs 反馈,跨 turn 上下文持久化",
)
async def chat_turn(req: ChatStreamRequest, request: Request) -> EventSourceResponse:
    """**小团 App 集成首选这个端点。**

    turn 唯一走 V3 LangGraph 主路径:graph 自带 InMemorySaver(thread_id=session_id)
    持久化跨 turn 上下文,由 graph 内部级联自行判断「新需求 vs 反馈」,
    无需小团 App 自己维护"现在该调哪个端点"的状态机。

    LangGraph build / import 失败 → 直接 500(langgraph_unavailable);
    V1 legacy router→planner/refiner 双路径已退役删除,不再 fallback。

    SSE 序列:
        agent_thought → tool_call_* (多次) → [replan_triggered] →
        itinerary_ready + agent_narration | chitchat_reply → done
    """
    mode = resolve_planner_mode(
        header_value=request.headers.get("X-Planner-Mode"),
        env_value=os.getenv("PLANNER_MODE"),
    )
    user_id = resolve_user_id(req.user_id, request.headers.get("X-User-Id"))

    # ---- LangGraph 路径（唯一 turn 路径；V1 旧路径已退役删除）----
    try:
        from agent.graph.sse_adapter import run_graph_stream
        # 探活:构造一次 graph build(首次调时 lazy compile)
        from agent.graph.build import get_compiled_graph
        get_compiled_graph()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"langgraph_unavailable: {type(e).__name__}: {e}",
        )

    # graph 自带 InMemorySaver,thread_id=session_id;不再走 ConversationStore
    # 包装:拦截事件同步到 SESSION_STORE(协作房间创建时需要行程+规划事件历史)
    async def _graph_stream_with_session_sync():
        intent_data = None
        events_history: list[dict[str, Any]] = []
        async for ev in run_graph_stream(
            user_input=req.message,
            session_id=req.session_id,
            user_id=user_id,
            scenario_id=req.scenario_id,
            planner_mode=mode,
        ):
            # 收集所有事件到历史(协作房间新成员回放用)
            events_history.append(ev.model_dump())
            # 拦截 intent_parsed 和 itinerary_ready 同步到 SESSION_STORE
            if ev.type == SseEventType.INTENT_PARSED:
                intent_data = ev.payload
            elif ev.type == SseEventType.ITINERARY_READY:
                # 单一合并入口（ADR-0013 F-4 新增第二个写点后抽出，见
                # api/_session_store.py::sync_snapshot docstring）；本调用点
                # 传全部四键，行为与抽出前的整体赋值完全等价。
                sync_snapshot(
                    req.session_id,
                    intent=intent_data,
                    itinerary=ev.payload,
                    user_id=user_id,
                    planning_events=events_history,
                )
            yield ev
        # 流结束后确保 session 有事件历史(即使没有 itinerary_ready)
        if req.session_id in SESSION_STORE:
            SESSION_STORE[req.session_id]["planning_events"] = events_history

    inner = _graph_stream_with_session_sync()
    return EventSourceResponse(
        safe_stream(inner),
        media_type="text/event-stream",
        headers={
            "X-Planner-Mode": mode,
            "X-User-Id": user_id,
            "X-Turn-Kind": "langgraph",
        },
    )
