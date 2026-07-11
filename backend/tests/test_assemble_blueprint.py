"""tests.test_assemble_blueprint —— 蓝图→Itinerary 拼装（edge_v1）。

assemble_from_blueprint(intent, blueprint, user_profile) 把 LLM 出的
PlanBlueprint（仅 mid nodes + preferred_start_time）拼装为合法 Itinerary
（含首尾 home + 自动 hops + schedule 派生视图）。

【测试矩阵】

```
| Test | 场景                      | 验证重点                                           |
|------|--------------------------|----------------------------------------------------|
| A1   | 标准 2 段（POI + 餐厅）   | 4 nodes / 3 hops / 时间游标自洽 / 首跳 buffer=0     |
| A2   | 单段（仅餐厅）            | 3 nodes / 2 hops / total_minutes 等于段+两次通勤    |
| A3   | 同地复用（连续两段同 POI） | 中间 hop minutes=0 mode=virtual path_type=in_place |
| A4   | 反序（餐厅 → POI）         | 顺序保留 / 不强制 POI 在前                          |
```

每个测试都跑公共不变量 `_assert_invariants`：

1. `len(hops) == len(nodes) - 1`
2. 首尾 home（target_kind="home" / target_id="home" / duration_min=0）
3. `total_minutes == _parse(last_node.start) - _parse(first_hop.start)`
4. 每条 hop 的 `start_time + minutes ≤ to_node.start_time`（含 buffer 容差）
5. 每条 hop 的 `start_time == from_node.start_time + from_node.duration_min`
6. schedule 长度 = nodes + hops 总数

【过渡态桥】

`agent/__init__.py` 仍 eager-import `planner.py`（依赖旧 ItineraryStage，Task 9 修），
但 `agent.assemble_blueprint / agent.blueprint / agent.lookup_hop` 自身不依赖
兄弟模块。下面把 `agent` 注册为空命名空间包，让 from-import 跳过 __init__.py
副作用——参考 tests/test_lookup_hop.py 同款套路。
"""

from __future__ import annotations

import sys
import types

import pytest

# ============================================================
# 桥接：绕过 agent/__init__.py eager-import 旧 ItineraryStage
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    from pathlib import Path as _Path

    _agent_dir = _Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]  # 让 Python 把它当包，子模块按文件解析
    sys.modules["agent"] = _stub

# 同步重置 lookup_hop 缓存（避免被其它测试 monkeypatch 残留污染）
from agent.planning.commute import lookup_hop as _lookup_hop_mod  # noqa: E402
from agent.planning.blueprint import assemble_blueprint as _assemble_blueprint_mod  # noqa: E402
from agent.planning.blueprint.assemble_blueprint import (  # noqa: E402
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
from schemas.itinerary import Itinerary  # noqa: E402


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(autouse=True)
def _reset_lookup_cache():
    """每个测试前后清空 lookup_hop 模块级 lru_cache。"""
    _lookup_hop_mod.reset_cache()
    yield
    _lookup_hop_mod.reset_cache()


@pytest.fixture
def profile():
    """demo_user 画像，含 home_location 坐标 + transport_preference=taxi。"""
    return load_user_profile()


def _intent(duration: tuple[int, int] = (3, 5), raw: str = "下午带娃出门") -> IntentExtraction:
    """构造极简意图（assemble 当前不读它，仅作签名占位）。"""
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=list(duration),
        distance_max_km=5,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input=raw,
        parse_confidence=0.9,
    )


# ============================================================
# 公共不变量断言（所有测试复用）
# ============================================================


