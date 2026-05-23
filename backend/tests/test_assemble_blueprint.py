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
from agent import lookup_hop as _lookup_hop_mod  # noqa: E402
from agent.assemble_blueprint import (  # noqa: E402
    _parse_hhmm,
    assemble_from_blueprint,
)
from agent.blueprint import (  # noqa: E402
    BlueprintNode,
    BlueprintTargetKind,
    PlanBlueprint,
)
from data.loader import load_user_profile  # noqa: E402
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

    # I7：to_node.start_time == hop.start + hop.minutes + hop.buffer_min
    for i, hop in enumerate(hops):
        to_node = nodes[i + 1]
        expected_to_start = (
            _parse_hhmm(hop.start_time) + hop.minutes + hop.buffer_min
        )
        assert _parse_hhmm(to_node.start_time) == expected_to_start, (
            f"nodes[{i + 1}].start_time={to_node.start_time} 应等于 "
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

    预期时间轴（preferred_start_time=14:00, taxi）：
      n0(home, 14:00, 0min)
        h0: 14:00 → 14:09, 9min, taxi, real_route, buffer=0
      n1(P040, 14:09, 165min) → 17:14
        h1: 17:14 → 17:19, 5min, taxi, real_route, buffer=5
      n2(R001, 17:24, 60min) → 18:24
        h2: 18:24 → 18:31, 7min, taxi, real_route, buffer=0
      n3(home, 18:31, 0min)
      total = 18:31 - 14:00 = 271min
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

    # 首跳 hop 命中 routes.json：home → P040 taxi_minutes = 9
    assert itin.hops[0].start_time == "14:00"
    assert itin.hops[0].minutes == 9
    assert itin.hops[0].mode == "taxi"
    assert itin.hops[0].path_type == "real_route"
    assert itin.nodes[1].start_time == "14:09"

    # 公共不变量
    _assert_invariants(itin, bp)


def test_A1_actual_timing_walkthrough(profile):
    """A1 同结构，逐字段验证时间游标的精确推进。

    从 14:00 开始，taxi：
      h0 home→P040 = 9min, buffer=0  → 14:00→14:09 → P040 start 14:09
      n1 P040 165min                 → 14:09→16:54
      h1 P040→R001 = 5min, buffer=5  → 16:54→16:59 → R001 start 17:04
      n2 R001 60min                  → 17:04→18:04
      h2 R001→home = 7min, buffer=0  → 18:04→18:11 → home start 18:11
      total = 18:11 - 14:00 = 251min
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

    # 完整精确断言
    assert itin.nodes[0].start_time == "14:00"
    assert itin.nodes[1].start_time == "14:09"
    assert itin.nodes[2].start_time == "17:04"
    assert itin.nodes[3].start_time == "18:11"

    assert itin.hops[0].start_time == "14:00" and itin.hops[0].minutes == 9
    assert itin.hops[1].start_time == "16:54" and itin.hops[1].minutes == 5
    assert itin.hops[2].start_time == "18:04" and itin.hops[2].minutes == 7

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
