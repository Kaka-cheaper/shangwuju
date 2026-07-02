"""tests.test_activity_pool —— ADR-0010 D-1：约束 + utility 构建层。

覆盖 `agent.planning.planners.activity_pool`：
1. TimeWindow 值对象（交集 / 包含 / 非法构造）
2. 饭点窗单一真相源（与 critic._rules.checks 共读同一组常量，不漂移）
3. opening_hours 解析（与 critic._is_in_business_hours 语义一致）
4. POI / 餐厅候选时间窗构建（含 pin 收窄接口）
5. Visit 构造（duration 不夹年龄 cap / base_score 与 _utility 分量对应关系 /
   刻意剔除 _overload_penalty）
6. 候选池扩容 / 分层取样（多样性）
7. 路线级 utility：通勤紧凑 / 多样性罚 / 预算软罚 / route_score / marginal_score

本文件是纯新增（ADR-0010 D-1 铁律）：不改 ils_planner 现有流程，只读它的
`_utility`/`_overload_penalty` 做纵向复用验证。
"""

from __future__ import annotations

import pytest

from agent.planning.critic import meal_windows
from agent.planning.critic._rules import checks as critic_checks
from agent.planning.critic._rules.helpers import _is_in_business_hours
from agent.planning.planners import activity_pool as ap
from agent.planning.planners.ils_planner import _overload_penalty, _utility
from agent.planning.weights_llm import PlanningWeights
from schemas.domain import (
    Location,
    Poi,
    PoiCapacity,
    Restaurant,
    RestaurantCapacity,
    SuggestedDuration,
)
from schemas.intent import Companion, IntentExtraction


# ============================================================
# 共享 fixture helpers
# ============================================================


def _intent(
    *,
    social_context: str = "家庭日常",
    companions: tuple[Companion, ...] = (),
    physical: tuple[str, ...] = (),
    dietary: tuple[str, ...] = (),
    distance_max_km: float = 10.0,
) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=distance_max_km,
        companions=list(companions),
        physical_constraints=list(physical),
        dietary_constraints=list(dietary),
        experience_tags=[],
        social_context=social_context,
        raw_input="测试",
        parse_confidence=0.9,
        ambiguous_fields=[],
    )


def _weights(**overrides) -> PlanningWeights:
    base = dict(comfort=0.3, time=0.2, cost=0.2, smoothness=0.3, source="test")
    base.update(overrides)
    return PlanningWeights(**base)


def _poi(
    *,
    poi_id: str = "P_T1",
    poi_type: str = "亲子乐园",
    distance_km: float = 3.0,
    rating: float = 4.5,
    opening_hours: str = "09:00-21:00",
    suggested: SuggestedDuration | int | None = 90,
    price_range: list[float] | None = None,
    age_range: list[int] | None = None,
    tags: tuple[str, ...] = (),
    suitable_for: tuple[str, ...] = (),
) -> Poi:
    return Poi(
        id=poi_id,
        name=f"测试 POI {poi_id}",
        type=poi_type,
        location=Location(name="测试地", lat=30.25, lng=120.15),
        distance_km=distance_km,
        opening_hours=opening_hours,
        rating=rating,
        age_range=age_range,
        price_range=price_range,
        tags=list(tags),
        suitable_for=list(suitable_for),
        suggested_duration_minutes=suggested,
        capacity=PoiCapacity(daily_quota=100, available_slots=50),
    )


def _restaurant(
    *,
    rest_id: str = "R_T1",
    cuisine: str = "粤菜",
    distance_km: float = 3.0,
    rating: float = 4.3,
    opening_hours: str = "10:30-22:00",
    typical_dining_min: int | None = 90,
    avg_price: float = 120.0,
    tags: tuple[str, ...] = (),
    suitable_for: tuple[str, ...] = (),
) -> Restaurant:
    return Restaurant(
        id=rest_id,
        name=f"测试餐厅 {rest_id}",
        cuisine=cuisine,
        location=Location(name="测试地", lat=30.25, lng=120.15),
        distance_km=distance_km,
        opening_hours=opening_hours,
        avg_price=avg_price,
        rating=rating,
        typical_dining_min=typical_dining_min,
        capacity=RestaurantCapacity(),
        tags=list(tags),
        suitable_for=list(suitable_for),
    )


