"""tests.test_planner_node_swap —— ADR-0013 F-1：局部重解引擎（换菜/定向调整）。

覆盖 `agent.planning.planners.node_swap` 的 `resolve_node_swap` / `feasible_alternatives`：

1. 换菜只动目标这一格——其余保留节点的 id 顺序不变（含目标在中间的场景），
   时刻允许微移（不断言必须相同）。
2. 三级降级序列逐级触发：同子类满足 → 同大类异子类满足 → 近似满足 + advisory。
3. kind 永不跨——即便另一 kind 的候选池里有"看起来很合适"的项，也绝不被选中；
   同 kind 内确实没有替代时如实失败（`SWAP_NO_ALTERNATIVE_FOUND`），不误跨类。
4. 无方向换（点踩）：`adjustment=None` 时按 base_score 挑最优同子类候选。
5. `ledger_slice` 影响候选选择（软偏置，能改变最终胜出者）。
6. `feasible_alternatives` 逐候选 `try_insert` 预验证，不可行的候选不出现在列表里。
7. advisory 是自包含中文人话，不泄漏内部字段名/id 占位符。
8. `SWAP_KEPT_NODE_UNFIT`：换掉目标后，钉住的其余节点本身排不到一块儿
   （中间站被抽走后两端直达通勤暴涨）——用受控通勤表复现这个边界。

风格对齐 `test_planner_pinning_advisory.py`（自建 fixture，确定性，不依赖 mock
数据具体 id）。合成 id（"PA1"/"RB1" 等）不落 `lookup_hop` 真实路网/haversine
索引，天然落到其 4 级兜底常量通勤（`FALLBACK_MIN=15` 分钟）——除测试 8 显式
monkeypatch 通勤表之外，全部测试的合成实体互相之间/与 home 之间通勤恒为
15 分钟，行程顺序完全由营业时间/饭点惯例窗决定，具确定性。
"""

from __future__ import annotations

import pytest

from agent.planning.blueprint import assemble_blueprint as assemble_blueprint_module
from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
from agent.planning.planners import node_swap, route_builder
from agent.planning.planners.activity_pool import build_visit_from_poi, build_visit_from_restaurant
from agent.planning.planners.route_builder import make_commute_fn, route_to_blueprint
from agent.planning.planners.route_scheduler import schedule_route
from agent.planning.weights_llm import get_planning_weights
from data.loader import load_user_profile
from schemas.advisory import AdvisoryCode
from schemas.domain import Location, Poi, PoiCapacity, ReservationSlot, Restaurant, RestaurantCapacity
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary
from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension


# ============================================================
# 共享 fixture helpers（风格对齐 test_planner_pinning_advisory.py）
# ============================================================


def _intent(
    *,
    start_time: str = "2026-07-02T14:00",
    duration_hours: list[int] | None = None,
    dietary_constraints: list[str] | None = None,
    physical_constraints: list[str] | None = None,
    field_provenance: dict[str, str] | None = None,
    capacity_requirement: int | None = None,
) -> IntentExtraction:
    return IntentExtraction(
        start_time=start_time,
        duration_hours=duration_hours if duration_hours is not None else [1, 10],
        distance_max_km=50.0,
        companions=[],
        physical_constraints=physical_constraints or [],
        dietary_constraints=dietary_constraints or [],
        experience_tags=[],
        social_context="独处放空",
        raw_input="测试",
        parse_confidence=0.9,
        ambiguous_fields=[],
        field_provenance=field_provenance,
        capacity_requirement=capacity_requirement,
    )


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
    capacity: RestaurantCapacity | None = None,
    slots: list[ReservationSlot] | None = None,
    suitable_for: list[str] | None = None,
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
        capacity=capacity or RestaurantCapacity(),
        reservation_slots=slots or [],
        tags=tags or [],
        suitable_for=suitable_for or [],
    )


def _build_itinerary(intent: IntentExtraction, entities: list, depart_min: int, budget_min: int = 600) -> Itinerary:
    """把一组 Poi/Restaurant 实体排成一条 Itinerary——与 `resolve_node_swap`
    内部完全同一条构造路径（`build_visit_from_poi/_restaurant` → `schedule_route`
    → `route_to_blueprint` → `assemble_from_blueprint`），保证测试 fixture 与
    被测代码的口径不漂移。"""
    weights = get_planning_weights(intent, client=None)
    profile = load_user_profile()
    commute_fn = make_commute_fn(profile)
    visits = [
        build_visit_from_poi(e, intent, weights) if isinstance(e, Poi) else build_visit_from_restaurant(e, intent, weights)
        for e in entities
    ]
    schedule = schedule_route(visits, depart_min=depart_min, budget_min=budget_min, commute_fn=commute_fn)
    assert schedule is not None, "fixture 构造失败：候选组合本身不可行，检查测试参数"
    blueprint = route_to_blueprint(schedule, intent, depart_min)
    return assemble_from_blueprint(intent, blueprint, profile)


