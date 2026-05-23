# -*- coding: utf-8 -*-
"""test_router —— 输入域路由器（Phase 0.8）单测。

覆盖：
- LLM 客户端 stub：6 类输入分类正确性
- cta_chips 白名单校验（拒绝发明 send 文案）
- planning 类强制清空 chips
- LLM 失败 → fallback_decision 兜底
- _stub_route 关键词 fast path：5 类高频输入命中

不依赖：
- 真 LLM API key
- 网络
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from agent.intent.router import (
    RouterError,
    classify_input,
    fallback_decision,
)
from agent.intent.prompts.router_prompt import PRIMARY_CTAS
from schemas.router import InputKind, RouterDecision


# ============================================================
# Fake LLM client（按 prompt 接收的最后一条 user 消息返预录响应）
# ============================================================


@dataclass
class _FakeResp:
    content: str
    tool_calls: list = None  # type: ignore[assignment]
    finish_reason: str = "stop"
    raw: dict | None = None

    def __post_init__(self) -> None:
        if self.tool_calls is None:
            self.tool_calls = []


class FakeLLM:
    """按 user 输入查表返响应；找不到 → 抛异常。"""

    provider = "fake"
    model = "fake"

    def __init__(self, table: dict[str, str | Exception]) -> None:
        self.table = table
        self.calls = 0

    def chat(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        # 取最后一条 user 内容（前面是 system + few-shot）
        user_text = ""
        for m in reversed(messages):
            if m.role == "user":
                user_text = m.content or ""
                break
        if user_text in self.table:
            v = self.table[user_text]
            if isinstance(v, Exception):
                raise v
            return _FakeResp(content=v)
        # 默认按 PLANNING 兜底（与下游 fallback 行为一致）
        return _FakeResp(
            content=json.dumps(
                {
                    "input_kind": "planning",
                    "confidence": 0.6,
                    "reply_text": "正在为你规划下午行程……",
                    "tone": "warm",
                    "cta_chips": [],
                    "rationale": "fake default",
                }
            )
        )

    def stream_chat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def chat_with_tools(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def _wl_send_for(idx: int) -> str:
    return PRIMARY_CTAS[idx]["send"]


# ============================================================
# 6 类分类（LLM 输出合法）
# ============================================================


def test_router_meta_classification():
    """问能力 → meta + 至少 1 个白名单 chip。"""
    user_q = "你是谁"
    table = {
        user_q: json.dumps(
            {
                "input_kind": "meta",
                "confidence": 0.95,
                "reply_text": "我是「晌午局」——你的下午半日出行管家。",
                "tone": "neutral",
                "cta_chips": [
                    {"label": "带娃放电", "send": _wl_send_for(0), "icon": "👨‍👩‍👧"},
                    {"label": "一个人放空", "send": _wl_send_for(3), "icon": "🌿"},
                ],
                "rationale": "test",
            },
            ensure_ascii=False,
        )
    }
    decision = classify_input(user_q, client=FakeLLM(table))
    assert decision.input_kind == InputKind.META
    assert decision.tone == "neutral"
    assert len(decision.cta_chips) == 2
    assert decision.cta_chips[0].send == _wl_send_for(0)


def test_router_emotional_classification():
    table = {
        "我累死了": json.dumps(
            {
                "input_kind": "emotional",
                "confidence": 0.9,
                "reply_text": "听起来今天真的挺累的呢。",
                "tone": "empathetic",
                "cta_chips": [
                    {"label": "找个安静地方", "send": _wl_send_for(3), "icon": "🌿"},
                ],
                "rationale": "test",
            }
        )
    }
    decision = classify_input("我累死了", client=FakeLLM(table))
    assert decision.input_kind == InputKind.EMOTIONAL
    assert decision.tone == "empathetic"
    assert len(decision.cta_chips) == 1


def test_router_planning_clears_chips():
    """planning 类即使 LLM 给了 chips 也强制清空。"""
    table = {
        "今天下午陪老婆孩子": json.dumps(
            {
                "input_kind": "planning",
                "confidence": 0.95,
                "reply_text": "正在为你规划下午行程……",
                "tone": "warm",
                "cta_chips": [
                    {"label": "误塞", "send": _wl_send_for(0), "icon": "👨‍👩‍👧"},
                ],
                "rationale": "test",
            }
        )
    }
    decision = classify_input("今天下午陪老婆孩子", client=FakeLLM(table))
    assert decision.input_kind == InputKind.PLANNING
    assert decision.cta_chips == []


def test_router_chitchat_classification():
    table = {
        "你好": json.dumps(
            {
                "input_kind": "chitchat",
                "confidence": 0.9,
                "reply_text": "你好呀！",
                "tone": "warm",
                "cta_chips": [
                    {"label": "带娃放电", "send": _wl_send_for(0), "icon": "👨‍👩‍👧"},
                ],
                "rationale": "test",
            }
        )
    }
    decision = classify_input("你好", client=FakeLLM(table))
    assert decision.input_kind == InputKind.CHITCHAT


def test_router_off_topic_classification():
    table = {
        "1+1=?": json.dumps(
            {
                "input_kind": "off_topic",
                "confidence": 0.85,
                "reply_text": "这个我帮不上忙呢～",
                "tone": "playful",
                "cta_chips": [
                    {"label": "下午局规划", "send": _wl_send_for(0), "icon": "👨‍👩‍👧"},
                ],
                "rationale": "test",
            }
        )
    }
    decision = classify_input("1+1=?", client=FakeLLM(table))
    assert decision.input_kind == InputKind.OFF_TOPIC
    assert decision.tone == "playful"


def test_router_ambiguous_classification():
    table = {
        "出去玩": json.dumps(
            {
                "input_kind": "ambiguous",
                "confidence": 0.75,
                "reply_text": "想约谁一起呢？",
                "tone": "warm",
                "cta_chips": [
                    {"label": "带娃放电", "send": _wl_send_for(0), "icon": "👨‍👩‍👧"},
                    {"label": "一个人放空", "send": _wl_send_for(3), "icon": "🌿"},
                ],
                "rationale": "test",
            }
        )
    }
    decision = classify_input("出去玩", client=FakeLLM(table))
    assert decision.input_kind == InputKind.AMBIGUOUS
    assert len(decision.cta_chips) == 2


# ============================================================
# 白名单校验（核心防御）
# ============================================================


def test_router_rejects_invented_send():
    """LLM 发明 send 文案 → 该 chip 被丢弃。"""
    table = {
        "你是谁": json.dumps(
            {
                "input_kind": "meta",
                "confidence": 0.9,
                "reply_text": "test",
                "tone": "neutral",
                "cta_chips": [
                    {"label": "合法", "send": _wl_send_for(0), "icon": "👨‍👩‍👧"},
                    {"label": "非法发明", "send": "随便写的输入文案", "icon": "🤔"},
                ],
                "rationale": "test",
            }
        )
    }
    decision = classify_input("你是谁", client=FakeLLM(table))
    # 只剩 1 个合法 chip
    assert len(decision.cta_chips) == 1
    assert decision.cta_chips[0].send == _wl_send_for(0)


def test_router_dedup_chips():
    """相同 send 的 chip 去重，仅保留首个。"""
    table = {
        "你是谁": json.dumps(
            {
                "input_kind": "meta",
                "confidence": 0.9,
                "reply_text": "test",
                "tone": "neutral",
                "cta_chips": [
                    {"label": "首个", "send": _wl_send_for(0), "icon": "👨‍👩‍👧"},
                    {"label": "重复", "send": _wl_send_for(0), "icon": "👨‍👩‍👧"},
                ],
                "rationale": "test",
            }
        )
    }
    decision = classify_input("你是谁", client=FakeLLM(table))
    assert len(decision.cta_chips) == 1
    assert decision.cta_chips[0].label == "首个"


def test_router_truncates_to_4_chips():
    """超过 4 个 chip 截断到 4 个。"""
    chips = [
        {"label": f"按钮{i}", "send": _wl_send_for(i), "icon": "🎯"}
        for i in range(min(8, len(PRIMARY_CTAS)))
    ]
    table = {
        "你是谁": json.dumps(
            {
                "input_kind": "meta",
                "confidence": 0.9,
                "reply_text": "test",
                "tone": "neutral",
                "cta_chips": chips,
                "rationale": "test",
            }
        )
    }
    decision = classify_input("你是谁", client=FakeLLM(table))
    assert len(decision.cta_chips) <= 4


# ============================================================
# 错误处理 + fallback
# ============================================================


def test_router_invalid_json_raises():
    """LLM 输出非 JSON → RouterError。"""
    table = {"你是谁": "this is not json"}
    with pytest.raises(RouterError) as ei:
        classify_input("你是谁", client=FakeLLM(table))
    assert ei.value.reason == "json_decode_failed"


def test_router_missing_required_field_raises():
    """schema 校验失败 → RouterError。"""
    table = {
        "你是谁": json.dumps(
            {
                "input_kind": "meta",
                "confidence": 0.9,
                # 缺 reply_text
                "tone": "neutral",
                "cta_chips": [],
            }
        )
    }
    with pytest.raises(RouterError) as ei:
        classify_input("你是谁", client=FakeLLM(table))
    assert ei.value.reason == "schema_validation_failed"


def test_router_fallback_decision_is_planning():
    """fallback_decision 总是返 planning，让原 planner 兜底。"""
    decision = fallback_decision("你是谁")
    assert decision.input_kind == InputKind.PLANNING
    assert decision.cta_chips == []


def test_router_invalid_input_kind_raises():
    """LLM 输出未知 input_kind → schema 校验失败。"""
    table = {
        "test": json.dumps(
            {
                "input_kind": "unknown_kind",  # 不在 6 类内
                "confidence": 0.9,
                "reply_text": "test",
                "tone": "warm",
                "cta_chips": [],
            }
        )
    }
    with pytest.raises(RouterError):
        classify_input("test", client=FakeLLM(table))


def test_router_confidence_default_when_missing():
    """LLM 没给 confidence → 默认 0.7。"""
    table = {
        "你是谁": json.dumps(
            {
                "input_kind": "meta",
                "reply_text": "test",
                "tone": "neutral",
                "cta_chips": [],
            }
        )
    }
    decision = classify_input("你是谁", client=FakeLLM(table))
    assert decision.confidence == 0.7
