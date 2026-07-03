"""tests.test_planner_pinning_advisory —— ADR-0010 D-7：pinning + advisory 通道。

覆盖 `agent.planning.planners.ils_planner.plan_hybrid` 新增的 `pinned` 形参与
`HybridResult.advisories`（ADR-0010 决策 11「绝不默默忽略」）：

1. `PinSpec` resolve 不到已召回候选 → `NO_MATCHING_CANDIDATES`。
2. resolve 到但排不进最终路线（时间窗物理不可行）→ `PINNED_UNSATISFIABLE`，
   message 含目标真实名称。
3. 候选稀薄导致总时长比期望短 → `SHORTER_THAN_REQUESTED`。
4. 总花费超出用户默认预算 → `OVER_BUDGET`。
5. 修复闭环里 pin 是本轮唯一可修算子、被牺牲保全局 → `PINNED_DROPPED_IN_REPAIR`。
6. advisory message 是自包含中文人话（断言关键词/字符集，不断言全句）。

风格对齐 `test_route_builder.py`（自建 `_poi`/`_restaurant`/`_intent` fixture，
而非依赖 mock 数据目录里的具体 id——除通勤外，`plan_hybrid` 内部所有几何/时长
均由 fixture 显式控制）。通勤解析判断点：`plan_hybrid` 内部经
`route_builder.make_commute_fn` 调用生产 `lookup_hop`，测试自建的合成 id
（"PZ1" 等，不在真实 mock 坐标索引里）天然落到 `lookup_hop` 的 4 级兜底
（`FALLBACK_MIN=15` 分钟，确定性常量）——因此本文件的合成实体与真实 mock
数据同台使用是安全的：合成实体互相之间/与 home 之间的通勤恒为 15 分钟。
"""

from __future__ import annotations

import pytest

from agent.core.llm_client_stub import StubLLMClient
from agent.planning.planners import ils_planner
from agent.planning.planners.ils_planner import HybridResult, plan_hybrid
from data.loader import load_user_profile
from schemas.advisory import AdvisoryCode
from schemas.domain import Location, Poi, PoiCapacity, Restaurant, RestaurantCapacity
from schemas.intent import Companion, IntentExtraction
from schemas.pin import PinSpec


# ============================================================
# 共享 fixture helpers（风格对齐 test_route_builder.py）
# ============================================================


def _intent(
    *,
    social_context: str = "独处放空",
    companions: tuple[Companion, ...] = (),
    duration_hours: list[int] | None = None,
    start_time: str = "2026-07-02T14:00",
    budget_per_person: float | None = None,
) -> IntentExtraction:
    return IntentExtraction(
        start_time=start_time,
        duration_hours=duration_hours if duration_hours is not None else [1, 3],
        distance_max_km=10.0,
        companions=list(companions),
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context=social_context,
        raw_input="测试",
        parse_confidence=0.9,
        ambiguous_fields=[],
        budget_per_person=budget_per_person,
    )


def _poi(
    *,
    poi_id: str,
    poi_type: str = "typeA",
    suggested: int = 60,
    opening: str = "08:00-22:00",
    dist: float = 3.0,
    price_range: tuple[int, int] | None = None,
) -> Poi:
    return Poi(
        id=poi_id,
        name=f"测试 POI {poi_id}",
        type=poi_type,
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=dist,
        opening_hours=opening,
        rating=4.5,
        age_range=None,
        price_range=list(price_range) if price_range else None,
        tags=[],
        suitable_for=[],
        suggested_duration_minutes=suggested,
        capacity=PoiCapacity(daily_quota=100, available_slots=50),
    )


def _restaurant(
    *,
    rest_id: str,
    cuisine: str = "粤菜",
    opening: str = "17:00-22:00",
    dist: float = 3.0,
    dining_min: int = 60,
    avg_price: float = 80.0,
) -> Restaurant:
    return Restaurant(
        id=rest_id,
        name=f"测试餐厅 {rest_id}",
        cuisine=cuisine,
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=dist,
        opening_hours=opening,
        avg_price=avg_price,
        rating=4.3,
        typical_dining_min=dining_min,
        capacity=RestaurantCapacity(),
        tags=[],
        suitable_for=[],
    )


