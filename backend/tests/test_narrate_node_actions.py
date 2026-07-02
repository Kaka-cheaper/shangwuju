"""tests.test_narrate_node_actions —— ADR-0013 F-3：node_actions 组装与下发。

覆盖三层：

1. `agent.graph.nodes.narrate._build_node_actions`——直接单测组装逻辑：
   chips + alternatives 合并成 `{node_id: {chips, alternatives}}`；单节点
   `feasible_alternatives` 异常不连累其它节点；两者都空的节点不进结果。
2. `agent.graph.nodes.narrate.narrate_node`——整体集成：真实 Itinerary +
   覆盖候选池 → `result["node_actions"]` 形状完整（chips 来自模板生成器，
   因为测试环境 `LLM_PROVIDER=stub`——见 tests/conftest.py，narrate 的
   `use_llm` 判定天然为 False，走确定性模板路径，不需要 mock LLM）。
3. `agent.graph._emit_handlers.emit_narrate`——SSE payload 组装契约：
   `node_actions` 作为 AGENT_NARRATION payload 的兄弟字段(深审改址,详见 emit 测试注释)、无内容不加字段。

**已知集成缺口（图级测试的诚实记录，见本文件末尾）**：`node_actions` 目前
是 `narrate_node` 计算好、放进自己返回 diff 的一个新键，但 LangGraph 的
`StateGraph.astream(stream_mode="updates")` 只会把"在 `AgentState`
（`agent/graph/state.py`）声明过的字段"透传进事件流——没有声明的键会被
**静默丢弃**（本文件末尾的图级测试用真实编译图 + 对 `_build_node_actions`
的 spy 实测验证了这一行为，不是猜测）。本次任务的并行纪律明确
`agent/graph/state.py` 由另一条 F-2（诉求台账）子代理线并发编辑、本次
绝不可碰——因此"在 AgentState 里补一行 `node_actions` 字段声明"这个动作
本次故意不做，是已知、显式记录的后续收尾项，不是遗漏。narrate_node /
emit_narrate 两层的逻辑已经写好、单测直接调用两者都能验证正确（见前两组
测试），一旦 `node_actions` 字段被登记进 `AgentState`（+ EPISODE_SCOPED +
`reset_for_new_episode()`），全链路会立即生效，不需要再改本文件涉及的任何
代码。
"""

from __future__ import annotations

import asyncio

import pytest

from agent.graph import sse_adapter as sse
from agent.graph._emit_context import EmitContext
from agent.graph._emit_handlers import emit_narrate
from agent.graph.nodes import narrate as narrate_mod
from agent.graph.nodes.narrate import _build_node_actions, narrate_node
from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
from agent.planning.planners.activity_pool import build_visit_from_poi, build_visit_from_restaurant
from agent.planning.planners.route_builder import make_commute_fn, route_to_blueprint
from agent.planning.planners.route_scheduler import schedule_route
from agent.planning.weights_llm import get_planning_weights
from data.loader import load_user_profile
from schemas.domain import Location, Poi, PoiCapacity, Restaurant, RestaurantCapacity
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary
from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension
from schemas.node_chip import NodeChip


# ============================================================
# 共享 fixture helpers（风格对齐 test_planner_node_swap.py）
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


def _poi(*, poi_id: str, opening: str = "08:00-22:00") -> Poi:
    return Poi(
        id=poi_id, name=f"POI-{poi_id}", type="公园",
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=3.0, opening_hours=opening, rating=4.5,
        tags=[], suitable_for=[], suggested_duration_minutes=60,
        capacity=PoiCapacity(daily_quota=100, available_slots=50),
    )


def _rest(*, rest_id: str, opening: str = "11:00-23:00") -> Restaurant:
    return Restaurant(
        id=rest_id, name=f"REST-{rest_id}", cuisine="火锅",
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=3.0, opening_hours=opening, avg_price=100.0, rating=4.3,
        typical_dining_min=60, capacity=RestaurantCapacity(), tags=[], suitable_for=[],
    )


def _build_itinerary(intent: IntentExtraction, entities: list, depart_min: int = 14 * 60) -> Itinerary:
    """与 resolve_node_swap/feasible_alternatives 内部同一条构造路径，保证
    fixture 与被测代码口径不漂移（同 test_planner_node_swap.py 的做法）。"""
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


def _chip(node_id: str, label: str = "更便宜的") -> NodeChip:
    return NodeChip(
        node_id=node_id, label=label,
        adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.PRICE, value="cheaper"),
    )


# ============================================================
# 1. _build_node_actions：组装逻辑单测
# ============================================================


