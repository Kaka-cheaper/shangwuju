"""agent.planning.planners.pace_budget —— ADR-0010 D-3：节奏 / slack / 区间填充模型。

【为什么不放进 activity_pool.py（选址判断点）】

`activity_pool.py`（D-1）的定位在其自身 docstring 里已明确收窄为"①约束 +
utility 构建层"——把**单个候选** POI/餐厅变成一个 `Visit`（自然时长/候选窗/
utility 基分）。本模块处理的是另一个轴："这一整条路线该有多少活动、留多少
空白"——消费的是 `IntentExtraction` 整体（companions + social_context）与
`duration_hours` 区间，产出的是路线级预算参数，不产出/不消费任何单个 `Visit`。
把它塞进 `activity_pool.py` 会让那个模块的"每候选一个 Visit"边界变模糊
（概念完整性：一个模块只回答一类问题）。故独立成本模块，供 D-4（贪心插入构造）
在"决定选多少活动、什么时候停"时消费。

【与曾经存在的 `IntentExtraction.pace_profile` / `PaceProfile` 的关系（历史记录，
该字段已随 ADR-0014 G-0 砍除）】

`schemas/persona.PaceProfile`（spec planning-quality-deep-review R1+R8 引入，
ADR-0014 G-0 于 2026-07-03 砍除）曾是一套**数值型**节奏画像
（`single_session_max_min` / `total_active_min` / `break_every_min` /
`preferred_dwell_min`），来源是 LLM 意图解析注入 prompt 的 prior + refiner
"太久/盯不住" 反馈缩水回写——服务的是 **LLM 主路径的 prompt 注入与迭代反馈**，
本模块当初 `grep` 确认它未被任何 planner/critic 代码实际消费（只有
`intent/parser.py`/`intent/refiner.py`/`core/feedback_detector.py` 引用），
这正是 ADR-0014 G-0 判定其"空转"并砍除的依据——本模块当初"不读 pace_profile、
不假设两套信号该如何合流"的克制判断被后续验证是对的：该字段最终因为
真的没有消费方而被砍，而不是被合流进本模块。

本模块的 `pace()` 是**另一套**、**分类型**（relaxed/medium/energetic 三档）的
节奏信号，ADR-0010 决策 4 原话"节奏不是新字段，从 companions/age + social_context
推（与驱动 age cap 同一批信号）"——明确要求从既有字段**重新推导**，与已砍除的
`pace_profile` 无关，不受其砍除影响。"太久了"类反馈的收缩契约已迁移到
`IntentExtraction.duration_hours` 上界（见 `agent/intent/refiner.py`），
仍然不经过本模块的三档模型——`duration_hours` 是 D-2/D-4 消费的硬预算输入，
`pace()` 的三档只决定预算内的 slack 比例，两者角色不同，无需合流。

【公开接口】

- `PACE_RELAXED` / `PACE_MEDIUM` / `PACE_ENERGETIC`：三档节奏标签常量。
- `pace(intent) -> str`：从 companions + social_context 推节奏档，混合信号取
  最受限（`PACE_RELAXED` > `PACE_MEDIUM` > `PACE_ENERGETIC`，与 age cap 取
  最严同精神——ADR-0010 决策 10"节奏聚合"）。
- `slack_fraction(pace) -> float`：每档节奏对应的留白比例（env 可调，见下方
  判断点）。
- `IntervalFillTargets` / `interval_fill_targets(intent, pace) -> IntervalFillTargets`：
  把 `duration_hours=[lo,hi]` 换算成 D-4 消费的区间填充参数。

【调研留痕：本步自行拍板、值得读者知道的判断点】

1. **`pace()` 的信号集合与优先级**：
   - `PACE_RELAXED` 触发条件：companions 含 ≤6 岁或 ≥75 岁成员，**或**
     `social_context ∈ {独处放空, 老人伴助}`（ADR-0010 D-3 任务原文点名的
     信号集合，与 age cap 分桶的"照护负担/体力受限"同一批人群、同一批理由——
     幼童/高龄需要更多恢复性留白，独处/老人伴助场景天然偏舒缓）。
   - `PACE_ENERGETIC` 触发条件：`social_context == 朋友热闹`（唯一点名的高能量
     场景——朋友聚会场景倾向"多逛少歇"）。
   - 其余（无信号命中）→ `PACE_MEDIUM`（ADR-0010 决策 10"无同行人 → 中等默认"
     的自然推广：不仅无同行人，任何未命中 relaxed/energetic 信号的组合都归中等）。
   - 混合取最受限：只要命中 `PACE_RELAXED` 信号，不论是否同时命中
     `PACE_ENERGETIC` 信号，一律返回 `PACE_RELAXED`（与 `age_caps.
     strictest_cap_for_companions` 的"多代际取最严"同一设计精神——ADR-0010
     决策 10 原话"混合同行取最受限者"）。这类冲突组合（如"孩子 3 岁 + 朋友
     热闹"）在 mock 场景库里罕见，但规则必须对任意组合都有定义，不能留未定义
     行为。
   - **S3（情侣亲密）/ S7（独处放空）的验收表述"不排满/1 活动+大留白"被归为
     一类**，但本函数只把"独处放空"归入 relaxed（ADR-0010 D-3 任务原文点名的
     信号集合就是如此）——"情侣亲密"未命中任何 relaxed/energetic 信号，落
     `PACE_MEDIUM`。S3 的"不排满"预期不是靠 `slack_fraction` 撑起来的，是
     D-4 贪心构造 + 路线级多样性罚（`route_diversity_penalty`）+ 活动自然时长
     本身共同限制数量的涌现结果（ADR-0010 决策 4"反过度打包"三条中的第一条：
     "自然时长本身限制数量（大块）"）——这是 D-4 该验的事，D-3 只按 ADR 原文
     字面实现 `pace()` 的信号集合，不因为 S3 验收描述而擅自把"情侣亲密"也塞进
     relaxed（那会是没有 ADR 依据的私自扩大解释）。已 surface 给复审：若复审
     认为"情侣亲密"也该产生额外留白，请在 D-4/D-6 实测后再决定是否回填。
2. **`slack_fraction` 初值来源（待 D-5 实测校准，ADR 原文已声明）**：
   - `relaxed=0.30` / `medium=0.15` / `energetic=0.05`——**每档比上一档翻倍**
     的等比设计，不是拍脑袋的独立三个数：
     - 参照系是 `age_caps.py` 已引用的 Smithsonian SEEC 幼儿注意力基线与本
       ADR"反过度打包"讨论——幼童/高龄群体的"有效活跃时长"显著短于"在外
       总时长"（这正是 age cap 存在的理由），留白需求量级上高于中等档；
     - `energetic=0.05` 取一个"几乎不留白但不为 0"的小正数——不设 0 是因为
       通勤本身、餐前等待这些"结构性 slack"（`not_before_start` 已表达的
       机制）不该被硬性抹去，即使是最活跃的朋友聚会路线，个别窗约束逼出的
       等待仍应被允许而非报错；
     - 三档翻倍关系（0.05→0.15→0.30）给后续 D-5 实测提供一个有单调结构、
       容易解释"为什么调大/调小"的起点，而非三个互相独立、调整时缺乏参照的
       数字。
   - 全部经 `_env_float` 包一层，允许运行时/测试环境覆盖（复用 `ils_planner`
     已确立的 env 可调惯例，如 `PLANNER_DIVERSITY_REPEAT_PENALTY`）。
3. **`interval_fill_targets` 的返回形状与"软/硬"边界（D-4 消费契约，本步只
   建接口不建消费逻辑）**：
   - `lo_min` / `hi_min`：直接是 `duration_hours` 换算的分钟值，代表"在外
     总时长"这个硬性外壳——`hi_min` 就是 D-2 `schedule_route(..., budget_min=)`
     该传入的硬预算（ADR-0010 决策 4"duration_hours 角色变为在外时长的总
     预算（上限）"）；`lo_min` 是 D-4 贪心插入循环追的下限目标，不由 D-2/D-3
     强制（D-2 的 `schedule_route` 不知道"下限"这个概念，只知道"总时长
     ≤ budget_min"这一个上限约束——下限的追逐是 D-4 插入循环自己的停止条件）。
   - `activity_budget_min = hi_min * (1 - slack_fraction(pace))`：供 D-4 内部
     判断"活动+通勤已经填够了、可以按节奏收尾"的**软**目标（不是再交给 D-2
     的第二个硬预算——D-2 已经只接受一个 `budget_min`，不应该也不需要知道
     "软目标"这个 D-4 内部概念）。刻意允许 `activity_budget_min < lo_min` 的
     数学可能性（当 `slack_fraction` 较大且 `[lo,hi]` 区间窄，如老人场景
     `[3,3]` + relaxed 0.30 → activity_budget=126min < lo=180min）——这**不是
     bug**：这种情况下"软目标"已经小于"下限"，意味着单靠 slack_fraction 的
     分配比例不足以自动保证下限，D-4 必须在"够不够 lo"与"是否已经按节奏该
     停"之间做取舍，ADR-0010 决策 10"稀缺兜底"已经给了原则性答案（宁可短而
     好，不塞次优凑数）——具体怎么在循环里体现这个取舍是 D-4 的实现细节，
     本步不越权替 D-4 拍板，只把三个数字诚实地算出来交给它。
   - 返回值用 `frozen dataclass`（而非裸 tuple）：避免 D-4 消费时靠位置索引
     猜"第几个是什么"，与本模块其它值对象（`TimeWindow`/`Visit`）的既有风格
     一致。
4. **`pace` 形参与模块级 `pace()` 函数同名**：`interval_fill_targets(intent,
   pace)` 的第二个形参字面沿用 ADR-0010 任务原文给出的签名（`interval_fill_
   targets(intent, pace) -> (lo_min, hi_min, activity_budget_min)`）。函数体内
   不调用同名的模块级 `pace()`，只做形参传入值的运算，Python 的函数级作用域
   下这不构成实际冲突（调用方需要显式 `p = pace(intent); interval_fill_targets
   (intent, p)` 两步——刻意保持两个函数解耦：`pace()` 只读 intent 推节奏档，
   `interval_fill_targets()` 只读"给定节奏档"后的区间换算，便于单独测试每一步、
   也便于 D-4 在同一个 intent 上多档试探而不必重复推导 pace）。

不负责：
- 子集选择 / 贪心插入构造本身（D-4 消费本模块产出的参数，不在本模块实现）。
- 路线可行性排程（D-2 `route_scheduler.py`；`interval_fill_targets` 产出的
  `hi_min` 只是 D-2 `budget_min` 的数据来源，本模块不调 D-2）。
- 已随 ADR-0014 G-0 砍除的 `IntentExtraction.pace_profile` 的读取/合流——
  该问题随字段砍除一并消解，见上方判断点的历史记录。
"""

