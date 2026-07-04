"""router_node 三层反馈识别集成测试（spec feedback-routing-fix Task 3 / R1 R3 R4 R6；
ADR-0011 决策 2 / E-1 行为反转 + E-2-c 统一脑子塌缩均已同步到本文件）。

用垫桩脑子判定（mock `classify_turn`）隔离 LLM 不确定性，专测 router_node 的
规则级联：
- R1：has_itinerary + 长反馈 → feedback（Layer 1 强信号词典命中，不依赖归并，
  也不触达脑子）
- R6.4：无 itinerary → 行为与原逻辑一致（不进新分支）

ADR-0011 决策 2（E-1）删除了 route_turn.py 原 :300-302 的兜底归并
（"has_itinerary + planning/ambiguous → 强制 feedback"）——没有任何下游会
"问"，实测被硬猜重规划违反 L0 禁令 1。本文件里**纯靠归并**才成立的用例已删除
或翻转（见各用例内联说明）；**靠 Layer 1 强信号词典**命中的用例不受影响
（Layer 1 在归并之前、且未被 E-1/E-2-c 触碰）。

E-2-c 迁移：`classify_input`（RouterDecision 形状）→ `classify_turn`
（RouteJudgment 形状），旧 "ambiguous" 标签改名 "clarify"，"meta"/"off_topic"
塌缩/改名（meta 併入 chitchat，off_topic 改名 defense）。
"""

from __future__ import annotations

import pytest

from agent.graph.nodes import router as router_mod
from agent.graph.state import make_initial_state
from agent.routing.brain import RouteJudgment


def _make_judgment(label: str) -> RouteJudgment:
    return RouteJudgment(
        label=label,
        confidence=0.8,
        reply_text="ok",
        tone="warm",
        cta_chips=[],
        rationale="test",
    )


def _state_with_itinerary(user_input: str):
    """构造带 itinerary 的 state（模拟 checkpointer 跨 turn 恢复了上一轮方案）。"""
    st = make_initial_state(user_input=user_input, session_id="s1")
    st["itinerary"] = {"summary": "上一轮方案"}  # 非空即可触发 has_itinerary
    return st


# ---- R1：has_itinerary + 长反馈 → feedback（Layer 1 强信号词典命中）----

# 这 2 句都含 _STRONG_FEEDBACK_KEYWORDS 里的强信号词（"太赶"/"便宜点"），
# Layer 1 在脑子调用之前就直接判 feedback——不依赖已删除的兜底归并，
# 无论脑子判什么都不影响结果（下面刻意仍 mock 成 clarify，证明这条路径确实是
# Layer 1 短路，不是靠归并才凑出来的 feedback）。
_LONG_FEEDBACK_STRONG_SIGNAL = [
    "整体节奏对孩子来说太赶了",
    "这个预算有点超了，能不能找便宜点的",
]


@pytest.mark.parametrize("text", _LONG_FEEDBACK_STRONG_SIGNAL)
def test_long_feedback_with_strong_signal_routes_to_feedback(monkeypatch, text):
    """R1：已有方案 + 含强信号词的长反馈 → feedback（Layer 1 命中，不调脑子）。"""
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_turn", lambda *a, **k: _make_judgment("clarify")
    )
    out = router_mod.router_node(_state_with_itinerary(text))
    assert out["route_kind"] == "feedback", f"{text!r} 应路由为 feedback"


# ADR-0011 决策 2（E-1）行为反转：这 2 句不含任何强信号词/壳2 canonical 文本，
# 规则层（提问/预约/确认/软约束）也识别不出具体对话行为——旧行为靠已删除的
# 兜底归并把 LLM 判的 ambiguous 强行掰成 feedback；归并删除后，识别不出具体
# 对话行为、脑子自己也判 clarify 时，应该走"问一句"而不是默默重规划。
_LONG_FEEDBACK_NO_STRONG_SIGNAL = [
    "感觉这个安排有点累，想要更轻松悠闲一些的",
    "第二个活动我不太喜欢，能换一个吗",
]


@pytest.mark.parametrize("text", _LONG_FEEDBACK_NO_STRONG_SIGNAL)
def test_long_feedback_without_strong_signal_routes_to_clarify(monkeypatch, text):
    """归并已删：无强信号词的长反馈，脑子判 clarify 时不再被强行掰成 feedback。"""
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_turn", lambda *a, **k: _make_judgment("clarify")
    )
    out = router_mod.router_node(_state_with_itinerary(text))
    assert out["route_kind"] == "clarify", f"{text!r} 应路由为 clarify（澄清而非硬猜）"


# ---- 纯社交/越界输入（chitchat/defense）即使有方案也不该被吞成 feedback ----
# 用户观察的 bug：规划后输入「你好」被当反馈重新规划，未走闲聊路由。

@pytest.mark.parametrize("label", ["chitchat", "defense"])
def test_social_input_with_itinerary_stays_its_label(monkeypatch, label):
    """有方案 + 脑子判明确社交/越界类 → 保持原标签，不重规划。

    ADR-0011 E-2-c：6 标签闭集里 meta/emotional 已塌缩进 chitchat，不再是
    独立可判的标签；off_topic 改名 defense。
    """
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_turn", lambda *a, **k: _make_judgment(label)
    )
    out = router_mod.router_node(_state_with_itinerary("你好"))
    assert out["route_kind"] == label, (
        f"有方案时 {label} 应保持 {label}（走闲聊气泡），不该被吞成 feedback"
    )


