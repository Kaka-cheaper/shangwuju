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

from ..core.llm_client import LLMChatResponse, LLMClient, LLMMessage, strip_json_fence
from ..core.prompt_guard import wrap_user_input
from .prompts.intent_parser_prompt import (
    INTENT_PARSER_FEW_SHOTS,
    INTENT_PARSER_SYSTEM_PROMPT,
    build_intent_parser_system_prompt_with_priors,
)


@dataclass
class IntentParseError(Exception):
    """意图解析最终失败。Agent 上层应触发 ask_back 流程。"""

    reason: str
    raw_text: str | None = None
    last_validation_error: str | None = None

    def __str__(self) -> str:  # pragma: no cover
        return f"IntentParseError({self.reason})"


def _build_messages(
    user_input: str,
    error_feedback: str | None = None,
    *,
    user_id: str | None = None,
) -> list[LLMMessage]:
    """组装 system + few-shot + user 消息。

    user_id 不为空时，system prompt 会拼接 persona/memory prior（Phase 0.7）。
    """
    system_prompt = (
        build_intent_parser_system_prompt_with_priors(user_id)
        if user_id
        else INTENT_PARSER_SYSTEM_PROMPT
    )
    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=system_prompt),
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
                    f"原始用户输入：{wrap_user_input(user_input)}"
                ),
            )
        )
    else:
        # spec prompt-injection-defense L3：边界标记包裹用户输入，防指令/数据混淆
        messages.append(LLMMessage(role="user", content=wrap_user_input(user_input)))
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


# pitfalls 2026-05-24：LLM 经常 hallucinate `pace_profile.total_active_max_min`
# （正确字段名 `total_active_min`）+ 偶尔赋 None 值。PaceProfile.extra="forbid"
# 会拒绝陌生字段；NonNegativeInt 会拒绝 None。这里按 schema 白名单做防御性清洗，
# 不抛错，让 LLM 偶发漂移不至于让整条 demo 链路崩。
_PACE_PROFILE_ALLOWED_FIELDS = frozenset(
    {
        "single_session_max_min",
        "total_active_min",
        "break_every_min",
        "preferred_dwell_min",
    }
)


def _sanitize_payload(payload: dict) -> dict:
    """规范 LLM 输出 payload 中已知容易漂移的字段。

    当前仅 sanitize `pace_profile`：
    - 删除 schema 未定义字段（如 LLM hallucinate 的 `total_active_max_min`）
    - 删除值为 None 的字段（避免 NonNegativeInt 校验报错）
    - 整对象只剩空 dict / 全 None → 整体设为 None
    """
    pace = payload.get("pace_profile")
    if isinstance(pace, dict):
        cleaned = {
            k: v
            for k, v in pace.items()
            if k in _PACE_PROFILE_ALLOWED_FIELDS and v is not None
        }
        payload["pace_profile"] = cleaned if cleaned else None
    return payload


def parse_intent(
    user_input: str,
    *,
    client: LLMClient,
    max_retries: int = 1,
    user_id: str | None = None,
) -> IntentExtraction:
    """主入口：用 LLM 抽取意图，Pydantic 二次校验。

    Phase 0.7：传 user_id 时 system prompt 注入 persona+memory prior（"我是谁 + 学过什么"）。
    user_id 为 None 时退化为原行为（无 prior，按 §5.7 D-SoT 抽取）。

    流程：
    1. 调 LLM（response_format=json_object）
    2. 剥围栏 + json.loads
    3. Pydantic v2 校验
    4. 失败 → 把错误回灌 LLM 重试 max_retries 次
    """
    error_feedback: str | None = None
    last_response: LLMChatResponse | None = None

    for attempt in range(max_retries + 1):
        messages = _build_messages(user_input, error_feedback, user_id=user_id)
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

        # LLM hallucination 防御性清洗（pace_profile 字段漂移 / None 值）
        payload = _sanitize_payload(payload)

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
