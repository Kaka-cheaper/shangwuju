"""spec algorithm-redesign R5：memory_writer 副作用单测。

测试覆盖（≥ 5 项）：
- 5 条上限：写入第 6 条时丢弃最旧的
- 幂等键 5 分钟窗口：同 social_context + 5min 内重复不追加
- 失败 / cancel 不写回
- 隐私脱敏：summary 不含「5 岁」原始数字（由 LLM 处理；这里测 fallback 摘要）
- 文件锁不冲突（threading.Lock）
- profile schema 向后兼容（旧 4 字段也能加载）
- summary 上限 500 字符

不消费真 LLM；用 stub client。
"""

from __future__ import annotations

import json
import sys
import threading
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


from agent.planning.memory_writer import (  # noqa: E402
    _is_duplicate,
    _now_iso,
    persist_memory,
)
from schemas.domain import RecentTrip, UserProfile  # noqa: E402
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary  # noqa: E402


# ============================================================
# Fixture
# ============================================================


@pytest.fixture
def temp_profile_path(tmp_path: Path) -> Path:
    """临时 user_profile.json，含 4 字段最小完整 profile + 空 recent_trips"""
    path = tmp_path / "user_profile.json"
    profile = {
        "user_id": "demo_user",
        "home_location": {
            "name": "测试家",
            "lat": 30.275,
            "lng": 120.075,
        },
        "default_budget": 300.0,
        "transport_preference": "taxi",
        "recent_trips": [],
        "social_context_history": [],
    }
    path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def stub_client():
    client = MagicMock()
    client.provider = "stub"
    return client


def _make_state(
    *,
    user_decision: str = "confirm",
    social_context: str = "家庭日常",
) -> dict:
    intent = _make_intent(social_context=social_context)
    itinerary = _make_legal_itinerary()
    return {
        "intent": intent,
        "itinerary": itinerary,
        "user_decision": user_decision,
        "user_id": "demo_user",
    }


# ============================================================
# 测试 1：成功写入 1 条
# ============================================================


def test_persist_memory_writes_recent_trip(temp_profile_path, stub_client):
    state = _make_state()
    ok = persist_memory(state, profile_path=temp_profile_path, client=stub_client)
    assert ok is True

    raw = json.loads(temp_profile_path.read_text(encoding="utf-8"))
    profile = UserProfile.model_validate(raw)
    assert profile.recent_trips is not None
    assert len(profile.recent_trips) == 1
    assert profile.recent_trips[0].social_context == "家庭日常"
    assert profile.recent_trips[0].success is True
    assert profile.recent_trips[0].summary  # 非空


# ============================================================
# 测试 2：5 条上限
# ============================================================


def test_persist_memory_5_trips_upper_limit(temp_profile_path, stub_client):
    """先写 5 条，再写第 6 条 → 丢弃最旧"""
    # 先塞 5 条已有记录（LIFO 顺序：最新在头，最旧在尾）
    raw = json.loads(temp_profile_path.read_text(encoding="utf-8"))
    raw["recent_trips"] = [
        {
            # 最新在头：i=5 → 2026-04-05；i=1 → 2026-04-01
            "timestamp": f"2026-04-{6-i:02d}T10:00:00Z",
            "social_context": f"old_context_{i}",
            "summary": f"old summary {i}",
            "success": True,
        }
        for i in range(1, 6)
    ]
    temp_profile_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    # 写第 6 条（不同 social_context 避免幂等键拦下）
    state = _make_state(social_context="情侣亲密")
    ok = persist_memory(state, profile_path=temp_profile_path, client=stub_client)
    assert ok is True

    profile = UserProfile.model_validate(
        json.loads(temp_profile_path.read_text(encoding="utf-8"))
    )
    assert len(profile.recent_trips) == 5  # 上限维持
    # 最新的在头
    assert profile.recent_trips[0].social_context == "情侣亲密"
    # old_context_5（fixture 中尾部最旧记录）应被丢弃
    contexts = [t.social_context for t in profile.recent_trips]
    assert "old_context_5" not in contexts, f"最旧的 old_context_5 应被丢弃，实际：{contexts}"


# ============================================================
# 测试 3：幂等键 5 分钟窗口
# ============================================================


def test_idempotent_within_5min_window(temp_profile_path, stub_client):
    """同一 session 5 分钟内同 social_context 重复 persist → 第二次跳过"""
    state = _make_state()
    ok1 = persist_memory(state, profile_path=temp_profile_path, client=stub_client)
    assert ok1 is True

    # 立即再写一次（5 分钟内）
    ok2 = persist_memory(state, profile_path=temp_profile_path, client=stub_client)
    assert ok2 is False, "5 分钟内重复应被幂等键拦下"

    # 验证 profile 只有 1 条
    profile = UserProfile.model_validate(
        json.loads(temp_profile_path.read_text(encoding="utf-8"))
    )
    assert len(profile.recent_trips) == 1


def test_is_duplicate_same_context_within_window():
    """_is_duplicate helper：5 分钟内同 social_context → True"""
    now = _now_iso()
    trip = RecentTrip(
        timestamp=now,
        social_context="家庭日常",
        summary="测试",
        success=True,
    )
    assert _is_duplicate([trip], "家庭日常", now) is True
    # 不同 social_context 不算 duplicate
    assert _is_duplicate([trip], "情侣亲密", now) is False


