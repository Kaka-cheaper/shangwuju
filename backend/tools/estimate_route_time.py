"""tools.estimate_route_time —— T4 估两点间通勤时间。

输入/输出：EstimateRouteTimeInput / EstimateRouteTimeOutput
失败分支：
- NOT_FOUND：mock_data/routes.json 中没有这条路线（多发于 LLM 自创 id）

不做距离超限判断——E3 由 Agent 编排层（结合 distance_max_km）决定，
本 Tool 只回原始时间数据，保持单一职责。
"""

from __future__ import annotations

from schemas.errors import FailureReason
from schemas.tools import EstimateRouteTimeInput, EstimateRouteTimeOutput

from .registry import register_tool
from ._helpers import find_route


_DESC = (
    "估算 from_location → to_location 的步行 / 打车 / 公交时间。location 接受 home"
    " / POI id / Restaurant id；mock 中找不到该对返 reason=not_found。"
)


@register_tool(
    name="estimate_route_time",
    description=_DESC,
    input_model=EstimateRouteTimeInput,
    output_model=EstimateRouteTimeOutput,
)
def estimate_route_time(inp: EstimateRouteTimeInput) -> EstimateRouteTimeOutput:
    route = find_route(inp.from_location, inp.to_location)
    if route is None:
        return EstimateRouteTimeOutput(
            success=False,
            reason=FailureReason.NOT_FOUND,
            route=None,
        )
    return EstimateRouteTimeOutput(success=True, route=route)
