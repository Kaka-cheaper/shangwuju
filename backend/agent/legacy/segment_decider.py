"""agent.segment_decider —— 兼容 alias，重定向到新 node_decider（edge_v1）。

⚠️ 本文件已废弃；保留仅为不破坏现有 import：

- `agent/planner.py`、`agent/planner_hybrid.py`、`agent/critics.py`、
  `agent/graph/nodes/replan.py`、`agent/planner_llm_first.py`、
  `tests/test_segment_decider.py` 等仍可能 `from .segment_decider import ...`。

新代码请直接 `from agent.planning.blueprint.node_decider import decide_nodes`。

参考 spec itinerary-edge-model-refactor R7 / Task 9：
    `segment_decider.py` 重命名为 `node_decider.py`，原文件保留
    `from ..planning.blueprint.node_decider import *` 兼容 alias，避免外部 import 损坏。
"""

# FROZEN: 详见 AGENTS.md §3.3.1，仅 fallback / safety-net，不改业务

from ..planning.blueprint.node_decider import *  # noqa: F401, F403
from ..planning.blueprint.node_decider import (  # 显式 re-export（让 IDE 看得见）
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
