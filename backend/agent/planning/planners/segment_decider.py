"""agent.planning.planners.segment_decider —— ils_planner / rule_planner / replan 的活依赖（兼容 alias）。

【真实定位】

本模块**不是** legacy / 冻结——是 PLANNER_LLM_STRATEGY=hybrid 路径与 rule planner 路径
的活依赖。被 `ils_planner` / `rule_planner` / `graph/nodes/replan.py` / 5 个测试共同消费。

【与 blueprint/node_decider 的关系】

参考 spec itinerary-edge-model-refactor R7 / Task 9：edge_v1 重构时新建 `blueprint/node_decider.py`，
本文件作为 `from ..blueprint.node_decider import *` 兼容 alias 保留，避免外部 import 损坏。

新代码请直接 `from agent.planning.blueprint.node_decider import decide_nodes`。
"""

from ..blueprint.node_decider import *  # noqa: F401, F403
from ..blueprint.node_decider import (  # 显式 re-export（让 IDE 看得见）
    ALWAYS_INCLUDED,
    FULL_MID_NODES,
    FULL_SEGMENTS,
    KIND_DINING,
    KIND_MAIN,
    THRESHOLD_SHORT_HAS_BOTH_MIN,
    THRESHOLD_SHORT_MIN,
    THRESHOLD_VERY_SHORT_MIN,
    decide_nodes,
    decide_segments,
    explain_nodes,
    explain_segments,
)

__all__ = [
    "ALWAYS_INCLUDED",
    "FULL_MID_NODES",
    "FULL_SEGMENTS",
    "KIND_DINING",
    "KIND_MAIN",
    "THRESHOLD_SHORT_HAS_BOTH_MIN",
    "THRESHOLD_SHORT_MIN",
    "THRESHOLD_VERY_SHORT_MIN",
    "decide_nodes",
    "decide_segments",
    "explain_nodes",
    "explain_segments",
]
