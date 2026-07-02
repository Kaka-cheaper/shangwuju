"""tests.test_chat_adjust_endpoint —— ADR-0013 F-4：POST /chat/adjust 端点验收。

覆盖任务书「验收」点：

1. `adjust` 成功换菜：方案变（`itinerary_ready` 纯 `Itinerary` dump，负向断言
   不带 `node_actions`/`chips`/`demand_ledger` 等附加字段——F-3 集成实测炸出的
   隐含契约，同 `agent/graph/_emit_handlers.py::emit_narrate` docstring「深审
   改址」一节）+ 台账记账且满足回写（tier 1/2 → `SATISFIED`）+ `SESSION_STORE`
   投影同步（换菜后确认必须拿到新方案）。
2. 无可换候选 → 业务性失败：`AGENT_NARRATION` 只带告知文案 + `done`，不是
   `stream_error`，方案不动；`type=adjust` 即便失败也照常记账（诉求不因这次
   没能满足就消失）。
3. `alternative`（具名备选）候选池收窄：验证点击的是"这一个"就真的换成"这
   一个"，不是同池里评分更高的另一个（`_narrow_pool_to_single_alternative`
   的存在意义——若不收窄，`test_planner_node_swap.
   test_undirected_swap_picks_best_scoring_same_subtype_candidate` 已证明
   默认会选中评分更高的候选）。
4. `dislike`：无方向换 + 不记账。
5. 无方案 / 无 checkpoint → 端点 4xx（`chat_adjust` 直接 `HTTPException`，
   不流到一半才报错）。

驱动手法：直调 `api._streams.graph_adjust._graph_adjust`（同 `test_graph_
confirm_stream.py` / `test_e0a_graph_confirm_writeback.py` 的既有风格——本
仓库对"端点测试"的实际先例是直调内部异步生成器/函数，不起 TestClient/真
ASGI 服务；`test_graph_confirm_stream.py` 本身也是这样测 `/chat/confirm` 的）。
fixture 复用 `test_planner_node_swap` 的合成 Poi/Restaurant 构造器（自建、
确定性、不依赖 mock 数据具体 id），先跑一轮真实 turn 让 thread 有合法
checkpoint，再用 `aupdate_state` 覆盖成本文件完全可控的场景。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any

import pytest

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.graph import sse_adapter as sse  # noqa: E402
from agent.graph.build import get_compiled_graph  # noqa: E402
from agent.routing.canonical_shortcut import DEMO_SCENARIOS  # noqa: E402
from api._session_store import SESSION_STORE  # noqa: E402
from api._streams.graph_adjust import _graph_adjust  # noqa: E402
from api._streams.models import ChatAdjustRequest  # noqa: E402
from api.adjust import chat_adjust  # noqa: E402
from schemas.advisory import AdvisoryCode  # noqa: E402
from schemas.demand_ledger import LedgerEntry, LedgerEntryStatus  # noqa: E402
from schemas.itinerary import Itinerary  # noqa: E402
from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension  # noqa: E402
from schemas.sse import SseEventType  # noqa: E402
from tests.test_planner_node_swap import _build_itinerary, _intent, _node_ids, _poi, _rest  # noqa: E402

_USER_INPUT = DEMO_SCENARIOS[1]["input"]  # S2，同 test_e0a 的确定性理由


def _run(coro):
    return asyncio.run(coro)


def _drive_turn(*, user_input: str, session_id: str) -> list:
    async def _go():
        return [
            ev
            async for ev in sse.run_graph_stream(
                user_input=user_input, session_id=session_id, user_id="demo_user"
            )
        ]

    return _run(_go())


def _seed_thread_with_scenario(session_id: str, *, itinerary, intent, pois, restaurants) -> tuple[Any, dict]:
    """先跑一轮真实 turn 拿到合法 checkpoint，再覆盖成本文件确定性场景。"""
    events = _drive_turn(user_input=_USER_INPUT, session_id=session_id)
    assert any(e.type == SseEventType.ITINERARY_READY for e in events), "前置：该 thread 应已有合法 checkpoint"

    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}

    async def _seed():
        await graph.aupdate_state(
            config,
            {
                "itinerary": itinerary,
                "intent": intent,
                "pois": pois,
                "restaurants": restaurants,
                "weights": None,
                "demand_ledger": [],
            },
            as_node="narrate",
        )

    _run(_seed())
    SESSION_STORE[session_id] = {
        "intent": intent.model_dump(),
        "itinerary": itinerary.model_dump(),
        "user_id": "demo_user",
    }
    return graph, config


def _current_state(graph, config) -> dict[str, Any]:
    snapshot = _run(graph.aget_state(config))
    return snapshot.values


def _collect_adjust(req: ChatAdjustRequest, *, graph, config, state) -> list:
    async def _go():
        return [ev async for ev in _graph_adjust(req, graph=graph, config=config, state=state)]

    return _run(_go())


# ============================================================
# 1) adjust 成功换菜：方案变 + 台账记账且满足回写 + 投影同步 + 纯 dump 负向断言
# ============================================================


def test_adjust_success_changes_plan_records_satisfied_and_syncs_session_store():
    session_id = "f4_adjust_success"
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", tags=["高人均"])
    itinerary = _build_itinerary(intent, [poi_a, rb1], depart_min=14 * 60)
    rb_t1 = _rest(rest_id="RB_T1", cuisine="火锅", tags=["不辣"])  # 同子类 + 满足 → tier1

    graph, config = _seed_thread_with_scenario(
        session_id, itinerary=itinerary, intent=intent, pois=[poi_a], restaurants=[rb1, rb_t1]
    )
    state = _current_state(graph, config)

    req = ChatAdjustRequest(
        session_id=session_id,
        node_id="RB1",
        action={
            "type": "adjust",
            "adjustment": {"dimension": "dietary", "value": "不辣"},
            "label": "不辣的",
        },
    )
    events = _collect_adjust(req, graph=graph, config=config, state=state)
    types_ = [e.type.value for e in events]

    assert "stream_error" not in types_, events
    assert types_[-1] == "done"
    assert types_[0] == "agent_thought"
    assert "itinerary_ready" in types_

    # ---- ITINERARY_READY 纯 Itinerary dump 负向断言（F-3 集成实测炸雷的教训）----
    itinerary_ready_ev = next(e for e in events if e.type.value == "itinerary_ready")
    parsed = Itinerary.model_validate(itinerary_ready_ev.payload)  # extra="forbid"：混入杂字段这里就会炸
    assert "node_actions" not in itinerary_ready_ev.payload
    assert "chips" not in itinerary_ready_ev.payload
    assert "demand_ledger" not in itinerary_ready_ev.payload
    assert _node_ids(parsed) == ["home", "PA1", "RB_T1", "home"]

    # ---- AGENT_NARRATION 携带重算的 node_actions + 台账投影 ----
    narration_ev = next(e for e in events if e.type.value == "agent_narration")
    assert narration_ev.payload.get("text")
    assert "把" in narration_ev.payload["text"] and "换成" in narration_ev.payload["text"]
    node_actions = narration_ev.payload.get("node_actions")
    assert isinstance(node_actions, dict)
    ledger_display = narration_ev.payload.get("demand_ledger")
    assert isinstance(ledger_display, list) and len(ledger_display) == 1
    assert ledger_display[0]["status"] == "satisfied"  # tier1 → 满足回写
    assert ledger_display[0]["dimension"] == "dietary"

    # ---- 图状态回写：itinerary 已换 + 台账已记且标满足 ----
    post_state = _current_state(graph, config)
    assert _node_ids(post_state["itinerary"]) == ["home", "PA1", "RB_T1", "home"]
    ledger_entries = [LedgerEntry.model_validate(d) for d in post_state["demand_ledger"]]
    assert len(ledger_entries) == 1
    assert ledger_entries[0].status == LedgerEntryStatus.SATISFIED
    assert ledger_entries[0].adjustment.dimension == NodeAdjustmentDimension.DIETARY
    assert ledger_entries[0].source_text == "不辣的"
    assert ledger_entries[0].node_ref.target_id == "RB1"  # 记的是点击时那个节点，历史准确

    # ---- SESSION_STORE 投影同步：换菜后确认必须拿到新方案 ----
    cached = SESSION_STORE[session_id]
    assert cached["itinerary"]["nodes"]
    synced_ids = [n["target_id"] for n in cached["itinerary"]["nodes"]]
    assert "RB_T1" in synced_ids and "RB1" not in synced_ids
    # 未涉及字段原样保留（sync_snapshot 局部合并，不是整体覆盖）
    assert cached["user_id"] == "demo_user"
    assert cached["intent"] is not None


# ============================================================
# 1b) tier 3（SWAP_DEGRADED）：成功但只是近似满足——advisory 非空的 aupdate_state
# 回写路径（深审顺带发现的 D-7 AdvisoryCode 检查点序列化缺口，见
# agent/graph/build.py::_build_checkpoint_serde 的 AdvisoryCode 补注释）；
# 不标满足（tier3 不算「诉求被满足」，见 mark_satisfied 消费口径）。
# ============================================================


def test_adjust_tier3_degraded_success_keeps_active_not_satisfied_and_narration_carries_advisory():
    session_id = "f4_adjust_tier3_degraded"
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", tags=["高人均"])
    itinerary = _build_itinerary(intent, [poi_a, rb1], depart_min=14 * 60)
    # 唯一候选既不同子类也不满足"不辣"——只能近似满足（同 test_planner_node_swap
    # 的 test_degrade_tier3_no_candidate_satisfies_adjustment_produces_swap_degraded）。
    rb_t3 = _rest(rest_id="RB_T3", cuisine="日料", tags=[])

    graph, config = _seed_thread_with_scenario(
        session_id, itinerary=itinerary, intent=intent, pois=[poi_a], restaurants=[rb1, rb_t3]
    )
    state = _current_state(graph, config)

    req = ChatAdjustRequest(
        session_id=session_id,
        node_id="RB1",
        action={"type": "adjust", "adjustment": {"dimension": "dietary", "value": "不辣"}},
    )
    events = _collect_adjust(req, graph=graph, config=config, state=state)
    types_ = [e.type.value for e in events]
    assert "stream_error" not in types_, events
    assert "itinerary_ready" in types_

    narration_ev = next(e for e in events if e.type.value == "agent_narration")
    messages = narration_ev.payload.get("messages")
    assert messages and messages[0]["code"] == AdvisoryCode.SWAP_DEGRADED.value
    assert "最接近" in narration_ev.payload["text"] or "没找到" in narration_ev.payload["text"]

    post_state = _current_state(graph, config)
    assert _node_ids(post_state["itinerary"]) == ["home", "PA1", "RB_T3", "home"]
    ledger_entries = [LedgerEntry.model_validate(d) for d in post_state["demand_ledger"]]
    assert len(ledger_entries) == 1
    assert ledger_entries[0].status == LedgerEntryStatus.ACTIVE, "tier3 是近似满足，不应回写 SATISFIED"

    # advisories 真实回写图状态（含活的 AdvisoryCode 枚举，检查点序列化不再告警/不再丢失）
    advisories = post_state["advisories"]
    assert advisories and advisories[0]["code"] == AdvisoryCode.SWAP_DEGRADED


# ============================================================
# 2) 无可换候选：业务性失败，不是 stream_error，方案不动；照常记账
# ============================================================


def test_adjust_no_alternative_is_business_failure_not_stream_error_but_still_records_ledger():
    session_id = "f4_adjust_no_alternative"
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅")
    itinerary = _build_itinerary(intent, [poi_a, rb1], depart_min=14 * 60)

    graph, config = _seed_thread_with_scenario(
        session_id, itinerary=itinerary, intent=intent, pois=[poi_a], restaurants=[rb1]  # 池里只有目标自己
    )
    state = _current_state(graph, config)

    req = ChatAdjustRequest(
        session_id=session_id,
        node_id="RB1",
        action={"type": "adjust", "adjustment": {"dimension": "price", "value": "cheaper"}},
    )
    events = _collect_adjust(req, graph=graph, config=config, state=state)
    types_ = [e.type.value for e in events]

    assert types_ == ["agent_thought", "agent_narration", "done"], types_
    narration_ev = events[1]
    assert narration_ev.payload.get("text")
    assert "node_actions" not in narration_ev.payload
    assert "demand_ledger" not in narration_ev.payload

    # 方案不动
    post_state = _current_state(graph, config)
    assert _node_ids(post_state["itinerary"]) == ["home", "PA1", "RB1", "home"]

    # 诉求依然记账（换不成不代表用户不再想要）——ACTIVE，不是 SATISFIED
    ledger_entries = [LedgerEntry.model_validate(d) for d in post_state["demand_ledger"]]
    assert len(ledger_entries) == 1
    assert ledger_entries[0].status == LedgerEntryStatus.ACTIVE
    assert ledger_entries[0].source_text  # label 缺省时按维度合成，非空


# ============================================================
# 3) alternative：候选池收窄——点哪个就换成哪个，不是评分更高的另一个
# ============================================================


def test_alternative_swaps_to_exactly_the_chosen_target_not_the_highest_scoring_peer():
    session_id = "f4_adjust_alternative_pinned"
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅")
    itinerary = _build_itinerary(intent, [poi_a, rb1], depart_min=14 * 60)
    # 同 test_planner_node_swap.test_undirected_swap_picks_best_scoring_same_subtype_candidate：
    # 不收窄候选池时，无方向换默认会选中评分更高的 RB_HI——这里故意点选评分更低的 RB_LO，
    # 验证「点这个就该换成这个」的字面承诺没有被"最优候选"algorithm 顶掉。
    rb_hi = _rest(rest_id="RB_HI", cuisine="火锅", rating=4.9)
    rb_lo = _rest(rest_id="RB_LO", cuisine="火锅", rating=2.0)

    graph, config = _seed_thread_with_scenario(
        session_id, itinerary=itinerary, intent=intent, pois=[poi_a], restaurants=[rb1, rb_hi, rb_lo]
    )
    state = _current_state(graph, config)

    req = ChatAdjustRequest(
        session_id=session_id, node_id="RB1", action={"type": "alternative", "target_id": "RB_LO"}
    )
    events = _collect_adjust(req, graph=graph, config=config, state=state)
    types_ = [e.type.value for e in events]
    assert "stream_error" not in types_, events
    assert types_[-1] == "done"

    post_state = _current_state(graph, config)
    assert _node_ids(post_state["itinerary"]) == ["home", "PA1", "RB_LO", "home"]
    # alternative 不记账
    assert post_state["demand_ledger"] == []


# ============================================================
# 4) dislike：无方向换 + 不记账
# ============================================================


def test_dislike_swaps_without_direction_and_does_not_record_ledger():
    session_id = "f4_adjust_dislike"
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅", rating=3.0)
    rb_better = _rest(rest_id="RB_BETTER", cuisine="火锅", rating=4.9)
    itinerary = _build_itinerary(intent, [poi_a, rb1], depart_min=14 * 60)

    graph, config = _seed_thread_with_scenario(
        session_id, itinerary=itinerary, intent=intent, pois=[poi_a], restaurants=[rb1, rb_better]
    )
    state = _current_state(graph, config)

    req = ChatAdjustRequest(session_id=session_id, node_id="RB1", action={"type": "dislike"})
    events = _collect_adjust(req, graph=graph, config=config, state=state)
    types_ = [e.type.value for e in events]
    assert "stream_error" not in types_, events
    assert types_[-1] == "done"

    post_state = _current_state(graph, config)
    assert _node_ids(post_state["itinerary"]) == ["home", "PA1", "RB_BETTER", "home"]
    assert post_state["demand_ledger"] == []


# ============================================================
# 5) 无方案 / 无 checkpoint → 端点直接 4xx（不流到一半才报错）
# ============================================================


def test_chat_adjust_endpoint_raises_404_when_session_has_no_plan():
    from fastapi import HTTPException

    session_id = "f4_adjust_never_ran_thread"
    req = ChatAdjustRequest(session_id=session_id, node_id="whatever", action={"type": "dislike"})

    with pytest.raises(HTTPException) as exc_info:
        _run(chat_adjust(req))
    assert exc_info.value.status_code == 404


def test_chat_adjust_endpoint_success_path_end_to_end():
    """端点整体验收（含 chat_adjust 自己的前置校验 + EventSourceResponse 包装）。"""
    session_id = "f4_adjust_endpoint_e2e"
    intent = _intent()
    poi_a = _poi(poi_id="PA1")
    rb1 = _rest(rest_id="RB1", cuisine="火锅")
    itinerary = _build_itinerary(intent, [poi_a, rb1], depart_min=14 * 60)
    rb_better = _rest(rest_id="RB_BETTER", cuisine="火锅", rating=4.9)

    _seed_thread_with_scenario(
        session_id, itinerary=itinerary, intent=intent, pois=[poi_a], restaurants=[rb1, rb_better]
    )

    req = ChatAdjustRequest(session_id=session_id, node_id="RB1", action={"type": "dislike"})
    resp = _run(chat_adjust(req))

    async def _drain():
        out = []
        async for chunk in resp.body_iterator:
            out.append(chunk)
        return out

    chunks = _run(_drain())
    assert chunks, "应产出至少一条 SSE 数据"
    joined = "".join(str(c) for c in chunks)
    assert "stream_error" not in joined
    assert '"done"' in joined or "event: done" in joined
