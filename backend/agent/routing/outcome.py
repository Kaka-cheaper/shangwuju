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
        injection_blocked: 本轮是否命中壳1 提示词注入检测（ADR-0011 E-2-c 新增，
            收敛 `graph/nodes/router.py` 原本重复调用 `detect_injection` 的问题——
            route_turn.py Layer 0 判定过一次，这里把判定结果原样带出去，adapter
            不必为了"写会话日志要不要打码"这一件事而重新跑一遍检测。默认 False；
            只有 Layer 0 命中时才置 True，其余所有分支（含壳2/Layer 1/脑子/壳3）
            都不改这个字段。
    """

    kind: RouteKind
    decision: Optional[RouterDecision]
    injection_blocked: bool = False
