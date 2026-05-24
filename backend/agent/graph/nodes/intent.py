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
from agent.intent.parser import parse_intent
from agent.core.llm_client import get_llm_client


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


def intent_node(state: AgentState) -> dict[str, Any]:
    client = get_llm_client()
    user_input = state.get("user_input") or ""
    user_id = state.get("user_id") or "demo_user"

    intent = parse_intent(user_input, client=client, user_id=user_id)

    # spec execution-quality-review R1：词典外社交意图降级文案
    out: dict[str, Any] = {"intent": intent}
    warning = _detect_out_of_vocab_social(
        user_input, getattr(intent, "social_context", "") or ""
    )
    if warning:
        existing = list(state.get("quality_issues") or [])
        existing.append(warning)
        out["quality_issues"] = existing
    return out