from __future__ import annotations

from dataclasses import dataclass

from schemas.intent import IntentExtraction

from .ils_planner import _env_float

# ============================================================
# 节奏档常量
# ============================================================

PACE_RELAXED = "relaxed"
PACE_MEDIUM = "medium"
PACE_ENERGETIC = "energetic"

_RELAXED_MAX_CHILD_AGE = 6
_RELAXED_MIN_SENIOR_AGE = 75

_RELAXED_SOCIAL_CONTEXTS: frozenset[str] = frozenset({"独处放空", "老人伴助"})
_ENERGETIC_SOCIAL_CONTEXTS: frozenset[str] = frozenset({"朋友热闹"})


def _has_relaxed_companion(companions) -> bool:
    for c in companions or []:
        age = getattr(c, "age", None)
        if not isinstance(age, int):
            continue
        if age <= _RELAXED_MAX_CHILD_AGE or age >= _RELAXED_MIN_SENIOR_AGE:
            return True
    return False


def pace(intent: IntentExtraction) -> str:
    """从 companions + social_context 推节奏档（ADR-0010 决策 4/10）。

    混合信号取最受限：`PACE_RELAXED` > `PACE_MEDIUM` > `PACE_ENERGETIC`。
    见模块 docstring 判断点 1 的信号集合与冲突组合处理说明。
    """
    companions = getattr(intent, "companions", None) if intent is not None else None
    social_context = getattr(intent, "social_context", "") if intent is not None else ""

    if _has_relaxed_companion(companions) or social_context in _RELAXED_SOCIAL_CONTEXTS:
        return PACE_RELAXED
    if social_context in _ENERGETIC_SOCIAL_CONTEXTS:
        return PACE_ENERGETIC
    return PACE_MEDIUM


