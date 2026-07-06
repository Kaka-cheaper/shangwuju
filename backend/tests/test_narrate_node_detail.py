"""tests.test_narrate_node_detail —— 节点「真实数据详情」(node_detail) 组装与下发。

ADR-0015「事实/计算归确定性代码与数据，绝不让 LLM 编造」在 ItineraryCard
的现场体现：narrate 阶段为每个活动节点构造 `node_detail`（评分/价钱/距离/
可订/标签/营业——全部从真实 `Poi`/`Restaurant` 实体字段派生），与既有
`node_actions` 并列挂进 `AGENT_NARRATION` payload。

覆盖三层（结构对齐 `test_narrate_node_actions.py`）：

1. `agent.graph.nodes.narrate._build_node_detail`——直接单测组装逻辑：
   餐厅/POI 字段映射、诚实红线（无可用槽→"需排队"不冒充、售罄→"约满"不
   隐瞒、字段缺失→优雅省略不编造）、home 节点不产出、实体查不到时跳过。
2. `agent.graph.nodes.narrate.narrate_node`——整体集成：真实 Itinerary +
   全量目录候选池 → `result["node_detail"]` 形状完整。
3. `agent.graph._emit_handlers.emit_narrate`——SSE payload 组装契约：
   `node_detail` 作为 `AGENT_NARRATION` payload 的兄弟字段、无内容不加字段
   （与 `node_actions` 同一先例，镜像同一条 emit 路径）。
4. 图级（stub）：真实编译图跑通，`node_detail` 经 SSE 到达前端。
"""

from __future__ import annotations

import asyncio

from agent.graph import sse_adapter as sse
from agent.graph._emit_context import EmitContext
from agent.graph._emit_handlers import emit_narrate
from agent.graph.nodes import narrate as narrate_mod
from agent.graph.nodes.narrate import _build_node_detail, narrate_node
from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
from agent.planning.planners.activity_pool import build_visit_from_poi, build_visit_from_restaurant
from agent.planning.planners.route_builder import make_commute_fn, route_to_blueprint
from agent.planning.planners.route_scheduler import schedule_route
from agent.planning.weights_llm import get_planning_weights
from data.loader import load_user_profile
from schemas.domain import (
    Location,
    Poi,
    PoiCapacity,
    ReservationSlot,
    Restaurant,
    RestaurantCapacity,
)
from schemas.intent import IntentExtraction
from schemas.itinerary import ActivityNode, Hop, Itinerary


# ============================================================
# 共享 fixture helpers（风格对齐 test_narrate_node_actions.py）
# ============================================================


def _intent() -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-07-02T14:00",
        duration_hours=[1, 10],
        distance_max_km=50.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="独处放空",
        raw_input="测试",
        parse_confidence=0.9,
        ambiguous_fields=[],
    )


def _poi(
    *,
    poi_id: str,
    opening: str = "09:00-18:00",
    rating: float = 4.6,
    distance_km: float = 4.2,
    age_range=None,
    price_range=None,
    tags=None,
    available_slots: int = 45,
    daily_quota=200,
) -> Poi:
    return Poi(
        id=poi_id, name=f"POI-{poi_id}", type="公园",
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=distance_km, opening_hours=opening, rating=rating,
        age_range=age_range, price_range=price_range,
        tags=tags or [], suitable_for=[], suggested_duration_minutes=60,
        capacity=PoiCapacity(daily_quota=daily_quota, available_slots=available_slots),
    )


def _rest(
    *,
    rest_id: str,
    opening: str = "10:30-21:30",
    rating: float = 4.5,
    distance_km: float = 2.1,
    avg_price: float = 100.0,
    capacity: RestaurantCapacity | None = None,
    reservation_slots=None,
    tags=None,
) -> Restaurant:
    return Restaurant(
        id=rest_id, name=f"REST-{rest_id}", cuisine="火锅",
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=distance_km, opening_hours=opening, avg_price=avg_price, rating=rating,
        typical_dining_min=60,
        capacity=capacity or RestaurantCapacity(),
        reservation_slots=reservation_slots if reservation_slots is not None else [
            ReservationSlot(time="17:00", available=True, queue_minutes=0),
        ],
        tags=tags or [], suitable_for=[],
    )


