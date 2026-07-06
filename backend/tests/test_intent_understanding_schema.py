"""tests.test_intent_understanding_schema —— 信任带 §四① `understanding` 字段单测。

覆盖（同 test_intent_provenance_schema.py 的既有模式）：
1. Optional 兼容：不传时默认空串 ""，旧 checkpoint（JSON 里压根没有这个键）
   依然能 model_validate 通过，免迁移；1600+ 既有测试与 stub 不产该字段时
   不炸（本次任务的硬要求）。
2. 传入合法字符串时正常保留。
3. model_dump() 里恒含这个键（前端 INTENT_PARSED 事件整体透传 model_dump()，
   不需要下游做"有没有这个键"的特判）。
4. D9 extra=forbid 防线不受新字段松动。
5. stub client（parse_intent 走 StubLLMClient）产出的 intent 也带非空
   understanding（stub fixture 已补一条合理值，见 llm_client_stub.py）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.intent import IntentExtraction


def _base_kwargs() -> dict:
    return dict(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[],
        physical_constraints=["亲子友好"],
        dietary_constraints=["不辣", "日料"],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="测试",
        parse_confidence=0.8,
    )


def test_understanding_defaults_to_empty_string_without_migration():
    intent = IntentExtraction(**_base_kwargs())
    assert intent.understanding == ""
    # model_dump 恒带这个键——前端 emit_intent 直接整体 model_dump() 透传，
    # 不需要"这个键存不存在"的特判。
    assert "understanding" in intent.model_dump()


def test_old_checkpoint_json_without_understanding_key_still_validates():
    """旧 checkpoint / 未升级的 stub：JSON 里压根没有 understanding 键。"""
    payload = _base_kwargs()
    assert "understanding" not in payload
    intent = IntentExtraction.model_validate(payload)
    assert intent.understanding == ""


def test_understanding_accepts_llm_generated_sentence():
    intent = IntentExtraction(
        **_base_kwargs(),
        understanding="用户想周五晚和室友唱K，还说别太贵，我理解成想热闹但钱别花太多",
    )
    assert intent.understanding.startswith("用户")


def test_understanding_extra_forbid_still_rejects_unknown_top_level_field():
    """新增字段不应松动既有 D9 extra=forbid 防线。"""
    payload = _base_kwargs()
    payload["scene_type"] = "family"
    with pytest.raises(ValidationError):
        IntentExtraction.model_validate(payload)


def test_stub_client_intent_carries_non_empty_understanding():
    """stub 冒烟路径：parse_intent 走 StubLLMClient 时 understanding 非空，
    信任带①拍在 --stub 冒烟下也能看到真实效果（而不是永远空白）。"""
    from agent.core.llm_client_stub import StubLLMClient
    from agent.intent.parser import parse_intent

    client = StubLLMClient()
    intent = parse_intent("今天下午想带家人出去玩", client=client)
    assert intent.understanding != ""
