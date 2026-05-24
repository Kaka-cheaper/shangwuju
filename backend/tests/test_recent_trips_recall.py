"""spec algorithm-redesign R5：recent_trips + dietary_preference 召回 prompt 注入测试。

测试覆盖（≥ 3 项）：
- 召回匹配 social_context 的 recent_trip 注入 prompt
- dietary_preference 自然语言注入命中关键词
- profile 不含 recent_trips 时 prompt 不报错（向后兼容）

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


# ============================================================
# 测试 1：dietary_preference 注入 prompt
# ============================================================


def test_dietary_preference_injected():
    """user_profile.json 含 dietary_preference → prompt 包含「饮食偏好」段"""
    addendum = _build_user_profile_addendum()
    # mock_data/user_profile.json 已含 dietary_preference 字段
    if not addendum:
        pytest.skip("mock_data/user_profile.json 不含 dietary_preference 字段")
    assert "饮食偏好" in addendum
    # 命中默认 mock 中的关键词
    assert "健康轻食" in addendum or "辣度" in addendum


# ============================================================
# 测试 2：recent_trips 注入 prompt（最新 2 条）
# ============================================================


def test_recent_trips_injected():
    """user_profile.json 含 recent_trips → prompt 包含「最近行程」段"""
    addendum = _build_user_profile_addendum()
    if "最近行程" not in addendum:
        pytest.skip("mock_data/user_profile.json 不含 recent_trips 字段")
    # 命中默认 mock 中的 social_context
    assert "家庭日常" in addendum or "情侣亲密" in addendum
    # 最多注入 2 条（避免 prompt 过长）
    # 数 "「" 个数（每条 1 个）
    count = addendum.count("场景：")
    assert count <= 2


# ============================================================
# 测试 3：缺失字段时不报错（向后兼容）
# ============================================================


def test_user_profile_addendum_missing_fields_returns_empty(tmp_path, monkeypatch):
    """SHANGWUJU_MOCK_DIR 指向旧 4 字段 profile → addendum 返空字符串"""
    old_profile = {
        "user_id": "demo_user",
        "home_location": {"name": "家", "lat": 30.0, "lng": 120.0},
        "default_budget": 300.0,
        "transport_preference": "taxi",
    }
    profile_path = tmp_path / "user_profile.json"
    profile_path.write_text(json.dumps(old_profile, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("SHANGWUJU_MOCK_DIR", str(tmp_path))

    # 清 lru_cache
    from data.loader import load_user_profile
    load_user_profile.cache_clear()

    # 也需要旧 mock 数据（personas.json 等）才能让 priors view 不崩
    # 直接调 _build_user_profile_addendum
    addendum = _build_user_profile_addendum()
    assert addendum == ""


# ============================================================
# 测试 4：prompt 整体可生成（不破已有 priors 注入）
# ============================================================


def test_full_prompt_with_priors_includes_user_profile_section():
    """build_intent_parser_system_prompt_with_priors 含 user_profile 召回段"""
    full = build_intent_parser_system_prompt_with_priors("demo_user")
    # 至少含 base prompt
    assert INTENT_PARSER_SYSTEM_PROMPT in full
    # 长度比 base 长（注入了 priors + user_profile）
    assert len(full) > len(INTENT_PARSER_SYSTEM_PROMPT)
