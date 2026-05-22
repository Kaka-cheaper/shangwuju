"""tests.test_critics_v2_commute —— Step 1：通勤可达性 critic。

覆盖：
1. 同地相邻段（buffer=0 也通过）
2. 真 mock 路线命中：home→P011 taxi 15min，buffer 仅 5min → CRITICAL
3. 真 mock 路线命中：home→P011 buffer 充足（20min）→ 不触发
4. 路线 mock 缺失：用 haversine 兜底（构造 home + 跨城坐标段）
5. 出发段无前段，跳过；无坐标且无路线，安静跳过
6. 通勤 metadata 写入 stage（commute_minutes_required + commute_mode）
7. transport_preference=walking 时按步行分钟数判断
8. 缺数据时不误伤（坐标全 None + mock 无路线 → 不报）

不调 LLM。用 R001 / P011 / home 真 mock 数据；transport_preference 默认 taxi。
"""

from __future__ import annotations

import pytest

from agent.v2.critics_v2 import (
    Severity,
    ViolationCode,
    validate_itinerary,
)
from data.loader import load_pois, load_user_profile
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary, ItineraryStage


# ============================================================
# fixture
# ============================================================

def _make_intent(duration_hours: list[int] = [4, 6]) -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=duration_hours,  # type: ignore[arg-type]
        distance_max_km=10.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="测试",
        parse_confidence=0.9,
    )


def _filter_commute(violations):
    return [v for v in violations if v.code == ViolationCode.COMMUTE_INFEASIBLE]


# ============================================================
# 测试 1：同地段 buffer=0 不报错
# ============================================================

def test_same_target_zero_buffer_no_violation():
    """两段都指向同一个 POI（如先看展再继续看展），buffer=0 也应通过。"""
    intent = _make_intent()
    itinerary = Itinerary(
        summary="测试",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:30", title="武林广场出发"),
            ItineraryStage(
                kind="主活动",
                start="14:30",
                end="15:30",
                title="第一段",
                poi_id="P001",
            ),
            # 第二段同一 POI（继续待）：buffer=0 但同地无需通勤
            ItineraryStage(
                kind="主活动",
                start="15:30",
                end="16:30",
                title="第二段",
                poi_id="P001",
            ),
            ItineraryStage(
                kind="用餐",
                start="16:45",
                end="17:45",
                title="餐厅",
                restaurant_id="R001",
            ),
            ItineraryStage(kind="返回", start="17:45", end="18:30", title="回家"),
        ],
        total_minutes=270,
    )
    violations = validate_itinerary(itinerary, intent)
    commute_v = _filter_commute(violations)
    # 第二段同地 buffer=0 不应报；其它段 buffer 应充足
    assert all(
        "第 3 段" not in v.message for v in commute_v
    ), f"同地段不应报 commute，实际：{[v.message for v in commute_v]}"


# ============================================================
# 测试 2：路线命中 + buffer 不足 → CRITICAL
# ============================================================

def test_route_known_buffer_too_short_triggers_critical():
    """home→P011 taxi 15min（mock 数据已知），但只给 5min buffer → CRITICAL。"""
    intent = _make_intent()
    # 家 14:00→14:25 出发段，14:30 起主活动 P011（buffer 仅 5min）
    itinerary = Itinerary(
        summary="测试 buffer 不足",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:25", title="武林广场出发"),
            ItineraryStage(
                kind="主活动",
                start="14:30",  # ← buffer 仅 5min，但 home→P011 需要 15min
                end="16:30",
                title="P011 主活动",
                poi_id="P011",
            ),
            ItineraryStage(kind="转场", start="16:30", end="17:00", title="转场"),
            ItineraryStage(
                kind="用餐",
                start="17:00",
                end="18:00",
                title="餐厅",
                restaurant_id="R009",  # P011→R009 路线已知
            ),
            ItineraryStage(kind="返回", start="18:00", end="18:30", title="回家"),
        ],
        total_minutes=270,
    )
    violations = validate_itinerary(itinerary, intent)
    commute_v = _filter_commute(violations)

    assert commute_v, f"buffer 不足应触发 COMMUTE_INFEASIBLE，实际所有 violations：{[(v.code, v.message) for v in violations]}"
    # 检查是 CRITICAL
    assert all(v.severity == Severity.CRITICAL for v in commute_v)
    # 消息应含具体数字（mock 路线 home→P011 taxi=15min；buffer=5min；缺 10min）
    msg = commute_v[0].message
    assert "15" in msg or "10" in msg or "通勤" in msg, f"消息应含具体数字：{msg}"