# ============================================================
# slack_fraction：每档节奏的留白比例
# ============================================================

SLACK_FRACTION_RELAXED = _env_float("PLANNER_SLACK_FRACTION_RELAXED", 0.30)
SLACK_FRACTION_MEDIUM = _env_float("PLANNER_SLACK_FRACTION_MEDIUM", 0.15)
SLACK_FRACTION_ENERGETIC = _env_float("PLANNER_SLACK_FRACTION_ENERGETIC", 0.05)
"""初值来源与"每档翻倍"设计见模块 docstring 判断点 2；env 可调供 D-5 实测校准——
**注意绑定时机**（code-review finding #8）：`_env_float` 在模块 import 时求值一次，
env 必须在**进程启动前**设置；import 后 setenv/monkeypatch.setenv 不生效（与
`ils_planner.ILS_ITERATIONS` 等既有 env 常量同一约定）。测试要改这些值请直接
monkeypatch 模块属性（如 `monkeypatch.setattr(pace_budget, "SLACK_FRACTION_RELAXED", ...)`
并注意 `_SLACK_FRACTION_BY_PACE` 字典也在 import 期绑定）。"""

_SLACK_FRACTION_BY_PACE: dict[str, float] = {
    PACE_RELAXED: SLACK_FRACTION_RELAXED,
    PACE_MEDIUM: SLACK_FRACTION_MEDIUM,
    PACE_ENERGETIC: SLACK_FRACTION_ENERGETIC,
}


