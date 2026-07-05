"""nodes.execute —— Plan-and-Execute 中的 execute 阶段（并行调候选搜索）。

通过 LangGraph 并行触发 3 个搜索 worker（POI / 餐厅 / 用户画像），结果合并到 State。

并行实现：
- search_pois_worker        → state["pois"] / state["pois_relaxed_tags"]
- search_restaurants_worker → state["restaurants"] / state["restaurants_relaxed_tags"]
- get_user_profile_worker    → state["user_profile"]

路线图（暂未实现，14h 切高德时一并落地，spec D 已规划）：
- estimate_routes_worker     → state["routes"]（粗估常用 home→候选 POI 距离）

为什么 relaxed_tags 分两个 key（pois_*/restaurants_*）：
- LangGraph 默认 reduce 是覆盖，多 worker 同写一个 key 会冲突
- 业务上 POI / 餐厅的放宽路径是独立信号，分开存便于前端区分展示

注意：
- 当前 routes 在 assemble 阶段直接调 lookup_hop（routes.json mock 命中 + haversine 兜底），
  够用；estimate_routes_worker 在切真高德时落地（届时 routes 提前缓存收益显著）
- 任何 worker 失败不阻塞其他 worker（容忍空候选，让 replan 兜）
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.runtime.tools.search_adapter import (
    search_pois_for_intent,
    search_restaurants_for_intent,
    get_user_profile_for_user,
)


def search_pois_worker(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    if intent is None:
        return {"pois": [], "pois_relaxed_tags": []}
    user_id = state.get("user_id") or "demo_user"
    # 读写分离批双键：user_id=模板（home 坐标），session_id=累积（本会话访问史排重）
    pois, relaxed = search_pois_for_intent(
        intent, user_id=user_id, session_id=state.get("session_id")
    )
    return {"pois": pois, "pois_relaxed_tags": relaxed}


def search_restaurants_worker(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    if intent is None:
        return {"restaurants": [], "restaurants_relaxed_tags": []}
    user_id = state.get("user_id") or "demo_user"
    rests, relaxed = search_restaurants_for_intent(
        intent, user_id=user_id, session_id=state.get("session_id")
    )
    return {"restaurants": rests, "restaurants_relaxed_tags": relaxed}


def get_user_profile_worker(state: AgentState) -> dict[str, Any]:
    user_id = state.get("user_id") or "demo_user"
    profile = get_user_profile_for_user(user_id)
    return {"user_profile": profile}


def execute_collect_node(state: AgentState) -> dict[str, Any]:
    """汇聚节点：execute 阶段并行 worker 跑完后由 LangGraph 自动 merge State；
    本节点用作 join point，把数量摘要打成日志（不真改 State）。"""
    pois = state.get("pois") or []
    rests = state.get("restaurants") or []
    # 不写 State，只是给路由 / 调用方一个稳定的 join 点
    return {}
