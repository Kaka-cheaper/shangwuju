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
import re
from dataclasses import dataclass

from pydantic import ValidationError

from schemas.intent import IntentExtraction

from ..core.llm_client import LLMChatResponse, LLMClient, LLMMessage, strip_json_fence
from ..core.prompt_guard import wrap_user_input
from .prompts.intent_parser_prompt import (
    INTENT_PARSER_FEW_SHOTS,
    INTENT_PARSER_SYSTEM_PROMPT,
    build_intent_parser_system_prompt_with_priors,
    compute_injected_priors,
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


# ============================================================
# ADR-0014 决策 1（G-1）：首轮出处——LLM 自报 + 规则交叉校正
# ============================================================
#
# 先验注入集是已知的（`compute_injected_priors`，与实际拼进 prompt 的
# `build_intent_parser_system_prompt_with_priors` 用同一份 `data.memory_store.
# compute_priors` 计算，不重复定义一份"哪些算先验"——真相源声明纪律）。
# 校正只做一个方向的纠偏："LLM 自报的出处 = 先验值本身、但原话确实没提"，
# 这是 ADR 证实的唯一失配方向（先验拼进输出后 LLM 把它当 user_stated 交差）；
# 反方向（自报 prior 但原话其实提了）不做规则纠正，ADR 未要求，也没有同等
# 强度的证据支撑。冲突时规则赢——不管 LLM 自报什么，命中条件就强制 prior。

_SCALAR_PROVENANCE_FIELDS: tuple[str, ...] = (
    "start_time",
    "start_weekday",
    "duration_hours",
    "distance_max_km",
    "social_context",
    "capacity_requirement",
    # ADR-0014 决策 3（G-3）：budget_per_person 同款标量规范。已经是 Optional，
    # 循环体 `if value is None: continue` 天然处理"用户没明说数字"——不写
    # provenance 键（与 start_weekday 的 None 处理完全同款，非新增分支）。
    # 无先验注入通道（compute_injected_priors 不产 budget 值），故不需要在下方
    # forced_prior 分支加 budget 特判；非 None 时落到"自报缺失兜底 user_stated"
    # 分支——该字段没有"随手给个默认数字"的 schema 默认值可比对，任何非空值
    # 几乎恒为 user_stated（唯一产出路径是原话明说数字，见 schema 字段 docstring）。
    "budget_per_person",
)

# 列表字段里"有先验注入通道"的三类受控词典（先验可能把值塞进这三个字段）。
_LIST_FIELDS_WITH_PRIOR: tuple[str, ...] = (
    "physical_constraints",
    "dietary_constraints",
    "experience_tags",
)

# extra_services 无先验注入通道（persona/memory 都不会喂它），只做自报兜底，
# 不做 prior 强制纠偏。preferred_poi_types / companions 不在 field_provenance
# 覆盖范围内（见 schemas/intent.py::IntentExtraction.field_provenance 字段
# docstring 的范围拍板），不在这里处理。
_LIST_FIELDS_NO_PRIOR: tuple[str, ...] = ("extra_services",)

# 标量字段的 schema 默认值（用于自报缺失时的 default/user_stated 兜底判断）。
# 没有"自然默认值"的字段（start_time/start_weekday/capacity_requirement）不登记，
# 缺自报时一律兜底 user_stated（这几个字段只要有值，几乎总是来自用户或明确推断，
# 不存在"随手给个默认数字"的情况）。
_SCALAR_SCHEMA_DEFAULTS: dict[str, object] = {
    "duration_hours": [4, 6],
    "distance_max_km": 5.0,
    "social_context": "家庭日常",
}

_DISTANCE_HINT_RE = re.compile(
    r"\d+\s*(公里|千米|km|米)|太远|远一点|远点|近一点|近些|别太远|靠近|不限距离"
)


def _floats_equal(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(float(a) - float(b)) < tol


def _distance_stated_in_raw(raw: str) -> bool:
    """raw_input 里是否有"用户自己提过距离"的文字线索（数字+单位，或远近措辞）。

    只用于："输出的 distance_max_km 恰好等于先验建议距离"时，判断这个巧合
    是先验补的还是用户自己也说了同一个数——有线索就不强制纠正为 prior
    （宁可信自报，不误伤真正的用户输入）。
    """
    return bool(_DISTANCE_HINT_RE.search(raw or ""))


def _apply_provenance_correction(
    intent: IntentExtraction, user_id: str | None
) -> IntentExtraction:
    """首轮出处：LLM 自报 + 规则交叉校正（ADR-0014 决策 1）。

    覆盖范围与键规范见 `schemas.intent.IntentExtraction.field_provenance`
    字段 docstring。规则：
    - 命中"值 == 先验值 且原话未提"→ 机械回标 `prior`（覆盖自报，规则赢）。
    - 否则沿用 LLM 自报；自报缺失时按 schema 默认值兜底（等于默认值→
      `default`，否则 `user_stated`）。
    """
    priors = compute_injected_priors(user_id)
    self_reported = dict(intent.field_provenance or {})
    raw = intent.raw_input or ""
    corrected: dict[str, str] = {}

    for field in _SCALAR_PROVENANCE_FIELDS:
        value = getattr(intent, field)
        if value is None:
            continue

        forced_prior = False
        if field == "social_context" and priors.social_context is not None:
            forced_prior = value == priors.social_context and value not in raw
        elif field == "distance_max_km" and priors.distance_max_km is not None:
            forced_prior = _floats_equal(
                value, priors.distance_max_km
            ) and not _distance_stated_in_raw(raw)

        if forced_prior:
            corrected[field] = "prior"
            continue

        reported = self_reported.get(field)
        if reported:
            corrected[field] = reported
        else:
            default_val = _SCALAR_SCHEMA_DEFAULTS.get(field)
            corrected[field] = "default" if value == default_val else "user_stated"

    for field in _LIST_FIELDS_WITH_PRIOR:
        for value in getattr(intent, field) or []:
            key = f"{field}:{value}"
            if value in priors.tags and value not in raw:
                corrected[key] = "prior"
            else:
                corrected[key] = self_reported.get(key) or "user_stated"

    for field in _LIST_FIELDS_NO_PRIOR:
        for value in getattr(intent, field) or []:
            key = f"{field}:{value}"
            corrected[key] = self_reported.get(key) or "user_stated"

    return intent.model_copy(update={"field_provenance": corrected})


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
        # ADR-0014 决策 1（G-1）：出处交叉校正（必须在 raw_input 兜底之后——
        # 校正要用最终 raw_input 判断"原话有没有提到"）
        intent = _apply_provenance_correction(intent, user_id)
        return intent

    # 不应到达
    raise IntentParseError(
        reason="exhausted",
        raw_text=last_response.content if last_response else None,
        last_validation_error=error_feedback,
    )
