"""tests.test_critics_v2 —— Wave 4 Task 5：critics_v2 兜底层综合单测（edge_v1）。

覆盖 9 项契约：

```
| #  | 测试                                                  | 触发                                  |
|----|-------------------------------------------------------|--------------------------------------|
| 1  | test_legal_itinerary_no_critical_violations           | 标准合法行程 → 0 critical            |
| 2  | test_invariants_critic_catches_hops_count_mismatch   | hops 数 != nodes-1 → INVARIANT_BROKEN|
| 3  | test_invariants_critic_catches_non_home_first_node    | 首节点非 home → INVARIANT_BROKEN     |
| 4  | test_invariants_critic_catches_home_with_duration     | home duration!=0 → INVARIANT_BROKEN  |
| 5  | test_nodes_incomplete_when_only_home_nodes            | 仅 [home,home] → NODES_INCOMPLETE    |
| 6  | test_duration_too_long / too_short                    | 总时长越界 → DURATION_OUT_OF_RANGE   |
| 7  | test_temporal_inconsistent_to_node_too_early           | to_node 早于 hop+buffer → TIMELINE   |
| 8  | test_format_violations_only_critical / no_dot_path    | 人话化 + 不暴露 dot-path             |
| 9  | test_dietary_violation / demo_full_check              | warning + demo-aware                 |
```

【人话约束（design.md 强约束）】

format_violations_for_llm 必须杜绝 `nodes[1]` / `hops[2]` 这类 dot-path。
test 8 直接断言输出字符串中不包含 dot-path 模板。

【过渡态桥】

`agent/__init__.py` 仍 eager-import 损坏的 `planner.py`（待 Task 9 修），
顶部把 `agent` 注册为空命名空间包绕开。删除时机：Task 9 完成后。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


# ============================================================
# 过渡态桥
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.planning.critic.critics_v2 import (  # noqa: E402
    Severity,
    Violation,
    ViolationCode,
    _check_invariants,
    format_violations_for_llm,
    validate_itinerary,
)
from schemas.intent import IntentExtraction  # noqa: E402
from schemas.itinerary import (  # noqa: E402
    ActivityNode,
    Hop,
    Itinerary,
    OrderRecord,
)


# ============================================================
# fixture builders
# ============================================================


def _make_intent(
    *,
    duration_hours: list[int] = [4, 6],
    distance_max_km: float = 10.0,
    dietary_constraints: list[str] | None = None,
    social_context: str = "家庭日常",
) -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=duration_hours,  # type: ignore[arg-type]
        distance_max_km=distance_max_km,
        companions=[],
        physical_constraints=[],
        dietary_constraints=dietary_constraints or [],
        experience_tags=[],
        social_context=social_context,
        raw_input="测试输入",
        parse_confidence=0.9,
    )


def _make_legal_itinerary(
    *,
    poi_id: str = "P040",  # 童趣海洋亲子馆 / suitable_for=家庭日常
    restaurant_id: str = "R001",  # 轻语沙拉 / suitable_for=家庭日常 / tags=低脂
    poi_duration: int = 165,
    dining_duration: int = 60,
) -> Itinerary:
    """构造 4 nodes / 3 hops 的标准合法 itinerary（与 design.md 示例时间轴一致）。

    路网真值（taxi）：home→P040 9 / P040→R001 5 / R001→home 7。
    时间轴（POI 165min）：
        14:00 出发 → 14:09 抵 P040 → 停 165min → 16:54 离
        16:54 hop 5min + 5buf → 17:04 抵 R001 → 停 60min → 18:04 离
        18:04 hop 7min → 18:11 到家
    total = 251min（落在 [4,6]h ±30min 即 [210,390] 容差内）。
    """
    nodes = [
        ActivityNode(
            node_id="n0",
            kind="起点",
            target_kind="home",
            target_id="home",
            start_time="14:00",
            duration_min=0,
            title="出发",
        ),
        ActivityNode(
            node_id="n1",
            kind="主活动",
            target_kind="poi",
            target_id=poi_id,
            start_time="14:09",
            duration_min=poi_duration,
            title=poi_id,
        ),
        ActivityNode(
            node_id="n2",
            kind="用餐",
            target_kind="restaurant",
            target_id=restaurant_id,
            start_time="17:04",
            duration_min=dining_duration,
            title=restaurant_id,
        ),
        ActivityNode(
            node_id="n3",
            kind="终点",
            target_kind="home",
            target_id="home",
            start_time="18:11",
            duration_min=0,
            title="回家",
        ),
    ]
    hops = [
        Hop(
            hop_id="h0",
            from_node_id="n0",
            to_node_id="n1",
            start_time="14:00",
            minutes=9,
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
        Hop(
            hop_id="h1",
            from_node_id="n1",
            to_node_id="n2",
            start_time="16:54",
            minutes=5,
            mode="taxi",
            path_type="real_route",
            buffer_min=5,
        ),
        Hop(
            hop_id="h2",
            from_node_id="n2",
            to_node_id="n3",
            start_time="18:04",
            minutes=7,
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
    ]
    return Itinerary(
        summary="家庭半日方案（测试）",
        nodes=nodes,
        hops=hops,
        total_minutes=251,
    )


# ============================================================
# 测试 1：合法 itinerary 不触发 critical
# ============================================================


def test_legal_itinerary_no_critical_violations():
    """4 nodes / 3 hops 标准合法行程 + matching intent → 无 critical violation。"""
    intent = _make_intent()
    itinerary = _make_legal_itinerary()

    violations = validate_itinerary(itinerary, intent)
    critical = [v for v in violations if v.severity == Severity.HARD]

    assert critical == [], f"合法 itinerary 不应有 critical violation，实际：{critical}"


# ============================================================
# 测试 2-4：INVARIANT_BROKEN（结构不变量）
# ============================================================


def test_invariants_critic_catches_hops_count_mismatch():
    """构造合法 itinerary 后手工 pop 一个 hop（绕过 Pydantic）→ _check_invariants 命中。"""
    itinerary = _make_legal_itinerary()
    # bypass Pydantic：构造后直接 mutate list
    itinerary.hops.pop()  # len(hops) = 2，应等于 len(nodes)-1=3 → 不变量违反

    violations = _check_invariants(itinerary)
    codes = [v.code for v in violations]

    assert ViolationCode.INVARIANT_BROKEN in codes, (
        f"hops 数与 nodes-1 不匹配应触发 INVARIANT_BROKEN，实际：{codes}"
    )
    assert all(v.severity == Severity.HARD for v in violations if v.code == ViolationCode.INVARIANT_BROKEN)


def test_invariants_critic_catches_non_home_first_node():
    """构造合法后把首节点 target_kind 改为 poi → INVARIANT_BROKEN。"""
    itinerary = _make_legal_itinerary()
    # bypass Pydantic：直接替换首节点
    fake_first = ActivityNode(
        node_id="n0",
        kind="主活动",
        target_kind="poi",  # ← 故意非 home
        target_id="P040",
        start_time="14:00",
        duration_min=0,
        title="不该是 POI",
    )
    itinerary.nodes[0] = fake_first

    violations = _check_invariants(itinerary)
    codes = [v.code for v in violations]
    assert ViolationCode.INVARIANT_BROKEN in codes


def test_invariants_critic_catches_home_with_duration():
    """home 节点 duration_min != 0 → INVARIANT_BROKEN。"""
    itinerary = _make_legal_itinerary()
    # bypass Pydantic：直接替换尾节点为 duration_min=10 的 home
    fake_last = ActivityNode(
        node_id="n3",
        kind="终点",
        target_kind="home",
        target_id="home",
        start_time="17:21",
        duration_min=10,  # ← 故意非 0
        title="回家",
    )
    itinerary.nodes[-1] = fake_last

    violations = _check_invariants(itinerary)
    codes = [v.code for v in violations]
    assert ViolationCode.INVARIANT_BROKEN in codes
    msg = next(v.message for v in violations if v.code == ViolationCode.INVARIANT_BROKEN)
    assert "0" in msg or "停留" in msg, f"消息应说明 home 不应停留：{msg}"


# ============================================================
# 测试 5：NODES_INCOMPLETE（仅 [home, home]）
# ============================================================


def test_nodes_incomplete_when_only_home_nodes():
    """退化 itinerary [home, home] / hops=[in_place 0min] → NODES_INCOMPLETE critical。"""
    nodes = [
        ActivityNode(
            node_id="n0",
            kind="起点",
            target_kind="home",
            target_id="home",
            start_time="14:00",
            duration_min=0,
            title="出发",
        ),
        ActivityNode(
            node_id="n1",
            kind="终点",
            target_kind="home",
            target_id="home",
            start_time="14:00",
            duration_min=0,
            title="回家",
        ),
    ]
    hops = [
        Hop(
            hop_id="h0",
            from_node_id="n0",
            to_node_id="n1",
            start_time="14:00",
            minutes=0,
            mode="virtual",
            path_type="in_place",
            buffer_min=0,
        ),
    ]
    itinerary = Itinerary(
        summary="退化",
        nodes=nodes,
        hops=hops,
        total_minutes=0,
    )

    intent = _make_intent()
    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations if v.severity == Severity.HARD]

    assert ViolationCode.NODES_INCOMPLETE in codes, (
        f"应触发 NODES_INCOMPLETE，实际 codes={codes}"
    )


# ============================================================
# 测试 6：DURATION_OUT_OF_RANGE
# ============================================================


def test_duration_too_long_triggers_critical():
    """duration_hours=[4,6] / total_minutes=480 → 480 > 6*60+30=390 → critical。"""
    intent = _make_intent(duration_hours=[4, 6])
    itinerary = _make_legal_itinerary()
    # 越过 Pydantic：直接改 total_minutes（合法字段，无 frozen 限制）
    object.__setattr__(itinerary, "total_minutes", 480)

    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations if v.severity == Severity.HARD]

    assert ViolationCode.DURATION_OUT_OF_RANGE in codes, (
        f"应触发 DURATION_OUT_OF_RANGE（过长），实际 codes={codes}"
    )


def test_duration_too_short_triggers_critical():
    """total_minutes=60 < 4*60-30=210 → critical。"""
    intent = _make_intent(duration_hours=[4, 6])
    itinerary = _make_legal_itinerary()
    object.__setattr__(itinerary, "total_minutes", 60)

    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations if v.severity == Severity.HARD]

    assert ViolationCode.DURATION_OUT_OF_RANGE in codes


# ============================================================
# 测试 7：TIMELINE_INCONSISTENT
# ============================================================


def test_temporal_inconsistent_to_node_too_early():
    """构造 to_node.start 早于 hop.end + buffer 的情况 → TIMELINE_INCONSISTENT。

    手工构造 hops/nodes 让 _check_temporal_feasibility 命中（绕过 assemble 自洽保证）。
    """
    nodes = [
        ActivityNode(
            node_id="n0",
            kind="起点",
            target_kind="home",
            target_id="home",
            start_time="14:00",
            duration_min=0,
            title="出发",
        ),
        ActivityNode(
            node_id="n1",
            kind="主活动",
            target_kind="poi",
            target_id="P040",
            start_time="14:01",  # ← 故意：hop.end=14:09 + buffer 5 = 14:14 应有；却是 14:01
            duration_min=60,
            title="P040",
        ),
        ActivityNode(
            node_id="n2",
            kind="终点",
            target_kind="home",
            target_id="home",
            start_time="15:10",
            duration_min=0,
            title="回家",
        ),
    ]
    hops = [
        Hop(
            hop_id="h0",
            from_node_id="n0",
            to_node_id="n1",
            start_time="14:00",
            minutes=9,
            mode="taxi",
            path_type="real_route",
            buffer_min=5,  # 14:00+9min+5buf=14:14；but to_node start=14:01
        ),
        Hop(
            hop_id="h1",
            from_node_id="n1",
            to_node_id="n2",
            start_time="15:01",
            minutes=9,
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
    ]
    itinerary = Itinerary(
        summary="时间错乱",
        nodes=nodes,
        hops=hops,
        total_minutes=70,
    )

    intent = _make_intent(duration_hours=[1, 2])
    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations if v.severity == Severity.HARD]

    assert ViolationCode.TIMELINE_INCONSISTENT in codes, (
        f"to_node 早于 hop.end + buffer 应触发 TIMELINE_INCONSISTENT，实际 codes={codes}"
    )


# ============================================================
# 测试 8：format_violations_for_llm 人话化
# ============================================================


def test_format_violations_only_critical_in_message():
    """1 critical + 1 warning → 输出仅含 critical。"""
    critical_v = Violation(
        code=ViolationCode.NODES_INCOMPLETE,
        severity=Severity.HARD,
        message="行程中间没有任何活动节点（nodes 仅含首尾 home）。",
        field_path="nodes",
    )
    warning_v = Violation(
        code=ViolationCode.DISTANCE_EXCEEDED,
        severity=Severity.SOFT,
        message="第 2 段「主活动 · 远点」距家 8.0km 超出。",
        field_path="nodes[1].target_id",
    )
    msg = format_violations_for_llm([critical_v, warning_v])

    assert "活动节点" in msg, "critical 必须进消息"
    assert "8.0km" not in msg, "warning 不应进消息"
    assert "1 处违规" in msg, f"应标注 critical 数量为 1，实际：\n{msg}"


def test_format_violations_empty_when_no_critical():
    """0 critical（空 / 全是 warning）→ 输出空字符串。"""
    assert format_violations_for_llm([]) == ""

    only_warning = Violation(
        code=ViolationCode.DISTANCE_EXCEEDED,
        severity=Severity.SOFT,
        message="略超",
        field_path="nodes[1]",
    )
    assert format_violations_for_llm([only_warning]) == "", (
        "全 warning 时应返回空字符串"
    )


def test_format_violations_does_not_leak_dot_path():
    """关键人话约束：format_violations_for_llm 输出不应含 nodes[N] / hops[N] 这类 dot-path。

    design.md 强约束：LLM 看到的是「第 N 段「kind · title」」，不是字段路径。
    """
    violations = [
        Violation(
            code=ViolationCode.HOP_INFEASIBLE,
            severity=Severity.HARD,
            message=(
                "第 2 段「主活动 · 童趣海洋亲子馆」去往 第 3 段「用餐 · 轻语沙拉」"
                "的通勤实际需要约 9 分钟（taxi），但行程里这段 hop 只留了 3 分钟。"
            ),
            field_path="hops[1].minutes",
        ),
        Violation(
            code=ViolationCode.INVARIANT_BROKEN,
            severity=Severity.HARD,
            message="行程结构不变量违反：首节点必须是 home。",
            field_path="nodes[0]",
        ),
    ]
    msg = format_violations_for_llm(violations)

    # 关键断言：不暴露 dot-path
    assert "hops[1]" not in msg, f"输出不应含 hops[1] 字段路径：\n{msg}"
    assert "hops[1].minutes" not in msg, f"输出不应含 dot-path：\n{msg}"
    assert "nodes[0]" not in msg, f"输出不应含 nodes[0] 字段路径：\n{msg}"
    # 必须包含人话定位
    assert "第 2 段" in msg or "童趣海洋亲子馆" in msg, f"应包含人话定位：\n{msg}"


# ============================================================
# 测试 9：DIETARY_VIOLATION + demo_full_check
# ============================================================


def test_dietary_violation_warning_when_restaurant_tags_miss():
    """intent dietary=['粤菜'] / 餐厅 R001（低脂）不含粤菜 → warning。"""
    intent = _make_intent(dietary_constraints=["粤菜"])
    itinerary = _make_legal_itinerary(restaurant_id="R001")  # R001 tags 不含粤菜

    violations = validate_itinerary(itinerary, intent)
    dietary_warnings = [
        v for v in violations
        if v.code == ViolationCode.DIETARY_VIOLATION and v.severity == Severity.SOFT
    ]

    assert dietary_warnings, (
        f"R001 不含粤菜 tag 应触发 DIETARY_VIOLATION warning，"
        f"实际 violations={[v.code for v in violations]}"
    )


def test_dietary_violation_no_trigger_when_tag_match():
    """intent dietary=['低脂'] / 餐厅 R001（含低脂）→ 不触发 dietary。"""
    intent = _make_intent(dietary_constraints=["低脂"])
    itinerary = _make_legal_itinerary(restaurant_id="R001")

    violations = validate_itinerary(itinerary, intent)
    dietary_codes = [v.code for v in violations if v.code == ViolationCode.DIETARY_VIOLATION]
    assert not dietary_codes


def test_demo_full_check_enabled_triggers_at_17_00(monkeypatch):
    """ENABLE_DEMO_FULL_CHECK=1 + 用餐 node start=17:00 → critical。"""
    monkeypatch.setenv("ENABLE_DEMO_FULL_CHECK", "1")

    # 构造一个用餐 node start=17:00 的 itinerary（手工，避免 _make_legal_itinerary 的
    # 时间表不匹配问题）
    nodes = [
        ActivityNode(
            node_id="n0",
            kind="起点",
            target_kind="home",
            target_id="home",
            start_time="14:00",
            duration_min=0,
            title="出发",
        ),
        ActivityNode(
            node_id="n1",
            kind="主活动",
            target_kind="poi",
            target_id="P040",
            start_time="14:09",
            duration_min=160,
            title="P040",
        ),
        ActivityNode(
            node_id="n2",
            kind="用餐",
            target_kind="restaurant",
            target_id="R001",
            start_time="17:00",  # ← demo 满座埋点时段
            duration_min=60,
            title="R001",
        ),
        ActivityNode(
            node_id="n3",
            kind="终点",
            target_kind="home",
            target_id="home",
            start_time="18:07",
            duration_min=0,
            title="回家",
        ),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="14:00", minutes=9, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="16:49", minutes=5, mode="taxi", path_type="real_route", buffer_min=5),
        Hop(hop_id="h2", from_node_id="n2", to_node_id="n3", start_time="18:00", minutes=7, mode="taxi", path_type="real_route", buffer_min=0),
    ]
    itinerary = Itinerary(
        summary="17:00 用餐",
        nodes=nodes,
        hops=hops,
        total_minutes=247,
    )
    intent = _make_intent()

    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations if v.severity == Severity.HARD]

    assert ViolationCode.RESTAURANT_FULL_UNRESOLVED in codes, (
        f"开关开 + 17:00 应触发 RESTAURANT_FULL_UNRESOLVED，实际 codes={codes}"
    )


def test_demo_full_check_disabled_no_trigger_at_17_00(monkeypatch):
    """ENABLE_DEMO_FULL_CHECK=0 + 用餐 node start=17:00 → 不触发。"""
    monkeypatch.setenv("ENABLE_DEMO_FULL_CHECK", "0")

    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home", start_time="14:00", duration_min=0, title="出发"),
        ActivityNode(node_id="n1", kind="主活动", target_kind="poi", target_id="P040", start_time="14:09", duration_min=160, title="P040"),
        ActivityNode(node_id="n2", kind="用餐", target_kind="restaurant", target_id="R001", start_time="17:00", duration_min=60, title="R001"),
        ActivityNode(node_id="n3", kind="终点", target_kind="home", target_id="home", start_time="18:07", duration_min=0, title="回家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="14:00", minutes=9, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="16:49", minutes=5, mode="taxi", path_type="real_route", buffer_min=5),
        Hop(hop_id="h2", from_node_id="n2", to_node_id="n3", start_time="18:00", minutes=7, mode="taxi", path_type="real_route", buffer_min=0),
    ]
    itinerary = Itinerary(summary="17:00 用餐 关开关", nodes=nodes, hops=hops, total_minutes=247)
    intent = _make_intent()

    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations]
    assert ViolationCode.RESTAURANT_FULL_UNRESOLVED not in codes


# ============================================================
# 测试 12：CAPACITY_REQUIREMENT_VIOLATED（spec innovation-review M3）
# ============================================================


def test_capacity_violated_when_party_size_exceeds_table():
    """intent.capacity_requirement=6 / R001 仅 2/4 桌 → critical。"""
    intent = _make_intent()
    intent.capacity_requirement = 6  # ≥6 人

    itinerary = _make_legal_itinerary(restaurant_id="R001")  # R001: 2/4 only
    violations = validate_itinerary(itinerary, intent)
    capacity_violations = [
        v for v in violations
        if v.code == ViolationCode.CAPACITY_REQUIREMENT_VIOLATED
        and v.severity == Severity.HARD
    ]

    assert capacity_violations, (
        f"R001 仅含 2/4 桌但同行 6 人 → 应触发 CAPACITY_REQUIREMENT_VIOLATED，"
        f"实际 violations={[v.code for v in violations]}"
    )
    msg = capacity_violations[0].message
    assert "桌型" in msg, f"违规消息应含「桌型」字样：{msg}"
    # 不暴露 dot-path
    assert "target_id" not in msg, f"违规消息不暴露字段名：{msg}"


def test_capacity_no_trigger_when_le_4():
    """capacity_requirement=4 → 不触发（4 人桌业界默认有）。"""
    intent = _make_intent()
    intent.capacity_requirement = 4

    itinerary = _make_legal_itinerary(restaurant_id="R001")
    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations]
    assert ViolationCode.CAPACITY_REQUIREMENT_VIOLATED not in codes, (
        f"capacity_requirement=4 不应触发 critic，实际 codes={codes}"
    )


def test_capacity_no_trigger_when_none():
    """capacity_requirement=None（同行 ≤3 人不必填）→ 不触发。"""
    intent = _make_intent()
    intent.capacity_requirement = None

    itinerary = _make_legal_itinerary(restaurant_id="R001")
    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations]
    assert ViolationCode.CAPACITY_REQUIREMENT_VIOLATED not in codes
