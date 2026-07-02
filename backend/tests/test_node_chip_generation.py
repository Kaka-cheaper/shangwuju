"""tests.test_node_chip_generation —— ADR-0013 F-3：节点调整按钮的生成与校验。

覆盖三层：

1. `schemas.node_chip.NodeChip`——契约本身（label ≤8 字硬校验 / extra=forbid /
   委托 NodeAdjustment 校验 dimension+value 合法组合）。
2. `agent.intent.narrator.generate_template_node_chips`——stub/rule 模式地板
   （确定性模板，按 kind 走规则表，见该函数 docstring）：每 kind 的规则表
   逐条覆盖、≤3 上限、"只生成该节点管得了的调整"（`NodeAdjustmentDimension`
   本身只有 6 个节点级维度、没有路线级维度可选，本文件用"生成出的维度必须
   落在该 kind 的预期子集里"操作化这条断言）。
3. `agent.intent.narrator._validate_llm_node_chips` +
   `generate_title_and_narration`——LLM 搭车产出的校验与"不半信半用"整体
   回落模板生成器的判定逻辑。

风格对齐 `tests/test_planner_node_swap.py`（自建 fixture，不依赖 mock 数据
具体 id，确定性）。
"""

from __future__ import annotations

import pytest

from agent.intent.narrator import (
    _node_chip_context,
    _validate_llm_node_chips,
    generate_template_node_chips,
    generate_title_and_narration,
)
from schemas.domain import Location, Poi, PoiCapacity, Restaurant, RestaurantCapacity
from schemas.intent import IntentExtraction
from schemas.itinerary import ActivityNode, Hop, Itinerary
from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension
from schemas.node_chip import NodeChip


# ============================================================
# 共享 fixture helpers
# ============================================================


def _intent(*, dietary=None, physical=None) -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-07-02T14:00",
        duration_hours=[4, 6],
        distance_max_km=10.0,
        companions=[],
        physical_constraints=physical or [],
        dietary_constraints=dietary or [],
        experience_tags=[],
        social_context="独处放空",
        raw_input="测试",
        parse_confidence=0.9,
        ambiguous_fields=[],
    )


def _poi(*, poi_id: str, poi_type: str = "公园", tags: list[str] | None = None) -> Poi:
    return Poi(
        id=poi_id,
        name=f"POI-{poi_id}",
        type=poi_type,
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=3.0,
        opening_hours="08:00-22:00",
        rating=4.5,
        tags=tags or [],
        suitable_for=[],
        capacity=PoiCapacity(daily_quota=100, available_slots=50),
    )


def _rest(*, rest_id: str, cuisine: str = "火锅", tags: list[str] | None = None) -> Restaurant:
    return Restaurant(
        id=rest_id,
        name=f"REST-{rest_id}",
        cuisine=cuisine,
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=3.0,
        opening_hours="11:00-23:00",
        avg_price=100.0,
        rating=4.3,
        capacity=RestaurantCapacity(),
        tags=tags or [],
        suitable_for=[],
    )


def _itinerary_with_nodes(node_specs: list[tuple]) -> Itinerary:
    """按 (target_kind, target_id, title) 列表拼一份最小合法 Itinerary（home 首尾
    自动补齐）——只关心 nodes 的 target_kind/target_id 字段，不关心排程真实性
    （本文件测的是"生成器读 itinerary.nodes 的行为"，不是排程算法本身）。"""
    nodes = [
        ActivityNode(
            node_id="n_home_start", kind="起点", target_kind="home",
            target_id="home", start_time="14:00", duration_min=0, title="家",
        )
    ]
    for i, (kind, tid, title) in enumerate(node_specs):
        nodes.append(
            ActivityNode(
                node_id=f"n_{i}", kind="主活动" if kind == "poi" else "用餐",
                target_kind=kind, target_id=tid,
                start_time=f"{14 + i}:30", duration_min=60, title=title,
            )
        )
    nodes.append(
        ActivityNode(
            node_id="n_home_end", kind="终点", target_kind="home",
            target_id="home", start_time="20:00", duration_min=0, title="家",
        )
    )
    hops = [
        Hop(hop_id=f"h{i}", from_node_id=nodes[i].node_id, to_node_id=nodes[i + 1].node_id,
            start_time="14:00", minutes=10, mode="taxi", path_type="estimated")
        for i in range(len(nodes) - 1)
    ]
    return Itinerary(summary="占位", nodes=nodes, hops=hops, total_minutes=300)


