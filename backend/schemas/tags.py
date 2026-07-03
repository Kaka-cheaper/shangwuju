"""tags —— 三类 tag 词典 + social_context 9 选 1。

定义来源：`docs/01-requirements/需求分析.md` §5.1 / §5.2 / §5.3 / §5.5。
任何 LLM 抽取的 tag 必须命中这里的词典——下游 Pydantic 校验会拦截发明的 tag
（对应 `pitfalls.md` P1-预埋 词典出口防御）。

【ADR-0014 决策 2（G-2）：严重度分层】
受控词典（physical / dietary）逐词分 hard（一票否决，搜索侧永不放宽、critic
侧逐词核验）/ soft（可协商，搜索侧按 2×2 出处矩阵降级，未满足由出口满足度
审计告知，不在搜索期гейт）。experience 词典无一票否决语义，全 soft。

分层依据（不闭门造词）：逐词对照 `mock_data/pois.json` / `mock_data/
restaurants.json` 的真实 tag 分布 + `agent/intent/prompts/
intent_parser_prompt.py`「隐含约束抽取规则」段的机械触发规则（系统里"这个
tag 到底对应什么真实场景"的唯一权威证据）判定，理由见各常量下方注释。

不负责：
- 词典扩展（新增 tag 必须先改 §5.x 词典再同步本文件）。
"""

from typing import Literal


# ===== §5.1 物理约束（人物） =====
PhysicalTag = Literal[
    "亲子友好",
    "适合 5-10 岁",
    "适合青少年",
    "适合老人",
    "无台阶",
    "可休息",
    "无障碍",
    "高强度",
    "低强度",
]

PHYSICAL_TAGS: frozenset[str] = frozenset(
    [
        "亲子友好",
        "适合 5-10 岁",
        "适合青少年",
        "适合老人",
        "无台阶",
        "可休息",
        "无障碍",
        "高强度",
        "低强度",
    ]
)


# ---- ADR-0014 决策 2：physical 严重度分层 ----
PHYSICAL_HARD_TAGS: frozenset[str] = frozenset(
    {"无台阶", "无障碍", "适合老人", "可休息"}
)
"""安全型（缺了对应人群真的去不了/用不了，不是不够贴心）：
- 无台阶 / 无障碍 / 适合老人：ADR-0014 决策 2 原文点名的安全型范例
  （"轮椅可达"对应本词典的"无障碍"）。
- 可休息：与"无台阶/适合老人"同一条机械触发规则同句产出——
  `intent_parser_prompt.py` few-shot："腿不好 / 老人 / 外公外婆" →
  physical 加 "适合老人""无台阶""可休息"，三者描述的是同一个"移动能力
  受限者的必需设施"场景，不是加分项，归同一严重度。
"""

PHYSICAL_SOFT_TAGS: frozenset[str] = PHYSICAL_TAGS - PHYSICAL_HARD_TAGS
"""舒适 / 内容适配型（亲子友好 / 适合 5-10 岁 / 适合青少年 / 高强度 / 低强度）：
- 亲子友好 / 适合 5-10 岁 / 适合青少年：真正的年龄安全底线由
  `tools.search_pois` 的 `age_in_party` 精确年龄区间过滤单独保障（恒定过滤，
  不走 relax 链路）；这三个 tag 只是氛围/内容适配的补充描述，缺了不代表
  "去不了"，只代表"不够对味"。
- 高强度 / 低强度：`intent_parser_prompt.py`「隐含约束抽取规则」段没有任何
  机械触发规则产出这两个 tag——无证据表明它对应真实体能禁忌，更像节奏
  偏好，归软。
"""


def is_hard_physical_tag(tag: str) -> bool:
    """physical 词典里该 tag 是否安全型硬约束（ADR-0014 决策 2）。"""
    return tag in PHYSICAL_HARD_TAGS


# ===== §5.2 饮食约束 =====
DietaryTag = Literal[
    "低脂",
    "健康轻食",
    "高蛋白",
    "日料",
    "粤菜",
    "不辣",
    "无牛肉",
    "有儿童餐",
    "高人均",
    "有包间",
    "软烂",
    "下午茶",
    "甜品",
]

DIETARY_TAGS: frozenset[str] = frozenset(
    [
        "低脂",
        "健康轻食",
        "高蛋白",
        "日料",
        "粤菜",
        "不辣",
        "无牛肉",
        "有儿童餐",
        "高人均",
        "有包间",
        "软烂",
        "下午茶",
        "甜品",
    ]
)