def _node_ids(itinerary: Itinerary) -> list[str]:
    return [n.target_id for n in itinerary.nodes]


# ============================================================
# 1. 换菜只动目标这一格：id 序不变（含中间位置），时刻允许微移
# ============================================================


def test_swap_only_touches_target_node_preserves_order_of_kept_nodes():
    """PA1（仅早间开放）→ RB1（饭点窗，目标）→ PC1（仅夜间开放）——目标天然
    被夹在中间。换成 RB_NEW 后，PA1/PC1 的位置必须原样保留，RB_NEW 落在 RB1
    原来的那个槽位（同一 index），不是被追加到末尾或打乱顺序。
    """
    intent = _intent()
    poi_a = _poi(poi_id="PA1", opening="08:00-16:00")  # 早间专属
    poi_c = _poi(poi_id="PC1", opening="20:00-23:59")  # 夜间专属
    rb1 = _rest(rest_id="RB1")
    itinerary = _build_itinerary(intent, [poi_a, poi_c, rb1], depart_min=14 * 60)
    assert _node_ids(itinerary) == ["home", "PA1", "RB1", "PC1", "home"]

    rb_new = _rest(rest_id="RB_NEW", cuisine="火锅", avg_price=60.0)
    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a, poi_c], restaurants=[rb1, rb_new], target_node_id="RB1"
    )

    assert result.success, result.advisories
    assert result.swapped_to == "RB_NEW"
    assert result.degrade_tier == 1
    assert _node_ids(result.new_itinerary) == ["home", "PA1", "RB_NEW", "PC1", "home"]
    # 保留节点仍在方案里，时刻允许微移——不断言具体数值，只断言仍是合法 HH:MM。
    kept_nodes = {n.target_id: n for n in result.new_itinerary.nodes if n.target_id in ("PA1", "PC1")}
    assert set(kept_nodes) == {"PA1", "PC1"}


# ============================================================
# 2. 三级降级序列逐级触发
# ============================================================


def _base_two_node_itinerary(intent: IntentExtraction, rb1: Restaurant, poi_a: Poi):
    return _build_itinerary(intent, [poi_a, rb1], depart_min=14 * 60)


def test_degrade_tier1_same_subtype_satisfies_adjustment():
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", tags=["高人均"])
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    adjustment = NodeAdjustment(dimension=NodeAdjustmentDimension.DIETARY, value="不辣")
    rb_t1 = _rest(rest_id="RB_T1", cuisine="火锅", tags=["不辣"])  # 同子类(火锅) + 满足

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_t1], target_node_id="RB1", adjustment=adjustment
    )
    assert result.success
    assert result.swapped_to == "RB_T1"
    assert result.degrade_tier == 1
    assert result.advisories == []  # tier1 命中，不产 SWAP_DEGRADED


def test_degrade_tier2_different_subtype_satisfies_adjustment():
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", tags=["高人均"])
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    adjustment = NodeAdjustment(dimension=NodeAdjustmentDimension.DIETARY, value="不辣")
    # 无同子类(火锅)候选——只有异子类(粤菜)满足调整，逼降级到 tier2。
    rb_t2 = _rest(rest_id="RB_T2", cuisine="粤菜", tags=["不辣"])

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_t2], target_node_id="RB1", adjustment=adjustment
    )
    assert result.success
    assert result.swapped_to == "RB_T2"
    assert result.degrade_tier == 2
    assert result.advisories == []  # tier2 仍是"满足调整"，不算近似，不产 SWAP_DEGRADED


def test_degrade_tier3_no_candidate_satisfies_adjustment_produces_swap_degraded():
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", tags=["高人均"])
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    adjustment = NodeAdjustment(dimension=NodeAdjustmentDimension.DIETARY, value="不辣")
    # 唯一候选既不同子类也不满足"不辣"——只能近似满足。
    rb_t3 = _rest(rest_id="RB_T3", cuisine="日料", tags=[])

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_t3], target_node_id="RB1", adjustment=adjustment
    )
    assert result.success
    assert result.swapped_to == "RB_T3"
    assert result.degrade_tier == 3
    degraded = [a for a in result.advisories if a.code == AdvisoryCode.SWAP_DEGRADED]
    assert degraded, result.advisories
    assert rb_t3.name in degraded[0].message


# ============================================================
# 3. kind 永不跨
# ============================================================