def _mock_query(monkeypatch, *, pois: list[Poi] = (), restaurants: list[Restaurant] = ()):
    monkeypatch.setattr(ils_planner, "_query_pois", lambda intent, tracer: list(pois))
    monkeypatch.setattr(
        ils_planner, "_query_restaurants", lambda intent, tracer: list(restaurants)
    )


# ============================================================
# 1. resolve 不到 → NO_MATCHING_CANDIDATES
# ============================================================


def test_pin_resolve_miss_produces_no_matching_candidates_advisory(monkeypatch):
    poi = _poi(poi_id="PZ1")
    _mock_query(monkeypatch, pois=[poi])

    intent = _intent(duration_hours=[1, 2])
    result = plan_hybrid(
        intent,
        client=StubLLMClient(),
        pinned=[PinSpec(kind="poi", target_id="PZ_GHOST")],
    )

    assert result.success, f"应成功（PZ1 作为涌现候选兜底）：{result.failure_detail}"
    codes = {a.code for a in result.advisories}
    assert AdvisoryCode.NO_MATCHING_CANDIDATES in codes, [a.code for a in result.advisories]
    msg = next(a.message for a in result.advisories if a.code == AdvisoryCode.NO_MATCHING_CANDIDATES)
    assert "没找到" in msg or "找不到" in msg


# ============================================================
# 2. resolve 到但时间窗物理不可行 → PINNED_UNSATISFIABLE（含目标名）
# ============================================================


def test_pinned_unsatisfiable_when_time_window_physically_infeasible(monkeypatch):
    # PZ2 开放时段远离出发时刻+预算窗口 → try_insert 恒不可行；
    # PZ3 正常营业，兜底让方案仍能交付（否则空排程会走早失败分支，advisory 被作废）。
    poi_ghost = _poi(poi_id="PZ2", opening="22:00-23:00")
    poi_ok = _poi(poi_id="PZ3", opening="08:00-22:00")
    _mock_query(monkeypatch, pois=[poi_ghost, poi_ok])

    intent = _intent(duration_hours=[1, 3], start_time="2026-07-02T14:00")
    result = plan_hybrid(
        intent,
        client=StubLLMClient(),
        pinned=[PinSpec(kind="poi", target_id="PZ2")],
    )

    assert result.success, f"应靠 PZ3 交付方案：{result.failure_detail}"
    unsat = [a for a in result.advisories if a.code == AdvisoryCode.PINNED_UNSATISFIABLE]
    assert unsat, [a.code for a in result.advisories]
    assert poi_ghost.name in unsat[0].message, unsat[0].message
    # PZ2 本身不可行，理应不出现在最终产物里
    assert not any(n.target_id == "PZ2" for n in result.itinerary.nodes)


# ============================================================
# 3. 候选稀薄 → 总时长比期望短 → SHORTER_THAN_REQUESTED
# ============================================================


def test_shorter_than_requested_advisory_when_candidate_pool_too_thin(monkeypatch):
    lone_restaurant = _restaurant(rest_id="RZ1", opening="17:00-22:00", dining_min=60)
    _mock_query(monkeypatch, restaurants=[lone_restaurant])

    # lo=6h(360min)-30 容差=330min tol；单一餐厅+两段 15min 兜底通勤远小于此。
    intent = _intent(duration_hours=[6, 8], start_time="2026-07-02T17:00")
    result = plan_hybrid(intent, client=StubLLMClient())

    assert result.success, f"应以短而好的方案交付：{result.failure_detail}"
    shorter = [a for a in result.advisories if a.code == AdvisoryCode.SHORTER_THAN_REQUESTED]
    assert shorter, [a.code for a in result.advisories]
    assert "短" in shorter[0].message


# ============================================================
# 4. 总花费超预算 → OVER_BUDGET
# ============================================================


def test_over_budget_advisory_when_total_cost_exceeds_default_budget(monkeypatch):
    """intent.budget_per_person 缺省（None，未明说数字）→ 退回 persona
    default_budget，措辞口径为"你档案里的默认"（ADR-0014 决策 3·G-3）。"""
    budget = load_user_profile().default_budget
    expensive_poi = _poi(
        poi_id="PZ4", suggested=60, price_range=(int(budget) + 500, int(budget) + 600)
    )
    _mock_query(monkeypatch, pois=[expensive_poi])

    intent = _intent(duration_hours=[1, 2])
    result = plan_hybrid(intent, client=StubLLMClient())

    assert result.success, f"应成功交付（只是超预算）：{result.failure_detail}"
    over = [a for a in result.advisories if a.code == AdvisoryCode.OVER_BUDGET]
    assert over, [a.code for a in result.advisories]
    assert "预算" in over[0].message
    assert "你档案里的默认" in over[0].message, over[0].message


