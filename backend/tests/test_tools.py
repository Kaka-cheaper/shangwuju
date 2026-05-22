"""tests.test_tools —— 7 个 Tool 的成功 + 失败分支覆盖。

每个 Tool 至少 2 个用例（一成功一失败），并把演示场景集 §四 的覆盖率自检也
转成断言，确保 Mock 数据始终满足 Demo 跑通的最低门槛。
"""

from __future__ import annotations

import pytest

import tools  # noqa: F401  触发注册
from data.loader import load_pois, load_restaurants
from schemas.errors import FailureReason
from schemas.tools import (
    BuyTicketInput,
    CheckRestaurantAvailabilityInput,
    EstimateRouteTimeInput,
    GenerateShareMessageInput,
    GetUserProfileInput,
    ReserveRestaurantInput,
    SearchPoisInput,
    SearchRestaurantsInput,
)
from tools import TOOL_REGISTRY, invoke_tool
from tools.buy_ticket import buy_ticket
from tools.check_restaurant_availability import check_restaurant_availability
from tools.estimate_route_time import estimate_route_time
from tools.generate_share_message import generate_share_message
from tools.get_user_profile import get_user_profile
from tools.reserve_restaurant import reserve_restaurant
from tools.search_pois import search_pois
from tools.search_restaurants import search_restaurants


# ============================================================
# T0 注册表元测试
# ============================================================

def test_registry_contains_seven_tools():
    expected = {
        "search_pois",
        "search_restaurants",
        "check_restaurant_availability",
        "estimate_route_time",
        "reserve_restaurant",
        "buy_ticket",
        "generate_share_message",
        "get_user_profile",
    }
    assert expected.issubset(set(TOOL_REGISTRY)), (
        f"缺失 Tool: {expected - set(TOOL_REGISTRY)}"
    )


def test_invoke_tool_drift_field_caught():
    """LLM 漂移字段（写错参数名）应被 Pydantic 拦截。"""
    res = invoke_tool("search_pois", {"max_distance": 5})  # 字段名错
    assert not res.success
    assert res.reason == FailureReason.INVALID_INPUT


# ============================================================
# T1 search_pois
# ============================================================

def test_search_pois_family_success():
    """S1 家庭主场景：亲子友好 + 适合 5-10 岁，应有 ≥4 候选。"""
    out = search_pois(
        SearchPoisInput(
            distance_max_km=5,
            physical_constraints=["亲子友好", "适合 5-10 岁"],
            social_context="家庭日常",
        )
    )
    assert out.success
    assert len(out.candidates) >= 4
    for poi in out.candidates:
        assert "亲子友好" in poi.tags
        assert "适合 5-10 岁" in poi.tags
        assert "家庭日常" in poi.suitable_for
        assert poi.distance_km <= 5


def test_search_pois_distance_too_strict_empty():
    out = search_pois(
        SearchPoisInput(
            distance_max_km=0.1,  # 0.1km 内基本不可能有 POI
            physical_constraints=["亲子友好"],
        )
    )
    assert not out.success
    assert out.reason == FailureReason.EMPTY_CANDIDATES


def test_search_pois_age_filter_excludes_out_of_range():
    """age_in_party=[5] 时候选必须满足 age_range[0] <= 5 <= age_range[1]。"""
    out = search_pois(
        SearchPoisInput(
            distance_max_km=5,
            physical_constraints=["亲子友好"],
            age_in_party=[5],
        )
    )
    assert out.success
    for poi in out.candidates:
        if poi.age_range:
            lo, hi = poi.age_range
            assert lo <= 5 <= hi


# ============================================================
# T2 search_restaurants
# ============================================================

def test_search_restaurants_family_success():
    out = search_restaurants(
        SearchRestaurantsInput(
            distance_max_km=5,
            dietary_constraints=["低脂", "健康轻食"],
            social_context="家庭日常",
        )
    )
    assert out.success
    assert len(out.candidates) >= 2
    for r in out.candidates:
        assert "低脂" in r.tags and "健康轻食" in r.tags


def test_search_restaurants_capacity_six_filter():
    """S8 跨代际：要求 6 人桌 + 粤菜。"""
    out = search_restaurants(
        SearchRestaurantsInput(
            distance_max_km=5,
            dietary_constraints=["粤菜"],
            capacity_requirement=6,
        )
    )
    assert out.success
    assert len(out.candidates) >= 2
    for r in out.candidates:
        assert r.capacity.six is True


def test_search_restaurants_private_room_required():
    out = search_restaurants(
        SearchRestaurantsInput(
            distance_max_km=5,
            dietary_constraints=["高人均", "有包间"],
            require_private_room=True,
        )
    )
    assert out.success
    for r in out.candidates:
        assert r.capacity.private_room is True