# ============================================================
# 1. TimeWindow 值对象
# ============================================================


def test_time_window_intersect_overlapping():
    a = ap.TimeWindow(600, 800)
    b = ap.TimeWindow(700, 900)
    got = a.intersect(b)
    assert got == ap.TimeWindow(700, 800)


def test_time_window_intersect_disjoint_returns_none():
    a = ap.TimeWindow(600, 700)
    b = ap.TimeWindow(800, 900)
    assert a.intersect(b) is None


def test_time_window_intersect_touching_endpoint_is_zero_width_window():
    """端点相接（[600,700] 与 [700,800]）保留零宽窗口，不视为不相交。

    本层不擅自判"零宽窗口不可用"——是否可用是 D-2 调度器的判定，D-1 只如实收窄。
    """
    a = ap.TimeWindow(600, 700)
    b = ap.TimeWindow(700, 800)
    got = a.intersect(b)
    assert got == ap.TimeWindow(700, 700)
    assert got.duration_min == 0


def test_time_window_contains():
    w = ap.TimeWindow(600, 700)
    assert w.contains(600)
    assert w.contains(700)
    assert w.contains(650)
    assert not w.contains(599)
    assert not w.contains(701)


def test_time_window_rejects_end_before_start():
    with pytest.raises(ValueError):
        ap.TimeWindow(700, 600)


# ============================================================
# 2. 饭点窗单一真相源（与 critic._rules.checks 共读，不漂移）
# ============================================================


def test_meal_window_constants_shared_with_critic_checks():
    """checks.py 的私有常量与 meal_windows 模块数值完全一致——单一真相源钉住。

    这条测试的意义：如果未来有人在 checks.py 里"顺手"改了一个数字而忘记同步
    meal_windows.py（或反过来），本测试会先炸，比等 check_meal_time 行为漂移
    才被发现快得多。
    """
    assert critic_checks._LUNCH_START_MIN == meal_windows.LUNCH_START_MIN
    assert critic_checks._LUNCH_END_MIN == meal_windows.LUNCH_END_MIN
    assert critic_checks._DINNER_START_MIN == meal_windows.DINNER_START_MIN
    assert critic_checks._DINNER_END_MIN == meal_windows.DINNER_END_MIN
    assert critic_checks._SUPPER_START_MIN == meal_windows.SUPPER_START_MIN
    assert critic_checks._TEAHOUSE_CUISINES == meal_windows.TEAHOUSE_CUISINES


def test_activity_pool_meal_windows_use_same_boundaries_as_check_meal_time():
    """activity_pool 构建的餐厅窗边界，必须与 check_meal_time 的判定边界一致。

    构造一个宽营业时间（不裁剪窗）的正餐餐厅，窗的左右端点应恰好等于
    meal_windows 里的常量——证明 D-1 的窗构造没有另起一套边界。
    """
    rest = _restaurant(cuisine="粤菜", opening_hours="00:00-23:59")
    windows = ap.build_restaurant_time_windows(rest, duration_min=90)
    starts = sorted(w.start_min for w in windows)
    ends = sorted(w.end_min for w in windows)
    assert starts == [
        meal_windows.LUNCH_START_MIN,
        meal_windows.DINNER_START_MIN,
        meal_windows.SUPPER_START_MIN,
    ]
    assert meal_windows.LUNCH_END_MIN in ends
    assert meal_windows.DINNER_END_MIN in ends


# ============================================================
# 3. opening_hours 解析（与 critic._is_in_business_hours 语义一致）
# ============================================================


def test_opening_hours_window_parses_simple_range():
    w = ap._opening_hours_window("09:00-21:00")
    assert w == ap.TimeWindow(9 * 60, 21 * 60)


def test_opening_hours_window_empty_is_unconstrained():
    w = ap._opening_hours_window("")
    assert w == ap.FULL_DAY_WINDOW


def test_opening_hours_window_unparseable_is_unconstrained():
    w = ap._opening_hours_window("全天营业")
    assert w == ap.FULL_DAY_WINDOW


