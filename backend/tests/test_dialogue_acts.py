"""test_dialogue_acts —— 确认/预约规则识别 + route_turn 内的优先级顺序（ADR-0011 E-2-c）。

`classify_dialogue_act`/`DialogueAct`/`DialogueActResult` 已随"Layer 2 + Layer 3
塌缩为一次脑子调用"整体退役（见 `agent/core/dialogue_acts.py` 模块 docstring）：
- 提问（QUESTION）/ 提约束（SOFT_CONSTRAINT）本就是各自模块（itinerary_qa /
  soft_constraint_sniffer）的规则判定，现直接从 `route_turn.py` 调用；
- 预约（BOOKING）/ 确认（CONFIRM）仍留在本模块，是本模块现在唯一"自己拥有"的
  逻辑，两者现在都路由到 "confirm"（ADR-0011 决策 1，不再是旧世界的 "chitchat"）。

原"提问 > 预约 > 确认 > 提约束"优先级顺序现直接编码在 `route_turn.py` 的
Layer 1.8 级联里（不再有一个居中的 `classify_dialogue_act` 函数可单独测试
"顺序"），故行为契约测试改为直接驱动 `route_turn.route_turn()` 集成断言——
这也顺带验证这 4 个规则命中时确实不会触达脑子调用（省一次 LLM）。
"""

from __future__ import annotations

from agent.context.sources import GraphStateSource
from agent.core.dialogue_acts import (
    build_booking_decision,
    build_confirm_decision,
    looks_like_booking,
    looks_like_confirm,
)
from agent.graph.state import make_initial_state
from agent.routing.brain import RouteJudgment
from agent.routing.route_turn import route_turn


def _itin() -> dict:
    return {
        "nodes": [
            {"target_kind": "home", "target_id": "home"},
            {"target_kind": "poi", "target_id": "P001"},
            {"target_kind": "restaurant", "target_id": "R001"},
        ]
    }


# ============================================================
# 确认识别（CONFIRM）
# ============================================================


def test_confirm_pure_yes():
    assert looks_like_confirm("好的，就这个")
    assert looks_like_confirm("可以")
    assert looks_like_confirm("就这么定了")


def test_confirm_excludes_feedback_question_add():
    assert not looks_like_confirm("好的但是太远了"), "含反馈→不是纯确认"
    assert not looks_like_confirm("可以近一点吗"), "请求/反馈→不是确认"
    assert not looks_like_confirm("行，加个咖啡"), "追加→不是确认"
    assert not looks_like_confirm("我妈膝盖不好"), "提约束→不是确认"


def test_build_confirm_decision():
    d = build_confirm_decision("就这个")
    assert d is not None and d.input_kind.value == "confirm", (
        "ADR-0011 决策 1：确认独立出口，不再是 chitchat"
    )
    assert build_confirm_decision("太远了") is None


# ============================================================
# 预约指令（BOOKING）：给我预约吧 → 确认 chip，不重规划
# ============================================================


def test_booking_recognition():
    assert looks_like_booking("给我预约吧")
    assert looks_like_booking("帮我订吧")
    assert looks_like_booking("下单")
    assert not looks_like_booking("可以预约吗"), "提问不是预约指令"
    assert not looks_like_booking("别预约太远的"), "含反馈词→交回 feedback"
    assert not looks_like_booking("这版太赶了")


def test_build_booking_decision_has_confirm_chip():
    d = build_booking_decision("给我预约吧")
    assert d is not None and d.input_kind.value == "confirm"
    assert d.rationale == "dialogue_act_booking"
    assert len(d.cta_chips) == 1 and d.cta_chips[0].action == "confirm"


# ============================================================
# route_turn 集成：规则命中不触达脑子，且路由到正确标签
# ============================================================


def _brain_should_not_run(*args, **kwargs):
    raise AssertionError("规则命中不应再调脑子")


def _route(text: str, itinerary):
    state = make_initial_state(user_input=text, session_id="s")
    state["itinerary"] = itinerary
    return route_turn(
        text,
        itinerary,
        state.get("user_id"),
        client=object(),
        context_source=GraphStateSource(state),
        classify_fn=_brain_should_not_run,
    )


def test_route_turn_booking_not_feedback():
    out = _route("给我预约吧", _itin())
    assert out.kind == "confirm", f"预约指令不该重规划，实际 {out.kind}"
    assert any(c.action == "confirm" for c in out.decision.cta_chips)


def test_route_turn_confirm_not_replan():
    out = _route("好的，就这个", _itin())
    assert out.kind == "confirm", f"确认不该重规划，实际 {out.kind}"


def test_route_turn_question_runs_before_booking_confirm():
    out = _route("这家餐厅贵不贵", _itin())
    assert out.kind == "chitchat"
    assert "人均" in out.decision.reply_text


def test_route_turn_soft_constraint_runs_before_brain():
    out = _route("我妈膝盖不好走不远", _itin())
    assert out.kind == "chitchat"
    assert any("适合老人" in c.label for c in out.decision.cta_chips)


def test_route_turn_no_dialogue_act_rule_reaches_brain():
    """"还想加个喝咖啡的地方"不是提问/预约/确认/软约束，规则层都不该误吞它——
    真到达脑子调用，交给脑子判定（ADR-0011 决策 2 意图：这类"追加"现在该由
    脑子直接判 feedback，见 `agent/routing/brain_prompt.py` 少样本，不再依赖
    "识别不出就兜底"的间接路径）。本用例只验证规则层放行、请求真的传到了脑子，
    脑子本身的判断质量由 `test_routing_brain_real_llm.py` 冒烟验证。
    """

    def _fake_brain(context_text, user_input, has_itinerary, *, client):
        assert user_input == "还想加个喝咖啡的地方"
        assert has_itinerary is True
        return RouteJudgment(
            label="feedback", confidence=0.85, reply_text="收到，正在调整……", tone="warm"
        )

    state = make_initial_state(user_input="还想加个喝咖啡的地方", session_id="s2")
    state["itinerary"] = _itin()
    out = route_turn(
        "还想加个喝咖啡的地方",
        _itin(),
        state.get("user_id"),
        client=object(),
        context_source=GraphStateSource(state),
        classify_fn=_fake_brain,
    )
    assert out.kind == "feedback"
