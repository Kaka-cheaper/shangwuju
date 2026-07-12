"""test_routing_brain —— 统一路由脑子单测（ADR-0011 E-2-c）。

垫桩 LLM client（不打真网络），覆盖：
- 6 标签各自正确解析（label/confidence/reply_text/tone/cta_chips）
- 槽位（node_ref / feedback_hint）原样透传
- 低置信度 → 归并 clarify（有方案/无方案两条地板文案）；已经是 clarify 不重复降级
- cta_chips 白名单校验（发明的 send 被丢弃）
- confirm 标签强制钉死唯一确认 chip；planning/feedback 强制清空 chips
- 软约束 chip enrichment（对话轮路由规则层重构 2026-07-12 新增）：clarify +
  有方案 + 原始输入含词典软约束关键词 → 代码拼「换成X的」chip，且该 chip 的
  send 已注册进 canonical_shortcut 的 exact-match 集合
- 失败路径 → 哨兵 None：坏 JSON / 非 JSON 对象 / schema 校验失败 / 空响应 / LLM 抛异常

真 LLM 冒烟见 test_routing_brain_real_llm.py（仿 test_refiner_real_llm.py 先例）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from agent.routing.brain import CONFIDENCE_FLOOR, RouteJudgment, classify_turn


@dataclass
class _FakeResp:
    content: str | None
    tool_calls: list = field(default_factory=list)
    finish_reason: str = "stop"
    raw: dict | None = None


class _FakeClient:
    """恒返回构造时给定的 content（忽略入参），供纯粹测试解析/校验逻辑。"""

    provider = "fake"
    model = "fake"

    def __init__(self, content: str | None, *, raise_on_chat: Exception | None = None) -> None:
        self._content = content
        self._raise = raise_on_chat
        self.last_messages: list | None = None

    def chat(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.last_messages = messages
        if self._raise is not None:
            raise self._raise
        return _FakeResp(content=self._content)

    def stream_chat(self, *a, **k):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def chat_with_tools(self, *a, **k):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def _payload(**overrides: Any) -> dict:
    base = {
        "label": "chitchat",
        "confidence": 0.9,
        "reply_text": "你好呀，今天想怎么安排下午？",
        "tone": "warm",
        "cta_chips": [],
        "node_ref": None,
        "feedback_hint": None,
        "rationale": "test",
    }
    base.update(overrides)
    return base


CONTEXT_TEXT = "【首轮原始需求】\n（无）\n"


# ============================================================
# 6 标签各自解析
# ============================================================


@pytest.mark.parametrize(
    "label", ["planning", "feedback", "chitchat", "confirm", "clarify", "defense"]
)
def test_classify_turn_parses_each_label(label: str) -> None:
    client = _FakeClient(json.dumps(_payload(label=label, confidence=0.9), ensure_ascii=False))
    judgment = classify_turn(CONTEXT_TEXT, "随便一句话", True, client=client)
    assert judgment is not None
    assert judgment.label == label


def test_classify_turn_defaults_confidence_and_tone_when_missing() -> None:
    payload = _payload()
    del payload["confidence"]
    del payload["tone"]
    client = _FakeClient(json.dumps(payload, ensure_ascii=False))
    judgment = classify_turn(CONTEXT_TEXT, "你好", False, client=client)
    assert judgment is not None
    # 缺 confidence 时保守估计 0.5，落在地板以下 → 触发降级为 clarify
    assert judgment.label == "clarify"


# ============================================================
# 槽位透传
# ============================================================


def test_slots_round_trip() -> None:
    client = _FakeClient(
        json.dumps(
            _payload(
                label="feedback",
                confidence=0.85,
                node_ref="量贩式 KTV",
                feedback_hint="想换得更近",
            ),
            ensure_ascii=False,
        )
    )
    judgment = classify_turn(CONTEXT_TEXT, "KTV 有点远", True, client=client)
    assert judgment is not None
    assert judgment.node_ref == "量贩式 KTV"
    assert judgment.feedback_hint == "想换得更近"


# ============================================================
# 低置信度地板
# ============================================================


def test_low_confidence_downgrades_to_clarify_with_itinerary() -> None:
    client = _FakeClient(
        json.dumps(_payload(label="planning", confidence=0.3), ensure_ascii=False)
    )
    judgment = classify_turn(CONTEXT_TEXT, "这个不太好", True, client=client)
    assert judgment is not None
    assert judgment.label == "clarify"
    assert "调整现在的方案" in judgment.reply_text or "方案" in judgment.reply_text
    assert len(judgment.cta_chips) == 3  # FLOOR_CLARIFY_CTAS 三选项


def test_low_confidence_downgrades_to_clarify_without_itinerary() -> None:
    client = _FakeClient(
        json.dumps(_payload(label="planning", confidence=0.3), ensure_ascii=False)
    )
    judgment = classify_turn(CONTEXT_TEXT, "随便说说", False, client=client)
    assert judgment is not None
    assert judgment.label == "clarify", "ADR-0011 拍板 b：低置信度一律归并 clarify，不因无方案而不同"


def test_low_confidence_never_survives_as_feedback() -> None:
    """ADR-0011 拍板 b："置信度低归并澄清，绝不归并 feedback"。"""
    client = _FakeClient(
        json.dumps(_payload(label="feedback", confidence=0.4), ensure_ascii=False)
    )
    judgment = classify_turn(CONTEXT_TEXT, "换一个", True, client=client)
    assert judgment is not None
    assert judgment.label == "clarify"


def test_already_clarify_low_confidence_keeps_own_content() -> None:
    """label 已经是 clarify 时不重复降级——用自己的 reply_text/chips。"""
    client = _FakeClient(
        json.dumps(
            _payload(label="clarify", confidence=0.2, reply_text="你是想让我猜猜看吗？"),
            ensure_ascii=False,
        )
    )
    judgment = classify_turn(CONTEXT_TEXT, "嗯", True, client=client)
    assert judgment is not None
    assert judgment.label == "clarify"
    assert judgment.reply_text == "你是想让我猜猜看吗？"


def test_confidence_at_floor_not_downgraded() -> None:
    client = _FakeClient(
        json.dumps(_payload(label="planning", confidence=CONFIDENCE_FLOOR), ensure_ascii=False)
    )
    judgment = classify_turn(CONTEXT_TEXT, "今天下午带娃出去玩", False, client=client)
    assert judgment is not None
    assert judgment.label == "planning"


# ============================================================
# cta_chips 白名单
# ============================================================


def test_invented_chip_send_is_dropped() -> None:
    client = _FakeClient(
        json.dumps(
            _payload(
                label="chitchat",
                cta_chips=[{"label": "瞎编的", "send": "随便写的从没出现过的文案", "icon": "🤔"}],
            ),
            ensure_ascii=False,
        )
    )
    judgment = classify_turn(CONTEXT_TEXT, "你好", False, client=client)
    assert judgment is not None
    assert judgment.cta_chips == []


def test_whitelisted_chip_send_kept() -> None:
    from agent.intent.prompts.router_prompt import PRIMARY_CTAS

    send = PRIMARY_CTAS[0]["send"]
    client = _FakeClient(
        json.dumps(
            _payload(label="chitchat", cta_chips=[{"label": "带娃", "send": send, "icon": "👨‍👩‍👧"}]),
            ensure_ascii=False,
        )
    )
    judgment = classify_turn(CONTEXT_TEXT, "你好", False, client=client)
    assert judgment is not None
    assert len(judgment.cta_chips) == 1
    assert judgment.cta_chips[0].send == send


# ============================================================
# 标签专属按钮纪律
# ============================================================


def test_confirm_label_forces_single_confirm_chip_regardless_of_llm_chips() -> None:
    from agent.intent.prompts.router_prompt import PRIMARY_CTAS

    client = _FakeClient(
        json.dumps(
            _payload(
                label="confirm",
                cta_chips=[{"label": "别的", "send": PRIMARY_CTAS[0]["send"], "icon": "🌿"}],
            ),
            ensure_ascii=False,
        )
    )
    judgment = classify_turn(CONTEXT_TEXT, "好的就这个", True, client=client)
    assert judgment is not None
    assert len(judgment.cta_chips) == 1
    assert judgment.cta_chips[0].send == "确认预约"
    assert judgment.cta_chips[0].action == "confirm"


@pytest.mark.parametrize("label", ["planning", "feedback"])
def test_planning_feedback_force_empty_chips(label: str) -> None:
    from agent.intent.prompts.router_prompt import PRIMARY_CTAS

    client = _FakeClient(
        json.dumps(
            _payload(
                label=label,
                confidence=0.9,
                cta_chips=[{"label": "误塞", "send": PRIMARY_CTAS[0]["send"], "icon": "👨‍👩‍👧"}],
            ),
            ensure_ascii=False,
        )
    )
    judgment = classify_turn(CONTEXT_TEXT, "今天下午带娃出去玩", False, client=client)
    assert judgment is not None
    assert judgment.cta_chips == []


# ============================================================
# 软约束 chip enrichment（对话轮路由规则层重构，2026-07-12）
# ============================================================
# 软约束嗅探器的路由角色已删除，"提约束·没说改 → 主动问 + 换成X的 chip"改由
# 脑子判 clarify 之后，代码独立对 user_input 跑关键词规则表
# （agent.core.soft_constraint_tags.sniff_tags）追加 chip——不依赖 LLM 输出内容，
# 见 agent/routing/brain.py::_apply_soft_constraint_chip_enrichment。


def test_clarify_with_itinerary_and_soft_constraint_keyword_gets_chip() -> None:
    client = _FakeClient(
        json.dumps(
            _payload(label="clarify", confidence=0.8, cta_chips=[]),
            ensure_ascii=False,
        )
    )
    judgment = classify_turn(
        CONTEXT_TEXT, "我妈膝盖不好，走不远", True, client=client
    )
    assert judgment is not None
    assert judgment.label == "clarify"
    assert len(judgment.cta_chips) == 1
    chip = judgment.cta_chips[0]
    assert "适合老人" in chip.label
    assert "换成适合老人" in chip.send
    assert "帮我换成" in chip.send, "send 应含祈使替换词，点击回传后能被壳2/Layer1识别"


def test_clarify_chip_send_is_registered_in_canonical_shortcut() -> None:
    """chip 的 send 必须精确落在 canonical_shortcut 的 exact-match 集合里
    （BLOCK 1 决策 #4）——保证点击回传后 FP≈0 确定性短路成 feedback，不依赖
    looks_like_feedback_strong 二次辨认。"""
    from agent.routing.canonical_shortcut import canonical_shortcut_decision

    client = _FakeClient(
        json.dumps(
            _payload(label="clarify", confidence=0.8, cta_chips=[]),
            ensure_ascii=False,
        )
    )
    judgment = classify_turn(
        CONTEXT_TEXT, "我妈膝盖不好，走不远", True, client=client
    )
    assert judgment is not None
    chip = judgment.cta_chips[0]

    outcome = canonical_shortcut_decision(chip.send, has_itinerary=True)
    assert outcome is not None, f"chip.send={chip.send!r} 应被壳2 canonical 精确识别"
    assert outcome.kind == "feedback"


def test_clarify_without_itinerary_no_chip_enrichment() -> None:
    """无方案时"换成X的"没有方案可换，不适用——即使命中关键词也不追加 chip。"""
    client = _FakeClient(
        json.dumps(
            _payload(label="clarify", confidence=0.8, cta_chips=[]),
            ensure_ascii=False,
        )
    )
    judgment = classify_turn(
        CONTEXT_TEXT, "我妈膝盖不好，走不远", False, client=client
    )
    assert judgment is not None
    assert judgment.cta_chips == []


def test_clarify_no_soft_constraint_keyword_no_chip_enrichment() -> None:
    """没有命中软约束关键词的 clarify（如信息不足类澄清）不该凭空长出 chip。"""
    client = _FakeClient(
        json.dumps(
            _payload(label="clarify", confidence=0.8, cta_chips=[]),
            ensure_ascii=False,
        )
    )
    judgment = classify_turn(CONTEXT_TEXT, "这个不太好", True, client=client)
    assert judgment is not None
    assert judgment.cta_chips == []


def test_non_clarify_label_no_chip_enrichment() -> None:
    """非 clarify 标签不触发本机制（即使原始输入含软约束关键词），避免
    feedback/chitchat 等其他标签被意外插入这颗 chip。"""
    client = _FakeClient(
        json.dumps(
            _payload(label="chitchat", confidence=0.9, cta_chips=[]),
            ensure_ascii=False,
        )
    )
    judgment = classify_turn(
        CONTEXT_TEXT, "我妈膝盖不好，走不远", True, client=client
    )
    assert judgment is not None
    assert judgment.cta_chips == []


# ============================================================
# 失败路径 → 哨兵 None
# ============================================================


def test_bad_json_returns_none() -> None:
    client = _FakeClient("this is not json at all")
    assert classify_turn(CONTEXT_TEXT, "你好", False, client=client) is None


def test_non_dict_json_returns_none() -> None:
    client = _FakeClient(json.dumps(["not", "a", "dict"]))
    assert classify_turn(CONTEXT_TEXT, "你好", False, client=client) is None


def test_invalid_label_returns_none() -> None:
    client = _FakeClient(json.dumps(_payload(label="not_a_real_label"), ensure_ascii=False))
    assert classify_turn(CONTEXT_TEXT, "你好", False, client=client) is None


def test_missing_reply_text_returns_none() -> None:
    payload = _payload()
    del payload["reply_text"]
    client = _FakeClient(json.dumps(payload, ensure_ascii=False))
    assert classify_turn(CONTEXT_TEXT, "你好", False, client=client) is None


def test_empty_response_returns_none() -> None:
    client = _FakeClient(None)
    assert classify_turn(CONTEXT_TEXT, "你好", False, client=client) is None


def test_llm_exception_returns_none() -> None:
    client = _FakeClient(None, raise_on_chat=RuntimeError("network boom"))
    assert classify_turn(CONTEXT_TEXT, "你好", False, client=client) is None


# ============================================================
# 消息构造：注入隔离 + few-shot 存在
# ============================================================


def test_messages_wrap_user_input_and_include_context() -> None:
    from agent.core.prompt_guard import INPUT_CLOSE, INPUT_OPEN

    client = _FakeClient(json.dumps(_payload(), ensure_ascii=False))
    classify_turn("【首轮原始需求】\n出去玩\n", "你好", False, client=client)
    assert client.last_messages is not None
    last_user = [m for m in client.last_messages if m.role == "user"][-1]
    assert INPUT_OPEN in last_user.content and INPUT_CLOSE in last_user.content
    assert "你好" in last_user.content
    assert "出去玩" in last_user.content
    # few-shot 确实进了 messages（system + 若干组 user/assistant 对 + 本轮）
    assert len(client.last_messages) > 3