def test_opening_hours_window_cross_day_is_unconstrained():
    """跨日营业（22:00-04:00）与 `_is_in_business_hours` 的简化处理一致：不约束。"""
    w = ap._opening_hours_window("22:00-04:00")
    assert w == ap.FULL_DAY_WINDOW


@pytest.mark.parametrize("start_min,end_min", [(9 * 60, 10 * 60), (20 * 60, 21 * 60)])
def test_opening_hours_window_consistent_with_is_in_business_hours(start_min, end_min):
    """构造出的窗内任意子区间，`_is_in_business_hours` 都应判 True——两处共读同一份正则。"""
    oh = "09:00-21:00"
    w = ap._opening_hours_window(oh)
    assert w.contains(start_min) and w.contains(end_min)
    assert _is_in_business_hours(start_min, end_min, oh) is True


# ============================================================
# 4. POI / 餐厅候选时间窗构建（含 pin 收窄接口）
# ============================================================


def test_poi_time_window_is_opening_hours_start_window():
    """POI 窗 = 营业时间按 duration 换算的**开始时刻窗**：[开门, 打烊 - duration]。

    语义契约：start ∈ 窗 ⇒ 整段停留落在营业时间内（对齐 check_opening_hours）。
    """
    poi = _poi(opening_hours="10:00-18:00")
    windows = ap.build_poi_time_windows(poi, duration_min=60)
    assert windows == [ap.TimeWindow(10 * 60, 17 * 60)]  # 最晚 17:00 开始才呆得满 60min


def test_poi_time_window_narrowed_by_pin():
    """pin 收窄接口：POI 侧 pin 与营业换算窗求交（物理约束不可协商）。"""
    poi = _poi(opening_hours="10:00-18:00")
    pin = ap.TimeWindow(11 * 60, 12 * 60)
    windows = ap.build_poi_time_windows(poi, duration_min=60, pin=pin)
    assert windows == [ap.TimeWindow(11 * 60, 12 * 60)]


def test_poi_time_window_pin_outside_opening_hours_yields_no_window():
    """pin 落在打烊时段——物理冲突，D-1 层如实收窄到空，不假装能满足。"""
    poi = _poi(opening_hours="10:00-18:00")
    pin = ap.TimeWindow(19 * 60, 20 * 60)
    windows = ap.build_poi_time_windows(poi, duration_min=60, pin=pin)
    assert windows == []


def test_restaurant_time_window_default_is_meal_windows_intersect_start_window():
    """正餐类餐厅默认窗 = 饭点(start 语义) ∩ 营业换算的开始时刻窗。

    11:00-22:00 营业、90 分钟正餐 → 最晚 20:30 开始：午/晚餐窗完整保留；
    夜宵窗（21:00 起）整个呆不满——21:00 开吃 22:00 打烊只剩 60 分钟——被丢弃。
    """
    rest = _restaurant(cuisine="粤菜", opening_hours="11:00-22:00")
    windows = ap.build_restaurant_time_windows(rest, duration_min=90)
    assert ap.TimeWindow(meal_windows.LUNCH_START_MIN, meal_windows.LUNCH_END_MIN) in windows
    assert ap.TimeWindow(meal_windows.DINNER_START_MIN, meal_windows.DINNER_END_MIN) in windows
    assert len(windows) == 2  # 夜宵窗被 duration 换算自然裁掉


def test_restaurant_dinner_window_clipped_by_closing_time_minus_duration():
    """本次窗语义修复的回归测试（审查抓出的缺陷）：营业到 19:00 的店、90 分钟晚餐，
    晚餐窗必须裁到 [17:00, 17:30]——17:30 后开吃就吃不完（打烊前离场）。

    修复前的「饭点窗 ∩ 营业停留窗」会给出 [17:00,19:00]，调度器按 start 语义
    排 18:30 开吃 → 20:00 结束 → OPENING_HOURS HARD 违规 + 修复闭环空转噪声。
    """
    rest = _restaurant(cuisine="粤菜", opening_hours="10:00-19:00")
    windows = ap.build_restaurant_time_windows(rest, duration_min=90)
    dinner = next(w for w in windows if w.start_min == meal_windows.DINNER_START_MIN)
    assert dinner.end_min == 17 * 60 + 30, (
        f"晚餐窗尾应为 打烊(19:00) - 90min = 17:30，实际 {dinner.end_min}"
    )