def test_is_duplicate_outside_window():
    """超 5 分钟窗口 → 不算 duplicate"""
    trip = RecentTrip(
        timestamp="2026-05-18T10:00:00Z",
        social_context="家庭日常",
        summary="旧",
        success=True,
    )
    now = "2026-05-18T11:00:00Z"  # 1 小时后
    assert _is_duplicate([trip], "家庭日常", now) is False


# ============================================================
# 测试 4：cancel 不写回
# ============================================================


def test_cancel_decision_skips_write(temp_profile_path, stub_client):
    state = _make_state(user_decision="cancel")
    ok = persist_memory(state, profile_path=temp_profile_path, client=stub_client)
    assert ok is False

    profile = UserProfile.model_validate(
        json.loads(temp_profile_path.read_text(encoding="utf-8"))
    )
    assert profile.recent_trips == []  # 空


# ============================================================
# 测试 5：success=False（refine 草稿）也允许写入
# ============================================================


def test_refine_draft_writes_with_success_false(temp_profile_path, stub_client):
    """user_decision=None / "refine" → 写入但 success=False"""
    state = _make_state(user_decision="refine")
    ok = persist_memory(state, profile_path=temp_profile_path, client=stub_client)
    assert ok is True

    profile = UserProfile.model_validate(
        json.loads(temp_profile_path.read_text(encoding="utf-8"))
    )
    assert len(profile.recent_trips) == 1
    assert profile.recent_trips[0].success is False


# ============================================================
# 测试 6：summary 长度上限 + 隐私（fallback 不含具体数字）
# ============================================================


def test_summary_does_not_contain_age_numbers_in_fallback(
    temp_profile_path, stub_client
):
    """stub 模式 fallback summary 应不含具体年龄数字（虽然原始 itinerary 节点没年龄）

    更重要的：stub 模式生成的 fallback 文本是格式化字符串，不会泄漏 intent.companions 中
    的 age 字段。这是设计纪律：fallback summary 仅含 social_context + 节点类型 + 时长。
    """
    state = _make_state()
    persist_memory(state, profile_path=temp_profile_path, client=stub_client)

    profile = UserProfile.model_validate(
        json.loads(temp_profile_path.read_text(encoding="utf-8"))
    )
    summary = profile.recent_trips[0].summary
    assert summary
    # fallback summary 不应含具体年龄关键字（intent 里也没年龄数字所以这里更稳）
    assert "5 岁" not in summary
    assert "5岁" not in summary
    # 长度限制
    assert len(summary) <= 500


# ============================================================
# 测试 7：threading.Lock 跨平台不依赖 fcntl
# ============================================================


def test_threading_lock_used_for_cross_platform():
    """memory_writer 使用 threading.Lock（不应导入 fcntl）"""
    import agent.planning.memory_writer as mw
    # 验证 _FILE_LOCK 是 threading.Lock 实例（_lock 内部类型）
    assert isinstance(mw._FILE_LOCK, type(threading.Lock())) or hasattr(
        mw._FILE_LOCK, "acquire"
    )
    # 验证不依赖 fcntl
    src = Path(mw.__file__).read_text(encoding="utf-8")
    assert "import fcntl" not in src
    assert "from fcntl" not in src


# ============================================================
# 测试 8：social_context_history 去重 + 上限
# ============================================================


def test_social_context_history_dedup_and_cap(temp_profile_path, stub_client):
    """重复的 social_context 应去重；新值移到头部"""
    # 先塞已有 history
    raw = json.loads(temp_profile_path.read_text(encoding="utf-8"))
    raw["social_context_history"] = ["独处放空", "家庭日常", "情侣纪念"]
    temp_profile_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    state = _make_state(social_context="家庭日常")
    persist_memory(state, profile_path=temp_profile_path, client=stub_client)

    profile = UserProfile.model_validate(
        json.loads(temp_profile_path.read_text(encoding="utf-8"))
    )
    history = profile.social_context_history or []
    # 新 social_context "家庭日常" 应去重并移到头
    assert history[0] == "家庭日常"
    # 总数不变（去重后还是 3 条）
    assert len(history) == 3
    assert "独处放空" in history
    assert "情侣纪念" in history


# ============================================================
# 测试 9：schema 向后兼容（旧 4 字段也能加载）
# ============================================================


def test_old_4_field_profile_loads(tmp_path):
    """旧 user_profile.json 仅 4 字段（无 dietary / recent_trips） → 仍可加载"""
    path = tmp_path / "user_profile.json"
    old = {
        "user_id": "demo_user",
        "home_location": {"name": "家", "lat": 30.0, "lng": 120.0},
        "default_budget": 300.0,
        "transport_preference": "taxi",
    }
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")

    profile = UserProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert profile.user_id == "demo_user"
    assert profile.recent_trips is None  # 缺省 None
    assert profile.social_context_history is None
    assert profile.dietary_preference is None


# ============================================================
# 测试 10：失败时不抛异常
# ============================================================


def test_persist_memory_never_raises(stub_client, tmp_path):
    """profile_path 不存在 / 不可写 → 返 False，不抛异常"""
    bad_path = tmp_path / "nonexistent" / "subdir" / "user_profile.json"
    state = _make_state()
    ok = persist_memory(state, profile_path=bad_path, client=stub_client)
    assert ok is False  # 不抛异常，仅返 False
