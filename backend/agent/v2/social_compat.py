"""social_compat —— social_context 与候选 suitable_for / 标签的兼容矩阵。

【为什么独立成文件】

旧 critics_v2._check_social_context 用零碎 if 判：
- 「独处+多人位」warning
- 「家庭+商务包间」warning

问题：
1. 全是 warning，不进 backprompt，LLM 不修
2. 矩阵散落在代码 if 里，加场景就一直 if
3. 与 narrator 暖语气逻辑没共享（narrator 同样需要"知道这场景该说什么调性"）

修复：抽到独立矩阵 + 兼容性等级（match / acceptable / poor / blocking），
critics_v2 看 blocking 升 CRITICAL，narrator 看 match 选问候语调性。

【兼容性等级】

- MATCH       ：完美匹配（家庭日常 + 适合家庭）
- ACCEPTABLE  ：可接受（家庭日常 + 适合朋友）
- POOR        ：调性偏差但仍可用（闺蜜聊天 + 适合家庭）→ warning
- BLOCKING    ：业务上严重不匹配 → critical（必须 backprompt）

【与既有代码的关系】

- 旧 critics_v2._check_social_context 改为 thin wrapper 调本模块
- narrator 读 match 等级决定问候语
- 真上线时矩阵由运营团队维护词典；本质同 SemRel ontology

不负责：
- LLM 调用 / Critic 实现
- 历史 if 逻辑（已迁移到 _MISMATCH_RULES）
"""

from __future__ import annotations

from enum import Enum

from schemas.domain import Poi, Restaurant
from schemas.intent import IntentExtraction


# ============================================================
# 兼容性等级
# ============================================================

class CompatLevel(str, Enum):
    MATCH = "match"
    ACCEPTABLE = "acceptable"
    POOR = "poor"
    BLOCKING = "blocking"


# ============================================================
# 矩阵：(input_social_context, candidate_suitable_for) → CompatLevel
# ============================================================
#
# 设计纪律：
# 1. 默认 ACCEPTABLE（mock 数据无明确不兼容信号时不误伤）
# 2. 仅声明 MATCH / POOR / BLOCKING 这三个特殊态
# 3. BLOCKING 仅留「业务上严重不匹配 + 评委一压就翻」的两类：
#    - 独处放空 ↔ 适合家庭/适合商务（人多场合）
#    - 家庭日常 ↔ 适合商务（成人化场合，不适合带 5 岁孩）
#    - 老人伴助 ↔ 网红打卡 / 拍照友好（人多 / 物理风险）

_BLOCKING_SOCIAL_MATCHES: dict[tuple[str, str], str] = {
    # 独处放空：禁多人场合
    ("独处放空", "家庭日常"): "「独处放空」选了「适合家庭」的多人场合，调性严重不匹配",
    ("独处放空", "朋友热闹"): "「独处放空」选了「适合朋友热闹」的场合，与独处期望相悖",
    ("独处放空", "商务接待"): "「独处放空」选了「商务接待」的正式场合，与独处期望相悖",
    ("独处放空", "纪念日仪式感"): "「独处放空」选了「纪念日仪式感」的双人专属场合",
    # 家庭日常：禁成人化场合
    ("家庭日常", "商务接待"): "家庭场景（含 5 岁孩）选了「商务接待」的成人化场合，氛围不合",
    # 老人伴助：禁人多/物理风险
    ("老人伴助", "朋友热闹"): "老人伴助场景选了「朋友热闹」的喧嚣场合，物理上对老人不友好",
}

# POOR（warning）：调性偏差但可用
_POOR_SOCIAL_MATCHES: dict[tuple[str, str], str] = {
    ("闺蜜聊天", "家庭日常"): "闺蜜聊天选了家庭日常场合，会被儿童干扰",
    ("闺蜜聊天", "商务接待"): "闺蜜聊天选了商务接待场合，过于正式",
    ("情侣亲密", "家庭日常"): "情侣亲密选了家庭日常场合，私密性不足",
    ("情侣亲密", "朋友热闹"): "情侣亲密选了朋友热闹场合，氛围喧嚣",
    ("商务接待", "家庭日常"): "商务接待选了家庭日常场合，体面感不足",
    ("商务接待", "朋友热闹"): "商务接待选了朋友热闹场合，体面感不足",
    ("纪念日仪式感", "家庭日常"): "纪念日仪式感选了家庭日常场合，仪式感不足",
    ("纪念日仪式感", "朋友热闹"): "纪念日仪式感选了朋友热闹场合，仪式感不足",
}


# ============================================================
# 主接口
# ============================================================

def evaluate(
    input_social: str, candidate_suitable_for: list[str]
) -> tuple[CompatLevel, str]:
    """评估 (input social_context, candidate.suitable_for) 的兼容性。

    Args:
        input_social: 用户意图抽出的 social_context（9 选 1）
        candidate_suitable_for: 候选 POI/餐厅 的 suitable_for 列表

    Returns:
        (level, reason)
        - level=MATCH：input_social 在 candidate_suitable_for 内
        - level=BLOCKING：命中 _BLOCKING_SOCIAL_MATCHES 中任意一条
        - level=POOR：命中 _POOR_SOCIAL_MATCHES 中任意一条
        - level=ACCEPTABLE：以上都不满足（默认通过）
    """
    if not input_social:
        return CompatLevel.ACCEPTABLE, ""
    if not candidate_suitable_for:
        # 候选没声明 suitable_for → 不报，默认通过（mock 数据可能字段缺失）
        return CompatLevel.ACCEPTABLE, ""

    # 优先看 MATCH
    if input_social in candidate_suitable_for:
        return CompatLevel.MATCH, ""

    # 看 BLOCKING（任一 candidate suitable_for 命中 → BLOCKING）
    for s in candidate_suitable_for:
        key = (input_social, s)
        if key in _BLOCKING_SOCIAL_MATCHES:
            return CompatLevel.BLOCKING, _BLOCKING_SOCIAL_MATCHES[key]

    # 看 POOR
    for s in candidate_suitable_for:
        key = (input_social, s)
        if key in _POOR_SOCIAL_MATCHES:
            return CompatLevel.POOR, _POOR_SOCIAL_MATCHES[key]

    # 默认 ACCEPTABLE
    return CompatLevel.ACCEPTABLE, ""


def evaluate_poi(intent: IntentExtraction, poi: Poi) -> tuple[CompatLevel, str]:
    """便捷封装：传 IntentExtraction + Poi。"""
    return evaluate(intent.social_context, poi.suitable_for)


def evaluate_restaurant(
    intent: IntentExtraction, rest: Restaurant
) -> tuple[CompatLevel, str]:
    """便捷封装：传 IntentExtraction + Restaurant。"""
    return evaluate(intent.social_context, rest.suitable_for)


__all__ = [
    "CompatLevel",
    "evaluate",
    "evaluate_poi",
    "evaluate_restaurant",
]
