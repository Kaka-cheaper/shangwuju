"""tests.test_assemble_fold —— 出发时刻倒推（I1「出门即行程」，ADR-0017，C2+候选A）。

【钉住的行为】`assemble_from_blueprint` 的两层机制：
1. 候选 A（`_find_backschedule_anchor`/`_backschedule_departure`，方案 1.3）：
   预演一次正向拼装，找时间序上第一个被吸附顶出正差额的餐厅节点作锚，从锚点
   反推 `preferred_start_time`，倒推不早于原下限时重跑一次正向 assemble——
   任意位置的锚点（含中段，如"先看展后吃饭"）都能把死等吸收进出发时刻。
2. `_fold_leading_wait`（候选 B，倒推后收尾兜底）：候选 A 在"锚点=首个 mid
   节点"时的退化子集，对残留的首段差额做最后一次折叠（19:00 出门罚站
   55 分钟 → 19:55 出门正好落座），total_minutes 如实缩小。

【测试矩阵】

```
| Test | 场景                                | 验证重点                             |
|------|-------------------------------------|--------------------------------------|
| F1   | 首站餐厅吸附出首段差额               | 折叠量精确 / n0+h0 同步移 / 活动节点不动 |
| F2   | 单轮收敛（第 9 轮探针的测试化）       | 折叠后出发时刻重跑 assemble → 同槽/gap=0/total 不再缩 |
| F3   | 幂等：折叠输出再过折叠 = no-op        | _fold_leading_wait 二次调用返 0       |
| F4   | total_minutes 与节点时刻代数一致      | total == last_node.start - nodes[0].start（critic 不覆盖的口播数字链专防）|
| F5   | 无首段差额（POI 首站）零扰动          | fold=0，行为与折叠前逐字节一致        |
| F6   | 中段 gap（候选 A 核心场景）           | 首站 POI + 次站餐厅吸附 → 倒推消除死等、看展时长不变 |
| F6b  | 边界：倒推早于下限（`_backschedule_departure` 纯函数单测） | 人为构造"锚点目标时刻"早于原下限 → 返回 None，维持原时刻 |
| F6c  | 无锚点（唯一槽早于自然到达，A8 同款） | 找不到锚点 → 出发时刻不倒推           |
| F6d  | 多锚点链路（已知范围限定）           | 只倒推第一个锚点，下游锚点自身 gap 不连带消除 |
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
    _backschedule_departure,
    _find_backschedule_anchor,
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
from schemas.itinerary import ActivityNode, Hop  # noqa: E402


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
# F0：任务书「情侣看展」病灶复现——看展 90min + 晚饭最早 17:00 槽
#     断言：无中段死等 / 出发后移 / 看展时长不变 / total 代数一致
# ============================================================


def test_F0_couple_exhibition_repro_no_mid_deadwait(profile, monkeypatch):
    """任务书病灶精确复现（情侣看展场景）：

    原病灶（候选 A 前）：
      14:08 出发 → 看展 UCCA 90min（14:15-15:45）→ 7min 通勤 → 15:45 到餐厅
      → 干等 75 分钟 → 17:00（CAPARESH 晚市最早可订槽）→ 用餐 100min
    75min 死等落在中段（第1站→第2站之间），C2 首段折叠不碰它。

    候选 A 正确态（倒推）：晚饭钉 17:00 → 回推通勤 7min → 看展 16:53 结束 →
    看展 90min → 15:23 开始 → 出门 15:15。整个下午后移 ~75min，死等消失，
    看展 90min 一分不少。

    本用例用 monkeypatch 精确复刻这条链路：
      home→UCCA 7min buffer0，看展 90min，UCCA→CAPARESH 7min buffer5，
      CAPARESH 晚市最早可订槽 17:00，用餐 100min。
    出发时刻声明 14:08（原病灶值）。
    """
    monkeypatch.setattr(
        _assemble_blueprint_mod,
        "load_restaurants",
        lambda: [_fake_restaurant("CAPARESH", [("16:30", False), ("17:00", True)])],
    )

    def _fake_lookup_hop(from_id, to_id, transport_pref, user_profile):
        table = {
            ("home", "UCCA"): (7, "taxi", "real_route"),
            ("UCCA", "CAPARESH"): (7, "taxi", "real_route"),
            ("CAPARESH", "home"): (7, "taxi", "real_route"),
        }
        return table[(from_id, to_id)]

    monkeypatch.setattr(_assemble_blueprint_mod, "lookup_hop", _fake_lookup_hop)

    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="UCCA",
                duration_min=90,
            ),
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="CAPARESH",
                duration_min=100,
            ),
        ],
        preferred_start_time="14:08",
        rationale="情侣看展病灶复现",
    )
    itin = assemble_from_blueprint(_intent("看展再吃饭"), bp, profile)

    # ① 出发后移：14:08 → 15:11（17:00 - [7(hop0)+0(buffer0) + 90(看展)
    #    + 7(hop1)+5(buffer1)] = 17:00 - 109min = 15:11）
    assert itin.nodes[0].start_time == "15:11"
    # ② 看展时长一分不少：仍是 90min
    assert itin.nodes[1].target_id == "UCCA"
    assert itin.nodes[1].duration_min == 90
    # ③ 无中段死等：餐厅自然到达恰好等于锚点槽 17:00，差额 0
    assert itin.nodes[2].target_id == "CAPARESH"
    assert itin.nodes[2].start_time == "17:00"
    natural = _parse_hhmm(itin.hops[1].start_time) + itin.hops[1].minutes + itin.hops[1].buffer_min
    assert _parse_hhmm(itin.nodes[2].start_time) - natural == 0, (
        "中段死等必须为 0——这是任务书要消灭的 75min 罚站"
    )
    # ④ total 代数一致：total == 尾 home.start - 首 home.start
    expected_total = _parse_hhmm(itin.nodes[-1].start_time) - _parse_hhmm(itin.nodes[0].start_time)
    assert itin.total_minutes == expected_total
    # total = 15:11 → 看展 90 → 17:00 用餐 100 → 18:40 → 返程 7 → 18:47 到家
    # = 18:47 - 15:11 = 216min（内容一分不少，只是整体后移，死等 0）
    assert itin.total_minutes == 216


# ============================================================
# F6：中段 gap——候选 A 落地后由「不折」改判「倒推消除」，
#     候选 A 全文含候选 B 为其在「锚点=首节点」情形下的退化子集
# ============================================================


def test_F6_mid_segment_gap_backscheduled_into_departure(profile, monkeypatch):
    """首站 POI（无差额）+ 次站餐厅吸附出中段等待 → 候选 A（方案 1.3）以
    餐厅节点为锚，把中段死等整体倒推进出发时刻——这正是任务书「情侣看展」
    病灶的最小复现：看展后到餐厅之间的死等不该留在中段，出发时刻本身后移。

    倒推前（候选 B 时代）的行为：出发不折，70min 中段死等留在餐厅节点前
    （见本文件历史版本）；候选 A 落地后本用例改判：出发后移 70min，死等清零。
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

    # 出发时刻倒推：13:00 → 14:10（R_TEST 锚点：15:30 - 5(hop1)-5(buffer)
    # - 60(P_TEST) - 10(hop0+buffer0，此处 buffer0=0，故 = hop0 分钟数 10) = 14:10）
    assert itin.nodes[0].start_time == "14:10"
    # 看展（P_TEST）时长一分不少：仍是 60min，只是整体后移
    assert itin.nodes[1].duration_min == 60
    # 中段死等清零：自然到达恰好等于锚点槽 15:30
    assert itin.nodes[2].start_time == "15:30"
    natural = _parse_hhmm(itin.hops[1].start_time) + itin.hops[1].minutes + itin.hops[1].buffer_min
    assert _parse_hhmm(itin.nodes[2].start_time) - natural == 0


