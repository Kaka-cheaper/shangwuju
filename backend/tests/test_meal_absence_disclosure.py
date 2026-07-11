"""tests.test_meal_absence_disclosure —— 饭缺席发声（C6，I4 缺席必须发声）。

【钉住的行为】
1. `meal_windows.crossed_meal_window` 完整区间重叠谓词：
   overlap = min(end, w_end) - max(start, w_start) >= 45min；多窗报时间序最早；
   夜宵仅整窗落夜宵时段才参与（拍板项 P4）。S1/S3/S6 三场景数值核算
   （方案 1.27-④ 的误报排除表转成断言）。
2. `meal_absence.build_meal_absence_signal` 三分叉互斥：单一分叉点
   （explicit_dining_requested）单一产出点——True 无饭→MEAL_REQUESTED_UNSEATED
   （试了没排上+出路）/ None 跨窗无饭→MEAL_OMITTED_BY_DESIGN（默认吃过来+
   想加跟我说）/ False→无码轻确认句；方案有餐厅节点→全静默。两码结构上
   禁共现。
3. narrate_node 集成：advisory 进口播 honest 段 + SSE advisories；轻确认只进
   口播不进结构化条目；cap 优先序在组装处排（显式失败>常识缺席>一般放宽，
   cap 函数本体不动——方案 1.34-W2）。
4. ILS 路径 `_build_success_advisories` 同步（同一实现 import，不复制判定）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.planning.critic.meal_absence import (  # noqa: E402
    MEAL_ABSENCE_LIGHT_CONFIRM,
    build_meal_absence_signal,
)
from agent.planning.critic.meal_windows import (  # noqa: E402
    MEAL_ABSENCE_MIN_OVERLAP_MIN,
    crossed_meal_window,
)
from schemas.advisory import AdvisoryCode  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402


def _intent(**overrides) -> IntentExtraction:
    kw = dict(
        start_time="today_evening",
        duration_hours=[3, 4],
        distance_max_km=5.0,
        companions=[Companion(role="室友", count=3)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="朋友热闹",
        raw_input="周五晚上去 K 歌",
        parse_confidence=0.9,
    )
    kw.update(overrides)
    return IntentExtraction(**kw)


def _min(h: int, m: int = 0) -> int:
    return h * 60 + m


# ============================================================
# 1. 谓词窗口数值核算（S1/S3/S6 + 夜宵 + 阈值边界）
# ============================================================


def test_predicate_S1_evening_ktv_reports_dinner_not_supper():
    """S1 原型（红队修订 A 的靶）：19:00 出发 + 3h → [19:00, 22:00]。
    晚饭窗重叠 60min 达标 → 报"晚饭"；夜宵窗因出行窗起点 <21:00 不参与
    （旧收窄谓词在这里要么哑火、要么报错饭名报成夜宵）。"""
    assert crossed_meal_window(_min(19), _min(22)) == "晚饭"


def test_predicate_S3_family_afternoon_crosses_dinner_head():
    """S3 数值例：14:00-18:00 家庭局——跨晚饭窗头 60min，达标（触发即该说，
    非误报：有饭的方案根本不会走到谓词这一步）。"""
    assert crossed_meal_window(_min(14), _min(18)) == "晚饭"


def test_predicate_S6_afternoon_tea_no_meal_window():
    """S6 数值例：14:00-17:00 下午局——午饭窗已过、晚饭窗重叠 0、夜宵不参与
    → None（安全，不误报）。"""
    assert crossed_meal_window(_min(14), _min(17)) is None


def test_predicate_supper_only_when_whole_window_in_supper():
    """夜宵窗规则（P4）：整个出行窗落夜宵时段（起点 ≥21:00）才点破。"""
    # 21:30 出发 + 3h → [21:30, 24:30]：晚饭窗重叠 <0，夜宵重叠 180 → 夜宵
    assert crossed_meal_window(_min(21, 30), _min(24, 30)) == "夜宵"
    # 20:00 出发 + 3h → [20:00, 23:00]：起点 <21:00 夜宵不参与；晚饭窗重叠 0 → None
    assert crossed_meal_window(_min(20), _min(23)) is None


def test_predicate_lunch_earliest_wins_and_threshold_boundary():
    """多窗报时间序最早（午饭优先于晚饭）；45min 阈值边界（44 不达标 45 达标）。"""
    # 11:00-20:00 全跨午/晚 → 报最早的午饭
    assert crossed_meal_window(_min(11), _min(20)) == "午饭"
    # 晚饭窗重叠恰 45：[16:00, 17:45]
    assert crossed_meal_window(_min(16), _min(17, 45)) == "晚饭"
    # 晚饭窗重叠 44：[16:00, 17:44]
    assert crossed_meal_window(_min(16), _min(17, 44)) is None
    assert MEAL_ABSENCE_MIN_OVERLAP_MIN == 45
    # 退化区间防御
    assert crossed_meal_window(_min(19), _min(19)) is None


# ============================================================
# 2. 三分叉互斥（fixture：POI-only 行程，实际窗跨晚饭窗）
# ============================================================


@pytest.fixture
def poi_only_crossing_dinner(monkeypatch):
    """15:00 出发的 POI-only 行程，实际窗 [15:00, ~17:48] 跨晚饭窗 ≥45min。"""
    from agent.planning.blueprint import assemble_blueprint as _ab_mod
    from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
    from agent.planning.blueprint.blueprint import (
        BlueprintNode,
        BlueprintTargetKind,
        PlanBlueprint,
    )
    from data.loader import load_user_profile

    def _fake_lookup_hop(from_id, to_id, transport_pref, user_profile):
        return (10, "taxi", "real_route")

    monkeypatch.setattr(_ab_mod, "lookup_hop", _fake_lookup_hop)
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=150,
            ),
        ],
        preferred_start_time="15:00",
        rationale="POI-only 跨晚饭窗用例",
    )
    # 15:00 → 15:10 到 → 17:40 结束 → 17:50 到家；窗 [15:00,17:50] 晚饭重叠 50min
    return assemble_from_blueprint(_intent(), bp, load_user_profile())


def test_fork_true_unseated(poi_only_crossing_dinner):
    advisory, confirm = build_meal_absence_signal(
        _intent(explicit_dining_requested=True), poi_only_crossing_dinner
    )
    assert confirm is None
    assert advisory is not None
    assert advisory.code == AdvisoryCode.MEAL_REQUESTED_UNSEATED
    assert "没排上" in advisory.message
    assert "默认" not in advisory.message, "显式失败绝不能说'默认你吃过来'"
    # 出路（诚实带出路）
    assert "再试" in advisory.message or "告诉我" in advisory.message


def test_fork_none_omitted_by_design_names_the_meal(poi_only_crossing_dinner):
    advisory, confirm = build_meal_absence_signal(
        _intent(explicit_dining_requested=None), poi_only_crossing_dinner
    )
    assert confirm is None
    assert advisory is not None
    assert advisory.code == AdvisoryCode.MEAL_OMITTED_BY_DESIGN
    assert "晚饭" in advisory.message, "必须点破缺的是哪一顿"
    assert "跟我说" in advisory.message, "必须给出路"


def test_fork_false_light_confirm(poi_only_crossing_dinner):
    advisory, confirm = build_meal_absence_signal(
        _intent(explicit_dining_requested=False), poi_only_crossing_dinner
    )
    assert advisory is None
    assert confirm == MEAL_ABSENCE_LIGHT_CONFIRM


def test_fork_mutual_exclusion_structural(poi_only_crossing_dinner):
    """互斥：三态各自至多产出一路信号，两码禁共现（结构性保证的行为面钉子）。"""
    for value in (True, None, False):
        advisory, confirm = build_meal_absence_signal(
            _intent(explicit_dining_requested=value), poi_only_crossing_dinner
        )
        assert not (advisory is not None and confirm is not None)


def test_fork_silent_when_restaurant_present(monkeypatch):
    """方案里有餐厅节点 → 三态全静默（无缺席可言）。"""
    from agent.planning.blueprint import assemble_blueprint as _ab_mod
    from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
    from agent.planning.blueprint.blueprint import (
        BlueprintNode,
        BlueprintTargetKind,
        PlanBlueprint,
    )
    from data.loader import load_user_profile

    monkeypatch.setattr(
        _ab_mod, "lookup_hop", lambda *a, **k: (10, "taxi", "real_route")
    )
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
        preferred_start_time="17:30",
        rationale="有饭方案",
    )
    itin = assemble_from_blueprint(_intent(), bp, load_user_profile())
    for value in (True, None, False):
        advisory, confirm = build_meal_absence_signal(
            _intent(explicit_dining_requested=value), itin
        )
        assert advisory is None and confirm is None, f"value={value}"


def test_fork_none_without_window_crossing_stays_silent(monkeypatch):
    """None 态 + 实际窗不跨任何饭点窗（S6 形态）→ 沉默（不该说的不说）。"""
    from agent.planning.blueprint import assemble_blueprint as _ab_mod
    from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
    from agent.planning.blueprint.blueprint import (
        BlueprintNode,
        BlueprintTargetKind,
        PlanBlueprint,
    )
    from data.loader import load_user_profile

    monkeypatch.setattr(
        _ab_mod, "lookup_hop", lambda *a, **k: (10, "taxi", "real_route")
    )
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=120,
            ),
        ],
        preferred_start_time="14:00",
        rationale="下午局不跨窗",
    )
    # 14:00 → 14:10 到 → 16:10 结束 → 16:20 到家；晚饭窗重叠 <0
    itin = assemble_from_blueprint(_intent(), bp, load_user_profile())
    advisory, confirm = build_meal_absence_signal(_intent(), itin)
    assert advisory is None and confirm is None


# ============================================================
# 3. narrate_node 集成 + cap 优先序
# ============================================================


def test_narrate_node_speaks_omission(poi_only_crossing_dinner):
    from agent.graph.nodes.narrate import narrate_node

    state = {
        "intent": _intent(),
        "itinerary": poi_only_crossing_dinner,
        "user_id": "demo_user",
    }
    out = narrate_node(state)
    assert "没有给你排晚饭" in out["narration"]
    codes = [str(getattr(a.get("code"), "value", a.get("code"))) for a in out["advisories"]]
    assert "meal_omitted_by_design" in codes


def test_narrate_node_speaks_unseated_and_dedupes_dining_unmet(poi_only_crossing_dinner):
    from agent.graph.nodes.narrate import narrate_node

    state = {
        "intent": _intent(
            explicit_dining_requested=True,
            preferred_poi_types=["烧烤"],
            raw_input="想吃烧烤，顺便逛逛",
        ),
        "itinerary": poi_only_crossing_dinner,
        "user_id": "demo_user",
    }
    out = narrate_node(state)
    assert "没排上" in out["narration"]
    # "同一顿饭只道歉一次"：unmet_cuisines 的"烧烤没找到"不再重复出现
    assert "烧烤" not in (out["narration"] or "") or out["narration"].count("没排上") == 1


def test_narrate_node_light_confirm_not_in_structured_advisories(poi_only_crossing_dinner):
    """False 态：轻确认句进口播、不进结构化 advisories（方案 1.31 呈现面表）。"""
    from agent.graph.nodes.narrate import narrate_node

    state = {
        "intent": _intent(explicit_dining_requested=False),
        "itinerary": poi_only_crossing_dinner,
        "user_id": "demo_user",
    }
    out = narrate_node(state)
    assert "按你说的，这次没排饭" in out["narration"]
    codes = [str(getattr(a.get("code"), "value", a.get("code"))) for a in out["advisories"]]
    assert "meal_omitted_by_design" not in codes
    assert "meal_requested_unseated" not in codes


def test_disclosure_priority_sort_order():
    """cap 优先序（1.34-W2）：显式失败 > 常识缺席 > 一般放宽；同级保序稳定。"""
    from agent.graph.nodes.narrate import _sort_advisories_for_disclosure

    advisories = [
        {"code": "shorter_than_requested", "message": "短了一些"},
        {"code": "constraint_relaxed", "message": "放宽了一条"},
        {"code": "meal_omitted_by_design", "message": "没排饭"},
        {"code": "meal_requested_unseated", "message": "试了没排上"},
    ]
    out = _sort_advisories_for_disclosure(advisories)
    codes = [a["code"] for a in out]
    assert codes == [
        "meal_requested_unseated",
        "meal_omitted_by_design",
        "shorter_than_requested",
        "constraint_relaxed",
    ]
    # 枚举成员形状同样可排（model_dump 的 python 模式产出枚举成员）
    out2 = _sort_advisories_for_disclosure(
        [
            {"code": AdvisoryCode.SHORTER_THAN_REQUESTED, "message": "a"},
            {"code": AdvisoryCode.MEAL_REQUESTED_UNSEATED, "message": "b"},
        ]
    )
    assert out2[0]["message"] == "b"


# ============================================================
# 4. ILS 路径同步
# ============================================================


def test_ils_build_success_advisories_carries_meal_absence(poi_only_crossing_dinner):
    from agent.planning.planners.ils_planner import _build_success_advisories

    advisories = _build_success_advisories(
        pin_advisories=[],
        unmet_pinned=[],
        dropped_pins=set(),
        pinned_by_key={},
        violations=[],
        current_scheduled=[],
        money_budget=0,
        intent=_intent(),
        itinerary=poi_only_crossing_dinner,
    )
    codes = [a.code for a in advisories]
    assert AdvisoryCode.MEAL_OMITTED_BY_DESIGN in codes


def test_ils_build_success_advisories_backward_compatible_without_new_args():
    """不传 intent/itinerary（存量直调形状）→ 行为与 C6 之前一致，不崩不产新码。"""
    from agent.planning.planners.ils_planner import _build_success_advisories

    advisories = _build_success_advisories(
        pin_advisories=[],
        unmet_pinned=[],
        dropped_pins=set(),
        pinned_by_key={},
        violations=[],
        current_scheduled=[],
        money_budget=0,
    )
    assert advisories == []
