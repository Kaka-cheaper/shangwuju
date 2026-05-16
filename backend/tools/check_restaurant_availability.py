"""tools.check_restaurant_availability —— T3 查餐厅指定时段是否可订。

输入/输出：CheckRestaurantAvailabilityInput / CheckRestaurantAvailabilityOutput
失败分支（success=false）：
- NOT_FOUND：餐厅 id 不存在
- RESTAURANT_FULL：指定时段 available=false（E1 异常埋点）

设计说明：
- 即使 RESTAURANT_FULL，也尽力推荐 suggested_alternative_time（同餐厅其他可用时段）
- success=true 表示「该时段可订」；available 字段冗余地把状态再表达一次给 Agent 用
- party_size 仅作日志透出，未来可叠加桌型校验（当前 mock 不细分）
"""

from __future__ import annotations

from data.loader import load_restaurants
from schemas.errors import FailureReason
from schemas.tools import (
    CheckRestaurantAvailabilityInput,
    CheckRestaurantAvailabilityOutput,
)

from .registry import register_tool


_DESC = (
    "查询某餐厅在指定时间是否可订座；不可订时返 reason=restaurant_full 并尝试给"
    "suggested_alternative_time 用于 Agent 触发改约。"
)


def _suggest_alternative(restaurant, requested_time: str) -> str | None:
    """从 reservation_slots 中挑一个 available=true 的时段，优先返回比 requested 晚的。"""
    later = [s for s in restaurant.reservation_slots if s.available and s.time > requested_time]
    if later:
        return min(later, key=lambda s: s.time).time
    earlier = [s for s in restaurant.reservation_slots if s.available]
    if earlier:
        return max(earlier, key=lambda s: s.time).time
    return None


@register_tool(
    name="check_restaurant_availability",
    description=_DESC,
    input_model=CheckRestaurantAvailabilityInput,
    output_model=CheckRestaurantAvailabilityOutput,
)
def check_restaurant_availability(
    inp: CheckRestaurantAvailabilityInput,
) -> CheckRestaurantAvailabilityOutput:
    restaurant = next(
        (r for r in load_restaurants() if r.id == inp.restaurant_id), None
    )
    if restaurant is None:
        return CheckRestaurantAvailabilityOutput(
            success=False,
            reason=FailureReason.NOT_FOUND,
            restaurant_id=inp.restaurant_id,
            time=inp.time,
            available=False,
        )

    slot = next((s for s in restaurant.reservation_slots if s.time == inp.time), None)
    if slot is None or not slot.available:
        return CheckRestaurantAvailabilityOutput(
            success=False,
            reason=FailureReason.RESTAURANT_FULL,
            restaurant_id=inp.restaurant_id,
            time=inp.time,
            available=False,
            queue_minutes=0,
            suggested_alternative_time=_suggest_alternative(restaurant, inp.time),
        )

    return CheckRestaurantAvailabilityOutput(
        success=True,
        restaurant_id=inp.restaurant_id,
        time=inp.time,
        available=True,
        queue_minutes=slot.queue_minutes,
    )