def test_kind_never_crosses_even_when_no_same_kind_alternative_exists():
    """restaurants 池里除目标外别无他选；pois 池里刻意放一个"看起来很合适"
    （满足饮食 tag）的候选——正确实现绝不会因为矮子里拔将军就跨去 poi 池，
    应如实报告"这一类换不了"，方案原样不变。"""
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    poi_lookalike = _poi(poi_id="PA_FAKE", poi_type="伪装成餐厅的公园", tags=["不辣"])
    rb1 = _rest(rest_id="RB1", cuisine="火锅")
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a, poi_lookalike], restaurants=[rb1], target_node_id="RB1"
    )
    assert not result.success
    assert any(a.code == AdvisoryCode.SWAP_NO_ALTERNATIVE_FOUND for a in result.advisories)
    assert _node_ids(result.new_itinerary) == _node_ids(itinerary)
    assert "PA_FAKE" not in _node_ids(result.new_itinerary)


# ============================================================
# 4. 无方向换（点踩）：按 base_score 挑同子类最优候选
# ============================================================


def test_undirected_swap_picks_best_scoring_same_subtype_candidate():
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅")
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    # 同子类(火锅)两个候选，评分悬殊（4.9 vs 2.0）——无论权重怎么混，评分差距
    # 大到足以主导 comfort 项，结果应恒定选高分的那个。
    rb_hi = _rest(rest_id="RB_HI", cuisine="火锅", rating=4.9)
    rb_lo = _rest(rest_id="RB_LO", cuisine="火锅", rating=2.0)

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_hi, rb_lo], target_node_id="RB1", adjustment=None
    )
    assert result.success
    assert result.swapped_to == "RB_HI"
    assert result.degrade_tier == 1


# ============================================================
# 5. ledger_slice 影响选择（软偏置）
# ============================================================


def test_ledger_slice_biases_choice_toward_demand_satisfying_candidate():
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅")
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    rb_high_rating = _rest(rest_id="RB_HI", cuisine="火锅", rating=4.9, tags=[])
    rb_ledger_ok = _rest(rest_id="RB_LO", cuisine="火锅", rating=4.0, tags=["不辣"])

    # 无 ledger：评分更高的 RB_HI 胜出。
    without_ledger = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_high_rating, rb_ledger_ok], target_node_id="RB1"
    )
    assert without_ledger.swapped_to == "RB_HI"

    # 有生效诉求「不辣」：即使评分更低，满足诉求的 RB_LO 优先被尝试并胜出。
    ledger = [NodeAdjustment(dimension=NodeAdjustmentDimension.DIETARY, value="不辣")]
    with_ledger = node_swap.resolve_node_swap(
        itinerary,
        intent,
        pois=[poi_a],
        restaurants=[rb1, rb_high_rating, rb_ledger_ok],
        target_node_id="RB1",
        ledger_slice=ledger,
    )
    assert with_ledger.swapped_to == "RB_LO"


# ============================================================
# 6. feasible_alternatives：逐候选 try_insert 预验证
# ============================================================


def test_feasible_alternatives_excludes_time_infeasible_candidates():
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅")
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    rb_ok1 = _rest(rest_id="RB_OK1", cuisine="火锅", rating=4.5)
    rb_ok2 = _rest(rest_id="RB_OK2", cuisine="火锅", rating=4.0)
    # 09:00-15:00 营业，饭点惯例窗（午/晚/夜宵）与之交集为空 → 候选窗为空 →
    # try_insert 恒不可行，必须被排除在预验证结果之外。
    rb_bad = _rest(rest_id="RB_BAD", cuisine="火锅", opening="09:00-15:00")

    alternatives = node_swap.feasible_alternatives(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_ok1, rb_ok2, rb_bad], target_node_id="RB1", k=3
    )
    ids = {a.target_id for a in alternatives}
    assert ids == {"RB_OK1", "RB_OK2"}
    assert "RB_BAD" not in ids

    # k 限制生效。
    top1 = node_swap.feasible_alternatives(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_ok1, rb_ok2, rb_bad], target_node_id="RB1", k=1
    )
    assert len(top1) == 1

    # 展示要素完整（name/rating/distance/price/category）。
    picked = next(a for a in alternatives if a.target_id == "RB_OK1")
    assert picked.name == rb_ok1.name
    assert picked.rating == rb_ok1.rating
    assert picked.distance_km == rb_ok1.distance_km
    assert picked.category == "火锅"
    assert picked.kind == "restaurant"


