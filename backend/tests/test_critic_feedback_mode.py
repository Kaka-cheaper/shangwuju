"""spec algorithm-redesign R1：critics_v2 三档反馈模式 + compute_reward 数值验证。

测试覆盖：
- compute_reward 公式正确性（CRITICAL × INVARIANT_BROKEN(1.5) = -1.5；空列表 = 0.0）
- compute_reward 多违规累加
- _get_feedback_mode env 解析（默认 / 越界 fallback）
- format_violations_for_llm 三档行为差异
- 默认值 pinpoint-all 与 spec A 之前行为完全一致（diff 为空）

不消费真 LLM；不依赖 mock_data；纯单元测试。
"""

from __future__ import annotations

import os

import pytest

from agent.planning.critic.critics_v2 import (
    CODE_WEIGHTS,
    SEVERITY_WEIGHTS,
    Severity,
    Violation,
    ViolationCode,
    _get_feedback_mode,
    compute_reward,
    format_violations_for_llm,
)


# ============================================================
# Fixture: 构造 violations 用例
# ============================================================


def _make_violation(
    code: ViolationCode,
    severity: Severity,
    message: str = "测试违规",
) -> Violation:
    return Violation(
        code=code,
        severity=severity,
        message=message,
        field_path="test.path",
    )


@pytest.fixture
def critical_invariant() -> Violation:
    """CRITICAL × INVARIANT_BROKEN(1.5) = 1.5"""
    return _make_violation(
        ViolationCode.INVARIANT_BROKEN,
        Severity.CRITICAL,
        "首节点必须是 home",
    )


@pytest.fixture
def warning_distance() -> Violation:
    """WARNING(0.2) × DISTANCE_EXCEEDED(0.8) = 0.16"""
    return _make_violation(
        ViolationCode.DISTANCE_EXCEEDED,
        Severity.WARNING,
        "第 2 段距家 8.5km，超过用户期望 5.0km",
    )


@pytest.fixture
def critical_age() -> Violation:
    """CRITICAL × AGE_DURATION_MISMATCH(default 1.0) = 1.0"""
    return _make_violation(
        ViolationCode.AGE_DURATION_MISMATCH,
        Severity.CRITICAL,
        "第 3 段停留 196 分钟，超出 5 岁孩童约束",
    )


# ============================================================
# 测试 1：compute_reward 数值正确性
# ============================================================


def test_compute_reward_empty_list_returns_zero():
    """空列表 → 0.0"""
    assert compute_reward([]) == 0.0


def test_compute_reward_critical_invariant_value(critical_invariant):
    """CRITICAL × INVARIANT_BROKEN(1.5) = -1.5"""
    reward = compute_reward([critical_invariant])
    assert reward == -1.5


def test_compute_reward_warning_distance_small_negative(warning_distance):
    """WARNING(0.2) × DISTANCE_EXCEEDED(0.8) = -0.16

    验证 macro 设计目标：单条 WARNING 显著 < 单条 CRITICAL（避免逆优先级）
    """
    reward = compute_reward([warning_distance])
    assert reward == pytest.approx(-0.16, abs=1e-6)
    assert abs(reward) < 0.4, "WARNING 单条应 < 0.4（CODE_WEIGHTS macro 设计）"


def test_compute_reward_critical_dominates_warning(
    critical_invariant, warning_distance
):
    """单条 CRITICAL ≥ 1.5；任何 WARNING ≤ 0.4 —— macro 设计目标"""
    crit = compute_reward([critical_invariant])
    warn = compute_reward([warning_distance])
    assert abs(crit) >= 1.5
    assert abs(warn) <= 0.4
    assert abs(crit) > abs(warn) * 5, "CRITICAL 至少 5 倍于 WARNING"


def test_compute_reward_multi_violations_accumulate(
    critical_invariant, warning_distance, critical_age
):
    """多违规累加：1.5 + 0.16 + 1.0 = 2.66"""
    reward = compute_reward([critical_invariant, warning_distance, critical_age])
    expected = -(1.5 + 0.16 + 1.0)
    assert reward == pytest.approx(expected, abs=1e-6)


def test_compute_reward_unknown_code_uses_default_weight():
    """CODE_WEIGHTS 不含的 code 走 dict.get(code, 1.0) 兜底"""
    # SOCIAL_CONTEXT_MISMATCH 不在 CODE_WEIGHTS，应取默认 1.0
    v = _make_violation(
        ViolationCode.SOCIAL_CONTEXT_MISMATCH,
        Severity.CRITICAL,
        "调性不匹配",
    )
    reward = compute_reward([v])
    # CRITICAL(1.0) × default(1.0) = 1.0
    assert reward == -1.0


# ============================================================
# 测试 2：_get_feedback_mode env 解析
# ============================================================


def test_feedback_mode_default_is_pinpoint_all(monkeypatch):
    """env 不设 → 默认 pinpoint-all"""
    monkeypatch.delenv("CRITIC_FEEDBACK_MODE", raising=False)
    assert _get_feedback_mode() == "pinpoint-all"


def test_feedback_mode_first_only(monkeypatch):
    monkeypatch.setenv("CRITIC_FEEDBACK_MODE", "first-only")
    assert _get_feedback_mode() == "first-only"


def test_feedback_mode_reward(monkeypatch):
    monkeypatch.setenv("CRITIC_FEEDBACK_MODE", "reward")
    assert _get_feedback_mode() == "reward"


