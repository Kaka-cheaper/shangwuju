"""_stub_stream —— demo 主路径 SSE fixture（家庭主场景 + E1 异常 → 重规划）。

来自 main.py 拆分（spec code-modularization-refactor H1-final）；行为完全一致。

设计纪律：
- 不调 LLM、不调真 planner（demo 安全网）
- 与 api_contract.md §2 示例事件序列严格对齐
- intent_override 让 /chat/refine 复用本流程
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from schemas import (
    ActivityNode,
    Companion,
    Hop,
    IntentExtraction,
    Itinerary,
    ScheduleEntry,
    SseEvent,
    SseEventType,
)
from schemas.errors import FailureReason

from .._session_store import SESSION_STORE
from .._sse_helpers import delay as _delay
from .._sse_helpers import now_ms as _now_ms
from .models import ChatStreamRequest


async def _stub_stream(
    req: ChatStreamRequest,
    *,
    intent_override: Optional[IntentExtraction] = None,
    starting_seq: int = 0,
) -> AsyncIterator[SseEvent]:
    """对应 api_contract.md §2 示例事件序列（含 E1 异常 → 重规划 → 成功）。

    参数：
        intent_override: 若提供，跳过 fixture intent 直接用它；search_pois / search_restaurants
                         的 input 也会反映其 distance_max_km / 约束（用于 /chat/refine 复用）。
        starting_seq:    seq 起始值；refine 流复用主路径时 seq 从已经 emit 过的位置继续。

    注意：当前固定家庭主场景输出。P2 接入真实 planner 后，按意图差异化。
    """
    seq = starting_seq

    def emit(type_: SseEventType, payload: dict[str, Any]) -> SseEvent:
        nonlocal seq
        ev = SseEvent(type=type_, seq=seq, payload=payload, timestamp_ms=_now_ms())
        seq += 1
        return ev

    # ---- 0: intent_parsed ----
    if intent_override is not None:
        intent = intent_override
    else:
        intent = IntentExtraction(
            start_time="today_afternoon",
            duration_hours=[4, 6],
            distance_max_km=5,
            companions=[
                Companion(role="妻子", count=1),
                Companion(role="孩子", age=5, count=1),
            ],
            physical_constraints=["亲子友好", "适合 5-10 岁"],
            dietary_constraints=["低脂", "健康轻食"],
            experience_tags=[],
            social_context="家庭日常",
            raw_input=req.message,
            parse_confidence=0.88,
            ambiguous_fields=[],
        )
    # 仅当走主路径（/chat/stream）时推 intent_parsed；refine 已经推过 refinement_done，
    # 不再重复推 intent_parsed 避免前端重置 IntentSummary
    if intent_override is None:
        yield emit(SseEventType.INTENT_PARSED, intent.model_dump())
        await _delay()

    # ---- 1-2: get_user_profile ----
    yield emit(
        SseEventType.TOOL_CALL_START,
        {"tool": "get_user_profile", "input": {"user_id": "demo_user"}},
    )
    await _delay(220)
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "get_user_profile",
            "output": {
                "success": True,
                "profile": {
                    "user_id": "demo_user",
                    "home_location": {"name": "西溪居住区"},
                    "default_budget": 300.0,
                    "transport_preference": "taxi",
                },
            },
            "duration_ms": 80,
        },
    )
    await _delay()

    # ---- 3-4: search_pois ----
    yield emit(
        SseEventType.TOOL_CALL_START,
        {
            "tool": "search_pois",
            "input": {
                "distance_max_km": intent.distance_max_km,
                "physical_constraints": list(intent.physical_constraints),
                "experience_tags": list(intent.experience_tags),
                "social_context": intent.social_context,
                "age_in_party": [c.age for c in intent.companions if c.age is not None] or None,
            },
        },
    )
    await _delay(420)
    # 候选按 distance ≤ intent.distance_max_km 过滤
    _all_pois = [
        {"id": "P001", "name": "森林儿童探索乐园", "distance_km": 4.2, "rating": 4.6},
        {"id": "P004", "name": "西溪亲子动物园", "distance_km": 3.5, "rating": 4.5},
        {"id": "P007", "name": "童趣沙池公园", "distance_km": 2.8, "rating": 4.3},
    ]
    _poi_candidates = [p for p in _all_pois if p["distance_km"] <= intent.distance_max_km] or _all_pois[-1:]
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "search_pois",
            "output": {
                "success": True,
                "candidates": _poi_candidates,
            },
            "duration_ms": 120,
        },
    )
    await _delay()

    # ---- 5: agent_thought（流式打字效果可选）----
    yield emit(
        SseEventType.AGENT_THOUGHT,
        {"text": "命中 3 个亲子 POI，按距离与评分综合，优先「森林儿童探索乐园」。"},
    )
    await _delay(300)

    # ---- 6-7: search_restaurants ----
    yield emit(
        SseEventType.TOOL_CALL_START,
        {
            "tool": "search_restaurants",
            "input": {
                "distance_max_km": intent.distance_max_km,
                "dietary_constraints": list(intent.dietary_constraints),
                "social_context": intent.social_context,
            },
        },
    )
    await _delay(420)
    _all_restaurants = [
        {"id": "R001", "name": "轻语沙拉 · 西溪店", "distance_km": 2.1, "avg_price": 75},
        {"id": "R005", "name": "绿野食光", "distance_km": 3.0, "avg_price": 88},
    ]
    _rest_candidates = [r for r in _all_restaurants if r["distance_km"] <= intent.distance_max_km] or _all_restaurants[:1]
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "search_restaurants",
            "output": {
                "success": True,
                "candidates": _rest_candidates,
            },
            "duration_ms": 110,
        },
    )
    await _delay()

    # ---- 8-9: check_restaurant_availability 17:00 → 满（埋点 E1）----
    yield emit(
        SseEventType.TOOL_CALL_START,
        {
            "tool": "check_restaurant_availability",
            "input": {"restaurant_id": "R001", "time": "17:00", "party_size": 3},
        },
    )
    await _delay(260)
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "check_restaurant_availability",
            "output": {
                "success": True,
                "restaurant_id": "R001",
                "time": "17:00",
                "available": False,
                "queue_minutes": 0,
                "suggested_alternative_time": "17:30",
            },
            "duration_ms": 60,
        },
    )
    await _delay()

    # ---- 10: replan_triggered（评委要看的异常韧性证据）----
    yield emit(
        SseEventType.REPLAN_TRIGGERED,
        {
            "reason": FailureReason.RESTAURANT_FULL.value,
            "from_tool": "check_restaurant_availability",
        },
    )
    await _delay(300)

    # ---- 11-12: 改约 17:30，成功 ----
    yield emit(
        SseEventType.TOOL_CALL_START,
        {
            "tool": "check_restaurant_availability",
            "input": {"restaurant_id": "R001", "time": "17:30", "party_size": 3},
        },
    )
    await _delay(260)
    yield emit(
        SseEventType.TOOL_CALL_END,
        {
            "tool": "check_restaurant_availability",
            "output": {
                "success": True,
                "restaurant_id": "R001",
                "time": "17:30",
                "available": True,
                "queue_minutes": 0,
            },
            "duration_ms": 55,
        },
    )
    await _delay()

    # ---- 13: itinerary_ready ----
    # edge_v1：用 ActivityNode + Hop 构造，5 段旧 stages → 3 mid nodes（home / P001 / R001 / home）+ 4 hops
    nodes: list[ActivityNode] = [
        ActivityNode(
            node_id="n_home_start",
            kind="出发",
            target_kind="home",
            target_id="home",
            start_time="14:00",
            duration_min=0,
            title="从家出发",
            note="预估打车 25 分钟",
        ),
        ActivityNode(
            node_id="n_1",
            kind="主活动",
            target_kind="poi",
            target_id="P001",
            start_time="14:25",
            duration_min=155,
            title="森林儿童探索乐园 · 亲子游玩",
            note="5 岁年龄段适配，户外低强度",
        ),
        ActivityNode(
            node_id="n_2",
            kind="用餐",
            target_kind="restaurant",
            target_id="R001",
            start_time="17:30",
            duration_min=75,
            title="轻语沙拉 · 健康轻食晚餐",
            note="待你确认后为你预约 17:30 三人位",
        ),
        ActivityNode(
            node_id="n_home_end",
            kind="返回",
            target_kind="home",
            target_id="home",
            start_time="19:10",
            duration_min=0,
            title="回到家",
            note="预估打车 25 分钟",
        ),
    ]
    hops: list[Hop] = [
        Hop(
            hop_id="h_0",
            from_node_id="n_home_start",
            to_node_id="n_1",
            start_time="14:00",
            minutes=25,
            mode="taxi",
            path_type="estimated",
            buffer_min=0,
        ),
        Hop(
            hop_id="h_1",
            from_node_id="n_1",
            to_node_id="n_2",
            start_time="17:00",
            minutes=25,
            mode="walking",
            path_type="estimated",
            buffer_min=5,
        ),
        Hop(
            hop_id="h_2",
            from_node_id="n_2",
            to_node_id="n_home_end",
            start_time="18:45",
            minutes=25,
            mode="taxi",
            path_type="estimated",
            buffer_min=0,
        ),
    ]
    # 派生 schedule 视图：按 start_time 顺序展平 nodes + hops
    schedule: list[ScheduleEntry] = [
        ScheduleEntry(
            entry_kind="node", ref_id="n_home_start", start="14:00", end="14:00",
            title="从家出发", minutes=0, mode=None,
        ),
        ScheduleEntry(
            entry_kind="hop", ref_id="h_0", start="14:00", end="14:25",
            title="打车前往西溪湿地", minutes=25, mode="taxi",
        ),
        ScheduleEntry(
            entry_kind="node", ref_id="n_1", start="14:25", end="17:00",
            title="森林儿童探索乐园 · 亲子游玩", minutes=155, mode=None,
        ),
        ScheduleEntry(
            entry_kind="hop", ref_id="h_1", start="17:00", end="17:25",
            title="步行 + 短途打车至轻语沙拉", minutes=25, mode="walking",
        ),
        ScheduleEntry(
            entry_kind="node", ref_id="n_2", start="17:30", end="18:45",
            title="轻语沙拉 · 健康轻食晚餐", minutes=75, mode=None,
        ),
        ScheduleEntry(
            entry_kind="hop", ref_id="h_2", start="18:45", end="19:10",
            title="打车回家", minutes=25, mode="taxi",
        ),
        ScheduleEntry(
            entry_kind="node", ref_id="n_home_end", start="19:10", end="19:10",
            title="回到家", minutes=0, mode=None,
        ),
    ]
    itinerary = Itinerary(
        summary="家庭半日方案 · 西溪亲子探索 + 健康晚餐",
        nodes=nodes,
        hops=hops,
        schedule=schedule,
        orders=[],
        share_message=None,
        total_minutes=310,
    )
    SESSION_STORE[req.session_id] = {
        "intent": intent.model_dump(),
        "itinerary": itinerary.model_dump(),
    }
    yield emit(SseEventType.ITINERARY_READY, itinerary.model_dump())
    await _delay(150)

    # ---- 13.5: agent_narration（暖心开场白；stub 模式走模板）----
    try:
        from agent.intent.narrator import generate_narration

        narration_text = generate_narration(
            intent=intent,
            itinerary=itinerary,
            stage="stream",
            use_llm=False,  # stub 模式：纯模板，不调 LLM
        )
        yield emit(
            SseEventType.AGENT_NARRATION,
            {"text": narration_text, "stage": "stream"},
        )
        await _delay(120)
    except Exception:  # noqa: BLE001
        # narration 失败不阻塞主流程
        narration_text = None

    # ---- v2 ConversationStore 同步 hook（stub 路径也持久化让 /chat/turn 能用）----
    try:
        from agent.runtime.orchestrator import (
            record_planning_result,
            record_refinement_result,
        )

        agent_msg = (narration_text if narration_text else None) or f"已为你规划：{itinerary.summary}"

        if intent_override is not None:
            await record_refinement_result(
                session_id=req.session_id,
                user_id=getattr(req, "user_id", None) or "demo_user",
                refined_intent=intent,
                new_itinerary=itinerary,
                feedback_text=req.message,
                agent_message=agent_msg,
            )
        else:
            await record_planning_result(
                session_id=req.session_id,
                user_id=getattr(req, "user_id", None) or "demo_user",
                intent=intent,
                itinerary=itinerary,
                user_message=req.message,
                agent_message=agent_msg,
            )
    except Exception:  # noqa: BLE001
        pass

    # ---- 14: done ----
    yield emit(SseEventType.DONE, {})