def slack_fraction(pace_tier: str) -> float:
    """按节奏档返回留白比例；未知档名（防御性，不应发生）回退中等档。"""
    return _SLACK_FRACTION_BY_PACE.get(pace_tier, SLACK_FRACTION_MEDIUM)


# ============================================================
# interval_fill_targets：区间填充参数（D-4 消费接口）
# ============================================================


@dataclass(frozen=True)
class IntervalFillTargets:
    """`duration_hours=[lo,hi]` 换算成 D-4 贪心插入构造消费的区间填充参数。

    字段含义与"软/硬"边界见模块 docstring 判断点 3：
    - `lo_min` / `hi_min`：在外总时长的下限/上限（分钟）。`hi_min` 是 D-2
      `schedule_route(budget_min=...)` 该用的硬预算；`lo_min` 是 D-4 插入
      循环自己追的下限目标，D-2 不知道这个概念。
    - `activity_budget_min`：供 D-4 参考的"活动+通勤"软目标（`hi_min *
      (1 - slack_fraction)`），不是第二个硬预算——允许数学上小于 `lo_min`
      （见判断点 3 的"稀缺兜底"讨论），D-4 自行决定如何在软目标与下限之间
      取舍。
    """

    lo_min: int
    hi_min: int
    activity_budget_min: int


def interval_fill_targets(
    intent: IntentExtraction, pace: str
) -> IntervalFillTargets:
    """把 `intent.duration_hours` 按给定节奏档换算成区间填充参数。

    `pace` 由调用方先算好传入（`pace(intent)` 是独立一步，见模块 docstring
    判断点 4）——本函数只做"给定节奏档"之后的区间换算，不重新推导节奏。
    """
    lo_h, hi_h = intent.duration_hours[0], intent.duration_hours[1]
    lo_min = int(lo_h * 60)
    hi_min = int(hi_h * 60)
    activity_budget_min = int(round(hi_min * (1 - slack_fraction(pace))))
    return IntervalFillTargets(
        lo_min=lo_min, hi_min=hi_min, activity_budget_min=activity_budget_min
    )
