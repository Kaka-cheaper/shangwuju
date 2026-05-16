"""agent.intent_parser —— 自然语言 → IntentExtraction（§5.7 D-SoT）。

实现方式：
- 用 LLMClient.chat（response_format=json_object）抽取
- 围栏剥离 + Pydantic 校验双保险（pitfalls P2-预埋）
- 校验失败回灌 LLM 1 次（让模型自己改正）

不负责：
- LLM 客户端实现（在 llm_client.py）
- Prompt 文案（在 prompts/system_prompt.py）
- 规划循环（在 planner.py）
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from schemas.intent import IntentExtraction

from .llm_client import LLMChatResponse, LLMClient, LLMMessage, strip_json_fence
from .prompts.system_prompt import (
    INTENT_PARSER_FEW_SHOTS,
    INTENT_PARSER_SYSTEM_PROMPT,
)


@dataclass
class IntentParseError(Exception):
    """意图解析最终失败。Agent 上层应触发 ask_back 流程。"""

    reason: str
    raw_text: str | None = None
    last_validation_error: str | None = None

    def __str__(self) -> str:  # pragma: no cover
        return f"IntentParseError({self.reason})"


def _build_messages(user_input: str, error_feedback: str | None = None) -> list[LLMMessage]:
    """组装 system + few-shot + user 消息。"""
    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=INTENT_PARSER_SYSTEM_PROMPT),
    ]
    for fs_user, fs_assistant in INTENT_PARSER_FEW_SHOTS:
        messages.append(LLMMessage(role="user", content=fs_user))
        messages.append(LLMMessage(role="assistant", content=fs_assistant))
    if error_feedback:
        # 把首次校验错误回灌让 LLM 自行修正
        messages.append(
            LLMMessage(
                role="user",
                content=(
                    f"以下是上一次输出的校验错误，请按 schema 修正后**重新输出**纯 JSON：\n"
                    f"{error_feedback}\n\n"
                    f"原始用户输入：{user_input}"
                ),
            )
        )
    else:
        messages.append(LLMMessage(role="user", content=user_input))
    return messages


def _parse_json(text: str | None) -> dict:
    """剥离围栏 + 容错解析。"""
    if text is None:
        raise IntentParseError(reason="empty_response")
    cleaned = strip_json_fence(text)
    if not cleaned:
        raise IntentParseError(reason="empty_response", raw_text=text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise IntentParseError(
            reason="json_decode_failed",
            raw_text=text,
            last_validation_error=str(e),
        ) from e
    if not isinstance(data, dict):
        raise IntentParseError(reason="not_a_json_object", raw_text=text)
    return data


def parse_intent(
    user_input: str,
    *,
    client: LLMClient,
    max_retries: int = 1,
) -> IntentExtraction:
    """主入口：用 LLM 抽取意图，Pydantic 二次校验。

    流程：
    1. 调 LLM（response_format=json_object）
    2. 剥围栏 + json.loads
    3. Pydantic v2 校验
    4. 失败 → 把错误回灌 LLM 重试 max_retries 次
    """
    error_feedback: str | None = None
    last_response: LLMChatResponse | None = None

    for attempt in range(max_retries + 1):
        messages = _build_messages(user_input, error_feedback)
        last_response = client.chat(
            messages,
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        try:
            payload = _parse_json(last_response.content)
        except IntentParseError as e:
            error_feedback = e.last_validation_error or "上次输出不是合法 JSON"
            if attempt >= max_retries:
                raise
            continue

        try:
            intent = IntentExtraction.model_validate(payload)
        except ValidationError as ve:
            error_feedback = str(ve)
            if attempt >= max_retries:
                raise IntentParseError(
                    reason="schema_validation_failed",
                    raw_text=last_response.content,
                    last_validation_error=str(ve),
                ) from ve
            continue

        # 规则修正：raw_input 兜底；ambiguous_fields 缺失时按 confidence 推断
        if not intent.raw_input:
            intent = intent.model_copy(update={"raw_input": user_input})
        return intent

    # 不应到达
    raise IntentParseError(
        reason="exhausted",
        raw_text=last_response.content if last_response else None,
        last_validation_error=error_feedback,
    )
