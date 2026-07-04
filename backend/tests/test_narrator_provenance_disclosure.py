"""tests.test_narrator_provenance_disclosure —— ADR-0014 决策 1（G-1）narration
出处诚实告知消费方测试。

背景：字段出处（field_provenance）落地后，narration 应该能诚实说出"距离你
没提，我按默认安排的" / "我从你的话里猜你想要 X，不合适可以说"这类口径——
与既有的未满足品类诚实告知（`test_narrator_honest_substitution.py`）同一套
"模板路径确定性、LLM 路径走 prompt 指令"分工。

覆盖：
1. 模板路径 `_template_narration`：distance_max_km 出处 default → 出现
   "你没提…按默认"类句子；无出处信号（field_provenance=None，旧数据/回归）
   → 不出现，不影响既有行为。
2. 模板路径：某标签出处 inferred → 出现"我猜你想要…"类句子。
3. LLM 路径 prompt 接线：`build_narrator_user_message(provenance_hints=...)`
   附加【出处信息】触发块；不传/全空则不附加。
4. system prompt 含【出处诚实告知】规则段。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


def _install_agent_stub() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    agent_dir = backend_root / "agent"
    if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
        stub = types.ModuleType("agent")
        stub.__path__ = [str(agent_dir)]
        sys.modules["agent"] = stub


_install_agent_stub()

from agent.intent.narrator import _provenance_hints, _template_narration  # noqa: E402
from agent.intent.prompts.narrator_prompt import (  # noqa: E402
    NARRATOR_SYSTEM_PROMPT,
    build_narrator_user_message,
)
from schemas.intent import IntentExtraction  # noqa: E402
from schemas.itinerary import ActivityNode, Hop, Itinerary  # noqa: E402


def _make_itinerary() -> Itinerary:
    nodes = [
        ActivityNode(node_id="n_hs", kind="出发", target_kind="home", target_id="home", start_time="14:00", duration_min=0, title="家"),
        ActivityNode(node_id="n_1", kind="主活动", target_kind="poi", target_id="P022", start_time="14:15", duration_min=90, title="毛球先生猫咖"),
        ActivityNode(node_id="n_he", kind="回家", target_kind="home", target_id="home", start_time="16:00", duration_min=0, title="家"),
    ]
    hops = [
        Hop(hop_id="h_0", from_node_id="n_hs", to_node_id="n_1", start_time="14:00", minutes=15, mode="taxi", path_type="estimated"),
        Hop(hop_id="h_1", from_node_id="n_1", to_node_id="n_he", start_time="15:45", minutes=15, mode="taxi", path_type="estimated"),
    ]
    return Itinerary(
        schema_version="edge_v1",
        summary="下午安排",
        nodes=nodes,
        hops=hops,
        total_minutes=120,
    )


def _base_intent(**overrides) -> IntentExtraction:
    base = dict(
        start_time="today_afternoon",
        duration_hours=[2, 3],
        distance_max_km=5.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="独处放空",
        raw_input="想一个人出去转转",
        parse_confidence=0.8,
    )
    base.update(overrides)
    return IntentExtraction(**base)


# ============================================================
# 1. 模板路径：distance_max_km 出处 default → "你没提…按默认" 类句子
# ============================================================


def test_template_narration_discloses_distance_default():
    intent = _base_intent(field_provenance={"distance_max_km": "default"})
    text = _template_narration(intent, _make_itinerary(), "stream")
    assert "没提" in text and "默认" in text and "5" in text, f"应出现距离默认告知：{text}"


def test_template_narration_silent_without_provenance_data():
    """无出处信息（field_provenance=None，旧数据/回归场景）→ 不出现该句子，
    也不应该影响既有 narration 输出（向后兼容）。"""
    intent = _base_intent()  # field_provenance 默认 None
    text = _template_narration(intent, _make_itinerary(), "stream")
    assert "没提" not in text and "按默认" not in text


def test_template_narration_silent_when_distance_provenance_is_user_stated():
    """distance 出处是 user_stated（用户自己说的）时不应该出现"没提"这种误导句。"""
    intent = _base_intent(field_provenance={"distance_max_km": "user_stated"})
    text = _template_narration(intent, _make_itinerary(), "stream")
    assert "没提" not in text


# ============================================================
# 2. 模板路径：inferred 标签 → "我猜你想要…" 类句子
# ============================================================


def test_template_narration_discloses_inferred_tag():
    intent = _base_intent(
        experience_tags=["独处舒缓"],
        field_provenance={"experience_tags:独处舒缓": "inferred"},
    )
    text = _template_narration(intent, _make_itinerary(), "stream")
    assert "猜" in text and "独处舒缓" in text, f"应出现推断标签告知：{text}"


# ============================================================
# 3. _provenance_hints 提炼信号（LLM 路径与模板路径共用的信号计算）
# ============================================================


def test_provenance_hints_empty_when_no_provenance_data():
    assert _provenance_hints(_base_intent()) == {}


def test_provenance_hints_picks_first_inferred_tag_only():
    intent = _base_intent(
        dietary_constraints=["不辣", "日料"],
        field_provenance={
            "dietary_constraints:不辣": "user_stated",
            "dietary_constraints:日料": "inferred",
        },
    )
    hints = _provenance_hints(intent)
    assert hints.get("inferred_tag") == "日料"


# ============================================================
# 5. budget_ambiguous（ADR-0014 决策 3 · G-3）："别太贵"类定性预算表达
# ============================================================


def test_provenance_hints_picks_up_budget_ambiguous_signal():
    """budget_per_person=None + ambiguous_fields 含 "budget_per_person"
    （parser 对定性预算表达的自报，见 intent_parser_prompt.py【预算抽取规则】）
    → hints 里出现 budget_ambiguous 信号，即使 field_provenance 全空
    （budget 信号不依赖 field_provenance，只依赖 ambiguous_fields）。"""
    intent = _base_intent(ambiguous_fields=["budget_per_person"])
    hints = _provenance_hints(intent)
    assert hints.get("budget_ambiguous") is True


def test_provenance_hints_silent_when_budget_already_quantified():
    """budget_per_person 已经有具体数字（哪怕 ambiguous_fields 里还留着旧
    标记）→ 不触发 budget_ambiguous，避免"已经量化了却还说没法量化"的
    自相矛盾文案。"""
    intent = _base_intent(
        budget_per_person=50.0, ambiguous_fields=["budget_per_person"]
    )
    hints = _provenance_hints(intent)
    assert "budget_ambiguous" not in hints


def test_provenance_hints_silent_when_no_budget_ambiguity():
    """没有预算相关的自报信号 → 不触发（不误报）。"""
    intent = _base_intent()
    hints = _provenance_hints(intent)
    assert "budget_ambiguous" not in hints


def test_template_narration_discloses_budget_ambiguous():
    intent = _base_intent(ambiguous_fields=["budget_per_person"])
    text = _template_narration(intent, _make_itinerary(), "stream")
    assert "预算" in text and "没法" in text, f"应出现预算听到但没法量化的告知：{text}"


def test_prompt_includes_budget_ambiguous_instruction():
    msg = build_narrator_user_message(
        intent_dict=_base_intent().model_dump(),
        itinerary_dict=_make_itinerary().model_dump(),
        stage_label="stream",
        provenance_hints={"budget_ambiguous": True},
    )
    assert "【出处信息】" in msg
    assert "预算" in msg


def test_system_prompt_has_budget_ambiguous_disclosure_rule():
    assert "预算顾虑" in NARRATOR_SYSTEM_PROMPT or "没法精确卡预算" in NARRATOR_SYSTEM_PROMPT


# ============================================================
# 4. LLM 路径 prompt 接线：build_narrator_user_message(provenance_hints=...)
# ============================================================


def test_prompt_includes_provenance_instruction_when_hints_given():
    msg = build_narrator_user_message(
        intent_dict=_base_intent().model_dump(),
        itinerary_dict=_make_itinerary().model_dump(),
        stage_label="stream",
        provenance_hints={"distance_default": True, "distance_km": 5.0},
    )
    assert "【出处信息】" in msg
    assert "默认" in msg


def test_prompt_omits_provenance_instruction_when_hints_empty():
    msg = build_narrator_user_message(
        intent_dict=_base_intent().model_dump(),
        itinerary_dict=_make_itinerary().model_dump(),
        stage_label="stream",
    )
    assert "【出处信息】" not in msg

    msg_empty_dict = build_narrator_user_message(
        intent_dict=_base_intent().model_dump(),
        itinerary_dict=_make_itinerary().model_dump(),
        stage_label="stream",
        provenance_hints={},
    )
    assert "【出处信息】" not in msg_empty_dict


def test_system_prompt_has_provenance_disclosure_rules():
    assert "出处诚实告知" in NARRATOR_SYSTEM_PROMPT
    assert "按默认" in NARRATOR_SYSTEM_PROMPT


# ============================================================
# 6. 归因措辞跟着出处走（文案修缮批 · 真 LLM 点火 A6 实锤）
# ============================================================
#
# A6 实锤：用户原话"周末下午约了闺蜜想找个网红的地方拍拍照吃个下午茶"，
# 「亲密情侣」是 experience_tags 里 provenance=inferred 的推断标签，叙事却
# 说"**你提到的**「亲密情侣」我猜可能是……"——"你提到的"+"我猜"同句自相
# 矛盾，把词典推断归因成用户原话（出处误归因）。修法：prompt 的出处告知
# 规则 + inferred 信号行都显式禁止对推断标签用"你提到"类措辞。


def test_system_prompt_forbids_claiming_user_said_for_inferred():
    assert "把推断说成用户原话" in NARRATOR_SYSTEM_PROMPT, (
        "出处诚实告知规则应显式禁止对 inferred 标签用「你提到的」类归因措辞"
        "（A6 实锤：『你提到的「亲密情侣」我猜…』自相矛盾）"
    )


def test_prompt_inferred_hint_carries_anti_misattribution_instruction():
    msg = build_narrator_user_message(
        intent_dict=_base_intent().model_dump(),
        itinerary_dict=_make_itinerary().model_dump(),
        stage_label="stream",
        provenance_hints={"inferred_tag": "亲密情侣"},
    )
    assert "亲密情侣" in msg
    assert "不能说成「你提到的亲密情侣」" in msg, (
        "inferred 信号行应就地钉死禁用措辞，不能只靠 system prompt 远程规则"
    )
