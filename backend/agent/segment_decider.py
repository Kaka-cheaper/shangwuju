"""agent.segment_decider —— 根据 IntentExtraction 决定行程要哪些段。

【为什么需要这个模块】（参考 pitfalls.md P1-2026-05-17）

历史包袱：planner.py 与 演示场景集.md §三 期待结构都把 itinerary 写死成
「出发 → 主活动 → 转场 → 用餐 → 返回」5 段。一旦用户反馈"我只有 1 小时"，
duration_hours 缩到 [1,1] 也无济于事——planner 依然强塞用餐段，造成下面问题：

- 总时长无法压缩（用餐 + 转场 至少占 60+ 分钟）
- Critic 把 "5 段缺失" 判硬违规
- 文案模板默认有"先去玩、再去吃"叙述
- confirm 默认调 reserve_restaurant，导致"我只想散步"也被预约餐厅

修复方向：把 stage 集合做成 IntentExtraction 的函数，让规划层按需要拼段。

【输入与输出】

- 输入：IntentExtraction（含 duration_hours / social_context / dietary_constraints）
- 输出：SegmentSet —— 一个 frozenset[str]，包含本次该出现的 stage kind

【规则纯粹性】

- 纯函数（无副作用）；同 intent 必同输出，便于测试
- 不调 LLM；LLM 介入路径在 refiner（refiner 改 duration → 本模块自动跟着变）
  这是为什么用户说的"LLM 路由判断反馈"在当前架构下**已经天然实现**：
  refiner 把"我只有 1 小时"翻译成 duration_hours=[1,1]，下游本模块按时长削段。

不负责：
- 时间轴拼装（在 planner._assemble_itinerary）
- ILS 候选生成（在 planner_hybrid）
- Critic 判段（在 critics.HardConstraintCritic）
"""

from __future__ import annotations

from typing import Final

from schemas.intent import IntentExtraction


# ============================================================
# 段类型词典（与 演示场景集.md §三 / planner.DEFAULT_DINING_TIMES 同源）
# ============================================================

# 永远要的两段（demo 评委要看到"从家出发→回家"闭环）
ALWAYS_INCLUDED: Final[frozenset[str]] = frozenset({"出发", "返回"})

# 完整 5 段（向后兼容：4h+ 场景仍维持现状）
FULL_SEGMENTS: Final[frozenset[str]] = frozenset(
    {"出发", "主活动", "转场", "用餐", "返回"}
)

# 用餐导向的 social_context（即使时长不足也优先保留用餐段）
_DINING_FOCUSED_CONTEXTS: Final[frozenset[str]] = frozenset(
    {"商务接待", "纪念日仪式感"}
)

# 单人沉浸场景（只去 POI、不强行吃饭）
_SOLO_IMMERSIVE_CONTEXTS: Final[frozenset[str]] = frozenset({"独处放空"})

# 时长阈值（分钟）
THRESHOLD_VERY_SHORT_MIN: Final[int] = 90   # < 90min：单段
THRESHOLD_SHORT_MIN: Final[int] = 180       # < 180min：弹性单/三段
THRESHOLD_SHORT_HAS_BOTH_MIN: Final[int] = 150  # 150-180min 时若有饮食偏好可塞主活动


# ============================================================
# 主入口
# ============================================================

def decide_segments(intent: IntentExtraction) -> frozenset[str]:
    """决定本次行程的段集合。

    返回 frozenset[str]，元素属于 演示场景集.md §三 期待的 stage kind。

    规则（按优先级）：
    1. 出发 / 返回 永远在
    2. duration_hours 上限 < 90min（极短）：
       - 用餐导向 social / 有 dietary → 单段「用餐」
       - 否则 → 单段「主活动」
    3. duration_hours 上限 < 180min（短）：
       - 用餐导向 social：
         - 时长 ≥ 150 → 三段（主活动+转场+用餐）
         - 否则 → 单段「用餐」
       - 独处放空 + 无 dietary → 单段「主活动」
       - 有 dietary + 非独处 → 三段（主活动+转场+用餐）
       - 都不满足 → 单段「主活动」
    4. duration_hours 上限 ≥ 180min（中长）：
       - 独处放空 + 无 dietary → 单段「主活动」（一人安静）
       - 否则 → 完整 5 段
    """
    duration_max_min = max(0, intent.duration_hours[1]) * 60
    has_dietary = bool(intent.dietary_constraints)
    ctx = intent.social_context

    base = set(ALWAYS_INCLUDED)

    # 极短：1 主体段
    if duration_max_min < THRESHOLD_VERY_SHORT_MIN:
        if has_dietary or ctx in _DINING_FOCUSED_CONTEXTS:
            base.add("用餐")
        else:
            base.add("主活动")
        return frozenset(base)

    # 短：单段或三段
    if duration_max_min < THRESHOLD_SHORT_MIN:
        if ctx in _DINING_FOCUSED_CONTEXTS:
            if duration_max_min >= THRESHOLD_SHORT_HAS_BOTH_MIN:
                base.update({"主活动", "转场", "用餐"})
            else:
                base.add("用餐")
        elif ctx in _SOLO_IMMERSIVE_CONTEXTS and not has_dietary:
            base.add("主活动")
        elif has_dietary:
            base.update({"主活动", "转场", "用餐"})
        else:
            base.add("主活动")
        return frozenset(base)

    # 中长：默认 5 段，独处放空例外
    if ctx in _SOLO_IMMERSIVE_CONTEXTS and not has_dietary:
        base.add("主活动")
        return frozenset(base)

    return FULL_SEGMENTS


# ============================================================
# 辅助：诊断标签（给 trace agent_thought 用）
# ============================================================

def explain_segments(intent: IntentExtraction, segments: frozenset[str]) -> str:
    """给 trace 用的中文摘要，让评委看到段决策的依据。"""
    duration_max_min = intent.duration_hours[1] * 60
    if duration_max_min < THRESHOLD_VERY_SHORT_MIN:
        bracket = "极短"
    elif duration_max_min < THRESHOLD_SHORT_MIN:
        bracket = "短"
    else:
        bracket = "中长"
    kept = "/".join(sorted(segments))
    return (
        f"段决策：duration_hours[1]={intent.duration_hours[1]}h（{bracket}）"
        f"，social={intent.social_context}"
        f"，dietary={'有' if intent.dietary_constraints else '无'}"
        f" → 保留段 {{{kept}}}"
    )