def _build_itinerary(intent: IntentExtraction, entities: list, depart_min: int = 14 * 60) -> Itinerary:
    """与 resolve_node_swap/feasible_alternatives 内部同一条构造路径（同
    test_narrate_node_actions.py 的做法）——用于"整体集成"层级的测试，不
    追求控制每个节点精确的 start_time（那由 `_minimal_itinerary_with_node`
    覆盖）。"""
    weights = get_planning_weights(intent, client=None)
    profile = load_user_profile()
    commute_fn = make_commute_fn(profile)
    visits = [
        build_visit_from_poi(e, intent, weights) if isinstance(e, Poi) else build_visit_from_restaurant(e, intent, weights)
        for e in entities
    ]
    schedule = schedule_route(visits, depart_min=depart_min, budget_min=600, commute_fn=commute_fn)
    assert schedule is not None, "fixture 构造失败：候选组合本身不可行"
    blueprint = route_to_blueprint(schedule, intent, depart_min)
    return assemble_from_blueprint(intent, blueprint, profile)


def _minimal_itinerary_with_node(*, target_kind: str, target_id: str, start_time: str) -> Itinerary:
    """直接构造一个只含单节点的最小合法 Itinerary（跳过调度器），用于精确
    控制被测节点的 `start_time`（`_build_node_detail` 的「最近可用槽」判定
    要用到它）。Itinerary 的 5 条 model_validator 不变量只约束 home 首尾 +
    hops 长度，不校验 hop 时间的自洽性，故可以这样直接拼装（见
    `schemas/itinerary.py::Itinerary._check_invariants`）。"""
    home = ActivityNode(
        node_id="n_home_start", kind="出发", target_kind="home", target_id="home",
        start_time="14:00", duration_min=0, title="从家出发",
    )
    mid = ActivityNode(
        node_id="n_0", kind="主活动" if target_kind == "poi" else "用餐",
        target_kind=target_kind, target_id=target_id,
        start_time=start_time, duration_min=60, title="测试节点",
    )
    home_end = ActivityNode(
        node_id="n_home_end", kind="返回", target_kind="home", target_id="home",
        start_time="20:00", duration_min=0, title="返回家中",
    )
    hop0 = Hop(
        hop_id="h_0", from_node_id="n_home_start", to_node_id="n_0",
        start_time="14:00", minutes=15, mode="taxi", path_type="estimated",
    )
    hop1 = Hop(
        hop_id="h_1", from_node_id="n_0", to_node_id="n_home_end",
        start_time=start_time, minutes=15, mode="taxi", path_type="estimated",
    )
    return Itinerary(
        summary="测试方案", nodes=[home, mid, home_end], hops=[hop0, hop1],
        total_minutes=360,
    )


# ============================================================
# 1. _build_node_detail：组装逻辑单测
# ============================================================


def test_build_node_detail_restaurant_field_mapping():
    """餐厅字段映射：rating/avg_price→price_text/distance_km/reservation_slots
    →availability_text/capacity→桌型 tag/opening_hours→open_until_text。"""
    rest = _rest(
        rest_id="R1", rating=4.5, distance_km=2.1, avg_price=100.0,
        opening="10:30-21:30",
        capacity=RestaurantCapacity(private_room=True),
        reservation_slots=[
            ReservationSlot(time="17:00", available=False, queue_minutes=0),
            ReservationSlot(time="17:30", available=True, queue_minutes=0),
        ],
        tags=["粤菜", "礼仪感", "适合老人"],  # "礼仪感" 是 EXPERIENCE_TAGS 词典项
    )
    itinerary = _minimal_itinerary_with_node(
        target_kind="restaurant", target_id="R1", start_time="17:15"
    )

    detail = _build_node_detail(itinerary, pois=[], restaurants=[rest])

    assert "R1" in detail
    d = detail["R1"]
    assert d["kind"] == "restaurant"
    assert d["rating"] == 4.5
    assert d["price_text"] == "¥100/人"
    assert d["distance_km"] == 2.1
    assert d["availability_text"] == "可订17:30"
    assert "有包间" in d["tags"]
    assert "礼仪感" in d["tags"]
    assert len(d["tags"]) <= 2
    assert d["open_until_text"] == "营业至21:30"