def test_restaurant_time_window_narrow_opening_hours_drops_lunch_window():
    """只在傍晚营业的餐厅：午餐窗与营业时间无交集，应被自然丢弃（不是报错）。"""
    rest = _restaurant(cuisine="火锅", opening_hours="17:00-23:30")
    windows = ap.build_restaurant_time_windows(rest, duration_min=90)
    assert all(w.start_min >= meal_windows.DINNER_START_MIN for w in windows)
    assert len(windows) == 2  # 只剩晚餐 + 夜宵（夜宵尾被 23:30-90min=22:00 裁住）


def test_restaurant_time_window_teahouse_cuisine_unconstrained_by_meal_time():
    """茶点类 cuisine（下午茶/咖啡/烘焙甜品）不受饭点约束，窗=营业换算窗。"""
    rest = _restaurant(cuisine="下午茶", opening_hours="10:00-22:00")
    windows = ap.build_restaurant_time_windows(rest, duration_min=60)
    assert windows == [ap.TimeWindow(10 * 60, 21 * 60)]  # 打烊前 60min 收尾
    # 15:00（非饭点）应落在窗内
    assert any(w.contains(15 * 60) for w in windows)


def test_restaurant_time_window_pin_overrides_meal_convention_not_opening_hours():
    """pin（"6 点吃饭"）覆盖饭点惯例默认，但仍必须落在营业换算窗内（ADR-0010 决策 2）。"""
    rest = _restaurant(cuisine="粤菜", opening_hours="10:00-22:00")
    pin = ap.TimeWindow(15 * 60, 15 * 60 + 30)  # 下午 3 点，不在任何饭点惯例窗内
    windows = ap.build_restaurant_time_windows(rest, duration_min=90, pin=pin)
    assert windows == [ap.TimeWindow(15 * 60, 15 * 60 + 30)]


def test_restaurant_time_window_pin_still_bounded_by_physical_opening_hours():
    """pin 落在打烊时段——物理约束赢，收窄到空（不是"假装满足"）。"""
    rest = _restaurant(cuisine="粤菜", opening_hours="10:00-22:00")
    pin = ap.TimeWindow(23 * 60, 23 * 60 + 30)
    windows = ap.build_restaurant_time_windows(rest, duration_min=90, pin=pin)
    assert windows == []


def test_restaurant_windows_collapse_to_real_reservation_slots():
    """D-8a（ADR-0008 红队 R3 在①层的完成）：餐厅窗与真实预约槽单求交，坍缩成
    离散槽点——调度器从源头只排「订得上」的时刻。

    动机（实测）：连续窗让调度器排出 17:00 而店家槽单 17:30 起 → critic 无槽
    HARD → 修复闭环一轮挪 30 分钟地磨；两家正餐挤同一晚餐窗时 ping-pong 耗尽
    修复预算落地板。槽点化后该类违规从源头消失，critic 无槽检查退成纯兜底。

    满座槽**保留**（available=False 不过滤——满座由 critic 抓、闭环演旗舰链）。
    """
    from schemas.domain import ReservationSlot

    rest = _restaurant(cuisine="粤菜", opening_hours="10:00-22:00")
    rest = rest.model_copy(
        update={
            "reservation_slots": [
                ReservationSlot(time="12:00", available=True),
                ReservationSlot(time="17:00", available=False),  # 满座槽也保留
                ReservationSlot(time="17:30", available=True),
                ReservationSlot(time="15:00", available=True),  # 非饭点 → 被饭点窗滤掉
            ]
        }
    )
    windows = ap.build_restaurant_time_windows(rest, duration_min=90)
    points = sorted((w.start_min, w.end_min) for w in windows)
    assert (12 * 60, 12 * 60) in points  # 午餐槽
    assert (17 * 60, 17 * 60) in points  # 满座槽保留——旗舰链的原材料
    assert (17 * 60 + 30, 17 * 60 + 30) in points
    assert (15 * 60, 15 * 60) not in points  # 非饭点槽被饭点窗滤掉
    assert all(w.start_min == w.end_min for w in windows), "全部应为零宽槽点"


