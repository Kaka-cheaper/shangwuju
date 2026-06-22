"""test_dialogue_acts —— 会话内对话行为收口（确认识别 + classify_dialogue_act 统一判定）。

确定性测试。验证：确认识别及其排除（反馈/疑问/追加不算确认）、分类的优先级顺序
（提问 > 确认 > 提约束）、router 集成（确认不重规划、追加走 feedback）。

行为契约不变：同输入得同对话行为（DialogueAct），同路由目标（由 route_turn 映射）。
"""

from __future__ import annotations

from agent.core.dialogue_acts import (
    DialogueAct,
    build_booking_decision,
    build_confirm_decision,
    classify_dialogue_act,
    looks_like_booking,
    looks_like_confirm,
)


def _itin() -> dict:
    return {
        "nodes": [
            {"target_kind": "home", "target_id": "home"},
            {"target_kind": "poi", "target_id": "P001"},
            {"target_kind": "restaurant", "target_id": "R001"},
        ]
    }


# ---- 确认识别（CONFIRM 对话行为）----

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
    assert d is not None and d.input_kind.value == "chitchat"
    assert build_confirm_decision("太远了") is None


# ---- classify_dialogue_act 优先级：提问 > 确认 > 提约束 ----

def test_classify_question_first():
    result = classify_dialogue_act("这家餐厅贵不贵", _itin(), client=None)
    assert result is not None
    assert result.act == DialogueAct.QUESTION
    assert "人均" in result.decision.reply_text


def test_classify_confirm():
    result = classify_dialogue_act("好的就这个", _itin(), client=None)
    assert result is not None
    assert result.act == DialogueAct.CONFIRM
    assert result.decision.rationale == "dialogue_act_confirm"


# ---- 预约指令（BOOKING）：给我预约吧 → 确认 chip，不重规划 ----

def test_booking_recognition():
    assert looks_like_booking("给我预约吧")
    assert looks_like_booking("帮我订吧")
    assert looks_like_booking("下单")
    assert not looks_like_booking("可以预约吗"), "提问不是预约指令"
    assert not looks_like_booking("别预约太远的"), "含反馈词→交回 feedback"
    assert not looks_like_booking("这版太赶了")


def test_build_booking_decision_has_confirm_chip():
    d = build_booking_decision("给我预约吧")
    assert d is not None and d.input_kind.value == "chitchat"
    assert d.rationale == "dialogue_act_booking"
    assert len(d.cta_chips) == 1 and d.cta_chips[0].action == "confirm"


def test_classify_booking_before_confirm():
    # 预约指令走 BOOKING（chitchat + confirm chip），不落 feedback
    result = classify_dialogue_act("给我预约吧", _itin(), client=None)
    assert result is not None
    assert result.act == DialogueAct.BOOKING
    assert result.decision.rationale == "dialogue_act_booking"
    assert any(c.action == "confirm" for c in result.decision.cta_chips)


def test_router_booking_not_feedback(monkeypatch):
    from agent.graph.state import make_initial_state

    router_mod = _patch_ambiguous(monkeypatch)
    st = make_initial_state(user_input="给我预约吧", session_id="sb")
    st["itinerary"] = _itin()
    out = router_mod.router_node(st)
    assert out["route_kind"] == "chitchat", f"预约指令不该重规划，实际 {out['route_kind']}"
    assert any(c.action == "confirm" for c in out["router_decision"].cta_chips)


def test_classify_soft_constraint():
    result = classify_dialogue_act("我妈膝盖不好走不远", _itin(), client=None)
    assert result is not None
    assert result.act == DialogueAct.SOFT_CONSTRAINT
    assert any("适合老人" in c.label for c in result.decision.cta_chips)


def test_classify_none_for_real_feedback():
    assert classify_dialogue_act("这版太赶了", _itin(), client=None) is None


def test_classify_none_for_add():
    # 追加不在这里拦，交回兜底（→ feedback → refiner 增量合并）
    assert classify_dialogue_act("还想加个喝咖啡的地方", _itin(), client=None) is None


# ---- router 集成：确认→chitchat 不重规划；追加→feedback ----

def _patch_ambiguous(monkeypatch):
    from agent.graph.nodes import router as router_mod
    from schemas.router import InputKind, RouterDecision

    def _amb(*a, **k):
        return RouterDecision(
            input_kind=InputKind("ambiguous"), confidence=0.7,
            reply_text="?", tone="warm", cta_chips=[], rationale="t",
        )

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(router_mod, "classify_input", _amb)
    return router_mod


def test_router_confirm_not_replan(monkeypatch):
    from agent.graph.state import make_initial_state

    router_mod = _patch_ambiguous(monkeypatch)
    st = make_initial_state(user_input="好的，就这个", session_id="s1")
    st["itinerary"] = _itin()
    out = router_mod.router_node(st)
    assert out["route_kind"] == "chitchat", f"确认不该重规划，实际 {out['route_kind']}"


def test_router_add_goes_feedback(monkeypatch):
    from agent.graph.state import make_initial_state

    router_mod = _patch_ambiguous(monkeypatch)
    st = make_initial_state(user_input="还想加个喝咖啡的地方", session_id="s2")
    st["itinerary"] = _itin()
    out = router_mod.router_node(st)
    assert out["route_kind"] == "feedback", "追加应走 feedback→refiner 增量合并"
