"""ADR-0008 Phase A 行为保持 characterization 测试。

【目的】

Phase A 是**纯结构迁移**：把「逐 check 各自 safe_load_* 加载数据」收口为
「CriticContext 一次性加载 + Check 注册表 flat collect-all」。本测试钉死
`validate_itinerary` 在一组覆盖「成功 + 每类违规」的行程上的违规多重集
（code + severity），作为行为未变的 golden 快照。

它补充——而非替代——既有逐 check 测试（test_critics_v2* / test_meal_time_critic /
test_age_aware_critic / test_tool_response_inconsistency / test_social_compat）。
那些是 per-check 的 characterization；本测试是 end-to-end 经 `validate_itinerary`
的整链 characterization。

【三层断言】

1. **golden 多重集**：每个场景 `validate_itinerary` 产出的 (code, severity) 排序多重集
   == 重构前实测值（行为逐字节保持）。
2. **接缝等价**：手工 `CriticContext.build` + `validate(plan, ctx)` 产出与 thin shim
   `validate_itinerary` **逐字段相同**的 Violation —— 锁定 shim 是对新接缝的纯转发，
   不做任何后处理。
3. **两数据源分离**（ADR-0008 G5）：反幻觉场景里 target_id 真实存在于全量 mock，
   但不在 tool_results 搜索快照中 → 仍被判幻觉。证明 check_tool_consistency 读的是
   **快照**而非全量 mock（否则全量 mock 命中会压掉违规）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# ============================================================
# 过渡态桥（与 test_critics_v2 等同）：旁路 agent/__init__.py 的损坏 eager-import
# ============================================================
if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.planning.commute.lookup_hop import lookup_hop  # noqa: E402
from agent.planning.critic.context import CriticContext  # noqa: E402
from agent.planning.critic.critics_v2 import validate_itinerary  # noqa: E402
from agent.planning.critic.validate import validate  # noqa: E402
from data.loader import load_user_profile  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402
from schemas.itinerary import ActivityNode, Hop, Itinerary  # noqa: E402


# ============================================================
# 构造器
# ============================================================


def _mk_intent(**kw) -> IntentExtraction:
    base = dict(
        start_time="2026-05-22T14:00",
        duration_hours=[4, 6],
        distance_max_km=10.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="characterization",
        parse_confidence=0.9,
    )
    base.update(kw)
    return IntentExtraction(**base)  # type: ignore[arg-type]


def _legal(poi_id="P040", rest_id="R001", poi_dur=165, din=60) -> Itinerary:
    """4 nodes / 3 hops 标准合法行程（hop 分钟与 P040/R001 真实路网一致）。"""
    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home", start_time="14:00", duration_min=0, title="出发"),
        ActivityNode(node_id="n1", kind="主活动", target_kind="poi", target_id=poi_id, start_time="14:09", duration_min=poi_dur, title=poi_id),
        ActivityNode(node_id="n2", kind="用餐", target_kind="restaurant", target_id=rest_id, start_time="17:04", duration_min=din, title=rest_id),
        ActivityNode(node_id="n3", kind="终点", target_kind="home", target_id="home", start_time="18:11", duration_min=0, title="回家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="14:00", minutes=9, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="16:54", minutes=5, mode="taxi", path_type="real_route", buffer_min=5),
        Hop(hop_id="h2", from_node_id="n2", to_node_id="n3", start_time="18:04", minutes=7, mode="taxi", path_type="real_route", buffer_min=0),
    ]
    return Itinerary(summary="characterization", nodes=nodes, hops=hops, total_minutes=251)


def _degenerate() -> Itinerary:
    """退化 [home, home]：触发 NODES_INCOMPLETE（且 total=0 必然越界 DURATION）。"""
    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home", start_time="14:00", duration_min=0, title="出发"),
        ActivityNode(node_id="n1", kind="终点", target_kind="home", target_id="home", start_time="14:00", duration_min=0, title="回家"),
    ]
    hops = [Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="14:00", minutes=0, mode="virtual", path_type="in_place", buffer_min=0)]
    return Itinerary(summary="degenerate", nodes=nodes, hops=hops, total_minutes=0)


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _single_restaurant(rid: str, start: str, dur: int = 60) -> Itinerary:
    """路网自洽的 [home, rid, home]——hop 分钟取 lookup_hop，避免 hop/timeline 噪声。"""
    profile = load_user_profile()
    m1, _, pt1 = lookup_hop("home", rid, "taxi", profile)
    m2, _, pt2 = lookup_hop(rid, "home", "taxi", profile)
    sm = int(start.split(":")[0]) * 60 + int(start.split(":")[1])
    dep, end, arr = sm - m1, sm + dur, sm + dur + m2
    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home", start_time=_hhmm(dep), duration_min=0, title="出发"),
        ActivityNode(node_id="n1", kind="用餐", target_kind="restaurant", target_id=rid, start_time=start, duration_min=dur, title=rid),
        ActivityNode(node_id="n2", kind="终点", target_kind="home", target_id="home", start_time=_hhmm(arr), duration_min=0, title="回家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time=_hhmm(dep), minutes=m1, mode="taxi", path_type=pt1, buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time=_hhmm(end), minutes=m2, mode="taxi", path_type=pt2, buffer_min=0),
    ]
    return Itinerary(summary="single_restaurant", nodes=nodes, hops=hops, total_minutes=arr - dep)


class _FakePoi:
    """tool_results 候选池最小对象（check_tool_consistency 用 getattr(.id)）。"""

    def __init__(self, _id: str):
        self.id = _id


# ============================================================
# golden 电池：(场景名, plan, intent, user_id, tool_results) → 期望 (code, severity) 排序多重集
# 期望值为重构前 validate_itinerary 实测输出（行为保持基线）。
# ============================================================


def _duration_long() -> Itinerary:
    it = _legal()
    object.__setattr__(it, "total_minutes", 480)  # 480 > 6*60+30=390
    return it


def _capacity_intent() -> IntentExtraction:
    intent = _mk_intent()
    intent.capacity_requirement = 6
    return intent


_BATTERY = [
    # B-1 severity labels: critical → hard, warning → soft
    ("success_legal", _legal(), _mk_intent(), "demo_user", None, []),
    ("duration_out_of_range", _duration_long(), _mk_intent(), "demo_user", None,
        [("duration_out_of_range", "hard")]),
    # nodes_incomplete: Stage-0 short-circuit — NODES_INCOMPLETE fires in Stage 0,
    # DURATION_OUT_OF_RANGE (Stage 1) is suppressed. Expected: only nodes_incomplete.
    ("nodes_incomplete", _degenerate(), _mk_intent(), "demo_user", None,
        [("nodes_incomplete", "hard")]),
    ("distance_exceeded", _legal(), _mk_intent(distance_max_km=0.1), "demo_user", None,
        [("distance_exceeded", "soft"), ("distance_exceeded", "soft")]),
    # B-2a: dietary → HARD（gate 修复）
    ("dietary_violation", _legal(rest_id="R001"), _mk_intent(dietary_constraints=["粤菜"]), "demo_user", None,
        [("dietary_violation", "hard")]),
    ("capacity_violated", _legal(rest_id="R001"), _capacity_intent(), "demo_user", None,
        [("capacity_requirement_violated", "hard")]),
    ("age_duration_mismatch", _legal(), _mk_intent(companions=[Companion(role="孩子", age=5)]), "demo_user", None,
        [("age_duration_mismatch", "hard")]),
    # tool_response_inconsistency: Stage-0 fires (check_tool_consistency is Stage 0),
    # short-circuit — same single violation, just severity label updated.
    ("tool_response_inconsistency", _legal(), _mk_intent(), "demo_user",
        {"pois": [_FakePoi("P033")], "restaurants": [_FakePoi("R001")]},
        [("tool_response_inconsistency", "hard")]),
    # restaurant_full: use duration=[1,1]+dietary=["低脂"] → decide_nodes=["用餐"] → restaurant-only
    # plan passes Stage 0; R001 suitable_for=家庭日常 matches social=家庭日常 → no social violation;
    # R001 tags include 低脂 → no dietary violation; R001 slot 17:00 available=False → FULL fires.
    ("restaurant_full_unresolved", _single_restaurant("R001", "17:00"),
        _mk_intent(duration_hours=[1, 1], dietary_constraints=["低脂"]), "demo_user", None,
        [("restaurant_full_unresolved", "hard")]),
    # B-2a: _single_restaurant("R046") [home,R046,home] + duration=[1,3] → decide_nodes returns
    # ["主活动","用餐"]（medium-long，social=家庭日常）→ poi required but missing → Stage 0
    # NODES_INCOMPLETE short-circuits; meal_time check never runs.
    ("meal_time_unreasonable_now_nodes_incomplete",
        _single_restaurant("R046", "15:00"), _mk_intent(duration_hours=[1, 3]), "demo_user", None,
        [("nodes_incomplete", "hard")]),
    # B-2a: meal_time → HARD（gate 修复）。用 decide_nodes→["用餐"]-only 的 intent
    # (duration=[1,2], social=商务接待) 让餐厅单节点通过 Stage 0，再由 meal_time 在 Stage 1 触发。
    # R002（粤味轩）suitable_for 包含商务接待 → check_social_context 不触发。
    ("meal_time_as_hard",
        _single_restaurant("R002", "15:00"), _mk_intent(duration_hours=[1, 2], social_context="商务接待"),
        "demo_user", None,
        [("meal_time_unreasonable", "hard")]),
]


def _multiset(violations):
    return sorted((v.code.value, v.severity.value) for v in violations)


@pytest.mark.parametrize("name,plan,intent,user_id,tool_results,expected", _BATTERY, ids=[b[0] for b in _BATTERY])
def test_validate_itinerary_golden_multiset(name, plan, intent, user_id, tool_results, expected, monkeypatch):
    """end-to-end golden：validate_itinerary 的违规多重集 == 重构前实测基线。"""
    # restaurant_full 依赖 demo 满座开关（默认开）；显式置 1 避免跨测试 env 泄漏
    monkeypatch.setenv("ENABLE_DEMO_FULL_CHECK", "1")
    violations = validate_itinerary(plan, intent, user_id=user_id, tool_results=tool_results)
    assert _multiset(violations) == expected, (
        f"[{name}] 违规多重集偏离基线（行为已改变！）：实际 {_multiset(violations)}，期望 {expected}"
    )


@pytest.mark.parametrize("name,plan,intent,user_id,tool_results,expected", _BATTERY, ids=[b[0] for b in _BATTERY])
def test_validate_seam_equivalent_to_shim(name, plan, intent, user_id, tool_results, expected, monkeypatch):
    """接缝等价：手工 build ctx + validate 与 thin shim validate_itinerary 逐字段一致。"""
    monkeypatch.setenv("ENABLE_DEMO_FULL_CHECK", "1")
    via_shim = validate_itinerary(plan, intent, user_id=user_id, tool_results=tool_results)
    ctx = CriticContext.build(intent, user_id=user_id, tool_results=tool_results)
    via_seam = validate(plan, ctx)
    # Violation 是 pydantic 模型，按字段相等；列表顺序也必须一致
    assert via_seam == via_shim, f"[{name}] validate(seam) 与 validate_itinerary(shim) 输出不一致"


def test_tool_consistency_reads_snapshot_not_full_mock():
    """两数据源分离（G5）：target_id 在全量 mock 里真实存在（P040），但不在 tool_results
    搜索快照里 → 仍判幻觉。证明反幻觉读快照而非全量 mock。"""
    plan = _legal(poi_id="P040")  # P040 真实存在于全量 mock
    intent = _mk_intent()
    snapshot_without_p040 = {"pois": [_FakePoi("P033")], "restaurants": [_FakePoi("R001")]}
    violations = validate_itinerary(plan, intent, tool_results=snapshot_without_p040)
    codes = [v.code.value for v in violations]
    assert "tool_response_inconsistency" in codes, (
        "P040 在全量 mock 但不在搜索快照 → 应判幻觉；若读了全量 mock 则会漏判"
    )


# ============================================================
# B-2a 新增：节点完整性按 target_kind 判断（非自由文本 kind）
# ============================================================


def test_missing_restaurant_node_fires_nodes_incomplete():
    """dining-required plan（[home, poi, home]）缺少餐厅节点 → NODES_INCOMPLETE（Stage 0）。

    B-2a B1：decide_nodes(intent) 返回 ["主活动","用餐"]（duration=[4,6] 家庭日常）。
    plan 只有 poi 节点，无 target_kind=="restaurant" → 触发 NODES_INCOMPLETE。
    Stage 0 短路 → 不产出其他 Stage-1/2 违规。
    """
    from agent.planning.commute.lookup_hop import lookup_hop
    from data.loader import load_user_profile

    profile = load_user_profile()
    # 构造 [home, P040(poi), home]，时间自洽
    m1, _, pt1 = lookup_hop("home", "P040", "taxi", profile)
    m2, _, pt2 = lookup_hop("P040", "home", "taxi", profile)
    dep_min = 14 * 60
    end_min = dep_min + m1 + 120
    arr_min = end_min + m2
    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home",
                     start_time=_hhmm(dep_min), duration_min=0, title="出发"),
        ActivityNode(node_id="n1", kind="主活动", target_kind="poi", target_id="P040",
                     start_time=_hhmm(dep_min + m1), duration_min=120, title="P040"),
        ActivityNode(node_id="n2", kind="终点", target_kind="home", target_id="home",
                     start_time=_hhmm(arr_min), duration_min=0, title="回家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1",
            start_time=_hhmm(dep_min), minutes=m1, mode="taxi", path_type=pt1, buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2",
            start_time=_hhmm(end_min), minutes=m2, mode="taxi", path_type=pt2, buffer_min=0),
    ]
    plan = Itinerary(summary="poi_only", nodes=nodes, hops=hops, total_minutes=arr_min - dep_min)

    # intent: duration=[4,6] 家庭日常 → decide_nodes=["主活动","用餐"] → 需要 restaurant
    intent = _mk_intent(duration_hours=[4, 6])
    violations = validate_itinerary(plan, intent)

    codes = [v.code.value for v in violations]
    assert "nodes_incomplete" in codes, (
        "dining-required plan 缺 restaurant 节点 → 应触发 NODES_INCOMPLETE；"
        f"实际 codes={codes}"
    )
    # Stage 0 短路：不应有 Stage-1/2 违规（DURATION 等不应出现）
    assert all(v.code.value == "nodes_incomplete" for v in violations), (
        f"Stage 0 应短路，不应有其他违规；实际 violations={codes}"
    )


def test_yexiao_labeled_restaurant_passes_completeness():
    """夜宵类型 node（kind='夜宵' 自由文本）target_kind==restaurant → 不误触 NODES_INCOMPLETE。

    B-2a B1 核心防御：kind 是 LLM 自由选的展示标签（夜宵/早茶/自由...），
    completeness 判断必须走 target_kind，否则会把合法夜场/宵夜方案误判为缺餐厅节点。

    intent: decide_nodes=["主活动","用餐"]（duration=[4,6] 家庭日常）→ 需要 poi + restaurant。
    plan: [home, P040(poi), R046(target_kind=restaurant, kind="夜宵"), home] → 两种都有 → passes。
    """
    from agent.planning.commute.lookup_hop import lookup_hop
    from data.loader import load_user_profile

    profile = load_user_profile()
    # 构造 [home, P040(poi, kind=主活动), R046(restaurant, kind="夜宵"), home]
    m01, _, _ = lookup_hop("home", "P040", "taxi", profile)
    m12, _, _ = lookup_hop("P040", "R046", "taxi", profile)
    m23, _, _ = lookup_hop("R046", "home", "taxi", profile)
    t0 = 14 * 60
    t1 = t0 + m01
    t1e = t1 + 90   # poi 停 90min
    t2 = t1e + m12
    t2e = t2 + 60   # 餐厅停 60min
    t3 = t2e + m23
    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home",
                     start_time=_hhmm(t0), duration_min=0, title="出发"),
        ActivityNode(node_id="n1", kind="主活动", target_kind="poi", target_id="P040",
                     start_time=_hhmm(t1), duration_min=90, title="P040"),
        ActivityNode(node_id="n2", kind="夜宵",  # ← LLM 自由标签，非 "用餐"
                     target_kind="restaurant", target_id="R046",
                     start_time=_hhmm(t2), duration_min=60, title="R046"),
        ActivityNode(node_id="n3", kind="终点", target_kind="home", target_id="home",
                     start_time=_hhmm(t3), duration_min=0, title="回家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1",
            start_time=_hhmm(t0), minutes=m01, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2",
            start_time=_hhmm(t1e), minutes=m12, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h2", from_node_id="n2", to_node_id="n3",
            start_time=_hhmm(t2e), minutes=m23, mode="taxi", path_type="real_route", buffer_min=0),
    ]
    plan = Itinerary(summary="yexiao_rest", nodes=nodes, hops=hops, total_minutes=t3 - t0)

    intent = _mk_intent(duration_hours=[4, 6])
    ctx = CriticContext.build(intent, user_id="demo_user")
    from agent.planning.critic._rules.checks import check_nodes_incomplete
    violations = check_nodes_incomplete(plan, ctx=ctx)

    codes = [v.code.value for v in violations]
    assert "nodes_incomplete" not in codes, (
        "target_kind==restaurant 节点（kind='夜宵'）应通过节点完整性检查；"
        "不得因自由文本 kind 不是 '用餐' 而误判；"
        f"实际 violations={codes}"
    )
