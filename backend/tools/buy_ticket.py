"""tools.buy_ticket —— T6 购买 POI 门票（执行类）。

输入/输出：BuyTicketInput / BuyTicketOutput
失败分支：
- NOT_FOUND：poi_id 不存在
- TICKET_SOLD_OUT：capacity.available_slots == 0（E2 异常埋点）
- INVALID_INPUT：quantity == 0 或超过 available_slots（语义校验）

埋点失败案例：mock_data/pois.json 中 P002（展览售罄）/ P006（茶馆售罄）/ P010（SPA 满）
/ P013（密室满）/ P_SOLD（音乐节限定场售罄）—— 任意一个 poi_id 都能触发 E2 演示。

Mock 行为（参考 AGENTS.md §4.2「真实下单只用日志体现」）：
- 不修改数据；返回伪造 order_id（poi_id + 时间 + 哈希）
- 不写文件；纯函数
- total_price 用 price_range[0] × quantity 估算（mock 简化，不区分日期/票档）
"""

from __future__ import annotations

import hashlib
import time as _time

from data.loader import load_pois
from schemas.errors import FailureReason
from schemas.tools import BuyTicketInput, BuyTicketOutput

from .registry import register_tool


_DESC = (
    "购买 POI 门票，返回 mock order_id 与总价。失败：POI 不存在 → not_found；"
    "available_slots=0 → ticket_sold_out（E2）；quantity=0 或超出库存 → invalid_input。"
    "免费 POI（price_range=null）按 0 元计算。"
)


def _make_order_id(poi_id: str, quantity: int) -> str:
    seed = f"{poi_id}|{quantity}|{int(_time.time() * 1000)}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8].upper()
    return f"T-{poi_id}-{digest}"


@register_tool(
    name="buy_ticket",
    description=_DESC,
    input_model=BuyTicketInput,
    output_model=BuyTicketOutput,
)
def buy_ticket(inp: BuyTicketInput) -> BuyTicketOutput:
    poi = next((p for p in load_pois() if p.id == inp.poi_id), None)
    if poi is None:
        return BuyTicketOutput(
            success=False,
            reason=FailureReason.NOT_FOUND,
            poi_id=inp.poi_id,
        )

    # quantity = 0 是无意义请求
    if inp.quantity == 0:
        return BuyTicketOutput(
            success=False,
            reason=FailureReason.INVALID_INPUT,
            poi_id=inp.poi_id,
        )

    # E2：库存不足 / 售罄
    available = poi.capacity.available_slots
    if available == 0:
        return BuyTicketOutput(
            success=False,
            reason=FailureReason.TICKET_SOLD_OUT,
            poi_id=inp.poi_id,
        )
    if inp.quantity > available:
        return BuyTicketOutput(
            success=False,
            reason=FailureReason.INVALID_INPUT,
            poi_id=inp.poi_id,
        )

    unit_price = poi.price_range[0] if poi.price_range else 0.0
    total_price = float(unit_price) * inp.quantity

    return BuyTicketOutput(
        success=True,
        order_id=_make_order_id(inp.poi_id, inp.quantity),
        poi_id=inp.poi_id,
        quantity=inp.quantity,
        total_price=total_price,
    )
