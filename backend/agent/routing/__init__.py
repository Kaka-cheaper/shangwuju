"""agent.routing —— 路由 bounded context 的家。

装 RouteKind / RouteOutcome / route_turn（待建）/ 信号表（待建）。
积木（injection_detector / feedback_detector / dialogue_acts 等）留 agent/core/。

见 ADR-0005：routing 包断循环依赖——graph → routing 单向，
routing 不得 import agent/graph/*。
"""

from agent.routing.kinds import RouteKind
from agent.routing.outcome import RouteOutcome
from agent.routing.route_turn import route_turn

__all__ = [
    "RouteKind",
    "RouteOutcome",
    "route_turn",
]
