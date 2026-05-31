"""router_node 三层反馈识别集成测试（spec feedback-routing-fix Task 3 / R1 R3 R4 R6）。

用真 LLM stub（mock classify_input）隔离 LLM 不确定性，专测 router_node 三层逻辑：
- R1：has_itinerary + 长反馈 → feedback
- R4：has_itinerary + 真新需求（LLM 判 planning）→ planning（不误伤）
- R6.4：无 itinerary → 行为与原逻辑一致（不进新分支）
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


# ---- R1：has_itinerary + 长反馈 → feedback ----

_LONG_FEEDBACK = [
    "感觉这个安排有点累，想要更轻松悠闲一些的",
    "第二个活动我不太喜欢，能换一个吗",
    "整体节奏对孩子来说太赶了",
    "这个预算有点超了，能不能找便宜点的",
]


@pytest.mark.parametrize("text", _LONG_FEEDBACK)
def test_long_feedback_routes_to_feedback(monkeypatch, text):
    """R1：已有方案 + 长反馈 → feedback（无论 LLM 判什么非 planning）。"""
    # 模拟 LLM 判 ambiguous（实测这类输入 LLM 多判 ambiguous）
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_input", lambda *a, **k: _make_decision("ambiguous")
    )
    out = router_mod.router_node(_state_with_itinerary(text))
    assert out["route_kind"] == "feedback", f"{text!r} 应路由为 feedback"


# ---- R4：has_itinerary + 真新需求（LLM 判 planning）→ planning ----

_NEW_REQUESTS = [
    "周末想带爸妈去吃顿好的，换个安排",
    "那这样，下午改成和朋友打球",
]


@pytest.mark.parametrize("text", _NEW_REQUESTS)
def test_new_request_routes_to_planning(monkeypatch, text):
    """R4：已有方案 + LLM 判 planning（明确新需求）→ planning（不误伤）。"""
    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(
        router_mod, "classify_input", lambda *a, **k: _make_decision("planning")
    )
    out = router_mod.router_node(_state_with_itinerary(text))
    assert out["route_kind"] == "planning", f"{text!r} 应路由为 planning"


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
