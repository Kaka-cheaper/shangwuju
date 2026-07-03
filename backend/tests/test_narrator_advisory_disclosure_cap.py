"""tests.test_narrator_advisory_disclosure_cap —— ADR-0014 决策 2（G-2）告知限额。

覆盖 `agent.intent.narrator._apply_advisory_disclosure_cap`：
1. ≤2 条 advisory 原样返回（不折叠）
2. >2 条 advisory → 只保留前 2 条 + 1 条折叠句（"还有 N 处小取舍"措辞，
   不含歉意用语）
3. 端到端：`generate_narration`（模板路径）真的把 3 条以上 advisory 限到
   2 条 + 折叠句，不是只在单元函数层面生效
"""

from __future__ import annotations

from agent.intent.narrator import (
    _apply_advisory_disclosure_cap,
    generate_narration,
)
from schemas.intent import IntentExtraction
from schemas.itinerary import ActivityNode, Hop, Itinerary


def test_cap_noop_when_at_or_below_limit():
    assert _apply_advisory_disclosure_cap([]) == []
    assert _apply_advisory_disclosure_cap(["a"]) == ["a"]
    assert _apply_advisory_disclosure_cap(["a", "b"]) == ["a", "b"]


def test_cap_folds_remainder_with_confident_not_apologetic_wording():
    advisories = ["第一条", "第二条", "第三条", "第四条"]
    capped = _apply_advisory_disclosure_cap(advisories)

    assert capped[:2] == ["第一条", "第二条"]
    assert len(capped) == 3
    folded = capped[2]
    assert "2" in folded  # 4 条 - 2 条呈现 = 2 处折叠
    # 自信取舍措辞，不道歉
    for apologetic in ("抱歉", "对不起", "不好意思", "sorry"):
        assert apologetic not in folded


def _minimal_itinerary() -> Itinerary:
    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home", start_time="14:00", duration_min=0, title="出发"),
        ActivityNode(node_id="n1", kind="主活动", target_kind="poi", target_id="P040", start_time="14:09", duration_min=120, title="P040"),
        ActivityNode(node_id="n2", kind="终点", target_kind="home", target_id="home", start_time="16:09", duration_min=0, title="回家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="14:00", minutes=9, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="16:09", minutes=9, mode="taxi", path_type="real_route", buffer_min=0),
    ]
    return Itinerary(summary="测试方案", nodes=nodes, hops=hops, total_minutes=138)


def test_generate_narration_template_path_applies_disclosure_cap():
    """模板路径（use_llm=False）端到端验证限额生效——3 条 advisory 只显 2 条
    原文 + 1 句折叠，不是全量堆进 narration。
    """
    intent = IntentExtraction(
        start_time="2026-07-02T14:00",
        duration_hours=[2, 3],
        distance_max_km=10.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="测试",
        parse_confidence=0.9,
    )
    itinerary = _minimal_itinerary()
    advisories = ["点名的『甲』这次塞不进去了。", "『乙』超出预算一些。", "『丙』这次没能安排上。"]

    text = generate_narration(
        intent=intent, itinerary=itinerary, use_llm=False, advisories=advisories
    )

    assert "甲" in text
    assert "乙" in text
    assert "丙" not in text  # 第 3 条被折叠，不再逐字出现
    assert "还有 1 处" in text or "1 处" in text
