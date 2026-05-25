"""Chat 端点：/chat/stream + /chat/confirm + /chat/refine + /chat/turn。

来自 main.py 拆分（spec code-modularization-refactor H1-final）。
所有 SSE 流实现细节在 api/_streams/* 子模块；本文件仅定义 4 个 router 端点。
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from schemas import (
    RefinementInput,
    SseEventType,
    resolve_planner_mode,
)

from ._session_store import SESSION_STORE, resolve_user_id
from ._sse_helpers import safe_stream
from ._streams.models import ChatConfirmRequest, ChatStreamRequest
from ._streams.planner_stream import _planner_stream
from ._streams.refine_real import _refine_stream_real
from ._streams.route import _routed_stream_real, _routed_stream_stub
from ._streams.stub_confirm import _stub_confirm
from ._streams.stub_refine import _refine_stream
from .health import _use_real_planner

router = APIRouter()


@router.post(
    "/chat/stream",
    tags=["小团接入"],
    summary="对话主入口（旧版）：一句话 → SSE 流式输出",
)
async def chat_stream(req: ChatStreamRequest, request: Request) -> EventSourceResponse:
    """**小团 App 不要直接用这个端点，改用 `/chat/turn`。**

    本端点保留是因为内部 e2e 测试依赖；`/chat/turn` 多了「跨 turn 上下文」识别，
    会自动判断是新需求还是反馈，更适合 App 真实调用场景。

    解析 PLANNER_MODE：
        header X-Planner-Mode > env PLANNER_MODE > default("rule")
    解析 user_id（Phase 0.7）：
        body.user_id > X-User-Id header > "demo_user"

    分发（Phase 0.8 输入域路由）：
        1. 真 LLM 模式 → 先跑 router 6 类分类
            - planning  → 走真 planner（_planner_stream）
            - 其他 5 类 → 推 chitchat_reply（payload=RouterDecision）+ done
        2. stub 模式  → 关键词 fast path 兜底（让前端 demo 也能演示「你是谁」气泡）
            - 命中关键词 → 推 chitchat_reply + done
            - 否则       → 走 stub fixture
    """
    mode = resolve_planner_mode(
        header_value=request.headers.get("X-Planner-Mode"),
        env_value=os.getenv("PLANNER_MODE"),
    )
    user_id = resolve_user_id(req.user_id, request.headers.get("X-User-Id"))
    if _use_real_planner():
        inner = _routed_stream_real(req, mode=mode, user_id=user_id)
    else:
        inner = _routed_stream_stub(req)
    return EventSourceResponse(
        safe_stream(inner),
        media_type="text/event-stream",
        headers={"X-Planner-Mode": mode, "X-User-Id": user_id},
    )


@router.post(
    "/chat/confirm",
    tags=["小团接入"],
    summary="确认下单：派发预约餐厅、买票、生成转发文案 3 个执行类工具",
)
async def chat_confirm(req: ChatConfirmRequest, request: Request) -> EventSourceResponse:
    """用户确认行程方案 → 后端按白名单（allowed_*_ids）派发执行类 Tool。

    防 hallucination：
        - 前端从 ItineraryReady 收到合法 ID 集合，confirm 时回传
        - reserve_restaurant / buy_ticket 仅能在该集合内派发
        - 缺省时不做白名单校验（向后兼容；demo 短路径不破）

    SSE 序列：
        reserve_restaurant → buy_ticket（如有 POI 票）→ generate_share_message →
        itinerary_ready（含 orders + share_message）→ memory_persisted → done
    """
    mode = resolve_planner_mode(
        header_value=request.headers.get("X-Planner-Mode"),
        env_value=os.getenv("PLANNER_MODE"),
    )
    return EventSourceResponse(
        safe_stream(_stub_confirm(req, mode=mode)),
        media_type="text/event-stream",
        headers={"X-Planner-Mode": mode},
    )


@router.post(
    "/chat/refine",
    tags=["小团接入"],
    summary="独立反馈：给定 session 的反馈文本 → refiner 合并约束 → 重新规划",
)
async def chat_refine(req: RefinementInput, request: Request) -> EventSourceResponse:
    """Phase 0.6：用户拒绝方案 + 反馈 → refiner 合并 → 重新规划。

    流程（详见 api_contract.md §7）：
        1. 从内存 session 取原 intent；不存在 → 422
        2. 推 refinement_start（含 feedback_text）
        3. 调 refiner（A 实现的 backend.agent.refiner.refine_intent；
           未实现时走内置启发式 _stub_refine 兜底）
        4. 推 refinement_done（含 RefinementOutput）
        5. 复用主路径事件序列，但用 refined_intent 驱动（distance 等关键字段反映新值）
        6. done
    """
    cached = SESSION_STORE.get(req.session_id)
    if cached is None:
        raise HTTPException(
            status_code=422,
            detail=f"session not found: {req.session_id}",
        )

    mode = resolve_planner_mode(
        header_value=request.headers.get("X-Planner-Mode"),
        env_value=os.getenv("PLANNER_MODE"),
    )

    if _use_real_planner():
        inner = _refine_stream_real(req, cached, mode=mode)
    else:
        inner = _refine_stream(req, cached)
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

    与 `/chat/stream` 区别:本端点会从 `ConversationStore` 取当前 session 状态,
    自动判断 message 是「新需求」还是「对已有方案的反馈」——前者走规划,后者走重规划,
    无需小团 App 自己维护"现在该调哪个端点"的状态机。

    Phase 0.12 起增加 ReAct 路径(USE_REACT_AGENT=1,默认 ON):
        1. ReAct 单一 Agent:让 LLM 看到全部 8 工具,自主决策何时调用
        2. critic 兜底:output_validator 验证违规 → ModelRetry 让 LLM 自纠错
        3. 上下文跨 turn 持久:用 ConversationRepository.messages 喂 message_history

    USE_REACT_AGENT=0 → 走旧的 router → planner / refiner 双路径(demo 安全兜底)。
    任何 ReAct 路径异常(import 错 / 配置错)→ 自动 fallback 到旧路径,确保 demo 稳定。

    决策逻辑(旧路径,仅 USE_REACT_AGENT=0 走):
        1. 从 ConversationStore 取当前 session 的 ConversationState
        2. 如果已有 itinerary_snapshot 且 message 看着像反馈 → 走 refine 路径
        3. 否则走 stream 路径(router → planner / chitchat)

    SSE 序列:
        - ReAct 路径:agent_thought → tool_call_* (多次) → [replan_triggered] →
                      itinerary_ready + agent_narration | chitchat_reply → done
        - feedback 路径:与 /chat/refine 一致
        - fresh 路径:   与 /chat/stream 一致
    """
    mode = resolve_planner_mode(
        header_value=request.headers.get("X-Planner-Mode"),
        env_value=os.getenv("PLANNER_MODE"),
    )
    user_id = resolve_user_id(req.user_id, request.headers.get("X-User-Id"))

    # ---- LangGraph 路径（USE_LANGGRAPH=1,最高优先级）----
    use_langgraph = (os.getenv("USE_LANGGRAPH") or "0").strip() == "1"
    if use_langgraph:
        try:
            from agent.graph.sse_adapter import run_graph_stream
            # 探活:构造一次 graph build(首次调时 lazy compile)
            from agent.graph.build import get_compiled_graph
            get_compiled_graph()
        except Exception as e:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger("main").warning(
                "langgraph_unavailable_fallback: %s: %s",
                type(e).__name__,
                e,
            )
        else:
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
                        SESSION_STORE[req.session_id] = {
                            "intent": intent_data,
                            "itinerary": ev.payload,
                            "user_id": user_id,
                            "planning_events": events_history,
                        }
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

    use_react = (os.getenv("USE_REACT_AGENT") or "1").strip() != "0"

    if use_react:
        try:
            # 探活:先验证 unified_agent 能 import(捕 import / 配置错防 sys 异常)
            from agent.runtime.orchestrator import run_react_turn
            from agent.runtime.react_agent import unified_agent  # noqa: F401  探活
        except Exception as e:  # noqa: BLE001
            # ReAct 路径不可用 → fallback 旧路径
            import logging as _logging
            _logging.getLogger("main").warning(
                "react_unavailable_fallback_to_legacy: %s: %s",
                type(e).__name__,
                e,
            )
        else:
            # 构造 ReAct 流式生成器
            # 包装:拦截事件同步到 SESSION_STORE(协作房间创建时需要行程+规划事件历史)
            async def _react_stream_with_session_sync():
                intent_data = None
                events_history: list[dict[str, Any]] = []
                async for ev in run_react_turn(
                    session_id=req.session_id,
                    user_id=user_id,
                    message=req.message,
                    mode=mode,
                ):
                    events_history.append(ev.model_dump())
                    if ev.type == SseEventType.INTENT_PARSED:
                        intent_data = ev.payload
                    elif ev.type == SseEventType.ITINERARY_READY:
                        SESSION_STORE[req.session_id] = {
                            "intent": intent_data,
                            "itinerary": ev.payload,
                            "user_id": user_id,
                            "planning_events": events_history,
                        }
                    yield ev
                if req.session_id in SESSION_STORE:
                    SESSION_STORE[req.session_id]["planning_events"] = events_history

            inner = _react_stream_with_session_sync()
            return EventSourceResponse(
                safe_stream(inner),
                media_type="text/event-stream",
                headers={
                    "X-Planner-Mode": mode,
                    "X-User-Id": user_id,
                    "X-Turn-Kind": "react",
                },
            )

    # ---- 旧路径(USE_REACT_AGENT=0 或 ReAct 不可用时走这里)----
    from agent.runtime.conversation import get_default_store
    from agent.runtime.orchestrator import decide_turn_kind

    # 取 v2 ConversationState 决定路径
    store = get_default_store()
    state = await store.get_or_create(req.session_id, user_id=user_id)
    turn_kind = decide_turn_kind(req.message, state)

    if turn_kind == "feedback" and state.itinerary_snapshot is not None:
        # 反馈路径:构造 RefinementInput 走原 refine 流
        refine_req = RefinementInput(
            session_id=req.session_id,
            feedback_text=req.message,
        )
        # 兼容旧 SESSION_STORE:refine 端点从那里取 intent,所以同步一份
        if state.intent_snapshot is not None:
            SESSION_STORE.setdefault(
                req.session_id,
                {
                    "intent": state.intent_snapshot,
                    "itinerary": state.itinerary_snapshot,
                    "user_id": user_id,
                },
            )
        cached = SESSION_STORE[req.session_id]
        if _use_real_planner():
            inner = _refine_stream_real(refine_req, cached, mode=mode)
        else:
            inner = _refine_stream(refine_req, cached)
        return EventSourceResponse(
            safe_stream(inner),
            media_type="text/event-stream",
            headers={
                "X-Planner-Mode": mode,
                "X-User-Id": user_id,
                "X-Turn-Kind": "feedback",
            },
        )

    # fresh 路径:走原 stream 流
    if _use_real_planner():
        inner = _routed_stream_real(req, mode=mode, user_id=user_id)
    else:
        inner = _routed_stream_stub(req)
    return EventSourceResponse(
        safe_stream(inner),
        media_type="text/event-stream",
        headers={
            "X-Planner-Mode": mode,
            "X-User-Id": user_id,
            "X-Turn-Kind": "fresh",
        },
    )
