"""tests.test_store_swap_router —— B2："换个店铺"聊天反馈分路判定。

覆盖 `agent.routing.store_swap_router.classify_store_swap`：
1. 泛化换店关键词命中 → mode="all"。
2. 点名换店（提到方案里某个节点的 title/片段）→ mode="named" + 正确的 target_node_id。
3. 点名消歧失败（0 个或 ≥2 个节点命中同一片段）→ 不算点名，退回泛化/其它判定。
4. 其它反馈（"太远了，帮我换近一点的地方"这类）→ None，维持既有全局重排路由
   不变——这是回归护栏，钉住"加法不改变现有行为"的承诺。
5. 点名优先于泛化：同一句话若同时命中点名与泛化关键词，按点名处理。
"""

from __future__ import annotations

from agent.routing.store_swap_router import (
    StoreSwapClassification,
    classify_store_swap,
    find_named_swap_target,
)


class _FakeNode:
    def __init__(self, target_kind: str, target_id: str, title: str):
        self.target_kind = target_kind
        self.target_id = target_id
        self.title = title


class _FakeItinerary:
    def __init__(self, nodes: list[_FakeNode]):
        self.nodes = nodes


def _itinerary(*specs: tuple[str, str, str]) -> _FakeItinerary:
    """specs: (target_kind, target_id, title) 三元组列表，自动补首尾 home 节点。"""
    nodes = [_FakeNode("home", "home", "出发")]
    nodes += [_FakeNode(k, i, t) for k, i, t in specs]
    nodes.append(_FakeNode("home", "home", "回家"))
    return _FakeItinerary(nodes)


# ============================================================
# 1. 泛化换店
# ============================================================


def test_generic_swap_keyword_classifies_as_all():
    itinerary = _itinerary(("restaurant", "R001", "轻语沙拉 · 西溪店"))
    for phrase in (
        "换个店铺", "换一批", "都换换", "换别的店", "一个店都没改怎么回事",
    ):
        result = classify_store_swap(phrase, itinerary)
        assert result is not None, f"{phrase!r} 应命中泛化换店"
        assert result.mode == "all", f"{phrase!r} 应判 mode=all，实际={result}"
        assert result.target_node_id is None


# ============================================================
# 2. 点名换店：entity linking
# ============================================================


def test_named_swap_matches_node_title_segment():
    itinerary = _itinerary(
        ("poi", "P026", "麦霸欢唱 KTV · 旗舰店"),
        ("restaurant", "R001", "轻语沙拉 · 西溪店"),
    )
    result = classify_store_swap("换掉那家KTV", itinerary)
    assert result == StoreSwapClassification(mode="named", target_node_id="P026")


def test_named_swap_matches_full_segment_business_name():
    itinerary = _itinerary(
        ("poi", "P026", "麦霸欢唱 KTV · 旗舰店"),
        ("restaurant", "R001", "老王烧烤 · 大排档"),
    )
    result = classify_store_swap("把老王烧烤换了", itinerary)
    assert result == StoreSwapClassification(mode="named", target_node_id="R001")


def test_find_named_swap_target_directly():
    itinerary = _itinerary(("restaurant", "R009", "独栖咖啡 · 单人友好"))
    assert find_named_swap_target("换掉独栖咖啡吧", itinerary) == "R009"
    assert find_named_swap_target("这个方案不错", itinerary) is None


# ============================================================
# 3. 点名消歧失败 → 不算点名
# ============================================================


def test_named_swap_ambiguous_two_matches_falls_back_to_none():
    """两个节点的片段恰好都是"旗舰店"这类共享后缀——消歧失败，不猜是哪一个，
    也不命中泛化关键词表，最终返回 None（维持现状路由到 refiner）。"""
    itinerary = _itinerary(
        ("poi", "P026", "麦霸欢唱 KTV · 旗舰店"),
        ("restaurant", "R099", "海底捞火锅 · 旗舰店"),
    )
    assert find_named_swap_target("旗舰店不满意", itinerary) is None
    assert classify_store_swap("旗舰店不满意", itinerary) is None


def test_named_swap_zero_matches_falls_back_to_generic_or_none():
    itinerary = _itinerary(("restaurant", "R001", "轻语沙拉 · 西溪店"))
    assert find_named_swap_target("换个地方吧", itinerary) is None
    # "换个地方吧" 在泛化关键词表内，两步判定都不命中"点名"，落到"泛化"
    result = classify_store_swap("换个地方吧", itinerary)
    assert result is not None and result.mode == "all"


# ============================================================
# 4. 回归护栏：其它反馈不受影响
# ============================================================


def test_unrelated_feedback_returns_none():
    """既有 e2c 图级测试的强信号反馈原话——必须继续判 None（维持走 refiner
    全局重排，B2 只做加法，不改变这条既有行为）。"""
    itinerary = _itinerary(("restaurant", "R001", "御膳坊烧烤 · 大排档"))
    for phrase in (
        "太远了，帮我换近一点的地方",
        "这个不太好",
        "预算紧，便宜点",
        "时间紧，缩短一点",
    ):
        assert classify_store_swap(phrase, itinerary) is None, (
            f"{phrase!r} 不该被误判为换店"
        )


def test_no_itinerary_returns_none():
    assert classify_store_swap("换个店铺", None) is None
    assert classify_store_swap("", _itinerary(("poi", "P001", "森林儿童探索乐园"))) is None


# ============================================================
# 5. 点名优先于泛化
# ============================================================


def test_named_takes_precedence_over_generic_keyword_in_same_utterance():
    itinerary = _itinerary(
        ("poi", "P026", "麦霸欢唱 KTV · 旗舰店"),
        ("restaurant", "R001", "轻语沙拉 · 西溪店"),
    )
    # 同时含泛化关键词"换别的店"与点名"KTV"——应按点名处理，只换 KTV 这一个。
    result = classify_store_swap("KTV不满意，换别的店试试", itinerary)
    assert result == StoreSwapClassification(mode="named", target_node_id="P026")
