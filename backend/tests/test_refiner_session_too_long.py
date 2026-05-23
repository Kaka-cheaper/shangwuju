"""tests.test_refiner_session_too_long —— spec planning-quality-deep-review R8（Task 7）。

验 Refiner / Feedback / Persona 三处「单段太长」反馈链路：

1. _extract_duration_from_feedback 识别「半小时」/「30 分钟」三类正则
2. _extract_duration_from_feedback 识别「一个半小时」/「1.5 小时」/「1 个半小时」类
3. _rule_fallback 命中 SESSION_TOO_LONG 关键词 → 输出 pace_profile.single_session_max_min 缩 30%
4. _rule_fallback 命中 SESSION_TOO_LONG → **不动** distance_max_km / duration_hours
5. 跨持久化反馈合并：原 IntentExtraction 已有 pace_profile → 在原值基础上缩 30%
6. build_intent_parser_system_prompt_with_priors 注入 persona.default_pace_profile 命中
   prompt addendum 含「档案默认节奏」段
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
from agent.intent.prompts.intent_parser_prompt import (  # noqa: E402
    INTENT_PARSER_SYSTEM_PROMPT,
    build_intent_parser_system_prompt_with_priors,
)
from agent.intent.refiner import (  # noqa: E402
    _extract_duration_from_feedback,
    _rule_fallback,
)
from schemas.intent import Companion, IntentExtraction  # noqa: E402
from schemas.persona import (  # noqa: E402
    PaceProfile,
    Persona,
    PersonaDefaultTags,
    UserMemory,
    UserPreferenceView,
)


# ============================================================
# 共享 fixture
# ============================================================


def _intent(
    *,
    duration: list[int] | None = None,
    distance: float = 5.0,
    pace: PaceProfile | None = None,
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
        pace_profile=pace,
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
# 3) _rule_fallback：SESSION_TOO_LONG 命中 → pace_profile 缩 30%
# ============================================================


def test_rule_fallback_session_too_long_shrinks_pace() -> None:
    """spec R8：用户说「太久」→ pace_profile.single_session_max_min × 0.7。"""
    original = _intent(
        duration=[4, 6],
        distance=5.0,
        pace=PaceProfile(single_session_max_min=90),
    )
    out = _rule_fallback(original, "这段太久了")

    pace = out.refined_intent.pace_profile
    assert pace is not None, "应当生成 pace_profile"
    # 90 * 0.7 = 63 → int(63)
    assert pace.single_session_max_min == 63, (
        f"应缩 30%，原 90 → {pace.single_session_max_min}"
    )
    # changed_fields 含变更说明
    assert any("单段时长" in c and "90" in c and "63" in c for c in out.changed_fields), (
        f"changed_fields 应含单段时长缩比说明：{out.changed_fields}"
    )


def test_rule_fallback_session_too_long_without_existing_pace_uses_default() -> None:
    """原 intent 没 pace_profile → 用 _DEFAULT_SESSION_MAX_MIN(90) 起步缩。"""
    original = _intent(duration=[4, 6], distance=5.0, pace=None)
    out = _rule_fallback(original, "看不下去了，盯不住")

    pace = out.refined_intent.pace_profile
    assert pace is not None
    assert pace.single_session_max_min == 63  # 90 * 0.7 = 63


# ============================================================
# 4) _rule_fallback：SESSION_TOO_LONG **不动** distance_max_km / duration_hours
# ============================================================


def test_rule_fallback_session_too_long_does_not_change_distance_or_duration() -> None:
    """spec R8 task 描述硬约束：单段太长反馈**不动**总时长 / 距离。"""
    original = _intent(
        duration=[4, 6],
        distance=5.0,
        pace=PaceProfile(single_session_max_min=90),
    )
    out = _rule_fallback(original, "太久了")

    refined = out.refined_intent
    # 关键断言：distance / duration_hours 都保留原值
    assert refined.distance_max_km == 5.0, (
        f"distance_max_km 应保持原值 5.0，实际 {refined.distance_max_km}"
    )
    assert list(refined.duration_hours) == [4, 6], (
        f"duration_hours 应保持原值 [4,6]，实际 {refined.duration_hours}"
    )

    # changed_fields 不含距离 / 总时长字样
    joined = " | ".join(out.changed_fields)
    assert "距离上限" not in joined, f"不应触发距离调整：{out.changed_fields}"
    # 注意 changed_fields 里有"单段时长"是合法的；只确保不含"总时长"或纯"时长：[" 这种总时长格式
    assert "[4, 6] →" not in joined and "[4, 6]→" not in joined, (
        f"不应触发总时长调整：{out.changed_fields}"
    )


# ============================================================
# 5) 跨持久化反馈合并：原 pace_profile 已有节奏 → 累积缩
# ============================================================


def test_rule_fallback_session_too_long_persistent_compounding() -> None:
    """模拟两轮反馈：第一轮 90→63；第二轮在 63 基础上再缩 30% → 44。"""
    intent_after_round1 = _intent(
        duration=[4, 6],
        distance=5.0,
        pace=PaceProfile(single_session_max_min=63),  # 第一轮反馈后的状态
    )
    out = _rule_fallback(intent_after_round1, "还是太久了")

    pace = out.refined_intent.pace_profile
    assert pace is not None
    # 63 * 0.7 = 44.1 → int(44)
    assert pace.single_session_max_min == 44, (
        f"二次反馈应在已缩值上再缩 30%，预期 44，实际 {pace.single_session_max_min}"
    )

    # **不**清空原 pace_profile 的其他字段（如果有）
    intent_with_other_fields = _intent(
        duration=[4, 6],
        distance=5.0,
        pace=PaceProfile(
            single_session_max_min=90,
            total_active_min=240,
            break_every_min=45,
            preferred_dwell_min=60,
        ),
    )
    out2 = _rule_fallback(intent_with_other_fields, "太长了")
    pace2 = out2.refined_intent.pace_profile
    assert pace2 is not None
    assert pace2.single_session_max_min == 63  # 缩
    # 其他字段不被清空
    assert pace2.total_active_min == 240, "total_active_min 不应被清空"
    assert pace2.break_every_min == 45, "break_every_min 不应被清空"
    assert pace2.preferred_dwell_min == 60, "preferred_dwell_min 不应被清空"


# ============================================================
# 6) Persona pace_profile 注入 build_intent_parser_system_prompt_with_priors
# ============================================================


class _FakePersona:
    """轻量替身，避免触碰真实 mock_data 路径。"""


def _make_view_with_pace(pace: PaceProfile | None) -> UserPreferenceView:
    persona = Persona(
        user_id="u_test",
        label="测试用户",
        icon="🧪",
        notes="测试节奏注入",
        home_location="testland",
        default_distance_max_km=5.0,
        default_budget=300.0,
        default_tags=PersonaDefaultTags(
            physical=["亲子友好"],
            dietary=["低脂"],
            experience=[],
            suitable_for_priority=["家庭日常"],
        ),
        default_pace_profile=pace,
    )
    memory = UserMemory(user_id="u_test")
    return UserPreferenceView(
        persona=persona,
        memory=memory,
        top_priors=["亲子友好", "低脂"],
        suggested_distance_max_km=5.0,
    )


def test_persona_pace_profile_injection_hits_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """spec R8：persona.default_pace_profile 非空 → prompt 含『档案默认节奏』段 + 字段值。"""
    pace = PaceProfile(
        single_session_max_min=75,
        break_every_min=45,
        preferred_dwell_min=60,
    )
    fake_view = _make_view_with_pace(pace)

    def _fake_compute_priors(_user_id: str) -> UserPreferenceView:
        return fake_view

    monkeypatch.setattr(
        "data.memory_store.compute_priors",
        _fake_compute_priors,
        raising=True,
    )

    prompt = build_intent_parser_system_prompt_with_priors("u_test")
    # 基础 prompt 仍在
    assert prompt.startswith(INTENT_PARSER_SYSTEM_PROMPT)
    # pace_profile prior 段命中
    assert "档案默认节奏" in prompt, "应注入『档案默认节奏』段"
    assert "75" in prompt, "single_session_max_min=75 应出现"
    assert "45" in prompt, "break_every_min=45 应出现"
    assert "60" in prompt, "preferred_dwell_min=60 应出现"
    # pace_profile 注入规则段命中（spec R8）
    assert "pace_profile" in prompt
    assert "spec planning-quality-deep-review R8" in prompt or "R8" in prompt


def test_persona_pace_profile_none_does_not_inject(monkeypatch: pytest.MonkeyPatch) -> None:
    """persona.default_pace_profile 为空 → 不注入『档案默认节奏（pace_profile prior）：』段。

    注：addendum 规则段里仍会出现 "档案默认节奏" 字样作为引用说明，
    但**实际节奏值段**「档案默认节奏（pace_profile prior）：xxx」不会出现。
    """
    fake_view = _make_view_with_pace(None)

    def _fake_compute_priors(_user_id: str) -> UserPreferenceView:
        return fake_view

    monkeypatch.setattr(
        "data.memory_store.compute_priors",
        _fake_compute_priors,
        raising=True,
    )

    prompt = build_intent_parser_system_prompt_with_priors("u_test")
    # pace=None 时不应有具体节奏值段（带括号的"（pace_profile prior）"才是数据段标志）
    assert "档案默认节奏（pace_profile prior）" not in prompt, (
        "pace=None 时不应注入具体节奏值段"
    )
    # 也不应出现具体分钟数（无 prior 时 prompt 里不会有 75 / 90 等数值）
    # 但注入规则段本身是常驻的，所以 "pace_profile" 字符串会有 → 不可作断言


# ============================================================
# 7) feedback_detector 同步含 SESSION_TOO_LONG 关键词
# ============================================================


@pytest.mark.parametrize(
    "txt",
    ["太久", "太长", "盯不住", "无聊", "扛不住", "腻了", "这段太久了", "看着盯不住"],
)
def test_feedback_detector_recognizes_session_too_long(txt: str) -> None:
    """spec R8：feedback_detector.looks_like_feedback 必须识别『单段太长』类反馈。"""
    assert looks_like_feedback(txt) is True, f"应识别为反馈：{txt!r}"
