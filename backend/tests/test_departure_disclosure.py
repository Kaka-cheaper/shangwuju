"""tests.test_departure_disclosure —— 出发时刻分歧强制披露（C3，ADR-0017）。

【钉住的行为】意图卡渲染 intent.start_time 声明值、时间轴渲染折叠后
nodes[0].start_time——两者分歧（声明含明确钟点且折叠值更晚）时，narration
定稿后由代码后置追加一句披露（方案 1.22 候选 (ii)，拍板项 P9）：
"你说 19:00 出门——其实 19:25 出发正好赶上，……想按原时间出门先在附近
转转也行，跟我说。"——必说、数字必对、含出路（拍板项 P2）。

【测试矩阵】

```
| Test | 场景                           | 验证重点                            |
|------|--------------------------------|-------------------------------------|
| D1   | 声明钟点 + 折叠值更晚           | 双值都在句中 + 含"跟我说"出路       |
| D2   | 无钟点声明（时段 token）        | 不披露（负例）                      |
| D3   | 无分歧（声明==折叠 / 折叠更早） | 不披露（幂等负例）                  |
| D4   | ISO 日期时间格式声明            | 钟点正确提取                        |
| D5   | narrate_node 集成（模板路径）   | 披露句真实出现在 narration 尾部      |
| D6   | narrate_node 无钟点集成         | narration 不含披露句                |
| D7   | 防重复：文案已含该句            | 不二次追加                          |
```

LLM/模板两条路径共用 narrate_node 的同一个追加点（代码结构级保证），
测试在 stub（模板路径）上验证即覆盖该追加点本身。
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

from agent.core.llm_client_stub import StubLLMClient  # noqa: E402
from agent.graph.nodes import narrate as narrate_mod  # noqa: E402
from agent.graph.nodes.narrate import (  # noqa: E402
    _departure_fold_disclosure,
    narrate_node,
)
from agent.intent.parser import parse_intent  # noqa: E402
from agent.planning.planners.rule_planner import plan_itinerary  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402


def _intent(start_time: str) -> IntentExtraction:
    return IntentExtraction(
        start_time=start_time,
        duration_hours=[3, 5],
        distance_max_km=5.0,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="晚上七点出门吃一顿",
        parse_confidence=0.9,
    )


class _FakeNode:
    def __init__(self, start_time: str):
        self.start_time = start_time


class _FakeItinerary:
    def __init__(self, first_start: str):
        self.nodes = [_FakeNode(first_start)]


# ============================================================
# D1-D4：披露句构造单测
# ============================================================


def test_D1_disclosure_contains_both_clocks_and_invite():
    clause = _departure_fold_disclosure(_intent("19:00"), _FakeItinerary("19:25"))
    assert "19:00" in clause, "声明钟点必须在句中（意图卡显示的就是它）"
    assert "19:25" in clause, "折叠钟点必须在句中（时间轴显示的就是它）"
    assert "跟我说" in clause, "措辞必须含出路（拍板项 P2）"


def test_D2_no_disclosure_when_no_declared_clock():
    """时段 token（today_evening）无钟点可比 → 不披露。"""
    assert _departure_fold_disclosure(
        _intent("today_evening"), _FakeItinerary("19:25")
    ) == ""


def test_D3_no_disclosure_when_no_divergence():
    """声明==折叠 → 无分歧不披露；折叠更早（系统建议早出门）也不披露
    （披露语义是"等待被吸收"，不覆盖反方向）。"""
    assert _departure_fold_disclosure(_intent("19:25"), _FakeItinerary("19:25")) == ""
    assert _departure_fold_disclosure(_intent("20:00"), _FakeItinerary("19:30")) == ""


def test_D4_iso_datetime_declared_clock_extracted():
    clause = _departure_fold_disclosure(
        _intent("2026-07-02T19:00"), _FakeItinerary("19:25")
    )
    assert "19:00" in clause and "19:25" in clause


def test_D4b_defensive_boundaries():
    """nodes 空 / nodes[0].start_time 非 HH:MM → 不披露不崩。"""

    class _NoNodes:
        nodes = []

    assert _departure_fold_disclosure(_intent("19:00"), _NoNodes()) == ""
    assert _departure_fold_disclosure(_intent("19:00"), _FakeItinerary("待定")) == ""


# ============================================================
# D5-D7：narrate_node 集成（追加点真实接线）
# ============================================================


def _narrate_state(start_time: str) -> dict:
    client = StubLLMClient()
    stub_intent = parse_intent("今天下午想出去玩", client=client)
    result = plan_itinerary(stub_intent)
    assert result.success and result.itinerary is not None
    intent = stub_intent.model_copy(update={"start_time": start_time})
    return {
        "intent": intent,
        "itinerary": result.itinerary,
        "user_id": "demo_user",
    }


def test_D5_narrate_node_appends_disclosure_on_divergence():
    """声明 00:05（必早于任何真实出发时刻）→ narration 尾部出现披露句，
    且引用的折叠值就是 nodes[0].start_time。"""
    state = _narrate_state("00:05")
    out = narrate_node(state)
    narration = out["narration"]
    actual = state["itinerary"].nodes[0].start_time
    assert "00:05" in narration
    assert actual in narration
    assert "跟我说" in narration


def test_D6_narrate_node_no_disclosure_without_clock():
    state = _narrate_state("today_afternoon")
    out = narrate_node(state)
    assert "出发正好赶上" not in out["narration"]


def test_D7_no_double_append_when_text_already_contains_clause(monkeypatch):
    """防重复：generate_title_and_narration 已产出含披露句的文案（未来 LLM
    路径若升级为 prompt 织入即此形态）→ 追加点不重复拼接。"""
    state = _narrate_state("00:05")
    clause = _departure_fold_disclosure(state["intent"], state["itinerary"])
    assert clause

    pre_baked = f"这是开场白。{clause}"
    monkeypatch.setattr(
        narrate_mod,
        "generate_title_and_narration",
        lambda **kwargs: ("标题", pre_baked, []),
    )
    out = narrate_node(state)
    assert out["narration"].count(clause) == 1
