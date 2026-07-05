"""test_ils_desire_recall_rerank —— ILS 召回品类感知（改口根治批 · 任务 2）。

【病灶（治的是这个）】`ils_planner._query_pois` / `_query_restaurants` 完全不
消费 `intent.preferred_poi_types`（desire-blind）：用户点名的品类（如「密室」，
全库仅 P013 一家——放大器）在 ILS 候选池里按 rating 序沉底，蓝图 LLM 被 critic
打回、落到 ILS 兜底后品类结构性回不来；观察记录"想撸串却给台球KTV"同根
（餐厅侧 cuisine 诉求同样被无视）。

【修法（复用，不抄第二份词法逻辑）】复用 `agent.runtime.tools.search_adapter`
的既有品类重排（`_rerank_by_preferred_poi_types` / `_rerank_by_preferred_cuisine`，
内部走 canonical 等价表 + 双向 substring 的 `poi_desire_match` 同一把尺子——
R3 重排/R4 未满足检测/本处 ILS 召回三个消费点共享 SoT）：grounding 过滤后把
desire 命中的候选稳定前置。为什么"排位提升"就够：下游
`activity_pool.build_route_candidate_pool` 按输入序分层轮转截断（top_k=15），
前置=截断存活权；组内相对 rating 序不变（稳定分区），无 desire 时原序返回，
零回归。

驱动手法：monkeypatch `ils_planner.invoke_tool` 返回固定候选（Poi/Restaurant
fixture 全部通过 grounding 过滤：距离在半径内、营业中、无 age cap 触发），
断言 `_query_pois`/`_query_restaurants` 输出的排位。写作时刻 RED：现状原序
返回，desire 命中者沉底。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

import agent.planning.planners.ils_planner as ils_planner  # noqa: E402
from agent.core.trace import Tracer  # noqa: E402
from schemas.domain import (  # noqa: E402
    Location,
    Poi,
    PoiCapacity,
    Restaurant,
    RestaurantCapacity,
)
from schemas.intent import IntentExtraction  # noqa: E402
from schemas.tools import SearchPoisOutput, SearchRestaurantsOutput  # noqa: E402


# ============================================================
# fixture
# ============================================================


def _poi(pid: str, name: str, poi_type: str, rating: float, dist: float = 3.0) -> Poi:
    return Poi(
        id=pid,
        name=name,
        type=poi_type,
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=dist,
        opening_hours="10:00-23:00",
        rating=rating,
        age_range=None,
        price_range=None,
        tags=[],
        suitable_for=[],
        suggested_duration_minutes=90,
        capacity=PoiCapacity(daily_quota=100, available_slots=50),
    )


def _rest(rid: str, name: str, cuisine: str, rating: float, dist: float = 3.0) -> Restaurant:
    return Restaurant(
        id=rid,
        name=name,
        cuisine=cuisine,
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=dist,
        opening_hours="11:00-23:00",
        avg_price=100.0,
        rating=rating,
        typical_dining_min=60,
        capacity=RestaurantCapacity(),
        tags=[],
        suitable_for=[],
    )


def _intent(**overrides) -> IntentExtraction:
    base = dict(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="朋友热闹",
        raw_input="下午和朋友玩密室",
        parse_confidence=0.9,
    )
    base.update(overrides)
    return IntentExtraction(**base)


def _patch_search(monkeypatch, *, pois=None, restaurants=None) -> None:
    """monkeypatch 模块级 `invoke_tool`：按 tool 名返回固定候选（rating 降序
    传入，模拟 Tool 层真实返回序）。"""

    def fake(name: str, args: dict) -> SimpleNamespace:
        if name == "search_pois":
            output = SearchPoisOutput(success=True, candidates=list(pois or [])).model_dump()
        elif name == "search_restaurants":
            output = SearchRestaurantsOutput(
                success=True, candidates=list(restaurants or [])
            ).model_dump()
        else:  # pragma: no cover —— 本测试只应触达两个搜索 tool
            raise AssertionError(f"unexpected tool: {name}")
        return SimpleNamespace(success=True, output=output, reason=None, duration_ms=1)

    monkeypatch.setattr(ils_planner, "invoke_tool", fake)


# rating 降序：desire 命中者（密室/烧烤）刻意垫底——现状 desire-blind 下沉底不动
_POIS = [
    _poi("P_CAT", "毛球先生猫咖", "猫咖", 4.9),
    _poi("P_BOOK", "山丘书店", "书店", 4.8),
    _poi("P_ESCAPE", "推理大师密室逃脱", "密室", 4.5),
]
_RESTS = [
    _rest("R_HOT", "鼎鼎鸳鸯火锅", "火锅", 4.8),
    _rest("R_BBQ", "夜烤场·精致烧烤", "烧烤", 4.3),
]


# ============================================================
# 1. POI 侧：desire 命中者前置（写作时刻 RED——现状 desire-blind 原序返回）
# ============================================================


def test_query_pois_reranks_desired_type_to_front(monkeypatch):
    _patch_search(monkeypatch, pois=_POIS)
    result = ils_planner._query_pois(_intent(preferred_poi_types=["密室"]), Tracer())
    assert [p.id for p in result] == ["P_ESCAPE", "P_CAT", "P_BOOK"], (
        "desire 命中的 POI（全库唯一密室）应稳定前置到池首（截断存活权），"
        f"其余保持原 rating 序，实际={[(p.id, p.type) for p in result]}"
    )


def test_query_pois_desire_alias_matches_via_canonical_vocab(monkeypatch):
    """同义表达（「K歌」↔「KTV」类）走 canonical 等价表——本处用"密室"直配，
    别名等价矩阵已由 test_category_vocab_alignment.py 覆盖，这里只钉"ILS 召回
    走的是同一个 poi_desire_match 判定"（tags 命中同样前置）。"""
    pois = [
        _poi("P_CAT", "毛球先生猫咖", "猫咖", 4.9),
        _poi("P_TAG", "谜境空间", "室内娱乐", 4.4),
    ]
    pois[1] = pois[1].model_copy(update={"tags": ["密室", "解谜"]})
    _patch_search(monkeypatch, pois=pois)
    result = ils_planner._query_pois(_intent(preferred_poi_types=["密室"]), Tracer())
    assert result[0].id == "P_TAG", (
        f"tags 词法命中也应前置（poi_desire_match 同一把尺子），实际={[p.id for p in result]}"
    )