def _assert_invariants(itin: Itinerary, blueprint: PlanBlueprint) -> None:
    """edge_v1 八条不变量。"""
    nodes = itin.nodes
    hops = itin.hops

    # I1：hops 长度 = nodes - 1
    assert len(hops) == len(nodes) - 1, (
        f"hops 长度 {len(hops)} ≠ nodes-1 = {len(nodes) - 1}"
    )

    # I2：首尾 home（target_kind / target_id / duration_min）
    assert nodes[0].target_kind == "home"
    assert nodes[0].target_id == "home"
    assert nodes[0].duration_min == 0
    assert nodes[-1].target_kind == "home"
    assert nodes[-1].target_id == "home"
    assert nodes[-1].duration_min == 0

    # I3：mid nodes 数量 = blueprint mid nodes 数量
    assert len(nodes) - 2 == len(blueprint.nodes)

    # I4：schedule 长度 = nodes + hops
    assert len(itin.schedule) == len(nodes) + len(hops)

    # I5：每条 hop 的 from/to 引用链（with index）
    for i, hop in enumerate(hops):
        assert hop.from_node_id == nodes[i].node_id, (
            f"hops[{i}].from_node_id={hop.from_node_id} 应等于 nodes[{i}].node_id={nodes[i].node_id}"
        )
        assert hop.to_node_id == nodes[i + 1].node_id, (
            f"hops[{i}].to_node_id={hop.to_node_id} 应等于 nodes[{i + 1}].node_id={nodes[i + 1].node_id}"
        )

    # I6：hop.start_time == from_node 的结束时刻（from_start + from_duration）
    for i, hop in enumerate(hops):
        from_node = nodes[i]
        expected_hop_start = _parse_hhmm(from_node.start_time) + from_node.duration_min
        assert _parse_hhmm(hop.start_time) == expected_hop_start, (
            f"hops[{i}].start_time={hop.start_time} 应等于 from_node {from_node.node_id} "
            f"的结束时刻 {from_node.start_time}+{from_node.duration_min}min"
        )

    # I7：to_node.start_time >= hop.start + hop.minutes + hop.buffer_min
    # （真因修复批 item 1 有意识放宽：曾是严格 == ，隐含"到达即入座、零等待"假设。
    # 但 not_before_start 钉窗（ADR-0009 决策 2·乙）与本批新增的餐厅槽吸附
    # 都会合法地把 to_node 排得比"hop 结束+buffer"更晚——多出的分钟是餐前
    # 等待 slack，前端渲染为"自由休息"块，不是 bug。critic 侧
    # check_temporal_alignment 用的就是同一条 `>=` 判据（见 assemble_blueprint.py
    # 对应注释"to_start ≥ hop_end + buffer 仍通过"），本不变量与其对齐，
    # 不再对齐一个从未被系统正式承诺过的"零 slack"假设。）
    for i, hop in enumerate(hops):
        to_node = nodes[i + 1]
        expected_to_start = (
            _parse_hhmm(hop.start_time) + hop.minutes + hop.buffer_min
        )
        assert _parse_hhmm(to_node.start_time) >= expected_to_start, (
            f"nodes[{i + 1}].start_time={to_node.start_time} 应不早于 "
            f"hop {hop.hop_id}({hop.start_time}+{hop.minutes}min+buf{hop.buffer_min})"
        )

    # I8：total_minutes = last_node.start_time - first_hop.start_time
    expected_total = (
        _parse_hhmm(nodes[-1].start_time) - _parse_hhmm(hops[0].start_time)
    )
    assert itin.total_minutes == expected_total, (
        f"total_minutes={itin.total_minutes} 应等于 "
        f"last_node({nodes[-1].start_time}) - first_hop({hops[0].start_time}) = {expected_total}"
    )


# ============================================================
# A1：标准 2 段（POI + 餐厅）
# ============================================================


