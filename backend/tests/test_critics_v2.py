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
| 6  | test_duration_too_long(HARD) / too_short(SOFT，ADR-0010) | 总时长越界 → DURATION_OUT_OF_RANGE |
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
    physical_constraints: list[str] | None = None,
    social_context: str = "家庭日常",
) -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=duration_hours,  # type: ignore[arg-type]
        distance_max_km=distance_max_km,
        companions=[],
        physical_constraints=physical_constraints or [],
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
    时间轴（POI 165min；D-8a 修订——用餐钉在**真实预约槽** 17:30）：
        14:00 出发 → 14:09 抵 P040 → 停 165min → 16:54 离
        16:54 hop 5min + 5buf → 17:04 自然抵 R001，**等到 17:30 开吃**（餐前等待，
        生产同款 not_before_start 语义；ADR-0008 红队 R3 做实后，排定时刻必须是
        店家真实提供的预约槽——旧 fixture 的 17:04 从来订不上、只是旧检查装看不见）
        → 停 60min → 18:30 离 → hop 7min → 18:37 到家
    total = 277min（落在 [4,6]h ±30min 即 [210,390] 容差内）。
    R001/R002 的 17:30 槽均真实可订（mock reservation_slots）。
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
            start_time="17:30",
            duration_min=dining_duration,
            title=restaurant_id,
        ),
        ActivityNode(
            node_id="n3",
            kind="终点",
            target_kind="home",
            target_id="home",
            start_time="18:37",
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
            start_time="18:30",
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
        total_minutes=277,
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


def test_duration_too_short_triggers_soft_advisory():
    """total_minutes=60 < 4*60-30=210 → SOFT（ADR-0010 D-3 拆向，intentional 行为改变）。

    修订前（ADR-0008）：越界一律 HARD。ADR-0010 决策 10"稀缺兜底"改判：时长
    不足降为 SOFT——候选稀薄时"短而好"的方案不该被硬性挡在 gate 外，只建议不
    拦截。本测试断言更新为：DURATION_OUT_OF_RANGE 触发但 severity=SOFT，且
    不出现在 HARD 违规里（不再挡 report.passed）。
    """
    intent = _make_intent(duration_hours=[4, 6])
    itinerary = _make_legal_itinerary()
    object.__setattr__(itinerary, "total_minutes", 60)

    violations = validate_itinerary(itinerary, intent)
    hard_codes = [v.code for v in violations if v.severity == Severity.HARD]
    soft_codes = [v.code for v in violations if v.severity == Severity.SOFT]

    assert ViolationCode.DURATION_OUT_OF_RANGE not in hard_codes
    assert ViolationCode.DURATION_OUT_OF_RANGE in soft_codes


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


def test_format_violations_renders_expected_range_natural_language():
    """spec planning-quality-deep-review R4：format 输出含「建议范围 X-Y min」，**不**含字段名。

    迁移自 test_age_aware_critic.py（ADR-0009 决策 8 删蓝图死 critic 层时收拢到本文件
    ——这条测的是 critics_v2.format_violations_for_llm 的活行为，与已删的蓝图 critic
    无关，不随蓝图死层一起删）。
    """
    v = Violation(
        code=ViolationCode.AGE_DURATION_MISMATCH,
        severity=Severity.HARD,
        message="第 1 段 90 分钟超出年龄约束（含 5 岁孩）",
        field_path="nodes[1].duration_min",
        expected_range=(60, 75),
    )
    text = format_violations_for_llm([v])
    assert "建议范围 60-75 min" in text
    # 不暴露字段名
    assert "expected_range" not in text
    assert "nodes[1]" not in text
    assert "duration_min" not in text
    assert "field_path" not in text


def test_format_violations_no_expected_range_no_extra_text():
    """无 expected_range 的 violation → format 不加「建议范围」段（同上，迁移自 test_age_aware_critic.py）。"""
    v = Violation(
        code=ViolationCode.DURATION_OUT_OF_RANGE,
        severity=Severity.HARD,
        message="总时长超上限",
    )
    text = format_violations_for_llm([v])
    assert "建议范围" not in text


# ============================================================
# 测试 9：DIETARY_VIOLATION + demo_full_check
# ============================================================


def test_dietary_violation_hard_when_restaurant_tags_miss():
    """ADR-0014 决策 2（G-2）改判：intent dietary=['不辣']（hard 忌口）/ 餐厅
    R001（低脂，不含不辣）→ 仍触发 DIETARY_VIOLATION HARD（gate 修复）。

    改判前本测试用 dietary=['粤菜']（风格型 soft tag）断言 HARD；G-2 把
    check_dietary 收窄成只核验 hard 子集（粤菜是 soft，不再由本 check
    gate，见 `test_dietary_violation_soft_only_mismatch_does_not_gate`），
    故本测试改用真正的 hard 忌口 tag 覆盖"该拦的确实拦住了"这条契约。
    """
    intent = _make_intent(dietary_constraints=["不辣"])
    itinerary = _make_legal_itinerary(restaurant_id="R001")  # R001 tags=[低脂]，不含不辣

    violations = validate_itinerary(itinerary, intent)
    dietary_hard = [
        v for v in violations
        if v.code == ViolationCode.DIETARY_VIOLATION and v.severity == Severity.HARD
    ]

    assert dietary_hard, (
        f"G-2：R001 不满足 hard 忌口 tag「不辣」应触发 DIETARY_VIOLATION HARD，"
        f"实际 violations={[(v.code, v.severity) for v in violations]}"
    )


def test_dietary_violation_soft_only_mismatch_does_not_gate():
    """ADR-0014 决策 2（G-2）：intent dietary=['粤菜']（风格型 soft tag，非
    忌口）/ 餐厅 R001（低脂，不含粤菜）→ **不**触发 DIETARY_VIOLATION。

    soft 约束未满足不该 gate 整条修复闭环——那是"这组约束下能做到的最好
    结果"，应该走出口满足度审计的 CONSTRAINT_RELAXED advisory 告知（见
    `agent.planning.critic.exit_audit`），不是 critic HARD 违规。
    """
    intent = _make_intent(dietary_constraints=["粤菜"])
    itinerary = _make_legal_itinerary(restaurant_id="R001")

    violations = validate_itinerary(itinerary, intent)
    dietary_codes = [v.code for v in violations if v.code == ViolationCode.DIETARY_VIOLATION]
    assert not dietary_codes, (
        f"G-2：soft-only dietary 未满足不应产生 DIETARY_VIOLATION，实际={dietary_codes}"
    )


def test_dietary_violation_no_trigger_when_tag_match():
    """intent dietary=['低脂'] / 餐厅 R001（含低脂）→ 不触发 dietary。"""
    intent = _make_intent(dietary_constraints=["低脂"])
    itinerary = _make_legal_itinerary(restaurant_id="R001")

    violations = validate_itinerary(itinerary, intent)
    dietary_codes = [v.code for v in violations if v.code == ViolationCode.DIETARY_VIOLATION]
    assert not dietary_codes


# ============================================================
# 测试 9b：PHYSICAL_VIOLATION（ADR-0014 决策 2 · G-2 新增，与 DIETARY_VIOLATION 对称）
# ============================================================


def test_physical_violation_hard_when_poi_tags_miss():
    """intent physical=['无障碍']（hard 安全型）/ P040（无该 tag）→ 触发
    PHYSICAL_VIOLATION HARD。

    P040 tags 含 适合老人/无台阶/可休息（同属 hard 安全簇）但不含"无障碍"，
    刻意验证 ALL-match（缺其中任一 hard 项即违规，不是"满足其它几个就算过关"）。
    """
    intent = _make_intent(physical_constraints=["无障碍"])
    itinerary = _make_legal_itinerary(poi_id="P040")

    violations = validate_itinerary(itinerary, intent)
    physical_hard = [
        v for v in violations
        if v.code == ViolationCode.PHYSICAL_VIOLATION and v.severity == Severity.HARD
    ]
    assert physical_hard, (
        f"G-2：P040 不满足 hard 物理 tag「无障碍」应触发 PHYSICAL_VIOLATION HARD，"
        f"实际 violations={[(v.code, v.severity) for v in violations]}"
    )


def test_physical_violation_no_trigger_when_tag_match():
    """intent physical=['适合老人']（hard，P040 已含）→ 不触发 PHYSICAL_VIOLATION。"""
    intent = _make_intent(physical_constraints=["适合老人"])
    itinerary = _make_legal_itinerary(poi_id="P040")

    violations = validate_itinerary(itinerary, intent)
    physical_codes = [v.code for v in violations if v.code == ViolationCode.PHYSICAL_VIOLATION]
    assert not physical_codes


def test_physical_violation_soft_only_mismatch_does_not_gate():
    """intent physical=['适合青少年']（soft，P040 不含）→ **不**触发
    PHYSICAL_VIOLATION——soft 未满足走出口满足度审计告知，不是 critic HARD。
    """
    intent = _make_intent(physical_constraints=["适合青少年"])
    itinerary = _make_legal_itinerary(poi_id="P040")

    violations = validate_itinerary(itinerary, intent)
    physical_codes = [v.code for v in violations if v.code == ViolationCode.PHYSICAL_VIOLATION]
    assert not physical_codes, (
        f"G-2：soft-only physical 未满足不应产生 PHYSICAL_VIOLATION，实际={physical_codes}"
    )


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
# 测试 11b（c′批 任务三）：backprompt 槽位提示增强
#
# 病灶：改动前 RESTAURANT_FULL_UNRESOLVED 的反馈文本只说"这个时刻订不上"，
# 不告诉 LLM 该店真实可订的槽（尤其最晚一个）——LLM 在 backprompt 轮里没有
# 这份信息，只能瞎猜下一次改到几点。R001 mock 槽位：17:00(满) / 17:30(可订)
# / 18:00(可订，排队 5min)。
# ============================================================


def test_demo_full_check_slot_full_message_includes_available_slots_hint(monkeypatch):
    """满座分支（17:00 available=False）：反馈须点出该店真实可订时段 17:30/18:00，
    并明示"最晚可订 18:00"——不能只说"已满座"就完事。
    """
    monkeypatch.setenv("ENABLE_DEMO_FULL_CHECK", "1")

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
    itinerary = Itinerary(summary="17:00 满座", nodes=nodes, hops=hops, total_minutes=247)
    intent = _make_intent()

    violations = validate_itinerary(itinerary, intent)
    full_violations = [v for v in violations if v.code == ViolationCode.RESTAURANT_FULL_UNRESOLVED]
    assert full_violations, "R001 17:00 应触发 RESTAURANT_FULL_UNRESOLVED"
    message = full_violations[0].message
    assert "17:30" in message and "18:00" in message, (
        f"反馈文本必须点出该店真实可订槽位，实际：{message}"
    )
    assert "最晚可订 18:00" in message, f"须明示最晚一个可订槽，实际：{message}"


def test_demo_full_check_super_late_arrival_no_slot_message_includes_available_slots_hint(monkeypatch):
    """超晚到达用例（"该时段无 slot 配置"分支）：node 排定 19:30——晚于 R001
    全部预约槽（17:00/17:30/18:00），槽吸附（assemble_blueprint.py::
    _earliest_available_slot_min）无解，落到 critic 拦截。反馈文本必须给出
    该店真实可订时段，不能只说"没有这个时刻"——否则 LLM 在 backprompt 轮里
    无从得知该把到达时刻提前到几点。
    """
    monkeypatch.setenv("ENABLE_DEMO_FULL_CHECK", "1")

    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home", start_time="14:00", duration_min=0, title="出发"),
        ActivityNode(node_id="n1", kind="主活动", target_kind="poi", target_id="P040", start_time="14:09", duration_min=280, title="P040"),
        ActivityNode(node_id="n2", kind="用餐", target_kind="restaurant", target_id="R001", start_time="19:30", duration_min=60, title="R001"),
        ActivityNode(node_id="n3", kind="终点", target_kind="home", target_id="home", start_time="20:37", duration_min=0, title="回家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="14:00", minutes=9, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="18:49", minutes=5, mode="taxi", path_type="real_route", buffer_min=5),
        Hop(hop_id="h2", from_node_id="n2", to_node_id="n3", start_time="20:30", minutes=7, mode="taxi", path_type="real_route", buffer_min=0),
    ]
    itinerary = Itinerary(summary="超晚到达", nodes=nodes, hops=hops, total_minutes=397)
    intent = _make_intent()

    violations = validate_itinerary(itinerary, intent)
    full_violations = [v for v in violations if v.code == ViolationCode.RESTAURANT_FULL_UNRESOLVED]
    assert full_violations, "19:30 排定（无此槽）应触发 RESTAURANT_FULL_UNRESOLVED"
    message = full_violations[0].message
    assert "没有这个时刻" in message
    assert "17:30" in message and "18:00" in message, (
        f"反馈文本必须点出该店真实可订槽位（尤其最晚一个），实际：{message}"
    )
    assert "最晚可订 18:00" in message, f"须明示最晚一个可订槽，实际：{message}"


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


# ============================================================
# O3 characterization：check_capacity 的 cap_req==5 分支（合并前钉死行为）
#
# 重构前 check_capacity 对 cap_req>=6 与 5 人（else 分支）两支判定逻辑字节完全相同
# （都是 has_seat = cap.six or cap.eight or cap.private_room）。以下两条测试钉死
# 5 人场景（触发 / 不触发）的现有行为，O3 合并死分支后必须仍然通过（行为不变）。
# ============================================================


def test_capacity_violated_when_party_size_is_5():
    """intent.capacity_requirement=5 / R001 仅 2/4 桌 → 触发 CAPACITY_REQUIREMENT_VIOLATED HARD。

    O3 重构前后行为必须一致：5 人一样坐不下 2/4 人桌，一样要触发。
    """
    intent = _make_intent()
    intent.capacity_requirement = 5  # 5 人（else 分支）

    itinerary = _make_legal_itinerary(restaurant_id="R001")  # R001: 2/4 only
    violations = validate_itinerary(itinerary, intent)
    capacity_violations = [
        v for v in violations
        if v.code == ViolationCode.CAPACITY_REQUIREMENT_VIOLATED
        and v.severity == Severity.HARD
    ]
    assert capacity_violations, (
        f"R001 仅含 2/4 桌但同行 5 人 → 应触发 CAPACITY_REQUIREMENT_VIOLATED，"
        f"实际 violations={[v.code for v in violations]}"
    )


def test_capacity_no_trigger_when_5_with_six_seat_restaurant():
    """capacity_requirement=5 / R002 含 6 人桌 → 不触发。"""
    intent = _make_intent(social_context="商务接待")
    intent.capacity_requirement = 5

    itinerary = _make_legal_itinerary(restaurant_id="R002")  # R002: six=True
    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations]
    assert ViolationCode.CAPACITY_REQUIREMENT_VIOLATED not in codes, (
        f"R002 含 6 人桌，5 人应可坐下，不应触发，实际 codes={codes}"
    )


def test_capacity_no_trigger_when_6_with_six_seat_restaurant():
    """capacity_requirement=6 / R002 含 6 人桌 → 不触发（对称覆盖 if 分支的通过路径）。"""
    intent = _make_intent(social_context="商务接待")
    intent.capacity_requirement = 6

    itinerary = _make_legal_itinerary(restaurant_id="R002")  # R002: six=True
    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations]
    assert ViolationCode.CAPACITY_REQUIREMENT_VIOLATED not in codes, (
        f"R002 含 6 人桌，6 人应可坐下，不应触发，实际 codes={codes}"
    )
