"""tests.test_refiner_session_too_long —— spec planning-quality-deep-review R8（Task 7）
+ ADR-0014 G-0（2026-07-03）迁移。

验 Refiner / Feedback 两处「单段太长」反馈链路：

1. _extract_duration_from_feedback 识别「半小时」/「30 分钟」三类正则
2. _extract_duration_from_feedback 识别「一个半小时」/「1.5 小时」/「1 个半小时」类
3. _rule_fallback 命中 SESSION_TOO_LONG 关键词 → duration_hours 上界缩 30%
   （ADR-0014 G-0 迁移：原缩的是 pace_profile.single_session_max_min，该字段全系统
   无消费方，业务空转；迁移到 duration_hours 上界，规划器拿它定总时长硬预算，是
   真实消费——"太久了"才有"行程真的变短"的用户可见效果）
4. _rule_fallback 命中 SESSION_TOO_LONG → 下限保护（不缩过 duration_hours 下界/
   1 小时地板）；explicit 数字反馈优先于关键词猜的收缩比例，两者不打架
5. feedback_detector 同步含 SESSION_TOO_LONG 关键词
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


# ============================================================
# stub 桥（与 test_age_aware_critic.py 同款，避免 agent/__init__.py eager-import 老 schema 炸）
# ============================================================


def _install_agent_stub() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    agent_dir = backend_root / "agent"

    if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
        stub = types.ModuleType("agent")
        stub.__path__ = [str(agent_dir)]
        sys.modules["agent"] = stub


_install_agent_stub()

from agent.core.feedback_detector import looks_like_feedback  # noqa: E402
from agent.intent.refiner import (  # noqa: E402
    _extract_duration_from_feedback,
    _rule_fallback,
)
from schemas.intent import Companion, IntentExtraction  # noqa: E402


# ============================================================
# 共享 fixture
# ============================================================


def _intent(
    *,
    duration: list[int] | None = None,
    distance: float = 5.0,
) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=list(duration or [4, 6]),
        distance_max_km=distance,
        companions=[Companion(role="孩子", age=5, count=1)],
        physical_constraints=["亲子友好"],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="今天下午带 5 岁娃出去玩",
        parse_confidence=0.92,
    )


# ============================================================
# 1) _extract_duration_from_feedback：半小时 / 30 分钟
# ============================================================


@pytest.mark.parametrize(
    "feedback,expected",
    [
        ("半小时", (0, 1)),
        ("半小时差不多", (0, 1)),
        ("30 分钟", (0, 1)),
        ("30分钟", (0, 1)),
        ("45 分钟", (0, 1)),
        ("20分钟就行", (0, 1)),
    ],
)
def test_extract_half_hour_and_minutes(feedback: str, expected: tuple[int, int]) -> None:
    """spec R8：扩展支持「半小时 / N 分钟」（N < 60 → (0, 1)）。"""
    assert _extract_duration_from_feedback(feedback) == expected


# ============================================================
# 2) _extract_duration_from_feedback：一个半小时 / 1.5 小时
# ============================================================


@pytest.mark.parametrize(
    "feedback,expected",
    [
        ("一个半小时", (1, 2)),
        ("一个半", (1, 2)),
        ("1 个半小时", (1, 2)),
        ("1.5 小时", (1, 2)),
        ("1.5小时", (1, 2)),
        ("我有 1 个半小时", (1, 2)),
    ],
)
def test_extract_one_and_half(feedback: str, expected: tuple[int, int]) -> None:
    """spec R8：扩展支持「一个半小时 / 1.5 小时」→ (1, 2)。"""
    assert _extract_duration_from_feedback(feedback) == expected


# ============================================================
# 3) _rule_fallback：SESSION_TOO_LONG 命中 → duration_hours 上界缩 30%
#    （ADR-0014 G-0 迁移探针：收缩契约从死字段 pace_profile 迁到真实消费的
#    duration_hours；"用户可见效果=行程真的变短"）
# ============================================================


def test_rule_fallback_session_too_long_shrinks_duration_upper_bound() -> None:
    """迁移探针：用户说「太久了」→ duration_hours 上界（不是下界）缩 30%。"""
    original = _intent(duration=[4, 6], distance=5.0)
    out = _rule_fallback(original, "这段太久了")

    refined = out.refined_intent
    # 下界不动，只收上界——[4, 6] → [4, 4]（6 * 0.7 = 4.2 → round 4；
    # 但下限保护要求不低于下界 4，恰好落在 4）
    assert refined.duration_hours[0] == 4, "下界不应被『太久了』反馈改动"
    assert refined.duration_hours[1] < 6, (
        f"上界应缩小，实际仍是 {refined.duration_hours[1]}"
    )
    assert refined.duration_hours[1] == 4, (
        f"6h × 0.7 = 4.2 → round 4，应为 4，实际 {refined.duration_hours[1]}"
    )

    # changed_fields 含变更说明（人话可读，提到具体小时数）
    assert any("6" in c and "4" in c for c in out.changed_fields), (
        f"changed_fields 应含时长上界收缩说明：{out.changed_fields}"
    )


def test_rule_fallback_session_too_long_does_not_touch_distance() -> None:
    """『太久了』只应影响 duration_hours，不应连带改 distance_max_km。"""
    original = _intent(duration=[4, 6], distance=5.0)
    out = _rule_fallback(original, "太久了")

    assert out.refined_intent.distance_max_km == 5.0, (
        f"distance_max_km 应保持原值 5.0，实际 {out.refined_intent.distance_max_km}"
    )
    assert "距离上限" not in " | ".join(out.changed_fields)


def test_rule_fallback_session_too_long_compounds_across_rounds() -> None:
    """多轮反馈应在上一轮结果基础上继续收（只要还没触底 lo）。"""
    original = _intent(duration=[1, 10], distance=5.0)
    out1 = _rule_fallback(original, "太久了")
    assert out1.refined_intent.duration_hours == [1, 7], (
        f"10h × 0.7 = 7 → 应为 [1, 7]，实际 {out1.refined_intent.duration_hours}"
    )

    out2 = _rule_fallback(out1.refined_intent, "还是太久")
    assert out2.refined_intent.duration_hours == [1, 5], (
        f"7h × 0.7 = 4.9 → round 5 → 应为 [1, 5]，实际 {out2.refined_intent.duration_hours}"
    )


# ============================================================
# 4) 下限保护：不缩过 duration_hours 下界 / 1 小时地板；
#    explicit 数字反馈优先于关键词收缩（不打架）
# ============================================================


def test_rule_fallback_session_too_long_floor_protection_stops_at_lower_bound() -> None:
    """已经缩到下界后，再命中『太久』不应继续缩、也不应产生一条空洞的时长变更。"""
    original = _intent(duration=[4, 4], distance=5.0)  # 上下界已相等
    out = _rule_fallback(original, "还是太久了")

    assert out.refined_intent.duration_hours == [4, 4], (
        "已在下界地板，不应再被『太久』反馈继续收缩"
    )
    joined = " | ".join(out.changed_fields)
    assert "时长上界" not in joined, f"已触底不应再报时长上界变更：{out.changed_fields}"


def test_rule_fallback_explicit_hour_number_wins_over_session_too_long_keyword() -> None:
    """反馈同时含具体小时数与『太久』关键词时，精确数字优先，不被关键词收缩覆盖。"""
    original = _intent(duration=[4, 6], distance=5.0)
    out = _rule_fallback(original, "太久了，只要 1 小时就行")

    assert list(out.refined_intent.duration_hours) == [1, 1], (
        f"显式『1 小时』应优先生效，实际 {out.refined_intent.duration_hours}"
    )


# ============================================================
# 5) feedback_detector 同步含 SESSION_TOO_LONG 关键词
# ============================================================


@pytest.mark.parametrize(
    "txt",
    ["太久", "太长", "盯不住", "无聊", "扛不住", "腻了", "这段太久了", "看着盯不住"],
)
def test_feedback_detector_recognizes_session_too_long(txt: str) -> None:
    """spec R8：feedback_detector.looks_like_feedback 必须识别『单段太长』类反馈。"""
    assert looks_like_feedback(txt) is True, f"应识别为反馈：{txt!r}"
