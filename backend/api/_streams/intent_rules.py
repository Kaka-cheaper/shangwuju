"""rule 模式纯算法意图解析（spec speed-constraints · 加分项命中）。

设计动机：
- rule 模式应该真的是「纯算法所有环节」，不调任何 LLM
- 真链路下 _intent_via_llm 调 LLM 抽 18 个字段约耗时 3-8s
- 关键词词典 + 正则在毫秒级完成 80% 高频输入的字段抽取
- 不命中字段用兜底默认值；schema 校验保证产出仍合法

抽取覆盖（按命中信号强度排序）：
1. distance_max_km：正则「数字 + 公里/km/千米」+ 「太远了 / 近一点」启发式
2. duration_hours：正则「数字 + 小时 / 几小时」+ 「半天 / 一下午」预设
3. companions：关键词字典「老婆 / 孩子 + 数字岁 / 朋友 N 人 / 父母 / 闺蜜 / 客户 / 一个人」
4. social_context：9 选 1 用关键词信号（与 _stub_route 信号一致）
5. dietary_constraints / physical_constraints / experience_tags：词典直接命中
6. capacity_requirement：companions 推导（≥4 人时设值）
7. start_time：关键词「下午 / 晚上 / 周末 / 周日」拼成 today_afternoon 等
8. raw_input + parse_confidence + ambiguous_fields：从抽取过程结果生成

不调 LLM；不抛异常；任何场景都返回合法 IntentExtraction。
"""

from __future__ import annotations

import re

from schemas.intent import Companion, IntentExtraction
from schemas.tags import (
    DIETARY_TAGS,
    EXPERIENCE_TAGS,
    PHYSICAL_TAGS,
    SOCIAL_CONTEXTS,
)


# ============================================================
# 距离抽取
# ============================================================

_DISTANCE_REGEX = re.compile(r"(\d+(?:\.\d+)?)\s*(公里|km|千米)", re.IGNORECASE)
_DISTANCE_NEAR_KEYWORDS = ("太远", "近一点", "近点", "别走太远", "别太远", "近的")


def _extract_distance_max_km(text: str) -> float:
    """抽距离上限。

    1. 显式数字「3 公里」「5km」→ 直接用
    2. 启发式「太远了 / 近一点」→ 默认 5 × 0.6 = 3km
    3. 不命中 → 默认 5km
    """
    m = _DISTANCE_REGEX.search(text)
    if m:
        try:
            v = float(m.group(1))
            return max(0.5, min(v, 50.0))
        except ValueError:
            pass

    if any(kw in text for kw in _DISTANCE_NEAR_KEYWORDS):
        return 3.0

    return 5.0


# ============================================================
# 时长抽取
# ============================================================

_DURATION_REGEX = re.compile(r"(\d+(?:\.\d+)?)\s*(小时|h|hour)", re.IGNORECASE)
_DURATION_KEYWORDS = {
    "半天": (3, 5),
    "一下午": (3, 5),
    "整个下午": (4, 6),
    "一晚上": (3, 5),
    "整晚": (3, 5),
    "几小时": (3, 5),
    "几个小时": (3, 5),
}


def _extract_duration_hours(text: str) -> list[int]:
    """抽时长 [min, max]。

    1. 显式「3 小时」→ [3, 4]
    2. 关键词「半天 / 一下午」→ 预设
    3. 默认 [3, 5]
    """
    m = _DURATION_REGEX.search(text)
    if m:
        try:
            v = int(float(m.group(1)))
            return [max(1, v), max(2, v + 1)]
        except ValueError:
            pass

    for kw, dur in _DURATION_KEYWORDS.items():
        if kw in text:
            return list(dur)

    return [3, 5]


# ============================================================
# 同行人抽取
# ============================================================

# 角色关键词 → role 名 + 默认 count（年龄从文本里再抽）
_ROLE_KEYWORDS: dict[str, str] = {
    "老婆": "妻子",
    "妻子": "妻子",
    "媳妇": "妻子",
    "孩子": "孩子",
    "宝宝": "孩子",
    "宝贝": "孩子",
    "儿子": "孩子",
    "女儿": "孩子",
    "娃": "孩子",
    "外公": "外公",
    "外婆": "外婆",
    "爷爷": "爷爷",
    "奶奶": "奶奶",
    "爸爸": "父亲",
    "妈妈": "母亲",
    "父亲": "父亲",
    "母亲": "母亲",
    "父母": "父母",
    "客户": "客户",
    "同事": "同事",
    "闺蜜": "闺蜜",
    "女朋友": "女朋友",
    "男朋友": "男朋友",
    "对象": "伴侣",
    "室友": "室友",
    "同学": "同学",
    "兄弟": "朋友",
    "哥们": "朋友",
}

_ALONE_KEYWORDS = ("一个人", "独自", "自己", "我一个", "独处", "一人")
_FRIEND_REGEX = re.compile(r"(\d+)\s*(个人|人|位)\s*(朋友)?")
_AGE_REGEX = re.compile(r"(\d+)\s*岁")


