"""test_finalize_plan_version_log —— ADR-0011 前置核实①：版本志条目构造纯函数单测。

问题命名：`finalize_plan_node` 的 trigger 判据(深审改判后)——**只反映入口
维度**(first/feedback):求解路径(ils/backprompt/give_up)已住在
`itinerary.decision_trace.final_strategy`,版本志再存一份=同一事实两处存放
必然漂移(改判理由见 finalize_plan.py 模块 docstring)。本文件绕开图跑,
直接单测 `_version_log_trigger` / `_version_log_entry` 两个纯函数。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.graph.nodes.finalize_plan import (  # noqa: E402
    _snippet,
    _version_log_entry,
    _version_log_trigger,
)


def test_trigger_is_first_when_no_replan_and_route_kind_planning():
    state = {"route_kind": "planning", "replan_strategy": None}
    assert _version_log_trigger(state) == "first"


def test_trigger_is_feedback_when_no_replan_and_route_kind_feedback():
    state = {"route_kind": "feedback", "replan_strategy": None}
    assert _version_log_trigger(state) == "feedback"


def test_trigger_ignores_replan_strategy_entry_dimension_only():
    """深审改判:critic 驱动的重排**不**改变 trigger——求解路径的事实归
    decision_trace.final_strategy(真因修复批已让它可信),版本志只记
    "用户视角这版因何而生"。即便升级到 ils/give_up,入口是反馈就记 feedback。"""
    for strategy in ("ils_fallback", "give_up", "llm_backprompt"):
        assert _version_log_trigger({"route_kind": "planning", "replan_strategy": strategy}) == "first"
        assert _version_log_trigger({"route_kind": "feedback", "replan_strategy": strategy}) == "feedback"


def test_version_log_entry_first_turn_summary_quotes_raw_input():
    state = {
        "route_kind": "planning",
        "replan_strategy": None,
        "user_input": "今天下午想带孩子出去玩",
    }
    entry = _version_log_entry(state, version_n=1)
    assert entry["version_n"] == 1
    assert entry["trigger"] == "first"
    assert entry["summary"] == "v1: 按『今天下午想带孩子出去玩』出方案"
    assert isinstance(entry["timestamp"], int)


def test_version_log_entry_feedback_turn_summary_quotes_feedback_text():
    state = {
        "route_kind": "feedback",
        "replan_strategy": None,
        "user_input": "太远了，帮我换近一点的地方",
    }
    entry = _version_log_entry(state, version_n=2)
    assert entry["version_n"] == 2
    assert entry["trigger"] == "feedback"
    assert entry["summary"] == "v2: 应『太远了，帮我换近一点的地方』调整"


def test_version_log_entry_truncates_long_raw_input_in_summary():
    long_text = "帮我规划一个下午的行程" * 10  # 远超 40 字截断上限
    state = {"route_kind": "planning", "replan_strategy": None, "user_input": long_text}
    entry = _version_log_entry(state, version_n=1)
    assert entry["summary"] != f"v1: 按『{long_text}』出方案"
    assert entry["summary"].startswith("v1: 按『帮我规划一个下午的行程")
    assert entry["summary"].endswith("…』出方案")


def test_snippet_passthrough_short_text_and_truncates_long_text():
    assert _snippet("短句") == "短句"
    long_text = "x" * 100
    snippet = _snippet(long_text)
    assert len(snippet) == 41  # 40 字 + 省略号
    assert snippet.endswith("…")
