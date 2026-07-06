"""tests.test_narrate_feedback_no_change_honesty —— B2 · A2 诚实层安全网。

【背景】"换个店铺"这类反馈若走既有全局重排（refiner → execute → planner →
…），系统没有按店名排除的机制（`agent.intent.prompts.refiner_prompt` C 类
既定诊断），重排后方案实体可能一个都没变——narration 却可能仍宣称"这版照你
说的换了"。B2 把"换店"类反馈分流到 node_swap 引擎（`agent.graph.nodes.
store_swap`），但"太远/太贵/太赶"这类**仍然**走全局重排的反馈没有对应的
排除机制，同样可能 0 处实体变化。本文件验证这条安全网：

1. `agent.graph.nodes.narrate._feedback_entities_unchanged` 纯函数的判定
   逻辑（route_kind 必须是 feedback / 需要快照可比 / 集合相等才算"没变"）。
2. 直调 `narrate_node`：diff=0 时模板路径产出诚实告知句、不宣称"已经换了"；
   diff≠0 或非反馈轮时不触发这条告知（避免误报）。
3. prompt 层接线：`narrator_prompt.build_narrator_user_message` 正确按
   `feedback_no_change` 追加指令，`NARRATOR_SYSTEM_PROMPT` 含对应诚实规则。
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
from agent.graph.nodes.narrate import (  # noqa: E402
    _entity_id_set,
    _feedback_entities_unchanged,
    narrate_node,
)
from agent.intent.parser import parse_intent  # noqa: E402
from agent.intent.prompts.narrator_prompt import (  # noqa: E402
    NARRATOR_SYSTEM_PROMPT,
    build_narrator_user_message,
)
from agent.planning.planners.rule_planner import plan_itinerary  # noqa: E402

_NO_CHANGE_PHRASE_MARKERS = ("没能真的换掉", "没能真的换")
_FORBIDDEN_RESULT_CLAIMS = (
    "已经换成", "已经帮你换", "换成了", "已经换掉", "帮你换了", "已换成",
)


def _plan() -> tuple:
    client = StubLLMClient()
    intent = parse_intent("今天下午想出去玩", client=client)
    result = plan_itinerary(intent)
    assert result.success and result.itinerary is not None
    return intent, result.itinerary


def _prior_ids_same_as(itinerary) -> list[str]:
    return sorted(_entity_id_set(itinerary))


# ============================================================
# 1. _feedback_entities_unchanged 纯函数判定
# ============================================================


def test_unchanged_true_when_feedback_and_entity_sets_equal():
    _intent, itinerary = _plan()
    prior = _prior_ids_same_as(itinerary)
    state = {"route_kind": "feedback", "feedback_prior_entities": prior}
    assert _feedback_entities_unchanged(state, itinerary) is True


def test_unchanged_false_when_entities_actually_differ():
    _intent, itinerary = _plan()
    state = {
        "route_kind": "feedback",
        "feedback_prior_entities": ["poi:FAKE_OLD", "restaurant:FAKE_OLD2"],
    }
    assert _feedback_entities_unchanged(state, itinerary) is False


def test_unchanged_false_when_not_feedback_route():
    _intent, itinerary = _plan()
    prior = _prior_ids_same_as(itinerary)
    state = {"route_kind": "planning", "feedback_prior_entities": prior}
    assert _feedback_entities_unchanged(state, itinerary) is False


def test_unchanged_false_when_no_prior_snapshot():
    _intent, itinerary = _plan()
    state = {"route_kind": "feedback", "feedback_prior_entities": None}
    assert _feedback_entities_unchanged(state, itinerary) is False


def test_unchanged_false_when_current_itinerary_has_no_entities():
    """防御性：当前方案无非 home 节点（不该真实发生的边界）时不触发——避免
    "两边都是空集合"的平凡相等被误判为"没变"。"""

    class _EmptyNode:
        target_kind = "home"
        target_id = "home"

    class _EmptyItinerary:
        nodes = [_EmptyNode()]

    state = {"route_kind": "feedback", "feedback_prior_entities": []}
    assert _feedback_entities_unchanged(state, _EmptyItinerary()) is False


# ============================================================
# 2. 直调 narrate_node：模板路径诚实告知
# ============================================================


def _build_state(*, route_kind: str, feedback_prior_entities) -> dict:
    intent, itinerary = _plan()
    return {
        "intent": intent,
        "itinerary": itinerary,
        "user_id": "demo_user",
        "route_kind": route_kind,
        "feedback_prior_entities": feedback_prior_entities,
        "violations": [],
        "advisories": [],
    }


def test_narrate_node_discloses_no_change_and_never_claims_swap():
    intent, itinerary = _plan()
    prior = _prior_ids_same_as(itinerary)
    state = {
        "intent": intent,
        "itinerary": itinerary,
        "user_id": "demo_user",
        "route_kind": "feedback",
        "feedback_prior_entities": prior,
        "violations": [],
        "advisories": [],
    }

    result = narrate_node(state)
    narration = result.get("narration") or ""

    assert any(marker in narration for marker in _NO_CHANGE_PHRASE_MARKERS), narration
    for phrase in _FORBIDDEN_RESULT_CLAIMS:
        assert phrase not in narration, f"narration 不许宣称结果性断言 {phrase!r}：{narration!r}"


def test_narrate_node_does_not_disclose_when_entities_actually_changed():
    state = _build_state(
        route_kind="feedback",
        feedback_prior_entities=["poi:FAKE_OLD", "restaurant:FAKE_OLD2"],
    )
    result = narrate_node(state)
    narration = result.get("narration") or ""
    for marker in _NO_CHANGE_PHRASE_MARKERS:
        assert marker not in narration, narration


def test_narrate_node_does_not_disclose_on_planning_route():
    intent, itinerary = _plan()
    prior = _prior_ids_same_as(itinerary)
    state = _build_state(route_kind="planning", feedback_prior_entities=prior)
    result = narrate_node(state)
    narration = result.get("narration") or ""
    for marker in _NO_CHANGE_PHRASE_MARKERS:
        assert marker not in narration, narration


# ============================================================
# 3. prompt 层接线
# ============================================================


def test_system_prompt_has_no_change_honesty_rule():
    assert "反馈未换到别的候选" in NARRATOR_SYSTEM_PROMPT
    assert any(p in NARRATOR_SYSTEM_PROMPT for p in _FORBIDDEN_RESULT_CLAIMS)


def test_build_user_message_appends_no_change_instruction_only_when_true():
    msg_true = build_narrator_user_message(
        intent_dict={"companions": []},
        itinerary_dict={"nodes": []},
        stage_label="stream",
        feedback_no_change=True,
    )
    assert "反馈未换到别的候选" in msg_true

    msg_false = build_narrator_user_message(
        intent_dict={"companions": []},
        itinerary_dict={"nodes": []},
        stage_label="stream",
        feedback_no_change=False,
    )
    assert "反馈未换到别的候选" not in msg_false
