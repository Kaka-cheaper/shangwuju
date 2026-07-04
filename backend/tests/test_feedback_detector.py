"""feedback_detector 语义类反馈识别测试（spec feedback-routing-fix Task 1 / R2）。

验证扩充词典后：
- 7 个曾漏判的自然反馈措辞 → True（R2.2）
- 明确的新规划需求 → False（R2.3 不误伤）
- 既有强信号反馈 → 仍 True（R6.1 不回归）
"""

from __future__ import annotations

import pytest

from agent.core.feedback_detector import looks_like_feedback, looks_like_feedback_strong


# R2.2：曾经漏判的语义类反馈（无数字单位，靠语义表达）
_SEMANTIC_FEEDBACK = [
    "太赶了",
    "节奏太快",
    "想轻松点",
    "行程太满了",
    "能不能轻松些",
]


@pytest.mark.parametrize("text", _SEMANTIC_FEEDBACK)
def test_semantic_feedback_detected(text: str) -> None:
    """R2.2：语义类反馈措辞应被识别为 feedback。"""
    assert looks_like_feedback(text) is True, f"{text!r} 应被识别为反馈"


# ADR-0011 决策 2（E-1）：纯品味/评价词清洗——"优雅/不太好"等不指向任何可调参数
# （距离/价格/时长/节奏/时间），且codebase 里没有任何模块（如 refiner 的
# duration_hours 收缩，ADR-0014 G-0 迁移前是 pace_profile）依赖这两个词做
# 具体动作，纯语义品评，误吞新需求面大
# （"想要精致优雅一点的下午"这类新需求也会含"优雅"），职责移交 LLM（脑子）。
# 与仍保留的 SESSION_TOO_LONG 词族（太久/太长/盯不住/无聊/扛不住/腻了，见
# test_refiner_session_too_long.py 的同步契约）不同——那组词有 refiner.py 的
# 具体调参逻辑撑腰，这两个词没有，故删除而非保留。
_PURGED_TASTE_WORDS = [
    "再优雅一点",
    "这个不太好",
]


@pytest.mark.parametrize("text", _PURGED_TASTE_WORDS)
def test_purged_taste_words_not_feedback(text: str) -> None:
    """ADR-0011 E-1：纯品味/评价词已从词表删除，不应再被识别为 feedback。"""
    assert looks_like_feedback(text) is False, f"{text!r} 应已随词表清洗不被识别为反馈"


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


# ============================================================
# B1c 短词目碰撞审计（2026-07-04 路演前小修批）：强信号子集的"近点"碰撞
# ============================================================
# 设计契约：强信号子集是"命中即拍板路由、无兜底"的层，每条词目必须单独接近
# 百分百精度。"近点"嵌在"附近点评/附近点心"这类"附近＋点X"常用搭配里会假命中
# ——扫描前剔除"附近"字样（"附近"本身不携带反馈语义），真"近点"诉求不受影响。


def test_strong_subset_not_fooled_by_fujin_dian_collision() -> None:
    """"附近点评/附近点心"的"近点"子串碰撞不应触发强信号直接拍板。"""
    assert looks_like_feedback_strong("帮我看看附近点评好的") is False
    assert looks_like_feedback_strong("附近点心店那家不错") is False


def test_strong_subset_still_hits_real_jindian() -> None:
    """真正的"近点"诉求（不与"附近"粘连）仍是强信号。"""
    assert looks_like_feedback_strong("近点的") is True
    assert looks_like_feedback_strong("太远了，近点") is True
