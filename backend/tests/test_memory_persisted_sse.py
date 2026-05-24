"""spec algorithm-redesign R5 收尾：narrate_node memory_status diff + MEMORY_PERSISTED SSE 序列化。

测试覆盖：
- narrate_node 返 state diff 含 memory_status 字段（confirm 路径）
- memory_status 含 social_context / summary_preview / success / skipped_reason
- cancel 路径 memory_status.success == False + skipped_reason="user_cancelled"
- MEMORY_PERSISTED 在 SseEventType 枚举里（前端可消费）

不调真 LLM；用 stub client + tempfile profile。
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.graph.nodes.narrate import narrate_node  # noqa: E402
from schemas.sse import SseEventType  # noqa: E402
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary  # noqa: E402


def test_memory_persisted_in_sse_event_type():
    """SseEventType 枚举含 MEMORY_PERSISTED（前端可订阅）"""
    assert hasattr(SseEventType, "MEMORY_PERSISTED")
    assert SseEventType.MEMORY_PERSISTED.value == "memory_persisted"


@pytest.fixture
def mock_user_profile_path(tmp_path, monkeypatch):
    """mock 一个临时 user_profile.json 路径，避免污染真实 mock_data"""
    fake_profile = {
        "user_id": "demo_user",
        "home_location": {"name": "测试家", "lat": 30.0, "lng": 120.0},
        "default_budget": 300.0,
        "transport_preference": "taxi",
        "recent_trips": [],
        "social_context_history": [],
    }
    profile_path = tmp_path / "user_profile.json"
    profile_path.write_text(
        json.dumps(fake_profile, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setenv("SHANGWUJU_MOCK_DIR", str(tmp_path))

    # 清 lru_cache 让新 path 生效
    from data.loader import load_user_profile, load_user_profiles

    load_user_profile.cache_clear()
    load_user_profiles.cache_clear()
    yield profile_path
    # cleanup：恢复 cache
    load_user_profile.cache_clear()
    load_user_profiles.cache_clear()


def test_narrate_node_returns_memory_status_on_confirm(
    mock_user_profile_path, monkeypatch
):
    """confirm 路径下 narrate_node 返 memory_status.success=True"""
    # 让 narrate_node 内的 get_llm_client 返 stub
    stub_client = MagicMock()
    stub_client.provider = "stub"

    monkeypatch.setattr(
        "agent.graph.nodes.narrate.get_llm_client",
        lambda: stub_client,
    )

    intent = _make_intent(social_context="家庭日常")
    itinerary = _make_legal_itinerary()
    state = {
        "intent": intent,
        "itinerary": itinerary,
        "user_decision": "confirm",
        "user_id": "demo_user",
    }

    result = narrate_node(state)
    assert "memory_status" in result, "narrate_node 应返 memory_status diff"
    ms = result["memory_status"]
    assert ms["social_context"] == "家庭日常"
    assert isinstance(ms["summary_preview"], str)
    assert "家庭日常" in ms["summary_preview"]
    assert isinstance(ms["success"], bool)
    # 首次写入应成功
    assert ms["success"] is True
    assert ms["skipped_reason"] is None


def test_narrate_node_returns_memory_status_on_cancel(
    mock_user_profile_path, monkeypatch
):
    """cancel 路径 → memory_status.success=False + skipped_reason='user_cancelled'"""
    stub_client = MagicMock()
    stub_client.provider = "stub"
    monkeypatch.setattr(
        "agent.graph.nodes.narrate.get_llm_client",
        lambda: stub_client,
    )

    intent = _make_intent(social_context="家庭日常")
    itinerary = _make_legal_itinerary()
    state = {
        "intent": intent,
        "itinerary": itinerary,
        "user_decision": "cancel",
        "user_id": "demo_user",
    }

    result = narrate_node(state)
    assert "memory_status" in result
    ms = result["memory_status"]
    assert ms["success"] is False
    assert ms["skipped_reason"] == "user_cancelled"


def test_narrate_node_summary_preview_format(
    mock_user_profile_path, monkeypatch
):
    """summary_preview 含「social_context · 节点序列」格式"""
    stub_client = MagicMock()
    stub_client.provider = "stub"
    monkeypatch.setattr(
        "agent.graph.nodes.narrate.get_llm_client",
        lambda: stub_client,
    )

    intent = _make_intent(social_context="家庭日常")
    itinerary = _make_legal_itinerary()
    state = {
        "intent": intent,
        "itinerary": itinerary,
        "user_decision": "confirm",
        "user_id": "demo_user",
    }

    result = narrate_node(state)
    preview = result["memory_status"]["summary_preview"]
    # legal_itinerary 含 1 个 poi + 1 个 restaurant 中间节点
    assert "活动" in preview or "用餐" in preview
    assert "→" in preview  # 节点序列分隔符
    assert len(preview) <= 80
