"""tests.test_b2_store_swap_routing_graph —— B2 图级验收（"换个店铺"端到端）。

【为什么要垫桩脑子判定】"换个店铺"不在 Layer 1 强信号词典里（那批词是"几乎
不可能是新需求"的高精度子集，"换"本身太容易出现在新需求里，见
`agent.core.feedback_detector` 模块 docstring），真实场景下这句话要靠 LLM
脑子（`agent.routing.brain.classify_turn`）的语义理解才能被判成 feedback——
这正是本任务书要修的 bug 的**真实触发路径**（用户报告"说了'换个店铺'，方案
却 0 改动、叙事却撒谎"，前提就是这句话已经被判成 feedback 送去处理）。stub
环境下脑子判定必然 schema 校验失败、落壳3保守地板（clarify），复现不了这
一步——用 `monkeypatch.setattr(router_mod, "classify_turn", ...)` 强制判定
结果，风格对齐既有 `tests/test_router_node_feedback.py` 的垫桩手法，只隔离
"脑子判什么"这一个不确定性变量，B2 分路本身（`route_after_router` →
`store_swap`）、node_swap 引擎、finalize_plan/narrate 全部走真实代码 + 真实
mock_data 目录。

钉住 B2 修复本身的验收（对应任务书诊断的病灶：全局重排 0 处变化但撒谎"换了"）：
1. "换个店铺"路由到 `store_swap`，不再进 `refiner` 全局重排——证据：本轮不
   产 `intent_parsed`/`refinement_done`（那是 refiner 完成的特征事件，见
   `agent.graph._emit_handlers.emit_refiner`）。
2. 方案确实换了店（不是 0 处变化）：新旧 itinerary 的非 home 实体集合不同。
3. 版本志按入口维度记一笔"因反馈而生"的新版本（`finalize_plan_node` 既有
   机制，本节点不需要另写一份）。
4. 会话级累积排除集确实写入了被换掉的旧实体。
5. narration 诚实带出"这版是照你『换个店铺』的反馈调过的"回顾句，不夸大
   不撒谎（真描述新方案里的新店名）。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.graph import sse_adapter as sse  # noqa: E402
from agent.graph.build import get_compiled_graph  # noqa: E402
from agent.graph.nodes import router as router_mod  # noqa: E402
from agent.routing.brain import RouteJudgment  # noqa: E402
from agent.routing.canonical_shortcut import DEMO_SCENARIOS  # noqa: E402

_PLANNING_INPUT = DEMO_SCENARIOS[1]["input"]  # S2，壳2 canonical 字面短路，确定性出方案


def _feedback_judgment(*_args, **_kwargs) -> RouteJudgment:
    return RouteJudgment(
        label="feedback",
        confidence=0.9,
        reply_text="ok",
        tone="warm",
        cta_chips=[],
        rationale="test-force-feedback",
    )


def _drive(*, user_input: str, session_id: str) -> list:
    async def _run() -> list:
        evs = []
        async for ev in sse.run_graph_stream(
            user_input=user_input, session_id=session_id, user_id="demo_user"
        ):
            evs.append(ev)
        return evs

    return asyncio.run(_run())


def _entity_ids(itinerary) -> set[str]:
    return {
        f"{n.target_kind}:{n.target_id}" for n in itinerary.nodes if n.target_kind != "home"
    }


def test_generic_store_swap_feedback_routes_through_store_swap_not_refiner(monkeypatch):
    monkeypatch.setattr(router_mod, "classify_turn", _feedback_judgment)
    session_id = "b2_generic_swap_graph"

    baseline_events = _drive(user_input=_PLANNING_INPUT, session_id=session_id)
    assert any(e.type.value == "itinerary_ready" for e in baseline_events), (
        f"baseline 规划轮应产出方案，events={[e.type.value for e in baseline_events]}"
    )

    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}
    before = asyncio.run(graph.aget_state(config))
    baseline_itinerary = before.values["itinerary"]

    events = _drive(user_input="换个店铺", session_id=session_id)
    types_ = [e.type.value for e in events]

    assert "stream_error" not in types_, f"不该裸 STREAM_ERROR，events={types_}"
    assert "itinerary_ready" in types_, f"应产出调整后的新方案，events={types_}"
    assert "chitchat_reply" not in types_, "换店反馈不应落进澄清/闲聊气泡通道"
    # store_swap 不经 refiner：intent_parsed/refinement_done 是 refiner 完成
    # 的特征事件（见 emit_refiner），本轮一条都不该出现。
    assert "intent_parsed" not in types_, f"不该经过 refiner，events={types_}"
    assert "refinement_done" not in types_, f"不该经过 refiner，events={types_}"

    after = asyncio.run(graph.aget_state(config))
    new_itinerary = after.values["itinerary"]

    assert _entity_ids(new_itinerary) != _entity_ids(baseline_itinerary), (
        "换个店铺应该真的换了店——不能像任务书诊断的病灶那样 0 处变化"
    )

    version_log = after.values.get("plan_version_log") or []
    assert len(version_log) == 2, version_log
    assert version_log[-1]["trigger"] == "feedback"
    assert "换个店铺" in version_log[-1]["summary"]

    swapped_out = after.values.get("swapped_out_entity_ids") or []
    assert swapped_out, "应把被换掉的旧实体记进会话级累积排除集，供下次换店防 ping-pong"

    narr = [e for e in events if e.type.value == "agent_narration"]
    assert narr, f"应推 AGENT_NARRATION，events={types_}"
    narration_text = narr[-1].payload.get("text", "")
    assert "这版是照你『换个店铺』的反馈调过的" in narration_text, narration_text


def test_second_generic_swap_does_not_bring_back_first_version(monkeypatch):
    """会话级累积排除跨图级完整两轮生效：连续两次"换个店铺"，第二版不该把
    第一版（用户已经明确表态不要）的实体换回来——真实端到端复现"防 ping-pong"
    承诺，不止是节点级单测。"""
    monkeypatch.setattr(router_mod, "classify_turn", _feedback_judgment)
    session_id = "b2_generic_swap_ping_pong_graph"

    _drive(user_input=_PLANNING_INPUT, session_id=session_id)
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}
    v1 = asyncio.run(graph.aget_state(config)).values["itinerary"]

    _drive(user_input="换个店铺", session_id=session_id)
    v2 = asyncio.run(graph.aget_state(config)).values["itinerary"]

    _drive(user_input="还是不满意，再换一批", session_id=session_id)
    v3 = asyncio.run(graph.aget_state(config)).values["itinerary"]

    v1_ids, v2_ids, v3_ids = _entity_ids(v1), _entity_ids(v2), _entity_ids(v3)
    assert v2_ids != v1_ids
    assert v3_ids != v2_ids
    assert v3_ids.isdisjoint(v1_ids), (
        f"连换两次不该回到第一版：v1={v1_ids} v3={v3_ids}"
    )