def test_F6b_backschedule_blocked_by_lower_bound_leaves_legitimate_wait():
    """边界（方案 1.3 候选 A 必须处理）：倒推出的出发时刻早于用户可接受下限
    → 维持原时刻，返回 None（差额留作中段合法等待，讲得出因由）。

    【为什么直接单测 `_backschedule_departure`，不走端到端 assemble】代数
    证明（见该函数 docstring「实现纪律记录」）：`_find_backschedule_anchor`
    的锚点判据本身就要求 `anchor_target_min > natural_arrival_min ==
    original_start_min + span`，代入边界判断式恒得
    `new_start_min > original_start_min`——只要锚点来自同一次预演链路，
    这条边界在端到端场景下数学上不可达，无法用 assemble_from_blueprint
    端到端构造出真实触发它的输入。直接单测纯函数、人为传入一个与"同一条
    预演链路"不一致的 `anchor_target_min`（模拟"锚点来自外部声明的截止
    时刻，早于按当前链路自然推出的时刻"这一未来可能扩展的场景），验证
    边界判断本身的正确性——这是防御性代码，测试同样应该是防御性的。
    """
    # 构造 3 个 mid 节点：n0(home)-h0-n1(POI,30min)-h1-n2(POI,20min)-h2-n3(锚点餐厅)
    nodes = [
        ActivityNode(
            node_id="n0", kind="起点", target_kind="home", target_id="home",
            start_time="09:00", duration_min=0, title="出发",
        ),
        ActivityNode(
            node_id="n1", kind="主活动", target_kind="poi", target_id="P1",
            start_time="09:10", duration_min=30, title="P1",
        ),
        ActivityNode(
            node_id="n2", kind="主活动", target_kind="poi", target_id="P2",
            start_time="09:45", duration_min=20, title="P2",
        ),
        ActivityNode(
            node_id="n3", kind="用餐", target_kind="restaurant", target_id="R1",
            start_time="10:30", duration_min=45, title="R1",
        ),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="09:00", minutes=10, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="09:40", minutes=5, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h2", from_node_id="n2", to_node_id="n3", start_time="10:05", minutes=5, mode="taxi", path_type="real_route", buffer_min=5),
    ]
    # span(anchor_idx=3) = h0(10) + n1.duration(30) + h1(5) + n2.duration(20) + h2(5)+buffer(5) = 75
    # 人为给一个"早于同链路自然到达"的 anchor_target_min（模拟外部截止时刻场景）：
    # 若 anchor_target_min=09:30（090*60+30=570），new_start = 570-75=495=08:15，
    # 早于 original_start_min=09:00(540) → 应返回 None。
    result = _backschedule_departure(
        nodes, hops, anchor_idx=3,
        anchor_target_min=9 * 60 + 30,
        original_start_min=9 * 60,
    )
    assert result is None

    # 对照组：anchor_target_min 足够晚（10:30，与自然到达一致）→ 正常倒推
    result2 = _backschedule_departure(
        nodes, hops, anchor_idx=3,
        anchor_target_min=10 * 60 + 30,
        original_start_min=9 * 60,
    )
    assert result2 == 9 * 60 + 15  # 10:30 - 75min = 09:15


