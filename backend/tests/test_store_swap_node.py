"""tests.test_store_swap_node —— B2："换个店铺"聊天反馈换全店/点名换店编排。

驱动 `agent.graph.nodes.store_swap.store_swap_node` 直调（纯函数节点，手工
构造 state dict 比驱动完整 LangGraph astream 更快、更确定性——风格对齐
`test_planner_node_swap.py` 自建 fixture 纪律：合成 id，不依赖真实 mock_data
具体内容）。本节点像 `graph_adjust.py` 一样读全量目录（`load_pois`/
`load_restaurants`），测试用 monkeypatch 换成本文件合成的候选池。

覆盖：
1. 整轮换店：非锁定节点全部换成不同实体。
2. 累积排除（传入态）：本会话早前换掉的实体不复现。
3. 累积排除跨两次换店防 ping-pong：连换两次不回到第一版。
4. 赞锁定节点保住（跳过）。
5. 独苗品类诚实兜底：换不出的类别原样保留 + advisory，不崩、不影响其它节点。
6. 点名换店：只换点名那一个，其它节点原样不动。
7. 点名换店 + 该目标恰好锁定：诚实告知 SWAP_TARGET_LOCKED，不静默执行也不
   静默跳过。
"""

from __future__ import annotations

from agent.graph.nodes import store_swap as store_swap_module
from agent.graph.nodes.store_swap import store_swap_node
from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
from agent.planning.planners.activity_pool import (
    build_visit_from_poi,
    build_visit_from_restaurant,
)
from agent.planning.planners.route_builder import make_commute_fn, route_to_blueprint
from agent.planning.planners.route_scheduler import schedule_route
from agent.planning.weights_llm import get_planning_weights
from data.loader import load_user_profile
from schemas.advisory import AdvisoryCode
from schemas.domain import Location, Poi, PoiCapacity, Restaurant, RestaurantCapacity
from schemas.intent import IntentExtraction

# ============================================================
# 共享 fixture helpers（风格对齐 test_planner_node_swap.py）
# ============================================================


def _intent(**overrides) -> IntentExtraction:
    base = dict(
        start_time="2026-07-02T14:00",
        duration_hours=[1, 10],
        distance_max_km=50.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="独处放空",
        raw_input="换个店铺",
        parse_confidence=0.9,
        ambiguous_fields=[],
    )
    base.update(overrides)
    return IntentExtraction(**base)


def _poi(
    *,
    poi_id: str,
    poi_type: str = "公园",
    opening: str = "08:00-22:00",
    suggested: int = 60,
    dist: float = 3.0,
    tags: list[str] | None = None,
    rating: float = 4.5,
) -> Poi:
    return Poi(
        id=poi_id,
        name=f"POI-{poi_id}",
        type=poi_type,
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=dist,
        opening_hours=opening,
        rating=rating,
        age_range=None,
        price_range=None,
        tags=tags or [],
        suitable_for=[],
        suggested_duration_minutes=suggested,
        capacity=PoiCapacity(daily_quota=100, available_slots=50),
    )


def _rest(
    *,
    rest_id: str,
    cuisine: str = "火锅",
    opening: str = "11:00-23:00",
    dining_min: int = 60,
    avg_price: float = 100.0,
    rating: float = 4.3,
    tags: list[str] | None = None,
    dist: float = 3.0,
) -> Restaurant:
    return Restaurant(
        id=rest_id,
        name=f"REST-{rest_id}",
        cuisine=cuisine,
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=dist,
        opening_hours=opening,
        avg_price=avg_price,
        rating=rating,
        typical_dining_min=dining_min,
        capacity=RestaurantCapacity(),
        reservation_slots=[],
        tags=tags or [],
        suitable_for=[],
    )


def _build_itinerary(intent, entities, depart_min: int = 14 * 60, budget_min: int = 600):
    weights = get_planning_weights(intent, client=None)
    profile = load_user_profile()
    commute_fn = make_commute_fn(profile)
    visits = [
        build_visit_from_poi(e, intent, weights)
        if isinstance(e, Poi)
        else build_visit_from_restaurant(e, intent, weights)
        for e in entities
    ]
    schedule = schedule_route(visits, depart_min=depart_min, budget_min=budget_min, commute_fn=commute_fn)
    assert schedule is not None, "fixture 构造失败：候选组合本身不可行"
    blueprint = route_to_blueprint(schedule, intent, depart_min)
    return assemble_from_blueprint(intent, blueprint, profile)


