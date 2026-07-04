"""tests.test_swap_narration_wording —— 换菜告知文案：降级(SWAP_DEGRADED)时
合并成一句诚实告知，不再"换菜确认 + 最接近告知"两个模板串联。

背景（真 LLM 点火 G1 探针实锤，路演 H1 主演示线 step3 必现）：

    按你的要求，把「夜烤场·精致烧烤」换成了「兰州牛肉面·老味道」，不辣。
    没找到完全符合你要求的，给你换了个最接近的——「兰州牛肉面·老味道」，
    先将就一下，不满意再告诉我？

问题拆解：
1. 店名重复两遍——`_build_success_narration`（换菜确认句）与 node_swap 的
   SWAP_DEGRADED advisory（最接近告知句）被 `compose_narration_text` 首尾
   串联，各自把新店名说了一遍。
2. tier 3 降级时确认句"按你的要求…{descriptor}。"是**假话**——降级恰恰意味着
   没找到满足 descriptor 的候选，不能宣称"按你的要求"已达成。
3. "先将就一下"泄气（advisory 原句在 node_swap.py，本批不动那个文件；出口
   拼装处绕开它的原文，用合并句替代）。

修法（拼装处收口，单人/房间共用）：SWAP_DEGRADED 在场时，主句换成一句合并的
诚实告知（"你要的「X」没找到完全符合的，把「A」换成了最接近的「B」，不满意
再告诉我。"），并把该 advisory 从尾拼列表里摘除（内容已并入主句）；其余
advisory（如 CONSTRAINT_RELAXED）照旧尾拼。完全命中（tier 1/2、具名备选）
路径的确认句一字不动（G2 实录「已经按你选的…」是好的）。
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

from api._streams.graph_adjust import (  # noqa: E402
    _build_success_narration,
    build_degraded_swap_narration,
    compose_swap_success_narration,
    split_swap_degraded_advisory,
)
from api._streams.models import (  # noqa: E402
    AdjustActionAdjust,
    AdjustActionAlternative,
)
from schemas.advisory import AdvisoryCode  # noqa: E402
from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension  # noqa: E402

_OLD = "夜烤场·精致烧烤"
_NEW = "兰州牛肉面·老味道"

# node_swap._swap_degraded_advisory 的现行原句（本批不改 planning/，出口绕行）
_DEGRADED_MSG = (
    f"没找到完全符合你要求的，给你换了个最接近的——『{_NEW}』，"
    "先将就一下，不满意再告诉我？"
)


def _degraded_advisory_dict() -> dict:
    return {"code": AdvisoryCode.SWAP_DEGRADED.value, "message": _DEGRADED_MSG}


def _adjust_action(value: str = "不辣") -> AdjustActionAdjust:
    return AdjustActionAdjust(
        type="adjust",
        adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.DIETARY, value=value),
        label=f"{value}的",
    )


# ============================================================
# 1. G1 实锤回归：降级换菜 → 一句合并诚实告知，店名只出现一次
# ============================================================


def test_degraded_swap_merges_into_single_honest_sentence() -> None:
    action = _adjust_action("不辣")
    base = _build_success_narration(action, _OLD, _NEW)
    text = compose_swap_success_narration(
        base,
        [_degraded_advisory_dict()],
        old_title=_OLD,
        new_title=_NEW,
        descriptor="不辣",
    )
    assert text.count(_NEW) == 1, f"新店名不应重复出现：{text}"
    assert "先将就一下" not in text, f"泄气措辞应消失：{text}"
    assert "没找到完全符合" in text and "最接近" in text, f"诚实告知要素缺失：{text}"
    assert "不辣" in text, f"约束回声应保留：{text}"
    assert _OLD in text, f"换掉的是哪一站应可见：{text}"
    assert "按你的要求" not in text, f"降级时不能宣称『按你的要求』已达成：{text}"


def test_degraded_swap_keeps_other_advisories_appended() -> None:
    relaxed = {
        "code": AdvisoryCode.CONSTRAINT_RELAXED.value,
        "message": "「安静聊天」这回在新换的这一站没对上，你留意一下。",
    }
    action = _adjust_action("不辣")
    base = _build_success_narration(action, _OLD, _NEW)
    text = compose_swap_success_narration(
        base,
        [_degraded_advisory_dict(), relaxed],
        old_title=_OLD,
        new_title=_NEW,
        descriptor="不辣",
    )
    assert relaxed["message"] in text, "非降级类 advisory 仍应尾拼"
    assert text.count(_NEW) == 1


# ============================================================
# 2. 完全命中路径的确认句一字不动（G2 实录是好的）
# ============================================================


def test_full_hit_alternative_confirmation_unchanged() -> None:
    action = AdjustActionAlternative(type="alternative", target_id="P9")
    base = _build_success_narration(action, "腾跃蹦床公园", "Vertical 攀岩馆")
    text = compose_swap_success_narration(
        base, [], old_title="腾跃蹦床公园", new_title="Vertical 攀岩馆"
    )
    assert text == "已经按你选的，把「腾跃蹦床公园」换成了「Vertical 攀岩馆」。"


def test_full_hit_adjust_confirmation_unchanged() -> None:
    action = _adjust_action("不辣")
    base = _build_success_narration(action, _OLD, _NEW)
    text = compose_swap_success_narration(base, [], old_title=_OLD, new_title=_NEW)
    assert text == f"按你的要求，把「{_OLD}」换成了「{_NEW}」，不辣。"


# ============================================================
# 3. 组件级：拆分器 + 房间归名版合并句
# ============================================================


def test_split_swap_degraded_advisory_extracts_only_degraded() -> None:
    degraded = _degraded_advisory_dict()
    other = {"code": AdvisoryCode.CONSTRAINT_RELAXED.value, "message": "x"}
    got, rest = split_swap_degraded_advisory([other, degraded])
    assert got is degraded
    assert rest == [other]

    got_none, rest_all = split_swap_degraded_advisory([other])
    assert got_none is None
    assert rest_all == [other]


def test_degraded_narration_room_variant_keeps_attribution() -> None:
    text = build_degraded_swap_narration(_OLD, _NEW, descriptor="不辣", requester="小李")
    assert text.startswith("小李要的「不辣」"), f"房间版必须归名：{text}"
    assert text.count(_NEW) == 1
    assert "先将就一下" not in text


def test_degraded_narration_without_descriptor_still_honest() -> None:
    """防御分支：无 descriptor（理论上 SWAP_DEGRADED 只在定向调整出现，但拼装
    处不该在意外形状下拼出假话）。"""
    text = build_degraded_swap_narration(_OLD, _NEW)
    assert "没找到完全符合" in text and "最接近" in text
    assert text.count(_NEW) == 1
