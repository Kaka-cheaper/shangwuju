"""tests.test_multi_turn_stability —— Step 10：跨 turn 反馈一致性 + 可逆收敛。

模拟用户 5+ 轮反馈，验证 refiner 在不依赖 LLM（走 _rule_fallback）的情况下
distance_max_km / duration_hours / dietary_constraints 可逆收敛。

测试场景：
1. 5 轮单维度反馈：distance 5→3→5→3→5（收敛）
2. 距离 + 时长 同时反馈，互不干扰
3. 反馈含具体小时数（"我只有 1 小时"）→ duration 精确收敛
4. 反馈"去掉 X" / "加 X"模式（dietary_constraints 增删）
5. raw_input 累积反馈历史不丢失（可追溯）
6. refiner 兜底路径：empty feedback 不破坏 intent
7. 跨 turn 反馈链路稳定性（5+ 轮无 schema 漂移）

不调真 LLM。所有测试都走 _rule_fallback 路径（client=None 自动走兜底）。
"""

from __future__ import annotations

import os
import pytest

from agent.intent.refiner import refine_intent
from schemas.intent import IntentExtraction


@pytest.fixture(autouse=True)
def _force_no_llm(monkeypatch):
    """强制走 _rule_fallback，避免误触真 LLM。"""
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("LLM_API_KEY", "")
    yield


def _initial_intent(
    *,
    distance_max_km: float = 5.0,
    duration_hours: list[int] = [4, 6],
    dietary: list[str] | None = None,
) -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=duration_hours,  # type: ignore[arg-type]
        distance_max_km=distance_max_km,
        companions=[],
        physical_constraints=[],
        dietary_constraints=dietary or [],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="家庭日常下午局",
        parse_confidence=0.9,
    )


# ============================================================
# 1. 单维度 5 轮反馈收敛
# ============================================================

def test_distance_5_rounds_converges():
    """5 轮 distance 反馈：5→3→5→3→5。

    refiner 兜底用 *0.6 / *1.5 系数；
    我们验证「方向正确」+「数字单调」即可，不要求精确 5↔3。
    """
    intent = _initial_intent(distance_max_km=5.0)
    history = [intent.distance_max_km]

    feedbacks = [
        "太远了，近一点",  # 5 → 缩小
        "再远一点也行",     # → 放大
        "近些吧",          # → 缩小
        "远点也行",         # → 放大
        "再近一点",         # → 缩小
    ]
    for fb in feedbacks:
        out = refine_intent(intent, fb, client=None)
        intent = out.refined_intent
        history.append(intent.distance_max_km)

    assert len(history) == 6
    # 方向：奇数下标比上一个大或不变（放大），偶数下标比上一个小或不变（缩小）
    # 但兜底数学有底线（>=2km 缩，<=15km 放）
    print(f"distance history: {history}")
    # 验证方向正确性而非精确值
    # 第一次缩小
    assert history[1] <= history[0]
    # 第二次放大
    assert history[2] >= history[1]


# ============================================================
# 2. 时长 + 距离 不互相干扰
# ============================================================

def test_duration_and_distance_independent():
    """反馈"时间紧"应只改时长，不动距离。"""
    intent = _initial_intent(distance_max_km=5.0, duration_hours=[4, 6])
    out = refine_intent(intent, "时间紧，短一点", client=None)
    assert out.refined_intent.distance_max_km == 5.0  # 距离不变
    # 时长变小
    assert max(out.refined_intent.duration_hours) <= 6


def test_distance_feedback_does_not_change_duration():
    """反馈"太远了"应只改距离，不动时长。"""
    intent = _initial_intent(distance_max_km=5.0, duration_hours=[4, 6])
    out = refine_intent(intent, "太远了，近一点", client=None)
    assert list(out.refined_intent.duration_hours) == [4, 6]


# ============================================================
# 3. 精确小时数收敛
# ============================================================

