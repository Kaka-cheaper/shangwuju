"""tests.test_narrator_multi_activity_rationale —— ADR-0010 边界节：narration 覆盖多活动。

背景（ADR-0010「边界」节明文遗留）：narrator 按单活动时代写成，3+ 活动的方案
只会逐个复述活动（"14:00 去 A，16:00 去 B，18:00 去 C"），讲不清"为什么选这
几个、为什么这个顺序"——多活动反而更让人困惑。本任务补齐两条路径：

- 模板路径（`_template_narration` / `_multi_activity_rationale`，确定性，可测）：
  活动数 ≥3 时追加一句"选择与顺序理由"，材料从 itinerary 本身现算——留白占比
  （节奏松紧）/ 用餐节点是否落在中后段 / 首尾活动时长对比（活跃靠前舒缓靠后）。
- LLM 路径（`NARRATOR_SYSTEM_PROMPT`）：新增对应指令段 + few-shot，验收退化为
  prompt 内容关键词断言（LLM_PROVIDER=stub 不实际调用 LLM，行为不可测）。

本文件只验证新增行为；既有 narrator 测试（test_narrator_full_nodes.py 等）
不在本文件重复覆盖。
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

from agent.intent.narrator import _multi_activity_rationale, _template_narration  # noqa: E402
from agent.intent.prompts.narrator_prompt import NARRATOR_SYSTEM_PROMPT  # noqa: E402
from schemas.intent import IntentExtraction  # noqa: E402
from schemas.itinerary import ActivityNode, Hop, Itinerary  # noqa: E402


# ============================================================
# Fixtures
# ============================================================


def _intent(*, social: str = "情侣亲密") -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[4, 6],
        distance_max_km=5,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context=social,
        preferred_poi_types=[],
        raw_input="出去玩",
        parse_confidence=0.85,
    )


def _itinerary(nodes: list[ActivityNode], hops: list[Hop], total_minutes: int) -> Itinerary:
    return Itinerary(
        schema_version="edge_v1",
        summary="测试",
        nodes=nodes,
        hops=hops,
        total_minutes=total_minutes,
    )


def _home(node_id: str, start_time: str) -> ActivityNode:
    return ActivityNode(
        node_id=node_id, kind="出发", target_kind="home", target_id="home",
        start_time=start_time, duration_min=0, title="家",
    )


def _hop(hop_id: str, a: str, b: str, start: str, minutes: int) -> Hop:
    return Hop(
        hop_id=hop_id, from_node_id=a, to_node_id=b, start_time=start,
        minutes=minutes, mode="taxi", path_type="estimated",
    )


def _three_node_tight_with_late_meal() -> Itinerary:
    """3 活动 · 紧凑 · 用餐落在后段（第 2/3 位）——猫咖 → 餐 → 电影院。"""
    nodes = [
        _home("n_home_s", "14:00"),
        ActivityNode(node_id="n_1", kind="主活动", target_kind="poi", target_id="P022", start_time="14:15", duration_min=90, title="毛球先生猫咖"),
        ActivityNode(node_id="n_2", kind="用餐", target_kind="restaurant", target_id="R001", start_time="16:30", duration_min=80, title="鹿园甜品"),
        ActivityNode(node_id="n_3", kind="主活动", target_kind="poi", target_id="P028", start_time="18:30", duration_min=120, title="万达 IMAX 电影院"),
        _home("n_home_e", "20:40"),
    ]
    hops = [
        _hop("h_0", "n_home_s", "n_1", "14:00", 15),
        _hop("h_1", "n_1", "n_2", "15:45", 10),
        _hop("h_2", "n_2", "n_3", "17:50", 10),
        _hop("h_3", "n_3", "n_home_e", "20:30", 10),
    ]
    return _itinerary(nodes, hops, 300)


def _three_node_roomy_no_meal_active_first() -> Itinerary:
    """3 活动 · 从容（大留白）· 无用餐 · 首段比末段更长（活跃靠前舒缓靠后）。"""
    nodes = [
        _home("n_home_s", "14:00"),
        ActivityNode(node_id="n_1", kind="主活动", target_kind="poi", target_id="P001", start_time="14:15", duration_min=90, title="海洋馆"),
        ActivityNode(node_id="n_2", kind="主活动", target_kind="poi", target_id="P002", start_time="16:15", duration_min=60, title="儿童乐园"),
        ActivityNode(node_id="n_3", kind="自由", target_kind="poi", target_id="P003", start_time="18:00", duration_min=30, title="散步公园"),
        _home("n_home_e", "19:30"),
    ]
    hops = [
        _hop("h_0", "n_home_s", "n_1", "14:00", 15),
        _hop("h_1", "n_1", "n_2", "15:45", 10),
        _hop("h_2", "n_2", "n_3", "17:15", 10),
        _hop("h_3", "n_3", "n_home_e", "18:30", 10),
    ]
    return _itinerary(nodes, hops, 330)


def _two_node_single_activity() -> Itinerary:
    """2 活动（< 3 阈值）——不应触发理由句。"""
    nodes = [
        _home("n_home_s", "14:00"),
        ActivityNode(node_id="n_1", kind="主活动", target_kind="poi", target_id="P001", start_time="14:15", duration_min=90, title="海洋馆"),
        ActivityNode(node_id="n_2", kind="用餐", target_kind="restaurant", target_id="R001", start_time="16:00", duration_min=60, title="快餐店"),
        _home("n_home_e", "17:15"),
    ]
    hops = [
        _hop("h_0", "n_home_s", "n_1", "14:00", 15),
        _hop("h_1", "n_1", "n_2", "15:45", 15),
        _hop("h_2", "n_2", "n_home_e", "17:00", 15),
    ]
    return _itinerary(nodes, hops, 195)


def _nodes_dump(itin: Itinerary) -> list[dict]:
    return [n.model_dump() for n in itin.nodes]


# ============================================================
# 1) _multi_activity_rationale 单测（纯函数，确定性）
# ============================================================


def test_rationale_empty_below_threshold() -> None:
    """活动数 <3（本例 2 个）→ 不加理由句（避免做作）。"""
    itin = _two_node_single_activity()
    assert _multi_activity_rationale(itin, _nodes_dump(itin)) == ""


def test_rationale_mentions_pace_and_meal_position_when_tight_and_late_meal() -> None:
    """3 活动 · 紧凑 · 用餐落后段 → 理由句含"紧凑"类措辞 + 用餐落位措辞。"""
    itin = _three_node_tight_with_late_meal()
    text = _multi_activity_rationale(itin, _nodes_dump(itin))
    assert text, "3 活动应产出理由句"
    assert "紧凑" in text
    assert "饭" in text and ("后段" in text or "垫肚子" in text)


def test_rationale_mentions_roominess_and_active_first_when_slack_and_no_meal() -> None:
    """3 活动 · 大留白 · 无用餐 · 首段更长 → 理由句含留白措辞 + 活跃靠前措辞。"""
    itin = _three_node_roomy_no_meal_active_first()
    text = _multi_activity_rationale(itin, _nodes_dump(itin))
    assert text, "3 活动应产出理由句"
    assert "留" in text  # "特意多留了些走停的时间"
    assert "前面" in text and "后面" in text  # "精力多的排前面，后面轻松收尾"


def test_rationale_covers_exactly_activity_count() -> None:
    """理由句中提到的数量应与非 home 活动数一致（本例 3）。"""
    itin = _three_node_tight_with_late_meal()
    text = _multi_activity_rationale(itin, _nodes_dump(itin))
    assert "3 个" in text


# ============================================================
# 2) _template_narration 集成：理由句嵌进完整开场白
# ============================================================


def test_template_narration_includes_rationale_for_three_activities() -> None:
    """模板路径整体产出：3 活动方案的开场白含"顺序/选择理由"要素（不只是逐个复述地点）。"""
    text = _template_narration(_intent(), _three_node_tight_with_late_meal(), "stream")
    # 活动仍然全部讲到（不回归 test_narrator_full_nodes 的既有验收）
    assert "毛球先生猫咖" in text
    assert "鹿园甜品" in text
    assert "电影" in text
    # 新增：选择/顺序理由要素
    assert "紧凑" in text
    assert "饭" in text


def test_template_narration_no_rationale_for_two_activities() -> None:
    """2 活动方案不应出现理由句相关措辞（回归：避免对少活动方案硬加解释）。"""
    text = _template_narration(_intent(), _two_node_single_activity(), "stream")
    for phrase in ("排得比较紧凑", "松紧刚好", "特意多留了些走停的时间", "精力多的排前面"):
        assert phrase not in text, f"2 活动不应硬加理由句，命中：{phrase}；文案：{text}"


# ============================================================
# 3) LLM 路径 prompt 内容断言（stub 模式不可测行为，退而求其次）
# ============================================================


def test_system_prompt_has_multi_activity_rationale_rule() -> None:
    """system prompt 必须含「多活动的选择与顺序理由」指令段 + 触发阈值说明。"""
    assert "多活动的选择与顺序理由" in NARRATOR_SYSTEM_PROMPT
    assert "为什么选这几个、为什么这样排" in NARRATOR_SYSTEM_PROMPT
    # 阈值与"不要硬加"纪律都要显式声明
    assert "≥3 个" in NARRATOR_SYSTEM_PROMPT
    assert "不要硬加这句话" in NARRATOR_SYSTEM_PROMPT


def test_system_prompt_rationale_material_sources_named() -> None:
    """材料来源（留白/活跃靠前舒缓靠后/饭点落位/同行人适配）必须在 prompt 里点名，
    不能让 LLM 凭空编一个理由。"""
    text = NARRATOR_SYSTEM_PROMPT
    assert "留白" in text or "留了" in text or "从容" in text
    assert "舒缓活动靠后" in text or "舒缓靠后" in text
    assert "饭点" in text
    assert "同行人" in text


def test_system_prompt_has_three_activity_rationale_fewshot() -> None:
    """prompt 必须含专门示范"选择与顺序理由"的三活动 few-shot。"""
    assert "选择与顺序理由示范" in NARRATOR_SYSTEM_PROMPT
    assert "海洋馆" in NARRATOR_SYSTEM_PROMPT
    assert "垫肚子" in NARRATOR_SYSTEM_PROMPT
