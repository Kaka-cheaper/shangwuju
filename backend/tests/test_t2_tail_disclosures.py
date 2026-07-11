"""tests.test_t2_tail_disclosures —— T2 尾巴：钟点保形 + 终点门禁披露（C8）。

【钉住的行为】
1. parser prompt 含「钟点保形」规则（用户明说钟点必须保留 HH:MM 形式，禁归一
   成时段 token）——这是 C3 披露句的上游依赖（方案 1.22）：钟点丢形则披露
   永不触发、折叠变静默。LLM 依从性由真 LLM 彩排验，这里钉 prompt 文本在场。
2. parser prompt 含「终点/门禁自报」规则（"十点前得回家" → ambiguous_fields
   加 "return_by"）。
3. `_return_by_disclosure`（narrate 层，G-3 定性预算同款范式）："return_by"
   在 ambiguous_fields 时把预计到家时刻（nodes[-1].start_time）亮给用户核；
   不在则零输出。narrate_node 集成走同一后置追加点。
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

from agent.graph.nodes.narrate import _return_by_disclosure  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402


def _intent(**overrides) -> IntentExtraction:
    kw = dict(
        start_time="today_evening",
        duration_hours=[3, 4],
        distance_max_km=5.0,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="晚上出去转转，十点前得回家",
        parse_confidence=0.85,
    )
    kw.update(overrides)
    return IntentExtraction(**kw)


class _FakeNode:
    def __init__(self, start_time: str):
        self.start_time = start_time


class _FakeItinerary:
    def __init__(self, *starts: str):
        self.nodes = [_FakeNode(s) for s in starts]


# ============================================================
# 1/2. prompt 规则在场钉（LLM 依从性归真 LLM 彩排）
# ============================================================


def test_parser_prompt_contains_clock_preservation_rule():
    from agent.intent.prompts.intent_parser_prompt import INTENT_PARSER_SYSTEM_PROMPT

    assert "钟点保形" in INTENT_PARSER_SYSTEM_PROMPT
    assert "禁止" in INTENT_PARSER_SYSTEM_PROMPT
    # 规则必须点名"归一成时段 token"这个失败方向
    assert "时段 token" in INTENT_PARSER_SYSTEM_PROMPT


def test_parser_prompt_contains_return_by_self_report_rule():
    from agent.intent.prompts.intent_parser_prompt import INTENT_PARSER_SYSTEM_PROMPT

    assert "return_by" in INTENT_PARSER_SYSTEM_PROMPT
    assert "终点" in INTENT_PARSER_SYSTEM_PROMPT


# ============================================================
# 3. 终点门禁披露
# ============================================================


def test_return_by_disclosure_shows_arrive_home_time():
    intent = _intent(ambiguous_fields=["return_by"])
    itin = _FakeItinerary("19:25", "19:30", "21:45")
    clause = _return_by_disclosure(intent, itin)
    assert "21:45" in clause, "到家时刻必须等于 nodes[-1].start_time"
    assert "回家" in clause
    assert "跟我说" in clause, "披露带出路（G-3 同款诚实纪律）"


def test_return_by_disclosure_silent_without_signal():
    intent = _intent(ambiguous_fields=[])
    itin = _FakeItinerary("19:25", "19:30", "21:45")
    assert _return_by_disclosure(intent, itin) == ""


def test_return_by_disclosure_defensive_boundaries():
    intent = _intent(ambiguous_fields=["return_by"])

    class _NoNodes:
        nodes = []

    assert _return_by_disclosure(intent, _NoNodes()) == ""
    assert _return_by_disclosure(intent, _FakeItinerary("19:00", "待定")) == ""


def test_narrate_node_appends_return_by_disclosure():
    """集成：narrate_node 的后置追加点真实接线（与 C3 披露同一纪律）。"""
    from agent.core.llm_client_stub import StubLLMClient
    from agent.graph.nodes.narrate import narrate_node
    from agent.intent.parser import parse_intent
    from agent.planning.planners.rule_planner import plan_itinerary

    client = StubLLMClient()
    stub_intent = parse_intent("今天下午想出去玩", client=client)
    result = plan_itinerary(stub_intent)
    assert result.success and result.itinerary is not None
    intent = stub_intent.model_copy(update={"ambiguous_fields": ["return_by"]})

    out = narrate_node(
        {"intent": intent, "itinerary": result.itinerary, "user_id": "demo_user"}
    )
    arrive_home = result.itinerary.nodes[-1].start_time
    assert arrive_home in out["narration"]
    assert "回家" in out["narration"]
