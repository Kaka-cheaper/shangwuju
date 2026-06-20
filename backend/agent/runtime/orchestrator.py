"""agent.v2.orchestrator —— 单一对话入口编排器。

解决的根因问题（参考 problem.md 问题 18）：
    旧架构里用户在 dock 直接输入反馈会触发 /chat/stream（全新规划流），
    LLM 看不到「上次 Agent 提议了什么」，把"不要太远"当成新需求重解析。
    本质：缺 conversation_id / message_history 概念。

本模块做的事：
    1. 单一入口 turn_chat()：根据 ConversationState 判断这是「新需求」还是「对上次的反馈」
    2. 自动选择路径：
        - 新输入 → router_v2 路由 → planning 走原 planner_stream / 其他走 chitchat
        - 反馈输入（已有 itinerary） → refiner 路径，把 intent 调整后重规划
    3. 跨 turn 持久化 message_history：用 Pydantic AI 风格的 ModelMessage list
       未来要让 intent_parser/router 看到上下文时，从这里取

向后兼容（重要）：
    - 不替换 main.py 现有的 /chat/stream / /chat/refine / /chat/confirm
    - 这些端点继续工作，但它们会调本模块的 hooks（save_state_after_*）
      让 ConversationStore 里有数据
    - 新增 /chat/turn 端点是「智能版」入口，前端 dock 直接发消息时调它
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.core.feedback_detector import looks_like_feedback
from agent.runtime.conversation import (
    ConversationState,
    ConversationStore,
    get_default_store,
)
from schemas import IntentExtraction, Itinerary, RouterDecision

logger = logging.getLogger(__name__)


# ============================================================
# Turn 决策：判断这次输入是「新需求」还是「对上次的反馈」
# ============================================================
# looks_like_feedback 已迁移到 agent.feedback_detector 作为 SoT
# 本模块继续 re-export 是为兼容旧引用


def decide_turn_kind(
    message: str,
    state: ConversationState,
) -> str:
    """决定这次 turn 走哪条路径。

    Returns:
        "feedback"  → 当前 itinerary 已存在 + message 看着像反馈 → 走 refine 路径
        "fresh"     → 视为新规划请求 → 走 router → planner / chitchat
    """
    if state.itinerary_snapshot is None:
        return "fresh"
    if looks_like_feedback(message):
        return "feedback"
    # 已有 itinerary 但消息不像反馈 —— 例如用户主动说"我想换一个场景"
    # 默认按 fresh 走（重新路由），让 router 决定（也可能 router 判 chitchat）
    return "fresh"


# ============================================================
# 状态写入 hooks（main.py 现有端点调用）
# ============================================================


async def record_planning_result(
    *,
    session_id: str,
    user_id: str,
    intent: IntentExtraction,
    itinerary: Itinerary,
    user_message: str,
    agent_message: str,
    store: Optional[ConversationStore] = None,
) -> ConversationState:
    """planner_stream 跑完后调本函数把状态写入 store。

    把 user_message + agent_message 转成 Pydantic AI 兼容的 ModelMessage 序列，
    后续 turn 调用 LLM 时可作 message_history 喂回。

    设计取舍：
        我们没让旧 intent_parser / planner 真的用 Pydantic AI Agent 跑，
        所以这里手工构造 ModelMessage（user_msg + agent_text_response）。
        如果后续 narrator 用 Pydantic AI，可让其 result.all_messages() 直接
        merge 进 state.messages。
    """
    s = store or get_default_store()
    state = await s.get_or_create(session_id, user_id=user_id)
    state.user_id = user_id
    state.intent_snapshot = intent.model_dump()
    state.itinerary_snapshot = itinerary.model_dump()

    # 追加这次 turn 的对话到 message_history
    # 用 Pydantic AI 的 ModelRequest/ModelResponse 而不是 raw dict，
    # 这样以后任何 v2 Agent 调 message_history= 都能直接消费
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    state.messages.append(
        ModelRequest(parts=[UserPromptPart(content=user_message)])
    )
    state.messages.append(
        ModelResponse(parts=[TextPart(content=agent_message)])
    )

    await s.save(state)
    return state


async def record_refinement_result(
    *,
    session_id: str,
    user_id: str,
    refined_intent: IntentExtraction,
    new_itinerary: Itinerary,
    feedback_text: str,
    agent_message: str,
    store: Optional[ConversationStore] = None,
) -> ConversationState:
    """refine_stream 跑完后调本函数。

    与 record_planning_result 类似，但 user_message 包了「（反馈）」前缀，
    便于上层在 message_history 里做语义分流。
    """
    s = store or get_default_store()
    state = await s.get_or_create(session_id, user_id=user_id)
    state.user_id = user_id
    state.intent_snapshot = refined_intent.model_dump()
    state.itinerary_snapshot = new_itinerary.model_dump()

    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    user_with_marker = f"（反馈）{feedback_text}" if feedback_text else "（反馈）"
    state.messages.append(
        ModelRequest(parts=[UserPromptPart(content=user_with_marker)])
    )
    state.messages.append(
        ModelResponse(parts=[TextPart(content=agent_message)])
    )

    await s.save(state)
    return state


async def record_confirm_result(
    *,
    session_id: str,
    user_id: str,
    final_itinerary: Itinerary,
    agent_message: str,
    store: Optional[ConversationStore] = None,
) -> ConversationState:
    """confirm_stream 跑完后调（用户已下单）。

    更新 itinerary_snapshot（含 orders + share_message），
    并追加一条 agent confirmation 消息。
    """
    s = store or get_default_store()
    state = await s.get_or_create(session_id, user_id=user_id)
    state.itinerary_snapshot = final_itinerary.model_dump()

    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    state.messages.append(
        ModelRequest(parts=[UserPromptPart(content="（确认下单）")])
    )
    state.messages.append(
        ModelResponse(parts=[TextPart(content=agent_message)])
    )

    # confirm 续期：已成单的会话保留更久（redis 后端用 7d；memory 忽略 ttl）。
    await s.save(state, ttl=getattr(s, "_CONFIRM_TTL_SECONDS", None))
    return state


async def record_chitchat_result(
    *,
    session_id: str,
    user_id: str,
    user_message: str,
    decision: RouterDecision,
    store: Optional[ConversationStore] = None,
) -> ConversationState:
    """router 判 chitchat / meta / emotional 等非 planning 后的状态写入。

    这种 turn 不更新 itinerary_snapshot，但把对话写进 messages。
    """
    s = store or get_default_store()
    state = await s.get_or_create(session_id, user_id=user_id)

    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    state.messages.append(
        ModelRequest(parts=[UserPromptPart(content=user_message)])
    )
    state.messages.append(
        ModelResponse(parts=[TextPart(content=decision.reply_text)])
    )

    await s.save(state)
    return state


# ============================================================
# 上下文增强：让 intent_parser 看到历史
# ============================================================


def enhance_message_with_context(
    new_message: str,
    state: ConversationState,
    *,
    max_history_chars: int = 800,
) -> str:
    """把 new_message 拼上压缩的 message_history 喂回 LLM。

    用法：
        当 turn_kind=="feedback" 时，refiner 看到的不仅是反馈本身，
        还包括"上次 Agent 提议了什么"。这样它能正确理解"换近一点的"
        是相对哪个 baseline 在调整。

    实现：
        把 state.messages 的最后 N 轮（user + agent）拼成纯文本前缀，
        加在新消息前面。max_history_chars 控制总长度避免吃 token。

    Returns:
        增强后的消息：「【上次对话】... \n\n 【本次反馈】new_message」
    """
    if not state.messages:
        return new_message

    parts: list[str] = []
    total_chars = 0
    # 反向遍历最近的对话
    for msg in reversed(state.messages[-6:]):  # 最多 6 条 = 3 轮
        # 取 ModelRequest/ModelResponse 的文本内容
        text = _extract_msg_text(msg)
        if not text:
            continue
        role = "用户" if "Request" in type(msg).__name__ else "Agent"
        line = f"{role}：{text}"
        if total_chars + len(line) > max_history_chars:
            break
        parts.insert(0, line)
        total_chars += len(line)

    if not parts:
        return new_message

    history_block = "\n".join(parts)
    return (
        f"【上次对话】\n{history_block}\n\n"
        f"【本次输入】{new_message}"
    )


def _extract_msg_text(msg: Any) -> str:
    """从 ModelRequest/ModelResponse 抽取文本内容（容错）。"""
    try:
        parts = getattr(msg, "parts", None) or []
        for p in parts:
            content = getattr(p, "content", None)
            if isinstance(content, str) and content:
                return content
    except Exception:  # noqa: BLE001
        pass
    return ""


__all__ = [
    "looks_like_feedback",
    "decide_turn_kind",
    "record_planning_result",
    "record_refinement_result",
    "record_confirm_result",
    "record_chitchat_result",
    "enhance_message_with_context",
    "run_react_turn",
]


# ============================================================
# Phase 0.12 · ReAct 单一 Agent 流式入口
# ============================================================
#
# 把 unified_agent（agent.v2.react_agent）的 ReAct 循环接到 SSE 流：
#   1. 用 Agent.iter() 流式订阅 nodes
#   2. CallToolsNode → emit tool_call_start / tool_call_end
#   3. ModelRequestNode 含 RetryPromptPart → emit replan_triggered（critic backprompt）
#   4. End node → emit itinerary_ready (+ agent_narration) 或 chitchat_reply
#   5. 写 ConversationState（messages / intent_snapshot / itinerary_snapshot）
#   6. emit done
#
# 异常策略：本函数遇异常直接 raise（不 emit stream_error），让 main.py 在
# EventSourceResponse 之外捕获后 fallback 到旧路径。这样首字节前出错可无缝切；
# 已 emit 部分事件再出错，由 _safe_stream 兜底 emit stream_error + done。


async def run_react_turn(
    *,
    session_id: str,
    user_id: str,
    message: str,
    mode: str = "llm",
    starting_seq: int = 0,
) -> "AsyncIterator[Any]":
    """ReAct 单一 Agent 流式 SSE 入口。

    与旧 ``_planner_stream`` 接口对齐，让 main.py /chat/turn 能 drop-in 替换。

    Args:
        session_id: 对话 session
        user_id: 当前用户（持久化偏好）
        message: 用户本轮输入
        mode: planner_mode 透传给 deps（"rule" / "llm"，仅作上下文）
        starting_seq: SSE seq 起始值

    Yields:
        ``SseEvent``——每条对应 unified_agent 节点的可视化（前端思考链路）

    Raises:
        Exception：iter() 失败时抛出，让 main.py 走 fallback 旧路径
    """
    # 延迟 import 避免模块加载顺序问题
    from typing import AsyncIterator
    from pydantic_ai import Agent
    from pydantic_ai.messages import (
        ModelResponse,
        RetryPromptPart,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )
    from agent.runtime.conversation import get_default_repo
    from agent.runtime.deps import AgentDeps
    from agent.runtime.observability import (
        bind_session_context,
        clear_session_context,
        get_logger as _get_logger,
        trace_span,
    )
    from agent.runtime.output_types import ChatResponse, ItineraryResponse
    from agent.runtime.react_agent import unified_agent
    from schemas import SseEvent, SseEventType
    import json
    import time
    import uuid

    log = _get_logger("agent.v2.orchestrator.react")
    turn_id = uuid.uuid4().hex[:10]
    bind_session_context(session_id=session_id, turn_id=turn_id, user_id=user_id)

    repo = get_default_repo()
    state = await repo.get_or_create(session_id, user_id=user_id)

    deps = AgentDeps(
        user_id=user_id,
        planner_mode=mode,
        session_id=session_id,
        extra={"intent_snapshot": state.intent_snapshot},
    )

    seq = starting_seq

    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _emit(type_: "SseEventType", payload: dict) -> "SseEvent":
        nonlocal seq
        ev = SseEvent(type=type_, seq=seq, payload=payload, timestamp_ms=_now_ms())
        seq += 1
        return ev

    try:
        # 心跳：MiMo 真 LLM 模式下首字节可能拖到 8s+，先推一条让前端感知
        log.info("react_turn.start", message_preview=message[:60])
        yield _emit(
            SseEventType.AGENT_THOUGHT,
            {"text": "正在理解你的需求……"},
        )

        # ============================================================
        # Pydantic AI iter() 流式订阅
        # ============================================================
        # 节点拦截策略：
        # - CallToolsNode 在「LLM 已返回 ToolCallPart」之后、「工具真正被调」之前到达
        #   → 在这里 emit tool_call_start
        # - ToolReturnPart 出现在下一个 ModelRequestNode 的 request.parts 里
        #   → 在 ModelRequestNode 时配对 emit tool_call_end
        # - RetryPromptPart 同样在 ModelRequestNode → emit replan_triggered
        # - End node 拿到 .data 即 AgentOutput

        # 工具调用配对状态：tool_call_id → (tool_name, started_at_ms, started_seq)
        pending_calls: dict[str, dict] = {}

        async with unified_agent.iter(
            message,
            deps=deps,
            message_history=state.messages,
        ) as run:
            async for node in run:
                if Agent.is_user_prompt_node(node):
                    # 用户输入入图节点；不 emit 事件
                    pass

                elif Agent.is_model_request_node(node):
                    # 模型请求节点。检查 request.parts 里的：
                    # - ToolReturnPart：上一轮工具执行完毕的回执 → emit tool_call_end
                    # - RetryPromptPart：critic backprompt → emit replan_triggered
                    request = getattr(node, "request", None)
                    parts = list(getattr(request, "parts", None) or [])
                    for part in parts:
                        if isinstance(part, ToolReturnPart):
                            call_id = getattr(part, "tool_call_id", "") or ""
                            tool_name = getattr(part, "tool_name", "") or ""
                            outcome = getattr(part, "outcome", "success")
                            content = getattr(part, "content", None)
                            # content 在 Pydantic AI 里通常是 Tool 函数返回的 dict
                            if isinstance(content, str):
                                try:
                                    output_dict = json.loads(content)
                                except Exception:  # noqa: BLE001
                                    output_dict = {"raw": content}
                            elif isinstance(content, dict):
                                output_dict = content
                            else:
                                output_dict = {"raw": str(content)}
                            started = pending_calls.pop(call_id, None)
                            duration_ms = (
                                _now_ms() - started["started_at_ms"]
                                if started
                                else 0
                            )
                            yield _emit(
                                SseEventType.TOOL_CALL_END,
                                {
                                    "tool": tool_name or (started or {}).get("tool", ""),
                                    "output": output_dict,
                                    "duration_ms": duration_ms,
                                },
                            )
                            log.info(
                                "tool_call_end",
                                tool=tool_name,
                                duration_ms=duration_ms,
                                outcome=outcome,
                                success=bool(output_dict.get("success", False)),
                            )

                        elif isinstance(part, RetryPromptPart):
                            # critic backprompt 触发的 ModelRetry
                            retry_content = getattr(part, "content", "")
                            retry_text = (
                                retry_content
                                if isinstance(retry_content, str)
                                else json.dumps(retry_content, ensure_ascii=False)[:200]
                            )
                            yield _emit(
                                SseEventType.REPLAN_TRIGGERED,
                                {
                                    "reason": "critic_violation",
                                    "from_tool": "output_validator",
                                    "detail": retry_text[:240],
                                },
                            )
                            log.warning(
                                "critic_replan",
                                detail_preview=retry_text[:120],
                            )

                elif Agent.is_call_tools_node(node):
                    # LLM 返回了 ModelResponse（含 ToolCallPart 与/或 TextPart）
                    # 在工具真正被调用前 emit tool_call_start
                    response: ModelResponse = getattr(node, "model_response")
                    parts = list(getattr(response, "parts", None) or [])
                    for part in parts:
                        if isinstance(part, ToolCallPart):
                            call_id = getattr(part, "tool_call_id", "") or ""
                            tool_name = getattr(part, "tool_name", "") or ""
                            args = getattr(part, "args", None)
                            if isinstance(args, str):
                                try:
                                    input_dict = json.loads(args) if args else {}
                                except Exception:  # noqa: BLE001
                                    input_dict = {"raw": args}
                            elif isinstance(args, dict):
                                input_dict = args
                            else:
                                input_dict = {}
                            pending_calls[call_id] = {
                                "tool": tool_name,
                                "started_at_ms": _now_ms(),
                                "started_seq": seq,
                            }
                            yield _emit(
                                SseEventType.TOOL_CALL_START,
                                {"tool": tool_name, "input": input_dict},
                            )
                            log.info(
                                "tool_call_start",
                                tool=tool_name,
                                input_keys=list(input_dict.keys())[:8],
                            )
                        elif isinstance(part, TextPart):
                            # LLM 在工具调用之间的"思考"文本（如果有）
                            text_content = getattr(part, "content", "") or ""
                            if text_content.strip():
                                yield _emit(
                                    SseEventType.AGENT_THOUGHT,
                                    {"text": text_content.strip()[:240]},
                                )

                elif Agent.is_end_node(node):
                    # 终止节点；node.data 是最终 AgentOutput
                    pass

        # iter() 结束，run.result 含最终结果
        result = run.result
        output = getattr(result, "output", None)

        # 同步 ConversationState：messages 跨 turn 累计 + 业务快照刷新
        try:
            state.messages = result.all_messages()
        except Exception:  # noqa: BLE001
            log.warning("react_turn.all_messages_failed")

        if isinstance(output, ItineraryResponse):
            itinerary = output.itinerary
            narration = output.narration

            # 工具前移（spec dialogue-act-routing）：ReAct 路径也把「确认动作清单」挂上，
            # 让 confirm 直接 replay、不依赖 intent 快照——拆掉「ReAct 不发 intent_parsed →
            # confirm 读到空 intent」这条断点。intent_snapshot 缺省时 build 内部降级。
            try:
                from agent.graph.nodes.execute_finalize import build_confirm_actions

                _intent_obj = None
                if state.intent_snapshot:
                    from schemas.intent import IntentExtraction

                    _intent_obj = IntentExtraction.model_validate(state.intent_snapshot)
                itinerary = itinerary.model_copy(
                    update={"pending_actions": build_confirm_actions(itinerary, _intent_obj)}
                )
            except Exception:  # noqa: BLE001
                log.warning("react_turn.build_confirm_actions_failed")

            # 同步 itinerary_snapshot；intent_snapshot 由旧 confirm 路径单独维护
            state.itinerary_snapshot = itinerary.model_dump()

            # 也兼容旧 _SESSION_STORE（confirm 路径仍读它）
            try:
                from main import _SESSION_STORE as _legacy_store  # type: ignore[import-not-found]
                _legacy_store[session_id] = {
                    "intent": state.intent_snapshot or {},
                    "itinerary": itinerary.model_dump(),
                    "user_id": user_id,
                }
            except Exception:  # noqa: BLE001
                pass

            yield _emit(
                SseEventType.ITINERARY_READY,
                itinerary.model_dump(),
            )
            yield _emit(
                SseEventType.AGENT_NARRATION,
                {"text": narration, "stage": "stream"},
            )
            log.info(
                "react_turn.itinerary_ready",
                nodes=len(itinerary.nodes),
                hops=len(itinerary.hops),
                total_minutes=itinerary.total_minutes,
            )

        elif isinstance(output, ChatResponse):
            # 用 RouterDecision 形态封装让前端 ChitchatBubble 零改动
            from schemas.router import (
                CtaChip,
                InputKind,
                RouterDecision,
            )

            chips: list[CtaChip] = []
            for s in (output.suggestions or [])[:4]:
                s_clean = (s or "").strip()
                if not s_clean:
                    continue
                # send 字段对前端 ChitchatBubble.cta 而言是"点击后发送的文本"，
                # 让 LLM 给的 suggestion 直接做 send；label 同步
                chips.append(
                    CtaChip(
                        label=s_clean[:24],
                        send=s_clean[:200],
                    )
                )

            decision = RouterDecision(
                input_kind=InputKind.CHITCHAT,
                confidence=0.9,
                reply_text=output.text,
                tone="warm",
                cta_chips=chips,
                rationale="react_chat_response",
            )
            yield _emit(
                SseEventType.CHITCHAT_REPLY,
                decision.model_dump(),
            )
            log.info(
                "react_turn.chitchat_reply",
                text_len=len(output.text),
                chips=len(chips),
            )

        else:
            # 不应到达；当作 chitchat 兜底
            log.warning(
                "react_turn.unknown_output_type",
                type=type(output).__name__,
            )

        # 持久化
        await repo.save(state)

        yield _emit(SseEventType.DONE, {})
        log.info("react_turn.done", events=seq - starting_seq)

    finally:
        clear_session_context()
