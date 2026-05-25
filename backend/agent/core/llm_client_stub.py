"""agent.llm_client_stub —— 给 W2/W3 测试用的 LLMClient 实现。

W3 前端开发时不需要真 LLM，可在 .env 里设 `LLM_PROVIDER=stub` 把整条链路跑通。
W2 单测也用本 stub，避免每次测试都调真 LLM。

**警告**：返回的内容是固定 fixture，仅覆盖家庭主场景。其他输入会返一段固定文案。
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Literal

from .llm_client import LLMChatResponse, LLMMessage


class StubLLMClient:
    """固定响应的 LLM 客户端，仅用于本地开发/单测。"""

    provider: str = "stub"
    model: str = "stub-model"

    def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> LLMChatResponse:
        # 简单的家庭主场景兜底
        sample_intent = {
            "start_time": "today_afternoon",
            "duration_hours": [4, 6],
            "distance_max_km": 5,
            "companions": [
                {"role": "妻子", "count": 1},
                {"role": "孩子", "age": 5, "count": 1},
            ],
            "physical_constraints": ["亲子友好", "适合 5-10 岁"],
            "dietary_constraints": ["低脂", "健康轻食"],
            "experience_tags": [],
            "social_context": "家庭日常",
            "raw_input": _last_user(messages),
            "parse_confidence": 0.88,
            "ambiguous_fields": [],
        }
        return LLMChatResponse(
            content=json.dumps(sample_intent, ensure_ascii=False),
            finish_reason="stop",
        )

    def stream_chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        text = "已为你规划好下午行程：森林儿童探索乐园 → 轻语沙拉..."
        for ch in text:
            yield ch

    def chat_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.3,
        tool_choice: Literal["auto", "required", "none"] = "auto",
    ) -> LLMChatResponse:
        # P2 阶段 A 同学接入真模型后再细化；当前返一个空 tool_calls 的 stop
        return LLMChatResponse(
            content="（stub）模拟 Agent 决策完成。",
            finish_reason="stop",
        )


def _last_user(messages: list[LLMMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user" and m.content:
            return m.content
    return ""
