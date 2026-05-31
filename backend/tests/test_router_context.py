"""classify_input has_itinerary 上下文注入测试（spec feedback-routing-fix Task 2 / R3）。

验证：
- has_itinerary=True 时，发给 LLM 的 messages 含反馈上下文提示（R3.1）
- has_itinerary=False 时，messages 不含该提示（R6.4 行为不变）
"""

from __future__ import annotations

import json

from agent.intent.router import classify_input
from agent.intent.prompts.router_prompt import FEEDBACK_CONTEXT_HINT


class _SpyClient:
    """记录最后一次 chat() 收到的 messages，返回固定合法 RouterDecision JSON。"""

    provider = "spy"
    model = "spy-model"

    def __init__(self) -> None:
        self.last_messages = None

    def chat(self, messages, *, temperature=0.3, response_format=None, max_tokens=None):
        self.last_messages = messages
        from agent.core.llm_client import LLMChatResponse

        payload = {
            "input_kind": "ambiguous",
            "confidence": 0.8,
            "reply_text": "想再说说哪里不合适吗？",
            "tone": "warm",
            "cta_chips": [],
            "rationale": "spy",
        }
        return LLMChatResponse(content=json.dumps(payload, ensure_ascii=False))

    def stream_chat(self, messages, *, temperature=0.3, max_tokens=None):
        yield ""

    def chat_with_tools(self, messages, tools, *, temperature=0.3, tool_choice="auto"):
        raise NotImplementedError


def _hint_present(messages) -> bool:
    """检查 messages 中是否有任一条 content 含反馈上下文提示。"""
    marker = FEEDBACK_CONTEXT_HINT.split("\n")[0]  # 取提示首行作为标记
    return any(marker in (m.content or "") for m in messages)


def test_has_itinerary_injects_context() -> None:
    """R3.1：has_itinerary=True 时 messages 含反馈上下文提示。"""
    client = _SpyClient()
    classify_input("想轻松点", client=client, has_itinerary=True)
    assert _hint_present(client.last_messages), "has_itinerary=True 应注入反馈上下文提示"


def test_no_itinerary_no_context() -> None:
    """R6.4：has_itinerary=False（默认）时不注入提示，行为不变。"""
    client = _SpyClient()
    classify_input("今天下午想带孩子出去玩", client=client)
    assert not _hint_present(client.last_messages), "无方案时不应注入反馈上下文"


def test_no_itinerary_user_input_intact() -> None:
    """R6.4 + 注入隔离（spec prompt-injection-defense R3）：无方案时最后一条 user
    message 含原始输入，且被【用户输入开始/结束】边界包裹（隔离防注入）。"""
    from agent.core.prompt_guard import INPUT_CLOSE, INPUT_OPEN

    client = _SpyClient()
    classify_input("你是谁", client=client, has_itinerary=False)
    last_user = [m for m in client.last_messages if m.role == "user"][-1]
    assert "你是谁" in last_user.content
    assert INPUT_OPEN in last_user.content and INPUT_CLOSE in last_user.content