def _node_ids(itinerary) -> list[str]:
    return [n.target_id for n in itinerary.nodes if n.target_kind != "home"]


def _state(itinerary, intent, *, user_input="换个店铺", pinned=None, swapped_out=None) -> dict:
    return {
        "itinerary": itinerary,
        "intent": intent,
        "user_input": user_input,
        "pinned_targets": pinned or [],
        "swapped_out_entity_ids": swapped_out or [],
        "route_kind": "feedback",
    }


def _patch_catalog(monkeypatch, pois: list[Poi], rests: list[Restaurant]) -> None:
    monkeypatch.setattr(store_swap_module, "load_pois", lambda: list(pois))
    monkeypatch.setattr(store_swap_module, "load_restaurants", lambda: list(rests))


# ============================================================
# 1. 整轮换店：全部非锁定节点都换成不同实体
# ============================================================


def test_swap_all_changes_every_non_locked_node(monkeypatch):
    intent = _intent()
    poi_a = _poi(poi_id="PA1", poi_type="公园")
    rb1 = _rest(rest_id="RB1", cuisine="火锅")
    itinerary = _build_itinerary(intent, [poi_a, rb1])
    assert _node_ids(itinerary) == ["PA1", "RB1"]

    poi_a2 = _poi(poi_id="PA2", poi_type="公园")
    rb2 = _rest(rest_id="RB2", cuisine="火锅")
    _patch_catalog(monkeypatch, [poi_a, poi_a2], [rb1, rb2])

    diff = store_swap_node(_state(itinerary, intent))

    new_ids = _node_ids(diff["itinerary"])
    assert new_ids == ["PA2", "RB2"], new_ids
    assert set(diff["swapped_out_entity_ids"]) == {"PA1", "RB1"}
    assert diff["advisories"] == []


# ============================================================
# 2. 累积排除（传入态）：本会话早前换掉的实体不复现
# ============================================================


def test_swap_all_excludes_previously_swapped_entities(monkeypatch):
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    poi_old = _poi(poi_id="PA_OLD")  # 假装是本会话早前换掉的旧实体
    poi_new = _poi(poi_id="PA_NEW")
    rb_old = _rest(rest_id="RB_OLD")
    rb_new = _rest(rest_id="RB_NEW")
    _patch_catalog(monkeypatch, [poi_a, poi_old, poi_new], [rb1, rb_old, rb_new])

    diff = store_swap_node(_state(itinerary, intent, swapped_out=["PA_OLD", "RB_OLD"]))

    new_ids = set(_node_ids(diff["itinerary"]))
    assert "PA_OLD" not in new_ids
    assert "RB_OLD" not in new_ids
    assert new_ids == {"PA_NEW", "RB_NEW"}
    # 累积排除集只增不减，本轮新换出的旧实体继续并入。
    assert set(diff["swapped_out_entity_ids"]) == {"PA_OLD", "RB_OLD", "PA1", "RB1"}


# ============================================================
# 3. 累积排除防 ping-pong：连换两次不回到第一版
# ============================================================


def test_cumulative_exclusion_prevents_ping_pong_across_two_swaps(monkeypatch):
    intent = _intent()
    poi_a = _poi(poi_id="PA1")  # 不参与本测试的换店焦点，保持不动的锚点
    rb1 = _rest(rest_id="RB1", cuisine="火锅", rating=5.0)  # 第一版
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    rb2 = _rest(rest_id="RB2", cuisine="火锅", rating=4.0)
    rb3 = _rest(rest_id="RB3", cuisine="火锅", rating=3.0)
    _patch_catalog(monkeypatch, [poi_a], [rb1, rb2, rb3])

    # 第一次换店：RB1 → 评分更高的同子类候选（RB2）
    diff1 = store_swap_node(_state(itinerary, intent))
    first_target = next(i for i in _node_ids(diff1["itinerary"]) if i != "PA1")
    assert first_target == "RB2"
    assert diff1["swapped_out_entity_ids"] == ["RB1"]

    # 第二次换店：喂回第一次的累积排除集——RB1 不该被换回来
    diff2 = store_swap_node(
        _state(diff1["itinerary"], intent, swapped_out=diff1["swapped_out_entity_ids"])
    )
    second_target = next(i for i in _node_ids(diff2["itinerary"]) if i != "PA1")

    assert second_target != "RB1", "第二次换店不该把会话累积排除过的第一版实体换回来"
    assert second_target != first_target, "第二次换店应换成另一个新实体，不是原地打转"
    assert second_target == "RB3"
    assert set(diff2["swapped_out_entity_ids"]) == {"RB1", "RB2"}


