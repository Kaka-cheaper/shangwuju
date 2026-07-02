"""test_soft_constraint_sniffer —— 闲聊软约束嗅探（规则版 + chip + 转正闭环）。

确定性测试，不打真 LLM（LLM 版另见 test_soft_constraint_sniffer_real_llm.py）。
重点验证三件事：
1. 规则表能从弦外之音抽出**词典内**的 tag，纯问候不误命中；
2. 嗅出的软约束拼成的 chip，其 send 文案能被路由判成 feedback（转正闭环不靠 LLM）；
3. augment 把软约束 chip 前置、整体不超过 4 个，无命中时原样返回。
"""

from __future__ import annotations

from agent.core.soft_constraint_sniffer import (
    _RULE_TABLE,
    _VALID_SOFT_TAGS,
    SoftConstraint,
    build_soft_constraint_chips,
    build_soft_constraint_decision,
    looks_like_explicit_revise,
    sniff_rule,
    sniff_soft_constraints,
)
from schemas.router import CtaChip


# ---- 规则版命中 ----

def test_rule_hits_elderly():
    hits = sniff_rule("我妈最近膝盖不太好，走不远")
    tags = {t for c in hits for t in c.tags}
    assert "适合老人" in tags
    assert "可休息" in tags


def test_rule_hits_spicy():
    hits = sniff_rule("我不太能吃辣的")
    tags = {t for c in hits for t in c.tags}
    assert tags == {"不辣"}


def test_rule_hits_crowd_to_quiet():
    hits = sniff_rule("那边周末人太多了，吵")
    tags = {t for c in hits for t in c.tags}
    assert "安静聊天" in tags


def test_rule_no_hit_on_greeting():
    assert sniff_rule("你好呀") == []
    assert sniff_rule("今天天气真不错") == []


def test_rule_dedups_repeated_tag():
    # "好累" 与 "孩子蔫" 都带 可休息；不该重复出现同一个 tag
    hits = sniff_rule("我好累，孩子也蔫了")
    flat = [t for c in hits for t in c.tags]
    assert len(flat) == len(set(flat)), f"tag 不应重复：{flat}"


# ---- 词典出口防御：规则表里的 tag 必须全部合法 ----

def test_all_rule_tags_are_valid_dictionary_tags():
    for _keywords, tags, *_rest in _RULE_TABLE:
        for t in tags:
            assert t in _VALID_SOFT_TAGS, f"规则表 tag 不在词典内：{t!r}"


# ---- 统一入口：规则命中时不依赖 client ----

def test_sniff_without_client_falls_back_to_rule():
    hits = sniff_soft_constraints("我妈膝盖不好", client=None)
    assert hits, "规则能命中时，client=None 也应返回结果"


def test_sniff_no_llm_when_disabled():
    # 规则不命中 + use_llm=False → 直接空，不报错
    assert sniff_soft_constraints("随便聊聊", client=None, use_llm=False) == []


# ---- chip 转正闭环：send 必须能被路由判成 feedback ----

def test_chip_send_routes_to_feedback_when_has_itinerary():
    chips = build_soft_constraint_chips(
        [SoftConstraint(tags=("适合老人", "可休息"), reason="老人腿脚不便")],
        has_itinerary=True,
    )
    assert chips
    send = chips[0].send
    # 关键闭环：C1 后转正不再靠 L1，而是点击后这句含"换成"祈使 → L3 判明说改 → feedback 重规划
    assert looks_like_explicit_revise(send), f"send 应被判为明说要改：{send!r}"
    # 且必须带着词典原词，refiner 才能并入
    assert "适合老人" in send


def test_chip_no_itinerary_is_planning_phrasing():
    chips = build_soft_constraint_chips(
        [SoftConstraint(tags=("适合老人",), reason="老人腿脚不便")],
        has_itinerary=False,
    )
    assert chips
    send = chips[0].send
    assert "安排" in send and "下午" in send


def test_chip_fields_within_pydantic_limits():
    chips = build_soft_constraint_chips(
        [SoftConstraint(tags=("适合老人", "可休息"), reason="老人腿脚不便")],
        has_itinerary=True,
    )
    # 能构造成功即说明 label≤24 / send≤200 未越界（CtaChip 有 Field 上限）
    assert all(isinstance(c, CtaChip) for c in chips)


# ---- augment：前置 + 截断 + 无命中原样返回 ----

# ---- router_node 接入：emotional/chitchat 分支才嗅探、注入软约束 chip ----

def _emotional_decision():
    from schemas.router import InputKind, RouterDecision

    return RouterDecision(
        input_kind=InputKind("emotional"),
        confidence=0.8,
        reply_text="照顾老人辛苦了",
        tone="empathetic",
        cta_chips=[],
        rationale="test",
    )


