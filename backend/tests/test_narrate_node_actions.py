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

**历史集成缺口（已闭合）**：`node_actions` 曾是 `narrate_node` 计算好、放进
自己返回 diff 的一个新键，但当时 LangGraph 的 `StateGraph.astream(stream_
mode="updates")` 只会把"在 `AgentState`（`agent/graph/state.py`）声明过的
字段"透传进事件流——没有声明的键会被静默丢弃。这道缺口已由后续批次补齐
`AgentState` 登记（`node_actions` 现已是 EPISODE_SCOPED 字段，见
`agent/graph/state.py` 与 `reset_for_new_episode()`），本文件末尾的图级测试
断言的正是"已登记生效"的现状，不再是已知缺口。

**体感编排批 ⑤**：`narrate_node` 反查 chips/alternatives 用的候选池已从
`state.pois`/`state.restaurants`（execute 阶段窄池）改为
`data.loader.load_pois()/load_restaurants()`（全量目录）——见第 2 组测试
（`test_narrate_node_result_includes_node_actions_with_template_chips` /
`test_narrate_node_node_actions_nonempty_when_selected_entity_missing_from_
narrow_state_pool`）monkeypatch `narrate_mod.load_pois`/`load_restaurants`
的手法，以及 narrate.py 模块 docstring「实体反查改用全量目录」一节。
"""

from __future__ import annotations

import asyncio

import pytest

from agent.graph import sse_adapter as sse
from agent.graph._emit_context import EmitContext
from agent.graph._emit_handlers import emit_finalize_plan, emit_narrate
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


def test_narrate_node_result_includes_node_actions_with_template_chips(monkeypatch):
    """narrate_node 现在从全量目录（`data.loader.load_pois/load_restaurants`）
    反查实体，不再吃 `state.pois`/`state.restaurants`（体感编排批 ⑤，见
    narrate.py 模块 docstring「实体反查改用全量目录」）——本测试 monkeypatch
    这两个 loader（narrate_mod 顶层导入的名字）直接返回本文件的合成候选池，
    等价于把它们当作"全量目录"，`state` 里故意不放 pois/restaurants 键
    （证明 narrate_node 确实不读它们）。
    """
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    rb2 = _rest(rest_id="RB2")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    monkeypatch.setattr(narrate_mod, "load_pois", lambda: [poi_a])
    monkeypatch.setattr(narrate_mod, "load_restaurants", lambda: [rb1, rb2])

    state = {
        "intent": intent,
        "itinerary": itinerary,
        "user_id": "demo_user",
        # 故意不传 pois/restaurants：narrate_node 已改读全量目录（上面的
        # monkeypatch），不再依赖这两个 state 键。
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


def test_narrate_node_node_actions_nonempty_when_selected_entity_missing_from_narrow_state_pool(
    monkeypatch,
):
    """体感编排批 ⑤ 冒烟回归：选中实体不在（execute 阶段留下的）窄池时，
    chips/备选仍非空。

    根因回顾：旧实现里 narrate_node 用 `state.pois`/`state.restaurants`
    （execute 阶段搜索 worker 的候选池，可能比方案实际选中的实体窄）做实体
    反查——真实 LLM 规划选中的实体不在这个窄池里时，模板 chips 反查落空、
    `feasible_alternatives` 因前置条件被违反抛 `ValueError`（被节点级
    try/except 吞掉），两者都空 → 该节点整个从 `node_actions` 消失（冒烟实测
    S2 场景全灭）。

    本测试直接模拟这个场景：`state["pois"]`/`state["restaurants"]` 是与方案
    毫不相干的窄池（不含 PA1/RB1），但 narrate_node 现在从全量目录反查（这里
    monkeypatch 成本文件的合成候选池，等价于"全量目录里确实有这两个实体"）
    ——断言 chips/alternatives 仍非空，证明不再依赖 state 里的窄池。
    """
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1")
    rb2 = _rest(rest_id="RB2")
    itinerary = _build_itinerary(intent, [poi_a, rb1])

    # 全量目录：确实覆盖方案选中的 PA1/RB1（+ RB1 的同子类备选 RB2）。
    monkeypatch.setattr(narrate_mod, "load_pois", lambda: [poi_a])
    monkeypatch.setattr(narrate_mod, "load_restaurants", lambda: [rb1, rb2])

    # execute 阶段窄池：与方案选中的实体完全不相干（模拟"选中实体不在窄池里"）。
    unrelated_poi = _poi(poi_id="UNRELATED_POI")
    unrelated_rest = _rest(rest_id="UNRELATED_REST")
    state = {
        "intent": intent,
        "itinerary": itinerary,
        "user_id": "demo_user",
        "pois": [unrelated_poi],
        "restaurants": [unrelated_rest],
    }
    result = narrate_node(state)  # 不应抛异常

    node_actions = result["node_actions"]
    assert node_actions, "选中实体不在窄池时 node_actions 不应全灭"
    assert set(node_actions.keys()) >= {"PA1", "RB1"}
    for node_id in ("PA1", "RB1"):
        assert node_actions[node_id]["chips"], node_actions
    assert any(a["target_id"] == "RB2" for a in node_actions["RB1"]["alternatives"])


# ============================================================
# 3. emit_finalize_plan / emit_narrate：SSE payload 组装契约
#    （体感编排批 P1：ITINERARY_READY 已挪到 emit_finalize_plan 推送，
#    emit_narrate 只推 AGENT_NARRATION，见两者各自 docstring）
# ============================================================


def _minimal_itinerary() -> Itinerary:
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    return _build_itinerary(intent, [poi_a])


def test_emit_finalize_plan_emits_pure_itinerary_dump():
    """体感编排批 P1：ITINERARY_READY 由 emit_finalize_plan 推送，且必须保持
    纯 Itinerary dump（不带 node_actions 等兄弟字段——它会被投影端口整体镜像、
    被确认流/房间反序列化成 Itinerary(extra_forbidden)，混入兄弟字段会炸
    确认，同 emit_narrate 曾经的深审教训）。finalize_plan_node 的返回 diff
    里本就没有 node_actions 键（那是 narrate_node 才算的东西），这里显式断言
    payload 形状，钉死"ITINERARY_READY 只认 itinerary 字段"这条契约。
    """
    ctx = EmitContext()
    itin = _minimal_itinerary()
    diff = {"itinerary": itin}

    events = emit_finalize_plan(ctx, diff)
    assert len(events) == 1
    ready = events[0]
    assert ready.type.value == "itinerary_ready"
    assert "node_actions" not in ready.payload, "ITINERARY_READY 必须保持纯 Itinerary dump"
    assert ready.payload["nodes"] == itin.model_dump()["nodes"]
    assert ctx.itinerary_emitted is True


def test_emit_narrate_attaches_node_actions_sibling_field_when_present():
    ctx = EmitContext()
    itin = _minimal_itinerary()
    node_actions = {"PA1": {"chips": [_chip("PA1").model_dump()], "alternatives": []}}
    diff = {"narration": "文案", "itinerary": itin, "advisories": [], "node_actions": node_actions}

    events = emit_narrate(ctx, diff)
    # 深审改址:node_actions 挂 AGENT_NARRATION(附加通道先例)。体感编排批 P1：
    # emit_narrate 不再推 ITINERARY_READY（已由 emit_finalize_plan 推过，
    # 见 test_emit_finalize_plan_emits_pure_itinerary_dump），这里只剩
    # AGENT_NARRATION 一条事件。
    assert len(events) == 1
    narr = events[0]
    assert narr.type.value == "agent_narration"
    assert narr.payload["node_actions"] == node_actions


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
# 4. 图级（stub）：narrate_node 在真实编译图里正确算出 node_actions，
#    且经 AGENT_NARRATION 透传到 SSE（AgentState 登记见本文件头部说明）
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

    随后断言 ITINERARY_READY payload 不含 `node_actions`（它挂在
    AGENT_NARRATION 的兄弟字段，见 `emit_narrate`/`emit_finalize_plan`
    docstring 的"深审改址"说明——ITINERARY_READY 必须保持纯 Itinerary dump，
    投影端口整体镜像后确认流/房间要 `Itinerary.model_validate`
    (extra_forbidden) 反序列化它，混入兄弟字段会直接炸掉确认）；
    AGENT_NARRATION payload 携带非空 node_actions。体感编排批 P1 之后
    ITINERARY_READY 由新节点 `finalize_plan` 推送、AGENT_NARRATION 仍由
    `narrate` 推送——两条事件出自不同节点，但下面的断言只认事件类型，
    不认哪个节点推的，故本测试对 P1 拓扑改动天然不变。
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
