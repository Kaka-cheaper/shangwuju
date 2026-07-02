"""tests.test_replan_canonical_reuse —— E-1 缺口修复:「重新规划一个」复用原始需求。

【这是什么问题】E-1 地板澄清 chip「重新规划一个」点击回传后,壳2 判 planning
→ intent 路径把这五个字当新需求解析——用户原始需求丢失,解析出空泛意图
(F-6 联动审查坐实,ADR-0011 落地状态节有案)。修复:intent_node 识别到
canonical 字面且上一事件 intent 存在时,复用其 raw_input 重解;房间侧
`_trigger_fresh_plan` 同款(测试在 test_room_route_turn_dispatch.py 5b 段)。

时序前提(本测试同时钉住):intent_node 读旧 intent 发生在它自己返回的
reset_for_new_episode() diff 之前——E-0-b 的重置发生在节点返回值合并时,
不是进入节点时,所以旧 intent 在替换判定时刻仍可读。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.graph.nodes.intent import intent_node  # noqa: E402
from agent.graph.state import make_initial_state  # noqa: E402
from agent.intent.prompts.router_prompt import FLOOR_REPLAN_SEND  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402

_ORIGINAL_RAW = "今晚和兄弟出来撸串喝点酒，人均 50 左右就行"


def _prior_intent() -> IntentExtraction:
    return IntentExtraction(
        start_time="today_evening",
        duration_hours=[3, 5],
        distance_max_km=5.0,
        companions=[Companion(role="朋友", count=3)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="朋友热闹",
        raw_input=_ORIGINAL_RAW,
        parse_confidence=0.9,
    )


def test_replan_literal_reuses_prior_raw_input():
    """有上一事件 intent 时,「重新规划一个」按原始需求重解,不解析这五个字本身。"""
    st = make_initial_state(user_input=FLOOR_REPLAN_SEND, session_id="replan-reuse")
    st["intent"] = _prior_intent()

    out = intent_node(st)

    new_intent = out["intent"]
    # stub 解析器会把输入包一层提示词壳存进 raw_input(既有行为),故断言包含语义:
    # 原始需求文本在、canonical 五个字不在——替换发生在解析之前。
    assert _ORIGINAL_RAW in new_intent.raw_input, (
        f"应复用原始需求重解,实际 raw_input={new_intent.raw_input!r}"
    )
    assert FLOOR_REPLAN_SEND not in new_intent.raw_input


def test_replan_literal_without_prior_intent_parses_as_is():
    """防御分支:无上一事件 intent(理论上地板 chip 只在有方案时发出)→ 原样解析,不崩。"""
    st = make_initial_state(user_input=FLOOR_REPLAN_SEND, session_id="replan-no-prior")

    out = intent_node(st)

    assert FLOOR_REPLAN_SEND in out["intent"].raw_input
