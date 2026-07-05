"""memory_writer 副作用单测（记忆身份读写分离批重钉）。

【判据变更理由（ADR-0015 身份边界补充决策，2026-07-05）】
旧判据：persist_memory 写全局单文件 user_profile.json 的 recent_trips——
demo 单用户假设下成立；演示日多访客并发时，A 确认的行程会串进 B 的意图
prompt（accumulation 默认身份共享）。新判据：行程档案按 **session_id 键控**
写进程内会话私有存储（data.memory_store 的 recent_trips 区），user_profile.json
退为只读模板（dietary_preference / home_location 等），运行时零文件写入——
并发写文件竞态随之消失，旧的 mock_data_runtime 护栏测试一并退役。

保留原样的策略语义（键变、机制不动）：
- 5 条上限（LIFO，最新在头）
- social_context + 5 分钟幂等窗
- cancel / 缺 intent/itinerary 不写
- 摘要脱敏（stub fallback 不含具体年龄数字）+ 500 字上限
- 永不抛异常

新增不变式：
- 无 session_id（无会话身份）不写——"会话即身份"，没有身份就没有归属
- 会话隔离：同 user_id 两个 session 的档案互不可见

不消费真 LLM；用 stub client。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 复用过渡态桥
if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.planning.memory_writer import persist_memory  # noqa: E402
from data.memory_store import get_recent_trips, reset_all_memory  # noqa: E402
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary  # noqa: E402


# ============================================================
# Fixture
# ============================================================


@pytest.fixture(autouse=True)
def _clean_memory():
    reset_all_memory()
    yield
    reset_all_memory()


@pytest.fixture
def stub_client():
    client = MagicMock()
    client.provider = "stub"
    return client


def _make_state(
    *,
    user_decision: str = "confirm",
    social_context: str = "家庭日常",
    session_id: str | None = "sess_mw_test",
    user_id: str = "demo_user",
) -> dict:
    intent = _make_intent(social_context=social_context)
    itinerary = _make_legal_itinerary()
    state: dict = {
        "intent": intent,
        "itinerary": itinerary,
        "user_decision": user_decision,
        "user_id": user_id,
    }
    if session_id is not None:
        state["session_id"] = session_id
    return state


# ============================================================
# 测试 1：成功写入 1 条（session 键控，不再落文件）
# ============================================================


def test_persist_memory_writes_recent_trip_session_keyed(stub_client):
    state = _make_state(session_id="sess_a")
    ok = persist_memory(state, client=stub_client)
    assert ok is True

    trips = get_recent_trips("sess_a")
    assert len(trips) == 1
    assert trips[0].social_context == "家庭日常"
    assert trips[0].success is True
    assert trips[0].summary  # 非空


def test_persist_memory_does_not_touch_profile_template(stub_client):
    """user_profile.json 是只读模板：persist 后模板文件逐字节不变（运行时零文件写）。"""
    import os

    template = Path(os.environ["SHANGWUJU_MOCK_DIR"]) / "user_profile.json"
    before = template.read_bytes()

    ok = persist_memory(_make_state(session_id="sess_ro"), client=stub_client)
    assert ok is True
    assert template.read_bytes() == before, "行程档案已改会话私有存储，模板文件不得被写"


# ============================================================
# 测试 2：会话隔离（本批核心不变式）
# ============================================================


def test_sessions_are_isolated_even_with_same_user_id(stub_client):
    """同 user_id、不同 session → 档案互不可见（会话即身份，跨访客不串味）。"""
    ok = persist_memory(
        _make_state(session_id="sess_visitor_a", user_id="u_dad"), client=stub_client
    )
    assert ok is True

    assert len(get_recent_trips("sess_visitor_a")) == 1
    assert get_recent_trips("sess_visitor_b") == [], (
        "访客 B 的会话不得看到访客 A 确认的行程档案"
    )


def test_missing_session_id_skips_write(stub_client):
    """无 session_id（无会话身份）→ 不写、返 False——没有身份就没有归属。"""
    ok = persist_memory(_make_state(session_id=None), client=stub_client)
    assert ok is False


# ============================================================
# 测试 3：5 条上限（LIFO，最新在头）
# ============================================================


def test_persist_memory_5_trips_upper_limit(stub_client):
    """写 6 条不同 social_context → 保 5 条，最旧的被丢弃。"""
    contexts = ["家庭日常", "老人伴助", "闺蜜聊天", "朋友热闹", "情侣亲密", "商务接待"]
    for sc in contexts:
        ok = persist_memory(
            _make_state(social_context=sc, session_id="sess_cap"), client=stub_client
        )
        assert ok is True

    trips = get_recent_trips("sess_cap")
    assert len(trips) == 5  # 上限维持
    assert trips[0].social_context == "商务接待"  # 最新在头
    kept = [t.social_context for t in trips]
    assert "家庭日常" not in kept, f"最旧的一条应被丢弃，实际：{kept}"


# ============================================================
# 测试 4：幂等键 5 分钟窗口
# ============================================================


def test_idempotent_within_5min_window(stub_client):
    """同 session 5 分钟内同 social_context 重复 persist → 第二次跳过。"""
    state = _make_state(session_id="sess_dup")
    assert persist_memory(state, client=stub_client) is True
    assert persist_memory(state, client=stub_client) is False, "5 分钟内重复应被幂等键拦下"
    assert len(get_recent_trips("sess_dup")) == 1


def test_same_context_different_sessions_both_write(stub_client):
    """幂等窗按 session 隔离：不同 session 同 social_context 各写各的。"""
    assert persist_memory(_make_state(session_id="sess_x"), client=stub_client) is True
    assert persist_memory(_make_state(session_id="sess_y"), client=stub_client) is True
    assert len(get_recent_trips("sess_x")) == 1
    assert len(get_recent_trips("sess_y")) == 1


# ============================================================
# 测试 5：cancel / refine 草稿语义（原样保留）
# ============================================================


def test_cancel_decision_skips_write(stub_client):
    ok = persist_memory(_make_state(user_decision="cancel"), client=stub_client)
    assert ok is False
    assert get_recent_trips("sess_mw_test") == []


def test_refine_draft_writes_with_success_false(stub_client):
    """user_decision=None / "refine" → 写入但 success=False。"""
    ok = persist_memory(
        _make_state(user_decision="refine", session_id="sess_draft"), client=stub_client
    )
    assert ok is True
    trips = get_recent_trips("sess_draft")
    assert len(trips) == 1
    assert trips[0].success is False


# ============================================================
# 测试 6：summary 脱敏 + 长度上限（原样保留）
# ============================================================


def test_summary_does_not_contain_age_numbers_in_fallback(stub_client):
    """stub fallback summary 仅含 social_context + 节点类型 + 时长，不泄漏年龄。"""
    persist_memory(_make_state(session_id="sess_pii"), client=stub_client)
    summary = get_recent_trips("sess_pii")[0].summary
    assert summary
    assert "5 岁" not in summary
    assert "5岁" not in summary
    assert len(summary) <= 500


# ============================================================
# 测试 7：永不抛异常
# ============================================================


def test_persist_memory_never_raises(stub_client):
    """intent / itinerary 缺失 → 返 False，不抛异常。"""
    ok = persist_memory({"session_id": "sess_bad"}, client=stub_client)
    assert ok is False

    ok2 = persist_memory(
        {"session_id": "sess_bad", "intent": object(), "itinerary": None},
        client=stub_client,
    )
    assert ok2 is False