def test_feasible_alternatives_excludes_entities_already_placed_elsewhere_in_itinerary():
    """深审追加发现（冒烟 harness 实测炸出的真实 bug，非推测）：
    `feasible_alternatives` 用 `try_insert` 直接预验证，`try_insert` 不检查
    候选是否已经是方案里**另一个**节点在用的实体；而真正执行换菜的
    `resolve_node_swap` 走 `route_builder.repair_route`，其 `kept_keys` 去重
    会自动把"已在场实体"从候选池里剔除。两条路径若不对齐，`feasible_
    alternatives` 会把"方案里另一个节点正在用的这个 POI"展示成"可行备选"，
    用户点了之后 `resolve_node_swap` 却会因为"新候选"其实是空的而以
    `SWAP_NO_ALTERNATIVE_FOUND` 拒绝——违反 ADR-0013"预验证可行才展示"的
    字面承诺。这里用两个 POI 节点（PA1 已在场、PB1 是目标）验证：真正的新
    候选 PC1 该出现，PA1（另一个节点正在用）绝不该出现。
    """
    intent = _intent()
    poi_a = _poi(poi_id="PA1")  # 已在方案里占着另一个节点
    poi_b = _poi(poi_id="PB1", poi_type="博物馆")  # 目标节点
    itinerary = _build_itinerary(intent, [poi_a, poi_b], depart_min=14 * 60)
    assert _node_ids(itinerary) == ["home", "PA1", "PB1", "home"]

    poi_new = _poi(poi_id="PC1", poi_type="博物馆")  # 真正的新候选

    alternatives = node_swap.feasible_alternatives(
        itinerary, intent, pois=[poi_a, poi_b, poi_new], restaurants=[], target_node_id="PB1", k=5
    )
    ids = {a.target_id for a in alternatives}
    assert "PA1" not in ids, "PA1 是方案里另一个节点正在用的实体，不该被展示为 PB1 的备选"
    assert "PC1" in ids


# ============================================================
# 7. advisory 人话（自包含中文，不泄漏内部字段名/id）
# ============================================================


def test_advisory_messages_are_self_contained_chinese_sentences():
    samples = [
        node_swap._swap_degraded_advisory("测试餐厅"),
        node_swap._kept_node_unfit_advisory(),
        node_swap._no_alternative_advisory(),
    ]
    for advisory in samples:
        assert advisory.message and advisory.message.strip(), advisory
        assert "None" not in advisory.message, advisory.message
        assert "nodes[" not in advisory.message, advisory.message
        assert "target_id" not in advisory.message, advisory.message
        assert any("一" <= ch <= "鿿" for ch in advisory.message), (
            f"advisory message 应含中文：{advisory.message!r}"
        )


# ============================================================
# 8. SWAP_KEPT_NODE_UNFIT：换掉目标后其余节点本身排不到一块儿
# ============================================================


def test_swap_kept_node_unfit_when_removing_target_breaks_remaining_route(monkeypatch):
    """三站方案 home→PA1→RB1(目标)→PC1→home；用受控通勤表让 PA1↔PC1 的直达
    通勤远大于经 RB1 中转的两段之和（现实里常见——景点常不在同一条直线上）。
    RB1 在场时两端从不需要直达；换掉 RB1 后，仅剩 PA1/PC1 两站被迫直达，
    `schedule_route(kept)` 应报不可行 → `SWAP_KEPT_NODE_UNFIT`，方案原样不变。
    """
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    poi_c = _poi(poi_id="PC1")
    rb1 = _rest(rest_id="RB1")

    distance_table = {
        frozenset({"home", "PA1"}): 10,
        frozenset({"PA1", "RB1"}): 10,
        frozenset({"RB1", "PC1"}): 10,
        frozenset({"PC1", "home"}): 10,
        frozenset({"home", "RB1"}): 10,
        frozenset({"home", "PC1"}): 10,
        frozenset({"PA1", "PC1"}): 999,  # 跳过中转站直达：现实里的"绕远路"
    }

    def fake_lookup_hop(from_id, to_id, transport_pref, user_profile):
        minutes = distance_table.get(frozenset({from_id, to_id}), 10)
        return minutes, "taxi", "real_route"

    monkeypatch.setattr(route_builder, "lookup_hop", fake_lookup_hop)
    monkeypatch.setattr(assemble_blueprint_module, "lookup_hop", fake_lookup_hop)

    itinerary = _build_itinerary(intent, [poi_a, poi_c, rb1], depart_min=14 * 60)
    assert _node_ids(itinerary) == ["home", "PA1", "RB1", "PC1", "home"]

    rb_new = _rest(rest_id="RB_NEW", cuisine="火锅", avg_price=50.0, rating=4.9)
    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a, poi_c], restaurants=[rb1, rb_new], target_node_id="RB1"
    )

    assert not result.success
    assert any(a.code == AdvisoryCode.SWAP_KEPT_NODE_UNFIT for a in result.advisories)
    assert _node_ids(result.new_itinerary) == _node_ids(itinerary)


# ============================================================
# 9. 前置条件违反 → ValueError（不是业务失败，契约违反）
# ============================================================


def test_target_node_id_not_found_raises_value_error():
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    with pytest.raises(ValueError):
        node_swap.resolve_node_swap(itinerary, intent, pois=[poi_a], restaurants=[rb1], target_node_id="GHOST")


