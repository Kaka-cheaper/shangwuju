"""agent.feedback_detector —— 「这条输入是否像对已有方案的反馈」的统一启发式判定。

设计动机：
    历史上有两份完全相同的 _FEEDBACK_KEYWORDS（orchestrator.py + graph/nodes/router.py），
    维护重复且容易漏同步。本模块作为唯一来源（SoT），两个 caller 都来这里调。

判定条件：
    1. 关键词命中（"太远 / 不要 / 换 / 改 / 缩短" 等）
    2. 阿拉伯数字 + 时间/距离单位（"3 公里 / 1.5 小时"）
    3. 中文数字 + 时间/距离单位（"一个小时 / 半小时 / 三公里"）
    4. 强信号短语「N 以内 / 以下 / 之内」（短句 + 单位）

spec planning-quality-deep-review R8（Task 7）扩展，ADR-0014 G-0（2026-07-03）迁移：
    - 加 SESSION_TOO_LONG 关键词（"太久 / 太长 / 盯不住 / 无聊 / 扛不住 / 腻了"），让
      用户说"这段太长了"时也能被识别为反馈，触发下游 refiner 的 duration_hours 上界
      收缩（原目标是 pace_profile.single_session_max_min，该字段全系统无消费方已
      随 ADR-0014 G-0 砍除，收缩契约迁移到有真实消费的 duration_hours，见
      agent/intent/refiner.py 模块 docstring）。
      **这份词必须和 agent/intent/refiner.py 的 _KEYWORDS_SESSION_TOO_LONG 保持同步**
      （test_refiner_session_too_long.py::test_feedback_detector_recognizes_session_
      too_long 显式钉死这条同步契约）——否则用户说"盯不住了"会连 feedback 都进不去，
      refiner 那份更宽的规则地板永远够不着。这 4 个词看似"情绪词"，实则**指向具体
      可调参数**（duration_hours 上界缩 30%），与下面 ADR-0011
      真正删除的"纯品味评价词"（无对应调参逻辑）不是一类，不可一并删除。

ADR-0011 决策 2（E-1）清洗（只删"无任何下游调参逻辑"的纯品味/评价词）：
    - 删「一般/普通/优雅/高级/没意思/不太好/更高级」——不指向任何可调参数（距离/
      价格/时长/节奏/时间），也没有任何模块依赖这些词做具体调整，纯语义品评，
      误吞新需求面大，语义判断职责移交 LLM（脑子）。
    - 「无聊/腻了/扛不住/盯不住」**不删**（见上条 R8 同步契约——先读码核实到
      test_refiner_session_too_long.py 的显式回归断言，才发现这 4 个词并非
      "纯品味词"，是任务书原始分拣清单的一处偏差，此处以代码里的测试契约为准）。
    - 字面 + 数字/单位信号保留（判据：指向具体可调参数的留，纯语义品评的删）。

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
    "便宜", "贵", "再贵点",
    # 「修改/调整」动词
    "改成", "改为", "调到", "缩短", "延长", "再短", "再长",
    # 「时间」类
    "时间", "早点", "晚点", "提前", "推迟",
    # 「以内/以下/之内」（强信号但需配合单位，由正则补充）
    # spec planning-quality-deep-review R8（Task 7）：单段时长抱怨——4 词与
    # refiner.py 的 _KEYWORDS_SESSION_TOO_LONG 显式同步（见上方模块 docstring），
    # ADR-0011 清洗**不动**这组（它们指向具体可调参数 duration_hours 上界，
    # 见 ADR-0014 G-0 迁移说明，非纯品味词）。
    "太久", "太长", "盯不住", "无聊", "扛不住", "腻了",
    # ============================================================
    # spec feedback-routing-fix R2：语义类反馈（无数字单位，靠语义表达）
    # 这些是用户对方案的口语化反馈，曾被漏判 → 当作新需求重规划（反馈无用 bug）
    # ============================================================
    # 节奏 / 强度类（指向具体可调参数：日程密度；交由 LLM 路径判断具体怎么调，
    # 不像 SESSION_TOO_LONG 那组有 _rule_fallback 的确定性关键词→字段映射）
    "节奏", "太赶", "赶", "轻松", "悠闲", "慢一点", "慢点", "紧凑", "太满", "太累",
    # ADR-0011 决策 2（E-1）：纯品味/情绪评价词已删——"一般/普通/优雅/高级/
    # 没意思/不太好/更高级"不指向任何可调参数，纯语义品评，误吞新需求面大
    # （"一般般的心情""随便逛逛"类新请求也会含这些字），职责移交 LLM（脑子）。
    # 逐词分拣见 backend/tests/test_feedback_detector.py 同步调整。
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
        本函数是**高召回粗筛**——含「换 / 改」等弱信号词也返 True，但这些词
        在「换成和朋友打球」这类新需求里也会出现。caller 必须结合 state.itinerary
        是否存在一起判断（无 itinerary 即不可能是反馈）。
        需要「不会误吞新需求」的强信号子集时，用 looks_like_feedback_strong()。
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


# ============================================================
# 强信号子集（spec feedback-routing-fix R4）
# ============================================================
# 这些词 / 模式几乎不可能出现在「全新需求」的开头，命中即可直接判 feedback，
# 不必再走 LLM。区别于全集里的弱信号词（"换 / 改 / 时间"——这些在
# "换成和朋友打球" / "改成看电影" 这类新需求里也出现，必须交 LLM 区分）。

# spec dialogue-act-routing C1：强信号子集只保留「几乎只可能指向方案」的词。
# 移除 8 个歧义词——它们高频出现在身体/情绪/口味/闲聊里，给"直接拍板"的特权会误吞：
#   太累(我太累了) 腻了(吃腻了) 节奏(喜欢慢节奏) 不太好(膝盖不太好) 不喜欢(不喜欢吃辣)
#   不太行(我不太行=身体) 不合适(时间不合适) 没意思(情绪)
# 它们仍留在全集 _FEEDBACK_KEYWORDS（高召回粗筛、不直接拍板路由），由带上下文的 L2 LLM 判。
#
# 「盯不住」「扛不住」ADR-0011 清洗时曾按"纯情绪词"考虑删除，读码核实到
# test_refiner_session_too_long.py::test_feedback_detector_recognizes_session_too_long
# 显式钉死它们必须被 looks_like_feedback 识别（spec R8 同步契约，见模块 docstring）
# 后撤回：这两词指向具体可调参数（duration_hours 上界，见 ADR-0014 G-0 迁移
# 说明），不是纯品味词，予以保留。
_STRONG_FEEDBACK_KEYWORDS: tuple[str, ...] = (
    # 距离类（明确指向"上一轮太远了"）
    "太远", "近一点", "近点", "别走太远", "别太远", "再近",
    "公里以内", "km以内", "公里内", "km内", "公里之内",
    # 节奏 / 强度类（明确指向"上一轮安排太满 / 太长"）
    "太赶", "太满", "太久", "太长", "盯不住", "扛不住", "紧凑",
    # 价格类
    "太贵", "便宜点",
)


def looks_like_feedback_strong(message: str) -> bool:
    """强信号反馈判定（spec feedback-routing-fix R4）。

    仅命中「几乎不可能是新需求开头」的强信号词 / 数字单位模式时返 True，
    供 router_node Layer 1 用——命中即直接判 feedback 不调 LLM，且不会误吞
    「换成和朋友打球」这类含弱信号词的新需求（弱信号交 LLM）。
    """
    if not message:
        return False
    txt = message.strip()
    if not txt:
        return False

    # 强信号关键词
    for kw in _STRONG_FEEDBACK_KEYWORDS:
        if kw in txt:
            return True

    # 数字 + 单位（"3 公里" / "1.5 小时"——明确的量化调整）
    for pat in _PATTERNS:
        if pat.search(txt):
            return True

    # 短输入 + 「以内/以下/之内」
    if len(txt) < 15:
        for hint in _WITHIN_HINTS:
            if hint in txt:
                return True

    return False


__all__ = ["looks_like_feedback", "looks_like_feedback_strong"]