def test_search_restaurants_empty_when_overconstrained():
    """互斥饮食 tag 组合 → tag relaxation 应丢弃一个再返回候选 + relaxed_tags 非空。

    Step 6 之前：硬过滤打到 0 → success=False。
    Step 6 之后：相互排斥的 tag 会被渐进放宽，至少有候选返回，但 relaxed_tags 暴露
    "我放弃了哪些 tag"，让 LLM 知道并在 rationale 解释。
    """
    out = search_restaurants(
        SearchRestaurantsInput(
            distance_max_km=5,
            dietary_constraints=["粤菜", "下午茶"],  # 互斥组合
        )
    )
    # 新行为：放宽后仍有候选 + relaxed_tags 非空
    if out.success:
        assert out.relaxed_tags, (
            "互斥 tag 组合应被 relax_tag_search 至少丢一个，relaxed_tags 应非空"
        )
    else:
        # 极端情况：mock 数据更新后即使放宽 3 级也找不到 → 这也算合法（保留旧契约）
        assert out.reason == FailureReason.EMPTY_CANDIDATES


# ============================================================
# T3 check_restaurant_availability
# ============================================================

def test_check_availability_success():
    """R001 17:30 应该可订。"""
    out = check_restaurant_availability(
        CheckRestaurantAvailabilityInput(
            restaurant_id="R001", time="17:30", party_size=3
        )
    )
    assert out.success
    assert out.available is True


def test_check_availability_full_returns_alternative():
    """E1 异常：R001 17:00 满，应给 17:30 作为替代。"""
    out = check_restaurant_availability(
        CheckRestaurantAvailabilityInput(
            restaurant_id="R001", time="17:00", party_size=3
        )
    )
    assert not out.success
    assert out.reason == FailureReason.RESTAURANT_FULL
    assert out.suggested_alternative_time == "17:30"


def test_check_availability_unknown_restaurant():
    out = check_restaurant_availability(
        CheckRestaurantAvailabilityInput(
            restaurant_id="R999", time="17:00"
        )
    )
    assert not out.success
    assert out.reason == FailureReason.NOT_FOUND


# ============================================================
# T4 estimate_route_time
# ============================================================

def test_estimate_route_success():
    out = estimate_route_time(
        EstimateRouteTimeInput(from_location="home", to_location="P001")
    )
    assert out.success
    assert out.route is not None
    assert out.route.taxi_minutes is not None


def test_estimate_route_unknown_pair():
    out = estimate_route_time(
        EstimateRouteTimeInput(from_location="home", to_location="X999")
    )
    assert not out.success
    assert out.reason == FailureReason.NOT_FOUND


# ============================================================
# T5 reserve_restaurant
# ============================================================

def test_reserve_restaurant_success():
    out = reserve_restaurant(
        ReserveRestaurantInput(restaurant_id="R001", time="17:30", party_size=3)
    )
    assert out.success
    assert out.order_id is not None
    assert out.confirmed_time == "17:30"


def test_reserve_restaurant_full_slot():
    out = reserve_restaurant(
        ReserveRestaurantInput(restaurant_id="R001", time="17:00", party_size=3)
    )
    assert not out.success
    assert out.reason == FailureReason.RESTAURANT_FULL


def test_reserve_restaurant_unknown_restaurant():
    out = reserve_restaurant(
        ReserveRestaurantInput(restaurant_id="R999", time="17:00", party_size=2)
    )
    assert not out.success
    assert out.reason == FailureReason.NOT_FOUND


# ============================================================
# T6.5 buy_ticket
# ============================================================

def test_buy_ticket_success():
    """P001 库存 45，买 2 张应成功并返 total_price=160（unit=80）。"""
    out = buy_ticket(BuyTicketInput(poi_id="P001", quantity=2, visitor_ages=[5, 35]))
    assert out.success
    assert out.order_id and out.order_id.startswith("T-P001-")
    assert out.quantity == 2
    assert out.total_price == 160.0


def test_buy_ticket_sold_out():
    """E2 异常：P_SOLD 售罄案例必须触发 TICKET_SOLD_OUT。"""
    out = buy_ticket(BuyTicketInput(poi_id="P_SOLD", quantity=1))
    assert not out.success
    assert out.reason == FailureReason.TICKET_SOLD_OUT
    assert out.order_id is None


def test_buy_ticket_unknown_poi():
    out = buy_ticket(BuyTicketInput(poi_id="P_NONE", quantity=1))
    assert not out.success
    assert out.reason == FailureReason.NOT_FOUND


def test_buy_ticket_invalid_quantity_zero():
    out = buy_ticket(BuyTicketInput(poi_id="P001", quantity=0))
    assert not out.success
    assert out.reason == FailureReason.INVALID_INPUT


