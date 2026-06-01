"""tests.test_narrator_full_nodes —— spec narration-and-intent-fidelity R1。

背景（真 LLM 实测踩的 bug）：行程实际「活动→用餐→活动」3 段，但 narration 讲到
吃饭就收尾，漏掉餐后活动（S3 漏探索乐园 / S5 漏电影院）。根因：
1. narrator_prompt 字数硬上限 50-80 + few-shot 全是餐尾收尾
2. _template_narration 有 phrases[:3] 截断

本测试验证：
- 模板路径（确定性）：3 节点行程复述全部 3 个地点（含餐后活动）
- prompt（关键词断言）：含「餐后」「必须讲」类活动完整性规则 + 三节点 few-shot
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

from agent.intent.narrator import _template_narration  # noqa: E402
from agent.intent.prompts.narrator_prompt import NARRATOR_SYSTEM_PROMPT  # noqa: E402
from schemas.intent import IntentExtraction  # noqa: E402
from schemas.itinerary import ActivityNode, Hop, Itinerary  # noqa: E402


def _make_three_node_itinerary() -> Itinerary:
    """构造「活动→用餐→活动」三活动行程（餐在中间，餐后还有电影院）。"""
    nodes = [
        ActivityNode(node_id="n_home_s", kind="出发", target_kind="home", target_id="home", start_time="14:00", duration_min=0, title="家"),
        ActivityNode(node_id="n_1", kind="主活动", target_kind="poi", target_id="P022", start_time="14:15", duration_min=90, title="毛球先生猫咖"),
        ActivityNode(node_id="n_2", kind="用餐", target_kind="restaurant", target_id="R001", start_time="16:30", duration_min=80, title="鹿园甜品"),
        ActivityNode(node_id="n_3", kind="主活动", target_kind="poi", target_id="P028", start_time="18:30", duration_min=120, title="万达 IMAX 电影院"),
        ActivityNode(node_id="n_home_e", kind="回家", target_kind="home", target_id="home", start_time="20:40", duration_min=0, title="家"),
    ]
    hops = [
        Hop(hop_id="h_0", from_node_id="n_home_s", to_node_id="n_1", start_time="14:00", minutes=15, mode="taxi", path_type="estimated"),
        Hop(hop_id="h_1", from_node_id="n_1", to_node_id="n_2", start_time="15:45", minutes=10, mode="taxi", path_type="estimated"),
        Hop(hop_id="h_2", from_node_id="n_2", to_node_id="n_3", start_time="17:50", minutes=10, mode="taxi", path_type="estimated"),
        Hop(hop_id="h_3", from_node_id="n_3", to_node_id="n_home_e", start_time="20:30", minutes=10, mode="taxi", path_type="estimated"),
    ]
    return Itinerary(
        schema_version="edge_v1",
        summary="情侣看展看电影",
        nodes=nodes,
        hops=hops,
        total_minutes=300,
    )


def _make_intent() -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[4, 6],
        distance_max_km=5,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="情侣亲密",
        preferred_poi_types=[],
        raw_input="和女朋友出去玩",
        parse_confidence=0.85,
    )


def test_template_narration_includes_all_three_activities() -> None:
    """模板路径：3 活动行程必须复述全部 3 个地点（含餐后的电影院，不许在用餐处截断）。"""
    text = _template_narration(_make_intent(), _make_three_node_itinerary(), "stream")
    assert "毛球先生猫咖" in text, f"漏讲第 1 活动：{text}"
    assert "鹿园甜品" in text, f"漏讲用餐节点：{text}"
    assert "万达 IMAX 电影院" in text or "电影院" in text, f"漏讲餐后活动（核心 bug）：{text}"


def test_template_narration_no_longer_truncates_to_three() -> None:
    """回归断言：旧 phrases[:3] 截断会砍掉第 4 个短语；去截断后餐后活动出现。"""
    text = _template_narration(_make_intent(), _make_three_node_itinerary(), "stream")
    # 三个活动 + 首尾 home，phrases 至少 4-5 条；电影院在末尾，旧截断会丢
    assert "电影" in text


def test_prompt_has_activity_completeness_rule() -> None:
    """prompt 必须含「活动完整性规则」+ 餐后活动必须讲的硬规则。"""
    assert "活动完整性规则" in NARRATOR_SYSTEM_PROMPT
    assert "餐后" in NARRATOR_SYSTEM_PROMPT
    assert "有几个活动就讲几个" in NARRATOR_SYSTEM_PROMPT


def test_prompt_has_three_node_fewshot() -> None:
    """prompt 必须含三活动·餐在中间的 few-shot 范例（防 LLM 学会餐尾收尾）。"""
    assert "餐后还有活动" in NARRATOR_SYSTEM_PROMPT
    assert "电影" in NARRATOR_SYSTEM_PROMPT


def test_prompt_elastic_word_limit() -> None:
    """字数上限改为按活动数弹性（不再是死的 50-80）。"""
    assert "弹性" in NARRATOR_SYSTEM_PROMPT
    assert "120 字" in NARRATOR_SYSTEM_PROMPT
