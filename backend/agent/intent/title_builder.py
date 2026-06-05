"""agent.intent.title_builder —— 行程卡片小红书风格大标题（itinerary.summary）。

行程卡片大标题（前端 ItineraryCard 透传 itinerary.summary）从旧的
「半日方案 · 单个POI（约X小时）」改成一句话小红书风格标题——**必须概括所有主要站点**
（旧 bug：只取停留最久的单站，漏掉其它站如烧烤）。

三层兜底中本模块负责「规则版」标题（无 LLM 也能出信息全的口语标题）：
- 规则兜底（narrator._template_narration）：LLM 未配 / stub / 解析不出 title 时
- 最底层 summary（assemble_blueprint._build_summary / rule_planner）：narrate 未覆盖时的保底

规格（与 LLM prompt 约束一致，规则版尽量贴近）：
- 一句话，约 8-22 字，简短有钩子
- 覆盖**所有主要站点**（用餐 + 活动，如「烧烤 + KTV」都要体现）
- 体现同行关系（室友 / 家人 / 闺蜜 / 朋友 / 独自）和/或时长氛围
- 口语化、有场景感
- **不要**「半日方案 ·」前缀、**不要**「（约X小时）」括号

设计纪律：
- 纯函数、零 LLM 依赖；输入是已归一化的「站点短语 + 同行短语 + 时长」原料
- 与 narrator._format_companions 解耦（companion → 短语在调用方做，本模块只拼标题）
- 站点短语用「+」连接（小红书味），与 narration 的「→」时间序叙述区分开
"""

from __future__ import annotations

from typing import Optional


# 把节点动作动词化：用活动/餐饮关键词 → 更有画面感的动词短语。
# 命中即替换为口语动作；未命中保留站点名本身（如「悦读亲子绘本馆」）。
# 顺序敏感：更具体的词放前面（「烤肉」先于「烤」）。
_ACTIVITY_VERB_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("烧烤", "烤肉", "撸串", "串"), "撸串"),
    (("KTV", "ktv", "唱K", "唱k", "K歌", "k歌", "量贩"), "唱K"),
    (("火锅",), "涮火锅"),
    (("猫咖", "猫舍"), "撸猫"),
    (("剧本杀",), "玩剧本杀"),
    (("密室",), "玩密室"),
    (("电影", "影院", "IMAX", "imax"), "看电影"),
    (("展", "美术馆", "博物馆", "艺术馆"), "看展"),
    (("书", "阅读", "绘本"), "看书"),
    (("茶", "茶院", "茶空间"), "喝茶"),
    (("咖啡",), "喝咖啡"),
    (("酒吧", "清吧", "小酒馆"), "小酌"),
    (("攀岩",), "攀岩"),
    (("游泳", "泳池"), "游泳"),
    (("温泉",), "泡温泉"),
    (("甜品", "蛋糕", "下午茶"), "吃下午茶"),
)


def _short_title(raw: str) -> str:
    """去掉「亲子游玩 · 森林儿童探索乐园」里的前缀分类标签，取最后一段实体名。"""
    t = (raw or "").strip()
    if " · " in t:
        t = t.split(" · ")[-1].strip()
    return t


def companions_to_title_phrase(companions: list) -> str:
    """同行人列表 → 标题用同行短语（简洁口语，不带人数前缀的「位/个」）。

    标题要短，不像 narration 那样写「4 位室友」；这里只取关系词：
        [室友×4]            → "和室友"
        [老婆, 孩子5]       → "和老婆孩子"
        [朋友×3]            → "和朋友"
        []                  → ""（标题不体现同行，只讲站点 + 时长）

    与 narrator._format_companions（narration 用，含人数/位）区分：标题更克制。
    """
    if not companions:
        return ""
    roles: list[str] = []
    seen: set[str] = set()
    for c in companions:
        role = (c.get("role") if isinstance(c, dict) else getattr(c, "role", None)) or ""
        age = c.get("age") if isinstance(c, dict) else getattr(c, "age", None)
        if not role:
            continue
        normalized = role.replace("妻子", "老婆").replace("丈夫", "老公")
        if age is not None and age <= 12:
            normalized = "孩子"
        if normalized in seen:
            continue
        seen.add(normalized)
        roles.append(normalized)
    if not roles:
        return ""
    # 老婆 + 孩子 这类家庭组合直接连写（「和老婆孩子」），其余顿号
    if all(r in ("老婆", "老公", "孩子") for r in roles):
        return "和" + "".join(roles)
    return "和" + "、".join(roles)


