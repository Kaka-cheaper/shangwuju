"""test_dialogue_acts —— 确认/预约规则识别 + route_turn 内的优先级顺序（ADR-0011 E-2-c）。

`classify_dialogue_act`/`DialogueAct`/`DialogueActResult` 已随"Layer 2 + Layer 3
塌缩为一次脑子调用"整体退役（见 `agent/core/dialogue_acts.py` 模块 docstring）：
- 提问（QUESTION）本就是 itinerary_qa 的规则判定，现直接从 `route_turn.py` 调用；
- 提约束（SOFT_CONSTRAINT）的路由角色已在对话轮路由规则层重构（2026-07-12）
  删除——原软约束嗅探器规则表命中即返的行为不再存在，这类输入现在规则层
  全数放行、落到脑子少样本判定（见 `agent/routing/brain_prompt.py`
  BRAIN_FEW_SHOTS），本文件的
  `test_route_turn_soft_constraint_no_longer_short_circuits_reaches_brain`
  钉住这个新契约；
- 预约（BOOKING）/ 确认（CONFIRM）仍留在本模块，是本模块现在唯一"自己拥有"的
  逻辑，两者现在都路由到 "confirm"（ADR-0011 决策 1，不再是旧世界的 "chitchat"），
  且两者的排除逻辑已收口成 `agent.core.coverage_gate` 的 per-rule 覆盖度闸
  （见 `agent/core/dialogue_acts.py` 模块 docstring 的收口说明）。

原"提问 > 预约 > 确认 > 提约束"优先级顺序现直接编码在 `route_turn.py` 的
Layer 1.8 级联里（不再有一个居中的 `classify_dialogue_act` 函数可单独测试
"顺序"），故行为契约测试改为直接驱动 `route_turn.route_turn()` 集成断言——
这也顺带验证 BOOKING/CONFIRM 规则命中时确实不会触达脑子调用（省一次 LLM）。
"""

from __future__ import annotations

