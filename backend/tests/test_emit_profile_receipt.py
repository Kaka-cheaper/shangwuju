"""tests.test_emit_profile_receipt —— 信任带①拍画像收据后端数据源回归。

覆盖（见 路演PPT/信任带设计终稿.md 2026-07-11 修订「五收据」画像行 +
`agent.graph._emit_handlers._consumed_profile_fields` docstring）：

1. `intent.field_provenance` 里某个受控词典字段的元素标为 "prior" →
   `get_user_profile_worker` 的 tool_call_end.output 携带 "profile_fields"，
   内容只含该字段该元素（忠实不编：不把整个字段搬进来，只搬真被标 prior 的
   那几个值）。
2. 全部字段都不含 prior 标注（都是 user_stated/inferred/default）→ 不挂
   "profile_fields" 键（"无真实消费不加字段"纪律，同 preview 的既有先例）。
3. `intent.field_provenance` 为 None（旧 checkpoint / 未跑校正）→ 同样不挂字段
   （不强行倒推）。
4. 三个受控词典字段（dietary_constraints / physical_constraints /
   experience_tags）各自独立判定，互不影响。
5. `ctx.last_intent` 由 `emit_intent` / `emit_refiner` 累积——`get_user_profile_
   worker` 的 emit（`emit_fanout_worker`）读的是 ctx 累积值，不是自己 diff 里
   的字段（它的 diff 只有 user_profile 键，从无 intent）。
6. UserProfile 结构性字段（home_location/transport_preference 等）不在
   `_consumed_profile_fields` 覆盖范围内——即使 provenance 里出现同名键也不应
   被这个函数误当画像收据源（因为它们本来就不在 field_provenance 覆盖范围，
   语义上是 assemble 阶段路线计算输入，不是"意图被画像先验改写"）。
"""

from __future__ import annotations

from agent.graph._emit_context import EmitContext
from agent.graph._emit_handlers import (
    _consumed_profile_fields,
    emit_fanout_worker,
    emit_intent,
)
from schemas.intent import IntentExtraction
from schemas.sse import SseEventType


def _make_intent(
    *,
    dietary_constraints: list[str] | None = None,
    physical_constraints: list[str] | None = None,
    experience_tags: list[str] | None = None,
    field_provenance: dict[str, str] | None = None,
) -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-07-11T18:00",
        duration_hours=[4, 6],
        distance_max_km=5.0,
        companions=[],
        physical_constraints=physical_constraints or [],
        dietary_constraints=dietary_constraints or [],
        experience_tags=experience_tags or [],
        social_context="家庭日常",
        raw_input="测试输入",
        parse_confidence=0.9,
        field_provenance=field_provenance,
    )


def _end_output(events):
    end_events = [e for e in events if e.type == SseEventType.TOOL_CALL_END]
    assert len(end_events) == 1
    return end_events[0].payload["output"]


class _FakeProfileOutput:
    """占位 GetUserProfileOutput——emit_fanout_worker 只判定 truthy/falsy 与
    是否为 None，不深读字段（同既有 test_emit_fanout_search_preview.py 的
    `object()` 占位手法）。"""


def test_prior_dietary_tag_surfaces_as_profile_field():
    ctx = EmitContext()
    intent = _make_intent(
        dietary_constraints=["不辣", "日料"],
        field_provenance={"dietary_constraints:日料": "prior", "dietary_constraints:不辣": "user_stated"},
    )
    emit_intent(ctx, {"intent": intent})

    events = emit_fanout_worker(ctx, "get_user_profile_worker", {"user_profile": _FakeProfileOutput()})
    payload = _end_output(events)

    assert "profile_fields" in payload
    fields = {f["field"]: f["tags"] for f in payload["profile_fields"]}
    assert fields == {"dietary_constraints": ["日料"]}


def test_no_prior_provenance_omits_profile_fields_key():
    ctx = EmitContext()
    intent = _make_intent(
        dietary_constraints=["不辣"],
        field_provenance={"dietary_constraints:不辣": "user_stated"},
    )
    emit_intent(ctx, {"intent": intent})

    events = emit_fanout_worker(ctx, "get_user_profile_worker", {"user_profile": _FakeProfileOutput()})
    payload = _end_output(events)

    assert "profile_fields" not in payload


def test_missing_field_provenance_omits_profile_fields_key():
    ctx = EmitContext()
    intent = _make_intent(dietary_constraints=["不辣"], field_provenance=None)
    emit_intent(ctx, {"intent": intent})

    events = emit_fanout_worker(ctx, "get_user_profile_worker", {"user_profile": _FakeProfileOutput()})
    payload = _end_output(events)

    assert "profile_fields" not in payload


def test_three_controlled_fields_judged_independently():
    ctx = EmitContext()
    intent = _make_intent(
        dietary_constraints=["日料"],
        physical_constraints=["适合老人"],
        experience_tags=["安静聊天"],
        field_provenance={
            "dietary_constraints:日料": "prior",
            "physical_constraints:适合老人": "user_stated",
            "experience_tags:安静聊天": "prior",
        },
    )
    emit_intent(ctx, {"intent": intent})

    events = emit_fanout_worker(ctx, "get_user_profile_worker", {"user_profile": _FakeProfileOutput()})
    payload = _end_output(events)

    fields = {f["field"] for f in payload["profile_fields"]}
    assert fields == {"dietary_constraints", "experience_tags"}


def test_last_intent_accumulated_from_emit_intent_not_from_worker_diff():
    """get_user_profile_worker 自己的 diff 从不含 intent——profile_fields 必须
    来自 ctx.last_intent（由更早派发的 emit_intent 写入），不是本函数入参 diff。"""
    ctx = EmitContext()
    intent = _make_intent(
        dietary_constraints=["日料"],
        field_provenance={"dietary_constraints:日料": "prior"},
    )
    emit_intent(ctx, {"intent": intent})
    assert ctx.last_intent is intent

    # get_user_profile_worker 的真实 diff 形状：只有 user_profile 一个键
    events = emit_fanout_worker(ctx, "get_user_profile_worker", {"user_profile": _FakeProfileOutput()})
    payload = _end_output(events)
    assert payload.get("profile_fields")


def test_structural_profile_fields_not_covered_by_provenance_projection():
    """home_location/transport_preference 等结构性字段不在 3 个受控词典字段
    覆盖范围内——即使调用方错误地在 field_provenance 塞了同名键也不会被
    _consumed_profile_fields 采纳（该函数只看 _PROFILE_TAG_FIELD_LABEL 里
    登记的三个字段名）。"""
    intent = _make_intent(
        field_provenance={"home_location:prior": "prior", "transport_preference": "prior"},
    )
    assert _consumed_profile_fields(intent) == []


def test_none_intent_returns_empty_list():
    assert _consumed_profile_fields(None) == []
