"""agent.refiner —— 用户拒绝方案 + 反馈 → 调整后的 IntentExtraction。

业务故事见 schemas/refine.py 顶部 docstring。

实现策略：
- LLM 调用 1 次，response_format=json_object
- 围栏剥离 + Pydantic 二次校验（防漂移；pitfalls P2-预埋）
- 若校验失败 → 错误回灌 LLM 1 次重试
- 若 2 次都失败 → 走规则化兜底（_rule_fallback：根据反馈关键词调字段）
  评分硬要求：refine 端到端必须有降级路径，**不能**让 Demo 上转圈
- LLM 成功后跑 _enforce_duration_consistency：若反馈含具体小时数，
  强制让 refined_intent.duration_hours 与 changed_fields 对齐
  （防 LLM 在文本里说改了但 JSON 字段没改，参考 problem.md 问题 11）

防御要点（与 intent_parser 一致）：
- 词典外 tag 由 Pydantic Literal 拦截 → 校验失败 → 重试 / 兜底
- raw_input 字段不允许被 LLM 改写（兜底覆盖回原值）
- 顶层禁止字段（scene_type 等）由 §5.7 model_config extra="forbid" 拦截

不负责：
- 重新规划（在 planner.plan_itinerary_with_mode）
- HTTP 端点（在 main.py，B 块）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from schemas.intent import IntentExtraction
from schemas.refine import RefinementOutput

from .llm_client import LLMClient, LLMMessage, strip_json_fence
from .prompts.refiner_prompt import (
    REFINER_FEW_SHOTS,
    REFINER_SYSTEM_PROMPT,
    build_user_message,
)


# ============================================================
# 异常
# ============================================================

@dataclass
class RefinementError(Exception):
    """refiner 全部路径失败（LLM 重试 + 兜底都不行）。

    上层应推 stream_error 事件并终止 SSE 流。
    """

    reason: str
    last_validation_error: str | None = None

    def __str__(self) -> str:  # pragma: no cover
        return f"RefinementError({self.reason})"


# ============================================================
# 主入口
# ============================================================

def refine_intent(
    original: IntentExtraction,
    feedback_text: str,
    *,
    client: LLMClient | None = None,
    max_retries: int = 1,
) -> RefinementOutput:
    """合并反馈进原 intent。

    流程：
    1. 调 LLM（response_format=json_object）
    2. 剥围栏 + json.loads
    3. Pydantic v2 校验（refined_intent 必须合法 IntentExtraction）
    4. 若失败 → 错误回灌 1 次
    5. 若仍失败 → _rule_fallback 兜底（不抛异常）

    `client` 缺省时通过 get_llm_client() 自动按 LLM_PROVIDER 环境变量构造，
    便于 HTTP 层（main.py）直接 `refine_intent(original, feedback)` 调用而不必关心 LLM 接线。
    """
    if client is None:
        from .llm_client import get_llm_client

        try:
            client = get_llm_client()
        except (ValueError, RuntimeError):
            # 缺 API key / base_url 等配置问题 → 直接走 _rule_fallback
            return _rule_fallback(original, feedback_text)
    original_json = original.model_dump_json()

    error_feedback: str | None = None
    for attempt in range(max_retries + 1):
        try:
            return _llm_refine(
                original=original,
                original_json=original_json,
                feedback_text=feedback_text,
                client=client,
                error_feedback=error_feedback,
            )
        except (RefinementError, ValidationError, json.JSONDecodeError) as e:
            error_feedback = str(e)
            if attempt >= max_retries:
                # 走兜底，不抛异常（Demo 不能因为 LLM 出 bug 而转圈）
                return _rule_fallback(original, feedback_text)


def _llm_refine(
    *,
    original: IntentExtraction,
    original_json: str,
    feedback_text: str,
    client: LLMClient,
    error_feedback: str | None,
) -> RefinementOutput:
    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=REFINER_SYSTEM_PROMPT),
    ]
    for fs_user, fs_assistant in REFINER_FEW_SHOTS:
        messages.append(LLMMessage(role="user", content=fs_user))
        messages.append(LLMMessage(role="assistant", content=fs_assistant))

    user_msg = build_user_message(original_json, feedback_text)
    if error_feedback:
        user_msg = (
            f"上次输出存在错误：\n{error_feedback}\n\n"
            f"请重新按 schema 严格输出。\n\n"
            f"{user_msg}"
        )
    messages.append(LLMMessage(role="user", content=user_msg))

    resp = client.chat(
        messages,
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    cleaned = strip_json_fence(resp.content)
    if not cleaned:
        raise RefinementError(reason="empty_response")

    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise RefinementError(reason="not_a_json_object")

    # raw_input 兜底：保留原句 + 拼接本次反馈，让下游能从 raw_input 提取精确约束
    # （pitfalls P1-2026-05-17 引申：反馈作为最高优先级约束，必须落到下游可读的字段）
    refined_intent_data = payload.get("refined_intent", {})
    if isinstance(refined_intent_data, dict):
        if feedback_text and feedback_text.strip():
            refined_intent_data["raw_input"] = (
                f"{original.raw_input}（反馈：{feedback_text.strip()}）"
            )
        else:
            refined_intent_data["raw_input"] = original.raw_input

    refined_intent = IntentExtraction.model_validate(refined_intent_data)

    raw_changed = list(payload.get("changed_fields", []) or [])
    # 问题 11 修复：LLM 可能在 changed_fields 里说改了时长，但 refined_intent.duration_hours
    # 字段没真改。强制对齐反馈里的具体小时数。
    refined_intent, fixed_changed = _enforce_duration_consistency(
        refined_intent, raw_changed, feedback_text
    )

    return RefinementOutput(
        refined_intent=refined_intent,
        changed_fields=fixed_changed,
        refiner_note=payload.get("refiner_note") or None,
    )


# ============================================================
# 规则化兜底（LLM 失败时不让 Demo 翻车）
# ============================================================

# 关键词 → 字段调整映射（粗粒度）
_KEYWORDS_DISTANCE_NEAR = ("太远", "近一点", "近些", "别太远", "靠近")
_KEYWORDS_DISTANCE_FAR = ("远一点", "远点", "再远", "不限距离")
_KEYWORDS_CHEAPER = ("太贵", "便宜", "划算", "省点", "预算紧", "贵了")
_KEYWORDS_TIME_TIGHT = ("时间紧", "快一点", "短一点", "时间不多")
_KEYWORDS_TIME_LOOSE = ("时间多", "长一点", "再长")


# ===== 中文数字 → 阿拉伯数字（仅 1-9，常用即可）=====
_CN_DIGITS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _extract_duration_from_feedback(feedback: str) -> tuple[int, int] | None:
    """从反馈文本里抽取具体小时时长。

    支持模式：
    - "我只有 1 小时"  / "一小时"        → (1, 1)
    - "就 2 小时吧"   / "两小时"         → (2, 2)
    - "1 到 2 个小时"                    → (1, 2)
    - "再给我 2-3 小时"                  → (2, 3)

    无具体数字（"时间紧" / "时间多" / "半小时"）→ None，让上层走关键词分支或保留原值。

    设计纪律：本函数是问题 11 修复的核心；写完后 _rule_fallback 与 _enforce_duration_consistency
    都依赖它。返回 tuple 而非 list 是因为下游统一用 list(refined.duration_hours) 比较，
    tuple 让函数纯化（不可变）。
    """
    import re

    if not feedback:
        return None
    s = feedback.strip()
    # 把中文数字归一为阿拉伯数字（仅在「数字 + 小时」上下文里替换，避免误伤）
    for cn, ar in _CN_DIGITS.items():
        s = s.replace(f"{cn}小时", f"{ar} 小时")
        s = s.replace(f"{cn}个小时", f"{ar} 小时")

    # 范围模式（必须先匹配，避免被单数字模式截断）
    range_re = re.compile(r"(\d+)\s*(?:到|至|-|~)\s*(\d+)\s*(?:个)?\s*小时")
    m = range_re.search(s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if 0 < lo <= hi <= 12:
            return (lo, hi)

    # 单数字模式
    single_re = re.compile(r"(\d+)\s*(?:个)?\s*小时")
    m = single_re.search(s)
    if m:
        n = int(m.group(1))
        if 0 < n <= 12:
            return (n, n)

    return None


def _enforce_duration_consistency(
    refined: IntentExtraction,
    changed_fields: list[str],
    feedback: str,
) -> tuple[IntentExtraction, list[str]]:
    """LLM 输出后校验：refined.duration_hours 必须与 feedback 真实数字一致。

    问题 11 根因：LLM 在 changed_fields 文本里复读了用户的"1 小时"，
    但 refined_intent.duration_hours 字段保留原值 [4,6]。下游 planner 用错时长
    导致行程仍 4 小时多。

    策略：
    - 反馈含具体数字（_extract_duration_from_feedback 命中）
    - refined.duration_hours 与提取值不符
    → 强制覆盖 refined.duration_hours，并修正 changed_fields 文本（如果 LLM 没生成时长条目，则补一条）
    """
    extracted = _extract_duration_from_feedback(feedback)
    if extracted is None:
        return refined, changed_fields

    current = tuple(refined.duration_hours)
    if current == extracted:
        return refined, changed_fields  # 已一致

    # 强制对齐
    fixed = refined.model_copy(update={"duration_hours": list(extracted)})
    fixed_changed = list(changed_fields)

    # 修正或补充 changed_fields 里的时长条目
    new_msg = f"时长：{list(current)} → {list(extracted)} 小时"
    has_duration_entry = any("时长" in c for c in fixed_changed)
    if has_duration_entry:
        fixed_changed = [
            new_msg if "时长" in c else c for c in fixed_changed
        ]
    else:
        fixed_changed.append(new_msg)

    return fixed, fixed_changed


def _rule_fallback(
    original: IntentExtraction, feedback_text: str
) -> RefinementOutput:
    """LLM 失败时按关键词做轻量调整。

    确保 refined_intent 仍是合法 IntentExtraction（用 model_copy(update=...)）。
    """
    feedback = (feedback_text or "").strip()
    feedback_lower = feedback.lower()

    updates: dict = {}
    changed: list[str] = []

    # 距离
    if any(k in feedback for k in _KEYWORDS_DISTANCE_NEAR):
        old = original.distance_max_km
        new = max(2.0, round(old * 0.6, 1))
        if new < old:
            updates["distance_max_km"] = new
            changed.append(f"距离上限：{old}km → {new}km")
    elif any(k in feedback for k in _KEYWORDS_DISTANCE_FAR):
        old = original.distance_max_km
        new = min(15.0, round(old * 1.5, 1))
        if new > old:
            updates["distance_max_km"] = new
            changed.append(f"距离上限：{old}km → {new}km")

    # 预算（去高人均 / 商务体面）
    if any(k in feedback for k in _KEYWORDS_CHEAPER):
        new_dietary = [t for t in original.dietary_constraints if t != "高人均"]
        if "健康轻食" not in new_dietary:
            new_dietary.append("健康轻食")
        if new_dietary != original.dietary_constraints:
            updates["dietary_constraints"] = new_dietary
            changed.append("去掉：高人均；加：健康轻食")
        new_exp = [t for t in original.experience_tags if t != "商务体面"]
        if new_exp != original.experience_tags:
            updates["experience_tags"] = new_exp
            changed.append("去掉体验：商务体面")

    # 时间——精确数字优先（"我只有 1 小时" / "两小时" / "2 到 3 小时"）
    extracted_duration = _extract_duration_from_feedback(feedback)
    if extracted_duration is not None:
        if tuple(original.duration_hours) != extracted_duration:
            updates["duration_hours"] = list(extracted_duration)
            changed.append(
                f"时长：{list(original.duration_hours)} → {list(extracted_duration)} 小时"
            )
    elif any(k in feedback for k in _KEYWORDS_TIME_TIGHT):
        if list(original.duration_hours) != [2, 3]:
            updates["duration_hours"] = [2, 3]
            changed.append(f"时长：{list(original.duration_hours)} → [2, 3] 小时")
    elif any(k in feedback for k in _KEYWORDS_TIME_LOOSE):
        if list(original.duration_hours) != [5, 7]:
            updates["duration_hours"] = [5, 7]
            changed.append(f"时长：{list(original.duration_hours)} → [5, 7] 小时")

    # 反馈为空 / 未命中关键词 → 距离 -1km 兜底（让候选打散）
    if not updates:
        old = original.distance_max_km
        if old > 2:
            new = max(2.0, round(old - 1, 1))
            updates["distance_max_km"] = new
            changed.append(f"距离上限：{old}km → {new}km（轻量调整）")

    # raw_input 兜底：保留原句 + 拼接本次反馈
    if feedback and feedback.strip():
        updates["raw_input"] = f"{original.raw_input}（反馈：{feedback.strip()}）"

    refined = original.model_copy(update=updates)
    note = (
        "已基于反馈关键词做轻量调整（LLM 不可用，走规则化兜底）。"
        if changed
        else "未识别可执行调整，已重新打散候选排序。"
    )
    return RefinementOutput(
        refined_intent=refined,
        changed_fields=changed,
        refiner_note=note,
    )
