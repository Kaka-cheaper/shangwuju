"""tests.fake_tools —— 给 Agent 端到端测试用的 Tool 假实现。

W2 阶段 Tool 实现还在 W1 进行中，本模块用最小可行假数据顶替注册到 TOOL_REGISTRY，
让 planner/executor 端到端跑通。

埋点：
- R001 在 17:00 已满 → 触发 E1，Agent 切到 17:30
- P_SOLD 售罄 → 触发 E2（仅在显式调 buy_ticket 时）

不参与生产代码：仅在 conftest.py 调用 register_fake_tools() 才装载。
"""

from __future__ import annotations

from schemas.domain import (
    Location,
    Poi,
    PoiCapacity,
    ReservationSlot,
    Restaurant,
    RestaurantCapacity,
    Route,
    UserProfile,
)
from schemas.errors import FailureReason
from schemas.tools import (
    BuyTicketInput,
    BuyTicketOutput,
    CheckRestaurantAvailabilityInput,
    CheckRestaurantAvailabilityOutput,
    EstimateRouteTimeInput,
    EstimateRouteTimeOutput,
    GenerateShareMessageInput,
    GenerateShareMessageOutput,
    GetUserProfileInput,
    GetUserProfileOutput,
    ReserveRestaurantInput,
    ReserveRestaurantOutput,
    SearchPoisInput,
    SearchPoisOutput,
    SearchRestaurantsInput,
    SearchRestaurantsOutput,
)
from tools.registry import TOOL_REGISTRY, register_tool


# ============================================================
# 假数据
# ============================================================

_FAKE_POIS = [
    Poi(
        id="P001",
        name="森林儿童探索乐园",
        type="亲子乐园",
        location=Location(name="西溪湿地"),
        distance_km=4.2,
        opening_hours="09:00-18:00",
        rating=4.6,
        age_range=[3, 10],
        price_range=[80, 120],
        tags=["亲子友好", "适合 5-10 岁", "户外", "低强度"],
        suitable_for=["家庭日常"],
        capacity=PoiCapacity(daily_quota=200, available_slots=45),
    ),
    Poi(
        id="P002",
        name="儿童科学馆",
        type="科技馆",
        location=Location(name="天目山路"),
        distance_km=3.5,
        opening_hours="09:30-17:30",
        rating=4.5,
        age_range=[4, 12],
        price_range=[40, 60],
        tags=["亲子友好", "适合 5-10 岁", "室内", "学习成长"],
        suitable_for=["家庭日常"],
        capacity=PoiCapacity(daily_quota=300, available_slots=120),
    ),
    Poi(
        id="P_SOLD",
        name="网红绘本馆",
        type="儿童绘本",
        location=Location(name="文一路"),
        distance_km=4.8,
        opening_hours="10:00-21:00",
        rating=4.4,
        age_range=[3, 10],
        price_range=[50, 50],
        tags=["亲子友好", "室内"],
        suitable_for=["家庭日常"],
        capacity=PoiCapacity(daily_quota=80, available_slots=0),
    ),
]


_FAKE_RESTAURANTS = [
    Restaurant(
        id="R001",
        name="轻语沙拉 · 西溪店",
        cuisine="健康轻食",
        location=Location(name="西溪银泰"),
        distance_km=2.1,
        opening_hours="10:30-21:30",
        avg_price=75,
        rating=4.5,
        capacity=RestaurantCapacity.model_validate(
            {"2": True, "4": True, "6": False, "8": False, "private_room": False}
        ),
        reservation_slots=[
            ReservationSlot(time="17:00", available=False, queue_minutes=0),
            ReservationSlot(time="17:30", available=True, queue_minutes=0),
            ReservationSlot(time="18:00", available=True, queue_minutes=5),
        ],
        tags=["低脂", "健康轻食", "有儿童餐", "亲子友好"],
        suitable_for=["家庭日常"],
    ),
    Restaurant(
        id="R002",
        name="原麦山丘 · 健康餐",
        cuisine="健康轻食",
        location=Location(name="武林银泰"),
        distance_km=3.8,
        opening_hours="11:00-21:00",
        avg_price=90,
        rating=4.4,
        capacity=RestaurantCapacity.model_validate(
            {"2": True, "4": True, "6": True, "8": False, "private_room": False}
        ),
        reservation_slots=[
            ReservationSlot(time="17:00", available=True, queue_minutes=0),
            ReservationSlot(time="17:30", available=True, queue_minutes=0),
            ReservationSlot(time="18:00", available=True, queue_minutes=10),
        ],
        tags=["低脂", "健康轻食", "有儿童餐"],
        suitable_for=["家庭日常"],
    ),
]