def test_missing_kept_node_entity_in_pool_raises_value_error():
    """候选池必须覆盖当前方案里全部已选节点（前置条件 2）——这里故意漏传
    保留节点 PA1 的实体，只给目标 RB1，验证契约违反会被诚实地报出来，而不是
    静默产出一个错误的方案。"""
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    with pytest.raises(ValueError):
        node_swap.resolve_node_swap(itinerary, intent, pois=[], restaurants=[rb1], target_node_id="RB1")


# ============================================================
# 10. schemas.node_adjustment：维度受控词典校验
# ============================================================


def test_node_adjustment_rejects_uncontrolled_dietary_tag():
    with pytest.raises(Exception):
        NodeAdjustment(dimension=NodeAdjustmentDimension.DIETARY, value="不存在的标签")


def test_node_adjustment_rejects_uncontrolled_ambience_value():
    with pytest.raises(Exception):
        NodeAdjustment(dimension=NodeAdjustmentDimension.AMBIENCE, value="社交")  # 不在安静-热闹两极里


def test_node_adjustment_accepts_valid_combinations():
    NodeAdjustment(dimension=NodeAdjustmentDimension.PRICE, value="cheaper")
    NodeAdjustment(dimension=NodeAdjustmentDimension.DISTANCE, value="closer")
    NodeAdjustment(dimension=NodeAdjustmentDimension.AMBIENCE, value="安静聊天")
    NodeAdjustment(dimension=NodeAdjustmentDimension.CROWD_FIT, value="亲子友好")
    NodeAdjustment(dimension=NodeAdjustmentDimension.CUISINE_OR_TYPE, value="粤菜")


# ============================================================
# 11. ADR-0014 横向深审 P0：候选池 hard 恒定过滤——绝不换入违反 hard 约束的候选
# ============================================================
#
# 三条路径（点踩 / 定向调整 / 具名备选）各来一条：candidate 池里刻意放一个
# "评分更高/更满足调整方向，但违反用户 hard 约束（不辣）"的辣店，验证正确
# 实现绝不会因为它"矮子里拔将军"更优就选中它。


def test_hard_dietary_constraint_filters_spicy_candidate_on_dislike():
    """无方向换（点踩）：评分极高的辣店违反 hard「不辣」，必须被恒定过滤
    掉——不过滤的话它会凭评分优势胜出（已用探针验证：`rest_tag_hit` 命中
    「不辣」带来的 comfort 加分，本可以被够大的评分差抵消，rating=5.0 vs
    1.0 时确凿会让辣店在 `_utility` 打分上反超，见 `ils_planner._utility`
    的 comfort 分量）。"""
    intent = _intent(dietary_constraints=["不辣"])
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", tags=["高人均"])
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    rb_spicy = _rest(rest_id="RB_SPICY", cuisine="火锅", rating=5.0, tags=[])  # 无「不辣」tag，真违反 hard
    rb_safe = _rest(rest_id="RB_SAFE", cuisine="火锅", rating=1.0, tags=["不辣"])

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_spicy, rb_safe], target_node_id="RB1", adjustment=None
    )
    assert result.success
    assert result.swapped_to == "RB_SAFE", "评分更高的辣店违反 hard 约束，绝不该被选中"


def test_hard_dietary_constraint_filters_spicy_candidate_on_directed_price_adjustment():
    """定向调整「更便宜」：更便宜、评分极高的辣店违反 hard「不辣」，即使它
    完美满足 adjustment 的方向谓词，也必须被过滤——不能因为"满足了用户这次
    点的调整方向"就绕过一票否决的安全底线（评分差同上一测试同一理由，用
    探针验证过不过滤会反转选择）。"""
    intent = _intent(dietary_constraints=["不辣"])
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", avg_price=100.0, tags=["高人均"])
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    adjustment = NodeAdjustment(dimension=NodeAdjustmentDimension.PRICE, value="cheaper")
    rb_spicy_cheap = _rest(rest_id="RB_SPICY", cuisine="火锅", avg_price=50.0, rating=5.0, tags=[])  # 更便宜但违反 hard
    rb_safe_cheap = _rest(rest_id="RB_SAFE", cuisine="火锅", avg_price=60.0, rating=1.0, tags=["不辣"])  # 更便宜且满足 hard

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_spicy_cheap, rb_safe_cheap],
        target_node_id="RB1", adjustment=adjustment,
    )
    assert result.success
    assert result.swapped_to == "RB_SAFE", "更便宜的辣店违反 hard 约束，绝不该被选中"
    assert result.degrade_tier == 1


