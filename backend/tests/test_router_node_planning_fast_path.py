"""Regression tests for LangGraph router planning fast path."""

from __future__ import annotations

import pytest

from agent.graph.nodes import router as router_mod
from agent.graph.state import make_initial_state


@pytest.mark.parametrize(
    "text",
    [
        "周五晚上和室友 4 个人想去 K 歌，预算别太贵",
        "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
    ],
)
def test_clear_planning_request_skips_llm_router(monkeypatch, text):
    called = {"llm": False}

    def _classify_should_not_run(*args, **kwargs):
        called["llm"] = True
        raise AssertionError("planning fast path should skip classify_input")

    monkeypatch.setattr(router_mod, "classify_input", _classify_should_not_run)

    out = router_mod.router_node(
        make_initial_state(user_input=text, session_id="planning-fast-path")
    )

    assert out["route_kind"] == "planning"
    assert called["llm"] is False


@pytest.mark.parametrize(
    "text",
    [
        "周五晚上和室友 4 个人想去 K 歌，预算别太贵",
        "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
    ],
)
def test_clear_planning_in_session_routes_to_feedback(monkeypatch, text):
    """session-no-new-request：同一句规划句，但 session 已有方案 → feedback。

    会话内没有"该丢上下文的新需求"：快路命中后转 feedback（交 refiner 带上下文），
    且仍不调 classify_input。
    """
    called = {"llm": False}

    def _classify_should_not_run(*args, **kwargs):
        called["llm"] = True
        raise AssertionError("命中快路不应再调 classify_input")

    monkeypatch.setattr(router_mod, "classify_input", _classify_should_not_run)

    st = make_initial_state(user_input=text, session_id="planning-in-session")
    st["itinerary"] = {"summary": "上一轮方案"}  # 触发 has_itinerary
    out = router_mod.router_node(st)

    assert out["route_kind"] == "feedback"
    assert called["llm"] is False