def test_router_emotional_with_itinerary_injects_soft_chip(monkeypatch):
    """已有方案 + LLM 判 emotional + 句含软约束 → 注入「换成适合老人的」chip。"""
    from agent.graph.nodes import router as router_mod
    from agent.graph.state import make_initial_state

    # 规则能命中「膝盖」，client 不会被真正调用（传 object() 即可）
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_input", lambda *a, **k: _emotional_decision()
    )
    st = make_initial_state(user_input="我妈最近膝盖不好，走不远", session_id="s1")
    st["itinerary"] = {"summary": "上一轮方案"}

    out = router_mod.router_node(st)
    assert out["route_kind"] == "emotional", "emotional 不该被 Layer 3 接管成 feedback"
    decision = out["router_decision"]
    assert any("适合老人" in c.label for c in decision.cta_chips), (
        f"应注入软约束 chip，实际 {[c.label for c in decision.cta_chips]}"
    )
    # 共情话术：reply_text 被换成贴软约束的句子，不再是原泛回话「照顾老人辛苦了」
    assert "歇脚" in decision.reply_text, f"reply_text 应被定制，实际 {decision.reply_text!r}"


def test_router_emotional_pure_emotion_no_soft_chip(monkeypatch):
    """纯情绪、规则不命中、LLM 不可用（object 客户端）→ 不注入 chip，气泡照常。"""
    from agent.graph.nodes import router as router_mod
    from agent.graph.state import make_initial_state

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_input", lambda *a, **k: _emotional_decision()
    )
    st = make_initial_state(user_input="我有点烦", session_id="s2")
    st["itinerary"] = {"summary": "上一轮方案"}

    out = router_mod.router_node(st)
    assert out["route_kind"] == "emotional"
    assert out["router_decision"].cta_chips == [], "无软约束不应凭空造 chip"


# ---- C1/C3：明说改判定 ----

def test_explicit_revise_detects_imperative():
    assert looks_like_explicit_revise("帮我换成适合老人的")
    assert looks_like_explicit_revise("这版去掉椰林餐厅")
    assert not looks_like_explicit_revise("我妈膝盖不好，走不远")
    assert not looks_like_explicit_revise("你好呀")


# ---- C3：build_soft_constraint_decision 三种归途 ----

def test_build_decision_soft_constraint_no_revise_returns_bubble():
    d = build_soft_constraint_decision("我妈膝盖不好走不远", has_itinerary=True, client=None)
    assert d is not None
    assert d.input_kind.value == "emotional"
    assert any("适合老人" in c.label for c in d.cta_chips)
    assert "歇脚" in d.reply_text


def test_build_decision_explicit_revise_returns_none():
    # 明说改 → None（交给 feedback 重规划，不再问）
    assert build_soft_constraint_decision("帮我换成适合老人的", has_itinerary=True, client=None) is None


def test_build_decision_no_soft_constraint_returns_none():
    # 真反馈、无软约束 → None（交回兜底当 feedback）
    assert build_soft_constraint_decision("这版方案不太好", has_itinerary=True, client=None) is None


# ---- C3：router L3 拆桶集成（ambiguous + 软约束没说改 → emotional 气泡，不再重规划）----

def _patch_classify_ambiguous(monkeypatch):
    from agent.graph.nodes import router as router_mod
    from schemas.router import InputKind, RouterDecision

    def _ambiguous(*a, **k):
        return RouterDecision(
            input_kind=InputKind("ambiguous"), confidence=0.7,
            reply_text="?", tone="warm", cta_chips=[], rationale="t",
        )

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(router_mod, "classify_input", _ambiguous)
    return router_mod


def test_router_l3_ambiguous_soft_constraint_becomes_bubble(monkeypatch):
    from agent.graph.state import make_initial_state

    router_mod = _patch_classify_ambiguous(monkeypatch)
    st = make_initial_state(user_input="我妈最近膝盖不太好，走不远", session_id="s1")
    st["itinerary"] = {"summary": "上一轮方案"}

    out = router_mod.router_node(st)
    # 不再被一刀切当 feedback：变成主动问的 emotional 气泡
    assert out["route_kind"] == "emotional", f"应主动问而非重规划，实际 {out['route_kind']}"
    assert any("适合老人" in c.label for c in out["router_decision"].cta_chips)


def test_router_l3_ambiguous_without_soft_constraint_asks_not_guesses(monkeypatch):
    """ADR-0011 决策 2（E-1）行为反转：本用例原名
    test_router_l3_ambiguous_real_feedback_still_feedback，原断言"认不出软约束的
    真反馈，仍兜成 feedback"正是靠 route_turn.py:300-302 的兜底归并做到的——该
    归并已被 E-1 删除（没有任何下游会"问"，实测硬猜重规划违反 L0 禁令 1）。

    "这版方案不太好"本身也是 ADR-0011 词表清洗删除的纯评价词（不指向任何可调
    参数），Layer 1 强信号 / 壳2 canonical / Layer 3 软约束嗅探都识别不出来，
    归并删除后不再有兜底把它硬掰成 feedback——正确行为是澄清（问"具体想改哪
    里"），而不是猜一个方向就动手重规划。
    """
    from agent.graph.state import make_initial_state

    router_mod = _patch_classify_ambiguous(monkeypatch)
    st = make_initial_state(user_input="这版方案不太好", session_id="s2")
    st["itinerary"] = {"summary": "上一轮方案"}

    out = router_mod.router_node(st)
    # 归并已删：认不出软约束/具体方向时，问一句而不是猜
    assert out["route_kind"] == "ambiguous", "识别不出具体方向时应澄清，不应默默重规划"
