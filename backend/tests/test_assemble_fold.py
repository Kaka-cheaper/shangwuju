"""tests.test_assemble_fold —— 首段等待折叠（I1「出门即行程」，ADR-0017，C2）。

【钉住的行为】`assemble_from_blueprint` 尾部后处理 `_fold_leading_wait`：
首站吸附/钉窗挤出的"出门后在店门口罚站"差额 g，被整体后移 nodes[0]/hops[0]
的出发时刻吸收（19:00 出门罚站 55 分钟 → 19:55 出门正好落座），
total_minutes 如实缩小 g。

【测试矩阵】

```
| Test | 场景                                | 验证重点                             |
|------|-------------------------------------|--------------------------------------|
| F1   | 首站餐厅吸附出首段差额               | 折叠量精确 / n0+h0 同步移 / 活动节点不动 |
| F2   | 单轮收敛（第 9 轮探针的测试化）       | 折叠后出发时刻重跑 assemble → 同槽/gap=0/total 不再缩 |
| F3   | 幂等：折叠输出再过折叠 = no-op        | _fold_leading_wait 二次调用返 0       |
| F4   | total_minutes 与节点时刻代数一致      | total == last_node.start - nodes[0].start（critic 不覆盖的口播数字链专防）|
| F5   | 无首段差额（POI 首站）零扰动          | fold=0，行为与折叠前逐字节一致        |
| F6   | 中段 gap 不折                        | 首站 POI + 次站餐厅吸附 → 出发时刻不动 |
| F7   | finalize_plan fold 可观测性          | trace rationale 追加一句 / ≥90min 写 quality_issues |
| F8   | finalize_plan 无 blueprint/无 trace  | 不追加、不崩（ILS/rule 路径防御边界） |
```

fixture 全部走 monkeypatch 定死通勤与餐厅槽（与 test_assemble_blueprint.py
A7/A8 同款），不依赖 mock 数据真值，专注折叠算法本身。
"""

from __future__ import annotations

import sys
import types

import pytest

# 桥接：绕过 agent/__init__.py eager-import（与 test_assemble_blueprint.py 同款）
if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    from pathlib import Path as _Path

    _agent_dir = _Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.planning.commute import lookup_hop as _lookup_hop_mod  # noqa: E402
from agent.planning.blueprint import assemble_blueprint as _assemble_blueprint_mod  # noqa: E402
from agent.planning.blueprint.assemble_blueprint import (  # noqa: E402
    _fold_leading_wait,
    _parse_hhmm,
    assemble_from_blueprint,
)
from agent.planning.blueprint.blueprint import (  # noqa: E402
    BlueprintNode,
    BlueprintTargetKind,
    PlanBlueprint,
)
from data.loader import load_user_profile  # noqa: E402
from schemas.domain import Location, ReservationSlot, Restaurant, RestaurantCapacity  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_lookup_cache():
    _lookup_hop_mod.reset_cache()
    yield
    _lookup_hop_mod.reset_cache()


@pytest.fixture
def profile():
    return load_user_profile()


def _intent(raw: str = "晚上出去吃一顿") -> IntentExtraction:
    return IntentExtraction(
        start_time="today_evening",
        duration_hours=[2, 3],
        distance_max_km=5,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input=raw,
        parse_confidence=0.9,
    )


def _fake_restaurant(rest_id: str, slots: list[tuple[str, bool]]) -> Restaurant:
    return Restaurant(
        id=rest_id,
        name="测试餐厅",
        cuisine="测试菜系",
        location=Location(name="测试地址", lat=30.28, lng=120.10),
        distance_km=1.0,
        opening_hours="00:00-24:00",
        avg_price=100.0,
        rating=4.5,
        typical_dining_min=60,
        capacity=RestaurantCapacity(),
        reservation_slots=[ReservationSlot(time=t, available=a) for t, a in slots],
        tags=[],
        suitable_for=[],
    )