def node_to_title_phrase(*, title: str, kind: str, target_kind: str) -> Optional[str]:
    """把单个中间站点转成标题里的「动作短语」；home / 空标题返回 None。

    优先用活动关键词派动词（撸串 / 唱K / 看电影）让标题有画面感；
    未命中关键词时：用餐节点 → 「吃饭」，活动节点 → 用店名（去分类前缀）。

    关键词匹配用**完整 title**（不先 split）——否则「麦霸欢唱 KTV · 旗舰店」被切成
    「旗舰店」会丢掉 KTV 关键词（真实踩过的漏站 bug）。
    """
    tk = (target_kind or "").strip()
    if tk == "home":
        return None
    full = (title or "").strip()
    if not full:
        return None

    # 活动/餐饮关键词 → 口语动词短语（信息全的核心：每个站点都要能识别出来）
    # 用完整 title 匹配，避免 split 丢掉前缀里的关键词（如 KTV）。
    for needles, verb in _ACTIVITY_VERB_HINTS:
        if any(n in full for n in needles):
            return verb

    name = _short_title(full)
    k = (kind or "").strip()
    if tk == "restaurant" or "用餐" in k or "夜宵" in k:
        return "吃饭"
    return name


def build_xiaohongshu_title(
    *,
    station_phrases: list[str],
    companions_phrase: str = "",
    total_hours: Optional[float] = None,
) -> str:
    """把「站点动作短语 + 同行短语 + 时长」拼成一句小红书风格标题（规则版）。

    Args:
        station_phrases: 已归一化的站点动作短语（如 ["撸串", "唱K"]），保序去重由本函数处理。
        companions_phrase: 同行短语（如「和室友」「和闺蜜」「一个人」）；空串=不体现。
        total_hours: 总时长（小时），用于补「X小时」氛围；None=不体现时长。

    Returns:
        一句话标题，覆盖所有传入站点，无「半日方案·」前缀、无「（约X小时）」括号。
        无任何站点时返回兜底「出门走走」。

    示例（室友4人·烧烤+KTV·4.5h）：
        station_phrases=["撸串", "唱K"], companions_phrase="和室友", total_hours=4.5
        → "和室友撸串+唱K，4.5小时"
    """
    # 保序去重（避免「撸串+撸串」这种重复站点）
    seen: set[str] = set()
    phrases: list[str] = []
    for p in station_phrases:
        p = (p or "").strip()
        if p and p not in seen:
            seen.add(p)
            phrases.append(p)

    # 同行短语规整：「一个人」在标题里口语化成「独自」（小红书味），空串不体现
    comp = (companions_phrase or "").strip()
    if comp == "一个人":
        comp = "独自"

    if not phrases:
        body = comp + "出门走走" if comp else "出门走走"
    else:
        joined = "+".join(phrases)
        body = f"{comp}{joined}" if comp else joined

    # 时长氛围：补到末尾（口语「，X小时」），仅在有站点且时长有效时加
    if phrases and total_hours and total_hours > 0:
        # 去掉 .0 尾巴让标题更干净：4.0 → 4，4.5 → 4.5
        h = round(total_hours, 1)
        h_str = str(int(h)) if abs(h - int(h)) < 0.05 else f"{h:g}"
        body = f"{body}，{h_str}小时"

    return body


__all__ = [
    "node_to_title_phrase",
    "companions_to_title_phrase",
    "build_xiaohongshu_title",
]