from agent.context.sources import GraphStateSource
from agent.core.dialogue_acts import (
    CONFIRM_CTA_CHIP,
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
# B1（2026-07-04 路演前小修批）：确认词表剪枝——短词目子串碰撞
# ============================================================
# 设计契约（级联分类·规则层高精度）：规则层每一条词目必须单独接近百分百精度，
# 召回归脑子（LLM）管；规则层判错=确定性做错事、无兜底，故宁剪勿留。
# 实锤误判（路由勘察程序化实测 + 本批复核）：单字"行"作子串命中"银行/行程/还行"。


def test_confirm_not_fooled_by_hang_embedded_in_words():
    """"银行卡里没钱了"（银行）/"这个行程安排的，还行吧"（行程/还行）不是确认。

    单字"行"已从子串词表剪除——被剪话术自然落到脑子（慢 2-4 秒但判得对），
    这是设计预期不是回归。"还行吧"是温吞评价，同样交脑子带上下文判。
    """
    assert not looks_like_confirm("银行卡里没钱了，下次再说"), "『银行』子串碰撞"
    assert not looks_like_confirm("这个行程安排的，还行吧"), "『行程/还行吧』子串碰撞"


def test_confirm_standalone_hang_forms_still_work():
    """"行"家族只在独立成句（整句去标点后恰为该词）时算确认——精度近百分百的保留形式。"""
    for t in ("行", "行吧", "行啊", "那行", "行行行", "行！"):
        assert looks_like_confirm(t), f"{t!r} 独立成句应仍判确认"


def test_confirm_negated_words_not_confirm():
    """B1c 审计边界条件：确认词被否定词直接前缀（不可以/不确定）时不算确认。"""
    assert not looks_like_confirm("这样不可以")
    assert not looks_like_confirm("我还不确定")


def test_booking_negation_and_cancel_not_booking():
    """B1c 审计边界条件：否定前缀（先不预约）/取消语境（取消预约）不是预约指令。"""
    assert not looks_like_booking("先不预约了")
    assert not looks_like_booking("取消预约")


# ============================================================
# B3（2026-07-04 路演前小修批）：纯确认补「确认预约」action chip
# ============================================================


def test_build_confirm_decision_carries_confirm_chip():
    """纯确认也带同一枚「确认预约」action chip——对齐 booking 路径与脑子 confirm
    路径（`brain._apply_label_chip_policy` 钉死同一枚 CONFIRM_CTA_CHIP）。修复前
    `build_confirm_decision` 硬编码 cta_chips=[]，是全系统确认出口里唯一不带引导
    按钮的（壳2 canonical「就这样挺好」与 Layer 1.8 纯确认共用本构造器，一并补齐）。
    """
    d = build_confirm_decision("就这个")
    assert d is not None
    assert len(d.cta_chips) == 1 and d.cta_chips[0].action == "confirm"
    assert d.cta_chips[0] == CONFIRM_CTA_CHIP, "必须复用同一枚 chip，防两处各造导致漂移"


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


def test_route_turn_canonical_keep_has_confirm_chip():
    """B3：「就这样挺好」走壳2 canonical 字面确认路径，也应带「确认预约」chip
    （与其它确认路径同款，不再是全系统唯一无按钮的确认回复）。"""
    out = _route("就这样挺好", _itin())
    assert out.kind == "confirm"
    assert any(c.action == "confirm" for c in out.decision.cta_chips), (
        f"壳2 确认应带 action=confirm chip，实际 {out.decision.cta_chips}"
    )


def test_route_turn_bank_sentence_reaches_brain():
    """B1：「银行卡里没钱了，下次再说」规则层不拍板（"行"已剪），应放行到脑子。"""
    def _fake_brain(context_text, user_input, has_itinerary, *, client):
        assert user_input == "银行卡里没钱了，下次再说"
        return RouteJudgment(
            label="chitchat", confidence=0.8, reply_text="好，那咱们下次再约。", tone="warm"
        )

    state = make_initial_state(user_input="银行卡里没钱了，下次再说", session_id="s3")
    state["itinerary"] = _itin()
    out = route_turn(
        "银行卡里没钱了，下次再说",
        _itin(),
        state.get("user_id"),
        client=object(),
        context_source=GraphStateSource(state),
        classify_fn=_fake_brain,
    )
    assert out.kind == "chitchat", f"应交脑子判定为 chitchat，实际 {out.kind}"


def test_route_turn_question_runs_before_booking_confirm():
    out = _route("这家餐厅贵不贵", _itin())
    assert out.kind == "chitchat"
    assert "人均" in out.decision.reply_text


def test_route_turn_soft_constraint_no_longer_short_circuits_reaches_brain():
    """对话轮路由规则层重构（2026-07-12）：软约束嗅探器的路由角色已删除
    （BLOCK 1 决策 #2/B'——不建独立安全网，"提约束·没说改"的判定改由脑子
    少样本承接，见 `agent/routing/brain_prompt.py` BRAIN_FEW_SHOTS）。

    "我妈膝盖不好走不远"曾经是软约束嗅探器规则表命中"膝盖/走不远"关键词、
    在脑子调用之前就短路出"换成适合老人的"气泡的场景（见本测试文件被替换前
    的旧版本）。规则层删除后，这句话既不含 Layer 1 强反馈词、不含预约/确认
    锚点，也不是提问，规则层全数放行——本用例只验证它确实到达了脑子（不再
    被任何规则层短路吞掉），脑子本身如何判（clarify + 代码拼"换成X的" chip，
    还是别的）由 `test_routing_brain_real_llm.py` 真 LLM 冒烟验证，这里只
    垫桩钉住"规则层放行、请求真的传到了脑子"这一件事。
    """

    def _fake_brain(context_text, user_input, has_itinerary, *, client):
        assert user_input == "我妈膝盖不好走不远"
        assert has_itinerary is True
        return RouteJudgment(
            label="clarify",
            confidence=0.75,
            reply_text="要不要把这版换成更适合老人的？",
            tone="empathetic",
            cta_chips=[],
        )

    state = make_initial_state(user_input="我妈膝盖不好走不远", session_id="s4")
    state["itinerary"] = _itin()
    out = route_turn(
        "我妈膝盖不好走不远",
        _itin(),
        state.get("user_id"),
        client=object(),
        context_source=GraphStateSource(state),
        classify_fn=_fake_brain,
    )
    assert out.kind == "clarify", (
        f"规则层不该再短路这句话，应交脑子判定，实际 {out.kind}"
    )


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