def _patch_fixture(monkeypatch, *, slots: list[tuple[str, bool]]) -> None:
    """首站餐厅场景：home→R_TEST 5min、R_TEST→home 5min，槽位由参数给。"""
    monkeypatch.setattr(
        _assemble_blueprint_mod,
        "load_restaurants",
        lambda: [_fake_restaurant("R_TEST", slots)],
    )

    def _fake_lookup_hop(from_id, to_id, transport_pref, user_profile):
        table = {
            ("home", "R_TEST"): (5, "taxi", "real_route"),
            ("R_TEST", "home"): (5, "taxi", "real_route"),
            ("home", "P_TEST"): (10, "taxi", "real_route"),
            ("P_TEST", "R_TEST"): (5, "taxi", "real_route"),
            ("R_TEST", "P_TEST"): (5, "taxi", "real_route"),
            ("P_TEST", "home"): (10, "taxi", "real_route"),
        }
        return table[(from_id, to_id)]

    monkeypatch.setattr(_assemble_blueprint_mod, "lookup_hop", _fake_lookup_hop)


def _restaurant_first_bp(start: str = "19:00") -> PlanBlueprint:
    return PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R_TEST",
                duration_min=90,
            ),
        ],
        preferred_start_time=start,
        rationale="折叠回归用例",
    )


# ============================================================
# F1：首站餐厅吸附出首段差额 → 折叠量精确
# ============================================================


def test_F1_fold_absorbs_leading_wait(profile, monkeypatch):
    """19:00 出发 + 5min 通勤 → 19:05 自然到达；19:00 槽已过、19:30 可订
    → 吸附 19:30，首段差额 25 分钟 → 折叠后 19:25 出门、19:30 落座。
    """
    _patch_fixture(monkeypatch, slots=[("19:00", False), ("19:30", True)])
    itin = assemble_from_blueprint(_intent(), _restaurant_first_bp("19:00"), profile)

    # 出发时刻被折叠到 19:25（19:30 - 5min 通勤）
    assert itin.nodes[0].start_time == "19:25", (
        f"出发时刻应折叠为 19:25，实际 {itin.nodes[0].start_time}"
    )
    assert itin.hops[0].start_time == "19:25"
    # 活动节点一个都不动
    assert itin.nodes[1].start_time == "19:30"
    assert itin.nodes[1].duration_min == 90
    # 精确对齐：h0_end + buffer(0) == n1.start（等号成立，无残留 gap）
    assert (
        _parse_hhmm(itin.hops[0].start_time) + itin.hops[0].minutes
        + itin.hops[0].buffer_min
        == _parse_hhmm(itin.nodes[1].start_time)
    )
    # total = 19:25 → 21:00(用餐毕) + 5min = 21:05 到家 → 100 分钟（比不折少 25）
    assert itin.total_minutes == 100
    # schedule 派生自折叠后时刻（首条 n0 entry 与 h0 entry 同步）
    assert itin.schedule[0].start == "19:25"
    assert itin.schedule[1].start == "19:25"


# ============================================================
# F2：单轮收敛（第 9 轮探针的测试化：同槽 / gap 归零 / total 恰缩）
# ============================================================


def test_F2_single_round_convergence(profile, monkeypatch):
    """收敛断言：用折叠后的出发时刻重跑一遍 assemble，吸附必选同一个槽、
    首段 gap 为 0、total 不再缩——证明折叠不振荡、一轮收敛。
    """
    _patch_fixture(monkeypatch, slots=[("19:00", False), ("19:30", True)])
    first = assemble_from_blueprint(_intent(), _restaurant_first_bp("19:00"), profile)

    # 用折叠后的出发时刻当新的 preferred_start_time 重跑
    second = assemble_from_blueprint(
        _intent(), _restaurant_first_bp(first.nodes[0].start_time), profile
    )

    # 同槽：两轮的餐厅落座时刻一致
    assert second.nodes[1].start_time == first.nodes[1].start_time == "19:30"
    # gap 归零：第二轮出发时刻不再被折叠（19:25 出发自然到达 19:30 == 槽）
    assert second.nodes[0].start_time == first.nodes[0].start_time == "19:25"
    # total 恰好不再缩
    assert second.total_minutes == first.total_minutes == 100


