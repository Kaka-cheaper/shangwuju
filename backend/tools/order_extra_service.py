"""tools.order_extra_service —— T7 附加服务下单（执行类）。

输入/输出：OrderExtraServiceInput / OrderExtraServiceOutput
失败分支：
- NOT_FOUND：target_id 不存在，或没有支持该目标的服务
- EXTRA_SERVICE_UNAVAILABLE：服务不可售 / 库存不足
- INVALID_INPUT：quantity == 0

Mock 行为：
- 不修改数据；返回伪造 order_id（service_id + target_id + 哈希）
- 不写文件；纯函数
"""

from __future__ import annotations

import hashlib
import time as _time

from data.loader import load_extra_services, load_pois, load_restaurants
from schemas.domain import ExtraService
from schemas.errors import FailureReason
from schemas.tools import OrderExtraServiceInput, OrderExtraServiceOutput

from .registry import register_tool


_DESC = (
    "为已选餐厅或 POI 加购蛋糕、鲜花、生日布置等附加服务，返回 mock order_id。"
    "失败：目标不存在或无匹配服务 → not_found；服务售罄/库存不足 → extra_service_unavailable。"
)


def _make_order_id(service_id: str, target_id: str, quantity: int) -> str:
    seed = f"{service_id}|{target_id}|{quantity}|{int(_time.time() * 1000)}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8].upper()
    return f"X-{service_id}-{digest}"


def _target_exists(kind: str, target_id: str) -> bool:
    if kind == "restaurant":
        return any(r.id == target_id for r in load_restaurants())
    if kind == "poi":
        return any(p.id == target_id for p in load_pois())
    return False


def _service_matches(service: ExtraService, inp: OrderExtraServiceInput) -> bool:
    if inp.target_kind not in service.target_kinds:
        return False
    if service.target_ids and "*" not in service.target_ids and inp.target_id not in service.target_ids:
        return False
    wanted = inp.service_type.strip()
    if not wanted:
        return False
    return (
        wanted == service.service_type
        or wanted in service.service_type
        or service.service_type in wanted
        or wanted in service.name
    )


@register_tool(
    name="order_extra_service",
    description=_DESC,
    input_model=OrderExtraServiceInput,
    output_model=OrderExtraServiceOutput,
)
def order_extra_service(inp: OrderExtraServiceInput) -> OrderExtraServiceOutput:
    if inp.quantity == 0:
        return OrderExtraServiceOutput(
            success=False,
            reason=FailureReason.INVALID_INPUT,
            service_type=inp.service_type,
            target_kind=inp.target_kind,
            target_id=inp.target_id,
        )

    if not _target_exists(inp.target_kind, inp.target_id):
        return OrderExtraServiceOutput(
            success=False,
            reason=FailureReason.NOT_FOUND,
            service_type=inp.service_type,
            target_kind=inp.target_kind,
            target_id=inp.target_id,
        )

    service = next(
        (s for s in load_extra_services() if _service_matches(s, inp)),
        None,
    )
    if service is None:
        return OrderExtraServiceOutput(
            success=False,
            reason=FailureReason.NOT_FOUND,
            service_type=inp.service_type,
            target_kind=inp.target_kind,
            target_id=inp.target_id,
        )

    if not service.available or service.inventory < inp.quantity:
        return OrderExtraServiceOutput(
            success=False,
            reason=FailureReason.EXTRA_SERVICE_UNAVAILABLE,
            service=service,
            service_type=inp.service_type,
            target_kind=inp.target_kind,
            target_id=inp.target_id,
        )

    return OrderExtraServiceOutput(
        success=True,
        order_id=_make_order_id(service.id, inp.target_id, inp.quantity),
        service=service,
        service_type=service.service_type,
        target_kind=inp.target_kind,
        target_id=inp.target_id,
        quantity=inp.quantity,
        total_price=float(service.price) * inp.quantity,
        scheduled_time=inp.scheduled_time,
    )