def test_build_node_actions_merges_chips_and_alternatives():
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    rb2 = _rest(rest_id="RB2")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    chips = [_chip("RB1")]
    node_actions = _build_node_actions(itinerary, intent, pois=[poi_a], restaurants=[rb1, rb2], node_chips=chips)

    assert "RB1" in node_actions
    assert node_actions["RB1"]["chips"] == [chips[0].model_dump()]
    # RB2 是同 kind 候选，feasible_alternatives 应能找到它（同子类同营业窗）
    alt_ids = {a["target_id"] for a in node_actions["RB1"]["alternatives"]}
    assert "RB2" in alt_ids


def test_build_node_actions_alternatives_k_cap_respected():
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    others = [_rest(rest_id=f"RB_ALT{i}") for i in range(5)]
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    node_actions = _build_node_actions(
        itinerary, intent, pois=[poi_a], restaurants=[rb1, *others], node_chips=[]
    )
    assert len(node_actions["RB1"]["alternatives"]) <= 2  # _NODE_ALTERNATIVES_K = 2


def test_build_node_actions_skips_node_with_neither_chips_nor_alternatives():
    """候选池里除目标外别无他选 → alternatives 为空；chips 也没给 → 该节点
    不该出现在 node_actions 里（"无内容不加字段"在节点粒度的体现）。"""
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    node_actions = _build_node_actions(itinerary, intent, pois=[poi_a], restaurants=[rb1], node_chips=[])
    assert node_actions == {}


def test_build_node_actions_one_node_alternatives_failure_does_not_affect_others(monkeypatch):
    """一个节点的 feasible_alternatives 抛异常，不该连累其它节点的按钮/备选
    一起消失——按节点独立捕获（narrate.py 的节点级隔离纪律）。"""
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    real_feasible_alternatives = narrate_mod.feasible_alternatives

    def _boom_for_poi(itinerary_, intent_, pois_, restaurants_, *, target_node_id, k):
        if target_node_id == "PA1":
            raise RuntimeError("模拟候选池覆盖缺口")
        return real_feasible_alternatives(itinerary_, intent_, pois_, restaurants_, target_node_id=target_node_id, k=k)

    monkeypatch.setattr(narrate_mod, "feasible_alternatives", _boom_for_poi)

    chips = [_chip("PA1", "更近的"), _chip("RB1", "更便宜的")]
    node_actions = _build_node_actions(itinerary, intent, pois=[poi_a], restaurants=[rb1], node_chips=chips)

    # PA1：alternatives 异常降级为空，但 chips 仍在（不因备选失败连累按钮）
    assert node_actions["PA1"]["chips"]
    assert node_actions["PA1"]["alternatives"] == []
    # RB1 不受影响（虽然本 fixture 候选池里 RB1 也没有其它同 kind 候选，
    # 但至少不因为 PA1 出错而报错或被跳过——它有 chips 所以仍应出现）
    assert node_actions["RB1"]["chips"]


# ============================================================
# 2. narrate_node：集成——真实 state + 模板路径（LLM_PROVIDER=stub）
# ============================================================


def test_narrate_node_result_includes_node_actions_with_template_chips():
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    rb2 = _rest(rest_id="RB2")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    state = {
        "intent": intent,
        "itinerary": itinerary,
        "user_id": "demo_user",
        "pois": [poi_a],
        "restaurants": [rb1, rb2],
    }
    result = narrate_node(state)

    assert "node_actions" in result
    node_actions = result["node_actions"]
    # 两个非 home 节点都应该至少有模板 chips（price/distance 恒生成）
    assert set(node_actions.keys()) >= {"PA1", "RB1"}
    for node_id in ("PA1", "RB1"):
        assert node_actions[node_id]["chips"], node_actions
    # RB1 应该能找到 RB2 作为备选（同子类同营业窗）
    assert any(a["target_id"] == "RB2" for a in node_actions["RB1"]["alternatives"])


def test_narrate_node_node_actions_empty_dict_when_no_pools_given():
    """候选池为空：模板生成器查不到任何节点对应的实体（`generate_
    template_node_chips` 静默跳过查不到实体的节点，见其 docstring），
    `feasible_alternatives` 也会因"目标实体本身在候选池里找不到"抛
    `ValueError`（node_swap 的前置条件违反）——narrate.py 的节点级
    try/except 应吞掉后者，最终两者都空 → `node_actions == {}`。
    整个过程不应让 narrate_node 本身抛异常（不因为漏传候选池就整体崩溃）。
    """
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    state = {
        "intent": intent,
        "itinerary": itinerary,
        "user_id": "demo_user",
        # 故意不传 pois/restaurants（模拟 state 里没有候选池的边界情况）
    }
    result = narrate_node(state)  # 不应抛异常
    assert result["node_actions"] == {}


# ============================================================
# 3. emit_narrate：SSE payload 兄弟字段组装契约
# ============================================================


def _minimal_itinerary() -> Itinerary:
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    return _build_itinerary(intent, [poi_a])