def test_feedback_mode_invalid_falls_back_to_pinpoint_all(monkeypatch, capsys):
    """typo / 越界值 → fallback pinpoint-all + stderr warn"""
    monkeypatch.setenv("CRITIC_FEEDBACK_MODE", "RL-mode")
    mode = _get_feedback_mode()
    assert mode == "pinpoint-all"
    captured = capsys.readouterr()
    assert "rl-mode" in captured.err.lower() or "RL-mode" in captured.err
    assert "pinpoint-all" in captured.err


def test_feedback_mode_case_insensitive(monkeypatch):
    """大写值会被 .lower() 规范化"""
    monkeypatch.setenv("CRITIC_FEEDBACK_MODE", "FIRST-ONLY")
    assert _get_feedback_mode() == "first-only"


# ============================================================
# 测试 3：format_violations_for_llm 三档输出差异
# ============================================================


def test_format_pinpoint_all_full_list(
    monkeypatch, critical_invariant, critical_age
):
    """pinpoint-all 模式输出完整 critical 列表"""
    monkeypatch.setenv("CRITIC_FEEDBACK_MODE", "pinpoint-all")
    out = format_violations_for_llm([critical_invariant, critical_age])
    assert "2 处违规" in out
    assert "1." in out and "2." in out
    assert "首节点必须是 home" in out
    assert "5 岁孩童" in out


def test_format_first_only_truncates_to_first_critical(
    monkeypatch, critical_invariant, critical_age
):
    """first-only 模式仅列第一条 critical"""
    monkeypatch.setenv("CRITIC_FEEDBACK_MODE", "first-only")
    out = format_violations_for_llm([critical_invariant, critical_age])
    assert "1 处违规" in out
    assert "1." in out
    assert "2." not in out
    assert "首节点必须是 home" in out
    assert "5 岁孩童" not in out  # 第二条被截掉


def test_format_reward_mode_returns_empty(
    monkeypatch, critical_invariant, critical_age
):
    """reward 模式返空字符串（dense scalar 模式由调用方独立调 compute_reward）"""
    monkeypatch.setenv("CRITIC_FEEDBACK_MODE", "reward")
    out = format_violations_for_llm([critical_invariant, critical_age])
    assert out == ""


def test_format_no_critical_returns_empty_in_all_modes(
    monkeypatch, warning_distance
):
    """0 critical → 三档都返空（warning 不进 backprompt）"""
    for mode in ("pinpoint-all", "first-only", "reward"):
        monkeypatch.setenv("CRITIC_FEEDBACK_MODE", mode)
        out = format_violations_for_llm([warning_distance])
        assert out == "", f"mode={mode} 应返空（仅 warning 时）"


# ============================================================
# 测试 4：默认行为与 spec A 之前一致（diff 为空）
# ============================================================


def test_default_format_unchanged_from_spec_a(
    monkeypatch, critical_invariant, critical_age
):
    """env 不设 → format 行为与 spec A 完全一致

    这条测试是「向后兼容硬约束」：spec C 三档 mode 引入不能破坏现有 470+ 项 pytest。
    """
    monkeypatch.delenv("CRITIC_FEEDBACK_MODE", raising=False)
    out = format_violations_for_llm([critical_invariant, critical_age])
    # 与 spec A 一致：完整 2 条 + 编号 + 修复指引收尾
    assert "2 处违规" in out
    assert "1." in out and "2." in out
    assert out.endswith("重新输出 ItineraryResponse。")


# ============================================================
# 测试 5：常量 SEVERITY_WEIGHTS / CODE_WEIGHTS 设计正确性
# ============================================================


def test_severity_weights_critical_5x_warning():
    """CRITICAL 是 WARNING 的 5 倍（1.0 / 0.2 = 5）

    这条断言固化设计意图：避免「100 个 warning 加起来反而比 1 个 critical 还重」
    的逆优先级失败模式。
    """
    assert SEVERITY_WEIGHTS[Severity.CRITICAL] == 1.0
    assert SEVERITY_WEIGHTS[Severity.WARNING] == 0.2
    ratio = SEVERITY_WEIGHTS[Severity.CRITICAL] / SEVERITY_WEIGHTS[Severity.WARNING]
    assert ratio == 5.0


def test_code_weights_macro_15_micro_08():
    """macro 级（结构性 / 节点完整性 / 时序）取 1.5；细粒度取 0.8"""
    # macro 级
    assert CODE_WEIGHTS[ViolationCode.INVARIANT_BROKEN] == 1.5
    assert CODE_WEIGHTS[ViolationCode.NODES_INCOMPLETE] == 1.5
    assert CODE_WEIGHTS[ViolationCode.TIMELINE_INCONSISTENT] == 1.5
    # 细粒度
    assert CODE_WEIGHTS[ViolationCode.DIETARY_VIOLATION] == 0.8
    assert CODE_WEIGHTS[ViolationCode.DISTANCE_EXCEEDED] == 0.8


def test_compute_reward_macro_dominates_micro():
    """单条 macro CRITICAL ≥ 单条 micro CRITICAL × 1.5

    防御性断言：CODE_WEIGHTS 调整后此关系应保持。
    """
    macro = _make_violation(
        ViolationCode.INVARIANT_BROKEN, Severity.CRITICAL, "macro"
    )
    # DURATION_OUT_OF_RANGE 不在 CODE_WEIGHTS，走 default 1.0
    micro_default = _make_violation(
        ViolationCode.DURATION_OUT_OF_RANGE, Severity.CRITICAL, "micro"
    )
    # DIETARY_VIOLATION 在 CODE_WEIGHTS = 0.8
    micro_explicit = _make_violation(
        ViolationCode.DIETARY_VIOLATION, Severity.CRITICAL, "micro2"
    )
    assert abs(compute_reward([macro])) > abs(compute_reward([micro_default]))
    assert abs(compute_reward([macro])) > abs(compute_reward([micro_explicit]))