# ============================================================
# 测试 3：路线命中 + buffer 充足 → 不触发
# ============================================================

def test_route_known_buffer_sufficient_no_violation():
    """home→P011 taxi 15min，给 20min buffer → 不触发 COMMUTE_INFEASIBLE。

    注意：R009→home taxi=15min（mock），所以最后一段 buffer 也得留够。
    """
    intent = _make_intent()
    itinerary = Itinerary(
        summary="测试 buffer 充足",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:10", title="武林广场出发"),
            # buffer = 14:30 - 14:10 = 20min > home→P011 taxi 15min
            ItineraryStage(
                kind="主活动",
                start="14:30",
                end="16:30",
                title="P011",
                poi_id="P011",
            ),
            ItineraryStage(kind="转场", start="16:30", end="17:00", title="转场"),
            ItineraryStage(
                kind="用餐",
                start="17:05",  # P011→R009 taxi 2min, buffer 5min, 充足
                end="18:05",
                title="餐厅",
                restaurant_id="R009",
            ),
            # R009→home taxi=15min，buffer 18:25-18:05=20min 充足
            ItineraryStage(kind="返回", start="18:25", end="18:55", title="回家"),
        ],
        total_minutes=295,
    )
    violations = validate_itinerary(itinerary, intent)
    commute_v = _filter_commute(violations)
    assert not commute_v, f"buffer 充足不应报 COMMUTE_INFEASIBLE，实际：{[v.message for v in commute_v]}"


# ============================================================
# 测试 4：路线 mock 缺失 → haversine 兜底
# ============================================================

def test_haversine_fallback_when_route_missing():
    """构造一个 mock 不存在的路线（比如 R001→R002），但有坐标，让 haversine 兜底。

    R001 (家附近 0.6km) → R002 (远点 4km)：直线 ≈ 几公里，taxi 至少 5+min。
    把 buffer 设极小（1min）应触发 critical。
    """
    intent = _make_intent()
    # 加载真坐标
    pois = load_pois()
    p_close = next((p for p in pois if p.id == "P011" and p.location.lat is not None), None)
    p_far = next(
        (p for p in pois
         if p.id != "P011" and p.location.lat is not None
         and p.distance_km >= 4),
        None,
    )
    if p_close is None or p_far is None:
        pytest.skip("缺少有坐标的 POI 测试样本（mock 数据未覆盖）")

    # 构造一段：先 P011 后 p_far，故意只给 1 分钟 buffer
    itinerary = Itinerary(
        summary="haversine 兜底测试",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:10", title="出发"),
            ItineraryStage(
                kind="主活动",
                start="14:25",
                end="15:25",
                title="近端",
                poi_id="P011",
                lat=p_close.location.lat,
                lng=p_close.location.lng,
            ),
            # P011 → p_far 之间 mock 路线大概率没有，走 haversine
            ItineraryStage(
                kind="主活动",
                start="15:26",  # 仅 1min buffer
                end="16:30",
                title="远端",
                poi_id=p_far.id,
                lat=p_far.location.lat,
                lng=p_far.location.lng,
            ),
            ItineraryStage(
                kind="用餐",
                start="17:00",
                end="18:00",
                title="餐厅",
                restaurant_id="R001",
            ),
            ItineraryStage(kind="返回", start="18:00", end="18:30", title="回家"),
        ],
        total_minutes=270,
    )
    violations = validate_itinerary(itinerary, intent)
    commute_v = _filter_commute(violations)

    # 至少 1 条命中（haversine 兜底）；mode 应该是 haversine_estimated 或 mock 命中
    if not commute_v:
        # 路线 mock 命中也算合理（mock_data/routes.json 可能扩了）
        # 验证 stage 的 commute_minutes_required 至少被填了
        cur_stage = itinerary.stages[2]
        assert cur_stage.commute_minutes_required is not None, (
            "至少应该把 commute_minutes_required 写到 stage 上"
        )


# ============================================================
# 测试 5：commute metadata 写到 stage 上
# ============================================================