def test_build_node_detail_poi_field_mapping():
    """POI 字段映射：rating/price_range→price_text/distance_km/
    capacity.available_slots→availability_text/age_range→适龄 tag/
    opening_hours→open_until_text。"""
    poi = _poi(
        poi_id="P1", rating=4.6, distance_km=4.2,
        opening="09:00-18:00",
        age_range=[3, 10], price_range=[80, 120],
        tags=["亲子友好", "户外"],  # "户外" 是 EXPERIENCE_TAGS 词典项
        available_slots=45,
    )
    itinerary = _minimal_itinerary_with_node(
        target_kind="poi", target_id="P1", start_time="14:15"
    )

    detail = _build_node_detail(itinerary, pois=[poi], restaurants=[])

    assert "P1" in detail
    d = detail["P1"]
    assert d["kind"] == "poi"
    assert d["rating"] == 4.6
    assert d["price_text"] == "¥80–120"
    assert d["distance_km"] == 4.2
    assert d["availability_text"] == "余45"
    assert "适合3-10岁" in d["tags"]
    assert "户外" in d["tags"]
    assert len(d["tags"]) <= 2
    assert d["open_until_text"] == "营业至18:00"


def test_build_node_detail_restaurant_no_available_slot_shows_need_queue():
    """诚实红线①：一个可用槽都没有 → 显式"需排队"，绝不冒充可订某个时段。"""
    rest = _rest(
        rest_id="R2",
        reservation_slots=[
            ReservationSlot(time="17:00", available=False, queue_minutes=10),
            ReservationSlot(time="17:30", available=False, queue_minutes=20),
        ],
    )
    itinerary = _minimal_itinerary_with_node(
        target_kind="restaurant", target_id="R2", start_time="17:15"
    )

    detail = _build_node_detail(itinerary, pois=[], restaurants=[rest])

    assert detail["R2"]["availability_text"] == "需排队"


def test_build_node_detail_restaurant_no_reservation_system_omits_availability():
    """该店根本没有预约槽表（不是"满"，是"不适用"）→ 省略该展示位，不硬造
    "需排队"（那是给"有槽表但全占满"场景的诚实措辞，两种情况语义不同）。"""
    rest = _rest(rest_id="R3", reservation_slots=[])
    itinerary = _minimal_itinerary_with_node(
        target_kind="restaurant", target_id="R3", start_time="17:15"
    )

    detail = _build_node_detail(itinerary, pois=[], restaurants=[rest])

    assert "availability_text" not in detail["R3"]


def test_build_node_detail_poi_sold_out_shows_full():
    """诚实红线②：POI available_slots==0 → 如实"约满"，不隐瞒、不省略。"""
    poi = _poi(poi_id="P2", available_slots=0)
    itinerary = _minimal_itinerary_with_node(
        target_kind="poi", target_id="P2", start_time="14:15"
    )

    detail = _build_node_detail(itinerary, pois=[poi], restaurants=[])

    assert detail["P2"]["availability_text"] == "约满"


def test_build_node_detail_poi_missing_price_range_shows_free():
    """price_range=None 是 Poi schema 文档明确语义"免费"（不是缺失），如实
    显示"免费"而非省略或编造一个价格。"""
    poi = _poi(poi_id="P3", price_range=None)
    itinerary = _minimal_itinerary_with_node(
        target_kind="poi", target_id="P3", start_time="14:15"
    )

    detail = _build_node_detail(itinerary, pois=[poi], restaurants=[])

    assert detail["P3"]["price_text"] == "免费"


def test_build_node_detail_poi_missing_age_range_omits_age_tag_gracefully():
    """诚实红线③：age_range 缺失（None）→ 优雅省略适龄 tag，不编造年龄区间。"""
    poi = _poi(poi_id="P4", age_range=None, tags=["拍照友好"])
    itinerary = _minimal_itinerary_with_node(
        target_kind="poi", target_id="P4", start_time="14:15"
    )

    detail = _build_node_detail(itinerary, pois=[poi], restaurants=[])

    tags = detail["P4"]["tags"]
    assert not any(t.startswith("适合") and "岁" in t for t in tags)
    assert "拍照友好" in tags