def test_emit_narrate_attaches_node_actions_sibling_field_when_present():
    ctx = EmitContext()
    itin = _minimal_itinerary()
    node_actions = {"PA1": {"chips": [_chip("PA1").model_dump()], "alternatives": []}}
    diff = {"narration": "文案", "itinerary": itin, "advisories": [], "node_actions": node_actions}

    events = emit_narrate(ctx, diff)
    # 深审改址:node_actions 挂 AGENT_NARRATION(附加通道先例);ITINERARY_READY
    # 保持纯 Itinerary dump——它会被投影端口整体镜像、被确认流/房间反序列化成
    # Itinerary(extra_forbidden),兄弟字段会炸确认(集成实测)。
    narr = next(e for e in events if e.type.value == "agent_narration")
    assert narr.payload["node_actions"] == node_actions
    ready = next(e for e in events if e.type.value == "itinerary_ready")
    assert "node_actions" not in ready.payload, "ITINERARY_READY 必须保持纯 Itinerary dump"
    assert ready.payload["nodes"] == itin.model_dump()["nodes"]


def test_emit_narrate_omits_node_actions_when_missing():
    ctx = EmitContext()
    itin = _minimal_itinerary()
    diff = {"narration": "文案", "itinerary": itin, "advisories": []}  # 无 node_actions 键

    events = emit_narrate(ctx, diff)
    narr = next(e for e in events if e.type.value == "agent_narration")
    assert "node_actions" not in narr.payload


def test_emit_narrate_omits_node_actions_when_empty_dict():
    ctx = EmitContext()
    itin = _minimal_itinerary()
    diff = {"narration": "文案", "itinerary": itin, "advisories": [], "node_actions": {}}

    events = emit_narrate(ctx, diff)
    narr = next(e for e in events if e.type.value == "agent_narration")
    assert "node_actions" not in narr.payload


# ============================================================
# 4. 图级（stub）：narrate_node 在真实编译图里正确算出 node_actions
#    + 诚实记录当前尚未透传到 SSE 的已知集成缺口（见本文件头部说明）
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


def test_graph_level_node_actions_reach_itinerary_ready_payload(monkeypatch):
    """图级 stub 测试（真实编译图，S2 canonical 短路，见 conftest 的
    LLM_PROVIDER=stub 默认——narrate 走确定性模板路径）。

    用 spy 包一层 `_build_node_actions` 断言：narrate_node 在真实图执行流程
    里确实被调用、且算出了非空 node_actions（chips 非空——模板生成器对
    餐厅/POI 节点恒产出 price/distance chip）。这证明 F-3 的业务逻辑本身
    在图里跑得通、没有被拓扑或 state 传递环节意外吞掉输入。

    随后断言当前 ITINERARY_READY payload **还没有** `node_actions`
    ——这是本文件头部说明的已知集成缺口（`AgentState` 尚未登记这个字段，
    LangGraph 因此在 `stream_mode="updates"` 的事件流环节静默丢弃这个键；
    `agent/graph/state.py` 本次任务范围内不可修改）。这一断言不是"这个功能
    坏了"，而是精确钉住"现在到这一步为止"，防止未来有人在不知情的情况下
    以为 node_actions 已经全链路生效。
    """
    calls: list[dict] = []
    real_build = narrate_mod._build_node_actions

    def _spy(itinerary, intent, pois, restaurants, node_chips):
        result = real_build(itinerary, intent, pois, restaurants, node_chips)
        calls.append(result)
        return result

    monkeypatch.setattr(narrate_mod, "_build_node_actions", _spy)

    evs = _drive(user_input=_USER_INPUT, session_id="node_actions_graph_probe")
    types = [e.type.value for e in evs]
    assert "itinerary_ready" in types, f"应正常出方案，events={types}"

    # narrate_node 内部确实算出了非空 node_actions（业务逻辑本身没问题）
    assert calls, "narrate_node 应该调用过 _build_node_actions"
    assert any(call for call in calls), f"应至少有一次算出非空 node_actions，calls={calls}"

    # 集成缺口已闭合(主代理深审补齐 AgentState 登记,EPISODE_SCOPED)。
    # 改址说明:node_actions 挂 AGENT_NARRATION(ITINERARY_READY 必须保持纯
    # Itinerary dump——投影端口整体镜像后确认流要反序列化它,兄弟字段会炸)。
    ready = next(e for e in evs if e.type.value == "itinerary_ready")
    assert "node_actions" not in ready.payload, "ITINERARY_READY 保持纯 dump"
    narr = next(e for e in evs if e.type.value == "agent_narration")
    assert "node_actions" in narr.payload, "AgentState 已登记,narration payload 应携带"
    actions = narr.payload["node_actions"]
    assert isinstance(actions, dict) and actions, f"node_actions 应非空,got={actions!r}"
    sample = next(iter(actions.values()))
    assert "chips" in sample or "alternatives" in sample, f"节点条目形状不对:{sample!r}"