_FAKE_ROUTES = {
    ("home", "P001"): Route(
        from_location="home", to_location="P001", walking_minutes=None, taxi_minutes=25, bus_minutes=40
    ),
    ("home", "P002"): Route(
        from_location="home", to_location="P002", walking_minutes=None, taxi_minutes=20, bus_minutes=35
    ),
    ("P001", "R001"): Route(
        from_location="P001", to_location="R001", walking_minutes=18, taxi_minutes=8, bus_minutes=15
    ),
    ("P002", "R001"): Route(
        from_location="P002", to_location="R001", walking_minutes=22, taxi_minutes=10, bus_minutes=18
    ),
    ("P001", "R002"): Route(
        from_location="P001", to_location="R002", walking_minutes=None, taxi_minutes=12, bus_minutes=22
    ),
    ("R001", "home"): Route(
        from_location="R001", to_location="home", walking_minutes=None, taxi_minutes=10, bus_minutes=20
    ),
    ("R002", "home"): Route(
        from_location="R002", to_location="home", walking_minutes=None, taxi_minutes=15, bus_minutes=25
    ),
}


_FAKE_PROFILE = UserProfile(
    user_id="demo_user",
    home_location=Location(name="文二路 · 嘉绿苑"),
    default_budget=300.0,
    transport_preference="taxi",
)


# ============================================================
# 注册函数
# ============================================================

_FAKE_TOOL_NAMES = (
    "get_user_profile",
    "search_pois",
    "search_restaurants",
    "check_restaurant_availability",
    "estimate_route_time",
    "reserve_restaurant",
    "buy_ticket",
    "generate_share_message",
)