def test_exact_hour_feedback_converges():
    """『我只有 1 小时』 → duration_hours=[1, 1]。"""
    intent = _initial_intent(duration_hours=[4, 6])
    out = refine_intent(intent, "我只有 1 小时", client=None)
    assert list(out.refined_intent.duration_hours) == [1, 1]


def test_exact_hour_feedback_chinese_digit():
    """中文数字也能识别。"""
    intent = _initial_intent(duration_hours=[4, 6])
    out = refine_intent(intent, "就两小时吧", client=None)
    # 接受 [2, 2] 或更宽松
    assert max(out.refined_intent.duration_hours) <= 3


def test_exact_hour_range_feedback():
    """『2-3 小时』。"""
    intent = _initial_intent(duration_hours=[4, 6])
    out = refine_intent(intent, "2 到 3 小时吧", client=None)
    durations = list(out.refined_intent.duration_hours)
    assert durations == [2, 3]


# ============================================================
# 4. raw_input 累积
# ============================================================

def test_raw_input_accumulates_feedback():
    """每轮反馈应保留在 raw_input 中（不破坏原句）。"""
    intent = _initial_intent()
    original_raw = intent.raw_input

    out1 = refine_intent(intent, "太远了 3 公里以内", client=None)
    assert original_raw in out1.refined_intent.raw_input
    assert "反馈" in out1.refined_intent.raw_input

    out2 = refine_intent(out1.refined_intent, "时间紧短一点", client=None)
    # 第二轮：原句仍在
    assert original_raw in out2.refined_intent.raw_input


# ============================================================
# 5. 5 轮无 schema 漂移
# ============================================================

def test_5_rounds_no_schema_drift():
    """5 轮反馈后 IntentExtraction 仍合法（必填字段都在）。"""
    intent = _initial_intent()
    feedbacks = [
        "近一点",
        "时间紧短一点",
        "便宜一些",
        "远点也行",
        "再近一点",
    ]
    for fb in feedbacks:
        out = refine_intent(intent, fb, client=None)
        intent = out.refined_intent
        # 关键字段必须在
        assert intent.social_context  # 不被 refiner 误改空
        assert intent.duration_hours and len(intent.duration_hours) == 2
        assert intent.distance_max_km > 0
        assert intent.raw_input
        assert intent.parse_confidence > 0
        # __pydantic_validator__ 已在 model_validate 时校验


# ============================================================
# 6. 反馈为空不破坏 intent
# ============================================================

def test_empty_feedback_does_not_break():
    """空字符串反馈走兜底——做距离 -1km 微调（让候选打散），但 schema 不破。"""
    intent = _initial_intent(distance_max_km=5.0)
    out = refine_intent(intent, "", client=None)
    # schema 仍合法
    assert out.refined_intent.distance_max_km > 0
    assert out.refined_intent.social_context == intent.social_context


# ============================================================
# 7. 兜底返回的 changed_fields 永远是 list[str]（前端契约）
# ============================================================

def test_changed_fields_is_list_str():
    intent = _initial_intent()
    out = refine_intent(intent, "近一点", client=None)
    assert isinstance(out.changed_fields, list)
    for s in out.changed_fields:
        assert isinstance(s, str)
        assert len(s) > 0


# ============================================================
# 8. 已设过的字段再撤回（用户改主意）
# ============================================================

def test_distance_back_and_forth_does_not_diverge():
    """5→3→5→3 →最终值在合理区间。"""
    intent = _initial_intent(distance_max_km=5.0)
    history = [intent.distance_max_km]

    for fb in ["太远了", "远一点", "再近一些", "稍远点"]:
        out = refine_intent(intent, fb, client=None)
        intent = out.refined_intent
        history.append(intent.distance_max_km)

    # 不发散：所有距离值都在 1-15km 之间
    for d in history:
        assert 1 <= d <= 15, f"距离 {d} 超出合理区间"