def test_A1_standard_two_segment(profile):
    """家庭半日：POI P040（亲子博物馆，165min）+ 餐厅 R001（轻食，60min）。

    routes.json 已知边（taxi）：
      home → P040 = 9min（real_route）
      P040 → R001 = 5min（real_route）
      R001 → home = 7min（real_route）

    R001.reservation_slots 含 17:00(满)/17:30(可订)/18:00(可订)——蓝图声明
    14:00 出发时自然到达 17:04，被吸附到 17:30（真因修复批 item 1）；候选 A
    出发时刻倒推（方案 1.3）以 R001 为锚点，把这段 26 分钟的"看展后干等"
    整体倒推进出发时刻——19:00 出发变 19:26 出发的同款模式，此处是
    14:00→14:26：

      n0(home, 14:26, 0min)
        h0: 14:26 → 14:35, 9min, taxi, real_route, buffer=0
      n1(P040, 14:35, 165min) → 17:20
        h1: 17:20 → 17:25, 5min, taxi, real_route, buffer=5
      n2(R001, 17:30, 60min) → 18:30 —— 自然到达 17:30 恰好等于槽，无残留等待
        h2: 18:30 → 18:37, 7min, taxi, real_route, buffer=0
      n3(home, 18:37, 0min)
      total = 18:37 - 14:26 = 251min（比未倒推少 26min，与消掉的中段死等等量）
    """
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=165,
            ),
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
        preferred_start_time="14:00",
        rationale="家庭半日：博物馆+轻食",
    )
    itin = assemble_from_blueprint(_intent((4, 5)), bp, profile)

    # 节点结构
    assert len(itin.nodes) == 4
    assert len(itin.hops) == 3
    assert [n.target_id for n in itin.nodes] == ["home", "P040", "R001", "home"]
    assert [n.kind for n in itin.nodes] == ["起点", "主活动", "用餐", "终点"]

    # 首跳 buffer=0；非首跳 buffer=5；返程 buffer=0
    assert itin.hops[0].buffer_min == 0
    assert itin.hops[1].buffer_min == 5
    assert itin.hops[2].buffer_min == 0

    # 出发时刻已被候选 A 倒推：14:00 → 14:26（R001 锚点，见类文档字符串）
    assert itin.hops[0].start_time == "14:26"
    assert itin.hops[0].minutes == 9
    assert itin.hops[0].mode == "taxi"
    assert itin.hops[0].path_type == "real_route"
    assert itin.nodes[1].start_time == "14:35"

    # 倒推后中段死等清零：自然到达恰好等于锚点槽（17:30），无残留 gap
    assert itin.nodes[2].start_time == "17:30"
    natural_arrival = (
        _parse_hhmm(itin.hops[1].start_time) + itin.hops[1].minutes + itin.hops[1].buffer_min
    )
    assert _parse_hhmm(itin.nodes[2].start_time) - natural_arrival == 0

    # 公共不变量
    _assert_invariants(itin, bp)


def test_A1_actual_timing_walkthrough(profile):
    """A1 同结构，逐字段验证时间游标的精确推进（含候选 A 出发时刻倒推）。

    未倒推的预演（内核 `_assemble_forward`，仅供推导倒推量，非最终断言）从
    14:00 开始：
      h0 home→P040 = 9min, buffer=0  → 14:00→14:09 → P040 start 14:09
      n1 P040 165min                 → 14:09→16:54
      h1 P040→R001 = 5min, buffer=5  → 16:54→16:59 → 自然到达 17:04
      n2 R001 60min —— 真因修复批 item 1（槽吸附）：R001.reservation_slots=
         [17:00(满)/17:30(可订)/18:00(可订)]，自然到达 17:04 对不上任何真实槽，
         吸附到"不早于到达的最早可用槽" 17:30——预演阶段产生 26min 中段死等。

    候选 A（方案 1.3，backward scheduling from anchor）：R001 是时间序上第一个
    被吸附顶出正差额的餐厅节点，以它为锚反推 preferred_start_time：
    17:30 - (5+5)min(hop1+buffer) - 165min(P040) - (9+0)min(hop0+buffer)
    = 14:26——晚于原下限 14:00，倒推生效，重跑正向 assemble：

      n0(home, 14:26, 0min)
        h0 home→P040 = 9min, buffer=0  → 14:26→14:35 → P040 start 14:35
      n1 P040 165min                 → 14:35→17:20
        h1 P040→R001 = 5min, buffer=5  → 17:20→17:25 → 自然到达 17:30
      n2 R001 60min —— 自然到达恰好 == 锚点槽 17:30，无残留中段等待
                                     → 17:30→18:30
        h2 R001→home = 7min, buffer=0  → 18:30→18:37 → home start 18:37
      total = 18:37 - 14:26 = 251min（比预演少 26min，即消掉的中段死等）

    （倒推前旧断言是 14:00/14:09/17:30/18:37/277——candidate A 落地后，这条
    正是任务书「情侣看展」病灶的最小复现：看展后到餐厅之间的死等被整体
    倒推进出发时刻，看展时长 165min 一分不少，仅仅整体后移。）
    """
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=165,
            ),
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
        preferred_start_time="14:00",
        rationale="精确时间轴验证",
    )
    itin = assemble_from_blueprint(_intent((4, 5)), bp, profile)

    # 完整精确断言（候选 A 倒推后）
    assert itin.nodes[0].start_time == "14:26"
    assert itin.nodes[1].start_time == "14:35"
    assert itin.nodes[2].start_time == "17:30"
    assert itin.nodes[3].start_time == "18:37"

    assert itin.hops[0].start_time == "14:26" and itin.hops[0].minutes == 9
    assert itin.hops[1].start_time == "17:20" and itin.hops[1].minutes == 5
    assert itin.hops[2].start_time == "18:30" and itin.hops[2].minutes == 7

    # 看展时长一分不少：n1.duration_min 恒 165（倒推只挪位置，不改时长）
    assert itin.nodes[1].duration_min == 165
    assert itin.total_minutes == 251

    # 标题
    assert itin.nodes[0].title in {"出发", profile.home_location.name}
    assert "无障碍亲子博物馆" in itin.nodes[1].title
    assert "轻语沙拉" in itin.nodes[2].title
    assert itin.nodes[3].title in {"回家", profile.home_location.name}

    # schema_version
    assert itin.schema_version == "edge_v1"

    _assert_invariants(itin, bp)


