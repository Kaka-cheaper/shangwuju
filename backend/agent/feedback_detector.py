"""agent.feedback_detector —— 「这条输入是否像对已有方案的反馈」的统一启发式判定。

设计动机：
    历史上有两份完全相同的 _FEEDBACK_KEYWORDS（orchestrator.py + graph/nodes/router.py），
    维护重复且容易漏同步。本模块作为唯一来源（SoT），两个 caller 都来这里调。

判定条件：
    1. 关键词命中（"太远 / 不要 / 换 / 改 / 缩短" 等）
    2. 阿拉伯数字 + 时间/距离单位（"3 公里 / 1.5 小时"）
    3. 中文数字 + 时间/距离单位（"一个小时 / 半小时 / 三公里"）
    4. 强信号短语「N 以内 / 以下 / 之内」（短句 + 单位）

边界（误判风险）：
    - 新需求里也可能含「不要太累」"我想去 1 公里以内的地方"——caller 必须结合
      上一轮 itinerary 是否存在一起判断（无 itinerary 即不可能是反馈）

不负责：
    - 是否真的走 feedback 路径（caller 在拿到本函数 True 后还要验 itinerary 存在）
    - LLM router 二次确认（在 agent/router.py classify_input 里）
"""

from __future__ import annotations

import re

# ============================================================
# 关键词列表（合并两处旧 _FEEDBACK_KEYWORDS）
# ============================================================

_FEEDBACK_KEYWORDS: tuple[str, ...] = (
    # 「距离/位置」类
    "太远", "近一点", "近点", "别走太远", "别太远", "再近",
    "公里以内", "km以内", "公里内", "km内", "公里之内",
    # 「拒绝/替换」类
    "不要", "去掉", "换一个", "换", "改一下", "再想想",
    "不喜欢", "不太行", "不行", "不合适",
    # 「价格」类
    "便宜", "贵", "再贵点", "更高级",
    # 「修改/调整」动词
    "改成", "改为", "调到", "缩短", "延长", "再短", "再长",
    # 「时间」类
    "时间", "早点", "晚点", "提前", "推迟",
    # 「以内/以下/之内」（强信号但需配合单位，由正则补充）
)

# ============================================================
# 中文数字 + 单位正则（覆盖「一个小时 / 半小时 / 三公里」等启发式漏掉的纯调整指令）
# ============================================================

# 中文数字（含「半 / 两」，覆盖口语表达）
_CN_DIGITS = r"[一二两三四五六七八九十半]"

# 时间单位
_TIME_UNITS = r"(?:小时|h|分钟|min)"
# 距离单位
_DISTANCE_UNITS = r"(?:公里|km|千米|米|m)"

# 阿拉伯数字（覆盖原有 \d+ 兼容）
_ARABIC_NUM = r"\d+(?:\.\d+)?"

# 完整匹配模式：
#   1. 阿拉伯数字 + 单位                  e.g. "3 公里"、"1.5 小时"
#   2. 中文数字 + (个)? + 单位             e.g. "一个小时"、"三公里"、"半小时"
#   3. 「N 以内 / 以下 / 之内」（数字 + 单位 + 限定词，强反馈信号）
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"{_ARABIC_NUM}\s*{_TIME_UNITS}", re.IGNORECASE),
    re.compile(rf"{_ARABIC_NUM}\s*{_DISTANCE_UNITS}", re.IGNORECASE),
    re.compile(rf"{_CN_DIGITS}\s*个?\s*{_TIME_UNITS}", re.IGNORECASE),
    re.compile(rf"{_CN_DIGITS}\s*{_DISTANCE_UNITS}", re.IGNORECASE),
    # 「N 以内/以下/之内」单独触发（可不带数字单位，但通常与上面重叠）
    # 短输入（<15 字）+ 含「以内/以下/之内」 → 强反馈意图
)

# 「以内/以下/之内」短句强信号（短输入时强烈倾向反馈）
_WITHIN_HINTS: tuple[str, ...] = ("以内", "以下", "之内")


def looks_like_feedback(message: str) -> bool:
    """轻量判断这条消息是不是「对已有方案的反馈」。

    判据（任一命中即返 True）：
        1. 含反馈关键词（"太远 / 不要 / 换" 等）
        2. 含阿拉伯/中文数字 + 时间/距离单位
        3. 短输入（<15 字）+ 「以内 / 以下 / 之内」

    Note:
        本函数不读 conversation state——caller 必须结合 state.itinerary
        是否存在一起判断（无 itinerary 即不可能是反馈，所有 True 都应忽略）。
    """
    if not message:
        return False
    txt = message.strip()
    if not txt:
        return False

    # 1. 关键词命中
    for kw in _FEEDBACK_KEYWORDS:
        if kw in txt:
            return True

    # 2. 数字 + 单位正则
    for pat in _PATTERNS:
        if pat.search(txt):
            return True

    # 3. 短输入 + 「以内/以下/之内」
    if len(txt) < 15:
        for hint in _WITHIN_HINTS:
            if hint in txt:
                return True

    return False


__all__ = ["looks_like_feedback"]
