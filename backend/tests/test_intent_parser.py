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


# ADR-0014 G-0（2026-07-03）砍除记录：原「pitfalls 2026-05-24：LLM hallucinate
# pace_profile 字段名（_sanitize_payload）」测试组（4 条）已随 `_sanitize_payload`
# 函数与 `IntentExtraction.pace_profile` 字段一并砍除——被清洗的字段本身已不存在，
# 这组测试连带失去意义。见 `agent/intent/parser.py` / `schemas/intent.py` 砍除记录。