# ============================================================
# A2：单段（仅餐厅）
# ============================================================


def test_A2_single_node_dining_only(profile):
    """单段方案：只想吃饭。

    routes.json 中 home → R001 没有正向边（仅有 R001 → home），
    所以 lookup_hop("home", "R001", "taxi", ...) 走 3 级 haversine：
      home (30.275, 120.075) → R001 (30.273, 120.080)
      ≈ 0.5km × 1.3 / 25kmh × 60 ≈ 1.6min → max(1, 2) ≈ 2min（haversine_estimated）

    所以：
      h0 home→R001 ≈ 估算 min, mode="haversine_estimated"
      n1 R001 60min
      h1 R001→home = 7min real_route taxi
    """
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
        preferred_start_time="12:00",
        rationale="单段去吃午餐",
    )
    itin = assemble_from_blueprint(_intent((1, 2), raw="只想吃饭"), bp, profile)

    # 结构
    assert len(itin.nodes) == 3
    assert len(itin.hops) == 2
    assert [n.target_id for n in itin.nodes] == ["home", "R001", "home"]
    assert itin.nodes[1].kind == "用餐"

    # 返程 hop 必须命中 routes 真值
    assert itin.hops[1].minutes == 7
    assert itin.hops[1].mode == "taxi"
    assert itin.hops[1].path_type == "real_route"

    # 首跳因 routes 无正向边，走 haversine
    assert itin.hops[0].mode == "haversine_estimated"
    assert itin.hops[0].path_type == "estimated"

    # buffer 规则：首跳 0，返程 0
    assert itin.hops[0].buffer_min == 0
    assert itin.hops[1].buffer_min == 0

    _assert_invariants(itin, bp)


# ============================================================
# A3：同地复用（连续两段同 POI）
# ============================================================


def test_A3_in_place_reuse_same_poi(profile):
    """同地复用：在 P040 先看展（90min）再休息（60min）。

    连续两个 BlueprintNode 都指向 P040 → lookup_hop 走 1 级 in_place：
      h0 home→P040 = 9min real_route taxi, buffer=0
      n1 P040 90min
      h1 P040→P040 = 0min virtual in_place, buffer=5
      n2 P040 60min（同 target_id, 不同 kind）
      h2 P040→home = 9min real_route taxi, buffer=0

    schedule 中 in_place hop 的 hidden=True（不渲染）。
    """
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=90,
            ),
            BlueprintNode(
                kind="自由",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",  # 同一 target_id → 触发 in_place
                duration_min=60,
            ),
        ],
        preferred_start_time="14:00",
        rationale="先看展再休息，都在博物馆里",
    )
    itin = assemble_from_blueprint(_intent((3, 4)), bp, profile)

    # 结构
    assert len(itin.nodes) == 4
    assert len(itin.hops) == 3
    assert [n.target_id for n in itin.nodes] == ["home", "P040", "P040", "home"]

    # 中间 hop 必须是 in_place（minutes=0 / virtual / in_place）
    middle = itin.hops[1]
    assert middle.minutes == 0
    assert middle.mode == "virtual"
    assert middle.path_type == "in_place"
    assert middle.from_node_id == "n1"
    assert middle.to_node_id == "n2"

    # in_place hop 在 schedule 中应被标 hidden=True
    schedule_hop_entries = [
        e for e in itin.schedule if e.entry_kind == "hop"
    ]
    assert len(schedule_hop_entries) == 3
    middle_entry = next(e for e in schedule_hop_entries if e.ref_id == middle.hop_id)
    assert middle_entry.hidden is True

    _assert_invariants(itin, bp)


# ============================================================
# A4：反序（餐厅 → POI，先吃后逛）
# ============================================================


