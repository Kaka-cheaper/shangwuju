"""tests.test_give_up_hard_tag_exhaustion_chips —— ADR-0014 决策 2（G-2）配套三件之一。

覆盖"hard 卡死"（候选彻底耗尽、LLM backprompt + ILS + rule 地板全部失败，
从未成功产出过方案）时的用户体验兜底：

1. `agent.planning.planners.rule_planner.relax_suggestion_chips`：
   - EMPTY_CANDIDATES 才给建议；其它失败原因返回空
   - 命中 hard tag 时追加"去掉这条要求"建议
   - 无 hard tag 时只给距离建议
2. `agent.graph.nodes.replan.ils_replan_node`：全部失败分支写入
   `state["give_up_chips"]`
3. `agent.graph.nodes.narrate.narrate_node`：itinerary=None 时不再裸返回
   `{"narration": None}`——产出兜底文案 + 透传 chips（旧行为是用户什么都
   看不到，界面像卡住了）
4. `agent.graph._emit_handlers.emit_narrate`：chips 挂进 AGENT_NARRATION
   payload（"无内容不加字段"，无 chips 时不出现该键）
"""

from __future__ import annotations

from agent.graph._emit_context import EmitContext
from agent.graph._emit_handlers import emit_narrate
from agent.graph.nodes.narrate import narrate_node
from agent.planning.planners.rule_planner import relax_suggestion_chips
from schemas.errors import FailureReason
from schemas.intent import IntentExtraction


def _intent(*, dietary_constraints=None, physical_constraints=None) -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-07-02T14:00",
        duration_hours=[3, 5],
        distance_max_km=10.0,
        companions=[],
        physical_constraints=physical_constraints or [],
        dietary_constraints=dietary_constraints or [],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="测试",
        parse_confidence=0.9,
    )


# ============================================================
# 1. relax_suggestion_chips
# ============================================================


def test_no_chips_when_failure_reason_is_not_empty_candidates():
    """非"约束太严"的失败原因（如画像加载失败）→ 不给放宽建议（文不对题）。"""
    intent = _intent(dietary_constraints=["不辣"])
    chips = relax_suggestion_chips(intent, FailureReason.NOT_FOUND)
    assert chips == []


def test_distance_chip_always_present_when_candidates_exhausted():
    """候选耗尽 → 至少给距离放宽建议（现成 refiner 关键词已能识别）。"""
    intent = _intent()  # 无 hard tag
    chips = relax_suggestion_chips(intent, FailureReason.EMPTY_CANDIDATES)
    assert len(chips) == 1
    assert chips[0].send == "距离可以再远一点"


def test_hard_tag_chip_appended_when_intent_has_hard_constraint():
    """命中 hard tag（无牛肉）→ 追加"去掉这条要求"建议，点名具体 tag。"""
    intent = _intent(dietary_constraints=["无牛肉", "日料"])
    chips = relax_suggestion_chips(intent, FailureReason.EMPTY_CANDIDATES)
    assert len(chips) == 2
    assert "无牛肉" in chips[1].label
    assert "无牛肉" in chips[1].send


def test_hard_tag_chip_checks_physical_too():
    """physical hard tag（无障碍）同样能命中，不只是 dietary。"""
    intent = _intent(physical_constraints=["无障碍", "亲子友好"])
    chips = relax_suggestion_chips(intent, FailureReason.EMPTY_CANDIDATES)
    assert len(chips) == 2
    assert "无障碍" in chips[1].send


# ============================================================
# 2+3. narrate_node 兜底文案 + chips 透传
# ============================================================


def test_narrate_node_gives_fallback_text_when_itinerary_none_with_chips():
    """itinerary=None（真正 hard 卡死）→ 不再裸返回 narration=None，产出
    兜底文案 + 透传 state.give_up_chips。
    """
    intent = _intent(dietary_constraints=["无牛肉"])
    chips_dump = [{"label": "去掉『无牛肉』试试", "send": "这次先不要无牛肉这个要求了"}]

    out = narrate_node({"intent": intent, "itinerary": None, "give_up_chips": chips_dump})

    assert out["narration"], "itinerary=None 时不应再返回空 narration，用户不该看到卡住"
    assert out["give_up_chips"] == chips_dump


def test_narrate_node_noop_when_intent_none():
    """intent 都没有 → 仍是真正的 no-op（与 finalize_plan_node 对称）。"""
    out = narrate_node({"intent": None, "itinerary": None})
    assert out == {"narration": None}


def test_narrate_node_fallback_text_without_chips_still_produced():
    """无 chips（如非 hard 卡死原因导致的失败）也仍给一句诚实兜底文案，
    不是"有 chips 才说话"。
    """
    intent = _intent()
    out = narrate_node({"intent": intent, "itinerary": None, "give_up_chips": []})
    assert out["narration"]
    assert out.get("give_up_chips") == []


# ============================================================
# 4. emit_narrate：chips 挂进 AGENT_NARRATION payload
# ============================================================


def test_emit_narrate_attaches_chips_when_present():
    ctx = EmitContext()
    chips_dump = [{"label": "放宽距离范围", "send": "距离可以再远一点"}]
    diff = {"narration": "排不出方案，试试这些建议？", "give_up_chips": chips_dump}

    events = emit_narrate(ctx, diff)
    assert len(events) == 1
    assert events[0].payload["chips"] == chips_dump


def test_emit_narrate_no_chips_key_when_empty():
    """"无内容不加字段"：没有 give_up_chips 时 payload 里不应出现 chips 键。"""
    ctx = EmitContext()
    diff = {"narration": "正常方案文案。"}

    events = emit_narrate(ctx, diff)
    assert "chips" not in events[0].payload