def test_F6c_no_anchor_when_no_restaurant_slot_actually_binds(profile, monkeypatch):
    """全程无餐厅节点被吸附顶出正差额（如唯一餐厅无可用槽，A8 同款情形）
    → 找不到锚点，_find_backschedule_anchor 返回 None，出发时刻不倒推。

    home→P_TEST 10min + P_TEST 60min + P_TEST→R_TEST 5min+buffer5 →
    自然到达 14:20；餐厅唯一槽 14:00 早于自然到达且无更晚槽——不吸附
    （A8 语义），此节点不构成"被顶出正差额"的锚点，倒推逻辑对它视而不见
    （正确：该店根本订不上，倒推也解决不了，真正问题该由 critic 的
    RESTAURANT_FULL_UNRESOLVED 接管）。
    """
    _patch_fixture(monkeypatch, slots=[("14:00", True)])
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
        rationale="无可用槽→无锚点",
    )
    itin = assemble_from_blueprint(_intent("下午逛逛再吃饭"), bp, profile)

    # 出发时刻不倒推（无锚点）
    assert itin.nodes[0].start_time == "13:00"
    # 自然到达原样保留（不吸附，诚实反映订不上），不是任何"被顶出的等待"
    assert itin.nodes[2].start_time == "14:20"


def test_F6d_multi_restaurant_chain_only_first_anchor_backscheduled(profile, monkeypatch):
    """已知范围限定（见 `_find_backschedule_anchor` docstring）：任务书明确
    是"时间序上**第一个**硬约束节点"——晚饭+夜宵两顿都被吸附出正差额时，
    只有晚饭（第一个锚点）驱动倒推；夜宵自己的 gap 不因此连带清零（它的
    自然到达只取决于晚饭的**绝对**结束时刻，与出发时刻前移量无关）。

    R1（晚饭）：home→R1 10min buffer0，30min 用餐，槽 13:40（自然到达
    13:00+10=13:10，差额 30min）。
    R2（夜宵）：R1→R2 5min buffer5，45min 用餐，槽 15:20（R1 结束 13:40+30=
    14:10，自然到达 14:10+10=14:20，差额 60min）。

    倒推只用 R1 做锚：13:40-10=13:30（出发从 13:00→13:30，+30min）；重跑后
    R1 差额清零，但 R2 差额原样保留 60min（R2 自然到达 14:20 不变，因为
    R1 的实际到达时刻 13:40 本身没变——只是"从哪个出发时刻走到 13:40"变了）。
    """
    monkeypatch.setattr(
        _assemble_blueprint_mod,
        "load_restaurants",
        lambda: [
            _fake_restaurant("R1", [("13:40", True)]),
            _fake_restaurant("R2", [("15:20", True)]),
        ],
    )

    def _fake_lookup_hop(from_id, to_id, transport_pref, user_profile):
        table = {
            ("home", "R1"): (10, "taxi", "real_route"),
            ("R1", "R2"): (5, "taxi", "real_route"),
            ("R2", "home"): (10, "taxi", "real_route"),
        }
        return table[(from_id, to_id)]

    monkeypatch.setattr(_assemble_blueprint_mod, "lookup_hop", _fake_lookup_hop)

    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐", target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R1", duration_min=30,
            ),
            BlueprintNode(
                kind="夜宵", target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R2", duration_min=45,
            ),
        ],
        preferred_start_time="13:00",
        rationale="多锚点范围限定用例",
    )
    itin = assemble_from_blueprint(_intent("先吃饭再吃夜宵"), bp, profile)

    # 出发时刻只按 R1（第一个锚点）倒推：13:00 → 13:30
    assert itin.nodes[0].start_time == "13:30"
    # R1 差额清零：自然到达恰好等于槽 13:40
    assert itin.nodes[1].start_time == "13:40"
    natural_r1 = (
        _parse_hhmm(itin.hops[0].start_time) + itin.hops[0].minutes + itin.hops[0].buffer_min
    )
    assert _parse_hhmm(itin.nodes[1].start_time) - natural_r1 == 0
    # R2 差额原样保留 60min（已知范围限定：不连带消除下游锚点自己的 gap）
    assert itin.nodes[2].start_time == "15:20"
    natural_r2 = (
        _parse_hhmm(itin.hops[1].start_time) + itin.hops[1].minutes + itin.hops[1].buffer_min
    )
    assert _parse_hhmm(itin.nodes[2].start_time) - natural_r2 == 60


