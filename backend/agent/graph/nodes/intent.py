"""nodes.intent —— 意图抽取节点。

复用 backend/agent/intent_parser.py 的 parse_intent —— 它已含 persona prior 注入。

输入：state["user_input"]
输出：
- state["intent"] = IntentExtraction
- state["quality_issues"]：spec execution-quality-review R1 词典外社交意图检测
  当 raw_input 含明确社交关键词（老师 / 客户 / 宠物 / 同事 / 网友 等不在 9 词典内）
  但 social_context 已被 LLM 强行映射成 9 选 1 时，写入降级文案让 narrator 主动质疑。
  设计纪律：仅在「关键词命中且与 social_context 语义偏差大」时触发，避免误伤。
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.intent.parser import IntentParseError, parse_intent
from agent.core.llm_client import get_llm_client
from schemas.intent import IntentExtraction


# spec execution-quality-review R1：词典外社交关键词 → 推断的最接近 social_context 9 选 1
# 当 raw_input 含 key 但 social_context 不在 fits 集合时，触发降级文案
_OUT_OF_VOCAB_SOCIAL_KEYWORDS: dict[str, set[str]] = {
    "老师": {"商务接待", "同学重聚", "朋友热闹"},
    "客户": {"商务接待"},
    "宠物": {"独处放空", "家庭日常"},  # 宠物伴随通常是独处或全家
    "狗子": {"独处放空", "家庭日常"},
    "同事": {"商务接待", "朋友热闹"},
    "网友": {"朋友热闹", "闺蜜聊天"},
    "导师": {"商务接待", "同学重聚"},
    "前辈": {"商务接待", "同学重聚"},
    "邻居": {"朋友热闹", "家庭日常"},
}


def _detect_out_of_vocab_social(raw_input: str, social_context: str) -> str | None:
    """检测词典外社交关键词与抽取的 social_context 是否语义偏差大。

    返回降级文案（中文）；None 表示无偏差不触发。
    """
    if not raw_input or not social_context:
        return None

    text = raw_input.lower()
    for keyword, fits in _OUT_OF_VOCAB_SOCIAL_KEYWORDS.items():
        if keyword in text or keyword in raw_input:
            # 命中关键词
            if social_context in fits:
                # social_context 已经在合理映射集合里，不质疑
                continue
            # 关键词命中但 social_context 偏离 → 触发降级文案
            return (
                f"我把您说的「{keyword}」理解为「{social_context}」场景，"
                f"如果不太合适，您可以说「换成 X 场景」让我重新规划"
            )
    return None


def _build_fallback_intent(user_input: str) -> IntentExtraction:
    """意图解析彻底失败时的兜底意图（保 demo 不崩）。

    设计：用最保守的默认值——空同行人 / 空词典字段 / 默认家庭日常场景 /
    宽松距离时长——让下游 search/blueprint 仍能出一个通用方案，
    raw_input 保留用户原话供 narrator 引用。低 parse_confidence 标记不确定。
    """
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input=user_input,
        parse_confidence=0.3,
        ambiguous_fields=["all"],
    )


def intent_node(state: AgentState) -> dict[str, Any]:
    client = get_llm_client()
    user_input = state.get("user_input") or ""
    user_id = state.get("user_id") or "demo_user"

    # 韧性修复：LLM 偶发返回非法 JSON → parse_intent 重试耗尽抛 IntentParseError。
    # 旧行为：异常冒泡到 graph 流 → stream_error → demo 崩（评委看到红色错误）。
    # 新行为：捕获后用兜底意图继续跑，并写 quality_issue 让 narrator 诚实告知。
    fallback_used = False
    try:
        # max_retries=2（共 3 次机会）：LLM 偶发 JSON 错是瞬态，多给一次重试
        # 显著降低落到兜底意图的概率（兜底是降级体验，能避则避）
        intent = parse_intent(
            user_input, client=client, user_id=user_id, max_retries=2
        )
    except IntentParseError as e:
        import logging as _logging

        _logging.getLogger("agent.graph.intent").warning(
            "intent_parse_failed_fallback: %s（raw_input=%r）", e.reason, user_input[:60]
        )
        intent = _build_fallback_intent(user_input)
        fallback_used = True

    # spec execution-quality-review R1：词典外社交意图降级文案
    out: dict[str, Any] = {"intent": intent}
    issues: list[str] = list(state.get("quality_issues") or [])

    if fallback_used:
        issues.append(
            "我没完全听懂你的需求，先按通用下午行程帮你安排了，"
            "你可以再说一遍或换种说法，我重新规划。"
        )

    warning = _detect_out_of_vocab_social(
        user_input, getattr(intent, "social_context", "") or ""
    )
    if warning:
        issues.append(warning)

    if issues:
        out["quality_issues"] = issues
    return out
