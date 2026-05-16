"""test_persona_memory —— Phase 0.7 persona + memory 测试。

验证：
1. 5 个 mock persona 能加载
2. memory 累积 / 拒绝 / 重置正常
3. compute_priors 按权重合并 persona + memory，rejected 强惩罚
4. unknown user_id 返 NOT_FOUND（保护 W1 既有断言）
5. demo_user alias 兼容兜底到 u_dad
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
# 1. persona 加载
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
# 2. memory 累积 / 拒绝
# ============================================================

def test_record_accepted_increments():
    m = record_accepted("u_dad", tags=["低脂", "亲子友好"], distance_km=4.5)
    assert m.accepted_tags.counts["低脂"] == 1
    assert m.accepted_tags.counts["亲子友好"] == 1
    assert m.distance_history == [4.5]

    m = record_accepted("u_dad", tags=["低脂"], distance_km=3.0)
    assert m.accepted_tags.counts["低脂"] == 2
    assert m.distance_history == [4.5, 3.0]


def test_record_rejected_decrements_accepted():
    """先 accept 一个 tag，再 reject，accepted 应当 -1。"""
    record_accepted("u_dad", tags=["低脂", "低脂"])  # 累计 2 次
    m = record_rejected("u_dad", tags=["低脂"])
    assert m.accepted_tags.counts["低脂"] == 1
    assert m.rejected_tags.counts["低脂"] == 1


def test_reset_memory_clears():
    record_accepted("u_dad", tags=["低脂"], distance_km=5.0)
    fresh = reset_memory("u_dad")
    assert fresh.accepted_tags.counts == {}
    assert fresh.distance_history == []


# ============================================================
# 3. compute_priors 合并打分
# ============================================================

def test_priors_persona_default_in_top():
    """新 user 还没 memory，priors 应当全是 persona default tag。"""
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
    """memory 累积多次后，应当排在 persona default 之前。"""
    # u_dad 默认含「亲子友好」「低脂」等
    # 给 u_dad 累 5 次"商务体面"（不在 persona default 里）
    for _ in range(5):
        record_accepted("u_dad", tags=["商务体面"])
    view = compute_priors("u_dad")
    # 商务体面应排在 top_priors 里（5 * 0.7 = 3.5，超过 persona base 3 * 0.3 = 0.9）
    assert "商务体面" in view.top_priors


def test_priors_rejected_punished():
    """rejected 应强惩罚到不进 top_priors。"""
    # u_dad 默认含「低脂」（base 3 * 0.3 = 0.9）
    record_rejected("u_dad", tags=["低脂", "低脂", "低脂"])  # 3 次 reject
    view = compute_priors("u_dad")
    # 低脂应被惩罚出 top_priors
    assert "低脂" not in view.top_priors


def test_priors_suggested_distance():
    """memory 中位数距离应作为 suggested_distance。"""
    record_accepted("u_dad", tags=[], distance_km=2.0)
    record_accepted("u_dad", tags=[], distance_km=3.0)
    record_accepted("u_dad", tags=[], distance_km=4.0)
    view = compute_priors("u_dad")
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
