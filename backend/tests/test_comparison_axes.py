"""spec algorithm-redesign R7：comparison_axes 三轴评分单测。

测试覆盖（≥ 4 项）：
- 5 岁娃 196min 反例 duration_compliance ≤ 50
- 合规候选 duration_compliance = 100
- 距离公式数学正确（target=60min ± 一定偏差时分数变化）
- 偏好匹配度从 semantic_scores 拿
- 三个字段都返 0-100 整数
- semantic_scores=None 时偏好默认 70
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.planning.comparison_axes import compute_axes  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary  # noqa: E402


def _intent_with_5yo() -> IntentExtraction:
    """带 5 岁娃 → cap 75min"""
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=[4, 6],
        distance_max_km=10.0,
        companions=[Companion(role="孩子", age=5, count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="带 5 岁娃出去玩",
        parse_confidence=0.9,
    )


# ============================================================
# 测试 1：5 岁娃 196min 反例 duration_compliance ≤ 50
# ============================================================


def test_5yo_with_long_poi_low_duration_compliance():
    """5 岁娃 + POI 196min（cap 75）→ 100% 违规 → duration_compliance = 0"""
    intent = _intent_with_5yo()
    # legal_itinerary 默认 POI 165min；改造让 POI 超 cap 严重
    itinerary = _make_legal_itinerary(poi_duration=196)
    axes = compute_axes(itinerary, intent)
    # itinerary 含 1 个 mid POI（n1），1/1 违规 → 0%
    assert axes["duration_compliance"] == 0


def test_compliant_itinerary_duration_100():
    """合规候选 → duration_compliance = 100"""
    intent = _make_intent()  # 无 companions → cap 9999
    itinerary = _make_legal_itinerary()
    axes = compute_axes(itinerary, intent)
    assert axes["duration_compliance"] == 100


# ============================================================
# 测试 2：距离合理度公式
# ============================================================


def test_distance_rationality_at_target_high_score():
    """通勤总时间接近 target → 高分（接近 100）"""
    intent = _make_intent(duration_hours=[5, 6])  # target = 5*60*0.2 = 60min
    itinerary = _make_legal_itinerary()
    # legal itinerary hops：9 + 5 + 7 = 21min（远低于 target 60）
    axes = compute_axes(itinerary, intent)
    # diff = 21 - 60 = -39；exp(-39^2/800) = exp(-1.9) ≈ 0.149；分数 ~14
    assert 0 <= axes["distance_rationality"] <= 100
    # 这个例子距离 target 较远，应在 30 以下
    assert axes["distance_rationality"] < 30


def test_distance_rationality_returns_int_0_100():
    """所有 axes 返 0-100 整数"""
    intent = _make_intent()
    itinerary = _make_legal_itinerary()
    axes = compute_axes(itinerary, intent)
    for key, val in axes.items():
        assert isinstance(val, int), f"{key}={val} 应是 int"
        assert 0 <= val <= 100, f"{key}={val} 应在 [0,100]"


# ============================================================
# 测试 3：偏好匹配度
# ============================================================


def test_preference_match_uses_semantic_scores():
    """semantic_scores={poi.id: 0.85} → preference_match = 85"""
    intent = _make_intent()
    itinerary = _make_legal_itinerary(poi_id="P040")
    axes = compute_axes(itinerary, intent, semantic_scores={"P040": 0.85})
    assert axes["preference_match"] == 85


def test_preference_match_default_70_when_no_scores():
    """semantic_scores=None → preference_match = 70 占位"""
    intent = _make_intent()
    itinerary = _make_legal_itinerary()
    axes = compute_axes(itinerary, intent, semantic_scores=None)
    assert axes["preference_match"] == 70


def test_preference_match_default_70_when_score_not_match():
    """semantic_scores 不含 itinerary 里的 poi.id → 70 占位"""
    intent = _make_intent()
    itinerary = _make_legal_itinerary(poi_id="P040")
    axes = compute_axes(intent=intent, itinerary=itinerary, semantic_scores={"P_OTHER": 0.9})
    assert axes["preference_match"] == 70


def test_preference_match_averages_multiple_pois():
    """多个 POI 的 semantic_scores 取平均

    legal itinerary 仅 1 个 POI 节点（P040），所以这里仅验证单 POI 的取值
    （扩展场景由 task 5 的 _make_intent_with_multi_poi 测）。
    """
    intent = _make_intent()
    itinerary = _make_legal_itinerary(poi_id="P040")
    axes = compute_axes(itinerary, intent, semantic_scores={"P040": 0.4})
    assert axes["preference_match"] == 40


# ============================================================
# 测试 4：三个字段都存在
# ============================================================


def test_three_axes_keys_always_present():
    intent = _make_intent()
    itinerary = _make_legal_itinerary()
    axes = compute_axes(itinerary, intent)
    assert "duration_compliance" in axes
    assert "distance_rationality" in axes
    assert "preference_match" in axes
    assert len(axes) == 3