def test_A4_reverse_order_restaurant_then_poi(profile):
    """先吃后逛：用餐 R001 在主活动 P040 之前。

    顺序保留 LLM 原意，不强制 POI → Restaurant。
    """
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=90,
            ),
        ],
        preferred_start_time="11:30",
        rationale="先吃午餐再去博物馆",
    )
    itin = assemble_from_blueprint(_intent((4, 5)), bp, profile)

    # 顺序保留
    assert [n.target_id for n in itin.nodes] == ["home", "R001", "P040", "home"]
    assert [n.kind for n in itin.nodes] == ["起点", "用餐", "主活动", "终点"]

    # 中段 hop R001 → P040：routes.json 里 P040→R001 有真值，但反向 R001→P040 没有，
    # 所以走 haversine（双端坐标都在）
    middle_hop = itin.hops[1]
    assert middle_hop.from_node_id == "n1"
    assert middle_hop.to_node_id == "n2"
    assert middle_hop.path_type in {"real_route", "estimated"}  # 实际是 estimated
    assert middle_hop.buffer_min == 5

    _assert_invariants(itin, bp)


# ============================================================
# 边角：transport_preference 默认与覆盖
# ============================================================


def test_A5_walking_preference_picks_walking_route(profile):
    """user_profile.transport_preference="walking" 时 hop.mode 应是 walking。"""
    profile_walk = profile.model_copy(update={"transport_preference": "walking"})
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=60,
            ),
        ],
        preferred_start_time="14:00",
        rationale="walk it",
    )
    itin = assemble_from_blueprint(_intent((2, 3)), bp, profile_walk)

    # routes.json: home→P040 walking_minutes=36
    assert itin.hops[0].mode == "walking"
    assert itin.hops[0].minutes == 36
    _assert_invariants(itin, bp)


# ============================================================
# 不变量负向：手工触发 RuntimeError（白盒）
# ============================================================


