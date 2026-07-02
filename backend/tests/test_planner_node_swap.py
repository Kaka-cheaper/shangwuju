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
from schemas.domain import Location, Poi, PoiCapacity, Restaurant, RestaurantCapacity
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
) -> IntentExtraction:
    return IntentExtraction(
        start_time=start_time,
        duration_hours=duration_hours if duration_hours is not None else [1, 10],
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
    poi_type: str = "公园",
    opening: str = "08:00-22:00",
    suggested: int = 60,
    dist: float = 3.0,
    tags: list[str] | None = None,
) -> Poi:
    return Poi(
        id=poi_id,
        name=f"POI-{poi_id}",
        type=poi_type,
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=dist,
        opening_hours=opening,
        rating=4.5,
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
        tags=tags or [],
        suitable_for=[],
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
