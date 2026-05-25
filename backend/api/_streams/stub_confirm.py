"""_stub_confirm —— /chat/confirm demo fixture。

reserve_restaurant + generate_share_message 模拟 + memory 累积 + ConversationStore 同步。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from schemas import IntentExtraction, Itinerary, SseEvent, SseEventType

from ..health import _use_real_planner
from .._session_store import SESSION_STORE
from .._sse_helpers import delay as _delay
from .._sse_helpers import now_ms as _now_ms
from .memory import _accumulate_memory_after_confirm
from .models import ChatConfirmRequest


async def _stub_confirm(req: ChatConfirmRequest) -> AsyncIterator[SseEvent]:
    """MVP-2 stub：confirm → reserve_restaurant + generate_share_message。"""
    seq = 0

    def emit(type_: SseEventType, payload: dict[str, Any]) -> SseEvent:
        nonlocal seq
        ev = SseEvent(type=type_, seq=seq, payload=payload, timestamp_ms=_now_ms())
        seq += 1
        return ev

    if req.decision != "confirm":
        yield emit(
            SseEventType.AGENT_THOUGHT,
            {"text": f"已收到 {req.decision}，本次不执行预约。"},
        )
        yield emit(SseEventType.DONE, {})
        return

    # spec execution-quality-review R2：白名单校验（hallucination 防护）
    # stub 默认拿 R001；如果前端传入 allowed_restaurant_ids 且不含 R001 → 拒绝
    target_restaurant_id = "R001"
    if req.allowed_restaurant_ids is not None and target_restaurant_id not in req.allowed_restaurant_ids:
        yield emit(
            SseEventType.STREAM_ERROR,
            {
                "reason": "hallucination_blocked",
                "detail": (
                    f"reserve_restaurant 目标 {target_restaurant_id} 不在合法白名单 "
                    f"{req.allowed_restaurant_ids}；可能是 AI 在多轮反馈中编造的，已拦截"
                ),
            },
        )
        yield emit(SseEventType.DONE, {})
        return

    # reserve_restaurant
    yield emit(
        SseEventType.TOOL_CALL_START,
        {
            "tool": "reserve_restaurant",
            "input": {"restaurant_id": target_restaurant_id, "time": "17:30", "party_size": 3},
        },
    )
    await _delay(320)
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "reserve_restaurant",
            "output": {
                "success": True,
                "order_id": "R20260516_001",
                "restaurant_id": "R001",
                "confirmed_time": "17:30",
                "confirmed_party_size": 3,
            },
            "duration_ms": 180,
        },
    )
    await _delay()

    # generate_share_message
    yield emit(
        SseEventType.TOOL_CALL_START,
        {
            "tool": "generate_share_message",
            "input": {
                "itinerary_summary": "家庭半日方案 · 西溪亲子探索 + 健康晚餐",
                "social_context": "家庭日常",
                "audience": "妻子",
            },
        },
    )
    await _delay(420)
    share_msg = (
        "下午带宝贝去西溪森林儿童探索乐园玩 2 小时，17:30 已订好轻语沙拉的三人位，"
        "都是低脂健康餐你可以放心吃。打车 25 分钟到，玩完慢慢走过去就行～"
    )
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "generate_share_message",
            "output": {"success": True, "message": share_msg},
            "duration_ms": 220,
        },
    )
    await _delay()

    # 把订单与文案合并写回 itinerary 并再推一次 itinerary_ready
    cached = SESSION_STORE.get(req.session_id, {})
    itin_dict = dict(cached.get("itinerary") or {})
    if itin_dict:
        itin_dict["orders"] = [
            {
                "order_id": "R20260516_001",
                "kind": "餐厅预约",
                "target_kind": "restaurant",
                "target_id": "R001",
                "target_name": "轻语沙拉 · 西溪店",
                "detail": "17:30 三人位",
            }
        ]
        itin_dict["share_message"] = share_msg
        SESSION_STORE[req.session_id] = {**cached, "itinerary": itin_dict}
        # Phase 0.7：confirm 累积 memory（记录 itinerary 命中的所有 tag）
        _accumulate_memory_after_confirm(cached, itin_dict)
        yield emit(SseEventType.ITINERARY_READY, itin_dict)
        await _delay(140)

        # confirm 后的暖心收尾文案（"都给你搞定了"语气）
        confirm_narration: str | None = None
        try:
            from agent.intent.narrator import generate_narration

            cached_intent_dict = cached.get("intent") or {}
            if cached_intent_dict:
                intent_obj = IntentExtraction.model_validate(cached_intent_dict)
                itin_obj = Itinerary.model_validate(itin_dict)
                confirm_narration = generate_narration(
                    intent=intent_obj,
                    itinerary=itin_obj,
                    stage="confirm",
                    use_llm=_use_real_planner(),
                )
                yield emit(
                    SseEventType.AGENT_NARRATION,
                    {"text": confirm_narration, "stage": "confirm"},
                )
                await _delay(120)
        except Exception:  # noqa: BLE001
            pass

        # v2 ConversationStore 同步 hook（confirm 后状态升级 itinerary 含 orders）
        try:
            from agent.runtime.orchestrator import record_confirm_result

            final_itin = Itinerary.model_validate(itin_dict)
            await record_confirm_result(
                session_id=req.session_id,
                user_id=cached.get("user_id") or "demo_user",
                final_itinerary=final_itin,
                agent_message=confirm_narration or "已完成下单。",
            )
        except Exception:  # noqa: BLE001
            pass

    yield emit(SseEventType.DONE, {})
