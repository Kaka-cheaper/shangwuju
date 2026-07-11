"""tests.test_emit_fanout_search_preview —— 信任带②拍检索收据芯片后端数据源回归。

覆盖（见 路演PPT/信任带设计终稿.md 2026-07-10 修订 + 任务交付说明）：
1. pois worker：diff["pois"] 非空 → out_summary["preview"] 是评分 top-3，
   降序排列，字段只有 {kind, name, rating}（kind="poi"）。
2. restaurants worker：同上，kind="restaurant"。
3. 候选 <3 条时 preview 全量携带（不补齐到 3 条）。
4. 空列表（diff["pois"] == []）→ 不挂 preview 字段（"无内容不加字段"纪律，
   同 emit_planner.plan_reason / emit_narrate.node_detail 等既有先例）。
5. get_user_profile worker（diff 只有 "user_profile" 键）→ 不产出 preview。
6. 同分候选保持稳定排序（sort 是 stable sort，同分时维持召回列表原始顺序）。

用合成假实体（不用真实 Poi/Restaurant Pydantic 模型，只需 .name/.rating 两个
属性——同 test_emit_planner_plan_reason.py 的既有测试风格：轻量 dataclass fake，
不依赖真实 domain 实体构造的全部必填字段）。不断言具体实体名/ID字面值以外的
业务含义（数据集正在从杭州迁望京，测试全用合成占位名）。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.graph._emit_context import EmitContext
from agent.graph._emit_handlers import emit_fanout_worker
from schemas.sse import SseEventType


@dataclass
class _FakeEntity:
    name: str
    rating: float


def _end_output(events):
    """取 tool_call_end 事件的 output 子字典（preview/count/found 都在这层，
    不在 payload 顶层——payload 顶层是 {tool, output, duration_ms, group_id, parallel}）。"""
    end_events = [e for e in events if e.type == SseEventType.TOOL_CALL_END]
    assert len(end_events) == 1
    return end_events[0].payload["output"]


def test_pois_worker_preview_is_rating_top3_descending():
    ctx = EmitContext()
    diff = {
        "pois": [
            _FakeEntity(name="甲地点", rating=3.5),
            _FakeEntity(name="乙地点", rating=4.8),
            _FakeEntity(name="丙地点", rating=4.2),
            _FakeEntity(name="丁地点", rating=4.9),
        ]
    }

    events = emit_fanout_worker(ctx, "search_pois_worker", diff)
    payload = _end_output(events)

    assert "preview" in payload
    preview = payload["preview"]
    assert len(preview) == 3
    assert [p["name"] for p in preview] == ["丁地点", "乙地点", "丙地点"]
    assert [p["rating"] for p in preview] == [4.9, 4.8, 4.2]
    assert all(p["kind"] == "poi" for p in preview)
    assert all(set(p.keys()) == {"kind", "name", "rating"} for p in preview)


def test_restaurants_worker_preview_kind_is_restaurant():
    ctx = EmitContext()
    diff = {
        "restaurants": [
            _FakeEntity(name="A餐厅", rating=4.1),
            _FakeEntity(name="B餐厅", rating=4.6),
        ]
    }

    events = emit_fanout_worker(ctx, "search_restaurants_worker", diff)
    payload = _end_output(events)

    preview = payload["preview"]
    assert [p["name"] for p in preview] == ["B餐厅", "A餐厅"]
    assert all(p["kind"] == "restaurant" for p in preview)


def test_preview_not_padded_when_fewer_than_three_candidates():
    ctx = EmitContext()
    diff = {"pois": [_FakeEntity(name="唯一地点", rating=4.0)]}

    events = emit_fanout_worker(ctx, "search_pois_worker", diff)
    payload = _end_output(events)

    assert len(payload["preview"]) == 1


def test_empty_candidates_omit_preview_key():
    ctx = EmitContext()
    diff = {"pois": []}

    events = emit_fanout_worker(ctx, "search_pois_worker", diff)
    payload = _end_output(events)

    assert "preview" not in payload
    assert payload["count"] == 0


def test_user_profile_worker_never_has_preview():
    ctx = EmitContext()
    diff = {"user_profile": object()}

    events = emit_fanout_worker(ctx, "get_user_profile_worker", diff)
    payload = _end_output(events)

    assert "preview" not in payload
    assert payload["found"] is True


def test_tied_rating_keeps_stable_original_order():
    ctx = EmitContext()
    diff = {
        "pois": [
            _FakeEntity(name="先到者", rating=4.5),
            _FakeEntity(name="后到者", rating=4.5),
        ]
    }

    events = emit_fanout_worker(ctx, "search_pois_worker", diff)
    payload = _end_output(events)

    assert [p["name"] for p in payload["preview"]] == ["先到者", "后到者"]