# ============================================================
# 1. schemas.node_chip.NodeChip 契约
# ============================================================


def test_node_chip_valid_construction():
    chip = NodeChip(
        node_id="R001", label="更近的",
        adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.DISTANCE, value="closer"),
    )
    assert chip.node_id == "R001"
    assert chip.label == "更近的"


def test_node_chip_rejects_label_over_8_chars():
    with pytest.raises(Exception):
        NodeChip(
            node_id="R001", label="这是一个超过八个字的按钮文案",
            adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.PRICE, value="cheaper"),
        )


def test_node_chip_rejects_empty_label():
    with pytest.raises(Exception):
        NodeChip(
            node_id="R001", label="",
            adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.PRICE, value="cheaper"),
        )


def test_node_chip_forbids_extra_fields():
    with pytest.raises(Exception):
        NodeChip(
            node_id="R001", label="更便宜的",
            adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.PRICE, value="cheaper"),
            extra_field="不该存在",
        )


def test_node_chip_delegates_adjustment_dict_validation():
    """adjustment 传字典时仍会走 NodeAdjustment 的受控词典校验（pydantic 嵌套
    校验），非法 value 应报错——不是 NodeChip 自己另开一套校验。"""
    with pytest.raises(Exception):
        NodeChip(
            node_id="R001", label="不辣的",
            adjustment={"dimension": "dietary", "value": "不存在的标签"},
        )


# ============================================================
# 2. generate_template_node_chips：按 kind 的确定性模板
# ============================================================

_RESTAURANT_ALLOWED_DIMS = {
    NodeAdjustmentDimension.PRICE,
    NodeAdjustmentDimension.AMBIENCE,
    NodeAdjustmentDimension.DIETARY,
}
_POI_ALLOWED_DIMS = {
    NodeAdjustmentDimension.DISTANCE,
    NodeAdjustmentDimension.AMBIENCE,
    NodeAdjustmentDimension.CROWD_FIT,
}


def test_template_chips_restaurant_price_always_present_and_dims_restricted():
    intent = _intent()
    rest = _rest(rest_id="R1", tags=[])
    itin = _itinerary_with_nodes([("restaurant", "R1", "测试餐厅")])
    chips = generate_template_node_chips(itin, intent, pois=[], restaurants=[rest])

    dims = {c.adjustment.dimension for c in chips}
    assert NodeAdjustmentDimension.PRICE in dims
    price_chip = next(c for c in chips if c.adjustment.dimension == NodeAdjustmentDimension.PRICE)
    assert price_chip.adjustment.value == "cheaper"
    # 只生成该节点管得了的调整：restaurant 绝不出现 distance/crowd_fit（POI 专属维度）
    assert dims <= _RESTAURANT_ALLOWED_DIMS
    assert len(chips) <= 3


def test_template_chips_restaurant_ambience_reversed_when_tag_present():
    intent = _intent()
    quiet_rest = _rest(rest_id="R1", tags=["安静聊天"])
    itin = _itinerary_with_nodes([("restaurant", "R1", "安静的店")])
    chips = generate_template_node_chips(itin, intent, pois=[], restaurants=[quiet_rest])
    ambience = next(c for c in chips if c.adjustment.dimension == NodeAdjustmentDimension.AMBIENCE)
    assert ambience.adjustment.value == "热闹"  # 反向

    lively_rest = _rest(rest_id="R2", tags=["热闹"])
    itin2 = _itinerary_with_nodes([("restaurant", "R2", "热闹的店")])
    chips2 = generate_template_node_chips(itin2, intent, pois=[], restaurants=[lively_rest])
    ambience2 = next(c for c in chips2 if c.adjustment.dimension == NodeAdjustmentDimension.AMBIENCE)
    assert ambience2.adjustment.value == "安静聊天"