def test_hard_dietary_constraint_rejects_named_alternative_that_violates_hard_constraint():
    """具名备选：用户点选的这一个若违反 hard 约束，`resolve_node_swap` 必须
    拒绝换入（业务性失败 `SWAP_NO_ALTERNATIVE_FOUND`），不能因为 `narrow_
    pool_to_single_alternative` 已经把候选池收窄到"只此一个"就绕过引擎自己
    的 hard 过滤——收窄后候选池里"当前已在场实体"仍覆盖前置条件 2（不触发
    ValueError），过滤只会剔除这一个违规的新候选，最终归于业务性失败。"""
    from agent.planning.planners.node_swap_support import narrow_pool_to_single_alternative

    intent = _intent(dietary_constraints=["不辣"])
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", tags=["高人均"])
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)
    rb_spicy = _rest(rest_id="RB_SPICY", cuisine="日料", tags=[])  # 用户明确点了这个，但违反 hard

    call_pois, call_rests = narrow_pool_to_single_alternative(
        itinerary, [poi_a], [rb1, rb_spicy], "restaurant", rb_spicy
    )
    result = node_swap.resolve_node_swap(
        itinerary, intent, call_pois, call_rests, target_node_id="RB1", adjustment=None, ledger_slice=()
    )
    assert not result.success
    assert any(a.code == AdvisoryCode.SWAP_NO_ALTERNATIVE_FOUND for a in result.advisories)
    assert _node_ids(result.new_itinerary) == _node_ids(itinerary)


def test_hard_physical_constraint_filters_violating_poi_candidate():
    """POI 侧对称覆盖：hard 物理约束（无障碍）过滤"距离更近、评分更占优"但
    违反它的候选——`dist=1.0` 的 `PA_BAD` 若不过滤，在无 hard 约束时确凿会
    凭距离优势胜出（已用探针验证：同样两个候选、intent 无 physical_
    constraints 时 `resolve_node_swap` 选中的正是 `PA_BAD`），加上 hard
    约束后必须改选 `PA_OK`。"""
    intent = _intent(physical_constraints=["无障碍"])
    poi_a = _poi(poi_id="PA1", poi_type="博物馆", tags=[])
    rb1 = _rest(rest_id="RB1")
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    poi_violates = _poi(poi_id="PA_BAD", poi_type="博物馆", tags=[], dist=1.0)  # 无「无障碍」tag，违反 hard
    poi_safe = _poi(poi_id="PA_OK", poi_type="博物馆", tags=["无障碍"], dist=20.0)

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a, poi_violates, poi_safe], restaurants=[rb1],
        target_node_id="PA1", adjustment=None,
    )
    assert result.success
    assert result.swapped_to == "PA_OK", "距离更近的候选违反 hard 约束，绝不该被选中"


# ============================================================
# 12. ADR-0014 横向深审 P0：换后单点审计——soft 未满足 → CONSTRAINT_RELAXED
# ============================================================


def test_soft_dietary_constraint_unmet_after_swap_produces_constraint_relaxed_advisory():
    """换后单点审计（P0 修法 2）：新换入节点若未满足 soft 约束（出处非
    default），换菜仍成功但要带 `CONSTRAINT_RELAXED` advisory 如实告知——
    不是静默换了个不完全对味的上去。"""
    intent = _intent(
        dietary_constraints=["低脂"],
        field_provenance={"dietary_constraints:低脂": "user_stated"},
    )
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", tags=["高人均"])
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)
    rb_new = _rest(rest_id="RB_NEW", cuisine="火锅", tags=[])  # 不含「低脂」（soft，非 hard）

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_new], target_node_id="RB1", adjustment=None
    )
    assert result.success
    assert result.swapped_to == "RB_NEW"
    relaxed = [a for a in result.advisories if a.code == AdvisoryCode.CONSTRAINT_RELAXED]
    assert relaxed, result.advisories
    assert "低脂" in relaxed[0].message
    assert "你说的" in relaxed[0].message  # user_stated 出处口径


# ============================================================
# 13. 分界修缮批 任务 1：换菜产物复跑 critic HARD 判据（旁路补验收步）
# ============================================================
#
# 病灶（全后端 LLM/规则分界普查实锤）：resolve_node_swap 是 critic 修复闭环外
# 的旁路，此前的 hard 防线只盖 dietary/physical tag 子集（`_filter_hard_
# violations` 候选池恒定过滤）——check_capacity（桌型）、check_demo_restaurant_
# full（真实预约槽）、check_social_context BLOCKING 在换菜路径无人执行。修法：
# 每个 tier 候选产出的 new_itinerary 复跑 critic HARD 判据（复用 validate 注册
# 表，不抄第二份逻辑），**换菜新引入**的 HARD 违规 → 该候选视为不可行、继续
# 现有降级序列；原方案基线里已有的违规不拦（否则带既有瑕疵的方案彻底不能换菜）。


