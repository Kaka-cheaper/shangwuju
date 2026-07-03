"""tests.test_narration_plan_recap —— ADR-0011 决策 3 E-2-b 验收：narration
消费方案版本志切片（"我考虑了你之前说的"回顾句）。

覆盖：
1. **模板路径确定性回顾**：`_template_narration(plan_recap=...)` 插入
   "这版是照你『太远了』的反馈调过的"式回顾句；不传则无。
2. **触发纪律**：`_plan_recap_clause` 仅在版本志末条 trigger=="feedback" 时
   产出回顾句——首轮（first）/换菜（adjust）/确认（confirm）/空版本志一律
   空串（"首轮不硬扯"，任务书原文）；summary 格式对不上时退化为通用回顾句。
3. **节点级接线**：narrate_node（stub → 模板路径）在反馈版本后 narration 含
   回顾句、首轮版本后不含。
4. **LLM prompt 指令**：`build_narrator_user_message(plan_recap=...)` 追加
   【上版回顾】一句指令；空串不追加。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# agent 命名空间桥接（与 test_narrator_full_nodes 同款）
if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.graph.nodes.narrate import _plan_recap_clause, narrate_node  # noqa: E402
from agent.intent.narrator import _template_narration  # noqa: E402
from agent.intent.prompts.narrator_prompt import build_narrator_user_message  # noqa: E402
from schemas.intent import IntentExtraction  # noqa: E402
from schemas.itinerary import ActivityNode, Hop, Itinerary  # noqa: E402

_RECAP_SENTENCE = "这版是照你『太远了』的反馈调过的"


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


def _make_itinerary() -> Itinerary:
    nodes = [
        ActivityNode(node_id="n_hs", kind="出发", target_kind="home", target_id="home", start_time="14:00", duration_min=0, title="家"),
        ActivityNode(node_id="n_1", kind="主活动", target_kind="poi", target_id="P022", start_time="14:15", duration_min=90, title="毛球先生猫咖"),
        ActivityNode(node_id="n_2", kind="用餐", target_kind="restaurant", target_id="R001", start_time="17:30", duration_min=60, title="鲸落·健康简餐"),
        ActivityNode(node_id="n_he", kind="回家", target_kind="home", target_id="home", start_time="19:00", duration_min=0, title="家"),
    ]
    hops = [
        Hop(hop_id="h_0", from_node_id="n_hs", to_node_id="n_1", start_time="14:00", minutes=15, mode="taxi", path_type="estimated"),
        Hop(hop_id="h_1", from_node_id="n_1", to_node_id="n_2", start_time="15:45", minutes=10, mode="taxi", path_type="estimated"),
        Hop(hop_id="h_2", from_node_id="n_2", to_node_id="n_he", start_time="18:50", minutes=10, mode="taxi", path_type="estimated"),
    ]
    return Itinerary(
        schema_version="edge_v1",
        summary="下午安排",
        nodes=nodes,
        hops=hops,
        total_minutes=300,
    )


def _feedback_version_log() -> list[dict]:
    return [
        {"version_n": 1, "summary": "v1: 按『今天下午带老婆孩子』出方案", "trigger": "first", "timestamp": 1},
        {"version_n": 2, "summary": "v2: 应『太远了』调整", "trigger": "feedback", "timestamp": 2},
    ]


# ============================================================
# 1) 模板路径确定性回顾
# ============================================================


def test_template_narration_inserts_recap_when_given():
    text = _template_narration(
        _make_intent(), _make_itinerary(), "stream", plan_recap=_RECAP_SENTENCE
    )
    assert _RECAP_SENTENCE in text
    # 回顾句在开场之后、活动复述之前（句式：{opener}{recap}，{body}。）
    assert text.index(_RECAP_SENTENCE) < text.index("毛球先生猫咖")


def test_template_narration_no_recap_by_default():
    text = _template_narration(_make_intent(), _make_itinerary(), "stream")
    assert "这版是照" not in text and "反馈调过" not in text


# ============================================================
# 2) 触发纪律：_plan_recap_clause
# ============================================================


def test_recap_clause_feedback_trigger_extracts_quote():
    clause = _plan_recap_clause(tuple(_feedback_version_log()))
    assert clause == _RECAP_SENTENCE


def test_recap_clause_silent_for_non_feedback_last_entry():
    # 首轮
    first_only = ({"version_n": 1, "summary": "v1: 按『带娃』出方案", "trigger": "first", "timestamp": 1},)
    assert _plan_recap_clause(first_only) == ""
    # 换菜（graph_adjust trigger="adjust"）
    adjust_last = (
        *_feedback_version_log(),
        {"version_n": 3, "summary": "v3: 按『更便宜的』把「A」换成「B」", "trigger": "adjust", "timestamp": 3},
    )
    assert _plan_recap_clause(adjust_last) == ""
    # 确认（graph_confirm trigger="confirm"）
    confirm_last = (
        *_feedback_version_log(),
        {"version_n": 3, "summary": "v3: 已确认下单", "trigger": "confirm", "timestamp": 3},
    )
    assert _plan_recap_clause(confirm_last) == ""
    # 空版本志
    assert _plan_recap_clause(()) == ""


def test_recap_clause_degrades_when_summary_format_unknown():
    log = ({"version_n": 2, "summary": "没有引用格式的摘要", "trigger": "feedback", "timestamp": 2},)
    assert _plan_recap_clause(log) == "这版是按你刚才的反馈调过的"


# ============================================================
# 3) 节点级接线：narrate_node 模板路径（stub LLM）
# ============================================================


def test_narrate_node_feedback_version_narration_contains_recap():
    state = {
        "intent": _make_intent(),
        "itinerary": _make_itinerary(),
        "plan_version_log": _feedback_version_log(),
    }
    out = narrate_node(state)  # type: ignore[arg-type]
    assert _RECAP_SENTENCE in (out.get("narration") or ""), (
        f"反馈版本后 narration 应含确定性回顾句：{out.get('narration')!r}"
    )


def test_narrate_node_first_version_no_recap():
    state = {
        "intent": _make_intent(),
        "itinerary": _make_itinerary(),
        "plan_version_log": [
            {"version_n": 1, "summary": "v1: 按『今天下午带老婆孩子』出方案", "trigger": "first", "timestamp": 1}
        ],
    }
    out = narrate_node(state)  # type: ignore[arg-type]
    narration = out.get("narration") or ""
    assert "这版是照" not in narration and "反馈调过" not in narration, (
        f"首轮不硬扯回顾句：{narration!r}"
    )


# ============================================================
# 4) LLM prompt 指令：build_narrator_user_message
# ============================================================


def test_prompt_includes_recap_instruction_when_given():
    msg = build_narrator_user_message(
        intent_dict=_make_intent().model_dump(),
        itinerary_dict=_make_itinerary().model_dump(),
        stage_label="stream",
        plan_recap=_RECAP_SENTENCE,
    )
    assert "【上版回顾】" in msg
    assert _RECAP_SENTENCE in msg
    assert "自然带一句" in msg


def test_prompt_omits_recap_instruction_when_empty():
    msg = build_narrator_user_message(
        intent_dict=_make_intent().model_dump(),
        itinerary_dict=_make_itinerary().model_dump(),
        stage_label="stream",
    )
    assert "【上版回顾】" not in msg
