"""test_e0a_graph_confirm_writeback —— ADR-0012 决策 2 验收：确认回写图状态。

问题命名：会话跨轮真相源收口——确认（HTTP 旁路）产出的终版方案必须能被
LangGraph checkpointer 看到，否则"下一轮从图状态读不到已下单"（ADR-0012 背景 2）。

验收点（任务书「验收」1-2）：
1. 一轮 /chat/turn 产出方案 → 模拟确认（直调 _graph_confirm）→ 断言图状态
   （aget_state）含 orders + user_decision="confirm" → 再跑一轮 turn 正常完成
   （证明 aupdate_state(as_node="narrate") 之后线程仍可正常续跑，不是死胡同）。
2. 房间会话（session_id 从未跑过图，无 checkpoint）确认：回写优雅跳过，不抛、
   不污染该 thread 的 checkpoint（真正跳过，不是吞异常之后偷偷建了个 checkpoint）。

驱动手法复用 test_d2_failure_drain.py 的 `sse.run_graph_stream` 直驱真实编译图
（stub LLM，见 tests/conftest.py 的 LLM_PROVIDER 默认值）。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

# ============================================================
# agent 命名空间桥接（与 test_d2_failure_drain / test_graph_confirm_stream 同款）
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.graph import sse_adapter as sse  # noqa: E402
from agent.graph.build import get_compiled_graph  # noqa: E402
from api._session_store import SESSION_STORE  # noqa: E402
from api._streams.graph_confirm import _graph_confirm  # noqa: E402
from api._streams.models import ChatConfirmRequest  # noqa: E402
from schemas.sse import SseEvent, SseEventType  # noqa: E402


def _drive_turn(*, user_input: str, session_id: str) -> list[SseEvent]:
    """驱动真实编译图跑一次 run_graph_stream，收集所有 SseEvent（同 test_d2_failure_drain）。"""

    async def _run() -> list[SseEvent]:
        evs: list[SseEvent] = []
        async for ev in sse.run_graph_stream(
            user_input=user_input,
            session_id=session_id,
            user_id="demo_user",
        ):
            evs.append(ev)
        return evs

    return asyncio.run(_run())


def _sync_session_store_like_chat_endpoint(session_id: str, events: list[SseEvent]) -> None:
    """模拟 api/chat.py::_graph_stream_with_session_sync 拦截事件写 SESSION_STORE。

    生产环境这一步由 /chat/turn 端点做（chat.py:102-127）；直驱 run_graph_stream
    的测试需要自己补这一步，否则 confirm 读不到 itinerary 快照。
    """
    intent_data = None
    for ev in events:
        if ev.type == SseEventType.INTENT_PARSED:
            intent_data = ev.payload
        elif ev.type == SseEventType.ITINERARY_READY:
            SESSION_STORE[session_id] = {
                "intent": intent_data,
                "itinerary": ev.payload,
                "user_id": "demo_user",
            }


async def _collect_confirm(req: ChatConfirmRequest) -> list[SseEvent]:
    return [ev async for ev in _graph_confirm(req)]


# ADR-0011 决策 2（E-1）：原文案"今天下午想带孩子出去玩"曾靠已删除的规划信号表
# fast path 确定性落进 planning；词表删除后同样文本要走到 Layer 2 LLM 分类才能
# 判 planning，而 stub 模式下 classify_input 对任何输入都必然抛异常（会落到新的
# 保守地板 chitchat 引导，连 intent 节点都进不去）。本文件只关心确认回写 wiring，
# 不关心路由本身，改用壳2 canonical 字面短路文本（/scenarios S2）确定性直达
# planning，不依赖 LLM。
from agent.routing.canonical_shortcut import DEMO_SCENARIOS  # noqa: E402

_USER_INPUT = DEMO_SCENARIOS[1]["input"]  # S2："今晚和兄弟出来撸串喝点酒，人均 50 左右就行"


def test_confirm_writes_back_graph_state_and_next_turn_continues():
    """核心验收：turn → confirm → aget_state 见 orders/user_decision → turn 仍能跑。"""
    session_id = "e0a_writeback_turn_confirm_turn"

    events = _drive_turn(user_input=_USER_INPUT, session_id=session_id)
    types1 = [e.type.value for e in events]
    assert "itinerary_ready" in types1, f"第一轮应产出方案，events={types1}"
    _sync_session_store_like_chat_endpoint(session_id, events)
    assert session_id in SESSION_STORE, "SESSION_STORE 同步（模拟 chat.py）应已写入"

    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}

    pre_snapshot = asyncio.run(graph.aget_state(config))
    assert pre_snapshot.values, "跑过一轮 turn 后该 thread 应已有 checkpoint"
    assert pre_snapshot.values.get("user_decision") != "confirm", (
        "确认前图状态不应已经是 confirm（防测试自身写反）"
    )

    confirm_events = asyncio.run(
        _collect_confirm(
            ChatConfirmRequest(session_id=session_id, decision="confirm")
        )
    )
    types2 = [e.type.value for e in confirm_events]
    assert confirm_events[-1].type == SseEventType.DONE, f"confirm 应以 DONE 收尾，events={types2}"
    assert "stream_error" not in types2, f"confirm 不应报错，events={types2}"

    post_snapshot = asyncio.run(graph.aget_state(config))
    assert post_snapshot.values.get("user_decision") == "confirm", (
        "ADR-0012 决策 2：confirm 后图状态应可见 user_decision=confirm"
    )
    written_itinerary = post_snapshot.values.get("itinerary")
    assert written_itinerary is not None
    assert written_itinerary.orders, "回写后的图状态 itinerary 应含 orders（终版含下单结果）"

    # 再跑一轮：证明 aupdate_state(as_node="narrate") 之后该 thread 仍能正常续跑
    # （不是死胡同——验证 as_node 选型不会卡死下一轮 astream(initial, config)）
    events3 = _drive_turn(user_input="还有其他推荐吗", session_id=session_id)
    types3 = [e.type.value for e in events3]
    assert "stream_error" not in types3, f"回写后新一轮 turn 不应报错，events={types3}"
    assert types3[-1] == "done", f"回写后新一轮 turn 应正常以 done 收尾，events={types3}"


def test_confirm_on_never_ran_thread_skips_writeback_without_raising():
    """房间会话（一次性 session_id，从未跑过图）确认：回写优雅跳过，不抛、不留痕。"""
    session_id = "e0a_room_session_never_ran_graph"

    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}
    pre_snapshot = asyncio.run(graph.aget_state(config))
    assert not pre_snapshot.values, "前置条件：这个 thread 从未跑过图，不应有 checkpoint"

    # 房间确认前会先把方案写进投影端口（SESSION_STORE），confirm 只认这个端口取数
    from tests.test_critics_v2 import _make_intent, _make_legal_itinerary

    intent = _make_intent()
    itinerary = _make_legal_itinerary()
    SESSION_STORE[session_id] = {
        "intent": intent.model_dump(),
        "itinerary": itinerary.model_dump(),
        "user_id": "demo_user",
    }

    confirm_events = asyncio.run(
        _collect_confirm(
            ChatConfirmRequest(session_id=session_id, decision="confirm")
        )
    )
    types_ = [e.type.value for e in confirm_events]
    assert confirm_events[-1].type == SseEventType.DONE, f"确认结果不受回写降级影响，events={types_}"
    assert "stream_error" not in types_, f"无 checkpoint 应静默跳过，不应报错，events={types_}"

    post_snapshot = asyncio.run(graph.aget_state(config))
    assert not post_snapshot.values, (
        "回写应真正跳过——不能在从未跑过图的 thread 上凭空造出一个 checkpoint"
    )