def test_F6e_not_before_start_pinned_node_excluded_from_backschedule(profile, monkeypatch):
    """范围限定（方案 1.11-b「两套槽机制不合并」的直接推论）：ILS/rule 路径
    钉进 `not_before_start` 的餐厅节点不被候选 A 当成倒推锚点——那条时间线
    已经是 `route_scheduler` 算好的、必要最小 slack 的独立最优解，候选 A
    只认 LLM 路径自己的 `_earliest_available_slot_min` 吸附产生的差额。

    【为什么把餐厅放成非首节点】若餐厅是首个 mid 节点，即便锚点扫描正确
    跳过它，它顶出的仍是**首段**差额，会被 `_fold_leading_wait`（C2 候选 B，
    既有行为、不看 not_before_start）折进出发时刻——那是 C2 的领地、与本
    用例要隔离验证的"锚点扫描排除"无关。故构造成 POI→钉窗餐厅：餐厅顶出
    的是**中段**差额，只有候选 A 的锚点倒推会碰它；验证 not_before_start
    钉窗节点被排除后，中段差额原样保留、出发时刻不倒推。

    构造：P_TEST（首站，无差额）→ R_TEST（次站，not_before_start="15:30"
    模拟 ILS 钉窗）。P_TEST 自然到达 13:10、停 60min→14:10，hop 5min+buffer5
    →自然到达 14:20；钉窗顶到 15:30（中段差额 70min）。因 not_before_start
    已设置，`_find_backschedule_anchor` 跳过它 → 出发不倒推、中段差额保留。
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
                not_before_start="15:30",  # 模拟 ILS/rule 路径钉窗
            ),
        ],
        preferred_start_time="13:00",
        rationale="ILS 钉窗排除用例",
    )
    itin = assemble_from_blueprint(_intent("下午逛逛再吃饭"), bp, profile)

    # 出发时刻不倒推（not_before_start 已设置的节点被排除在锚点扫描之外）
    assert itin.nodes[0].start_time == "13:00"
    # not_before_start 机制本身仍生效：餐厅钉在 15:30（既有行为，候选 A 不干预），
    # 中段差额 70min 原样保留（未被倒推消除——这正是"不碰 ILS 钉窗"的体现）
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