def test_buy_ticket_invalid_quantity_over_stock():
    out = buy_ticket(BuyTicketInput(poi_id="P001", quantity=999))
    assert not out.success
    assert out.reason == FailureReason.INVALID_INPUT


def test_buy_ticket_free_poi_zero_total():
    """免费/无 price_range 的 POI 应按 0 元计算（P005 江畔老人公园）。"""
    out = buy_ticket(BuyTicketInput(poi_id="P005", quantity=2))
    assert out.success
    assert out.total_price == 0.0


# ============================================================
# T6 generate_share_message
# ============================================================

def test_generate_share_family_tone():
    out = generate_share_message(
        GenerateShareMessageInput(
            itinerary_summary="14:00 西溪儿童乐园 / 17:30 轻语沙拉用餐",
            social_context="家庭日常",
            audience="妻子",
        )
    )
    assert out.success
    assert out.message is not None
    assert "妻子" in out.message
    assert "亲爱的" in out.message


def test_generate_share_business_tone_no_audience():
    out = generate_share_message(
        GenerateShareMessageInput(
            itinerary_summary="14:00 商务茶室 / 19:00 金樽日料",
            social_context="商务接待",
        )
    )
    assert out.success
    assert out.message is not None
    assert "您好" in out.message


def test_generate_share_blank_summary_invalid():
    out = generate_share_message(
        GenerateShareMessageInput(
            itinerary_summary="   ", social_context="家庭日常"
        )
    )
    assert not out.success
    assert out.reason == FailureReason.INVALID_INPUT


# ============================================================
# T7 get_user_profile
# ============================================================

def test_get_user_profile_success():
    out = get_user_profile(GetUserProfileInput(user_id="demo_user"))
    assert out.success
    assert out.profile is not None
    assert out.profile.user_id == "demo_user"
    assert out.profile.home_location.name


def test_get_user_profile_unknown_user():
    out = get_user_profile(GetUserProfileInput(user_id="someone_else"))
    assert not out.success
    assert out.reason == FailureReason.NOT_FOUND


# ============================================================
# 演示场景集 §四 Mock 覆盖率自检（硬性 gate）
# ============================================================

def _has_all(item_tags, required) -> bool:
    return set(required).issubset(set(item_tags))


def test_coverage_family_pois():
    pois = load_pois()
    matched = [p for p in pois if _has_all(p.tags, ["亲子友好", "适合 5-10 岁"])]
    assert len(matched) >= 4, f"S1 家庭 POI 覆盖不足：{[p.id for p in matched]}"


def test_coverage_friends_restaurants():
    rs = load_restaurants()
    matched = [
        r for r in rs if "网红打卡" in r.tags and r.capacity.four
    ]
    assert len(matched) >= 4


def test_coverage_couple_restaurants():
    rs = load_restaurants()
    matched = [r for r in rs if _has_all(r.tags, ["安静聊天", "亲密情侣"])]
    assert len(matched) >= 3


def test_coverage_elderly():
    pois = load_pois()
    rs = load_restaurants()
    matched_pois = [p for p in pois if _has_all(p.tags, ["适合老人", "无台阶"])]
    matched_rs = [r for r in rs if _has_all(r.tags, ["适合老人", "无台阶"])]
    assert len(matched_pois) >= 3
    assert len(matched_rs) >= 3


def test_coverage_afternoon_tea():
    rs = load_restaurants()
    matched = [r for r in rs if _has_all(r.tags, ["下午茶", "拍照友好"])]
    assert len(matched) >= 3


def test_coverage_business():
    rs = load_restaurants()
    matched = [
        r for r in rs if _has_all(r.tags, ["商务体面", "高人均", "有包间"])
    ]
    assert len(matched) >= 3


def test_coverage_solo():
    pois = load_pois()
    rs = load_restaurants()
    matched_pois = [p for p in pois if "独处舒缓" in p.tags]
    matched_rs = [r for r in rs if "独处舒缓" in r.tags]
    assert len(matched_pois) >= 3
    assert len(matched_rs) >= 2


def test_coverage_birthday_cantonese():
    rs = load_restaurants()
    matched = [
        r for r in rs if "粤菜" in r.tags and r.capacity.six
    ]
    assert len(matched) >= 2


def test_coverage_failure_cases_at_least_eight():
    """埋失败案例（capacity.available_slots=0 或 reservation_slots.available=false）≥ 8 处。"""
    pois = load_pois()
    rs = load_restaurants()
    poi_fails = [p for p in pois if p.capacity.available_slots == 0]
    restaurant_fails = [
        r for r in rs if any(not s.available for s in r.reservation_slots)
    ]
    total = len(poi_fails) + len(restaurant_fails)
    assert total >= 8, (
        f"失败埋点不足 8：POI={[p.id for p in poi_fails]}, "
        f"Restaurant={[r.id for r in restaurant_fails]}"
    )


