"""tools.reserve_restaurant —— T5 预约餐厅（执行类）。

输入/输出：ReserveRestaurantInput / ReserveRestaurantOutput
失败分支：
- NOT_FOUND：餐厅 id 不存在
- RESTAURANT_FULL：时段 available=false（即使 Agent 没先 check_availability，
  这里仍会作为 second line of defense 拦截）

Mock 行为（参考 AGENTS.md §4.2「真实下单只用日志体现」）：
- 不修改数据；返回伪造 order_id（restaurant_id + 时间 + 序号 hash）
- 不写文件；纯函数
"""

from __future__ import annotations

import hashlib
import time as _time

from data.loader import load_restaurants
from schemas.errors import FailureReason
from schemas.tools import ReserveRestaurantInput, ReserveRestaurantOutput

from .registry import register_tool


_DESC = (
    "预约餐厅指定时段，返回 mock order_id。失败：餐厅不存在 → not_found；"
    "时段不可订 → restaurant_full。Agent 应在调用前先 check_restaurant_availability。"
)


def _make_order_id(restaurant_id: str, time_: str, party: int) -> str:
    seed = f"{restaurant_id}|{time_}|{party}|{int(_time.time() * 1000)}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8].upper()
    return f"R-{restaurant_id}-{digest}"


@register_tool(
    name="reserve_restaurant",
    description=_DESC,
    input_model=ReserveRestaurantInput,
    output_model=ReserveRestaurantOutput,
)
def reserve_restaurant(inp: ReserveRestaurantInput) -> ReserveRestaurantOutput:
    restaurant = next(
        (r for r in load_restaurants() if r.id == inp.restaurant_id), None
    )
    if restaurant is None:
        return ReserveRestaurantOutput(
            success=False,
            reason=FailureReason.NOT_FOUND,
            restaurant_id=inp.restaurant_id,
        )

    slot = next(
        (s for s in restaurant.reservation_slots if s.time == inp.time), None
    )
    if slot is None or not slot.available:
        return ReserveRestaurantOutput(
            success=False,
            reason=FailureReason.RESTAURANT_FULL,
            restaurant_id=inp.restaurant_id,
        )

    order_id = _make_order_id(inp.restaurant_id, inp.time, inp.party_size)
    return ReserveRestaurantOutput(
        success=True,
        order_id=order_id,
        restaurant_id=inp.restaurant_id,
        confirmed_time=inp.time,
        confirmed_party_size=inp.party_size,
    )