def test_A6_assemble_returns_valid_pydantic_object(profile):
    """assemble 输出能通过 Pydantic 二次校验（schemas/itinerary.py model_validator）。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=90,
            ),
        ],
        preferred_start_time="14:00",
        rationale="ok",
    )
    itin = assemble_from_blueprint(_intent((2, 3)), bp, profile)

    # 重新走 model_validate 等价于 Pydantic 完整校验
    Itinerary.model_validate(itin.model_dump())


# ============================================================
# A7：餐厅预约槽吸附（真因修复批 item 1）
# ============================================================


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
        reservation_slots=[
            ReservationSlot(time=t, available=a) for t, a in slots
        ],
        tags=[],
        suitable_for=[],
    )


def test_A7_restaurant_slot_snap_and_cascade(profile, monkeypatch):
    """真因修复批 item 1：LLM 路径餐厅节点自然到达对不上真实预约槽的必然错位——
    叠加候选 A 出发时刻倒推（方案 1.3）后，中段死等被整体倒推进出发时刻。

    未倒推的预演（内核 `_assemble_forward`）："到达 15:17，最近可用槽在 15:30"：
      - preferred_start_time=13:00
      - home→P_TEST 60min（首跳 buffer=0）→ P_TEST 14:00 起
      - P_TEST 60min → 15:00 结束
      - P_TEST→R_TEST 12min + buffer5 → 餐厅自然到达 15:00+12+5=15:17
      - R_TEST.reservation_slots：15:00(不可订，且早于到达) / 15:30(可订)
        → 吸附到 15:30（不早于到达的最早可用槽）——预演阶段产生 13min 中段死等。

    候选 A：R_TEST 是首个被吸附顶出正差额的餐厅节点，以它为锚反推：
    15:30 - (12+5)min(hop1+buffer) - 60min(P_TEST) - (60+0)min(hop0+buffer)
    = 13:13——晚于原下限 13:00，倒推生效，重跑正向 assemble：
      - home→P_TEST 60min → P_TEST 14:13 起 → 15:13 结束
      - P_TEST→R_TEST 12min+buffer5 → 自然到达 15:30，恰好等于锚点槽，无残留等待
      - R_TEST 45min → 16:15 结束；R_TEST→home 20min（返程 buffer=0）→ 16:35

    lookup_hop 走 monkeypatch 定死通勤分钟数（不依赖 routes.json 真值，专注
    验证吸附本身 + 倒推顺延，不掺通勤查找的不确定性）。

    断言：
    - 出发时刻倒推为 "13:13"（不是原始声明的 "13:00"）。
    - 餐厅节点吸附为 "15:30"（不是未倒推时自然到达的 "15:17"），且倒推后
      自然到达与锚点槽精确对齐（无残留中段死等）。
    - 吸附挤出的等待不是"只改一个字段的假动作"：餐厅之后所有时刻（返程
      hop + home 终点 + total_minutes）跟着顺延，印证 assemble 的单游标
      （cursor_min）推进机制天然传导吸附结果——倒推重跑亦是同一内核。
    """
    monkeypatch.setattr(
        _assemble_blueprint_mod,
        "load_restaurants",
        lambda: [_fake_restaurant("R_TEST", [("15:00", False), ("15:30", True)])],
    )

    def _fake_lookup_hop(from_id, to_id, transport_pref, user_profile):
        table = {
            ("home", "P_TEST"): (60, "taxi", "real_route"),
            ("P_TEST", "R_TEST"): (12, "taxi", "real_route"),
            ("R_TEST", "home"): (20, "taxi", "real_route"),
        }
        return table[(from_id, to_id)]

    monkeypatch.setattr(_assemble_blueprint_mod, "lookup_hop", _fake_lookup_hop)

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
        rationale="槽吸附回归用例",
    )
    itin = assemble_from_blueprint(_intent((3, 4)), bp, profile)

    # 出发时刻倒推：13:00 → 13:13（R_TEST 锚点）
    assert itin.nodes[0].start_time == "13:13"
    assert itin.nodes[1].start_time == "14:13"

    # 吸附：倒推后自然到达恰好 == 锚点槽 15:30，无残留中段死等
    assert itin.nodes[2].target_id == "R_TEST"
    assert itin.nodes[2].start_time == "15:30", (
        f"应吸附到 15:30（不早于自然到达的最早可用槽），实际 {itin.nodes[2].start_time}"
    )
    natural_arrival = (
        _parse_hhmm(itin.hops[1].start_time) + itin.hops[1].minutes + itin.hops[1].buffer_min
    )
    assert _parse_hhmm(itin.nodes[2].start_time) - natural_arrival == 0

    # 顺延：餐厅之后所有时刻跟着推（不是只改餐厅这一个字段）
    assert itin.nodes[2].duration_min == 45
    assert itin.hops[2].start_time == "16:15"  # 15:30 + 45min
    assert itin.hops[2].minutes == 20
    assert itin.nodes[3].start_time == "16:35"  # 16:15 + 20min
    assert itin.total_minutes == 202  # 16:35 - 13:13（比未倒推的 215 少 13min）

    # 不变量：hops/nodes 长度、首尾 home
    assert len(itin.hops) == len(itin.nodes) - 1
    assert itin.nodes[0].target_kind == "home" and itin.nodes[-1].target_kind == "home"


def test_A8_restaurant_no_available_slot_leaves_natural_arrival_unsnapped(profile, monkeypatch):
    """无任何槽不早于到达时刻可用 → 不吸附（诚实，别硬造），让 critic 照常拦。

    R_TEST 唯一的槽（14:00）早于自然到达（15:17）且不存在任何更晚的槽——
    assemble 应原样保留自然到达时刻，不伪造一个不存在的可预约时刻。
    """
    monkeypatch.setattr(
        _assemble_blueprint_mod,
        "load_restaurants",
        lambda: [_fake_restaurant("R_TEST", [("14:00", True)])],
    )

    def _fake_lookup_hop(from_id, to_id, transport_pref, user_profile):
        table = {
            ("home", "P_TEST"): (60, "taxi", "real_route"),
            ("P_TEST", "R_TEST"): (12, "taxi", "real_route"),
            ("R_TEST", "home"): (20, "taxi", "real_route"),
        }
        return table[(from_id, to_id)]

    monkeypatch.setattr(_assemble_blueprint_mod, "lookup_hop", _fake_lookup_hop)

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
        rationale="无可用槽回归用例",
    )
    itin = assemble_from_blueprint(_intent((3, 4)), bp, profile)

    # 自然到达 15:17 原样保留，不吸附（critic 的 check_demo_restaurant_full
    # 会在下游拦：15:17 既不等于 14:00 这个唯一槽，判 RESTAURANT_FULL_UNRESOLVED）
    assert itin.nodes[2].start_time == "15:17"