def test_coverage_pois_at_least_20():
    """D4 mock 规模标准：POI ≥ 20 条。"""
    pois = load_pois()
    assert len(pois) >= 20, f"POI 不足 20：当前 {len(pois)} 条"


def test_coverage_restaurants_at_least_30():
    """D4 mock 规模标准：Restaurant ≥ 30 条。"""
    rs = load_restaurants()
    assert len(rs) >= 30, f"Restaurant 不足 30：当前 {len(rs)} 条"


def test_coverage_healthy_food_at_least_12():
    """D4：健康轻食 cuisine 餐厅 ≥ 12 条（家庭场景主力）。"""
    rs = load_restaurants()
    healthy = [r for r in rs if "健康轻食" in r.tags]
    assert len(healthy) >= 12, f"健康轻食仅 {len(healthy)} 条"


def test_coverage_social_context_breadth():
    """演示场景集 §四：mock 数据要覆盖 6+ 种 social_context。"""
    pois = load_pois()
    rs = load_restaurants()
    poi_ctx = {c for p in pois for c in p.suitable_for}
    rest_ctx = {c for r in rs for c in r.suitable_for}
    assert len(poi_ctx) >= 6, f"POI suitable_for 仅覆盖 {sorted(poi_ctx)}"
    assert len(rest_ctx) >= 6, f"Restaurant suitable_for 仅覆盖 {sorted(rest_ctx)}"


def test_coverage_e2_ticket_sold_out_explicit():
    """演示场景集 §六 加分项：E2 售罄案例必须有显式 P_SOLD 标识。"""
    pois = load_pois()
    p_sold = [p for p in pois if p.id == "P_SOLD"]
    assert len(p_sold) == 1, "缺 P_SOLD 案例（专门给 buy_ticket E2 演示用）"
    assert p_sold[0].capacity.available_slots == 0


# ============================================================
# Phase 0.8.x：扩 mock 后的本地生活类目覆盖（用户提到的猫咖 / 剧本杀 / KTV 等）
# ============================================================


def test_coverage_pois_at_least_35():
    """扩 mock 后：POI ≥ 35 条（演示密度）。"""
    pois = load_pois()
    assert len(pois) >= 35, f"POI 不足 35：当前 {len(pois)} 条"


def test_coverage_restaurants_at_least_40():
    """扩 mock 后：Restaurant ≥ 40 条。"""
    rs = load_restaurants()
    assert len(rs) >= 40, f"Restaurant 不足 40：当前 {len(rs)} 条"


def test_coverage_new_poi_categories():
    """扩 mock 后：以下高价值类目每类 ≥ 1 条。"""
    pois = load_pois()
    types = {p.type for p in pois}
    required = {"猫咖", "剧本杀", "KTV", "电影院", "美甲", "瑜伽馆", "健身房", "主题乐园", "室内运动馆", "livehouse", "酒吧", "烘焙工坊"}
    missing = required - types
    assert not missing, f"缺以下 POI 类目：{sorted(missing)}"


def test_coverage_new_cuisines():
    """扩 mock 后：餐厅 cuisine 覆盖以下高价值菜系。"""
    rs = load_restaurants()
    cuisines = {r.cuisine for r in rs}
    required = {"烧烤", "火锅", "川菜", "东南亚菜", "烘焙甜品", "西餐"}
    missing = required - cuisines
    assert not missing, f"缺以下菜系：{sorted(missing)}"


def test_coverage_evening_dining_slots():
    """扩 mock 后：≥ 5 家餐厅有 19:00 之后的可用 slot（晚饭后社交场景）。"""
    rs = load_restaurants()
    late = [
        r for r in rs
        if any(s.available and s.time >= "19:00" for s in r.reservation_slots)
    ]
    assert len(late) >= 5, f"晚饭后可订餐厅仅 {len(late)} 家"


# ============================================================
# 通过 invoke_tool 的端到端冒烟（保证 OpenAI Function Calling 链路正常）
# ============================================================

def test_invoke_tool_e2e_search_pois():
    res = invoke_tool(
        "search_pois",
        {
            "distance_max_km": 5,
            "physical_constraints": ["亲子友好", "适合 5-10 岁"],
            "social_context": "家庭日常",
        },
    )
    assert res.success
    assert res.duration_ms >= 0
    assert "candidates" in res.output
    assert len(res.output["candidates"]) >= 4


def test_invoke_tool_e2e_check_restaurant_full():
    res = invoke_tool(
        "check_restaurant_availability",
        {"restaurant_id": "R001", "time": "17:00", "party_size": 3},
    )
    assert not res.success
    assert res.reason == FailureReason.RESTAURANT_FULL