def test_template_chips_restaurant_no_ambience_chip_when_no_signal():
    """既没有"安静聊天"也没有"热闹" tag → 没有锚点可反，不生成 ambience chip
    （不瞎猜，见 `_reverse_ambience` docstring）。"""
    intent = _intent()
    rest = _rest(rest_id="R1", tags=["高人均"])
    itin = _itinerary_with_nodes([("restaurant", "R1", "无氛围标签的店")])
    chips = generate_template_node_chips(itin, intent, pois=[], restaurants=[rest])
    assert not any(c.adjustment.dimension == NodeAdjustmentDimension.AMBIENCE for c in chips)


def test_template_chips_restaurant_dietary_only_when_intent_signal():
    rest = _rest(rest_id="R1", tags=[])
    itin = _itinerary_with_nodes([("restaurant", "R1", "测试餐厅")])

    # 无信号：不生成 dietary chip
    no_signal_chips = generate_template_node_chips(itin, _intent(), pois=[], restaurants=[rest])
    assert not any(c.adjustment.dimension == NodeAdjustmentDimension.DIETARY for c in no_signal_chips)

    # 有信号：取 intent.dietary_constraints[0] 作为目标值
    with_signal_chips = generate_template_node_chips(
        itin, _intent(dietary=["不辣"]), pois=[], restaurants=[rest]
    )
    dietary_chip = next(c for c in with_signal_chips if c.adjustment.dimension == NodeAdjustmentDimension.DIETARY)
    assert dietary_chip.adjustment.value == "不辣"


def test_template_chips_poi_distance_always_present_and_dims_restricted():
    intent = _intent()
    poi = _poi(poi_id="P1", tags=[])
    itin = _itinerary_with_nodes([("poi", "P1", "测试景点")])
    chips = generate_template_node_chips(itin, intent, pois=[poi], restaurants=[])

    dims = {c.adjustment.dimension for c in chips}
    assert NodeAdjustmentDimension.DISTANCE in dims
    dist_chip = next(c for c in chips if c.adjustment.dimension == NodeAdjustmentDimension.DISTANCE)
    assert dist_chip.adjustment.value == "closer"
    # 只生成该节点管得了的调整：poi 绝不出现 price/dietary（restaurant 专属维度）
    assert dims <= _POI_ALLOWED_DIMS
    assert len(chips) <= 3


def test_template_chips_poi_crowd_fit_only_when_intent_signal():
    poi = _poi(poi_id="P1", tags=[])
    itin = _itinerary_with_nodes([("poi", "P1", "测试景点")])

    no_signal = generate_template_node_chips(itin, _intent(), pois=[poi], restaurants=[])
    assert not any(c.adjustment.dimension == NodeAdjustmentDimension.CROWD_FIT for c in no_signal)

    with_signal = generate_template_node_chips(
        itin, _intent(physical=["亲子友好"]), pois=[poi], restaurants=[]
    )
    crowd_chip = next(c for c in with_signal if c.adjustment.dimension == NodeAdjustmentDimension.CROWD_FIT)
    assert crowd_chip.adjustment.value == "亲子友好"


def test_template_chips_cap_at_three_per_node_even_when_all_signals_present():
    poi = _poi(poi_id="P1", tags=["安静聊天"])
    itin = _itinerary_with_nodes([("poi", "P1", "测试景点")])
    chips = generate_template_node_chips(
        itin, _intent(physical=["适合老人"]), pois=[poi], restaurants=[]
    )
    assert len(chips) == 3  # distance + ambience(reverse) + crowd_fit 全部触发


def test_template_chips_skip_home_nodes():
    intent = _intent()
    poi = _poi(poi_id="P1")
    itin = _itinerary_with_nodes([("poi", "P1", "测试景点")])
    chips = generate_template_node_chips(itin, intent, pois=[poi], restaurants=[])
    assert all(c.node_id != "home" for c in chips)


def test_template_chips_skip_node_missing_from_pool_without_crashing():
    """候选池查不到对应实体的节点静默跳过（展示层的降级纪律，不是
    node_swap 那种"候选池必须覆盖全部已选节点"硬前置条件）。"""
    intent = _intent()
    itin = _itinerary_with_nodes([("poi", "P_GHOST", "查无此地")])
    chips = generate_template_node_chips(itin, intent, pois=[], restaurants=[])
    assert chips == []


