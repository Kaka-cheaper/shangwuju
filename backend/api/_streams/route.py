"""Phase 0.8 输入域路由（Pre-Router）：关键词 fast path + LLM 分类。

来自 main.py 拆分（spec code-modularization-refactor H1-final）：
- _stub_route：关键词命中返 RouterDecision，否则 None（stub 模式专用）
- _make_chitchat_event：RouterDecision → CHITCHAT_REPLY 事件
- _routed_stream_stub / _routed_stream_real：stub / 真 LLM 模式分发

T3 适配器（ADR-0004 去重）：
- _routed_stream_real 路由决策改由共享 route_turn 驱动（canonical 信号表在 routing 层）
- 本文件删除原有重复的 _PLANNING_*_SIGNALS 副本和 _looks_like_planning
- stub 模式（_routed_stream_stub）保留 _stub_route 快路径（无 LLM 可用，不调 route_turn）
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional

from schemas import (
    InputKind,
    RouterDecision,
    SseEvent,
    SseEventType,
)

from agent.core.injection_detector import detect_injection

from .._sse_helpers import delay as _delay
from .._sse_helpers import now_ms as _now_ms
from .models import ChatStreamRequest
from .planner_stream import _planner_stream
from .stub_stream import _stub_stream

logger = logging.getLogger("api.route")


# 关键词 fast path（stub 模式 + 真 LLM 失败兜底用）
# 命中即推 chitchat_reply；未命中走原 planner
# 设计：每条精确等于白名单 send 文案的简化版（label 由 prompt 同步维护）
_STUB_CTA_TRIO: list[dict[str, str]] = [
    {
        "label": "陪老婆孩子",
        "send": "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁,老婆最近在减肥。",
        "icon": "👨‍👩‍👧",
    },
    {
        "label": "一个人放空",
        "send": "这周加班加得想吐，下午想一个人安安静静待几个小时再回家。",
        "icon": "🌿",
    },
    {
        "label": "商务接待",
        "send": "下午临时被叫去接个外地客户，对方是商务人士，帮我安排下。",
        "icon": "💼",
    },
]


def _stub_route(message: str) -> Optional[RouterDecision]:
    """关键词 fast path：命中返回 RouterDecision，否则返 None 走主路径。

    供 stub 模式与真 LLM 失败兜底使用。覆盖 5 类高频非主路径输入；
    真 LLM 路径覆盖更广（含「我累死了」「1+1=?」等模型才能识别的模糊语义）。
    """
    text = (message or "").strip().lower()
    if not text:
        return None

    # meta：问能力
    if any(kw in text for kw in ("你是谁", "你能做什么", "你是干嘛", "你叫什么", "什么 ai", "什么ai")):
        return RouterDecision(
            input_kind=InputKind.META,
            confidence=0.9,
            reply_text=(
                "我是「晌午局」——你的下午半日出行管家。一句话告诉我想做什么，"
                "我会帮你串好「去哪、吃啥、怎么走、几点订位」整条链路。要不试试？"
            ),
            tone="neutral",
            cta_chips=[c for c in _STUB_CTA_TRIO],  # type: ignore[misc]
            rationale="stub fast path · meta",
        )

    # chitchat：日常问候
    if text in ("你好", "hi", "hello", "嗨", "在吗") or text.startswith(("你好", "嗨", "hi ", "hello ")):
        return RouterDecision(
            input_kind=InputKind.CHITCHAT,
            confidence=0.9,
            reply_text="你好呀！要不要让我帮你规划一个下午行程？说一句你下午想做什么就行。",
            tone="warm",
            cta_chips=[c for c in _STUB_CTA_TRIO[:2]],  # type: ignore[misc]
            rationale="stub fast path · chitchat",
        )

    # emotional：疲惫/烦躁
    if any(kw in text for kw in ("累死", "累了", "心情差", "心情不好", "烦死", "好烦", "想哭", "崩溃")):
        return RouterDecision(
            input_kind=InputKind.EMOTIONAL,
            confidence=0.85,
            reply_text="听起来今天真的挺累的呢。要不下午别想工作了，我陪你找个安静的地方放空几小时？",
            tone="empathetic",
            cta_chips=[_STUB_CTA_TRIO[1]],  # type: ignore[list-item]
            rationale="stub fast path · emotional",
        )

    # off_topic：写代码/数学题/天气
    if any(
        kw in text
        for kw in ("写代码", "写个程序", "1+1", "天气怎么样", "明天天气", "几月几号", "今天星期")
    ):
        return RouterDecision(
            input_kind=InputKind.OFF_TOPIC,
            confidence=0.85,
            reply_text="这个我帮不上忙呢～不过下午局规划是我的强项，要不让我帮你安排一下？",
            tone="playful",
            cta_chips=[c for c in _STUB_CTA_TRIO],  # type: ignore[misc]
            rationale="stub fast path · off_topic",
        )

    # ambiguous：太短或没约束
    if text in ("出去玩", "玩", "去哪", "嗯", "看看", "吃饭", "随便"):
        return RouterDecision(
            input_kind=InputKind.AMBIGUOUS,
            confidence=0.8,
            reply_text="想约谁一起呢？告诉我「带 X 人 / 几公里以内 / 有没有特别约束」我就能帮你排好。",
            tone="warm",
            cta_chips=[c for c in _STUB_CTA_TRIO],  # type: ignore[misc]
            rationale="stub fast path · ambiguous",
        )

    return None  # 不是非 planning 输入 → 走原 stub_stream


def _make_chitchat_event(decision: RouterDecision, seq: int) -> SseEvent:
    return SseEvent(
        type=SseEventType.CHITCHAT_REPLY,
        seq=seq,
        payload=decision.model_dump(),
        timestamp_ms=_now_ms(),
    )


def _safe_refusal_decision() -> RouterDecision:
    """命中注入时的固定安全婉拒（spec prompt-injection-defense R4，与 V3 一致）。

    reply_text 是固定常量，绝不含用户输入文本（防 echo 攻击内容 R4.2）。
    """
    return RouterDecision(
        input_kind=InputKind.OFF_TOPIC,
        confidence=0.99,
        reply_text=(
            "这个我帮不上忙哦～不过下午局规划是我的强项~ "
            "试试告诉我你下午想做什么？"
        ),
        tone="playful",
        cta_chips=[],
        rationale="prompt_injection_blocked",
    )


def _injection_block_or_none(message: str) -> Optional[RouterDecision]:
    """注入检测闸：命中 high 返回安全婉拒 decision + 审计日志；否则 None。"""
    verdict = detect_injection(message or "")
    if verdict.is_injection and verdict.severity == "high":
        logger.warning(
            "prompt_injection_blocked(v1): category=%s matched=%s input_head=%r",
            verdict.category,
            verdict.matched,
            (message or "")[:40],
        )
        return _safe_refusal_decision()
    return None


async def _routed_stream_stub(req: ChatStreamRequest) -> AsyncIterator[SseEvent]:
    """stub 模式带 router：先过注入闸 → 关键词 fast path → 否则原 stub fixture。"""
    # Layer 0：注入检测（spec prompt-injection-defense）
    refusal = _injection_block_or_none(req.message)
    if refusal is not None:
        yield _make_chitchat_event(refusal, 0)
        await _delay(120)
        yield SseEvent(type=SseEventType.DONE, seq=1)
        return
    decision = _stub_route(req.message)
    if decision is not None:
        yield _make_chitchat_event(decision, 0)
        await _delay(120)
        yield SseEvent(type=SseEventType.DONE, seq=1)
        return
    # 主路径
    async for ev in _stub_stream(req):
        yield ev


async def _routed_stream_real(
    req: ChatStreamRequest,
    *,
    mode: str,
    user_id: str,
) -> AsyncIterator[SseEvent]:
    """真链路带 router：调共享 route_turn 拿 RouteOutcome，驱动原有 SSE 流。

    T3 适配器（ADR-0004）：路由决策来源从本地信号表换成 route_turn（V3 canonical 级联）。
    SSE 事件结构与流式行为不变，只换"去哪"的判定来源。

    模式分发：
    - mode == "rule"：route_turn Layer 0（注入）/ Layer 1.5（planning fast path）覆盖主路径，
                      Layer 2 以 stub client 兜底（classify_input 异常 → fallback planning）。
    - mode == "llm" ：route_turn 走完整级联（含 Layer 2 LLM 分类）；在后台线程跑防 event loop 阻塞，
                      期间推 agent_thought 心跳防首字节 8s 超时。

    route_turn 内部级联（已含注入检测、强信号、fast path、LLM 分类、兜底）：
        Layer 0  注入检测 → off_topic
        Layer 1  强信号反馈（has_itinerary；V1 传 None，故不触发）
        Layer 1.5 正向规划 fast path → planning
        Layer 1.7 用户画像问答 → chitchat
        Layer 2  LLM 分类；失败 → fallback planning
        Layer 3  会话内对话行为（has_itinerary；V1 传 None，故不触发）
        兜底归并 planning/ambiguous + has_itinerary → feedback（V1 不触发）
    """
    import asyncio
    import threading

    from agent.core.llm_client import get_llm_client
    from agent.routing.route_turn import route_turn

    client = get_llm_client(task="router")

    # ---- llm 模式：先推心跳防首字节超时（route_turn 可能走 LLM，5-10s）----
    seq = 0
    if mode == "llm":
        yield SseEvent(
            type=SseEventType.AGENT_THOUGHT,
            seq=seq,
            payload={"text": "正在理解你的需求……"},
            timestamp_ms=_now_ms(),
        )
        seq = 1

    # ---- 在后台线程跑 route_turn（同步；可能阻塞 LLM IO）----
    outcome_holder: dict[str, Any] = {}
    done_event = threading.Event()

    def _do_route() -> None:
        try:
            outcome_holder["outcome"] = route_turn(
                req.message, None, user_id, client=client
            )
        except Exception as e:  # noqa: BLE001
            outcome_holder["error"] = e
        finally:
            done_event.set()

    threading.Thread(target=_do_route, daemon=True).start()

    waited = 0.0
    while not done_event.is_set() and waited < 15.0:
        await asyncio.sleep(0.5)
        waited += 0.5

    # ---- 解包 outcome（后台异常极少见；当 planning 兜底）----
    if "outcome" in outcome_holder:
        outcome = outcome_holder["outcome"]
    else:
        # route_turn 本身抛异常（不应发生）→ 按 planning 兜底
        from agent.routing.outcome import RouteOutcome
        from agent.intent.router import fallback_decision
        outcome = RouteOutcome(
            kind="planning",
            decision=fallback_decision(req.message, reason="route_turn_failed"),
        )

    # ---- 分流：planning / feedback → planner；其余 → chitchat_reply ----
    if outcome.kind in ("planning", "feedback"):
        # planning：把 reply_text 作 thought 透出（让评委看到「Agent 已收到，开始规划」）
        if outcome.decision is not None and outcome.decision.reply_text:
            yield SseEvent(
                type=SseEventType.AGENT_THOUGHT,
                seq=seq,
                payload={"text": outcome.decision.reply_text},
                timestamp_ms=_now_ms(),
            )
            seq += 1
        async for ev in _planner_stream(
            req, mode=mode, user_id=user_id, starting_seq=seq
        ):
            yield ev
        return

    # 非主路径（chitchat / meta / emotional / off_topic / ambiguous）：推 chitchat_reply + done
    if outcome.decision is not None:
        yield _make_chitchat_event(outcome.decision, seq)
        # v2 ConversationStore 同步：chitchat / meta 等也要写入 messages
        try:
            from agent.runtime.orchestrator import record_chitchat_result

            await record_chitchat_result(
                session_id=req.session_id,
                user_id=user_id,
                user_message=req.message,
                decision=outcome.decision,
            )
        except Exception:  # noqa: BLE001
            pass
        seq += 1
    await _delay(120)
    yield SseEvent(type=SseEventType.DONE, seq=seq)
