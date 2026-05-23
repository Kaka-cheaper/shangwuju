"""agent.node_decider —— 根据 IntentExtraction 决定行程的中间节点 kind 列表（edge_v1）。

【为什么需要这个模块】（参考 pitfalls.md P1-2026-05-17 / spec itinerary-edge-model-refactor R7）

历史包袱：rule planner 把 itinerary 写死成「出发 / 主活动 / 转场 / 用餐 / 返回」5 段——
即使用户反馈"我只有 1 小时"，也会被强塞用餐段，导致：

- 总时长无法压缩（用餐 + 转场 至少占 60+ 分钟）
- Critic 判段缺失硬违规
- confirm 阶段误调 reserve_restaurant，"我只想散步"也被预约了餐厅

【edge_v1 的本质转变】

旧 `agent/segment_decider.decide_segments` 返「段集合」frozenset[str]，包含
"出发 / 主活动 / 转场 / 用餐 / 返回" 5 个 stage kind——其中「出发 / 转场 / 返回」
是过程段，与新模型 hop（边）的概念重叠。

新 `agent/node_decider.decide_nodes` 返「中间节点 kind 列表」list[str]，**只含 mid
nodes**：
- 不含首尾 home（assemble_from_blueprint 自动补）
- 不含通勤过程段（这些是 hops，不是 nodes）
- 元素为「主活动 / 用餐 / 夜宵 / 早茶 / 自由 / ...」等"在某地停留"的中文标签

【规则纯粹性】

- 纯函数（无副作用）；同 intent 必同输出，便于测试
- 不调 LLM；LLM 介入路径在 refiner（refiner 改 duration → 本模块自动跟着变）
- 用户提"我只有 1 小时"时，refiner 把它翻译成 duration_hours=[1,1]，
  下游本模块据此削掉中间节点

【返回顺序】

按时间序排列，调用方按列表顺序构造 BlueprintNode（餐前先去 POI、用餐前不再去 POI）。

不负责：
- 时间轴拼装（在 agent/assemble_blueprint.py）
- 蓝图 critic（在 agent/blueprint.py）
- ILS 候选生成（在 agent/planner_hybrid.py）
"""

from __future__ import annotations

from typing import Final, Iterable

from schemas.intent import IntentExtraction


# ============================================================
# 节点 kind 词典（中文标签 —— ActivityNode.kind 的取值）
# ============================================================

KIND_MAIN: Final[str] = "主活动"
"""主活动节点（target_kind=poi）。"""

KIND_DINING: Final[str] = "用餐"
"""用餐节点（target_kind=restaurant）。"""

# 完整中等场景的中间节点（新模型不再含「出发 / 转场 / 返回」过程段）
FULL_MID_NODES: Final[tuple[str, ...]] = (KIND_MAIN, KIND_DINING)
"""5 段时代的「3 段中间节点」≡ edge_v1 的 (主活动, 用餐) 二节点。"""

# 兼容别名：旧 FULL_SEGMENTS / ALWAYS_INCLUDED 仍被部分代码 import；
# Wave 5 期间保留以避免一次性大改。删除时机：Task 14 测试同步完成后。
ALWAYS_INCLUDED: Final[frozenset[str]] = frozenset({"出发", "返回"})
"""旧版「永远包含的过程段」语义；edge_v1 等同于「首尾 home 节点 + 路上 hop 自动补」。
保留仅供兼容引用；新代码请直接基于 nodes/hops 模型推理。"""

FULL_SEGMENTS: Final[frozenset[str]] = frozenset(
    {"出发", "主活动", "转场", "用餐", "返回"}
)
"""旧版 5 段集合；edge_v1 已弃用，仅作过渡 alias 保留以免外部 import 损坏。"""


# ============================================================
# 用餐 / 沉浸场景词典
# ============================================================

# 用餐导向的 social_context（即使时长不足也优先保留用餐节点）
_DINING_FOCUSED_CONTEXTS: Final[frozenset[str]] = frozenset(
    {"商务接待", "纪念日仪式感"}
)

# 单人沉浸场景（只去 POI、不强行吃饭）
_SOLO_IMMERSIVE_CONTEXTS: Final[frozenset[str]] = frozenset({"独处放空"})

# 时长阈值（分钟）—— 与旧 segment_decider 保持一致，避免行为漂移
THRESHOLD_VERY_SHORT_MIN: Final[int] = 90      # < 90min：单中间节点
THRESHOLD_SHORT_MIN: Final[int] = 180          # < 180min：弹性 1 / 2 节点
THRESHOLD_SHORT_HAS_BOTH_MIN: Final[int] = 150  # 150-180min 时，dining 导向场景可塞主活动


# ============================================================
# 主入口
# ============================================================