def test_restaurant_without_slots_keeps_continuous_windows():
    """无预约体系的店（slots 空）：不做槽点化，保留连续窗（防御性向后兼容）。"""
    rest = _restaurant(cuisine="粤菜", opening_hours="11:00-22:00")
    windows = ap.build_restaurant_time_windows(rest, duration_min=90)
    assert any(w.end_min > w.start_min for w in windows), "无槽单时窗应保持连续区间"


# ============================================================
# 5. Visit 构造
# ============================================================


def test_build_visit_from_poi_basic_fields():
    poi = _poi(poi_id="P_A", poi_type="展览", distance_km=2.0, price_range=[50.0, 80.0])
    intent = _intent()
    weights = _weights()
    visit = ap.build_visit_from_poi(poi, intent, weights)

    assert visit.kind == "poi"
    assert visit.target_id == "P_A"
    assert visit.category == "展览"
    assert visit.cost == 50.0
    assert visit.entity is poi
    assert visit.windows == ap.build_poi_time_windows(
        poi, duration_min=visit.duration_min
    )


def test_build_visit_from_poi_duration_projects_by_companions_then_clamped_to_age_cap():
    """ADR-0010 D-3（intentional 行为改变，取代 D-1 的"不夹"版本）：

    自然时长 = `get_duration_for_companions` 投影，**再夹年龄 cap**。5 岁孩子按
    `cap_for_age` 拿 75min cap；这里 suggested 投影出 150min（故意设置成远超
    cap），`Visit.duration_min` 必须被夹到 75，不能原样 150——D-3 落地后夹紧
    真正生效，不再是"记账但不执行"。
    """
    sd = SuggestedDuration(default=200, kid_3_6=150)
    poi = _poi(suggested=sd)
    intent = _intent(companions=(Companion(role="孩子", age=5, count=1),))
    visit = ap.build_visit_from_poi(poi, intent, _weights())
    assert visit.duration_min == 75  # 150 投影值被夹到 75 的年龄 cap


def test_build_visit_from_poi_duration_no_cap_when_no_companions_trigger_a_tier():
    """无同行人触发任何 cap 分桶时，duration 原样是自然投影值（不误夹）。"""
    sd = SuggestedDuration(default=200, kid_3_6=150)
    poi = _poi(suggested=sd)
    intent = _intent()  # 无 companions
    visit = ap.build_visit_from_poi(poi, intent, _weights())
    assert visit.duration_min == 200  # 无孩子同行，走 default 投影，不夹


def test_build_visit_from_poi_toddler_cap_clamps_duration_and_widens_window_tail():
    """ADR-0010 D-3 验收例句：3 岁娃 + suggested 120min → duration 45（婴幼儿 cap）
    ——且窗构建吃的是**夹紧后**的 duration，窗尾应是「打烊 − 45」而非「打烊 − 120」。

    这条测试同时钉住"夹紧必须发生在建窗之前"这一 D-3 铁律的联动效果：如果实现
    误把未夹紧的 120 传给 `build_poi_time_windows`，窗尾会是打烊前 120 分钟，
    比正确值早得多，本测试会先炸。
    """
    poi = _poi(suggested=120, opening_hours="10:00-18:00")
    intent = _intent(companions=(Companion(role="孩子", age=3, count=1),))
    visit = ap.build_visit_from_poi(poi, intent, _weights())

    assert visit.duration_min == 45  # ≤3 岁 婴幼儿 cap（age_caps.TODDLER_CAP_MIN）
    # 打烊 18:00 − 45min = 17:15；若误传未夹紧的 120min 会得到 16:00
    assert visit.windows == [ap.TimeWindow(10 * 60, 17 * 60 + 15)]


def test_build_visit_from_poi_duration_int_form_passthrough():
    poi = _poi(suggested=120)
    intent = _intent()
    visit = ap.build_visit_from_poi(poi, intent, _weights())
    assert visit.duration_min == 120


