"""_stub_confirm —— /chat/confirm demo fixture。

reserve_restaurant + order_extra_service + generate_share_message 模拟
memory 累积 + ConversationStore 同步。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import tools  # noqa: F401  触发执行类 Tool 注册
from schemas import IntentExtraction, Itinerary, SseEvent, SseEventType

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
    if not itin_dict:
        yield emit(SseEventType.DONE, {})
        return

    # 白名单（hallucination 防护）：方案首个餐厅须在合法白名单内（前端未传则跳过）。
    restaurant_nodes = [
        n for n in (itin_dict.get("nodes") or [])
        if n.get("target_kind") == "restaurant"
    ]
    first_restaurant_id = (
        restaurant_nodes[0].get("target_id") if restaurant_nodes else None
    ) or "R001"
    if (
        req.allowed_restaurant_ids is not None
        and first_restaurant_id not in req.allowed_restaurant_ids
    ):
        yield emit(
            SseEventType.STREAM_ERROR,
            {
                "reason": "hallucination_blocked",
                "detail": (
                    f"reserve_restaurant 目标 {first_restaurant_id} 不在合法白名单 "
                    f"{req.allowed_restaurant_ids}；可能是 AI 在多轮反馈中编造的，已拦截"
                ),
            },
        )
        yield emit(SseEventType.DONE, {})
        return

    # 工具前移（spec dialogue-act-routing）：与主路径 execute_finalize 共用同一个执行核
    # replay_confirm_actions——优先 replay 规划期锁好的动作清单，没有则按 intent 现算。
    # stub 仅保留自己的 SSE 动画节奏 + memory 累积，**不切换记忆系统**（C8 两套记忆问题另议）。
    from agent.graph.nodes.execute_finalize import (
        build_confirm_actions,
        replay_confirm_actions,
    )

    itin_obj = Itinerary.model_validate(itin_dict)
    intent_dict = cached.get("intent") or {}
    intent_obj = IntentExtraction.model_validate(intent_dict) if intent_dict else None
    actions = list(itin_obj.pending_actions) or build_confirm_actions(itin_obj, intent_obj)
    orders, exec_results, share_msg = replay_confirm_actions(actions)

    # 回放工具调用（带 delay，保留 stub 的演示节奏）
    for item in exec_results:
        yield emit(
            SseEventType.TOOL_CALL_START,
            {"tool": item["tool"], "input": item.get("input") or {}},
        )
        await _delay(280)
        yield emit(
            SseEventType.TOOL_CALL_END,
            {
                "tool": item["tool"],
                "output": item.get("output") or {},
                "duration_ms": item.get("duration_ms") or 0,
            },
        )
        await _delay()

    # 写回订单 + 文案，再推一次 itinerary_ready
    itin_dict["orders"] = [o.model_dump() for o in orders]
    itin_dict["share_message"] = share_msg
    SESSION_STORE[req.session_id] = {**cached, "itinerary": itin_dict}
    # Phase 0.7：confirm 累积 memory（stub 沿用 memory_store，不切到 memory_writer）
    _accumulate_memory_after_confirm(cached, itin_dict)
    yield emit(SseEventType.ITINERARY_READY, itin_dict)
    await _delay(140)

    # confirm 收尾文案（intent 缺省时跳过，不挡流）
    confirm_narration: str | None = None
    try:
        from agent.intent.narrator import generate_narration

        if intent_obj is not None:
            confirm_narration = generate_narration(
                intent=intent_obj,
                itinerary=Itinerary.model_validate(itin_dict),
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

        await record_confirm_result(
            session_id=req.session_id,
            user_id=cached.get("user_id") or "demo_user",
            final_itinerary=Itinerary.model_validate(itin_dict),
            agent_message=confirm_narration or "已完成下单。",
        )
    except Exception:  # noqa: BLE001
        pass

    yield emit(SseEventType.DONE, {})
