"""tests.test_critics_v2_hop —— Wave 4 Task 5：HOP_INFEASIBLE critic 单测（edge_v1）。

旧文件 `test_critics_v2_commute.py` 验「相邻 stage 间累积通勤」。重构后通勤是 hop，
critic 直接看 `hop.minutes` vs `lookup_hop(from_target_id, to_target_id, ...)` 实际值。

【4 项契约】

```
| Test                                | hop.minutes | actual_min | 期望                         |
|-------------------------------------|-------------|------------|------------------------------|
| 1. legal_hop_no_violation           | 9           | 9          | 0 violation                  |
| 2. hop_minutes_too_small_critical    | 3           | 9          | 1 critical (HOP_INFEASIBLE)  |
| 3. in_place_hop_skipped             | 0           | -          | 不调 lookup_hop / 0 violation|
| 4. fallback_15min_passes            | 15          | 15 (兜底)  | 0 violation                  |
```

【fixtures 选型】

- mock 路网：home → P040 taxi 9min / P040 → R001 taxi 5min / R001 → home taxi 7min
- 默认 demo_user transport_preference = "taxi"
- 4 nodes（home, P040, R001, home）+ 3 hops 是最小标准合法行程

【过渡态桥】

`agent/__init__.py` 仍 eager-import 损坏的 `planner.py`（待 Task 9 修），所以测试
顶部把 `agent` 注册为空命名空间包，让子模块 `agent.v2.critics_v2` / `agent.lookup_hop`
能直接 import 而不触发 __init__.py 副作用。删除时机：Task 9 完成后。

不写：fix 行为（仅断言 critic 触发，不修复实现）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Optional

import pytest


# ============================================================
# 过渡态桥：旁路 agent/__init__.py 的损坏 eager-import
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.planning.critic.critics_v2 import (  # noqa: E402
    Severity,
    ViolationCode,
    _check_hop_feasibility,
)
from data.loader import load_user_profile  # noqa: E402
from schemas.intent import IntentExtraction  # noqa: E402
from schemas.itinerary import ActivityNode, Hop, Itinerary  # noqa: E402


# ============================================================
# fixtures
# ============================================================


@pytest.fixture
def profile():
    """demo_user 画像（transport_preference=taxi，含 home_location 坐标）。"""
    return load_user_profile()


def _make_intent() -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=[4, 6],  # type: ignore[arg-type]
        distance_max_km=10.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="测试 hop critic",
        parse_confidence=0.9,
    )


def _make_node(
    *,
    node_id: str,
    target_kind: str,
    target_id: str,
    start_time: str,
    duration_min: int,
    title: str,
    kind: str = "活动",
) -> ActivityNode:
    return ActivityNode(
        node_id=node_id,
        kind=kind,
        target_kind=target_kind,  # type: ignore[arg-type]
        target_id=target_id,
        start_time=start_time,
        duration_min=duration_min,
        title=title,
    )


def _make_home_node(node_id: str, start_time: str, title: str) -> ActivityNode:
    return _make_node(
        node_id=node_id,
        target_kind="home",
        target_id="home",
        start_time=start_time,
        duration_min=0,
        title=title,
        kind="起点" if node_id == "n0" else "终点",
    )


def _make_hop(
    *,
    hop_id: str,
    from_node_id: str,
    to_node_id: str,
    start_time: str,
    minutes: int,
    mode: str = "taxi",
    path_type: str = "real_route",
    buffer_min: int = 0,
) -> Hop:
    return Hop(
        hop_id=hop_id,
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        start_time=start_time,
        minutes=minutes,
        mode=mode,  # type: ignore[arg-type]
        path_type=path_type,  # type: ignore[arg-type]
        buffer_min=buffer_min,
    )


def _filter_hop(violations):
    return [v for v in violations if v.code == ViolationCode.HOP_INFEASIBLE]


# ============================================================
# Test 1：legal hop（actual_min=9, hop.minutes=9）→ 0 violation
# ============================================================


def test_legal_hop_passes_no_violation(profile):
    """home → P040 taxi 实际 9min，hop.minutes=9 严格自洽 → 不触发 HOP_INFEASIBLE。"""
    nodes = [
        _make_home_node("n0", "14:00", "出发"),
        _make_node(
            node_id="n1",
            target_kind="poi",
            target_id="P040",
            start_time="14:09",
            duration_min=120,
            title="童趣海洋亲子馆",
            kind="主活动",
        ),
        _make_node(
            node_id="n2",
            target_kind="restaurant",
            target_id="R001",
            start_time="16:14",  # 14:09+120=16:09 → +5min taxi → +5min buffer
            duration_min=60,
            title="轻语沙拉",
            kind="用餐",
        ),
        _make_home_node("n3", "17:21", "回家"),  # 17:14+7min=17:21
    ]
    hops = [
        _make_hop(
            hop_id="h0",
            from_node_id="n0",
            to_node_id="n1",
            start_time="14:00",
            minutes=9,  # ← 与 lookup_hop(home, P040, taxi) actual=9 完全一致
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
        _make_hop(
            hop_id="h1",
            from_node_id="n1",
            to_node_id="n2",
            start_time="16:09",
            minutes=5,  # P040→R001 taxi=5min
            mode="taxi",
            path_type="real_route",
            buffer_min=5,
        ),
        _make_hop(
            hop_id="h2",
            from_node_id="n2",
            to_node_id="n3",
            start_time="17:14",
            minutes=7,  # R001→home taxi=7min
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
    ]
    itinerary = Itinerary(
        summary="legal hops",
        nodes=nodes,
        hops=hops,
        total_minutes=201,
    )

    violations = _check_hop_feasibility(itinerary, user_profile=profile)
    assert violations == [], f"完全自洽的 hop 不应触发 HOP_INFEASIBLE：{violations}"


# ============================================================
# Test 2：hop.minutes 偏小 6min（容差 2min）→ critical
# ============================================================


def test_hop_minutes_too_small_triggers_critical(profile):
    """home → P040 实际 taxi 9min，hop 故意只填 3min（偏小 6min > 容差 2）→ CRITICAL。"""
    nodes = [
        _make_home_node("n0", "14:00", "出发"),
        _make_node(
            node_id="n1",
            target_kind="poi",
            target_id="P040",
            start_time="14:03",  # cursor 配合 hop.minutes=3 推进
            duration_min=120,
            title="童趣海洋亲子馆",
            kind="主活动",
        ),
        _make_node(
            node_id="n2",
            target_kind="restaurant",
            target_id="R001",
            start_time="16:08",
            duration_min=60,
            title="轻语沙拉",
            kind="用餐",
        ),
        _make_home_node("n3", "17:15", "回家"),
    ]
    hops = [
        _make_hop(
            hop_id="h0",
            from_node_id="n0",
            to_node_id="n1",
            start_time="14:00",
            minutes=3,  # ← 故意偏小，actual=9 → 缺 6min
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
        _make_hop(
            hop_id="h1",
            from_node_id="n1",
            to_node_id="n2",
            start_time="16:03",
            minutes=5,
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
        _make_hop(
            hop_id="h2",
            from_node_id="n2",
            to_node_id="n3",
            start_time="17:08",
            minutes=7,
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
    ]
    itinerary = Itinerary(
        summary="hop minutes too small",
        nodes=nodes,
        hops=hops,
        total_minutes=195,
    )

    violations = _check_hop_feasibility(itinerary, user_profile=profile)
    hop_v = _filter_hop(violations)

    assert hop_v, f"hop.minutes=3 < actual=9 - 容差 2 = 7，应触发 CRITICAL，实际：{violations}"
    assert all(v.severity == Severity.HARD for v in hop_v)
    msg = hop_v[0].message
    # 人话约束：消息里应含具体分钟、目标点标题，不暴露 dot-path
    assert "9" in msg, f"消息应含 actual_min=9：{msg}"
    assert "3" in msg, f"消息应含 hop.minutes=3：{msg}"
    assert "童趣海洋亲子馆" in msg or "P040" in msg, f"消息应含目标点：{msg}"
    assert "hops[0]" not in msg, f"消息不应暴露 dot-path：{msg}"


# ============================================================
# Test 3：in_place hop（path_type="in_place"）→ 跳过 lookup_hop
# ============================================================


def test_in_place_hop_skipped(profile, monkeypatch):
    """同地复用的 hop（path_type=in_place）应直接跳过，不调 lookup_hop。"""
    # 用 monkeypatch 监控 lookup_hop 调用（同地段 hop 不应触发它）
    from agent.planning.critic import critics_v2 as critic_mod

    call_log: list[tuple] = []
    real_lookup = critic_mod.lookup_hop

    def spy_lookup(from_id, to_id, transport_pref, user_profile):
        call_log.append((from_id, to_id))
        return real_lookup(from_id, to_id, transport_pref, user_profile)

    monkeypatch.setattr(critic_mod, "lookup_hop", spy_lookup)

    nodes = [
        _make_home_node("n0", "14:00", "出发"),
        _make_node(
            node_id="n1",
            target_kind="poi",
            target_id="P040",
            start_time="14:09",
            duration_min=60,
            title="童趣海洋亲子馆 · 看展",
            kind="主活动",
        ),
        _make_node(
            node_id="n2",  # 同 P040，同地复用
            target_kind="poi",
            target_id="P040",
            start_time="15:09",
            duration_min=60,
            title="童趣海洋亲子馆 · 互动",
            kind="主活动",
        ),
        _make_home_node("n3", "16:18", "回家"),
    ]
    hops = [
        _make_hop(
            hop_id="h0",
            from_node_id="n0",
            to_node_id="n1",
            start_time="14:00",
            minutes=9,
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
        _make_hop(
            hop_id="h1",
            from_node_id="n1",
            to_node_id="n2",
            start_time="15:09",
            minutes=0,  # ← 同地复用 hop
            mode="virtual",
            path_type="in_place",
            buffer_min=0,
        ),
        _make_hop(
            hop_id="h2",
            from_node_id="n2",
            to_node_id="n3",
            start_time="16:09",
            minutes=9,  # P040 → home haversine ~ 9min
            mode="haversine_estimated",
            path_type="estimated",
            buffer_min=0,
        ),
    ]
    itinerary = Itinerary(
        summary="in_place reuse",
        nodes=nodes,
        hops=hops,
        total_minutes=138,
    )

    violations = _check_hop_feasibility(itinerary, user_profile=profile)
    hop_v = _filter_hop(violations)

    assert not hop_v, f"in_place hop 不应触发 HOP_INFEASIBLE：{hop_v}"
    # 只有 hop[0] hop[2] 应被 lookup_hop 调用，hop[1] 被跳过
    assert ("P040", "P040") not in call_log, (
        f"in_place hop 不应调 lookup_hop，实际调用：{call_log}"
    )
    # 至少 hop[0] / hop[2] 各调一次
    assert ("home", "P040") in call_log
    assert ("P040", "home") in call_log


# ============================================================
# Test 4：lookup_hop 4 级兜底（GHOST→GHOST）→ hop.minutes=15 应通过
# ============================================================


def test_fallback_15min_passes(profile):
    """两端 target_id 都不存在（mock 数据完全缺失）→ lookup_hop 返 15min 兜底；
    hop.minutes=15 严格匹配兜底值 → 0 violation。
    """
    nodes = [
        _make_home_node("n0", "14:00", "出发"),
        _make_node(
            node_id="n1",
            target_kind="poi",
            target_id="GHOST_X",  # mock 不存在
            start_time="14:15",
            duration_min=60,
            title="幽灵 POI",
            kind="主活动",
        ),
        _make_node(
            node_id="n2",
            target_kind="poi",
            target_id="GHOST_Y",  # mock 不存在
            start_time="15:30",
            duration_min=60,
            title="另一个幽灵",
            kind="主活动",
        ),
        _make_home_node("n3", "16:45", "回家"),
    ]
    hops = [
        _make_hop(
            hop_id="h0",
            from_node_id="n0",
            to_node_id="n1",
            start_time="14:00",
            minutes=15,  # 4 级兜底也是 15min
            mode="taxi",
            path_type="estimated",
            buffer_min=0,
        ),
        _make_hop(
            hop_id="h1",
            from_node_id="n1",
            to_node_id="n2",
            start_time="15:15",
            minutes=15,  # GHOST_X → GHOST_Y → 4 级兜底 15min
            mode="taxi",
            path_type="estimated",
            buffer_min=0,
        ),
        _make_hop(
            hop_id="h2",
            from_node_id="n2",
            to_node_id="n3",
            start_time="16:30",
            minutes=15,
            mode="taxi",
            path_type="estimated",
            buffer_min=0,
        ),
    ]
    itinerary = Itinerary(
        summary="fallback 15min",
        nodes=nodes,
        hops=hops,
        total_minutes=165,
    )

    violations = _check_hop_feasibility(itinerary, user_profile=profile)
    assert violations == [], (
        f"hop.minutes=15 与 lookup_hop 4 级兜底 actual=15 一致，不应触发 HOP_INFEASIBLE：{violations}"
    )


# ============================================================
# 兜底测试：profile 缺失时不误伤
# ============================================================


def test_no_profile_skips_check():
    """user_profile=None 时整个 critic 跳过（数据缺失不应误伤）。"""
    nodes = [
        _make_home_node("n0", "14:00", "出发"),
        _make_node(
            node_id="n1",
            target_kind="poi",
            target_id="P040",
            start_time="14:01",
            duration_min=60,
            title="童趣海洋亲子馆",
            kind="主活动",
        ),
        _make_home_node("n2", "15:01", "回家"),
    ]
    hops = [
        _make_hop(
            hop_id="h0",
            from_node_id="n0",
            to_node_id="n1",
            start_time="14:00",
            minutes=1,  # 故意离谱小，但没 profile 不应报
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
        _make_hop(
            hop_id="h1",
            from_node_id="n1",
            to_node_id="n2",
            start_time="15:00",
            minutes=1,
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
    ]
    itinerary = Itinerary(
        summary="no profile",
        nodes=nodes,
        hops=hops,
        total_minutes=61,
    )

    violations = _check_hop_feasibility(itinerary, user_profile=None)
    assert violations == [], f"profile=None 时应跳过，实际：{violations}"


# ============================================================
# Test 6：虚高方向 SOFT tripwire（四条不变式批 I2 · C8，2026-07-11）
# hop.minutes 比实际值多超容差 → SOFT（只告知不 gate）；
# 正常拼装路径（assemble 与 critic 共用同一 lookup_hop）数学上不可能触发，
# 本组测试就是"钉住测试里手造 hop 的诚实性"这层 tripwire 价值本身。
# ============================================================


def _upper_bound_itinerary(profile, h0_minutes: int) -> Itinerary:
    """home→P040 实际 taxi 9min，h0.minutes 由参数给（用于上界正反例）。"""
    nodes = [
        _make_home_node("n0", "14:00", "出发"),
        _make_node(
            node_id="n1",
            target_kind="poi",
            target_id="P040",
            start_time=f"14:{h0_minutes:02d}",
            duration_min=120,
            title="童趣海洋亲子馆",
            kind="主活动",
        ),
        _make_home_node("n2", "16:30", "回家"),
    ]
    hops = [
        _make_hop(
            hop_id="h0",
            from_node_id="n0",
            to_node_id="n1",
            start_time="14:00",
            minutes=h0_minutes,
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
        _make_hop(
            hop_id="h1",
            from_node_id="n1",
            to_node_id="n2",
            start_time=f"16:{h0_minutes:02d}",
            minutes=9,  # P040→home 反向：hangzhou routes 有 P040→home 9min 真值
            mode="taxi",
            path_type="real_route",
            buffer_min=0,
        ),
    ]
    return Itinerary(
        summary="upper bound probe",
        nodes=nodes,
        hops=hops,
        total_minutes=150,
    )


def test_hop_minutes_inflated_triggers_soft(profile):
    """hop.minutes=15 vs actual=9（虚高 6 > 容差 2）→ SOFT（只告知不 gate）。"""
    itinerary = _upper_bound_itinerary(profile, h0_minutes=15)
    violations = _check_hop_feasibility(itinerary, user_profile=profile)
    hop_v = _filter_hop(violations)
    assert hop_v, f"虚高 6min 应触发 SOFT tripwire，实际：{violations}"
    assert all(v.severity == Severity.SOFT for v in hop_v), (
        f"虚高方向必须是 SOFT（判 HARD 会让恢复的旧会话反馈轮全灭）：{hop_v}"
    )
    msg = hop_v[0].message
    assert "9" in msg and "15" in msg, f"消息应含实际值与标注值：{msg}"
    assert "hops[0]" not in msg, f"消息不应暴露 dot-path：{msg}"


def test_hop_minutes_within_upper_tolerance_passes(profile):
    """hop.minutes=11 vs actual=9（虚高 2 == 容差）→ 不触发（边界在容差内）。"""
    itinerary = _upper_bound_itinerary(profile, h0_minutes=11)
    violations = _check_hop_feasibility(itinerary, user_profile=profile)
    assert _filter_hop(violations) == [], (
        f"容差内（+2min）不应触发：{violations}"
    )
