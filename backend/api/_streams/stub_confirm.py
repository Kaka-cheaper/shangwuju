"""_stub_confirm —— /chat/confirm demo fixture。

reserve_restaurant + order_extra_service + generate_share_message 模拟
memory 累积 + ConversationStore 同步。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import tools  # noqa: F401  触发 order_extra_service 等执行类 Tool 注册
from schemas import IntentExtraction, Itinerary, SseEvent, SseEventType
from schemas.tools import OrderExtraServiceInput, OrderExtraServiceOutput
from tools.registry import invoke_tool

from ..health import _use_real_planner
from .._session_store import SESSION_STORE
from .._sse_helpers import delay as _delay
from .._sse_helpers import now_ms as _now_ms
from .memory import _accumulate_memory_after_confirm
from .models import ChatConfirmRequest


async def _stub_confirm(
    req: ChatConfirmRequest,
    *,
    mode: str = "rule",
) -> AsyncIterator[SseEvent]:
    """MVP-2 stub：confirm → reserve_restaurant + order_extra_service + generate_share_message。

    mode 控制 confirm 阶段 narration 是否调 LLM：
    - "rule"：use_llm=False 走模板（毫秒级）
    - "llm" ：use_llm=True 调 LLM 出有"人味"文案（15-25s）
    """
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

    cached = SESSION_STORE.get(req.session_id, {})
    itin_dict = dict(cached.get("itinerary") or {})
    restaurant_nodes = [
        n for n in (itin_dict.get("nodes") or [])
        if n.get("target_kind") == "restaurant"
    ]

    # spec execution-quality-review R2：白名单校验（hallucination 防护）
    # 先用当前 itinerary 中的首个餐厅；缺省时沿用 R001 demo fallback。
    first_restaurant = restaurant_nodes[0] if restaurant_nodes else {}
    target_restaurant_id = first_restaurant.get("target_id") or "R001"
    target_restaurant_name = first_restaurant.get("title") or "轻语沙拉 · 西溪店"
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
            "input": {
                "restaurant_id": target_restaurant_id,
                "time": "17:30",
                "party_size": 3,
            },
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
                "restaurant_id": target_restaurant_id,
                "confirmed_time": "17:30",
                "confirmed_party_size": 3,
            },
            "duration_ms": 180,
        },
    )
    await _delay()

    # order_extra_service：生日蛋糕 / 鲜花等附加服务。
    extra_orders: list[dict[str, Any]] = []
    intent_dict = cached.get("intent") or {}
    extra_services = [
        str(x).strip()
        for x in (intent_dict.get("extra_services") or [])
        if str(x).strip()
    ]
    social_context = intent_dict.get("social_context") or "家庭日常"
    for service_type in extra_services:
        inp = OrderExtraServiceInput(
            service_type=service_type,
            target_kind="restaurant",
            target_id=target_restaurant_id,
            quantity=1,
            scheduled_time="17:30",
            recipient_note=f"{social_context}场景",
        )
        yield emit(
            SseEventType.TOOL_CALL_START,
            {"tool": "order_extra_service", "input": inp.model_dump()},
        )
        await _delay(260)
        result = invoke_tool("order_extra_service", inp.model_dump())
        yield emit(
            SseEventType.TOOL_CALL_END,
            {
                "tool": "order_extra_service",
                "output": _jsonable_output(result.output),
                "duration_ms": result.duration_ms,
            },
        )
        if result.success:
            out = OrderExtraServiceOutput.model_validate(result.output)
            if out.success and out.order_id:
                service_name = out.service.name if out.service else service_type
                extra_orders.append(
                    {
                        "order_id": out.order_id,
                        "kind": f"{out.service_type}加购",
                        "target_kind": "restaurant",
                        "target_id": target_restaurant_id,
                        "target_name": target_restaurant_name,
                        "detail": (
                            f"{out.scheduled_time or '17:30'} 送达 / "
                            f"{service_name} x{out.quantity or 1} / "
                            f"总价 {out.total_price or 0} 元"
                        ),
                    }
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
    if itin_dict:
        itin_dict["orders"] = [
            {
                "order_id": "R20260516_001",
                "kind": "餐厅预约",
                "target_kind": "restaurant",
                "target_id": target_restaurant_id,
                "target_name": target_restaurant_name,
                "detail": "17:30 三人位",
            },
            *extra_orders,
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
                    use_llm=(mode != "rule" and _use_real_planner()),
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


def _jsonable_output(output: dict[str, Any]) -> dict[str, Any]:
    data = dict(output or {})
    reason = data.get("reason")
    if hasattr(reason, "value"):
        data["reason"] = reason.value
    return data
