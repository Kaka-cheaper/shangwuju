"""agent.context —— 会话上下文打包器（ADR-0011 决策 3）。

对外只暴露六件套的产出类型 `RoutingContext` 与打包主函数
`pack_routing_context`，以及两个底座实现（`GraphStateSource`/`RoomSource`）
与 refiner 切片辅助函数 `render_demand_recap`。消费方（路由脑子/refiner/
narrate）只应从这里 import，不应绕过本包直接拼读 state/Room 字段——
ADR-0011 决策 3"禁止各节点自己拼上下文"的边界正是靠这层收口维持。
"""

from __future__ import annotations

from .packer import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MAX_TURNS,
    pack_routing_context,
    render_demand_recap,
)
from .sources import GraphStateSource, RoomSource
from .types import (
    PlanSummaryLine,
    ProfileSnapshot,
    RoutingContext,
    SessionContextSource,
    TurnLogEntry,
)

__all__ = [
    "RoutingContext",
    "TurnLogEntry",
    "PlanSummaryLine",
    "ProfileSnapshot",
    "SessionContextSource",
    "GraphStateSource",
    "RoomSource",
    "pack_routing_context",
    "render_demand_recap",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_MAX_TOKENS",
]