def test_build_node_detail_restaurant_default_capacity_omits_table_tag():
    """capacity 全默认（只有 2/4 座，无 6/8/private_room）→ 桌型无特别可说，
    优雅省略，不硬凑一个"标准桌"之类的编造标签。"""
    rest = _rest(rest_id="R4", capacity=RestaurantCapacity(), tags=[])
    itinerary = _minimal_itinerary_with_node(
        target_kind="restaurant", target_id="R4", start_time="17:15"
    )

    detail = _build_node_detail(itinerary, pois=[], restaurants=[rest])

    assert detail["R4"]["tags"] == []


def test_build_node_detail_restaurant_dedupes_table_tag_against_raw_tags():
    """R002 型真实数据场景：capacity.private_room=True 派生"有包间"，原始
    tags 列表里恰好也字面包含"有包间"（mock 真实存在的重叠）——不该展示
    两次相同文案。"""
    rest = _rest(
        rest_id="R5",
        capacity=RestaurantCapacity(private_room=True, six=True, eight=True),
        tags=["粤菜", "高人均", "有包间", "礼仪感"],
    )
    itinerary = _minimal_itinerary_with_node(
        target_kind="restaurant", target_id="R5", start_time="17:15"
    )

    detail = _build_node_detail(itinerary, pois=[], restaurants=[rest])

    tags = detail["R5"]["tags"]
    assert tags.count("有包间") == 1
    assert len(tags) <= 2


def test_build_node_detail_restaurant_nearest_slot_not_just_earliest():
    """"最近一个 available 槽"是离该节点排定 start_time 最近的一个，不是
    全天最早的一个——用一组 (17:30 可订 / 19:00 可订、节点排在 18:50) 的
    场景区分两种算法：若实现只取"最早可用"会错误挑 17:30。"""
    rest = _rest(
        rest_id="R6",
        reservation_slots=[
            ReservationSlot(time="17:30", available=True, queue_minutes=0),
            ReservationSlot(time="19:00", available=True, queue_minutes=0),
        ],
    )
    itinerary = _minimal_itinerary_with_node(
        target_kind="restaurant", target_id="R6", start_time="18:50"
    )

    detail = _build_node_detail(itinerary, pois=[], restaurants=[rest])

    assert detail["R6"]["availability_text"] == "可订19:00"


def test_build_node_detail_home_node_produces_no_entry():
    """home 节点不产出 node_detail。"""
    itinerary = _minimal_itinerary_with_node(
        target_kind="poi", target_id="P5", start_time="14:15"
    )
    poi = _poi(poi_id="P5")

    detail = _build_node_detail(itinerary, pois=[poi], restaurants=[])

    assert "home" not in detail


def test_build_node_detail_skips_entity_not_found_in_catalog():
    """选中实体不在（monkeypatch 出错等原因导致的）候选池里时，该节点整个
    跳过，不崩、不编造一份假详情。"""
    itinerary = _minimal_itinerary_with_node(
        target_kind="poi", target_id="P_MISSING", start_time="14:15"
    )

    detail = _build_node_detail(itinerary, pois=[], restaurants=[])

    assert detail == {}


def test_build_node_detail_multi_node_covers_all_non_home():
    """多节点方案：POI + 餐厅都各自产出 node_detail（整体集成走
    scheduler 全流程，风格对齐 test_narrate_node_actions.py）。"""
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    detail = _build_node_detail(itinerary, pois=[poi_a], restaurants=[rb1])

    assert set(detail.keys()) == {"PA1", "RB1"}
    assert detail["PA1"]["kind"] == "poi"
    assert detail["RB1"]["kind"] == "restaurant"


# ============================================================
# 2. narrate_node：集成——真实 state + 全量目录候选池
# ============================================================


def test_narrate_node_result_includes_node_detail(monkeypatch):
    """narrate_node 应和 node_actions 一样从全量目录反查 node_detail（同一份
    候选池，同一寻址轴 target_id）。"""
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    monkeypatch.setattr(narrate_mod, "load_pois", lambda: [poi_a])
    monkeypatch.setattr(narrate_mod, "load_restaurants", lambda: [rb1])

    state = {
        "intent": intent,
        "itinerary": itinerary,
        "user_id": "demo_user",
    }
    result = narrate_node(state)

    assert "node_detail" in result
    node_detail = result["node_detail"]
    assert set(node_detail.keys()) >= {"PA1", "RB1"}
    assert node_detail["PA1"]["kind"] == "poi"
    assert node_detail["RB1"]["kind"] == "restaurant"


