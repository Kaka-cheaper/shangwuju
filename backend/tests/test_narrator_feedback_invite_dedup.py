"""tests.test_narrator_feedback_invite_dedup —— 收尾邀请反馈去重（文案修缮批
建议修 1，真 LLM 点火 A8/A6/C2 实锤）。

实锤（A6）：

    ……我猜可能是想要那种亲密氛围，闺蜜局其实也很合适，不合适随时跟我说。
    哪里不合适跟我说一声。

正文里已有邀请反馈语（出处告知的"不合适可以跟我说"、未满足告知的"不满意我
再换"），结尾又固定追加"哪里不合适跟我说一声。"——同一个意思背靠背说两遍。

两条路径都有病灶，都修：
- 模板路径（本文件断言）：`_template_narration` 结尾句是代码拼的——正文已含
  邀请反馈语则不拼（"先修代码侧"，任务拍板）。
- LLM 路径：结尾句是 prompt 少样本/风格规范带出来的——加"邀请反馈只说一次"
  去重规则（本文件断言 prompt 锚点，真实效果由 ≤8 次真 LLM 抽查另行验证）。
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

_ENDING = "哪里不合适跟我说一声。"


def _make_itinerary() -> Itinerary:
    nodes = [
        ActivityNode(node_id="n0", kind="出发", target_kind="home", target_id="home", start_time="14:00", duration_min=0, title="家"),
        ActivityNode(node_id="n1", kind="主活动", target_kind="poi", target_id="P022", start_time="14:15", duration_min=90, title="毛球先生猫咖"),
        ActivityNode(node_id="n2", kind="回家", target_kind="home", target_id="home", start_time="16:00", duration_min=0, title="家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="14:00", minutes=15, mode="taxi", path_type="estimated"),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="15:45", minutes=15, mode="taxi", path_type="estimated"),
    ]
    return Itinerary(schema_version="edge_v1", summary="下午安排", nodes=nodes, hops=hops, total_minutes=120)


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
# 1. 模板路径：正文已含邀请反馈语 → 不再拼固定收尾句
# ============================================================


def test_ending_suppressed_when_provenance_clause_already_invites() -> None:
    """出处告知句自带"不合适可以跟我说"→ 收尾句不再背靠背追加（A6 同款）。"""
    intent = _base_intent(
        experience_tags=["亲密情侣"],
        field_provenance={"experience_tags:亲密情侣": "inferred"},
    )
    text = _template_narration(intent, _make_itinerary(), "stream")
    assert "跟我说" in text, f"邀请反馈语本身应保留：{text}"
    assert _ENDING not in text, f"正文已有邀请，固定收尾不应再拼：{text}"


def test_ending_suppressed_when_unmet_clause_already_invites() -> None:
    """未满足告知句自带"不满意我再换"→ 收尾句不再追加。"""
    intent = _base_intent(preferred_poi_types=["烧烤"])
    text = _template_narration(intent, _make_itinerary(), "stream", None, ["烧烤"])
    assert "不满意我再换" in text
    assert _ENDING not in text, f"正文已有邀请，固定收尾不应再拼：{text}"


def test_ending_kept_when_no_invite_in_body() -> None:
    """正文没有任何邀请反馈语 → 收尾句照拼（回归保护，别把邀请弄丢）。"""
    intent = _base_intent()
    text = _template_narration(intent, _make_itinerary(), "stream")
    assert text.endswith(_ENDING), f"无邀请时收尾句应保留：{text}"


def test_confirm_ending_untouched() -> None:
    """confirm 阶段收尾是安抚句不是邀请句，不参与去重逻辑。"""
    intent = _base_intent(
        experience_tags=["亲密情侣"],
        field_provenance={"experience_tags:亲密情侣": "inferred"},
    )
    text = _template_narration(intent, _make_itinerary(), "confirm")
    assert text.endswith("都给你搞定了，可以放心出门了。")


# ============================================================
# 2. LLM 路径：prompt 里要有"邀请反馈只说一次"的去重规则
# ============================================================


def test_system_prompt_has_invite_once_rule() -> None:
    assert "邀请反馈只说一次" in NARRATOR_SYSTEM_PROMPT, (
        "narrator prompt 应含收尾邀请去重规则（真 LLM 叙事正文普遍已带"
        "『不合适可以跟我说』，结尾不该再固定追加一句）"
    )
