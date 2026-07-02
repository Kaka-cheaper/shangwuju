"""router_node 三层反馈识别集成测试（spec feedback-routing-fix Task 3 / R1 R3 R4 R6；
ADR-0011 决策 2 / E-1 行为反转已同步到本文件）。

用真 LLM stub（mock classify_input）隔离 LLM 不确定性，专测 router_node 三层逻辑：
- R1：has_itinerary + 长反馈 → feedback（Layer 1 强信号词典命中，不依赖归并）
- R6.4：无 itinerary → 行为与原逻辑一致（不进新分支）

ADR-0011 决策 2（E-1）删除了 route_turn.py 原 :300-302 的兜底归并
（"has_itinerary + planning/ambiguous → 强制 feedback"）——没有任何下游会
"问"，实测被硬猜重规划违反 L0 禁令 1。本文件里**纯靠归并**才成立的用例已删除
或翻转（见各用例内联说明）；**靠 Layer 1 强信号词典**命中的用例不受影响
（Layer 1 在归并之前、且未被 E-1 触碰）。
"""

from __future__ import annotations

import pytest

from agent.graph.nodes import router as router_mod
from agent.graph.state import make_initial_state
from schemas.router import InputKind, RouterDecision


def _make_decision(kind: str) -> RouterDecision:
    return RouterDecision(
        input_kind=InputKind(kind),
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
# Layer 1 在 LLM 分类之前就直接判 feedback——不依赖已删除的兜底归并，
# 无论 classify_input 判什么都不影响结果（下面刻意仍 mock 成 ambiguous，
# 证明这条路径确实是 Layer 1 短路，不是靠归并才凑出来的 feedback）。
_LONG_FEEDBACK_STRONG_SIGNAL = [
    "整体节奏对孩子来说太赶了",
    "这个预算有点超了，能不能找便宜点的",
]


@pytest.mark.parametrize("text", _LONG_FEEDBACK_STRONG_SIGNAL)
def test_long_feedback_with_strong_signal_routes_to_feedback(monkeypatch, text):
    """R1：已有方案 + 含强信号词的长反馈 → feedback（Layer 1 命中，不调 LLM）。"""
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_input", lambda *a, **k: _make_decision("ambiguous")
    )
    out = router_mod.router_node(_state_with_itinerary(text))
    assert out["route_kind"] == "feedback", f"{text!r} 应路由为 feedback"


# ADR-0011 决策 2（E-1）行为反转：这 2 句不含任何强信号词/壳2 canonical 文本，
# Layer 3（classify_dialogue_act）也识别不出具体对话行为（不是提问/预约/确认/
# 软约束）——旧行为靠已删除的兜底归并把 LLM 判的 ambiguous 强行掰成 feedback；
# 归并删除后，识别不出具体对话行为的 ambiguous 应该走"问一句"而不是默默重规划。
_LONG_FEEDBACK_NO_STRONG_SIGNAL = [
    "感觉这个安排有点累，想要更轻松悠闲一些的",
    "第二个活动我不太喜欢，能换一个吗",
]


@pytest.mark.parametrize("text", _LONG_FEEDBACK_NO_STRONG_SIGNAL)
def test_long_feedback_without_strong_signal_routes_to_ambiguous(monkeypatch, text):
    """归并已删：无强信号词的长反馈，LLM 判 ambiguous 时不再被强行掰成 feedback。"""
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_input", lambda *a, **k: _make_decision("ambiguous")
    )
    out = router_mod.router_node(_state_with_itinerary(text))
    assert out["route_kind"] == "ambiguous", f"{text!r} 应路由为 ambiguous（澄清而非硬猜）"


# ---- 纯社交输入（chitchat/meta/off_topic）即使有方案也不该被吞成 feedback ----
# 用户观察的 bug：规划后输入「你好」被当反馈重新规划，未走闲聊路由。

@pytest.mark.parametrize(
    "kind",
    ["chitchat", "meta", "off_topic"],
)
def test_social_input_with_itinerary_stays_chitchat(monkeypatch, kind):
    """有方案 + LLM 判明确社交类（你好/问能力/无关话题）→ chitchat，不重规划。

    Layer 3 兜底只接管 ambiguous（真反馈措辞落的桶）；chitchat/meta/off_topic
    有明确社交语义，应保持闲聊，不被误判为反馈触发重规划。
    """
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_input", lambda *a, **k: _make_decision(kind)
    )
    out = router_mod.router_node(_state_with_itinerary("你好"))
    assert out["route_kind"] == kind, (
        f"有方案时 {kind} 应保持 {kind}（走闲聊气泡），不该被吞成 feedback"
    )


def test_ambiguous_with_itinerary_routes_ambiguous(monkeypatch):
    """ADR-0011 决策 2（E-1）行为反转，本用例原名
    test_ambiguous_with_itinerary_routes_feedback ——这是 ADR-0011 前置核实②
    点名的"行为反转标志性断言"：有方案 + LLM 判 ambiguous，旧世界靠兜底归并
    强行掰成 feedback（"没有任何下游会问"）；新世界 ambiguous 就应该走 ambiguous
    （chitchat 气泡通道问一句），不再默默重规划。
    """
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_input", lambda *a, **k: _make_decision("ambiguous")
    )
    out = router_mod.router_node(_state_with_itinerary("这个不太好"))
    assert out["route_kind"] == "ambiguous"


# ---- 会话中期新需求：归并删除后 planning 不再被强行掰成 feedback ----
# 原 test_new_request_in_session_routes_to_feedback（"session-no-new-request"）
# 纯粹测试已删除的兜底归并本身（"has_itinerary + LLM 判 planning → 强制 feedback"），
# 归并删除后这条路径的正确去向是 planning（直接进 intent，ADR-0012 背景 5
# 描述的"会话中期新需求"场景——intent_node 已收口 episode 字段重置，见
# test_state_lifecycle.py::test_intent_path_resets_episode_state_mid_session，
# 那里是这个能力真正的回归测试归属地），故本用例整体退役，不在此重复断言。


# ---- R6.4：无 itinerary → 行为与原逻辑一致 ----

def test_no_itinerary_chitchat_stays_chitchat(monkeypatch):
    """R6.4：无方案 + LLM 判 chitchat → chitchat（不进 feedback 分支）。"""
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_input", lambda *a, **k: _make_decision("chitchat")
    )
    st = make_initial_state(user_input="你好", session_id="s2")  # 无 itinerary
    out = router_mod.router_node(st)
    assert out["route_kind"] == "chitchat"


def test_no_itinerary_planning_stays_planning(monkeypatch):
    """R6.4：无方案 + LLM 判 planning → planning。"""
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_input", lambda *a, **k: _make_decision("planning")
    )
    st = make_initial_state(user_input="今天下午带孩子出去玩", session_id="s3")
    out = router_mod.router_node(st)
    assert out["route_kind"] == "planning"


# ---- Layer 1 强信号：has_itinerary + 词典命中 → feedback（不调 LLM）----

def test_strong_feedback_layer1_no_llm(monkeypatch):
    """Layer 1：强信号反馈在 has_itinerary 时直接 feedback，不调 classify_input。"""
    called = {"llm": False}

    def _spy_classify(*a, **k):
        called["llm"] = True
        return _make_decision("planning")

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(router_mod, "classify_input", _spy_classify)
    out = router_mod.router_node(_state_with_itinerary("太远了，3公里以内"))
    assert out["route_kind"] == "feedback"
    assert called["llm"] is False, "Layer 1 强信号命中不应再调 LLM"
