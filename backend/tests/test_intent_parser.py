"""test_intent_parser —— 意图解析单测（用 stub LLM）。"""

from __future__ import annotations

from agent.intent.parser import parse_intent
from agent.core.llm_client_stub import StubLLMClient


def test_intent_parse_family_main_scene():
    client = StubLLMClient()
    intent = parse_intent(
        "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
        client=client,
    )

    assert intent.social_context == "家庭日常"
    assert "亲子友好" in intent.physical_constraints
    assert "低脂" in intent.dietary_constraints
    assert any(c.role == "孩子" and c.age == 5 for c in intent.companions)
    # D9 硬条款：禁止字段
    dumped = intent.model_dump()
    assert "scene_type" not in dumped
    assert "relation_type" not in dumped


def test_intent_parse_returns_raw_input_filled():
    client = StubLLMClient()
    msg = "测试句子"
    intent = parse_intent(msg, client=client)
    # stub 已回填 raw_input，但即使 LLM 没填，parse_intent 也要兜底
    assert intent.raw_input  # 非空



# ============================================================
# pitfalls 2026-05-24：LLM hallucinate pace_profile 字段名（_sanitize_payload）
# ============================================================


def test_sanitize_payload_drops_unknown_pace_fields():
    """LLM 输出 total_active_max_min（schema 真实字段是 total_active_min）应被清掉。"""
    from agent.intent.parser import _sanitize_payload

    payload = {
        "raw_input": "x",
        "pace_profile": {
            "single_session_max_min": 75,
            "total_active_max_min": None,  # LLM hallucinate 字段名
            "break_every_min": 45,
        },
    }
    cleaned = _sanitize_payload(payload)
    assert "total_active_max_min" not in cleaned["pace_profile"], (
        f"hallucinate 字段未清除：{cleaned['pace_profile']}"
    )
    assert cleaned["pace_profile"]["single_session_max_min"] == 75
    assert cleaned["pace_profile"]["break_every_min"] == 45


def test_sanitize_payload_drops_none_value_fields():
    """字段值为 None 应被清掉（避免 NonNegativeInt 校验报错）。"""
    from agent.intent.parser import _sanitize_payload

    payload = {
        "pace_profile": {
            "single_session_max_min": 75,
            "total_active_min": None,
            "break_every_min": None,
        },
    }
    cleaned = _sanitize_payload(payload)
    assert cleaned["pace_profile"] == {"single_session_max_min": 75}


def test_sanitize_payload_empty_pace_becomes_none():
    """全 None 或全未知字段 → pace_profile 整对象设为 None。"""
    from agent.intent.parser import _sanitize_payload

    payload = {"pace_profile": {"total_active_max_min": None, "junk": "x"}}
    cleaned = _sanitize_payload(payload)
    assert cleaned["pace_profile"] is None


def test_sanitize_payload_no_pace_profile_unchanged():
    """没有 pace_profile 字段不应报错，原样返回。"""
    from agent.intent.parser import _sanitize_payload

    payload = {"raw_input": "x"}
    cleaned = _sanitize_payload(payload)
    assert cleaned == {"raw_input": "x"}
