"""test_persona_memory —— persona 模板 + 会话私有 memory 测试。

验证：
1. 5 个 mock persona 能加载（模板，按 user_id 共享只读）
2. memory 累积 / 拒绝 / 重置正常（按 session_id 键控，会话私有）
3. compute_priors 双键合并：模板（user_id）+ 会话累积（session_id），rejected 强惩罚
4. unknown user_id 返 NOT_FOUND（保护 W1 既有断言）
5. demo_user alias 兼容兜底到 u_dad

【判据变更理由（记忆身份读写分离批，ADR-0015 身份边界补充决策，2026-07-05）】
旧判据：record_* 与 compute_priors 同用 user_id 单键——demo 单用户成立，
演示日多访客共用画像模板 id 时累积跨访客串味。新判据：
- 模板（persona）照旧按 user_id；
- 累积（accepted/rejected/distance/visited/routes）按 session_id；
- compute_priors(user_id, session_id)：session 缺省 → 纯模板视图（零累积）。
生产迁移 = 把会话键换成账号键，机制不动。
"""

from __future__ import annotations

import os

import pytest

from data.memory_store import (
    compute_priors,
    get_default_persona,
    get_persona,
    load_personas,
    record_accepted,
    record_rejected,
    reset_all_memory,
    reset_memory,
)
from schemas.errors import FailureReason
from schemas.tools import GetUserProfileInput
from tools.get_user_profile import get_user_profile


@pytest.fixture(autouse=True)
def _isolate_memory():
    # 每个测试前清掉所有 memory（防止累积污染）
    reset_all_memory()
    # 关掉磁盘持久化（用 in-memory 即可）
    os.environ.pop("SHANGWUJU_MEMORY_DIR", None)
    yield
    reset_all_memory()


# ============================================================
# 1. persona 加载（模板侧，键语义不变）
# ============================================================

def test_load_5_personas():
    personas = load_personas()
    ids = {p.user_id for p in personas}
    assert {"u_dad", "u_biz", "u_grandma", "u_solo", "u_couple"}.issubset(ids), (
        f"persona 缺失：{ids}"
    )


def test_persona_has_tags_and_notes():
    p = get_persona("u_dad")
    assert p is not None
    assert p.label
    assert p.notes
    assert p.default_tags.physical  # 至少有 1 个 tag
    assert p.default_tags.dietary
    assert "家庭日常" in p.default_tags.suitable_for_priority


def test_default_persona_falls_back():
    p = get_default_persona()
    assert p.user_id == "u_dad"


# ============================================================
# 2. memory 累积 / 拒绝（按会话键）
# ============================================================

def test_record_accepted_increments():
    m = record_accepted("sess_pm", tags=["低脂", "亲子友好"], distance_km=4.5)
    assert m.accepted_tags.counts["低脂"] == 1
    assert m.accepted_tags.counts["亲子友好"] == 1
    assert m.distance_history == [4.5]

    m = record_accepted("sess_pm", tags=["低脂"], distance_km=3.0)
    assert m.accepted_tags.counts["低脂"] == 2
    assert m.distance_history == [4.5, 3.0]


def test_record_rejected_decrements_accepted():
    """先 accept 一个 tag，再 reject，accepted 应当 -1。"""
    record_accepted("sess_pm", tags=["低脂", "低脂"])  # 累计 2 次
    m = record_rejected("sess_pm", tags=["低脂"])
    assert m.accepted_tags.counts["低脂"] == 1
    assert m.rejected_tags.counts["低脂"] == 1


def test_reset_memory_clears():
    record_accepted("sess_pm", tags=["低脂"], distance_km=5.0)
    fresh = reset_memory("sess_pm")
    assert fresh.accepted_tags.counts == {}
    assert fresh.distance_history == []


# ============================================================
# 3. compute_priors 双键合并打分
# ============================================================

def test_priors_persona_default_in_top():
    """新会话零累积（不传 session），priors 应当全来自 persona 模板 tag。"""
    view = compute_priors("u_dad")
    assert view.persona.user_id == "u_dad"
    # 至少包含 persona 默认 tag 中的 1 个
    persona_tags = (
        view.persona.default_tags.physical
        + view.persona.default_tags.dietary
        + view.persona.default_tags.experience
    )
    assert any(t in view.top_priors for t in persona_tags)


def test_priors_memory_overwhelms_persona():
    """会话累积多次后，应当排在 persona 模板 tag 之前。"""
    # 给会话累 5 次"商务体面"（不在 u_dad 模板里）
    for _ in range(5):
        record_accepted("sess_pm", tags=["商务体面"])
    view = compute_priors("u_dad", "sess_pm")
    # 商务体面应排在 top_priors 里（5 * 0.7 = 3.5，超过 persona base 3 * 0.3 = 0.9）
    assert "商务体面" in view.top_priors


def test_priors_session_scoped_no_leak():
    """本批核心不变式：A 会话的累积不得进入 B 会话（或无会话）的合并视图。"""
    for _ in range(5):
        record_accepted("sess_visitor_a", tags=["商务体面"])
    assert "商务体面" not in compute_priors("u_dad", "sess_visitor_b").top_priors
    assert "商务体面" not in compute_priors("u_dad").top_priors, (
        "不传 session（模板视图）不得混入任何会话的累积"
    )


def test_priors_rejected_punished():
    """rejected 应强惩罚到不进 top_priors。"""
    # u_dad 模板含「低脂」（base 3 * 0.3 = 0.9）
    record_rejected("sess_pm", tags=["低脂", "低脂", "低脂"])  # 3 次 reject
    view = compute_priors("u_dad", "sess_pm")
    # 低脂应被惩罚出 top_priors
    assert "低脂" not in view.top_priors


def test_priors_suggested_distance():
    """会话内 memory 中位数距离应作为 suggested_distance。"""
    record_accepted("sess_pm", tags=[], distance_km=2.0)
    record_accepted("sess_pm", tags=[], distance_km=3.0)
    record_accepted("sess_pm", tags=[], distance_km=4.0)
    view = compute_priors("u_dad", "sess_pm")
    assert view.suggested_distance_max_km == 3.0  # 中位数


# ============================================================
# 4. get_user_profile Tool 行为（W1 兼容）
# ============================================================

def test_get_user_profile_unknown_user_returns_not_found():
    """W1 旧测试：未知 user_id 必须返 NOT_FOUND。"""
    out = get_user_profile(GetUserProfileInput(user_id="someone_else"))
    assert not out.success
    assert out.reason == FailureReason.NOT_FOUND


def test_get_user_profile_demo_user_alias():
    """demo_user alias 兜底到 u_dad，但 user_id 字段保留 demo_user（W1 测试断言）。"""
    out = get_user_profile(GetUserProfileInput(user_id="demo_user"))
    assert out.success
    assert out.profile.user_id == "demo_user"


def test_get_user_profile_persona_id_works():
    """新接口：直接传 persona id 也成功。"""
    out = get_user_profile(GetUserProfileInput(user_id="u_biz"))
    assert out.success
    assert out.profile.user_id == "u_biz"
    assert out.profile.default_budget >= 500  # 商务白领默认预算高
