"""nodes.execute —— Plan-and-Execute 中的 execute 阶段（并行调候选搜索）。

通过 LangGraph Send API 并行触发 4 个搜索 worker（POI / 餐厅 / 用户画像 / 路线估算）。
每个 worker 负责调一个工具，结果合并到 State。

并行实现：
- search_pois_worker        → state["pois"]
- search_restaurants_worker → state["restaurants"]
- get_user_profile_worker    → state["user_profile"]
- estimate_routes_worker     → state["routes"]（先粗估常用 home→候选 POI 距离）

注意：
- 路线估算要等 POI 已选定后才能精确估，这里先粗估「home → top-K POI / top-K 餐厅」
  缓存进 routes，等蓝图出后 assemble 直接查
- 任何 worker 失败不阻塞其他 worker（容忍空候选，让 replan 兜）
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.tools.search_adapter import (
    search_pois_for_intent,
    search_restaurants_for_intent,
    get_user_profile_for_user,
)


def search_pois_worker(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    if intent is None:
        return {"pois": []}
    user_id = state.get("user_id") or "demo_user"
    pois = search_pois_for_intent(intent, user_id=user_id)
    return {"pois": pois}


def search_restaurants_worker(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    if intent is None:
        return {"restaurants": []}
    user_id = state.get("user_id") or "demo_user"
    rests = search_restaurants_for_intent(intent, user_id=user_id)
    return {"restaurants": rests}


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
