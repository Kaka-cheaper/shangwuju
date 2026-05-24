"""spec algorithm-redesign R4：preference_scorer LLM 语义打分单测。

测试覆盖（≥ 4 项）：
- 5 岁娃场景 LLM 给亲子 POI 高分（mock LLM 返指定分数）
- stub 模式短路（返全 0.5）
- LLM 调用失败时兜底（返全 0.5）
- JSON 解析失败时兜底
- 空 POI 列表
- LLM 返了部分 POI（缺失项填 0.5）
- LLM 返越界值（< 0 / > 1） → clip 到 [0,1]

不消费真 LLM；用 mock client 控制返回。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 复用过渡态桥
if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.core.llm_client import LLMChatResponse  # noqa: E402
from agent.planning.preference_scorer import (  # noqa: E402
    _coerce_and_clip,
    score_pois_with_llm,
)
from tests.test_grounding_first import _make_intent_with_preschool, _make_poi  # noqa: E402


# ============================================================
# Fixture
# ============================================================


def _make_mock_client(
    *,
    provider: str = "deepseek",
    response_content: str = '{"scores": {}}',
    raises: Exception | None = None,
):
    client = MagicMock()
    client.provider = provider
    client.model = "test-model"
    if raises is not None:
        client.chat.side_effect = raises
    else:
        client.chat.return_value = LLMChatResponse(content=response_content)
    return client


# ============================================================
# 测试 1：5 岁娃 + 亲子博物馆得高分（mock LLM）
# ============================================================


def test_llm_returns_high_score_for_kid_friendly_poi():
    """LLM 返指定分数 → preference_scorer 透传出去"""
    intent = _make_intent_with_preschool()
    pois = [
        _make_poi("P_KID", suggested_default=60),  # 假亲子
        _make_poi("P_ADULT", suggested_default=120),
    ]
    response = '{"scores": {"P_KID": 0.92, "P_ADULT": 0.35}}'
    client = _make_mock_client(response_content=response)

    scores = score_pois_with_llm(intent, pois, client=client)
    assert scores["P_KID"] == pytest.approx(0.92)
    assert scores["P_ADULT"] == pytest.approx(0.35)


# ============================================================
# 测试 2：stub 模式短路返全 0.5
# ============================================================


def test_stub_provider_returns_all_05():
    """client.provider == "stub" → 不调 LLM，直接返全 0.5"""
    intent = _make_intent_with_preschool()
    pois = [
        _make_poi("P_1"),
        _make_poi("P_2"),
        _make_poi("P_3"),
    ]
    client = _make_mock_client(provider="stub")

    scores = score_pois_with_llm(intent, pois, client=client)
    assert scores == {"P_1": 0.5, "P_2": 0.5, "P_3": 0.5}
    # 没调 client.chat
    client.chat.assert_not_called()


# ============================================================
# 测试 3：LLM 调用异常 → 兜底全 0.5
# ============================================================


def test_llm_chat_exception_falls_back_to_05():
    """client.chat 抛异常 → fallback 全 0.5（不阻断 ILS 主路径）"""
    intent = _make_intent_with_preschool()
    pois = [_make_poi("P_1"), _make_poi("P_2")]
    client = _make_mock_client(raises=RuntimeError("LLM API 超时"))

    scores = score_pois_with_llm(intent, pois, client=client)
    assert scores == {"P_1": 0.5, "P_2": 0.5}


# ============================================================
# 测试 4：JSON 解析失败 → 兜底全 0.5
# ============================================================


def test_invalid_json_falls_back_to_05():
    """LLM 返非 JSON 内容 → fallback 全 0.5"""
    intent = _make_intent_with_preschool()
    pois = [_make_poi("P_1"), _make_poi("P_2")]
    client = _make_mock_client(response_content="这不是 JSON")

    scores = score_pois_with_llm(intent, pois, client=client)
    assert scores == {"P_1": 0.5, "P_2": 0.5}


# ============================================================
# 测试 5：空 POI 列表 → 返空 dict
# ============================================================


def test_empty_pois_returns_empty_dict():
    intent = _make_intent_with_preschool()
    client = _make_mock_client()
    scores = score_pois_with_llm(intent, [], client=client)
    assert scores == {}
    client.chat.assert_not_called()


# ============================================================
# 测试 6：LLM 部分返回（缺失项填 0.5）
# ============================================================


def test_partial_response_fills_missing_with_05():
    """LLM 只返 P_1，没返 P_2 → P_2 填 0.5"""
    intent = _make_intent_with_preschool()
    pois = [_make_poi("P_1"), _make_poi("P_2"), _make_poi("P_3")]
    response = '{"scores": {"P_1": 0.85}}'
    client = _make_mock_client(response_content=response)

    scores = score_pois_with_llm(intent, pois, client=client)
    assert scores["P_1"] == pytest.approx(0.85)
    assert scores["P_2"] == 0.5
    assert scores["P_3"] == 0.5


# ============================================================
# 测试 7：LLM 返越界值 → clip 到 [0, 1]
# ============================================================


def test_out_of_range_values_are_clipped():
    """LLM 偶尔返 1.5 / -0.2 → clip 到 1.0 / 0.0"""
    intent = _make_intent_with_preschool()
    pois = [_make_poi("P_HIGH"), _make_poi("P_LOW"), _make_poi("P_OK")]
    response = '{"scores": {"P_HIGH": 1.5, "P_LOW": -0.2, "P_OK": 0.7}}'
    client = _make_mock_client(response_content=response)

    scores = score_pois_with_llm(intent, pois, client=client)
    assert scores["P_HIGH"] == 1.0
    assert scores["P_LOW"] == 0.0
    assert scores["P_OK"] == pytest.approx(0.7)


def test_string_scores_coerced_to_float():
    """LLM 偶尔返 "0.85" 字符串 → 转 float"""
    intent = _make_intent_with_preschool()
    pois = [_make_poi("P_1")]
    response = '{"scores": {"P_1": "0.85"}}'
    client = _make_mock_client(response_content=response)
    scores = score_pois_with_llm(intent, pois, client=client)
    assert scores["P_1"] == pytest.approx(0.85)


# ============================================================
# 测试 8：scores 不是 dict → 兜底
# ============================================================


def test_scores_not_dict_falls_back():
    """LLM 把 scores 输出成 list → fallback 全 0.5"""
    intent = _make_intent_with_preschool()
    pois = [_make_poi("P_1")]
    response = '{"scores": [0.85]}'  # 类型错误
    client = _make_mock_client(response_content=response)
    scores = score_pois_with_llm(intent, pois, client=client)
    assert scores == {"P_1": 0.5}


def test_payload_missing_scores_key():
    """LLM 输出缺 scores 字段 → fallback 全 0.5"""
    intent = _make_intent_with_preschool()
    pois = [_make_poi("P_1")]
    response = '{"foo": "bar"}'
    client = _make_mock_client(response_content=response)
    scores = score_pois_with_llm(intent, pois, client=client)
    assert scores == {"P_1": 0.5}


# ============================================================
# 测试 9：_coerce_and_clip 单元
# ============================================================


def test_coerce_and_clip_helpers():
    assert _coerce_and_clip(None) is None
    assert _coerce_and_clip("不是数字") is None
    assert _coerce_and_clip(0.5) == 0.5
    assert _coerce_and_clip("0.85") == pytest.approx(0.85)
    assert _coerce_and_clip(1.5) == 1.0
    assert _coerce_and_clip(-0.5) == 0.0
    assert _coerce_and_clip(0) == 0.0
    assert _coerce_and_clip(1) == 1.0
    # NaN
    assert _coerce_and_clip(float("nan")) is None
