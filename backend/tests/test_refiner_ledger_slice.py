"""tests.test_refiner_ledger_slice —— ADR-0011 决策 3 E-2-b 验收：refiner 消费
「版本志 + 台账生效条目」切片（已知窗口的闭合证明）。

【这是什么窗口】用户点了某个节点的定向调整按钮（如「更便宜的」，经
/chat/adjust 记进诉求台账），随后又说"太远了，重新帮我规划一下"这类全量
反馈——refiner 走 LLM 整体重解 intent，在本改动之前完全看不到台账，刚点的
诉求等于白点（全量重排不认账）。E-2-b 让 refiner_node 经会话上下文打包器
（`agent.context.pack_routing_context` + `render_demand_recap`）取切片喂进
refine_intent 的 LLM prompt。

覆盖三层（图级为主，按任务书"垫桩捕获 prompt，断言台账条目文本在内"）：

1. **图级**：真实编译图驱动（stub LLM）——规划轮 → 模拟点击的台账回写
   （与 api/_streams/graph_adjust.py 同一 `aupdate_state(as_node="narrate")`
   机制与形状）→ 反馈轮 → 垫桩捕获 refiner 发给 LLM 的 messages → 断言
   台账诉求文本（人话短语 + 原话引用）与版本志行都在 prompt 里。
2. **节点级**：refiner_node 把非空 `ledger_recap` 传给 refine_intent（kwargs
   垫桩），台账/版本志皆空时传 None（不给 prompt 塞空段落）。
3. **prompt 单元**：`build_user_message` 的 ledger_recap 段落拼装/省略。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

# agent 命名空间桥接（与 test_e2a_session_log 同款）
if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.core.llm_client import LLMChatResponse  # noqa: E402
from agent.graph import sse_adapter as sse  # noqa: E402
from agent.graph.build import get_compiled_graph  # noqa: E402
from agent.graph.nodes import refiner as refiner_mod  # noqa: E402
from agent.intent.prompts.refiner_prompt import build_user_message  # noqa: E402
from agent.routing.canonical_shortcut import DEMO_SCENARIOS  # noqa: E402
from schemas.demand_ledger import LedgerEntry, NodeRef  # noqa: E402
from schemas.intent import IntentExtraction  # noqa: E402
from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension  # noqa: E402
from schemas.sse import SseEvent  # noqa: E402

# 壳2 canonical 字面短路确定性直达 planning（同 test_e2a_session_log 选型理由）。
_PLANNING_INPUT = DEMO_SCENARIOS[1]["input"]

# "太远" 命中 feedback 强信号（Layer 1 确定性判 feedback，不依赖 stub LLM），
# 语义上正是"重新帮我规划"式的全量反馈——已知窗口的触发形态。
_FEEDBACK_INPUT = "太远了，重新帮我规划一下"

_CLICK_SOURCE_TEXT = "更便宜的"


def _drive_turn(*, user_input: str, session_id: str) -> list[SseEvent]:
    async def _run() -> list[SseEvent]:
        return [
            ev
            async for ev in sse.run_graph_stream(
                user_input=user_input, session_id=session_id, user_id="demo_user"
            )
        ]

    return asyncio.run(_run())


class _SpyClient:
    """记录 chat() 收到的 messages；返回非 JSON 内容让 refine_intent 走
    _rule_fallback（图继续正常推进，测试只关心 prompt 里有什么）。"""

    provider = "spy"
    model = "spy-model"

    def __init__(self) -> None:
        self.calls: list[list] = []

    def chat(self, messages, **kwargs):
        self.calls.append(list(messages))
        return LLMChatResponse(content="这不是 JSON")

    def stream_chat(self, messages, **kwargs):
        yield ""


def _click_entry() -> LedgerEntry:
    """模拟「更便宜的」chip 点击产生的台账条目（与 graph_adjust.py
    AdjustActionAdjust 分支同形：node_ref 指向方案里的节点，source_text=chip
    label）。"""
    return LedgerEntry(
        member_id=None,
        nickname=None,
        node_ref=NodeRef(kind="restaurant", target_id="R001"),
        adjustment=NodeAdjustment(
            dimension=NodeAdjustmentDimension.PRICE, value="cheaper"
        ),
        source_text=_CLICK_SOURCE_TEXT,
    )


# ============================================================
# 1) 图级：点击记台账 → 说"重新规划" → refiner prompt 含该诉求文本
# ============================================================


def test_clicked_demand_reaches_refiner_prompt_via_graph(monkeypatch):
    session_id = "e2b_refiner_ledger_slice_graph"

    # ---- 规划轮：出第一版方案 ----
    events1 = _drive_turn(user_input=_PLANNING_INPUT, session_id=session_id)
    assert any(e.type.value == "itinerary_ready" for e in events1), (
        f"规划轮应产出方案，events={[e.type.value for e in events1]}"
    )

    # ---- 模拟 chip 点击的台账回写（graph_adjust.py 的同一机制/形状/as_node）----
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}
    asyncio.run(
        graph.aupdate_state(
            config, {"demand_ledger": [_click_entry().model_dump()]}, as_node="narrate"
        )
    )

    # ---- 垫桩捕获 refiner 发给 LLM 的 prompt ----
    spy = _SpyClient()
    monkeypatch.setattr(refiner_mod, "get_llm_client", lambda: spy)

    # ---- 反馈轮：全量重排 ----
    events2 = _drive_turn(user_input=_FEEDBACK_INPUT, session_id=session_id)
    assert any(e.type.value == "itinerary_ready" for e in events2), (
        f"反馈轮应仍产出新方案（spy 走 _rule_fallback），events={[e.type.value for e in events2]}"
    )

    assert spy.calls, "refiner 应经 spy client 调过 LLM"
    # refine_intent 的业务 user 消息是每次调用的最后一条（few-shot 之后）
    last_user_msg = spy.calls[0][-1].content

    # 已知窗口的闭合证明：点击的台账诉求文本出现在 refiner prompt 里
    assert "用户此前的有效诉求" in last_user_msg, f"prompt 缺诉求回顾段：{last_user_msg[-600:]}"
    assert "更便宜" in last_user_msg, "点击诉求的人话短语必须在 prompt 里"
    assert _CLICK_SOURCE_TEXT in last_user_msg, "点击诉求的原话引用必须在 prompt 里"
    # 版本志切片同在（v1 出方案史）
    assert "此前的方案版本变化" in last_user_msg
    assert _PLANNING_INPUT[:10] in last_user_msg, "版本志 v1 行应引用首轮原始需求片段"
    # 反馈原话本身也在（既有行为不回归）
    assert _FEEDBACK_INPUT in last_user_msg


# ============================================================
# 2) 节点级：refiner_node → refine_intent 的 ledger_recap 参数
# ============================================================


def _make_intent() -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        preferred_poi_types=[],
        raw_input="今天下午带老婆孩子",
        parse_confidence=0.9,
    )


class _FakeClient:
    provider = "fake"
    model = "fake"


def test_refiner_node_passes_ledger_recap_kwarg(monkeypatch):
    captured: dict = {}

    def fake_refine_intent(original, feedback_text, **kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(refined_intent=original)

    monkeypatch.setattr(refiner_mod, "refine_intent", fake_refine_intent)
    monkeypatch.setattr(refiner_mod, "get_llm_client", lambda: _FakeClient())

    state = {
        "intent": _make_intent(),
        "user_input": "太远了，重新帮我规划一下",
        "demand_ledger": [_click_entry().model_dump()],
        "plan_version_log": [
            {"version_n": 1, "summary": "v1: 按『带娃』出方案", "trigger": "first", "timestamp": 1}
        ],
    }
    refiner_mod.refiner_node(state)  # type: ignore[arg-type]

    recap = captured.get("ledger_recap")
    assert recap, "台账/版本志非空时 ledger_recap 必须非空"
    assert "更便宜" in recap and _CLICK_SOURCE_TEXT in recap
    assert "v1: 按『带娃』出方案" in recap


def test_refiner_node_passes_none_when_no_history(monkeypatch):
    captured: dict = {}

    def fake_refine_intent(original, feedback_text, **kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(refined_intent=original)

    monkeypatch.setattr(refiner_mod, "refine_intent", fake_refine_intent)
    monkeypatch.setattr(refiner_mod, "get_llm_client", lambda: _FakeClient())

    state = {"intent": _make_intent(), "user_input": "太远了"}
    refiner_mod.refiner_node(state)  # type: ignore[arg-type]

    assert captured.get("ledger_recap") is None, "无历史可回顾时应传 None（不塞空段落）"


# ============================================================
# 3) prompt 单元：build_user_message 的 ledger_recap 段落
# ============================================================


def test_build_user_message_includes_ledger_recap_block():
    msg = build_user_message(
        "{}", "太远了", itinerary_summary=None,
        ledger_recap="此前已记录且仍生效的诉求（含点击调整）：\n- 更便宜（源：『更便宜的』）",
    )
    assert "用户此前的有效诉求（含点击调整，务必在这次输出里继续尊重）" in msg
    assert "更便宜（源：『更便宜的』）" in msg


def test_build_user_message_omits_block_when_recap_empty():
    for recap in (None, ""):
        msg = build_user_message("{}", "太远了", itinerary_summary=None, ledger_recap=recap)
        assert "用户此前的有效诉求" not in msg
