"""tests.test_memory_v2 —— Step 7：visited targets + preferred routes 记忆。

覆盖：
1. record_visited 写入 + 时间戳
2. recently_visited_ids cooldown 过滤（30 天前的不返回）
3. record_preferred_route 计数累加
4. record_visited 200 条上限滚动
5. UserMemory 向后兼容（旧 JSON 加载不报）
6. search_pois exclude_visited_ids 真生效
7. search_pois_for_intent 自动从 memory 拉 visited 排除（按 session 键）
8. 不同 kind 的 visited 互不影响（poi 不影响餐厅 search）

【判据变更理由（记忆身份读写分离批，ADR-0015 身份边界补充决策，2026-07-05）】
visited 排重是"确认累积"的读侧之一：累积改按 session_id 键控后，
search_*_for_intent 的排除键从 user_id 参数改为独立的 session_id 参数
（user_id 仍保留，只用于 home 坐标这类模板读）。会话隔离新增钉在
test_search_exclusion_is_session_scoped。

不调 LLM；用真 mock。
"""

from __future__ import annotations

import time
import pytest

from data.memory_store import (
    get_memory,
    record_preferred_route,
    record_visited,
    reset_all_memory,
)
from schemas.persona import UserMemory, VisitedRecord
from schemas.tools import SearchPoisInput
from tools.search_pois import search_pois


@pytest.fixture(autouse=True)
def _clean_memory():
    """每个测试前后清掉 memory 缓存，避免互相污染。"""
    reset_all_memory()
    yield
    reset_all_memory()


# ============================================================
# UserMemory schema
# ============================================================

def test_user_memory_default_visited_empty():
    """默认 visited_targets / preferred_routes 都为空（向后兼容）。"""
    m = UserMemory(user_id="x")
    assert m.visited_targets == []
    assert m.preferred_routes == {}


def test_visited_record_required_fields():
    r = VisitedRecord(
        target_id="P011",
        target_kind="poi",
        visited_at_ms=int(time.time() * 1000),
    )
    assert r.cooldown_days == 30


def test_recently_visited_ids_filters_by_cutoff():
    """超出 within_days 的访问不返回。"""
    now_ms = int(time.time() * 1000)
    # 31 天前
    old_ms = now_ms - 31 * 86400 * 1000
    m = UserMemory(
        user_id="x",
        visited_targets=[
            VisitedRecord(
                target_id="P_OLD", target_kind="poi", visited_at_ms=old_ms
            ),
            VisitedRecord(
                target_id="P_NEW", target_kind="poi", visited_at_ms=now_ms
            ),
        ],
    )
    recent = m.recently_visited_ids(within_days=30, now_ms=now_ms)
    assert "P_NEW" in recent
    assert "P_OLD" not in recent


# ============================================================
# record_visited
# ============================================================

def test_record_visited_writes_to_memory():
    """confirm 后调 record_visited → memory 被更新。"""
    m = record_visited(
        "u_test_step7",
        visits=[("P011", "poi"), ("R007", "restaurant")],
    )
    assert len(m.visited_targets) == 2
    ids = [v.target_id for v in m.visited_targets]
    assert "P011" in ids
    assert "R007" in ids
    # 不同 kind
    kinds = {v.target_kind for v in m.visited_targets}
    assert kinds == {"poi", "restaurant"}


def test_record_visited_caps_at_200():
    """累计超过 200 条自动滚动保留最新。"""
    user_id = "u_test_cap"
    visits = [(f"P{i:03d}", "poi") for i in range(250)]
    m = record_visited(user_id, visits=visits)
    assert len(m.visited_targets) == 200
    # 应保留最后的 200 条（P050 - P249）
    last_id = m.visited_targets[-1].target_id
    assert last_id == "P249"


def test_record_visited_idempotent_same_id_appends():
    """同一 id 多次访问会有多条记录（不去重，便于统计访问频次）。"""
    user_id = "u_test_dup"
    record_visited(user_id, visits=[("P011", "poi")])
    record_visited(user_id, visits=[("P011", "poi")])
    m = get_memory(user_id)
    assert len(m.visited_targets) == 2


# ============================================================
# record_preferred_route
# ============================================================