def test_build_visit_from_restaurant_duration_uses_typical_dining_min():
    rest = _restaurant(typical_dining_min=105)
    intent = _intent()
    visit = ap.build_visit_from_restaurant(rest, intent, _weights())
    assert visit.duration_min == 105
    assert visit.kind == "restaurant"
    assert visit.category == rest.cuisine
    assert visit.cost == rest.avg_price


def test_build_visit_from_poi_base_score_equals_raw_utility_including_overload_penalty():
    """ADR-0010 D-3（intentional 行为改变，取代 D-1 的"抵消"版本）：

    D-1 曾手动加回 `0.5 * _overload_penalty(...)` 精确抵消 `_utility` 内嵌的
    `-0.5 * _overload_penalty` 项；D-3 落地后撤销这个抵消——`base_score` 现在
    就是 `_utility(poi, None, ...)` 的原始返回值，overload 惩罚原样生效在
    base_score 里（"suggested 超 cap"重新体现为选择阶段的扣分）。
    """
    sd = SuggestedDuration(default=180, kid_3_6=90)
    poi = _poi(poi_id="P_OVL", suggested=sd)
    intent = _intent(companions=(Companion(role="孩子", age=5, count=1),))
    weights = _weights()

    raw_score, _fail = _utility(poi, None, "", intent, weights, semantic_scores=None)
    overload = _overload_penalty(poi, intent)
    assert overload == pytest.approx(0.3)  # 90 > cap 75 → 确实触发了惩罚

    visit = ap.build_visit_from_poi(poi, intent, weights)
    assert visit.base_score == pytest.approx(raw_score)  # 不再加回抵消


def test_build_visit_from_poi_base_score_reflects_overload_penalty_after_d3():
    """ADR-0010 D-3（intentional 行为改变，取代 D-1 的"两个 intent 打平分"版本）：

    D-1 版本要求"只有 age cap 触发 overload 不同的两个 intent，base_score 应
    完全一致"（证明抵消精确生效）。D-3 撤销抵消后，这个断言反过来——触发 overload
    的 kid intent 的 base_score 应该**低于**不触发的 adult intent，差值正好是
    `0.5 * overload_penalty`。这是本步"选择阶段该为体验残缺扣分"的直接验证。
    """
    sd = SuggestedDuration(default=180, kid_3_6=90)
    poi = _poi(poi_id="P_OVL2", suggested=sd)
    weights = _weights()

    intent_kid = _intent(companions=(Companion(role="孩子", age=5, count=1),))
    intent_adult = _intent(companions=(Companion(role="伴侣", age=30, count=1),))

    overload_kid = _overload_penalty(poi, intent_kid)
    overload_adult = _overload_penalty(poi, intent_adult)
    assert overload_kid == pytest.approx(0.3)
    assert overload_adult == pytest.approx(0.0)

    visit_kid = ap.build_visit_from_poi(poi, intent_kid, weights)
    visit_adult = ap.build_visit_from_poi(poi, intent_adult, weights)
    assert visit_adult.base_score - visit_kid.base_score == pytest.approx(
        0.5 * (overload_kid - overload_adult)
    )


def test_build_visit_from_restaurant_base_score_matches_utility():
    """餐厅侧不需要抵消 overload_penalty（该 penalty 只吃 poi 参数，传 None 天然为 0）。"""
    rest = _restaurant()
    intent = _intent(dietary=("粤菜",))
    weights = _weights()
    raw_score, _fail = _utility(None, rest, "", intent, weights)
    visit = ap.build_visit_from_restaurant(rest, intent, weights)
    assert visit.base_score == pytest.approx(raw_score)


def test_build_visit_from_poi_semantic_scores_applied():
    poi = _poi(poi_id="P_SEM")
    intent = _intent()
    weights = _weights()
    visit_no_sem = ap.build_visit_from_poi(poi, intent, weights, semantic_scores=None)
    visit_high_sem = ap.build_visit_from_poi(
        poi, intent, weights, semantic_scores={"P_SEM": 0.95}
    )
    assert visit_high_sem.base_score > visit_no_sem.base_score


