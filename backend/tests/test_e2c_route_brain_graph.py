"""test_e2c_route_brain_graph —— ADR-0011 E-2-c 图级验收（一脑三壳，全 stub）。

驱动真实编译图（`sse.run_graph_stream`，stub LLM，模式同 test_e2a_session_log.py），
钉住任务书点名的 4 条图级行为（均在 stub 模式下确定性可测，靠规则层/壳2/壳3
命中，不依赖 stub LLM 能正确判出 6 类）：

1. 陪聊（画像问答，规则层，ungated）不炸方案——已有方案时问"我的偏好是什么"，
   方案原样不变、不触发规划/重规划。
2. 歧义（脑子在 stub 下必然解析失败 → 壳3 保守地板）→ 澄清引导——有方案时
   一句认不出具体方向的话，得到 clarify 气泡 + 三个地板 chip，方案不变。
3. 确认（预约指令，规则层）→ 引导按钮，绝不自动下单——气泡带 action="confirm"
   的按钮，但当轮不产生任何订单/新方案。
4. 反馈（强信号，Layer 1）→ 走 refiner 路径——产出新方案 + refinement_done。

不覆盖：脑子对语义模糊输入的分类质量（那是 prompt 质量问题，靠
test_routing_brain.py 的垫桩单测 + 真 LLM 冒烟验证，不是图级 stub 能测的范围
——stub LLM 对任何 prompt 都返回固定的意图抽取形状 JSON，脑子在 stub 下总是
解析失败，图级测试因此只能验证"失败后落到哪"，不能验证"脑子判得准不准"）。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

# ============================================================
# agent 命名空间桥接（与 test_e2a_session_log.py 同款）
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.graph import sse_adapter as sse  # noqa: E402
from agent.graph.build import get_compiled_graph  # noqa: E402
from agent.routing.canonical_shortcut import DEMO_SCENARIOS  # noqa: E402

# S2："今晚和兄弟出来撸串喝点酒，人均 50 左右就行"——壳2 canonical 字面短路，
# 不依赖 stub LLM，确定性产出一份带方案的 baseline（同 test_e2a_session_log.py
# 选型理由）。
_PLANNING_INPUT = DEMO_SCENARIOS[1]["input"]


def _drive_turn(*, user_input: str, session_id: str) -> list:
    async def _run() -> list:
        evs = []
        async for ev in sse.run_graph_stream(
            user_input=user_input, session_id=session_id, user_id="demo_user"
        ):
            evs.append(ev)
        return evs

    return asyncio.run(_run())


def _state_values(session_id: str) -> dict:
    graph = get_compiled_graph()
    config = {"configurable": {"thread_id": session_id}}
    snapshot = asyncio.run(graph.aget_state(config))
    return dict(snapshot.values or {})


def _seed_plan(session_id: str) -> dict:
    events = _drive_turn(user_input=_PLANNING_INPUT, session_id=session_id)
    assert any(e.type.value == "itinerary_ready" for e in events), (
        f"baseline 规划轮应产出方案，events={[e.type.value for e in events]}"
    )
    return _state_values(session_id)["itinerary"]


def _chitchat_payload(events: list) -> dict:
    for ev in events:
        if ev.type.value == "chitchat_reply":
            return ev.payload
    raise AssertionError(f"未找到 chitchat_reply 事件，events={[e.type.value for e in events]}")


# ============================================================
# 1) 陪聊（画像问答）不炸方案
# ============================================================


def test_chitchat_persona_question_does_not_touch_plan():
    session_id = "e2c_chitchat_persona"
    baseline = _seed_plan(session_id)

    events = _drive_turn(user_input="我的偏好是什么", session_id=session_id)
    types_ = [e.type.value for e in events]
    assert "itinerary_ready" not in types_, f"陪聊不该重新出方案，events={types_}"

    payload = _chitchat_payload(events)
    assert payload["input_kind"] == "chitchat"
    assert payload["reply_text"]

    after = _state_values(session_id)["itinerary"]
    assert after == baseline, "陪聊轮不该改动方案"
    version_log = _state_values(session_id).get("plan_version_log") or []
    assert len(version_log) == 1, "陪聊轮不该追加版本志"


# ============================================================
# 2) 歧义 → 澄清引导（脑子 stub 下必然失败 → 壳3 保守地板）
# ============================================================


def test_ambiguous_feedback_with_plan_gets_clarify_bubble():
    session_id = "e2c_clarify_floor"
    baseline = _seed_plan(session_id)

    # 不含任何强信号词 / canonical 字面 / 提问 / 预约 / 确认 / 软约束线索——
    # 落到脑子（stub 下必然解析失败）→ 壳3 保守地板：有方案 → clarify。
    events = _drive_turn(user_input="这个不太好", session_id=session_id)
    types_ = [e.type.value for e in events]
    assert "itinerary_ready" not in types_, f"澄清不该默默重规划，events={types_}"

    payload = _chitchat_payload(events)
    assert payload["input_kind"] == "clarify"
    assert len(payload["cta_chips"]) == 3, "壳3 有方案地板应带三个澄清 chip"

    after = _state_values(session_id)["itinerary"]
    assert after == baseline, "澄清轮不该改动方案"


# ============================================================
# 3) 确认（预约指令）→ 引导按钮，绝不自动下单
# ============================================================


def test_confirm_booking_guides_button_without_auto_order():
    session_id = "e2c_confirm_guides_button"
    baseline = _seed_plan(session_id)

    events = _drive_turn(user_input="给我预约吧", session_id=session_id)
    types_ = [e.type.value for e in events]
    assert "itinerary_ready" not in types_, f"确认不该触发重规划，events={types_}"

    payload = _chitchat_payload(events)
    assert payload["input_kind"] == "confirm"
    confirm_chips = [c for c in payload["cta_chips"] if c.get("action") == "confirm"]
    assert confirm_chips, "确认气泡应带一个 action=confirm 的按钮"

    after_state = _state_values(session_id)
    assert after_state["itinerary"] == baseline, "确认轮不该改动方案"
    orders = getattr(after_state["itinerary"], "orders", None) or []
    assert not orders, "L0 全局禁令 1：文本确认绝不自动下单，只引导到显式按钮"


# ============================================================
# 4) 反馈（强信号）→ 走 refiner 路径
# ============================================================


def test_strong_feedback_routes_through_refiner():
    session_id = "e2c_feedback_refiner"
    _seed_plan(session_id)

    events = _drive_turn(user_input="太远了，帮我换近一点的地方", session_id=session_id)
    types_ = [e.type.value for e in events]
    assert "refinement_start" in types_, f"反馈应先推 refinement_start，events={types_}"
    assert "itinerary_ready" in types_, f"反馈应产出调整后的新方案，events={types_}"
    assert "chitchat_reply" not in types_, "反馈不应经过 chitchat 气泡通道"

    version_log = _state_values(session_id).get("plan_version_log") or []
    assert len(version_log) == 2, f"反馈轮应追加第二条版本志，实际={version_log!r}"