def _extract_companions(text: str) -> tuple[list[Companion], bool]:
    """抽同行人列表。返回 (companions, is_alone_explicit)。

    is_alone_explicit=True 表示用户明确说「一个人 / 独自」→ companions=[]
    """
    # 独处显式信号
    if any(kw in text for kw in _ALONE_KEYWORDS):
        return [], True

    out: list[Companion] = []
    seen_roles: set[str] = set()

    # 抽特定角色
    for kw, role in _ROLE_KEYWORDS.items():
        if kw in text and role not in seen_roles:
            seen_roles.add(role)
            # 角色含「孩子」类的尝试抽年龄
            if role in ("孩子",):
                age_m = _AGE_REGEX.search(text)
                age = int(age_m.group(1)) if age_m else None
                out.append(Companion(role=role, count=1, age=age))
            else:
                out.append(Companion(role=role, count=1))

    # 抽数字 + 朋友 / 人（朋友 4 个）
    fm = _FRIEND_REGEX.search(text)
    if fm and "朋友" not in seen_roles and "孩子" not in seen_roles:
        try:
            n = int(fm.group(1))
            if 2 <= n <= 20:
                out.append(Companion(role="朋友", count=n))
                seen_roles.add("朋友")
        except ValueError:
            pass

    return out, False


# ============================================================
# social_context 抽取（9 选 1）
# ============================================================

# 关键词信号矩阵（按优先级）
_SOCIAL_CONTEXT_SIGNALS: list[tuple[tuple[str, ...], str]] = [
    (("纪念日", "生日", "周年", "庆生"), "纪念日仪式感"),
    (("商务", "客户", "外地客户", "接待"), "商务接待"),
    (("外公", "外婆", "爷爷", "奶奶", "腿不好", "适合老人"), "老人伴助"),
    (("闺蜜", "下午茶", "拍照"), "闺蜜聊天"),
    (("情侣", "女朋友", "男朋友", "对象", "看展"), "情侣亲密"),
    (("一个人", "独自", "独处", "放空", "安静待"), "独处放空"),
    (("同学", "重聚", "同学聚会"), "同学重聚"),
    (("朋友", "兄弟", "哥们", "撸串", "夜宵", "热闹", "K 歌", "ktv"), "朋友热闹"),
    (("老婆", "妻子", "孩子", "宝贝", "娃", "全家"), "家庭日常"),
]


def _extract_social_context(text: str, companions: list[Companion]) -> str:
    """从文本 + companions 推 social_context。

    关键词信号优先；都不命中按 companions 兜底。
    """
    text_l = text.lower()
    for keywords, ctx in _SOCIAL_CONTEXT_SIGNALS:
        for kw in keywords:
            if kw in text_l or kw in text:
                return ctx

    # companions 兜底
    if not companions:
        return "独处放空"
    roles = {c.role for c in companions}
    if {"妻子", "孩子"} & roles:
        return "家庭日常"
    if {"外公", "外婆", "爷爷", "奶奶"} & roles:
        return "老人伴助"
    if {"客户"} & roles:
        return "商务接待"
    if {"闺蜜"} & roles:
        return "闺蜜聊天"
    if {"女朋友", "男朋友", "伴侣"} & roles:
        return "情侣亲密"

    return "家庭日常"


# ============================================================
# 三类 tag 抽取（直接词典命中）
# ============================================================

# 同义词 → 词典内规范值
_DIETARY_SYNONYMS: dict[str, str] = {
    "减肥": "低脂",
    "清淡": "不辣",
    "健康": "健康轻食",
    "减脂": "低脂",
    "包间": "有包间",
    "贵": "高人均",
    "高端": "高人均",
    "儿童餐": "有儿童餐",
}

_PHYSICAL_SYNONYMS: dict[str, str] = {
    "孩子": "亲子友好",
    "宝贝": "亲子友好",
    "宝宝": "亲子友好",
    "娃": "亲子友好",
    "腿不好": "无台阶",
    "走不动": "无台阶",
    "老人": "适合老人",
    "外公": "适合老人",
    "外婆": "适合老人",
    "爷爷": "适合老人",
    "奶奶": "适合老人",
}

_EXPERIENCE_SYNONYMS: dict[str, str] = {
    "拍照": "拍照友好",
    "网红": "网红打卡",
    "安静": "安静聊天",
    "热闹": "热闹",
    "撸串": "热闹",
    "夜宵": "热闹",
    "ktv": "社交",
    "K 歌": "社交",
    "k歌": "社交",
    "看展": "看展",
    "展览": "看展",
    "美术馆": "看展",
    "独处": "独处舒缓",
    "放空": "独处舒缓",
    "商务": "商务体面",
    "礼仪": "礼仪感",
    "情侣": "亲密情侣",
    "户外": "户外",
    "室内": "室内",
}