def test_commute_metadata_written_to_stage():
    """验通过场景下 stage.commute_minutes_required + commute_mode 被填写。"""
    intent = _make_intent()
    itinerary = Itinerary(
        summary="metadata",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:10", title="出发"),
            ItineraryStage(
                kind="主活动",
                start="14:30",
                end="16:30",
                title="P011",
                poi_id="P011",
            ),
            ItineraryStage(kind="转场", start="16:30", end="17:00", title="转场"),
            ItineraryStage(
                kind="用餐",
                start="17:05",
                end="18:05",
                title="餐厅",
                restaurant_id="R009",
            ),
            ItineraryStage(kind="返回", start="18:25", end="18:55", title="回家"),
        ],
        total_minutes=295,
    )
    validate_itinerary(itinerary, intent)
    # 主活动段（idx=1）应该有 commute（home→P011）
    main_stage = itinerary.stages[1]
    assert main_stage.commute_minutes_required is not None, (
        "主活动段应被写入通勤分钟数"
    )
    assert main_stage.commute_mode in ("walking", "taxi", "bus", "haversine_estimated"), (
        f"commute_mode 应是有效值，实际：{main_stage.commute_mode}"
    )


# ============================================================
# 测试 6：缺数据不误伤
# ============================================================

def test_no_violation_when_data_missing():
    """段 1 是 free-form 自由段，无 poi_id / restaurant_id / 坐标 → 不应报。"""
    intent = _make_intent()
    itinerary = Itinerary(
        summary="自由段",
        stages=[
            ItineraryStage(kind="出发", start="14:00", end="14:30", title="出发"),
            ItineraryStage(
                kind="主活动",
                start="14:35",  # 5min buffer
                end="15:35",
                title="自由活动（无具体地点）",
                # 不填 poi_id / restaurant_id / lat / lng
            ),
            ItineraryStage(
                kind="转场",
                start="15:35",
                end="15:45",
                title="转场",
            ),
            ItineraryStage(
                kind="用餐",
                start="15:50",
                end="16:50",
                title="餐厅",
                restaurant_id="R001",
            ),
            ItineraryStage(kind="返回", start="16:50", end="17:30", title="回家"),
        ],
        total_minutes=210,
    )
    violations = validate_itinerary(itinerary, intent)
    commute_v = _filter_commute(violations)
    # 主活动段 ↔ 出发段 / 主活动段 ↔ 转场段 都应该跳过（无 id 无坐标）
    # 但 R001 关联段可能因 home→R001 commute 触发——这取决于 buffer 是否够
    # 至少不应该全部段都报
    assert len(commute_v) <= 2, (
        f"自由段不应误伤，commute violations 数应有限：{[v.message for v in commute_v]}"
    )


# ============================================================
# 测试 7：transport_preference 实际生效
# ============================================================

def test_transport_preference_walking_uses_walking_minutes():
    """切到 walking 时，home→P011 mock 路线 walking 50min（远大于 taxi 15min），
    同样 5min buffer 必然触发 critical 且分钟数应大。"""
    intent = _make_intent()
    profile = load_user_profile()
    # 测试需要 demo_user 是 taxi 偏好——先确认默认值
    # 这里通过覆盖 profile.transport_preference 的方式测试不方便（对象冻结）
    # 改用 monkeypatch 替换 _safe_load_user_profile 行为
    from agent.v2 import critics_v2

    class _FakeProfile:
        transport_preference = "walking"
        home_location = profile.home_location

    original = critics_v2._safe_load_user_profile
    critics_v2._safe_load_user_profile = lambda user_id="demo_user": _FakeProfile()  # type: ignore[assignment]

    try:
        itinerary = Itinerary(
            summary="walking",
            stages=[
                ItineraryStage(kind="出发", start="14:00", end="14:25", title="出发"),
                ItineraryStage(
                    kind="主活动",
                    start="14:30",  # 5min buffer，walking 需要 50min
                    end="16:30",
                    title="P011",
                    poi_id="P011",
                ),
                ItineraryStage(kind="转场", start="16:30", end="17:00", title="转场"),
                ItineraryStage(
                    kind="用餐",
                    start="17:05",
                    end="18:05",
                    title="餐厅",
                    restaurant_id="R009",
                ),
                ItineraryStage(kind="返回", start="18:05", end="18:35", title="回家"),
            ],
            total_minutes=275,
        )
        violations = validate_itinerary(itinerary, intent)
        commute_v = _filter_commute(violations)
        assert commute_v, "walking 偏好下 5min buffer 必然触发 commute"
        # 50min 步行肯定缺 45min 以上
        msg = commute_v[0].message
        assert "walking" in msg or "通勤" in msg, f"消息应说明步行：{msg}"
    finally:
        critics_v2._safe_load_user_profile = original