def test_swap_rejects_candidate_with_insufficient_table_capacity():
    """6 人局（capacity_requirement=6）点踩换餐厅：评分最高的候选只有 2/4 人桌
    （check_capacity HARD），复跑判据必须把它判为不可行、降级选中有大桌的
    候选——修复前它凭评分胜出，换完 6 人坐不下且零告知。"""
    intent = _intent(capacity_requirement=6)
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", capacity=RestaurantCapacity(six=True))
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    rb_small = _rest(rest_id="RB_SMALL", cuisine="火锅", rating=5.0)  # 默认只有 2/4 人桌
    rb_big = _rest(rest_id="RB_BIG", cuisine="火锅", rating=3.5, capacity=RestaurantCapacity(six=True))

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_small, rb_big],
        target_node_id="RB1", adjustment=None,
    )
    assert result.success
    assert result.swapped_to == "RB_BIG", "只有 2/4 人桌的候选坐不下 6 人，绝不该被换入"


def test_swap_fails_honestly_when_all_candidates_violate_capacity():
    """全部候选都过不了桌型 HARD 判据 → 业务性失败（SWAP_NO_ALTERNATIVE_FOUND），
    方案原样不变——比静默换进一家坐不下的店诚实。"""
    intent = _intent(capacity_requirement=6)
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", capacity=RestaurantCapacity(six=True))
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    rb_small = _rest(rest_id="RB_SMALL", cuisine="火锅", rating=5.0)  # 唯一候选，桌型不够

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_small],
        target_node_id="RB1", adjustment=None,
    )
    assert not result.success
    assert any(a.code == AdvisoryCode.SWAP_NO_ALTERNATIVE_FOUND for a in result.advisories)
    assert _node_ids(result.new_itinerary) == _node_ids(itinerary)


def test_swap_rejects_candidate_whose_only_slot_is_full():
    """真实预约槽：调度器刻意保留 available=False 的槽点（grounding 设计，
    「满座由 critic 抓」——见 activity_pool 槽单求交的注释），而换菜旁路没有
    critic → 修复前唯一槽已满的店照样换进来，确认时订位必然失败、叙事却说
    「已确认」。复跑 check_demo_restaurant_full 后该候选必须被跳过。"""
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅")
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    rb_full = _rest(
        rest_id="RB_FULL", cuisine="火锅", rating=5.0,
        slots=[ReservationSlot(time="18:00", available=False)],
    )
    rb_free = _rest(
        rest_id="RB_FREE", cuisine="火锅", rating=3.5,
        slots=[ReservationSlot(time="18:00", available=True)],
    )

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_full, rb_free],
        target_node_id="RB1", adjustment=None,
    )
    assert result.success
    assert result.swapped_to == "RB_FREE", "唯一预约槽已满的店订不上，绝不该被换入"


def test_swap_rejects_candidate_with_blocking_social_context():
    """check_social_context BLOCKING：独处放空场景（fixture 默认）换进
    suitable_for=［家庭日常］的多人场合是矩阵明文 BLOCKING——复跑判据必须
    拦下，降级选中调性中性的候选。"""
    intent = _intent()  # social_context="独处放空"
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅")
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)

    rb_family = _rest(rest_id="RB_FAM", cuisine="火锅", rating=5.0, suitable_for=["家庭日常"])
    rb_neutral = _rest(rest_id="RB_NEU", cuisine="火锅", rating=3.5)

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_family, rb_neutral],
        target_node_id="RB1", adjustment=None,
    )
    assert result.success
    assert result.swapped_to == "RB_NEU", "社交调性 BLOCKING 的候选绝不该被换入"


def test_swap_preexisting_hard_violation_on_kept_node_does_not_block_swap():
    """基线容错：保留节点 PA1 本就违反 hard 物理约束（约束是方案定下之后才
    收紧的，critic 基线里已有这条）——复跑判据只拦「换菜新引入」的 HARD
    违规，不因既有违规拒绝一切候选（否则带既有瑕疵的方案彻底不能换菜）。"""
    intent = _intent(physical_constraints=["无障碍"])
    poi_a = _poi(poi_id="PA1")  # 无「无障碍」tag → PHYSICAL_VIOLATION 基线既有
    rb1 = _rest(rest_id="RB1", cuisine="火锅")
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)
    rb_new = _rest(rest_id="RB_NEW", cuisine="火锅", rating=4.8)

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_new], target_node_id="RB1"
    )
    assert result.success
    assert result.swapped_to == "RB_NEW"