# ============================================================
# F3：幂等——折叠输出再过折叠 = no-op
# ============================================================


def test_F3_fold_idempotent_on_folded_output(profile, monkeypatch):
    """对已折叠行程的 nodes/hops 再调一次 _fold_leading_wait → 返 0 且零改写。"""
    _patch_fixture(monkeypatch, slots=[("19:00", False), ("19:30", True)])
    itin = assemble_from_blueprint(_intent(), _restaurant_first_bp("19:00"), profile)

    before = [(n.node_id, n.start_time) for n in itin.nodes] + [
        (h.hop_id, h.start_time) for h in itin.hops
    ]
    g2 = _fold_leading_wait(itin.nodes, itin.hops)
    after = [(n.node_id, n.start_time) for n in itin.nodes] + [
        (h.hop_id, h.start_time) for h in itin.hops
    ]

    assert g2 == 0
    assert before == after


# ============================================================
# F4：total_minutes 与节点时刻代数一致（口播数字链专防，方案 1.27-②）
# ============================================================


@pytest.mark.parametrize(
    "slots,start",
    [
        ([("19:00", False), ("19:30", True)], "19:00"),   # 有折叠
        ([("19:05", True)], "19:00"),                     # 恰好无差额（到达即落座）
        ([("18:00", True), ("20:00", True)], "19:00"),    # 大差额（55min）
    ],
)
def test_F4_total_minutes_algebraic_consistency(profile, monkeypatch, slots, start):
    """total_minutes == parse(尾 home.start) - parse(首 home.start)——critic 的
    check_temporal_alignment 不校验 total_minutes 字段本身，这条代数断言是
    "narrator 口播小时数"数字链的唯一防线。
    """
    _patch_fixture(monkeypatch, slots=slots)
    itin = assemble_from_blueprint(_intent(), _restaurant_first_bp(start), profile)

    expected = _parse_hhmm(itin.nodes[-1].start_time) - _parse_hhmm(
        itin.nodes[0].start_time
    )
    assert itin.total_minutes == expected


# ============================================================
# F5：无首段差额（POI 首站）→ 零扰动
# ============================================================


def test_F5_no_fold_when_first_stop_is_poi(profile, monkeypatch):
    """首站 POI 无吸附 → 首段差额 0 → 出发时刻保持 blueprint 声明值。"""
    _patch_fixture(monkeypatch, slots=[("19:30", True)])
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P_TEST",
                duration_min=60,
            ),
        ],
        preferred_start_time="14:00",
        rationale="无差额用例",
    )
    itin = assemble_from_blueprint(_intent("下午出门转转"), bp, profile)

    assert itin.nodes[0].start_time == "14:00"
    assert itin.hops[0].start_time == "14:00"
    assert itin.nodes[1].start_time == "14:10"  # 14:00 + 10min 通勤 + buffer 0
    assert itin.total_minutes == 80  # 10 + 60 + 10


# ============================================================
# F6：中段 gap 不折（那是完整倒推批的领域）
# ============================================================


def test_F6_mid_segment_gap_not_folded(profile, monkeypatch):
    """首站 POI（无差额）+ 次站餐厅吸附出中段等待 → 出发时刻纹丝不动，
    中段差额原样保留（可能有正当因由，折叠只管首段）。
    """
    _patch_fixture(monkeypatch, slots=[("15:30", True)])
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P_TEST",
                duration_min=60,
            ),
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R_TEST",
                duration_min=45,
            ),
        ],
        preferred_start_time="13:00",
        rationale="中段 gap 用例",
    )
    itin = assemble_from_blueprint(_intent("下午逛逛再吃饭"), bp, profile)

    # 出发不折
    assert itin.nodes[0].start_time == "13:00"
    # 首站 13:10-14:10；13:10+60=14:10 → hop 5min + buffer 5 → 自然到达 14:20
    # → 吸附 15:30，中段等待 70 分钟保留在餐厅节点前
    assert itin.nodes[2].start_time == "15:30"
    natural = _parse_hhmm(itin.hops[1].start_time) + itin.hops[1].minutes + itin.hops[1].buffer_min
    assert _parse_hhmm(itin.nodes[2].start_time) - natural == 70


