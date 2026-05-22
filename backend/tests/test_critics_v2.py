"""tests.test_critics_v2 —— Pydantic AI ReAct Agent critic 兜底层单元测试。

覆盖 7 项契约：
1. 合法 itinerary → 空 violations
2. STAGES_INCOMPLETE：段数 < 5 → critical
3. DURATION_OUT_OF_RANGE：超出 ±30min 容差 → critical（高低两端各一）
4. TIMELINE_INCONSISTENT：段时间反序/重叠 → critical
5. format_violations_for_llm：仅 critical 进 prompt；0 critical → 空字符串
6. DIETARY_VIOLATION：用餐 stage 餐厅 tags 不覆盖意图 → warning
7. RESTAURANT_FULL_UNRESOLVED demo-aware：用餐 17:00 + 开关 → critical；关 → 不触发

设计原则：
- 用真 mock_data（R001 = 低脂餐厅 / R002 = 粤菜餐厅，R001 离家 0.6km 在 5km 内）
- 不调 LLM（critic 是算法）
- 不依赖任何 LLM_PROVIDER 设置
"""

from __future__ import annotations

import os

import pytest

from agent.v2.critics_v2 import (
    Severity,
    Violation,
    ViolationCode,
    format_violations_for_llm,
    validate_itinerary,
)
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary, ItineraryStage, OrderRecord


# ============================================================
# fixture builders
# ============================================================

def _make_intent(
    *,
    duration_hours: list[int] = [4, 6],
    distance_max_km: float = 5.0,
    dietary_constraints: list[str] | None = None,
    social_context: str = "家庭日常",
) -> IntentExtraction:
    """构造一个最小合法 intent。companions = [] 因测试不关心 social 细节。"""
    return IntentExtraction(
        start_time="2026-05-17T14:00",
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
    dining_start: str = "17:30",
    restaurant_id: str = "R001",  # R001 含「低脂」tag，距家 0.6km
    total_minutes: int = 300,  # 5h 落在 [4-0.5, 6+0.5] 容差区间
) -> Itinerary:
    """5 段标准合法行程。

    14:00 出发 → 14:30 主活动 → 16:00 转场 → dining_start 用餐 → 19:00 返回

    注意：返回段 start 故意比用餐 end 多 10min buffer，让 commute critic
    （R001→home taxi=7min）能通过验证。
    """
    # 计算用餐结束时间 = start + 60min
    s_h, s_m = (int(x) for x in dining_start.split(":"))
    end_total = s_h * 60 + s_m + 60
    e_h, e_m = end_total // 60, end_total % 60
    dining_end = f"{e_h:02d}:{e_m:02d}"

    # 返回段加 10min buffer 兼容 commute critic
    return_start_total = end_total + 10
    rs_h, rs_m = return_start_total // 60, return_start_total % 60
    return_start = f"{rs_h:02d}:{rs_m:02d}"

    return Itinerary(
        summary="家庭半日方案（测试）",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:30", title="武林广场出发"),
            ItineraryStage(
                kind="主活动",
                start="14:30",
                end="16:00",
                title="亲子游玩 · 测试 POI",
                poi_id=None,
            ),
            ItineraryStage(kind="转场", start="16:00", end=dining_start, title="散步前往餐厅"),
            ItineraryStage(
                kind="用餐",
                start=dining_start,
                end=dining_end,
                title="健康轻食",
                restaurant_id=restaurant_id,
            ),
            ItineraryStage(kind="返回", start=return_start, end="19:00", title="打车回家"),
        ],
        orders=[],
        total_minutes=total_minutes,
    )


# ============================================================
# 测试 1：合法 itinerary 不触发 critical
# ============================================================

def test_legal_itinerary_no_critical_violations():
    """5 段合法行程 + matching intent → 无 critical violation。

    注意：可能产生 warning（如 R001 距家 0.6km 是 OK 的，应该零 warning）。
    若 demo full check 误触发会失败 —— 用 17:30 用餐时间避开。
    """
    intent = _make_intent()
    itinerary = _make_legal_itinerary(dining_start="17:30")

    violations = validate_itinerary(itinerary, intent)
    critical = [v for v in violations if v.severity == Severity.CRITICAL]

    assert critical == [], f"合法 itinerary 不应有 critical violation，实际：{critical}"