# ============================================================
# 4. 赞锁定节点保住
# ============================================================


def test_swap_all_skips_locked_node(monkeypatch):
    intent = _intent()
    poi_a = _poi(poi_id="PA1")  # 锁定，不该被换
    rb1 = _rest(rest_id="RB1")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    poi_new = _poi(poi_id="PA_NEW")
    rb_new = _rest(rest_id="RB_NEW")
    _patch_catalog(monkeypatch, [poi_a, poi_new], [rb1, rb_new])

    pinned = [{"kind": "poi", "target_id": "PA1", "name": poi_a.name}]
    diff = store_swap_node(_state(itinerary, intent, pinned=pinned))

    new_ids = _node_ids(diff["itinerary"])
    assert "PA1" in new_ids, "锁定节点必须原样保留"
    assert "RB_NEW" in new_ids, "非锁定节点应换成新实体"
    assert "PA1" not in diff["swapped_out_entity_ids"]


# ============================================================
# 5. 独苗品类诚实兜底：换不出的类别原样保留，不崩、不连累其它节点
# ============================================================


def test_swap_all_honestly_reports_when_category_has_no_alternative(monkeypatch):
    intent = _intent()
    poi_a = _poi(poi_id="PA1")  # 独苗——poi 候选池里没有第二个
    rb1 = _rest(rest_id="RB1")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    rb_new = _rest(rest_id="RB_NEW")
    _patch_catalog(monkeypatch, [poi_a], [rb1, rb_new])

    diff = store_swap_node(_state(itinerary, intent))

    new_ids = _node_ids(diff["itinerary"])
    assert "PA1" in new_ids, "poi 独苗换不出时应原样保留，不崩"
    assert "RB_NEW" in new_ids, "restaurant 仍应成功换成新实体，不受 poi 换不出连累"
    assert any(
        a["code"] == AdvisoryCode.SWAP_NO_ALTERNATIVE_FOUND.value for a in diff["advisories"]
    ), diff["advisories"]
    assert "PA1" not in diff["swapped_out_entity_ids"], "换不出的节点不该被记进已换出集合"


# ============================================================
# 6. 点名换店：只换点名那一个
# ============================================================


def test_named_swap_only_touches_named_node(monkeypatch):
    intent = _intent()
    poi_a = _poi(poi_id="PA1", poi_type="密室")
    rb1 = _rest(rest_id="RB1", cuisine="火锅")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    poi_new = _poi(poi_id="PA_NEW", poi_type="密室")
    rb_new = _rest(rest_id="RB_NEW", cuisine="火锅")
    _patch_catalog(monkeypatch, [poi_a, poi_new], [rb1, rb_new])

    utterance = f"把{poi_a.name}换掉"
    diff = store_swap_node(_state(itinerary, intent, user_input=utterance))

    new_ids = _node_ids(diff["itinerary"])
    assert "PA_NEW" in new_ids
    assert "RB1" in new_ids, "点名换店只换点中的那个，其它节点不动"
    assert diff["swapped_out_entity_ids"] == ["PA1"]


# ============================================================
# 7. 点名换店 + 目标恰好锁定：诚实告知，不静默执行也不静默跳过
# ============================================================


def test_named_swap_locked_target_produces_honest_advisory_and_no_change(monkeypatch):
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    poi_new = _poi(poi_id="PA_NEW")
    _patch_catalog(monkeypatch, [poi_a, poi_new], [rb1])

    pinned = [{"kind": "poi", "target_id": "PA1", "name": poi_a.name}]
    utterance = f"把{poi_a.name}换掉"
    diff = store_swap_node(
        _state(itinerary, intent, user_input=utterance, pinned=pinned)
    )

    assert _node_ids(diff["itinerary"]) == _node_ids(itinerary), "锁定节点点名换店不应真的换"
    assert any(
        a["code"] == AdvisoryCode.SWAP_TARGET_LOCKED.value for a in diff["advisories"]
    ), diff["advisories"]
    assert diff["swapped_out_entity_ids"] == []


# ============================================================
# 8. 防御性早退：无 itinerary/intent 时原样透传
# ============================================================


def test_no_itinerary_or_intent_returns_empty_diff():
    assert store_swap_node({"itinerary": None, "intent": None, "user_input": "换个店铺"}) == {}
