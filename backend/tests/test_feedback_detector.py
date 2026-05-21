"""tests/test_feedback_detector.py —— 验证 looks_like_feedback 启发式覆盖。

测试目的：
- 关键词命中（"太远 / 不要 / 换" 等）
- 阿拉伯数字 + 单位（"3 公里 / 1.5 小时"）
- 中文数字 + 单位（"一个小时 / 半小时 / 三公里"）—— 旧版本漏覆盖
- 短句 + 「以内 / 以下 / 之内」—— 强反馈信号
- 边界：空字符串 / 不像反馈的新需求
"""

from __future__ import annotations

import pytest

from agent.feedback_detector import looks_like_feedback


# ============================================================
# 关键词命中
# ============================================================

@pytest.mark.parametrize(
    "txt",
    [
        "太远了",
        "近一点的",
        "换一个餐厅",
        "改成走路",
        "不要那么贵",
        "再想想",
        "缩短点时间",
        "提前一点出发",
    ],
)
def test_keyword_hits(txt: str) -> None:
    assert looks_like_feedback(txt) is True


# ============================================================
# 阿拉伯数字 + 单位
# ============================================================

@pytest.mark.parametrize(
    "txt",
    [
        "3 公里以内",
        "5km 之内",
        "1.5 小时",
        "30 分钟",
        "2千米",
    ],
)
def test_arabic_number_with_unit(txt: str) -> None:
    assert looks_like_feedback(txt) is True


# ============================================================
# 中文数字 + 单位（旧版本漏覆盖）
# ============================================================

@pytest.mark.parametrize(
    "txt",
    [
        "一个小时以内",
        "半小时",
        "三公里",
        "两小时",
        "五分钟",
        "一小时之内",
    ],
)
def test_chinese_number_with_unit(txt: str) -> None:
    assert looks_like_feedback(txt) is True, f"应识别中文数字+单位：{txt}"


# ============================================================
# 短输入 + 「以内/以下/之内」强信号
# ============================================================

@pytest.mark.parametrize(
    "txt",
    [
        "范围之内吧",
        "都以内",
        "尽量以下",
    ],
)
def test_short_within_hint(txt: str) -> None:
    assert looks_like_feedback(txt) is True


# ============================================================
# 反例：不像反馈
# ============================================================

@pytest.mark.parametrize(
    "txt",
    [
        "",
        "   ",
        "你好",
        "今天天气真好",
        "1+1=?",
    ],
)
def test_negative_cases(txt: str) -> None:
    assert looks_like_feedback(txt) is False


# ============================================================
# 边界：长输入含「以内」（不是短反馈，可能是新需求）
# ============================================================

def test_long_input_with_within_falls_back_to_other_signals() -> None:
    """长输入（>= 15 字）含「以内」但无关键词无数字单位 → 不算反馈。

    示例：「我想找个安静的地方静一静，不要太远的范围之内」
    含「之内」但是 22 字，不应只凭这一点判反馈。
    会被关键词「不要」命中（这测的是另一条规则）→ 命中。
    """
    # 仅含「以内/之内」，无关键词无数字单位（27 字，>= 15）：不命中
    txt = "我想找一个比较舒适放松的好地方走一走范围之内"
    assert looks_like_feedback(txt) is False

    # 但若含关键词「不要」，仍命中
    txt2 = "我想去近一点的地方，不要走太远，挺累的"
    assert looks_like_feedback(txt2) is True
