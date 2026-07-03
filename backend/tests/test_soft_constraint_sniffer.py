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

# ---- router_node 接入（ADR-0011 E-2-c：挂点从 emotional 机械换挂陪聊标签）----
# 软约束嗅探现在是 route_turn.py 的 Layer 1.8 规则判定（has_itinerary 时无条件
# 先跑，命中即返，不问脑子会判什么）——不再是"脑子判完 emotional 之后才二次
# 覆盖"的旧 Layer 3 结构。故命中场景下脑子（classify_turn）根本不会被调用；
# 只有规则不命中（含 sniff_llm 兜底也未命中）时才会真的落到脑子。


def test_router_soft_constraint_hit_injects_chip_without_brain(monkeypatch):
    """已有方案 + 句含软约束（规则命中）→ 注入「换成适合老人的」chip，且不触达脑子。"""
    from agent.graph.nodes import router as router_mod
    from agent.graph.state import make_initial_state

    def _brain_should_not_run(*a, **k):
        raise AssertionError("软约束规则命中不应再调脑子")

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(router_mod, "classify_turn", _brain_should_not_run)
    st = make_initial_state(user_input="我妈最近膝盖不好，走不远", session_id="s1")
    st["itinerary"] = {"summary": "上一轮方案"}

    out = router_mod.router_node(st)
    assert out["route_kind"] == "chitchat", "软约束气泡挂在 chitchat 标签下（原 emotional 已塌缩进陪聊）"
    decision = out["router_decision"]
    assert any("适合老人" in c.label for c in decision.cta_chips), (
        f"应注入软约束 chip，实际 {[c.label for c in decision.cta_chips]}"
    )
    # 共情话术：reply_text 定制成贴软约束的句子
    assert "歇脚" in decision.reply_text, f"reply_text 应被定制，实际 {decision.reply_text!r}"


def test_router_pure_emotion_no_soft_chip_falls_through_to_brain(monkeypatch):
    """纯情绪、规则不命中 → 落回脑子判定，气泡不带软约束 chip。"""
    from agent.graph.nodes import router as router_mod
    from agent.graph.state import make_initial_state
    from agent.routing.brain import RouteJudgment

    def _chitchat_judgment(*a, **k):
        return RouteJudgment(
            label="chitchat",
            confidence=0.8,
            reply_text="听起来有点烦呢",
            tone="empathetic",
            cta_chips=[],
            rationale="test",
        )

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(router_mod, "classify_turn", _chitchat_judgment)
    st = make_initial_state(user_input="我有点烦", session_id="s2")
    st["itinerary"] = {"summary": "上一轮方案"}

    out = router_mod.router_node(st)
    assert out["route_kind"] == "chitchat"
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
    assert d.input_kind.value == "chitchat", (
        "ADR-0011 E-2-c：挂点从 emotional 机械换挂陪聊标签（决策 1 陪聊塌缩吸收 emotional）"
    )
    assert any("适合老人" in c.label for c in d.cta_chips)
    assert "歇脚" in d.reply_text


def test_build_decision_explicit_revise_returns_none():
    # 明说改 → None（交给 feedback 重规划，不再问）
    assert build_soft_constraint_decision("帮我换成适合老人的", has_itinerary=True, client=None) is None


def test_build_decision_no_soft_constraint_returns_none():
    # 真反馈、无软约束 → None（交回兜底当 feedback）
    assert build_soft_constraint_decision("这版方案不太好", has_itinerary=True, client=None) is None


# ---- C3：软约束规则先于脑子命中（软约束没说改 → chitchat 气泡，不重规划）----
# 原"router L3 拆桶"用例驱动的是"Layer 2 判 ambiguous 之后 Layer 3 接管"的旧
# 结构；E-2-c 之后软约束判定在 Layer 1.8（脑子调用之前）无条件先跑，不再需要
# 先钉住脑子的判定结果才能验证这条路径，故不再 mock classify_turn 的返回值，
# 改用"脑子被调用即失败"的哨兵，直接证明规则层确实抢在脑子之前命中。


def _brain_should_not_run(*a, **k):
    raise AssertionError("软约束规则命中不应再调脑子")


def test_router_soft_constraint_no_revise_becomes_bubble(monkeypatch):
    from agent.graph.nodes import router as router_mod
    from agent.graph.state import make_initial_state

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(router_mod, "classify_turn", _brain_should_not_run)
    st = make_initial_state(user_input="我妈最近膝盖不太好，走不远", session_id="s1")
    st["itinerary"] = {"summary": "上一轮方案"}

    out = router_mod.router_node(st)
    # 不再被一刀切当 feedback：变成主动问的 chitchat 气泡
    assert out["route_kind"] == "chitchat", f"应主动问而非重规划，实际 {out['route_kind']}"
    assert any("适合老人" in c.label for c in out["router_decision"].cta_chips)


def test_router_real_feedback_without_soft_constraint_reaches_brain(monkeypatch):
    """ADR-0011 决策 2（E-1）行为反转 + E-2-c 挂点更新：本用例原名
    test_router_l3_ambiguous_real_feedback_still_feedback，原断言"认不出软约束的
    真反馈，仍兜成 feedback"正是靠 route_turn.py:300-302 的兜底归并做到的——该
    归并已被 E-1 删除（没有任何下游会"问"，实测硬猜重规划违反 L0 禁令 1）。

    "这版方案不太好"本身也是 ADR-0011 词表清洗删除的纯评价词（不指向任何可调
    参数），Layer 1 强信号 / 壳2 canonical / 软约束嗅探规则都识别不出来，
    应该真的落到脑子判定——本用例钉住"规则层放行、确实到达脑子"，脑子这里
    垫桩判 clarify（识别不出具体方向时的合理判断，质量由真 LLM 冒烟验证）。
    """
    from agent.graph.nodes import router as router_mod
    from agent.graph.state import make_initial_state
    from agent.routing.brain import RouteJudgment

    def _clarify_judgment(*a, **k):
        return RouteJudgment(
            label="clarify", confidence=0.7,
            reply_text="具体是哪里不太好呢？", tone="warm", cta_chips=[],
        )

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(router_mod, "classify_turn", _clarify_judgment)
    st = make_initial_state(user_input="这版方案不太好", session_id="s2")
    st["itinerary"] = {"summary": "上一轮方案"}

    out = router_mod.router_node(st)
    assert out["route_kind"] == "clarify", "识别不出具体方向时应澄清，不应默默重规划"