# ============================================================
# F7：finalize_plan fold 可观测性（trace rationale + quality_issues）
# ============================================================


def _trace():
    from schemas.decision_trace import DecisionTrace

    return DecisionTrace(
        blueprint_rationale="选点理由",
        weights_explanation="",
        critic_attempts=[],
        fallback_chain=[],
        final_strategy="llm_first",
    )


def test_F7_finalize_plan_appends_fold_note_to_trace(profile, monkeypatch):
    """LLM 路径（trace 存在 + state.blueprint 在场）：折叠量 >0 → trace 的
    blueprint_rationale 追加折叠说明（含原/折叠双时刻），量 <90min 不写
    quality_issues。
    """
    from agent.graph.nodes.finalize_plan import finalize_plan_node

    _patch_fixture(monkeypatch, slots=[("19:00", False), ("19:30", True)])
    bp = _restaurant_first_bp("19:00")
    itin = assemble_from_blueprint(_intent(), bp, profile)  # 折 25min
    itin = itin.model_copy(update={"decision_trace": _trace()})

    out = finalize_plan_node(
        {"intent": _intent(), "itinerary": itin, "blueprint": bp}
    )
    rationale = out["itinerary"].decision_trace.blueprint_rationale
    assert "折叠 25 分钟" in rationale
    assert "19:00" in rationale and "19:25" in rationale
    assert "quality_issues" not in out  # 25 < 90，不触发质量信号


def test_F7b_finalize_plan_writes_quality_issue_when_fold_huge(profile, monkeypatch):
    """折叠量 ≥90min（拍板项 P1 阈值）→ 追加 quality_issues（"规划没填满时段"
    信号），narrator 消费后可暴露给用户。
    """
    from agent.graph.nodes.finalize_plan import finalize_plan_node

    # 14:00 出发 + 5min 通勤 → 14:05 自然到达；唯一可订槽 17:30 → 折 205min
    _patch_fixture(monkeypatch, slots=[("17:30", True)])
    bp = _restaurant_first_bp("14:00")
    itin = assemble_from_blueprint(_intent(), bp, profile)
    assert itin.nodes[0].start_time == "17:25"
    itin = itin.model_copy(update={"decision_trace": _trace()})

    out = finalize_plan_node(
        {"intent": _intent(), "itinerary": itin, "blueprint": bp}
    )
    assert "quality_issues" in out
    assert any("205 分钟" in q for q in out["quality_issues"])
    # 既有 quality_issues 不被覆盖（合并语义）
    out2 = finalize_plan_node(
        {
            "intent": _intent(),
            "itinerary": itin,
            "blueprint": bp,
            "quality_issues": ["已有信号"],
        }
    )
    assert out2["quality_issues"][0] == "已有信号"
    assert len(out2["quality_issues"]) == 2


def test_F8_finalize_plan_fold_note_defensive_boundaries(profile, monkeypatch):
    """防御边界：① trace 不存在（ILS/rule 路径）→ 不追加、不崩；
    ② trace 在但 state.blueprint 缺失 → rationale 原样。"""
    from agent.graph.nodes.finalize_plan import finalize_plan_node

    _patch_fixture(monkeypatch, slots=[("19:00", False), ("19:30", True)])
    bp = _restaurant_first_bp("19:00")
    itin = assemble_from_blueprint(_intent(), bp, profile)

    # ① trace None（ILS 路径形态）——不崩即过
    out1 = finalize_plan_node({"intent": _intent(), "itinerary": itin})
    assert out1["itinerary"].decision_trace is None

    # ② trace 在、blueprint 缺 → rationale 不追加折叠句
    itin2 = itin.model_copy(update={"decision_trace": _trace()})
    out2 = finalize_plan_node({"intent": _intent(), "itinerary": itin2})
    assert out2["itinerary"].decision_trace.blueprint_rationale == "选点理由"