def _extract_tags_via_dict(
    text: str,
    synonyms: dict[str, str],
    valid: frozenset[str],
) -> list[str]:
    """从 synonyms 命中后映射到词典；保证产出全在 valid 集合内、去重保序。"""
    out: list[str] = []
    seen: set[str] = set()
    for kw, tag in synonyms.items():
        if kw not in valid:
            # 容错：synonyms 写错了
            pass
        if (kw in text or (kw.lower() in text.lower())) and tag in valid and tag not in seen:
            out.append(tag)
            seen.add(tag)
    return out


def _extract_dietary_constraints(text: str) -> list[str]:
    out = _extract_tags_via_dict(text, _DIETARY_SYNONYMS, DIETARY_TAGS)
    # 直接词典命中（不走 synonyms 也覆盖）
    for tag in DIETARY_TAGS:
        if tag in text and tag not in out:
            out.append(tag)
    return out


def _extract_physical_constraints(text: str) -> list[str]:
    out = _extract_tags_via_dict(text, _PHYSICAL_SYNONYMS, PHYSICAL_TAGS)
    for tag in PHYSICAL_TAGS:
        if tag in text and tag not in out:
            out.append(tag)
    return out


def _extract_experience_tags(text: str) -> list[str]:
    out = _extract_tags_via_dict(text, _EXPERIENCE_SYNONYMS, EXPERIENCE_TAGS)
    for tag in EXPERIENCE_TAGS:
        if tag in text and tag not in out:
            out.append(tag)
    return out


# ============================================================
# start_time 抽取
# ============================================================

_TIME_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("周日", "礼拜日", "礼拜天", "星期日", "星期天"), "sunday_afternoon"),
    (("周六", "礼拜六", "星期六"), "saturday_afternoon"),
    (("明天",), "tomorrow_afternoon"),
    (("晚上", "夜里", "今晚"), "today_evening"),
]


def _extract_start_time(text: str) -> str:
    for keywords, value in _TIME_KEYWORDS:
        for kw in keywords:
            if kw in text:
                return value
    return "today_afternoon"


# ============================================================
# 主入口
# ============================================================


def parse_intent_via_rules(message: str, *, user_id: str | None = None) -> IntentExtraction:
    """rule 模式纯算法意图解析。

    Args:
        message: 用户原始输入文本
        user_id: 当前 demo 不影响抽取，保留接口与 _intent_via_llm 一致

    Returns:
        合法 IntentExtraction（schema 校验通过）

    覆盖说明：
    - 80% 高频输入字段抽全（距离 / 时长 / 同行人 / social_context / 三类 tag）
    - 复杂语义（多代际 + 婴幼儿 + 高人均 + 偏好排斥）由 llm 模式接管
    - 抽不出的字段用兜底默认值；ambiguous_fields 标记不确定的
    """
    text = (message or "").strip()
    if not text:
        # 空输入：兜底家庭场景
        return IntentExtraction(
            start_time="today_afternoon",
            duration_hours=[3, 5],
            distance_max_km=5.0,
            companions=[],
            physical_constraints=[],
            dietary_constraints=[],
            experience_tags=[],
            social_context="独处放空",
            raw_input=message or "",
            parse_confidence=0.3,
            ambiguous_fields=["empty_input_fallback"],
        )

    # ---- 字段抽取 ----
    distance = _extract_distance_max_km(text)
    duration = _extract_duration_hours(text)
    companions, is_alone = _extract_companions(text)
    social_ctx = _extract_social_context(text, companions)
    dietary = _extract_dietary_constraints(text)
    physical = _extract_physical_constraints(text)
    experience = _extract_experience_tags(text)
    start_time = _extract_start_time(text)

    # ---- 标记不确定字段（让下游 critic 知道哪里弱）----
    ambiguous: list[str] = []
    if not companions and not is_alone:
        ambiguous.append("companions")  # 没显式抽到同行人但也没说「一个人」
    if not (dietary or physical or experience):
        ambiguous.append("tags")  # 三类 tag 都没命中

    # ---- capacity_requirement（4 人以上聚会推导）----
    capacity: int | None = None
    total_people = sum(c.count for c in companions) + 1  # +1 = 自己
    if total_people >= 4 or (companions and any(c.count >= 3 for c in companions)):
        capacity = total_people

    # ---- confidence：按命中信号丰富程度估 ----
    signals = (
        bool(_DISTANCE_REGEX.search(text)),
        bool(companions or is_alone),
        bool(dietary or physical or experience),
        bool(_DURATION_REGEX.search(text)),
    )
    confidence = 0.5 + 0.1 * sum(signals)  # 0.5 - 0.9 区间

    return IntentExtraction(
        start_time=start_time,
        duration_hours=duration,
        distance_max_km=distance,
        companions=companions,
        physical_constraints=physical,
        dietary_constraints=dietary,
        experience_tags=experience,
        social_context=social_ctx,  # type: ignore[arg-type]
        capacity_requirement=capacity,
        raw_input=text,
        parse_confidence=round(confidence, 2),
        ambiguous_fields=ambiguous,
    )
