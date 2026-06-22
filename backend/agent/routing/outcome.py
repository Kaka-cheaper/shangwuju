"""agent.routing.outcome —— RouteOutcome，route_turn 的类型化产出。

见 ADR-0003：把"去哪（kind）+ 可选回复 payload（decision）"显式化。
各 adapter 把 RouteOutcome 翻成自己的形状。

禁止：本模块不得 import agent/graph/* （会重新引入环）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from schemas.router import RouterDecision

from agent.routing.kinds import RouteKind


@dataclass(frozen=True)
class RouteOutcome:
    """route_turn 的类型化返回值。

    Attributes:
        kind:     路由目标，决定 graph 走哪条边。
        decision: LLM 分类器产出的回复 payload；
                  feedback / planning fast-path 等不经 LLM 的路径为 None。
    """

    kind: RouteKind
    decision: Optional[RouterDecision]