def decide_nodes(intent: IntentExtraction) -> list[str]:
    """决定本次行程的中间节点 kind 列表（按时间顺序）。

    返回 list[str]，元素为 ActivityNode.kind 的中文标签。
    不含首尾 home（assemble_from_blueprint 自动补），也不含 hop（通勤是边不是节点）。

    返回值示例：
    - ["主活动", "用餐"]：完整下午局（先玩后吃）
    - ["主活动"]：单段 POI 体验（用户只想散步）
    - ["用餐"]：直接用餐（商务接待 / 1 小时短时段）
    - []：极端兜底（理论不会发生，调用方应回退到默认 ["主活动"]）

    规则（按优先级，与旧 decide_segments 行为对齐）：

    1. duration_hours 上限 < 90min（极短）：
       - 用餐导向 social / 有 dietary → ["用餐"]
       - 否则 → ["主活动"]
    2. duration_hours 上限 < 180min（短）：
       - 用餐导向 social：
         - 时长 ≥ 150min → ["主活动", "用餐"]
         - 否则 → ["用餐"]
       - 独处放空 + 无 dietary → ["主活动"]
       - 有 dietary + 非独处 → ["主活动", "用餐"]
       - 都不满足 → ["主活动"]
    3. duration_hours 上限 ≥ 180min（中长）：
       - 独处放空 + 无 dietary → ["主活动"]（一人安静）
       - 否则 → ["主活动", "用餐"]
    """
    duration_max_min = max(0, intent.duration_hours[1]) * 60
    has_dietary = bool(intent.dietary_constraints)
    ctx = intent.social_context

    # 极短：1 个中间节点
    if duration_max_min < THRESHOLD_VERY_SHORT_MIN:
        if has_dietary or ctx in _DINING_FOCUSED_CONTEXTS:
            return [KIND_DINING]
        return [KIND_MAIN]

    # 短：1 或 2 节点
    if duration_max_min < THRESHOLD_SHORT_MIN:
        if ctx in _DINING_FOCUSED_CONTEXTS:
            if duration_max_min >= THRESHOLD_SHORT_HAS_BOTH_MIN:
                return [KIND_MAIN, KIND_DINING]
            return [KIND_DINING]
        if ctx in _SOLO_IMMERSIVE_CONTEXTS and not has_dietary:
            return [KIND_MAIN]
        if has_dietary:
            return [KIND_MAIN, KIND_DINING]
        return [KIND_MAIN]

    # 中长：默认 (主活动, 用餐)；独处放空例外
    if ctx in _SOLO_IMMERSIVE_CONTEXTS and not has_dietary:
        return [KIND_MAIN]
    return [KIND_MAIN, KIND_DINING]


# ============================================================
# 兼容 alias：旧函数 decide_segments
# ============================================================


def decide_segments(intent: IntentExtraction) -> frozenset[str]:
    """兼容 alias —— 把 decide_nodes 的中间节点列表转回旧「段集合」语义。

    转换规则（仅供过渡期 hybrid ILS / 旧 critic 使用）：
    - 永远含「出发 / 返回」（home 起讫的 hop 概念，旧代码仍按 stage 名查找）
    - 含 KIND_MAIN → 加「主活动」
    - 含 KIND_DINING → 加「用餐」
    - 含 主活动 + 用餐 → 加「转场」（新模型由 hop 表达，旧代码仍判段名）

    Wave 5 完结后，所有调用方应迁移到 decide_nodes，本函数与 alias 同步删除。
    """
    nodes = decide_nodes(intent)
    out: set[str] = set(ALWAYS_INCLUDED)  # {"出发", "返回"}
    if KIND_MAIN in nodes:
        out.add(KIND_MAIN)
    if KIND_DINING in nodes:
        out.add(KIND_DINING)
    if KIND_MAIN in nodes and KIND_DINING in nodes:
        out.add("转场")
    return frozenset(out)


# ============================================================
# 辅助：诊断标签（给 trace agent_thought 用）
# ============================================================


def explain_nodes(intent: IntentExtraction, nodes: Iterable[str]) -> str:
    """给 trace 用的中文摘要，让评委看到节点决策的依据。"""
    duration_max_min = intent.duration_hours[1] * 60
    if duration_max_min < THRESHOLD_VERY_SHORT_MIN:
        bracket = "极短"
    elif duration_max_min < THRESHOLD_SHORT_MIN:
        bracket = "短"
    else:
        bracket = "中长"
    kept = " → ".join(nodes) if nodes else "（无中间节点）"
    return (
        f"节点决策：duration_hours[1]={intent.duration_hours[1]}h（{bracket}）"
        f"，social={intent.social_context}"
        f"，dietary={'有' if intent.dietary_constraints else '无'}"
        f" → 中间节点 [{kept}]"
    )


def explain_segments(intent: IntentExtraction, segments: Iterable[str]) -> str:
    """旧 explain_segments 别名（兼容期保留；内部转发给 explain_nodes）。"""
    # 把段集合中的 mid kind 抽出来重述（"出发"/"返回"/"转场" 是过程段，不进 explain_nodes 输入）
    mid_kinds = [s for s in segments if s in {KIND_MAIN, KIND_DINING}]
    return explain_nodes(intent, mid_kinds)