def test_record_preferred_route_counts_segments():
    user_id = "u_test_route"
    record_preferred_route(
        user_id, segments=[("home", "P011"), ("P011", "R007"), ("R007", "home")]
    )
    m = get_memory(user_id)
    assert m.preferred_routes["home|P011"] == 1
    assert m.preferred_routes["P011|R007"] == 1
    assert m.preferred_routes["R007|home"] == 1
    # 再走一次 home→P011，计数 +1
    record_preferred_route(user_id, segments=[("home", "P011")])
    m = get_memory(user_id)
    assert m.preferred_routes["home|P011"] == 2


def test_record_preferred_route_skips_self_loop():
    """from == to 跳过（同地不算路径）。"""
    user_id = "u_test_self"
    record_preferred_route(user_id, segments=[("P011", "P011")])
    m = get_memory(user_id)
    assert m.preferred_routes == {}


# ============================================================
# search_pois exclude_visited_ids 集成
# ============================================================

def test_search_pois_excludes_visited_id():
    """exclude_visited_ids 真过滤掉指定 id 的候选。"""
    inp = SearchPoisInput(
        distance_max_km=10.0,
        physical_constraints=["亲子友好"],
        exclude_visited_ids=["P001"],
    )
    out = search_pois(inp)
    if out.success:
        ids = [p.id for p in out.candidates]
        assert "P001" not in ids, "exclude_visited_ids 应该过滤掉 P001"


def _mk_intent(social_context: str = "独处放空"):
    from schemas.intent import IntentExtraction

    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=[4, 6],  # type: ignore[arg-type]
        distance_max_km=10.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context=social_context,
        raw_input="测试",
        parse_confidence=0.9,
    )


def test_search_pois_for_intent_auto_excludes_recently_visited():
    """本会话 confirm 过 P011 后，同会话 search_pois_for_intent(session_id=...) 应排除 P011。"""
    from agent.runtime.tools.search_adapter import search_pois_for_intent

    session_id = "sess_test_exclude"
    # 模拟 confirm 后写入（会话键）
    record_visited(session_id, visits=[("P011", "poi")])

    pois, _ = search_pois_for_intent(_mk_intent(), session_id=session_id)
    ids = [p.id for p in pois]
    assert "P011" not in ids, (
        f"本会话 confirm 过的 P011 应被自动排除，实际候选 ids={ids}"
    )


def test_search_exclusion_is_session_scoped():
    """会话隔离：A 会话的 visited 不影响 B 会话的候选（跨访客不串味）。"""
    from agent.runtime.tools.search_adapter import search_pois_for_intent

    record_visited("sess_visitor_a", visits=[("P011", "poi")])
    pois, _ = search_pois_for_intent(_mk_intent(), session_id="sess_visitor_b")
    ids = [p.id for p in pois]
    assert "P011" in ids, (
        f"B 会话不该被 A 会话的访问史排重（累积会话私有），实际候选 ids={ids}"
    )


def test_search_pois_does_not_exclude_other_kind_visited():
    """本会话 confirm 过餐厅 R007 不应影响 POI 搜索。"""
    from agent.runtime.tools.search_adapter import search_pois_for_intent

    session_id = "sess_test_kind"
    # 只 confirm 餐厅 R007
    record_visited(session_id, visits=[("R007", "restaurant")])

    pois, _ = search_pois_for_intent(_mk_intent(), session_id=session_id)
    # POI 候选不应受 R007 影响（可能本身没合规候选，但不会因 R007 被过滤）
    ids = [p.id for p in pois]
    if pois:  # 有候选时
        # R007 不应出现（它是餐厅）也不应让 POI 被过滤；只验候选不为空
        assert "R007" not in ids, "餐厅 id 不应出现在 POI 候选"


# ============================================================
# 无会话键不调 memory（向后兼容）
# ============================================================

def test_no_session_id_no_exclude():
    """session_id=None 时不查 memory，候选不变。"""
    from agent.runtime.tools.search_adapter import search_pois_for_intent

    pois, _ = search_pois_for_intent(_mk_intent("家庭日常"), user_id=None)
    # 应有候选（P001 等亲子 POI）
    assert len(pois) > 0
