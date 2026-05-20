"""agent.graph.build —— LangGraph 拓扑编织。

最终拓扑：

START
  → router (条件分支)
       ├── chitchat → END
       ├── refiner → execute → planner → ...
       └── intent → execute → planner → ...

execute 阶段（并行 worker）：
  - search_pois_worker
  - search_restaurants_worker
  - get_user_profile_worker
所有 worker 完成后汇聚到 execute_collect

execute_collect → planner（出 weights + blueprint）
                → assemble（蓝图→Itinerary）
                → critic（验证）
                  ├── 通过 → narrate → interrupt(plan_ready) → ...
                  └── 硬违规 → replan_router
                              ├── llm_backprompt → planner（带 critic_feedback）
                              ├── ils_fallback → ils_replan → narrate
                              └── give_up → narrate

interrupt(plan_ready) HITL：
  ├── confirm → execute_finalize → END
  ├── refine → refiner → execute → planner → ...
  └── cancel → END

InMemorySaver checkpointer：thread_id=session_id，跨 turn 持久化 messages。
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agent.graph.nodes.assemble import assemble_node
from agent.graph.nodes.chitchat import chitchat_node
from agent.graph.nodes.critic import critic_node, route_after_critic
from agent.graph.nodes.execute import (
    execute_collect_node,
    get_user_profile_worker,
    search_pois_worker,
    search_restaurants_worker,
)
from agent.graph.nodes.execute_finalize import execute_finalize_node
from agent.graph.nodes.intent import intent_node
from agent.graph.nodes.narrate import narrate_node
from agent.graph.nodes.planner import planner_node
from agent.graph.nodes.refiner import refiner_node
from agent.graph.nodes.replan import (
    ils_replan_node,
    replan_router_node,
    route_after_replan,
)
from agent.graph.nodes.router import route_after_router, router_node
from agent.graph.state import AgentState


# ============================================================
# 模块级单例（首次 build 后缓存）
# ============================================================

_compiled_graph: Any = None


def build_graph(*, with_checkpointer: bool = True) -> Any:
    """构造并编译 LangGraph。

    Args:
        with_checkpointer: 是否加 InMemorySaver；测试时可关。
    """
    g = StateGraph(AgentState)

    # ---- 节点 ----
    g.add_node("router", router_node)
    g.add_node("chitchat", chitchat_node)
    g.add_node("intent", intent_node)
    g.add_node("refiner", refiner_node)

    # execute 阶段：并行 worker
    g.add_node("search_pois_worker", search_pois_worker)
    g.add_node("search_restaurants_worker", search_restaurants_worker)
    g.add_node("get_user_profile_worker", get_user_profile_worker)
    g.add_node("execute_collect", execute_collect_node)

    # plan 阶段
    g.add_node("planner", planner_node)
    g.add_node("assemble", assemble_node)

    # critic + replan
    g.add_node("critic", critic_node)
    g.add_node("replan_router", replan_router_node)
    g.add_node("ils_replan", ils_replan_node)

    # narrate + finalize
    g.add_node("narrate", narrate_node)
    g.add_node("execute_finalize", execute_finalize_node)

    # ---- 边 ----
    # START → router
    g.add_edge(START, "router")

    # router 后分支
    g.add_conditional_edges(
        "router",
        route_after_router,
        {
            "chitchat": "chitchat",
            "intent": "intent",
            "refiner": "refiner",
        },
    )

    # chitchat → END
    g.add_edge("chitchat", END)

    # intent / refiner 都进 execute（并行 worker）
    for src in ("intent", "refiner"):
        g.add_edge(src, "search_pois_worker")
        g.add_edge(src, "search_restaurants_worker")
        g.add_edge(src, "get_user_profile_worker")

    # 3 个 worker 都完成后汇聚 execute_collect
    g.add_edge("search_pois_worker", "execute_collect")
    g.add_edge("search_restaurants_worker", "execute_collect")
    g.add_edge("get_user_profile_worker", "execute_collect")

    # execute_collect → planner → assemble → critic
    g.add_edge("execute_collect", "planner")
    g.add_edge("planner", "assemble")
    g.add_edge("assemble", "critic")

    # critic 后分支
    g.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "narrate": "narrate",
            "replan_router": "replan_router",
        },
    )

    # replan_router 后分支
    g.add_conditional_edges(
        "replan_router",
        route_after_replan,
        {
            "planner": "planner",         # llm_backprompt 回 planner
            "ils_replan": "ils_replan",   # ils_fallback
            "narrate": "narrate",          # give_up
        },
    )

    # ils_replan → critic（再验一次）
    g.add_edge("ils_replan", "critic")

    # narrate → END（v1：interrupt 在 main.py 的 SSE 适配层暴露三按钮，不在 graph 内）
    # 三按钮的 confirm / refine / cancel 由前端再次发起 /chat/turn 触发新的 graph 执行：
    #   - confirm → 走 execute_finalize 路径（用户态字段携带）
    #   - refine  → 走 refiner_node 路径（user_input 是反馈）
    #   - cancel  → 不再触发 graph
    g.add_edge("narrate", END)
    g.add_edge("execute_finalize", END)

    # ---- 编译 ----
    if with_checkpointer:
        return g.compile(checkpointer=InMemorySaver())
    return g.compile()


def get_compiled_graph() -> Any:
    """模块级单例。"""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph(with_checkpointer=True)
    return _compiled_graph