def test_over_budget_advisory_prefers_stated_budget_over_persona_default(monkeypatch):
    """intent.budget_per_person 本轮明说（如 50 元）→ 优先于 persona
    default_budget（300 元）做比较对象，即使花费远低于 300 也照样告知；
    措辞口径为"你说的"（ADR-0014 决策 3·G-3）。"""
    budget = load_user_profile().default_budget
    assert budget > 100, "本测试假设 persona 默认预算显著高于 50 元的探针预算"

    # 候选花费 120 元：低于 persona 默认 300（旧逻辑不会告知），但高于本轮
    # 明说的 50 元预算（新逻辑应该告知）。
    modest_poi = _poi(poi_id="PZ4B", suggested=60, price_range=(120, 140))
    _mock_query(monkeypatch, pois=[modest_poi])

    intent = _intent(duration_hours=[1, 2], budget_per_person=50)
    result = plan_hybrid(intent, client=StubLLMClient())

    assert result.success, f"应成功交付（只是超预算）：{result.failure_detail}"
    over = [a for a in result.advisories if a.code == AdvisoryCode.OVER_BUDGET]
    assert over, [a.code for a in result.advisories]
    assert "你说的" in over[0].message, over[0].message
    assert "50" in over[0].message, over[0].message


# ============================================================
# 5. 修复闭环：pin 是本轮唯一可修算子 → 被牺牲保全局 → PINNED_DROPPED_IN_REPAIR
# ============================================================


def test_pinned_dropped_in_repair_when_sole_repair_operator_targets_pin(monkeypatch):
    """构造：pin 的 POI（PZ5）与另一个涌现 POI（PZ6）同时入选初版路线；

    monkeypatch `_run_unified_critic`（plan_hybrid 内部调用点）伪造"第 0 轮
    唯一 HARD 违规恰好定位到 PZ5、第 1 轮起干净"——这样只驱动 plan_hybrid 自身
    的黑名单保护/牺牲决策逻辑（本步改动的核心），不依赖凑出一个真实 critic
    violation 组合恰好只命中被保护的 pin（那需要摆布 mock 数据的精确边界条件，
    脆弱且离题；`_compute_blacklists`/critic 各自的既有正确性已有别的测试覆盖）。
    """
    from agent.planning.critic._rules.types import Severity, Violation, ViolationCode

    poi_pinned = _poi(poi_id="PZ5", suggested=60)
    poi_other = _poi(poi_id="PZ6", suggested=60)
    _mock_query(monkeypatch, pois=[poi_pinned, poi_other])

    intent = _intent(duration_hours=[3, 5])

    calls = {"n": 0}
    real_run_unified_critic = ils_planner._run_unified_critic

    def fake_run_unified_critic(itin, given_intent):
        calls["n"] += 1
        if calls["n"] == 1:
            idx = next(i for i, n in enumerate(itin.nodes) if n.target_id == "PZ5")
            violation = Violation(
                code=ViolationCode.SOCIAL_CONTEXT_MISMATCH,
                severity=Severity.HARD,
                message="测试：强制本轮唯一违规命中被保护的 pin",
                field_path=f"nodes[{idx}].target_id",
            )
            return ils_planner.HybridCriticReport(violations=[violation])
        return real_run_unified_critic(itin, given_intent)

    monkeypatch.setattr(ils_planner, "_run_unified_critic", fake_run_unified_critic)

    result = plan_hybrid(
        intent,
        client=StubLLMClient(),
        pinned=[PinSpec(kind="poi", target_id="PZ5")],
    )

    assert result.success, f"应在牺牲 pin 后收敛：{result.failure_detail}"
    dropped = [a for a in result.advisories if a.code == AdvisoryCode.PINNED_DROPPED_IN_REPAIR]
    assert dropped, [a.code for a in result.advisories]
    assert poi_pinned.name in dropped[0].message, dropped[0].message
    assert not any(n.target_id == "PZ5" for n in result.itinerary.nodes), (
        "PZ5 应已被修复闭环换掉，不该出现在最终产物里"
    )


