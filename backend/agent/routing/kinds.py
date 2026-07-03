"""agent.routing.kinds —— RouteKind 路由结果枚举。

从 agent/graph/state.py 迁移至此以断循环依赖（见 ADR-0005）。
graph/state.py 保留 re-export shim，所有现有 importer 零改动。

禁止：本模块不得 import agent/graph/* （会重新引入环）。

【ADR-0011 E-2-c：7→6 塌缩】
旧 7 值（planning/chitchat/meta/emotional/off_topic/ambiguous/feedback）按
L0 响应义务契约重新收口为 6 值——meta/emotional 塌缩进 chitchat（语气差异交
`RouterDecision.tone` 承载，不再是路由分支），off_topic 改名 defense，ambiguous
改名 clarify，新增 confirm（原先塞在 chitchat 里的"确认/预约"独立成一类）。
迁移面见 ADR-0011 前置核实②：graph 内 3 处（`route_after_router` / `build.py`
条件边表 / `emit_router`）历史上就只显式判 "planning"/"feedback"、其余一律
catch-all 兜底，故 6→7 塌缩对这 3 处**零改动**（catch-all 天然兼容任意新增/
改名的非 planning/feedback 值）；`schemas/router.py::InputKind` 与
`frontend/lib/types.ts::InputKind` 同步改名（契约面）。
"""

from __future__ import annotations

from typing import Literal

RouteKind = Literal[
    "planning",   # 满足-首轮：进 intent → planner → execute 主路径
    "feedback",   # 满足-反馈：对已有方案的反馈，走 refiner
    "chitchat",   # 陪聊：社交/情绪输入、方案提问、画像问答（吸收旧 chitchat/meta/emotional）
    "confirm",    # 确认：对方案的纯认可/主动执行表态，只引导显式按钮，绝不自动下单
    "clarify",    # 澄清：意图/指代有歧义，反问 + 选项（原 ambiguous 改名）
    "defense",    # 防御：越界请求得体拒绝（原 off_topic 改名，含注入拦截）
]