# ============================================================
# 3. emit_narrate：SSE payload 组装契约（镜像 node_actions 同一先例）
# ============================================================


def _minimal_itinerary() -> Itinerary:
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    return _build_itinerary(intent, [poi_a])


def test_emit_narrate_attaches_node_detail_sibling_field_when_present():
    ctx = EmitContext()
    itin = _minimal_itinerary()
    node_detail = {"PA1": {"kind": "poi", "rating": 4.6, "tags": []}}
    diff = {"narration": "文案", "itinerary": itin, "advisories": [], "node_detail": node_detail}

    events = emit_narrate(ctx, diff)
    assert len(events) == 1
    narr = events[0]
    assert narr.type.value == "agent_narration"
    assert narr.payload["node_detail"] == node_detail


def test_emit_narrate_omits_node_detail_when_missing():
    ctx = EmitContext()
    itin = _minimal_itinerary()
    diff = {"narration": "文案", "itinerary": itin, "advisories": []}  # 无 node_detail 键

    events = emit_narrate(ctx, diff)
    narr = next(e for e in events if e.type.value == "agent_narration")
    assert "node_detail" not in narr.payload


def test_emit_narrate_omits_node_detail_when_empty_dict():
    ctx = EmitContext()
    itin = _minimal_itinerary()
    diff = {"narration": "文案", "itinerary": itin, "advisories": [], "node_detail": {}}

    events = emit_narrate(ctx, diff)
    narr = next(e for e in events if e.type.value == "agent_narration")
    assert "node_detail" not in narr.payload


def test_emit_narrate_carries_both_node_actions_and_node_detail_independently():
    """两个兄弟字段互不影响——node_actions 缺失不该连累 node_detail 消失，
    反之亦然。"""
    ctx = EmitContext()
    itin = _minimal_itinerary()
    node_detail = {"PA1": {"kind": "poi", "rating": 4.6, "tags": []}}
    diff = {
        "narration": "文案", "itinerary": itin, "advisories": [],
        "node_detail": node_detail,
        # 故意不给 node_actions
    }

    events = emit_narrate(ctx, diff)
    narr = next(e for e in events if e.type.value == "agent_narration")
    assert "node_detail" in narr.payload
    assert "node_actions" not in narr.payload


# ============================================================
# 4. 图级（stub）：narrate_node 在真实编译图里正确算出 node_detail，
#    且经 AGENT_NARRATION 透传到 SSE
# ============================================================


from agent.routing.canonical_shortcut import DEMO_SCENARIOS  # noqa: E402

_USER_INPUT = DEMO_SCENARIOS[1]["input"]  # S2："今晚和兄弟出来撸串喝点酒，人均 50 左右就行"


def _drive(*, user_input: str, session_id: str) -> list:
    async def _run() -> list:
        evs = []
        async for ev in sse.run_graph_stream(user_input=user_input, session_id=session_id, user_id="demo_user"):
            evs.append(ev)
        return evs

    return asyncio.run(_run())


def test_graph_level_node_detail_reaches_agent_narration_payload():
    """图级 stub 测试（真实编译图，S2 canonical 短路）：node_detail 应随
    AGENT_NARRATION 到达前端，且形状里带真实字段（不是空壳）。"""
    evs = _drive(user_input=_USER_INPUT, session_id="node_detail_graph_probe")
    types = [e.type.value for e in evs]
    assert "itinerary_ready" in types, f"应正常出方案，events={types}"

    ready = next(e for e in evs if e.type.value == "itinerary_ready")
    assert "node_detail" not in ready.payload, "ITINERARY_READY 必须保持纯 Itinerary dump"

    narr = next(e for e in evs if e.type.value == "agent_narration")
    assert "node_detail" in narr.payload, "node_detail 应挂在 AGENT_NARRATION 兄弟字段"
    node_detail = narr.payload["node_detail"]
    assert isinstance(node_detail, dict) and node_detail, f"node_detail 应非空，got={node_detail!r}"
    sample = next(iter(node_detail.values()))
    assert sample.get("kind") in ("poi", "restaurant")
    # 至少一项真实数据字段非空——不是一个只有 kind 的空壳
    assert any(sample.get(k) for k in ("rating", "price_text", "distance_km", "availability_text", "tags"))