def register_fake_tools() -> None:
    """把假 Tool 注册到 TOOL_REGISTRY；幂等。"""
    # 幂等：先清掉同名
    for name in _FAKE_TOOL_NAMES:
        TOOL_REGISTRY.pop(name, None)

    # ---- get_user_profile ----
    @register_tool(
        name="get_user_profile",
        description="读取硬编码用户画像（家位置 / 默认预算 / 交通偏好）",
        input_model=GetUserProfileInput,
        output_model=GetUserProfileOutput,
    )
    def get_user_profile(_: GetUserProfileInput) -> GetUserProfileOutput:
        return GetUserProfileOutput(success=True, profile=_FAKE_PROFILE)

    # ---- search_pois ----
    @register_tool(
        name="search_pois",
        description="按距离 / physical / experience tag 过滤 POI 候选；空集返 empty_candidates",
        input_model=SearchPoisInput,
        output_model=SearchPoisOutput,
    )
    def search_pois(inp: SearchPoisInput) -> SearchPoisOutput:
        candidates = []
        for poi in _FAKE_POIS:
            if poi.distance_km > inp.distance_max_km:
                continue
            if inp.physical_constraints:
                # 至少命中一个
                if not (set(poi.tags) & set(inp.physical_constraints)):
                    continue
            if inp.age_in_party and poi.age_range:
                low, high = poi.age_range
                if not all(low <= a <= high for a in inp.age_in_party):
                    continue
            if inp.social_context and poi.suitable_for:
                if inp.social_context not in poi.suitable_for:
                    continue
            candidates.append(poi)
        if not candidates:
            return SearchPoisOutput(
                success=False, reason=FailureReason.EMPTY_CANDIDATES, candidates=[]
            )
        return SearchPoisOutput(success=True, candidates=candidates[: inp.limit or 10])

    # ---- search_restaurants ----
    @register_tool(
        name="search_restaurants",
        description="按距离 / dietary tag / 容量过滤餐厅候选；空集返 empty_candidates",
        input_model=SearchRestaurantsInput,
        output_model=SearchRestaurantsOutput,
    )
    def search_restaurants(inp: SearchRestaurantsInput) -> SearchRestaurantsOutput:
        candidates = []
        for r in _FAKE_RESTAURANTS:
            if r.distance_km > inp.distance_max_km:
                continue
            if inp.dietary_constraints:
                if not (set(r.tags) & set(inp.dietary_constraints)):
                    continue
            candidates.append(r)
        if not candidates:
            return SearchRestaurantsOutput(
                success=False, reason=FailureReason.EMPTY_CANDIDATES, candidates=[]
            )
        return SearchRestaurantsOutput(success=True, candidates=candidates[: inp.limit or 10])

    # ---- check_restaurant_availability ----
    @register_tool(
        name="check_restaurant_availability",
        description="查餐厅指定时段是否有位；available=false 触发 E1",
        input_model=CheckRestaurantAvailabilityInput,
        output_model=CheckRestaurantAvailabilityOutput,
    )
    def check_restaurant_availability(
        inp: CheckRestaurantAvailabilityInput,
    ) -> CheckRestaurantAvailabilityOutput:
        rest = next((r for r in _FAKE_RESTAURANTS if r.id == inp.restaurant_id), None)
        if rest is None:
            return CheckRestaurantAvailabilityOutput(
                success=False,
                reason=FailureReason.NOT_FOUND,
                restaurant_id=inp.restaurant_id,
                time=inp.time,
                available=False,
            )
        slot = next((s for s in rest.reservation_slots if s.time == inp.time), None)
        if slot is None:
            return CheckRestaurantAvailabilityOutput(
                success=False,
                reason=FailureReason.NOT_FOUND,
                restaurant_id=inp.restaurant_id,
                time=inp.time,
                available=False,
            )
        if not slot.available:
            return CheckRestaurantAvailabilityOutput(
                success=False,  # 用 success=false + RESTAURANT_FULL 触发 planner E1 分支
                reason=FailureReason.RESTAURANT_FULL,
                restaurant_id=inp.restaurant_id,
                time=inp.time,
                available=False,
                queue_minutes=0,
                suggested_alternative_time="17:30",
            )
        return CheckRestaurantAvailabilityOutput(
            success=True,
            restaurant_id=inp.restaurant_id,
            time=inp.time,
            available=True,
            queue_minutes=slot.queue_minutes,
        )

    # ---- estimate_route_time ----
    @register_tool(
        name="estimate_route_time",
        description="估算两点间通勤时间（步行/打车/公交）；默认走打车",
        input_model=EstimateRouteTimeInput,
        output_model=EstimateRouteTimeOutput,
    )
    def estimate_route_time(inp: EstimateRouteTimeInput) -> EstimateRouteTimeOutput:
        route = _FAKE_ROUTES.get((inp.from_location, inp.to_location))
        if route is None:
            return EstimateRouteTimeOutput(
                success=False, reason=FailureReason.NOT_FOUND, route=None
            )
        return EstimateRouteTimeOutput(success=True, route=route)

    # ---- reserve_restaurant ----
    @register_tool(
        name="reserve_restaurant",
        description="模拟预约餐厅；返订单号；冲突时返 RESTAURANT_FULL",
        input_model=ReserveRestaurantInput,
        output_model=ReserveRestaurantOutput,
    )
    def reserve_restaurant(inp: ReserveRestaurantInput) -> ReserveRestaurantOutput:
        # 假实现：复用 check 逻辑判断
        rest = next((r for r in _FAKE_RESTAURANTS if r.id == inp.restaurant_id), None)
        if rest is None:
            return ReserveRestaurantOutput(
                success=False, reason=FailureReason.NOT_FOUND, restaurant_id=inp.restaurant_id
            )
        slot = next((s for s in rest.reservation_slots if s.time == inp.time), None)
        if slot is None or not slot.available:
            return ReserveRestaurantOutput(
                success=False,
                reason=FailureReason.RESTAURANT_FULL,
                restaurant_id=inp.restaurant_id,
            )
        return ReserveRestaurantOutput(
            success=True,
            order_id=f"R20260516_{inp.restaurant_id}_{inp.time.replace(':','')}",
            restaurant_id=inp.restaurant_id,
            confirmed_time=inp.time,
            confirmed_party_size=inp.party_size,
        )

    # ---- buy_ticket ----
    @register_tool(
        name="buy_ticket",
        description="模拟购票；P_SOLD 售罄触发 E2",
        input_model=BuyTicketInput,
        output_model=BuyTicketOutput,
    )
    def buy_ticket(inp: BuyTicketInput) -> BuyTicketOutput:
        poi = next((p for p in _FAKE_POIS if p.id == inp.poi_id), None)
        if poi is None:
            return BuyTicketOutput(
                success=False, reason=FailureReason.NOT_FOUND, poi_id=inp.poi_id
            )
        if poi.capacity.available_slots <= 0:
            return BuyTicketOutput(
                success=False, reason=FailureReason.TICKET_SOLD_OUT, poi_id=inp.poi_id
            )
        unit = (poi.price_range[0] if poi.price_range else 0) or 0
        return BuyTicketOutput(
            success=True,
            order_id=f"T20260516_{inp.poi_id}",
            poi_id=inp.poi_id,
            quantity=inp.quantity,
            total_price=float(unit * inp.quantity),
        )

    # ---- generate_share_message ----
    @register_tool(
        name="generate_share_message",
        description="按 social_context 调性生成口语转发文案",
        input_model=GenerateShareMessageInput,
        output_model=GenerateShareMessageOutput,
    )
    def generate_share_message(
        inp: GenerateShareMessageInput,
    ) -> GenerateShareMessageOutput:
        tone = {
            "家庭日常": "下午带宝贝出门",
            "朋友热闹": "下午一起出去玩",
            "情侣亲密": "下午我们一起",
            "老人伴助": "下午陪您出门走走",
            "闺蜜聊天": "亲爱的下午一起",
            "商务接待": "下午为您安排了",
            "同学重聚": "老地方，下午见",
            "独处放空": "今天下午就一个人",
            "纪念日仪式感": "为这个特别的日子",
        }.get(inp.social_context, "下午出门")
        msg = f"{tone}：{inp.itinerary_summary}。"
        return GenerateShareMessageOutput(success=True, message=msg)


def unregister_fake_tools() -> None:
    """清除假 Tool 注册（幂等）。"""
    for name in _FAKE_TOOL_NAMES:
        TOOL_REGISTRY.pop(name, None)
