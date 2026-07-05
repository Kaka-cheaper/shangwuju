"""recent_trips + dietary_preference 召回 prompt 注入测试（记忆身份读写分离批重钉）。

【判据变更理由（ADR-0015 身份边界补充决策，2026-07-05）】
旧判据：_build_user_profile_addendum() 从全局 user_profile.json 读 recent_trips
注入 prompt——单用户假设下成立；多访客并发时 A 确认的行程会注进 B 的意图解析。
新判据：召回按 **session_id 键控**——
- dietary_preference：仍从 user_profile.json 模板读（共享只读，模板不是累积）；
- recent_trips：只从 data.memory_store 的会话私有档案读；**种子文件里的
  recent_trips 不再注入**（隐私式诚实：新会话零累积就该零召回，不能拿全局
  种子冒充"你上次去过"）。

不消费真 LLM；只测 prompt builder 的输出文本。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.intent.prompts.intent_parser_prompt import (  # noqa: E402
    INTENT_PARSER_SYSTEM_PROMPT,
    _build_user_profile_addendum,
    build_intent_parser_system_prompt_with_priors,
)
from data.memory_store import record_recent_trip, reset_all_memory  # noqa: E402
from schemas.domain import RecentTrip  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_memory():
    reset_all_memory()
    yield
    reset_all_memory()


def _seed_trip(session_id: str, *, social_context: str = "家庭日常", summary: str = "亲子博物馆一下午") -> None:
    record_recent_trip(
        session_id,
        RecentTrip(
            timestamp="2026-07-01T10:00:00Z",
            social_context=social_context,
            summary=summary,
            success=True,
        ),
    )


# ============================================================
# 测试 1：dietary_preference 注入 prompt（模板侧，键语义不变）
# ============================================================


def test_dietary_preference_injected():
    """user_profile.json 模板含 dietary_preference → 任意会话的 prompt 都含「饮食偏好」段。"""
    addendum = _build_user_profile_addendum("sess_any")
    if not addendum:
        pytest.skip("mock_data/user_profile.json 不含 dietary_preference 字段")
    assert "饮食偏好" in addendum
    assert "健康轻食" in addendum or "辣度" in addendum


# ============================================================
# 测试 2：recent_trips 只从会话私有档案注入
# ============================================================


def test_recent_trips_injected_from_session_store():
    """本会话有确认档案 → prompt 包含「最近行程」段（召回内容来自该会话）。"""
    _seed_trip("sess_recall", summary="家庭日常场景行程：活动 → 用餐")
    addendum = _build_user_profile_addendum("sess_recall")
    assert "最近行程" in addendum
    assert "家庭日常" in addendum


def test_recent_trips_capped_at_2_entries():
    """最多注入最新 2 条（避免 prompt 过长）——策略原样保留，仅存储改会话私有。"""
    for sc in ("家庭日常", "朋友热闹", "情侣亲密"):
        _seed_trip("sess_cap", social_context=sc)
    addendum = _build_user_profile_addendum("sess_cap")
    assert addendum.count("场景：") <= 2


def test_fresh_session_gets_no_recent_trips_even_if_seed_has_them():
    """核心隐私不变式：新会话零累积 → 零召回。

    种子 user_profile.json 里预置的 recent_trips **不得**注入——那是全局演示
    数据，不是"这位访客"的历史；拿它冒充记忆正是本批要根治的跨访客串味。
    """
    addendum = _build_user_profile_addendum("sess_fresh_never_used")
    assert "最近行程" not in addendum

    # session 未知（None）同样不得注入
    addendum_none = _build_user_profile_addendum(None)
    assert "最近行程" not in addendum_none


def test_sessions_do_not_see_each_others_trips():
    """会话隔离：A 会话的档案不得出现在 B 会话的 prompt 里。"""
    _seed_trip("sess_visitor_a", summary="访客A的专属行程摘要XYZ")
    addendum_b = _build_user_profile_addendum("sess_visitor_b")
    assert "访客A的专属行程摘要XYZ" not in addendum_b
    assert "最近行程" not in addendum_b


# ============================================================
# 测试 3：缺失字段时不报错（向后兼容，模板侧）
# ============================================================


def test_user_profile_addendum_missing_fields_returns_empty(tmp_path, monkeypatch):
    """SHANGWUJU_MOCK_DIR 指向旧 4 字段 profile 且会话无档案 → addendum 返空字符串。"""
    old_profile = {
        "user_id": "demo_user",
        "home_location": {"name": "家", "lat": 30.0, "lng": 120.0},
        "default_budget": 300.0,
        "transport_preference": "taxi",
    }
    profile_path = tmp_path / "user_profile.json"
    profile_path.write_text(json.dumps(old_profile, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("SHANGWUJU_MOCK_DIR", str(tmp_path))

    from data.loader import load_user_profile

    load_user_profile.cache_clear()

    addendum = _build_user_profile_addendum("sess_compat")
    assert addendum == ""


# ============================================================
# 测试 4：prompt 整体可生成（不破已有 priors 注入）
# ============================================================


def test_full_prompt_with_priors_includes_user_profile_section():
    """build_intent_parser_system_prompt_with_priors 双键（模板 user_id + 累积 session_id）可生成。"""
    _seed_trip("sess_full")
    full = build_intent_parser_system_prompt_with_priors("demo_user", "sess_full")
    assert INTENT_PARSER_SYSTEM_PROMPT in full
    assert len(full) > len(INTENT_PARSER_SYSTEM_PROMPT)
    assert "最近行程" in full