def test_template_chip_labels_always_within_8_chars_including_spaced_tag():
    """PHYSICAL_TAGS 里"适合 5-10 岁"带内部空格（原文 9 字），是 label 长度
    最容易溢出的边界值——`_compact_chip_label` 去空格后应恰好落在 8 字内。"""
    poi = _poi(poi_id="P1", tags=[])
    itin = _itinerary_with_nodes([("poi", "P1", "测试景点")])
    chips = generate_template_node_chips(
        itin, _intent(physical=["适合 5-10 岁"]), pois=[poi], restaurants=[]
    )
    crowd_chip = next(c for c in chips if c.adjustment.dimension == NodeAdjustmentDimension.CROWD_FIT)
    assert len(crowd_chip.label) <= 8, crowd_chip.label


# ============================================================
# 3. _validate_llm_node_chips：LLM 搭车产出的"不半信半用"校验
# ============================================================


def test_validate_llm_node_chips_accepts_well_formed_list():
    valid_ids = {"R001", "P001"}
    raw = [
        {"node_id": "R001", "label": "更便宜的", "dimension": "price", "value": "cheaper"},
        {"node_id": "P001", "label": "更近的", "dimension": "distance", "value": "closer"},
    ]
    parsed = _validate_llm_node_chips(raw, valid_ids)
    assert len(parsed) == 2
    assert {c.node_id for c in parsed} == valid_ids


def test_validate_llm_node_chips_rejects_non_list():
    assert _validate_llm_node_chips("not a list", {"R001"}) == []
    assert _validate_llm_node_chips(None, {"R001"}) == []


def test_validate_llm_node_chips_rejects_unknown_node_id():
    raw = [{"node_id": "GHOST", "label": "更便宜的", "dimension": "price", "value": "cheaper"}]
    assert _validate_llm_node_chips(raw, {"R001"}) == []


def test_validate_llm_node_chips_rejects_illegal_dimension_value_combo():
    raw = [{"node_id": "R001", "label": "热闹的", "dimension": "ambience", "value": "社交"}]
    assert _validate_llm_node_chips(raw, {"R001"}) == []


def test_validate_llm_node_chips_rejects_label_over_8_chars():
    raw = [{"node_id": "R001", "label": "这是一个超过八字的按钮", "dimension": "price", "value": "cheaper"}]
    assert _validate_llm_node_chips(raw, {"R001"}) == []


def test_validate_llm_node_chips_one_bad_item_invalidates_whole_batch():
    """"不半信半用"：批次里有一条不合法，整批（含其它本来合法的条目）都作废，
    不是"挑出合法的那几条凑合用"。"""
    raw = [
        {"node_id": "R001", "label": "更便宜的", "dimension": "price", "value": "cheaper"},
        {"node_id": "R001", "label": "坏数据", "dimension": "price", "value": "not_a_direction"},
    ]
    assert _validate_llm_node_chips(raw, {"R001"}) == []


def test_validate_llm_node_chips_caps_per_node_at_three_without_invalidating():
    """数量超标是"太热情"不是"不合法"——裁剪到前 3 个，不整体作废。"""
    raw = [
        {"node_id": "R001", "label": "更便宜的", "dimension": "price", "value": "cheaper"},
        {"node_id": "R001", "label": "更热闹", "dimension": "ambience", "value": "热闹"},
        {"node_id": "R001", "label": "不辣的", "dimension": "dietary", "value": "不辣"},
        {"node_id": "R001", "label": "粤菜", "dimension": "cuisine_or_type", "value": "粤菜"},
    ]
    parsed = _validate_llm_node_chips(raw, {"R001"})
    assert len(parsed) == 3


# ============================================================
# 4. generate_title_and_narration：LLM node_chips 集成 + 回落
# ============================================================


class _Resp:
    def __init__(self, content: str):
        self.content = content