# ============================================================
# 测试 2：STAGES_INCOMPLETE
# ============================================================

def test_stages_incomplete_triggers_critical():
    """stages 长度 = 3 → 必含 STAGES_INCOMPLETE critical。"""
    intent = _make_intent()
    itinerary = Itinerary(
        summary="只有 3 段",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:30", title="出发"),
            ItineraryStage(kind="主活动", start="14:30", end="16:00", title="看展"),
            ItineraryStage(kind="返回", start="16:00", end="16:30", title="回家"),
        ],
        total_minutes=150,  # 在容差内（[4*60-30=210, 6*60+30=390]）—— 等等，150 < 210 也会触发 duration
        # 但本测试只关心 STAGES_INCOMPLETE，duration 触发是预期附带产物
    )

    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations if v.severity == Severity.CRITICAL]

    assert ViolationCode.STAGES_INCOMPLETE in codes, (
        f"应触发 STAGES_INCOMPLETE，实际 codes={codes}"
    )


# ============================================================
# 测试 3：DURATION_OUT_OF_RANGE（高低两端）
# ============================================================

def test_duration_too_long_triggers_critical():
    """duration_hours=[4,6] / total_minutes=480 → 480 > 6*60+30=390 → critical。"""
    intent = _make_intent(duration_hours=[4, 6])
    itinerary = _make_legal_itinerary(total_minutes=480)

    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations if v.severity == Severity.CRITICAL]

    assert ViolationCode.DURATION_OUT_OF_RANGE in codes, (
        f"应触发 DURATION_OUT_OF_RANGE（过长），实际 codes={codes}"
    )


def test_duration_too_short_triggers_critical():
    """duration_hours=[4,6] / total_minutes=60 → 60 < 4*60-30=210 → critical。"""
    intent = _make_intent(duration_hours=[4, 6])
    itinerary = _make_legal_itinerary(total_minutes=60)

    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations if v.severity == Severity.CRITICAL]

    assert ViolationCode.DURATION_OUT_OF_RANGE in codes, (
        f"应触发 DURATION_OUT_OF_RANGE（过短），实际 codes={codes}"
    )


# ============================================================
# 测试 4：TIMELINE_INCONSISTENT
# ============================================================

def test_timeline_inconsistent_triggers_critical():
    """stages[1].start (13:30) 比 stages[0].end (14:30) 早 60 分钟（> 5min 容差）→ critical。"""
    intent = _make_intent()
    itinerary = Itinerary(
        summary="时间错乱",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:30", title="出发"),
            # 故意反序：13:30 早于上一段 14:30
            ItineraryStage(kind="主活动", start="13:30", end="15:00", title="时间错乱"),
            ItineraryStage(kind="转场", start="15:00", end="17:30", title="转场"),
            ItineraryStage(
                kind="用餐",
                start="17:30",
                end="18:30",
                title="用餐",
                restaurant_id="R001",
            ),
            ItineraryStage(kind="返回", start="18:30", end="19:00", title="回家"),
        ],
        total_minutes=300,
    )

    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations if v.severity == Severity.CRITICAL]

    assert ViolationCode.TIMELINE_INCONSISTENT in codes, (
        f"应触发 TIMELINE_INCONSISTENT，实际 codes={codes}"
    )


# ============================================================
# 测试 5：format_violations_for_llm
# ============================================================