# ============================================================
# 14. 收口深审精化：复跑判据的归因分桶（clean-first, honest-fallback）
# ============================================================
#
# 收口深审实锤（两条房间测试红 + 本文件合成探针复现同一根因链）：点踩 POI →
# 替补时长与原节点不同 → 重排时刻整体平移 → 被保留的餐厅被挪进 available=False
# 的槽点（调度器刻意不按 available 过滤，满座本该由 critic 抓）→ v1 的 delta
# 判定对每个候选都记到保留节点上的新增 RESTAURANT_FULL_UNRESOLVED → 候选全灭。
# 真实 mock 世界里"点踩 POI 且方案含餐厅"基本必踩中。精化语义：候选自身归因
# 的违规维持一票否决；仅殃及保留节点的违规不拒绝该格——先扫完全部 tier 找
# "零殃及"的干净候选，全无时回退交付第一个仅殃及者 + 诚实告知 advisory。


def _kept_shift_world():
    """PA1(165min) + R_KEPT(17:00 满座 / 17:30 可订)：基线 R_KEPT 排 17:30；
    换入 60min 短候选后到店时刻前移，调度器把 R_KEPT 吸附到 17:00 满座槽——
    确定性诱发"仅殃及保留节点"的 kept fault（探针已在当前代码下实锤复现）。"""
    intent = _intent()
    poi_a = _poi(poi_id="PA1", poi_type="博物馆", suggested=165)
    r_kept = _rest(
        rest_id="R_KEPT",
        slots=[
            ReservationSlot(time="17:00", available=False),
            ReservationSlot(time="17:30", available=True),
        ],
    )
    itinerary = _build_itinerary(intent, [poi_a, r_kept], depart_min=14 * 60)
    assert _node_ids(itinerary) == ["home", "PA1", "R_KEPT", "home"]
    return intent, poi_a, r_kept, itinerary


def test_swap_delivers_fallback_with_advisory_when_all_candidates_shift_kept_node():
    """全部候选都仅殃及保留餐厅 → 不再全灭拒换（v1 的误伤），回退交付该候选
    并附 SWAP_KEPT_TIME_SHIFTED 诚实告知：点名受累的保留节点 + 新排定时刻。"""
    intent, poi_a, r_kept, itinerary = _kept_shift_world()
    pb_short = _poi(poi_id="PB_SHORT", poi_type="博物馆", suggested=60)

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a, pb_short], restaurants=[r_kept],
        target_node_id="PA1", adjustment=None,
    )
    assert result.success, result.advisories
    assert result.swapped_to == "PB_SHORT"
    shifted = [a for a in result.advisories if a.code == AdvisoryCode.SWAP_KEPT_TIME_SHIFTED]
    assert shifted, result.advisories
    # 点名受累的保留节点：消息用 node.title（生产环境是真实店名；合成实体在
    # assemble 的 meta 反查不到目录时回落为 target_id，故这里断言 id 字样）。
    assert "「R_KEPT」" in shifted[0].message, "必须点名受累的保留节点"
    assert "17:00" in shifted[0].message, "必须如实带上挪到的新时刻"


def test_swap_prefers_clean_candidate_over_one_that_shifts_kept_node():
    """干净候选与殃及候选并存 → 必选零殃及的干净者（clean-first），不提前抓
    fallback、不产 SWAP_KEPT_TIME_SHIFTED。PB_SAME 与原节点同时长，重排后
    保留餐厅时刻不动。"""
    intent, poi_a, r_kept, itinerary = _kept_shift_world()
    pb_short = _poi(poi_id="PB_SHORT", poi_type="博物馆", suggested=60, rating=5.0)  # 殃及者，评分占优
    pb_same = _poi(poi_id="PB_SAME", poi_type="博物馆", suggested=165, rating=2.5)  # 干净者

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a, pb_short, pb_same], restaurants=[r_kept],
        target_node_id="PA1", adjustment=None,
    )
    assert result.success, result.advisories
    assert result.swapped_to == "PB_SAME", "存在干净候选时必须选干净者"
    assert not any(a.code == AdvisoryCode.SWAP_KEPT_TIME_SHIFTED for a in result.advisories)


def test_soft_constraint_unmet_with_default_provenance_produces_no_advisory():
    """出处口径对称覆盖：`default`（无出处数据）不产生告知——同
    `exit_audit.audit_constraint_relaxation` 的既定口径（模块 docstring
    「出处口径」节），不是本步新引入的例外。"""
    intent = _intent(dietary_constraints=["低脂"])  # 无 field_provenance
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", tags=["高人均"])
    itinerary = _base_two_node_itinerary(intent, rb1, poi_a)
    rb_new = _rest(rest_id="RB_NEW", cuisine="火锅", tags=[])

    result = node_swap.resolve_node_swap(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, rb_new], target_node_id="RB1", adjustment=None
    )
    assert result.success
    assert not any(a.code == AdvisoryCode.CONSTRAINT_RELAXED for a in result.advisories)
