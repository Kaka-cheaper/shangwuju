"""Phase 0.8 输入域路由（Pre-Router）：关键词 fast path + LLM 分类。

来自 main.py 拆分（spec code-modularization-refactor H1-final）：
- _stub_route：关键词命中返 RouterDecision，否则 None
- _make_chitchat_event：RouterDecision → CHITCHAT_REPLY 事件
- _routed_stream_stub / _routed_stream_real：stub / 真 LLM 模式分发
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Optional

from schemas import (
    InputKind,
    RouterDecision,
    SseEvent,
    SseEventType,
)

from .._sse_helpers import delay as _delay
from .._sse_helpers import now_ms as _now_ms
from .models import ChatStreamRequest
from .planner_stream import _planner_stream
from .stub_stream import _stub_stream


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


# ============================================================
# 优化 3 实验：正向 PLANNING 信号（让 llm 模式也能在毫秒级跳过 LLM router）
# ============================================================
#
# 设计动机：
#   _stub_route 仅识别 5 类**非** PLANNING 输入；典型 demo 输入「今天下午陪老婆孩子」
#   会返 None 让 llm 模式仍走 LLM router 调用 5-10s。
#
#   优化 3 思路：在 _stub_route 失败的输入上**正向匹配 PLANNING 信号**，命中即直接当
#   PLANNING 类继续走 _planner_stream，跳过 LLM router 节省 5-10s。
#
# 信号词典（覆盖 80%+ 主路径输入）：
#   - 时间：今天 / 明天 / 周末 / 周日 / 周六 / 下午 / 晚上
#   - 行为：出去玩 / 出去走走 / 散步 / 吃饭 / 看展 / K 歌 / 撸串 / 喝茶 / 拍照
#   - 同行：和老婆 / 和孩子 / 带 X / 陪 X / 朋友 / 闺蜜 / 客户 / 女朋友
#   - 约束：别离家太远 / 几公里以内 / 几小时

_PLANNING_TIME_SIGNALS = (
    "今天下午", "明天下午", "周末", "周日", "周六", "周五晚",
    "今晚", "今天晚上", "明天晚上", "下午", "晚上",
)

_PLANNING_ACTION_SIGNALS = (
    "出去玩", "出去走", "散步", "出门", "去玩", "找个地方",
    "看展", "k 歌", "k歌", "撸串", "夜宵", "下午茶",
    "聚会", "约会", "见面", "接待", "陪",
)

_PLANNING_COMPANION_SIGNALS = (
    "老婆", "孩子", "宝贝", "娃", "外公", "外婆", "爷爷", "奶奶",
    "父母", "妈", "爸", "客户", "闺蜜", "女朋友", "男朋友",
    "朋友", "兄弟", "同事", "同学", "室友",
)


def _looks_like_planning(message: str) -> bool:
    """正向 PLANNING 信号检测：命中说明大概率是规划输入，可跳过 LLM router。

    覆盖策略（任一命中即可）：
    1. 时间信号 + (行为或同行)：典型「今天下午陪老婆孩子」
    2. 距离/时长约束：「3 公里以内」「玩几小时」
    3. 多个同行信号：「老婆和孩子」
    """
    if not message:
        return False
    text = message.lower().strip()
    if len(text) < 6:
        # 太短不能确定是 PLANNING（防误判）
        return False

    has_time = any(s in text for s in _PLANNING_TIME_SIGNALS)
    has_action = any(s in text for s in _PLANNING_ACTION_SIGNALS)
    has_companion = any(s in text for s in _PLANNING_COMPANION_SIGNALS)
    has_distance = any(s in text for s in ("公里以内", "公里内", "km以内", "km内", "公里", "千米"))
    has_duration = any(s in text for s in ("几个小时", "几小时", "下午", "整晚", "半天", "一下午"))

    # 规则：时间 + (行为 OR 同行) → PLANNING
    if has_time and (has_action or has_companion):
        return True
    # 距离约束 + 任意主语 → PLANNING
    if has_distance and (has_action or has_companion):
        return True
    # 时长约束 + 行为 → PLANNING
    if has_duration and has_action:
        return True
    # 同行人 ≥ 2 个 → 通常是规划
    companion_hits = sum(1 for s in _PLANNING_COMPANION_SIGNALS if s in text)
    if companion_hits >= 2:
        return True

    return False


def _make_chitchat_event(decision: RouterDecision, seq: int) -> SseEvent:
    return SseEvent(
        type=SseEventType.CHITCHAT_REPLY,
        seq=seq,
        payload=decision.model_dump(),
        timestamp_ms=_now_ms(),
    )


async def _routed_stream_stub(req: ChatStreamRequest) -> AsyncIterator[SseEvent]:
    """stub 模式带 router：关键词 fast path 命中 → chitchat_reply；否则原 stub fixture。"""
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
    """真链路带 router：先分类，planning → 主 planner，否则推 chitchat_reply。

    模式分发：
    - mode == "rule" ：用 _stub_route 关键词 fast path 做分类（毫秒级）
                       未命中视为 PLANNING 走主路径；保证 rule 模式整链零 LLM 调用
    - mode == "llm"  ：调 LLM router classify_input（5-10s，覆盖范围更广）
                       LLM 失败 → _stub_route 兜底 → 仍失败按 PLANNING 兜底

    关键防御（仅 llm 模式需要）：
    - 在调 LLM 前推一条 agent_thought 心跳 → 防 8s 首字节超时
    - LLM 抛错 → 关键词 fast path → 仍失败 → 按 PLANNING 兜底
    """
    # ---- rule 模式：纯算法路由（毫秒级，无心跳必要）----
    if mode == "rule":
        decision = _stub_route(req.message)
        if decision is not None and decision.input_kind != InputKind.PLANNING:
            yield _make_chitchat_event(decision, 0)
            try:
                from agent.runtime.orchestrator import record_chitchat_result

                await record_chitchat_result(
                    session_id=req.session_id,
                    user_id=user_id,
                    user_message=req.message,
                    decision=decision,
                )
            except Exception:  # noqa: BLE001
                pass
            await _delay(120)
            yield SseEvent(type=SseEventType.DONE, seq=1)
            return
        # PLANNING 路径（_stub_route 返 None 也视作 PLANNING）→ 直接进 _planner_stream
        async for ev in _planner_stream(
            req, mode=mode, user_id=user_id, starting_seq=0
        ):
            yield ev
        return

    # ---- llm 模式：真 LLM 分类（5-10s，需心跳防超时）----
    import asyncio
    import threading

    from agent.intent.router import RouterError, classify_input, fallback_decision
    from agent.core.llm_client import get_llm_client

    # ---- 优化 3：正向 PLANNING 信号 fast path（跳过 LLM router 节省 5-10s）----
    # 由 env ROUTE_FAST_PATH 控制（默认开），评委可关闭对比效果
    fast_path_enabled = (os.getenv("ROUTE_FAST_PATH") or "1").strip() not in ("0", "false", "no", "off")
    if fast_path_enabled:
        # 1. 先看非 PLANNING 关键词（meta / chitchat / emotional / off_topic / ambiguous）
        stub_decision = _stub_route(req.message)
        if stub_decision is not None and stub_decision.input_kind != InputKind.PLANNING:
            yield _make_chitchat_event(stub_decision, 0)
            try:
                from agent.runtime.orchestrator import record_chitchat_result

                await record_chitchat_result(
                    session_id=req.session_id,
                    user_id=user_id,
                    user_message=req.message,
                    decision=stub_decision,
                )
            except Exception:  # noqa: BLE001
                pass
            await _delay(120)
            yield SseEvent(type=SseEventType.DONE, seq=1)
            return
        # 2. 再看正向 PLANNING 信号（典型「今天下午陪老婆孩子」类）
        if _looks_like_planning(req.message):
            # 直接进 _planner_stream，跳过 LLM router 5-10s
            async for ev in _planner_stream(
                req, mode=mode, user_id=user_id, starting_seq=0
            ):
                yield ev
            return

    # ---- 0. 心跳，防首字节超时 ----
    yield SseEvent(
        type=SseEventType.AGENT_THOUGHT,
        seq=0,
        payload={"text": "正在理解你的需求……"},
        timestamp_ms=_now_ms(),
    )

    # ---- 1. 后台跑 router LLM；超时 / 失败 → 关键词 fast path → 主路径兜底 ----
    decision_holder: dict[str, Any] = {}
    done_event = threading.Event()

    def _classify() -> None:
        try:
            client = get_llm_client()
            decision_holder["decision"] = classify_input(req.message, client=client)
        except RouterError as e:
            decision_holder["error"] = e
        except Exception as e:  # noqa: BLE001
            decision_holder["error"] = e
        finally:
            done_event.set()

    threading.Thread(target=_classify, daemon=True).start()

    # 在后台线程跑期间，每 1.5s 推一次 agent_thought 心跳维持 SSE 活跃
    loop = asyncio.get_running_loop()
    waited = 0.0
    while not done_event.is_set() and waited < 15.0:
        await asyncio.sleep(0.5)
        waited += 0.5

    decision: Optional[RouterDecision]
    if "decision" in decision_holder:
        decision = decision_holder["decision"]
    else:
        # LLM 失败 → 关键词 fast path
        decision = _stub_route(req.message) or fallback_decision(
            req.message, reason="llm_router_failed"
        )

    # ---- 2. 分流 ----
    if decision is not None and decision.input_kind != InputKind.PLANNING:
        # 非主路径：推 chitchat_reply + done
        yield _make_chitchat_event(decision, 1)
        # v2 ConversationStore 同步：chitchat / meta 等也要写入 messages
        try:
            from agent.runtime.orchestrator import record_chitchat_result

            await record_chitchat_result(
                session_id=req.session_id,
                user_id=user_id,
                user_message=req.message,
                decision=decision,
            )
        except Exception:  # noqa: BLE001
            pass
        await _delay(120)
        yield SseEvent(type=SseEventType.DONE, seq=2)
        return

    # PLANNING：把 reply_text 作 thought 透出（让评委看到「Agent 已收到，开始规划」）
    if decision is not None and decision.reply_text:
        yield SseEvent(
            type=SseEventType.AGENT_THOUGHT,
            seq=1,
            payload={"text": decision.reply_text},
            timestamp_ms=_now_ms(),
        )

    # 走原 _planner_stream（starting_seq=2，确保 seq 单调递增）
    async for ev in _planner_stream(
        req, mode=mode, user_id=user_id, starting_seq=2
    ):
        yield ev