def test_build_visit_from_poi_cost_defaults_to_zero_without_price_range():
    poi = _poi(price_range=None)
    visit = ap.build_visit_from_poi(poi, _intent(), _weights())
    assert visit.cost == 0.0


# ============================================================
# 6. 候选池扩容 / 分层取样
# ============================================================


def test_build_route_candidate_pool_returns_all_when_below_top_k():
    pois = [_poi(poi_id=f"P{i}", poi_type="展览") for i in range(3)]
    pool = ap.build_route_candidate_pool(pois, category_of=ap.poi_category, top_k=5)
    assert pool == pois


def test_build_route_candidate_pool_stratifies_across_categories():
    """同质池诊断（ADR-0010 D-1 原话："单口味搜索 top-5 会给出同质池"）：

    前 5 个候选全是"展览"类，后面才出现"公园"/"咖啡馆"——若直接切片取前 5
    （旧 CANDIDATE_TOP_K 行为），拿到的池子全同类，多样性罚无米下锅。
    分层取样应该在 top_k 里同时纳入其它类别。
    """
    homogeneous = [_poi(poi_id=f"PA{i}", poi_type="展览") for i in range(5)]
    diverse_tail = [
        _poi(poi_id="PB0", poi_type="公园"),
        _poi(poi_id="PC0", poi_type="咖啡馆"),
    ]
    candidates = homogeneous + diverse_tail
    pool = ap.build_route_candidate_pool(candidates, category_of=ap.poi_category, top_k=5)
    categories = {ap.poi_category(p) for p in pool}
    assert categories == {"展览", "公园", "咖啡馆"}, (
        f"分层取样应纳入尾部的其它类别，实际池子类别={categories}"
    )


def test_build_route_candidate_pool_preserves_within_category_order():
    """同类别内部相对顺序保留（假定输入已按相关性排序，不打乱组内排名）。"""
    pois = [_poi(poi_id=f"P{i}", poi_type="展览") for i in range(4)]
    pool = ap.build_route_candidate_pool(pois, category_of=ap.poi_category, top_k=4)
    assert [p.id for p in pool] == [p.id for p in pois]


def test_build_restaurant_route_pool_uses_cuisine_category():
    rests = [_restaurant(rest_id=f"R{i}", cuisine="粤菜") for i in range(5)] + [
        _restaurant(rest_id="RB0", cuisine="日料")
    ]
    pool = ap.build_restaurant_route_pool(rests, top_k=3)
    categories = {ap.restaurant_category(r) for r in pool}
    assert "日料" in categories


# ============================================================
# 7. 路线级 utility
# ============================================================


def _visit_for_route(
    *, category: str = "展览", distance_km: float = 3.0, cost: float = 100.0,
    base_score: float = 0.5,
) -> ap.Visit:
    poi = _poi(poi_type=category, distance_km=distance_km)
    return ap.Visit(
        kind="poi",
        target_id=poi.id,
        duration_min=90,
        windows=[ap.TimeWindow(9 * 60, 18 * 60)],
        base_score=base_score,
        category=category,
        cost=cost,
        entity=poi,
    )


def test_route_commute_compactness_single_visit_is_neutral():
    assert ap.route_commute_compactness([_visit_for_route()]) == 1.0


def test_route_commute_compactness_same_distance_is_perfect():
    v1 = _visit_for_route(distance_km=3.0)
    v2 = _visit_for_route(distance_km=3.0)
    assert ap.route_commute_compactness([v1, v2]) == pytest.approx(1.0)


def test_route_commute_compactness_decays_with_distance_gap():
    close = ap.route_commute_compactness(
        [_visit_for_route(distance_km=3.0), _visit_for_route(distance_km=3.5)]
    )
    far = ap.route_commute_compactness(
        [_visit_for_route(distance_km=1.0), _visit_for_route(distance_km=9.0)]
    )
    assert close > far
    assert 0 < far < close <= 1.0


def test_route_diversity_penalty_no_repeat_is_zero():
    visits = [_visit_for_route(category="展览"), _visit_for_route(category="公园")]
    assert ap.route_diversity_penalty(visits) == 0.0