def test_query_pois_without_desire_keeps_order(monkeypatch):
    """无 preferred_poi_types → 原序返回（稳定排序零回归）。"""
    _patch_search(monkeypatch, pois=_POIS)
    result = ils_planner._query_pois(_intent(preferred_poi_types=[]), Tracer())
    assert [p.id for p in result] == ["P_CAT", "P_BOOK", "P_ESCAPE"]


# ============================================================
# 2. 餐厅侧：cuisine 诉求对称缺陷同修（写作时刻 RED）
# ============================================================


def test_query_restaurants_reranks_desired_cuisine_to_front(monkeypatch):
    """"想撸串却给台球KTV"的餐厅侧同根：cuisine 与 preferred_poi_types 词法
    命中者前置（`_rerank_by_preferred_cuisine` 宽松双向 substring）。"""
    _patch_search(monkeypatch, restaurants=_RESTS)
    result = ils_planner._query_restaurants(
        _intent(preferred_poi_types=["烧烤"], raw_input="想撸串"), Tracer()
    )
    assert [r.id for r in result] == ["R_BBQ", "R_HOT"], (
        f"cuisine 命中诉求的餐厅应前置，实际={[(r.id, r.cuisine) for r in result]}"
    )


def test_query_restaurants_without_desire_keeps_order(monkeypatch):
    _patch_search(monkeypatch, restaurants=_RESTS)
    result = ils_planner._query_restaurants(_intent(preferred_poi_types=[]), Tracer())
    assert [r.id for r in result] == ["R_HOT", "R_BBQ"]
