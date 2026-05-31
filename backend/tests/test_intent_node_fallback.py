"""tests.test_intent_node_fallback —— 意图解析失败时 demo 不崩（韧性修复）。

背景（用户观察 + 日志根因）：mimo-v2.5-pro 偶发返回非法 JSON →
parse_intent 重试耗尽抛 IntentParseError → intent_node 无兜底 →
整条 graph 流抛异常 → stream_error → 评委看到红色崩溃。

修复：intent_node 捕获 IntentParseError，用 raw_input 构造一个合理的兜底意图
（social_context=家庭日常默认 / companions=[] / 词典字段空），让 demo 继续跑完，
而不是整条链路崩。
"""

from __future__ import annotations

from unittest.mock import patch

from agent.graph.nodes.intent import intent_node
from agent.graph.state import make_initial_state
from agent.intent.parser import IntentParseError
from schemas.intent import IntentExtraction


def _state(user_input: str):
    return make_initial_state(
        user_input=user_input,
        user_id="demo_user",
        session_id="t-fallback",
        scenario_id=None,
        planner_mode="llm",
    )


def test_intent_node_falls_back_when_parse_raises() -> None:
    """parse_intent 抛 IntentParseError → intent_node 返回兜底 intent，不抛异常。"""
    state = _state("今天下午想出去走走")
    with patch(
        "agent.graph.nodes.intent.parse_intent",
        side_effect=IntentParseError(reason="json_decode_failed"),
    ):
        out = intent_node(state)
    assert "intent" in out
    intent = out["intent"]
    assert isinstance(intent, IntentExtraction), "兜底必须返回合法 IntentExtraction"
    # raw_input 必须保留（下游 narrator / blueprint 还能看到用户原话）
    assert intent.raw_input == "今天下午想出去走走"
    # social_context 必须是合法 9 选 1（不能是 None / 空）
    from schemas.tags import SOCIAL_CONTEXTS

    assert intent.social_context in SOCIAL_CONTEXTS


def test_intent_node_fallback_marks_quality_issue() -> None:
    """兜底时应写 quality_issues，让 narrator 诚实告知"没完全听懂"。"""
    state = _state("balabala 听不懂的输入")
    with patch(
        "agent.graph.nodes.intent.parse_intent",
        side_effect=IntentParseError(reason="json_decode_failed"),
    ):
        out = intent_node(state)
    issues = out.get("quality_issues") or []
    assert any("没完全" in s or "理解" in s or "重新说" in s for s in issues), (
        f"兜底应写诚实告知 quality_issue，实际：{issues}"
    )


def test_intent_node_normal_path_unaffected() -> None:
    """正常解析成功路径不受兜底影响。"""
    fake = IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="朋友热闹",
        raw_input="和朋友出去玩",
        parse_confidence=0.9,
    )
    state = _state("和朋友出去玩")
    with patch("agent.graph.nodes.intent.parse_intent", return_value=fake):
        out = intent_node(state)
    assert out["intent"] is fake