def test_clarify_with_itinerary_routes_clarify(monkeypatch):
    """ADR-0011 决策 2（E-1）行为反转，本用例原名
    test_ambiguous_with_itinerary_routes_feedback ——这是 ADR-0011 前置核实②
    点名的"行为反转标志性断言"：有方案 + 脑子判 clarify（原 ambiguous），旧世界
    靠兜底归并强行掰成 feedback（"没有任何下游会问"）；新世界 clarify 就应该
    走 clarify（chitchat 气泡通道问一句），不再默默重规划。
    """
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_turn", lambda *a, **k: _make_judgment("clarify")
    )
    out = router_mod.router_node(_state_with_itinerary("这个不太好"))
    assert out["route_kind"] == "clarify"


# ---- 会话中期新需求：归并删除后 planning 不再被强行掰成 feedback ----
# 原 test_new_request_in_session_routes_to_feedback（"session-no-new-request"）
# 纯粹测试已删除的兜底归并本身（"has_itinerary + LLM 判 planning → 强制 feedback"），
# 归并删除后这条路径的正确去向是 planning（直接进 intent，ADR-0012 背景 5
# 描述的"会话中期新需求"场景——intent_node 已收口 episode 字段重置，见
# test_state_lifecycle.py::test_intent_path_resets_episode_state_mid_session，
# 那里是这个能力真正的回归测试归属地），故本用例整体退役，不在此重复断言。


# ---- R6.4：无 itinerary → 行为与原逻辑一致 ----

def test_no_itinerary_chitchat_stays_chitchat(monkeypatch):
    """R6.4：无方案 + 脑子判 chitchat → chitchat（不进 feedback 分支）。"""
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_turn", lambda *a, **k: _make_judgment("chitchat")
    )
    st = make_initial_state(user_input="你好", session_id="s2")  # 无 itinerary
    out = router_mod.router_node(st)
    assert out["route_kind"] == "chitchat"


def test_no_itinerary_planning_stays_planning(monkeypatch):
    """R6.4：无方案 + 脑子判 planning → planning。"""
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_turn", lambda *a, **k: _make_judgment("planning")
    )
    st = make_initial_state(user_input="今天下午带孩子出去玩", session_id="s3")
    out = router_mod.router_node(st)
    assert out["route_kind"] == "planning"


# ---- Layer 1 强信号：has_itinerary + 词典命中 → feedback（不调脑子）----

def test_strong_feedback_layer1_no_brain(monkeypatch):
    """Layer 1：强信号反馈在 has_itinerary 时直接 feedback，不调脑子。"""
    called = {"brain": False}

    def _spy_classify(*a, **k):
        called["brain"] = True
        return _make_judgment("planning")

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(router_mod, "classify_turn", _spy_classify)
    out = router_mod.router_node(_state_with_itinerary("太远了，3公里以内"))
    assert out["route_kind"] == "feedback"
    assert called["brain"] is False, "Layer 1 强信号命中不应再调脑子"


# ============================================================
# B2（2026-07-04 路演前小修批）：Layer 1 强反馈补问句排除护栏
# ============================================================
# 实锤误判：「太久没回复我了，人还在吗」含强信号词"太久"，被 Layer 1 直觉判成
# "嫌方案太久"送去重排——但整句在问不在评。兄弟层（dialogue_acts 的 booking/
# confirm）都有 looks_like_question 类排除，Layer 1 此前没有。
# 护栏判据（刻意收窄）：只认句尾疑问标记（吗/呢/？/?），且"吧＋问号"按附加问/
# 揣测语气处理＝陈述性抱怨仍走强反馈；不用句中线索（有没有/能不能…）——那会把
# "有没有便宜点的"这类问句形真反馈也排除出 Layer 1，误送 QA 弃答，护栏反伤主胜场。


def test_question_tail_with_strong_keyword_not_layer1_feedback(monkeypatch):
    """「太久没回复我了，人还在吗」——问句尾排除强反馈直觉，落到问答/脑子路径。"""
    called = {"brain": False}

    def _spy(*a, **k):
        called["brain"] = True
        return _make_judgment("chitchat")

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(router_mod, "classify_turn", _spy)
    out = router_mod.router_node(_state_with_itinerary("太久没回复我了，人还在吗"))
    assert out["route_kind"] != "feedback", "问句不该被强反馈直觉送去重排"
    assert out["route_kind"] == "chitchat", (
        f"应落问答（QA 弃答）或脑子（此处脑子垫桩 chitchat），实际 {out['route_kind']}"
    )


def test_rhetorical_ba_with_question_mark_still_feedback(monkeypatch):
    """护栏不误伤附加问（tag question）：「这也太远了吧？」句尾"吧"是揣测语气
    不是求信息，仍应走 Layer 1 强反馈（与 itinerary_qa「"吧"不算问」同一判据）。"""
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_turn", lambda *a, **k: _make_judgment("clarify")
    )
    out = router_mod.router_node(_state_with_itinerary("这也太远了吧？"))
    assert out["route_kind"] == "feedback", "『吧？』附加问是陈述性抱怨，强反馈不受护栏影响"


def test_plain_strong_feedback_unaffected_by_question_guard(monkeypatch):
    """真正的强反馈（太远了/太赶了）不受问句护栏影响。"""
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_turn", lambda *a, **k: _make_judgment("clarify")
    )
    for text in ("太远了", "太赶了"):
        out = router_mod.router_node(_state_with_itinerary(text))
        assert out["route_kind"] == "feedback", f"{text!r} 应仍走 Layer 1 强反馈"
