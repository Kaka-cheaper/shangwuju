"""agent.planning.comparison_axes —— 三轴评分计算（spec algorithm-redesign R7）。

【为什么需要这一层】

LLM-Modulo 范式（Kambhampati NeurIPS'24）的核心可见性叙事：「不只给一个方案，
还给出每个候选在不同评分维度上的明确分值」。让评委看到「AI 不是黑盒，AI 自己
对自己产出的方案有可量化的评估」。

【三轴定义（design.md §Component 5）】

- **时长合规度** (duration_compliance) = 100 * (1 - 违规节点数 / 总节点数)
  - 违规节点 = duration_min > age cap（按同行人推 cap）
  - 0-100 整数

- **距离合理度** (distance_rationality) = 100 * exp(-(总通勤 - target_min)^2 / 800)
  - target_min = duration_hours[0] × 60 × 0.2（理想通勤占比 20%）
  - 0-100 整数；越接近 target 分越高

- **偏好匹配度** (preference_match) = 100 * mean(semantic_scores)
  - 从 task 5 的 preference_scorer 输出拿；候选池为空则 70 占位
  - 0-100 整数

【设计纪律】

- 永不抛异常（任何错误返默认 70/70/70）
- 字段名固定 3 个：duration_compliance / distance_rationality / preference_match
- int 0-100（前端 axisbar 渲染用整数避免浮点显示问题）

不负责：
- 候选排序（utility 已经在 ils_planner 排过）
- 前端 UI 渲染
"""

from __future__ import annotations

import math
from typing import Optional

from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary


def compute_axes(
    itinerary: Itinerary,
    intent: IntentExtraction,
    *,
    semantic_scores: Optional[dict[str, float]] = None,
) -> dict[str, int]:
    """三轴评分计算（spec algorithm-redesign R7）。

    Args:
        itinerary: 待评分行程
        intent: 意图（提供 companions / duration_hours 用于 cap 推算）
        semantic_scores: task 5 的 preference_scorer 输出；None 时偏好分给 70 占位

    Returns:
        dict[str, int]：3 个 0-100 整数字段
        - duration_compliance：时长合规度
        - distance_rationality：距离合理度
        - preference_match：偏好匹配度

    设计：永不抛异常；任何子计算失败返默认 70（中性）
    """
    return {
        "duration_compliance": _compute_duration_compliance(itinerary, intent),
        "distance_rationality": _compute_distance_rationality(itinerary, intent),
        "preference_match": _compute_preference_match(itinerary, semantic_scores),
    }


def _compute_duration_compliance(
    itinerary: Itinerary, intent: IntentExtraction
) -> int:
    """时长合规度 = 100 * (1 - 违规节点数 / 总节点数)。

    违规判定：node.duration_min > age_cap（按同行人最严 cap）
    """
    try:
        cap = _resolve_age_cap(intent)
        if cap >= 9999:
            # 没有年龄约束 → 100 分（无违规可言）
            return 100

        total_nodes = 0
        violation_count = 0
        for node in itinerary.nodes:
            if node.target_kind != "poi":
                continue
            total_nodes += 1
            if node.duration_min > cap:
                violation_count += 1

        if total_nodes == 0:
            return 100
        ratio = 1.0 - (violation_count / total_nodes)
        return max(0, min(100, int(100 * ratio)))
    except Exception:
        return 70  # 中性兜底


def _compute_distance_rationality(
    itinerary: Itinerary, intent: IntentExtraction
) -> int:
    """距离合理度 = 100 * exp(-(总通勤 - target_min)^2 / 800)。

    target_min = duration_hours[0] × 60 × 0.2（理想通勤占总时长 20%）
    """
    try:
        # 总通勤时间 = 所有 hop.minutes 之和
        total_commute = sum(hop.minutes for hop in itinerary.hops)

        # target：理想通勤时间（按 duration_hours 下限的 20%）
        if intent.duration_hours and len(intent.duration_hours) >= 1:
            target = intent.duration_hours[0] * 60 * 0.2
        else:
            target = 60.0  # 默认 60min

        # 高斯衰减：|总通勤 - target| 越大，分数越低
        diff = total_commute - target
        score = 100 * math.exp(-(diff * diff) / 800.0)
        return max(0, min(100, int(score)))
    except Exception:
        return 70


def _compute_preference_match(
    itinerary: Itinerary,
    semantic_scores: Optional[dict[str, float]] = None,
) -> int:
    """偏好匹配度 = 100 * mean(semantic_scores)。

    semantic_scores 为 None 或空 → 70 占位（中性）
    """
    try:
        if not semantic_scores:
            return 70
        # 仅看 itinerary 中实际出现的 POI 的语义分
        relevant_scores: list[float] = []
        for node in itinerary.nodes:
            if node.target_kind != "poi":
                continue
            if node.target_id and node.target_id in semantic_scores:
                relevant_scores.append(semantic_scores[node.target_id])

        if not relevant_scores:
            return 70

        avg = sum(relevant_scores) / len(relevant_scores)
        return max(0, min(100, int(100 * avg)))
    except Exception:
        return 70


def _resolve_age_cap(intent: IntentExtraction) -> int:
    """从 companions 推单段最严 cap（与 ils_planner._resolve_age_cap 同源）"""
    if intent is None or not getattr(intent, "companions", None):
        return 9999

    caps: list[int] = []
    for c in intent.companions:
        age = getattr(c, "age", None)
        if not isinstance(age, int) or age < 0:
            continue
        if age <= 3:
            caps.append(45)
        elif age <= 6:
            caps.append(75)
        elif age <= 12:
            caps.append(120)
        elif age >= 75:
            caps.append(60)

    if not caps:
        return 9999
    return min(caps)


__all__ = ["compute_axes"]