def _bbq_itinerary_and_rest():
    rest = _rest(rest_id="R031", cuisine="烧烤", tags=[])
    itin = _itinerary_with_nodes([("restaurant", "R031", "炭烤大叔 · 路边烧烤")])
    return itin, rest


def test_generate_title_and_narration_uses_llm_node_chips_when_valid(monkeypatch):
    import agent.intent.narrator as narrator_mod

    itin, rest = _bbq_itinerary_and_rest()

    class JsonClient:
        provider = "deepseek"

        def chat(self, *, messages, temperature, **kw):
            return _Resp(
                '{"title": "烧烤局", "narration": "开场白。哪里不合适跟我说一声。", '
                '"node_chips": [{"node_id": "R031", "label": "更便宜的", '
                '"dimension": "price", "value": "cheaper"}]}'
            )

    monkeypatch.setattr(narrator_mod, "get_llm_client", lambda *a, **k: JsonClient())
    _title, _narration, chips = generate_title_and_narration(
        intent=_intent(), itinerary=itin, use_llm=True, restaurants=[rest],
    )
    assert len(chips) == 1
    assert chips[0].node_id == "R031"
    assert chips[0].adjustment.dimension == NodeAdjustmentDimension.PRICE


def test_generate_title_and_narration_falls_back_to_template_when_node_chips_illegal(monkeypatch):
    import agent.intent.narrator as narrator_mod

    itin, rest = _bbq_itinerary_and_rest()

    class BadChipClient:
        provider = "deepseek"

        def chat(self, *, messages, temperature, **kw):
            return _Resp(
                '{"title": "烧烤局", "narration": "开场白。哪里不合适跟我说一声。", '
                '"node_chips": [{"node_id": "R031", "label": "坏数据", '
                '"dimension": "price", "value": "not_a_real_direction"}]}'
            )

    monkeypatch.setattr(narrator_mod, "get_llm_client", lambda *a, **k: BadChipClient())
    _title, _narration, chips = generate_title_and_narration(
        intent=_intent(), itinerary=itin, use_llm=True, restaurants=[rest],
    )
    expected = generate_template_node_chips(itin, _intent(), pois=[], restaurants=[rest])
    assert [c.model_dump() for c in chips] == [c.model_dump() for c in expected]


def test_generate_title_and_narration_falls_back_to_template_when_node_chips_field_missing(monkeypatch):
    import agent.intent.narrator as narrator_mod

    itin, rest = _bbq_itinerary_and_rest()

    class NoChipsFieldClient:
        provider = "deepseek"

        def chat(self, *, messages, temperature, **kw):
            return _Resp('{"title": "烧烤局", "narration": "开场白。哪里不合适跟我说一声。"}')

    monkeypatch.setattr(narrator_mod, "get_llm_client", lambda *a, **k: NoChipsFieldClient())
    _title, _narration, chips = generate_title_and_narration(
        intent=_intent(), itinerary=itin, use_llm=True, restaurants=[rest],
    )
    expected = generate_template_node_chips(itin, _intent(), pois=[], restaurants=[rest])
    assert [c.model_dump() for c in chips] == [c.model_dump() for c in expected]


def test_generate_title_and_narration_rule_mode_uses_template_directly():
    itin, rest = _bbq_itinerary_and_rest()
    _title, _narration, chips = generate_title_and_narration(
        intent=_intent(), itinerary=itin, use_llm=False, restaurants=[rest],
    )
    expected = generate_template_node_chips(itin, _intent(), pois=[], restaurants=[rest])
    assert [c.model_dump() for c in chips] == [c.model_dump() for c in expected]


# ============================================================
# 5. _node_chip_context：喂给 LLM 的每节点提示（覆盖 + 跳过缺失实体）
# ============================================================


def test_node_chip_context_covers_pois_and_restaurants_skips_missing():
    intent = _intent()
    poi = _poi(poi_id="P1", tags=["安静聊天"])
    itin = _itinerary_with_nodes([("poi", "P1", "景点"), ("restaurant", "R_GHOST", "查无此店")])
    ctx = _node_chip_context(itin, pois=[poi], restaurants=[])
    assert len(ctx) == 1
    assert ctx[0]["node_id"] == "P1"
    assert ctx[0]["kind"] == "poi"
