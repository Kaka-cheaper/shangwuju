"""spec algorithm-redesign R4：_utility 函数加 semantic_scores 加项数学验证。

测试覆盖（≥ 2 项）：
- _utility 加 semantic_scores=None 时不加项（向后兼容）
- _utility 加 semantic_scores={poi.id: 1.0} 时分数提升 0.3
- 同一 POI 不同 semantic_score 反映在最终 utility 上
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# 复用过渡态桥
if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.planning.planners.ils_planner import _utility  # noqa: E402
from agent.planning.weights_llm import PlanningWeights  # noqa: E402
from tests.test_grounding_first import _make_intent_solo, _make_poi  # noqa: E402


def _default_weights() -> PlanningWeights:
    """构造默认权重 fixture"""
    return PlanningWeights(
        comfort=0.3,
        time=0.2,
        cost=0.2,
        smoothness=0.3,
        source="test",
    )


def test_utility_without_semantic_scores_backward_compat():
    """semantic_scores=None → 不加项（与 spec A R5 行为一致）"""
    intent = _make_intent_solo()
    poi = _make_poi("P_1", distance_km=2.0)
    w = _default_weights()
    score_no_sem, _ = _utility(poi, None, "", intent, w, semantic_scores=None)
    score_default, _ = _utility(poi, None, "", intent, w)  # 不传参数
    assert score_no_sem == score_default, "缺省与显式 None 行为应一致"


def test_utility_with_high_semantic_score_increases_utility():
    """semantic_scores={poi.id: 1.0} → utility 比 None 时高 0.3"""
    intent = _make_intent_solo()
    poi = _make_poi("P_1", distance_km=2.0)
    w = _default_weights()
    score_no_sem, _ = _utility(poi, None, "", intent, w, semantic_scores=None)
    score_high, _ = _utility(
        poi, None, "", intent, w, semantic_scores={"P_1": 1.0}
    )
    assert score_high == pytest.approx(score_no_sem + 0.3, abs=1e-6), (
        f"semantic=1.0 应让 utility +0.3：no_sem={score_no_sem:.4f} "
        f"high={score_high:.4f}"
    )


def test_utility_low_semantic_score_smaller_increase():
    """semantic_scores={poi.id: 0.0} → utility +0（0.3 * 0 = 0）"""
    intent = _make_intent_solo()
    poi = _make_poi("P_1", distance_km=2.0)
    w = _default_weights()
    score_no_sem, _ = _utility(poi, None, "", intent, w, semantic_scores=None)
    score_low, _ = _utility(
        poi, None, "", intent, w, semantic_scores={"P_1": 0.0}
    )
    assert score_low == pytest.approx(score_no_sem + 0.0, abs=1e-6)


def test_utility_missing_id_in_semantic_scores_uses_default_05():
    """semantic_scores 不含 poi.id → 用默认 0.5（+0.15）"""
    intent = _make_intent_solo()
    poi = _make_poi("P_NOT_IN_SCORES", distance_km=2.0)
    w = _default_weights()
    score_no_sem, _ = _utility(poi, None, "", intent, w, semantic_scores=None)
    score_default, _ = _utility(
        poi, None, "", intent, w, semantic_scores={"P_OTHER": 0.9}
    )
    expected_diff = 0.3 * 0.5
    assert score_default == pytest.approx(score_no_sem + expected_diff, abs=1e-6)


def test_utility_no_poi_no_semantic_added():
    """poi=None（仅餐厅）→ semantic 项不加（餐厅不参与 LLM 语义打分）

    设计：餐厅由 dietary_constraints + spec A R7 social_compat 处理
    """
    intent = _make_intent_solo()
    w = _default_weights()
    # 仅餐厅（构造一个最小 Restaurant 用 _make_intent 伴随的 fixture 风格）
    from tests.test_grounding_first import _make_restaurant

    rest = _make_restaurant("R_1", distance_km=2.0)
    score_no_sem, _ = _utility(None, rest, "17:30", intent, w, semantic_scores=None)
    score_with_sem, _ = _utility(
        None, rest, "17:30", intent, w, semantic_scores={"R_1": 1.0}
    )
    # 因为 poi=None，semantic 不加项，两个分数应一致
    assert score_no_sem == score_with_sem, "餐厅不参与 LLM 语义打分；分数应不变"