# ---- ADR-0014 决策 2：dietary 严重度分层 ----
DIETARY_HARD_TAGS: frozenset[str] = frozenset({"不辣", "无牛肉", "软烂"})
"""排除型忌口（吃了/吃不了会真出问题，不是口味偏好）：
- 不辣 / 无牛肉：ADR-0014 决策 2 原文点名的排除型忌口范例。
- 软烂：与 physical 的"无台阶/适合老人/可休息"同一条机械触发规则同句产出
  （"腿不好/老人/外公外婆" few-shot 同时给出 dietary "软烂"）——牙口/
  咀嚼能力受限是同一个生理限制簇的延伸，不是风味偏好，归同一严重度。
"""

DIETARY_SOFT_TAGS: frozenset[str] = DIETARY_TAGS - DIETARY_HARD_TAGS
"""风格 / 场合型（低脂 / 健康轻食 / 高蛋白 / 日料 / 粤菜 / 有儿童餐 / 高人均 /
有包间 / 下午茶 / 甜品）：
- 低脂 / 健康轻食 / 高蛋白：同一条机械触发规则"老婆减肥"产出的一组"健身/
  瘦身型饮食目标"，是目标性偏好而非医疗排除，归软。
- 日料 / 粤菜：菜系风格——ADR-0014 决策 2 原文点名"日料"为风格型范例，
  粤菜同属菜系词，归同一类。
- 有儿童餐 / 有包间：均是"有 XX"呈现式 amenity（非"不/无"排除句式），
  且均无独立的排除语义证据——有包间与"高人均"同一条机械触发规则
  ("商务/客户")同句产出，高人均已是 ADR 明确的风格型范例，同源归软；
  有儿童餐无机械触发证据，按同一"呈现式 amenity"归类。
- 高人均 / 下午茶 / 甜品：价格档 / 场合 / 品类风格标签，归软。
"""


def is_hard_dietary_tag(tag: str) -> bool:
    """dietary 词典里该 tag 是否排除型硬约束（ADR-0014 决策 2）。"""
    return tag in DIETARY_HARD_TAGS


# ===== §5.3 体验偏好 =====
ExperienceTag = Literal[
    "拍照友好",
    "网红打卡",
    "安静聊天",
    "热闹",
    "社交",
    "独处舒缓",
    "商务体面",
    "礼仪感",
    "亲密情侣",
    "学习成长",
    "看展",
    "室内",
    "户外",
]

EXPERIENCE_TAGS: frozenset[str] = frozenset(
    [
        "拍照友好",
        "网红打卡",
        "安静聊天",
        "热闹",
        "社交",
        "独处舒缓",
        "商务体面",
        "礼仪感",
        "亲密情侣",
        "学习成长",
        "看展",
        "室内",
        "户外",
    ]
)
"""ADR-0014 决策 2：experience 词典全 soft——纯氛围/场合偏好，无一票否决语义
（不存在"没有这个氛围 tag 就用不了/去不了"的场景），不设 EXPERIENCE_HARD_TAGS。
"""


def is_hard_tag(tag: str) -> bool:
    """跨三类词典的统一严重度查询接口（ADR-0014 决策 2）。

    hard = 一票否决：`tools._helpers.relax_tag_search` 永不放宽、
    `agent.planning.critic._rules.checks.check_dietary` / `check_physical`
    逐词核验。experience 词典全 soft，不在 DIETARY_HARD_TAGS / PHYSICAL_
    HARD_TAGS 里的 tag（含全部 experience tag、未登记的字符串）一律按 soft/
    非硬处理，返回 False。
    """
    return tag in DIETARY_HARD_TAGS or tag in PHYSICAL_HARD_TAGS


# ===== §5.5 社交上下文（单值，9 选 1） =====
SocialContext = Literal[
    "家庭日常",
    "老人伴助",
    "闺蜜聊天",
    "朋友热闹",
    "情侣亲密",
    "商务接待",
    "同学重聚",
    "独处放空",
    "纪念日仪式感",
]

SOCIAL_CONTEXTS: frozenset[str] = frozenset(
    [
        "家庭日常",
        "老人伴助",
        "闺蜜聊天",
        "朋友热闹",
        "情侣亲密",
        "商务接待",
        "同学重聚",
        "独处放空",
        "纪念日仪式感",
    ]
)
