"""agent.routing.kinds —— RouteKind 路由结果枚举。

从 agent/graph/state.py 迁移至此以断循环依赖（见 ADR-0005）。
graph/state.py 保留 re-export shim，所有现有 importer 零改动。

禁止：本模块不得 import agent/graph/* （会重新引入环）。
"""

from __future__ import annotations

from typing import Literal

RouteKind = Literal[
    "planning",   # 进 intent → planner → execute 主路径
    "chitchat",   # 闲聊回话直接出
    "meta",       # 元能力问答（你能做什么）
    "emotional",  # 情绪共情
    "off_topic",  # 范围外礼貌拒答
    "ambiguous",  # 输入歧义需要反问
    "feedback",   # 对已有方案的反馈（走 refiner）
]
