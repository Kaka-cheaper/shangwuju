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

【对话轮路由规则层重构（2026-07-12）：`looks_like_feedback_strong` 的覆盖度闸】
    本模块的高召回粗筛 `looks_like_feedback` **不受本批影响**——它喂着
    `agent/intent/refiner.py` 内部的 `is_scenario` 判别逻辑，属于硬护栏范围
    （改动会砸 refiner 内部行为，不在本次任务书范围内）。只有 `_strong` 版
    （供 route_turn.py Layer 1 拍板用）套用了 `agent.core.coverage_gate` 的
    per-rule 覆盖度闸——见该函数与 `_feedback_strong_anchors` 的说明。
"""

from __future__ import annotations

import re

from .coverage_gate import covers

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
    # 安全级硬约束类（点火前小修批 任务 2，K11 探针实锤）：过敏指向具体可调
    # 参数（dietary_constraints 排除项），非品味词；词目审查见强信号子集处。
    "过敏",
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
    # 安全级硬约束类（点火前小修批 任务 2；K11 探针实锤：房间成员说「我海鲜
    # 过敏」时 LLM 挂掉→静默落闲聊地板=丢安全约束）。
    # 【词目审查（9eecef0 精度契约：能想象日常语境里词目出现但不是对方案提
    #  约束，就剪；本词目逐语境推演后收录）】
    #  ① 问句形「大家有没有海鲜过敏的？」「这家有过敏原标注吗？」——覆盖度闸
    #    收口后自动弃权（"这家有过敏原标注吗？"剥掉锚点"过敏"后残余非空），
    #    不再单独依赖 route_turn 的 B2 问句尾护栏（该护栏仍在，是另一层防御，
    #    两者不冲突；互动核查见 test_allergy_question_guard_interplay）；
    #  ② 否定形「不过敏/没过敏/不会过敏」——覆盖度闸收口后同样自动弃权（残余
    #    非空），不再需要专门的 _STRONG_SCAN_NOISE 剔噪表；
    #  ③ 比喻用法「对人多的地方过敏」「对加班过敏」——字面非医学过敏，但在
    #    "有方案在场"（Layer 1 前提 has_itinerary）的语境下它就是对方案的
    #    回避型反馈，判 feedback 送 refiner 语义不错位；
    #  ④ 闲聊追述「我昨天过敏了」——本产品面（半日出行规划对话）出现率极低，
    #    且误判代价=多一轮保留约束的重排；漏判代价=硬约束静默丢失（文档 7.7
    #    安全约束优先于多数偏好）。代价不对称 + ①②在覆盖度闸下天然成立，
    #    按安全级硬约束收录。mock_data 全量 grep 无含「过敏」的实体名，无
    #    实体名碰撞面。
    #
    #  【覆盖度闸收口后的新推论（2026-07-12，如实记录）】"过敏"锚点本身只是
    #    两个字，任何完整点名过敏原的句子（"我海鲜过敏"/"他花生过敏"）残余
    #    都非空（"海鲜"/"花生"是过敏原名词，覆盖度闸判据里明确的"残余实义"，
    #    不进冻结填充集），覆盖度闸下会弃权到脑子，不再是 Layer 1 确定性
    #    拍板——K11 探针（scripts/smoke_final_llm.py）此前钉住的"过敏句 stub
    #    模式下 Layer 1 确定性触发"基线因此受影响，是本批引入覆盖度闸的
    #    结构性推论。是否要给"过敏"开例外通道（不经覆盖度闸、只认裸"过敏"
    #    子串）是产品语义判断，已 FLAG 给主代理，未擅自决定。
    "过敏",
)

# ============================================================
# 对话轮路由规则层重构（2026-07-12）：覆盖度闸收口
# ============================================================
# 原 `_STRONG_SCAN_NOISE`（"附近"防"近点"假命中、"不过敏/没过敏/不会过敏"
# 否定形剔噪）是逐词补丁式的排除机制——每发现一个新碰撞就往这个集合里加一个
# 词，边界永远说不清、下一个碰撞还得再加。现全部收口成
# `agent.core.coverage_gate.covers`：命中的锚点词（+ 数字单位正则命中的完整
# 片段）与冻结填充集是否覆盖整句，取代"剔除已知噪声词再扫描"。
#
# 「附近点评」类假命中：锚点词表里的"近点"作为子串确实出现在"附近点评"里，
# 但覆盖度闸算残余时，"附近点评"整句除了被命中的"近点"两个字，还剩"附评"
# （"点"被"近点"锚点吃掉，"评"留下）——非空残余，自动弃权，不需要专门列出
# "附近"作为噪声词。
#
# 「过敏」否定形/问句形假命中：这些场景（"我不过敏""这家有过敏原标注吗？"）
# 剥掉锚点"过敏"后残余"我不"/"这家有过敏原标注吗"均非空，同样自动弃权，
# 不需要专门列出"不过敏/没过敏/不会过敏"。
#
# 副作用（如实记录，不回避）：覆盖度闸对"过敏"最直接的推论是——**任何点名
# 具体过敏原的完整句子**（"我海鲜过敏"/"他花生过敏"）残余也非空（"海鲜"/
# "花生"是覆盖度闸判据里明确的"残余实义"，不是填充词），同样会弃权到脑子，
# 不再是 Layer 1 的确定性拍板。这与 K11 探针（`scripts/smoke_final_llm.py`）
# 此前钉住的"过敏句 stub 模式下 Layer 1 确定性触发"基线冲突——是本次重构
# 应用覆盖度闸这一决策的直接结构性推论，不是实现疏漏。是否要为"过敏"这类
# 安全级硬约束词单独开一个不经覆盖度闸的例外通道，属于产品语义判断（放宽会
# 削弱覆盖度闸"残余非空即弃权"的统一纪律；不放宽则 K11 在 --stub/LLM 挂掉
# 场景下的安全网从"确定性"降级为"依赖脑子可用"），已 FLAG 给主代理裁决，
# 未擅自选择任何一边（见改动日志）。


def _feedback_strong_anchors(text: str) -> tuple[str, ...]:
    """强信号规则自己的锚点词表命中——含关键词 + 数字单位正则命中片段。

    数字单位正则命中的片段若紧邻「以内/以下/之内」，把该后缀并入同一个锚点
    （"3公里以内"是一个语义整体，不是"3公里"信号 + "以内"残余两回事）——这是
    扩大本规则锚点识别范围，不是往共享填充集里加词，遵循 coverage_gate.py
    的铁律边界。
    """
    anchors: list[str] = [kw for kw in _STRONG_FEEDBACK_KEYWORDS if kw in text]
    within_already_covered = False
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            span = m.group(0)
            end = m.end()
            extended = span
            for hint in _WITHIN_HINTS:
                if text[end:end + len(hint)] == hint:
                    extended = span + hint
                    within_already_covered = True
                    break
            anchors.append(extended)
    # 「N 以内/以下/之内」也可能独立于数字单位正则出现（如中文数字未命中
    # _CN_DIGITS 但仍有限定词字面）——短输入场景下单独收作锚点，与
    # looks_like_feedback 的判据保持信号来源一致（见该函数判据 3）。已被数字
    # 单位正则吸收进 extended 锚点的「以内/以下/之内」不再重复添加（避免同一个
    # "以内" 子串既在扩展锚点里、又单独作为锚点，重复 replace 不会出错但没
    # 必要）。
    if len(text) < 15 and not within_already_covered:
        for hint in _WITHIN_HINTS:
            if hint in text:
                anchors.append(hint)
    return tuple(anchors)


def looks_like_feedback_strong(message: str) -> bool:
    """强信号反馈判定（spec feedback-routing-fix R4，覆盖度闸收口）。

    仅当锚点词（关键词 / 数字单位片段 / 以内类限定词）与冻结填充集覆盖了
    整句——残余为空——才返回 True，供 router_node Layer 1 用：命中即直接判
    feedback 不调 LLM。残余非空（句子里还有锚点词表读不懂的实义内容，如点名
    的过敏原名词、疑问语气、否定前缀）一律弃权，交回级联继续往下判（通常
    落到脑子）。不会误吞「换成和朋友打球」这类含弱信号词的新需求——弱信号词
    根本不在本函数的锚点词表里。
    """
    if not message:
        return False
    txt = message.strip()
    if not txt:
        return False

    anchors = _feedback_strong_anchors(txt)
    if not anchors:
        return False
    return covers(txt, anchors)


__all__ = ["looks_like_feedback", "looks_like_feedback_strong"]
