"""feedback_detector 语义类反馈识别测试（spec feedback-routing-fix Task 1 / R2）。

验证扩充词典后：
- 7 个曾漏判的自然反馈措辞 → True（R2.2）
- 明确的新规划需求 → False（R2.3 不误伤）
- 既有强信号反馈 → 仍 True（R6.1 不回归）
"""

from __future__ import annotations

import pytest

from agent.core.feedback_detector import looks_like_feedback


# R2.2：曾经漏判的语义类反馈（无数字单位，靠语义表达）
_SEMANTIC_FEEDBACK = [
    "太赶了",
    "节奏太快",
    "想轻松点",
    "再优雅一点",
    "行程太满了",
    "能不能轻松些",
    "这个不太好",
]


@pytest.mark.parametrize("text", _SEMANTIC_FEEDBACK)
def test_semantic_feedback_detected(text: str) -> None:
    """R2.2：语义类反馈措辞应被识别为 feedback。"""
    assert looks_like_feedback(text) is True, f"{text!r} 应被识别为反馈"


# R2.3：明确的新规划需求（不能被误判为反馈）
# 注意：纯新需求 = 不含任何反馈词。含「换/改」等弱信号词的新需求
# （如「换成和朋友打球」）由 router_node 集成测试（Task 3 / R4）覆盖——
# feedback_detector 是高召回粗筛，弱信号的精确区分交给 router_node 的 LLM 层。
_NEW_REQUESTS = [
    "今天下午想带孩子出去玩",
    "周末带爸妈去吃顿好的",
    "和女朋友去看个展",
]


@pytest.mark.parametrize("text", _NEW_REQUESTS)
def test_new_request_not_feedback(text: str) -> None:
    """R2.3：明确新需求不应被误判为反馈。"""
    assert looks_like_feedback(text) is False, f"{text!r} 不应被识别为反馈"


# R6.1：既有强信号反馈仍正常（防回归）
_STRONG_FEEDBACK = [
    "太远了，3公里以内",
    "换一家餐厅",
    "便宜点",
    "不要这么累",
    "一个小时以内",
]


@pytest.mark.parametrize("text", _STRONG_FEEDBACK)
def test_strong_feedback_still_works(text: str) -> None:
    """R6.1：既有强信号反馈不回归。"""
    assert looks_like_feedback(text) is True, f"{text!r} 强信号反馈应仍识别"


def test_empty_input_not_feedback() -> None:
    """空输入边界。"""
    assert looks_like_feedback("") is False
    assert looks_like_feedback("   ") is False