# ============================================================
# 6. advisory message 是自包含中文人话（关键词/字符集断言，非全句）
# ============================================================


def test_advisory_messages_are_self_contained_chinese_sentences():
    """全部 5 类 advisory 的措辞都应是自包含中文人话——不泄漏内部字段名/id 占位符
    （如 "nodes[" / "None" / "target_id"），且至少含一个中文字符。"""
    from schemas.advisory import Advisory

    samples: list[Advisory] = [
        ils_planner._no_matching_candidates_advisory([PinSpec(kind="poi", target_id="X1")]),
        ils_planner._no_matching_candidates_advisory(
            [PinSpec(kind="restaurant", target_id="X2")]
        ),
        ils_planner._no_matching_candidates_advisory(
            [PinSpec(kind="poi", target_id="X1"), PinSpec(kind="restaurant", target_id="X2")]
        ),
    ]
    samples.extend(
        ils_planner._build_success_advisories(
            pin_advisories=[],
            unmet_pinned=[
                type(
                    "FakeVisit",
                    (),
                    {"kind": "poi", "entity": _poi(poi_id="PZ9"), "target_id": "PZ9"},
                )()
            ],
            dropped_pins=set(),
            pinned_by_key={},
            violations=[],
            current_scheduled=[],
            money_budget=300.0,
        )
    )

    for a in samples:
        assert a.message and a.message.strip(), a
        assert "None" not in a.message, a.message
        assert "nodes[" not in a.message, a.message
        assert "target_id" not in a.message, a.message
        assert any("一" <= ch <= "鿿" for ch in a.message), (
            f"advisory message 应含中文：{a.message!r}"
        )


# ============================================================
# 7. 同码合并：多个 pin 塞不进 → 恰好一条 advisory 点全名字（深审修正 2）
# ============================================================


def test_multiple_unmet_pins_merge_into_single_advisory(monkeypatch):
    """两个 pin 都塞不进时应合并为**一条** PINNED_UNSATISFIABLE、message 点全
    两个名字——逐条产出会让 narrator 模板的诚实告知段同码句子膨胀（"绝不静默
    忽略"的通道若因文案超长被截断反而自吞告知）。"""
    ghost_a = _poi(poi_id="PZ7", opening="22:00-23:00")
    ghost_b = _poi(poi_id="PZ8", opening="22:00-23:00")
    poi_ok = _poi(poi_id="PZ3", opening="08:00-22:00")
    _mock_query(monkeypatch, pois=[ghost_a, ghost_b, poi_ok])

    intent = _intent(duration_hours=[1, 3], start_time="2026-07-02T14:00")
    result = plan_hybrid(
        intent,
        client=StubLLMClient(),
        pinned=[
            PinSpec(kind="poi", target_id="PZ7"),
            PinSpec(kind="poi", target_id="PZ8"),
        ],
    )

    assert result.success, f"应靠 PZ3 交付方案：{result.failure_detail}"
    unsat = [a for a in result.advisories if a.code == AdvisoryCode.PINNED_UNSATISFIABLE]
    assert len(unsat) == 1, [a.model_dump() for a in result.advisories]
    assert ghost_a.name in unsat[0].message, unsat[0].message
    assert ghost_b.name in unsat[0].message, unsat[0].message


# ============================================================
# 8. 成员资格过滤：pin 已在最终排程里 → 不产「没进方案」类告知（深审修正 1）
# ============================================================


def test_no_unmet_advisory_when_pin_present_in_final_schedule():
    """单元级驱动 `_build_success_advisories`：模拟「构造期 unmet / 修复期被
    牺牲」的历史记录与最终排程不一致的情形——修复闭环换血时原 pin 可能作为
    普通候选被重新插回，此时告知必须以最终排程为准，不得再说"塞不进去/被换掉"。"""
    from types import SimpleNamespace

    visit = SimpleNamespace(
        kind="poi", target_id="PZ9", entity=_poi(poi_id="PZ9"), cost=0.0
    )
    advisories = ils_planner._build_success_advisories(
        pin_advisories=[],
        unmet_pinned=[visit],
        dropped_pins={("poi", "PZ9")},
        pinned_by_key={("poi", "PZ9"): visit},
        violations=[],
        current_scheduled=[SimpleNamespace(visit=visit)],
        money_budget=300.0,
    )
    assert advisories == [], [a.model_dump() for a in advisories]