def test_route_diversity_penalty_second_same_category_penalized():
    visits = [_visit_for_route(category="展览"), _visit_for_route(category="展览")]
    assert ap.route_diversity_penalty(visits) == pytest.approx(ap.DIVERSITY_REPEAT_PENALTY)


def test_route_diversity_penalty_accumulates_per_repeat():
    visits = [_visit_for_route(category="展览") for _ in range(3)]
    assert ap.route_diversity_penalty(visits) == pytest.approx(2 * ap.DIVERSITY_REPEAT_PENALTY)


def test_route_budget_penalty_zero_when_within_budget():
    visits = [_visit_for_route(cost=100.0), _visit_for_route(cost=100.0)]
    assert ap.route_budget_penalty(visits, budget=300.0) == 0.0


def test_route_budget_penalty_positive_when_over_budget():
    visits = [_visit_for_route(cost=200.0), _visit_for_route(cost=200.0)]
    penalty = ap.route_budget_penalty(visits, budget=300.0)
    assert 0 < penalty < 1.0


def test_route_budget_penalty_monotonic_with_overage():
    visits_small_over = [_visit_for_route(cost=310.0)]
    visits_big_over = [_visit_for_route(cost=600.0)]
    small = ap.route_budget_penalty(visits_small_over, budget=300.0)
    big = ap.route_budget_penalty(visits_big_over, budget=300.0)
    assert 0 < small < big


def test_route_budget_penalty_zero_budget_guard():
    assert ap.route_budget_penalty([_visit_for_route(cost=100.0)], budget=0.0) == 0.0


def test_route_score_matches_manual_formula():
    v1 = _visit_for_route(category="展览", distance_km=3.0, cost=100.0, base_score=0.6)
    v2 = _visit_for_route(category="公园", distance_km=3.2, cost=100.0, base_score=0.5)
    weights = _weights(smoothness=0.3, cost=0.2)
    budget = 300.0

    score = ap.route_score([v1, v2], weights, budget)

    expected_activity = v1.base_score + v2.base_score
    expected_compactness = ap.route_commute_compactness([v1, v2])
    expected_diversity = ap.route_diversity_penalty([v1, v2])
    expected_budget_penalty = ap.route_budget_penalty([v1, v2], budget)
    expected = expected_activity + weights.smoothness * (
        expected_compactness - expected_diversity
    ) - weights.cost * expected_budget_penalty

    assert score == pytest.approx(expected)


def test_route_score_empty_route_is_zero():
    assert ap.route_score([], _weights(), budget=300.0) == 0.0


def test_marginal_score_positive_for_beneficial_addition():
    base = [_visit_for_route(category="展览", distance_km=3.0, base_score=0.6)]
    with_diverse_addition = base + [
        _visit_for_route(category="公园", distance_km=3.1, base_score=0.55)
    ]
    weights = _weights()
    gain = ap.marginal_score(with_diverse_addition, base, weights, budget=300.0)
    assert gain > 0


def test_marginal_score_lower_for_same_category_addition_due_to_diversity_penalty():
    """插入同类别第二个活动的边际分，应低于插入不同类别的边际分（多样性罚的直接体现）。"""
    base = [_visit_for_route(category="展览", distance_km=3.0, base_score=0.6)]
    weights = _weights()
    budget = 300.0

    same_category_gain = ap.marginal_score(
        base + [_visit_for_route(category="展览", distance_km=3.1, base_score=0.55)],
        base,
        weights,
        budget,
    )
    diverse_gain = ap.marginal_score(
        base + [_visit_for_route(category="公园", distance_km=3.1, base_score=0.55)],
        base,
        weights,
        budget,
    )
    assert diverse_gain > same_category_gain


def test_marginal_score_equals_route_score_difference():
    base = [_visit_for_route(category="展览", base_score=0.4)]
    extended = base + [_visit_for_route(category="公园", base_score=0.3)]
    weights = _weights()
    budget = 300.0
    assert ap.marginal_score(extended, base, weights, budget) == pytest.approx(
        ap.route_score(extended, weights, budget) - ap.route_score(base, weights, budget)
    )