def test_format_violations_only_critical_in_message():
    """1 critical + 1 warning → 输出仅含 critical。"""
    critical_v = Violation(
        code=ViolationCode.STAGES_INCOMPLETE,
        severity=Severity.CRITICAL,
        message="缺少用餐段",
        field_path="stages[*].kind",
    )
    warning_v = Violation(
        code=ViolationCode.DISTANCE_EXCEEDED,
        severity=Severity.WARNING,
        message="距离 6km 超出",
        field_path="stages[2]",
    )
    msg = format_violations_for_llm([critical_v, warning_v])

    assert "缺少用餐段" in msg, "critical 必须进消息"
    assert "距离 6km 超出" not in msg, "warning 不应进消息"
    assert "1 处违规" in msg or "有 1 处" in msg, f"应标注 critical 数量为 1，实际：\n{msg}"
    assert "stages[*].kind" in msg, "field_path 必须出现在消息中"


def test_format_violations_empty_when_no_critical():
    """0 critical（空 / 全是 warning）→ 输出空字符串。"""
    assert format_violations_for_llm([]) == ""

    only_warning = Violation(
        code=ViolationCode.DISTANCE_EXCEEDED,
        severity=Severity.WARNING,
        message="略超",
        field_path="stages[1]",
    )
    assert format_violations_for_llm([only_warning]) == "", (
        "全 warning 时应返回空字符串"
    )


# ============================================================
# 测试 6：DIETARY_VIOLATION（用真 mock 数据）
# ============================================================

def test_dietary_violation_warning_when_restaurant_tags_miss():
    """intent dietary=['低脂'] / 用餐指向 R002（粤菜，无低脂 tag） → warning。"""
    intent = _make_intent(dietary_constraints=["低脂"])
    # R002 = 粤味轩，tags 不含「低脂」
    itinerary = _make_legal_itinerary(restaurant_id="R002")

    violations = validate_itinerary(itinerary, intent)
    dietary_warnings = [
        v for v in violations
        if v.code == ViolationCode.DIETARY_VIOLATION and v.severity == Severity.WARNING
    ]

    assert dietary_warnings, (
        f"R002 不含低脂 tag 应触发 DIETARY_VIOLATION warning，实际 violations={[v.code for v in violations]}"
    )


def test_dietary_violation_no_trigger_when_tag_match():
    """intent dietary=['低脂'] / 用餐指向 R001（含低脂 tag） → 不触发 dietary。"""
    intent = _make_intent(dietary_constraints=["低脂"])
    itinerary = _make_legal_itinerary(restaurant_id="R001")  # R001 = 轻语沙拉，含低脂

    violations = validate_itinerary(itinerary, intent)
    dietary_codes = [v.code for v in violations if v.code == ViolationCode.DIETARY_VIOLATION]

    assert not dietary_codes, (
        f"R001 含低脂 tag 不应触发 DIETARY_VIOLATION，实际：{dietary_codes}"
    )


# ============================================================
# 测试 7：RESTAURANT_FULL_UNRESOLVED demo-aware
# ============================================================

def test_demo_full_check_enabled_triggers_at_17_00(monkeypatch):
    """ENABLE_DEMO_FULL_CHECK=1（默认）+ 用餐 stage start=17:00 → critical。"""
    monkeypatch.setenv("ENABLE_DEMO_FULL_CHECK", "1")
    intent = _make_intent()
    itinerary = _make_legal_itinerary(dining_start="17:00")

    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations if v.severity == Severity.CRITICAL]

    assert ViolationCode.RESTAURANT_FULL_UNRESOLVED in codes, (
        f"开关开 + 17:00 应触发 RESTAURANT_FULL_UNRESOLVED，实际 codes={codes}"
    )


def test_demo_full_check_disabled_no_trigger_at_17_00(monkeypatch):
    """ENABLE_DEMO_FULL_CHECK=0 + 用餐 stage start=17:00 → 不触发。"""
    monkeypatch.setenv("ENABLE_DEMO_FULL_CHECK", "0")
    intent = _make_intent()
    itinerary = _make_legal_itinerary(dining_start="17:00")

    violations = validate_itinerary(itinerary, intent)
    codes = [v.code for v in violations]

    assert ViolationCode.RESTAURANT_FULL_UNRESOLVED not in codes, (
        f"开关关时不应触发 RESTAURANT_FULL_UNRESOLVED，实际 codes={codes}"
    )
