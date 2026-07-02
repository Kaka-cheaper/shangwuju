"""test_state_lifecycle —— ADR-0012 决策 4 验收：字段生命周期表 + 重置收口。

问题命名：AgentState today 靠 make_initial_state "没写这个字段=保留旧值" 的隐式
persistence-by-omission 让方案跨轮存活；本测试把这条隐式机制升级为显式声明
（TURN_SCOPED / EPISODE_SCOPED / SESSION_SCOPED 三档 frozenset）并验证它是可执行
约束，而不只是文档：

1. 完备性：AgentState 每个字段必须在三个 frozenset 里恰好登记一次（决策 4 加固 1）。
2. 会话中期新需求（intent 路径）：today 靠 route_turn.py:300-302 的兜底归并把这条
   路径掩护成"会话中期不可达"，E-1 删归并后必须可达且不能让陈旧 episode 残留
   （itinerary / critic_feedback_text / advisories）漏进全新规划轮（ADR-0012 背景 5
   的定时炸弹）——本测试 monkeypatch route_turn 直接钉住 planning，模拟归并删除后的
   世界提前验收。
3. 首轮 no-op：intent_node 在全新会话（make_initial_state 从没写过 EPISODE_SCOPED
   键）上跑 reset_for_new_episode() 不应改变任何可观察行为。

驱动手法复用 test_e0a_graph_confirm_writeback.py 的 `sse.run_graph_stream` 直驱真实
编译图（stub LLM，见 tests/conftest.py 的 LLM_PROVIDER 默认值）+ `aget_state`/
`aupdate_state` 检查 checkpoint。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

# ============================================================
# agent 命名空间桥接（与 test_e0a_graph_confirm_writeback / test_d2_failure_drain 同款）
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.graph import sse_adapter as sse  # noqa: E402
from agent.graph.build import get_compiled_graph  # noqa: E402
from agent.graph.state import (  # noqa: E402
    AgentState,
    EPISODE_SCOPED,
    SESSION_SCOPED,
    TURN_SCOPED,
    make_initial_state,
    reset_for_new_episode,
)
from agent.graph.nodes.intent import intent_node  # noqa: E402
from agent.routing.outcome import RouteOutcome  # noqa: E402


# ============================================================
# 1) 完备性测试（决策 4 加固：把表升级为可执行约束）
# ============================================================


def test_lifecycle_sets_partition_all_agentstate_fields():
    """三个 frozenset 的并集必须等于 AgentState 全部字段，两两交集必须为空。

    新增字段不登记生命周期就会让这条测试红——这是把「字段生命周期表」从文档
    升级为 CI 约束的机制本身（ADR-0012 决策 4 加固 1）。
    """
    all_fields = set(AgentState.__annotations__.keys())
    tagged = TURN_SCOPED | EPISODE_SCOPED | SESSION_SCOPED

    missing = all_fields - tagged
    assert not missing, f"以下字段没有登记生命周期：{missing}"

    extra = tagged - all_fields
    assert not extra, f"以下登记的字段在 AgentState 里已不存在（该清理表了）：{extra}"

    assert TURN_SCOPED & EPISODE_SCOPED == set(), "TURN_SCOPED 与 EPISODE_SCOPED 不应重叠"
    assert TURN_SCOPED & SESSION_SCOPED == set(), "TURN_SCOPED 与 SESSION_SCOPED 不应重叠"
    assert EPISODE_SCOPED & SESSION_SCOPED == set(), "EPISODE_SCOPED 与 SESSION_SCOPED 不应重叠"


def test_reset_for_new_episode_covers_exactly_episode_scoped():
    """reset_for_new_episode() 的 key 集合必须恰好等于 EPISODE_SCOPED（非子集/超集）。"""
    assert set(reset_for_new_episode().keys()) == EPISODE_SCOPED


def test_make_initial_state_covers_exactly_turn_and_session_scoped():
    """make_initial_state() 只覆盖 TURN_SCOPED ∪ SESSION_SCOPED，不碰 EPISODE_SCOPED。"""
    state = make_initial_state(user_input="测试", session_id="s-lifecycle")
    assert set(state.keys()) == (TURN_SCOPED | SESSION_SCOPED)
    # 双重确认：一个字段都不在 EPISODE_SCOPED 里
    assert set(state.keys()) & EPISODE_SCOPED == set()


# ============================================================
# 2) 首轮 no-op：intent_node 在全新会话上跑 reset 不改变可观察行为
# ============================================================


def test_intent_node_reset_is_noop_on_first_turn():
    """首轮（make_initial_state 从没写过 EPISODE_SCOPED 键）跑 intent_node：

    reset_for_new_episode() 铺的这批零值，和"键缺失时 .get() 的等价读数"完全一致
    ——不需要特判，但要有测试钉住这件事（任务书要求）。
    """
    state = make_initial_state(user_input="今天下午带孩子出去玩", session_id="s-firstturn")
    # 前置条件：EPISODE_SCOPED 字段确实一个都不在 make_initial_state 的输出里
    assert not (set(state.keys()) & EPISODE_SCOPED)

    out = intent_node(state)

    # intent 是本轮解析出的新对象（不是 None，reset 没有把它冲掉）
    assert out.get("intent") is not None

    # 其余 EPISODE_SCOPED 字段：reset 给出的零值与"键缺失"读数等价（都是"没有上一版"）
    assert out.get("itinerary") is None
    assert out.get("blueprint") is None
    assert out.get("weights") is None
    assert out.get("critic_feedback_text") is None
    assert out.get("plan_attempt") == 0
    assert out.get("retry_count") == 0
    assert out.get("pois") == []
    assert out.get("restaurants") == []
    assert out.get("advisories") == []
    assert out.get("critic_attempts") == []
    assert out.get("fallback_chain") == []
    assert out.get("alternatives") == []
    assert out.get("user_decision") is None
    assert out.get("orders") == []
    assert out.get("share_message") is None
    assert out.get("execution_tool_results") == []


# ============================================================
# 3) 会话中期新需求：intent 路径必须重置 episode 字段（ADR-0012 背景 5 定时炸弹）
# ============================================================


def _drive_turn(*, user_input: str, session_id: str) -> list:
    async def _run():
        evs = []
        async for ev in sse.run_graph_stream(
            user_input=user_input,
            session_id=session_id,
            user_id="demo_user",
        ):
            evs.append(ev)
        return evs

    return asyncio.run(_run())


def test_intent_path_resets_episode_state_mid_session(monkeypatch):
    """模拟会话中期新需求：同一 thread 先出一版方案，人为在 checkpoint 里种下

    陈旧 episode 残留（critic_feedback_text / advisories——stub LLM 走 happy path
    不会自然产生这些残留，不种的话测不出"清没清"），再 monkeypatch 路由钉住
    planning 让第二轮走 intent 路径（模拟 E-1 删掉 route_turn.py:300-302 兜底归并
    后，这条今天不可达的路径变为可达）。

    断言直接取 intent 节点自己返回的 diff（graph.astream(..., stream_mode="updates")
    的裸 per-node 输出，比等第二轮全部跑完再看终态更贴近任务书"进 planner 时"这句
    话——终态里 critic_feedback_text 会被本轮 critic 重新计算覆盖，掩盖了"planner
    第一次读到的到底是不是陈旧值"这件事，只有拦截 intent 节点自己的输出才能证明
    reset 发生在 planner 读取之前）：陈旧 itinerary / critic_feedback_text /
    advisories 已清，intent 是本轮新解析的。
    """
    session_id = "e0b_intent_midsession_reset"

    # ---- 第一轮：正常出方案（首轮走 intent 路径，has_itinerary=False 不会被兜底归并）----
    events1 = _drive_turn(user_input="今天下午想带孩子出去玩", session_id=session_id)
    assert any(e.type.value == "itinerary_ready" for e in events1), (
        f"第一轮应产出方案，events={[e.type.value for e in events1]}"
    )

    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}
    pre = asyncio.run(graph.aget_state(config))
    assert pre.values.get("itinerary") is not None, "第一轮后应有 itinerary"
    old_intent = pre.values.get("intent")
    assert old_intent is not None

    # ---- 人为种下陈旧 episode 残留（模拟背景 5 描述的场景）----
    asyncio.run(
        graph.aupdate_state(
            config,
            {
                "critic_feedback_text": "上一版：预算超了，请调整（陈旧反馈，不应漏进新一轮）",
                "advisories": [{"code": "stale_advisory", "message": "陈旧告知"}],
            },
            as_node="narrate",
        )
    )
    mid = asyncio.run(graph.aget_state(config))
    assert mid.values.get("critic_feedback_text") is not None, "前置条件：陈旧残留应已种下"
    assert mid.values.get("advisories"), "前置条件：陈旧 advisories 应已种下"

    # ---- 钉住第二轮走 intent 路径（模拟 E-1 删归并后，会话中期新需求变为可达）----
    from agent.graph.nodes import router as router_mod

    def _fake_route_turn(**kwargs):
        return RouteOutcome(kind="planning", decision=None)

    monkeypatch.setattr(router_mod, "route_turn", _fake_route_turn)

    initial2 = make_initial_state(
        user_input="改成周末带闺蜜去看展",
        session_id=session_id,
        user_id="demo_user",
    )

    async def _drive_raw():
        chunks = []
        async for chunk in graph.astream(initial2, config=config, stream_mode="updates"):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(_drive_raw())

    intent_diffs = [c["intent"] for c in chunks if "intent" in c]
    assert len(intent_diffs) == 1, f"intent 节点应恰好跑一次，实际 chunks={chunks}"
    diff = intent_diffs[0]

    assert diff.get("critic_feedback_text") is None, (
        "陈旧 critic_feedback_text 必须在 intent_node 返回时已清空"
        "（否则会漏进 planner 第一次调用，ADR-0012 背景 5）"
    )
    assert diff.get("advisories") == [], "陈旧 advisories 必须在 intent_node 返回时已清空"
    assert diff.get("itinerary") is None, (
        "陈旧 itinerary 必须在 intent_node 返回时已清空"
        "（不能让 execute/planner 以为还在延续上一版方案）"
    )
    new_intent = diff.get("intent")
    assert new_intent is not None
    assert new_intent != old_intent, "第二轮 intent 必须是本轮新解析的对象，不是被 reset 误吞或沿用旧值"

    # 路由被 monkeypatch 钉死不影响图跑完（第二轮应正常产出新方案，不因此报错）
    final = asyncio.run(graph.aget_state(config))
    assert final.values.get("itinerary") is not None, "第二轮应正常产出新方案"
