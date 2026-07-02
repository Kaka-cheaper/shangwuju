"""agent.graph —— LangGraph Plan-and-Execute 业界标配重构（v1）。

设计依据：
- LangChain 官方 workflows-agents 范式：Routing + Plan-and-Execute + Evaluator-Optimizer
- 学术派旅行规划共识（arxiv 2509.21842 / 2512.11271 / 2405.18208）：
  plan → execute → critic → replan 四段
- AWS Nova travel agent 实现（2025-08）：router → action nodes 拓扑

核心拓扑（详见 build.py）：
    START → router_node → {chitchat | intent} → planner → execute(并行) →
        assemble → critic → {narrate | replan_router} → END

各节点职责：
- router_node      : 6 类输入分类（复用旧 router.py）
- chitchat_node    : 暖心回话（router 已生成 reply_text）
- intent_node      : LLM 意图抽取（复用 intent_parser.py）
- planner_node     : LLM 出 PlanBlueprint + 4 维 weights
                     （复用 blueprint_llm + weights_llm）
- execute_node     : Send API 并行调查询类工具
- assemble_node    : 蓝图→Itinerary（复用 assemble_blueprint）
- critic_node      : 7 类 ViolationCode（复用 critics_v2）
- replan_router    : LLM backprompt（≤2 次） / ILS 兜底（复用 planner_hybrid）
- narrate_node     : 暖语气文案（复用 narrator）；narrate → END 是图的真实终点
- refiner_node     : 反馈合并（复用 refiner）

确认（confirm）不是图内节点（ADR-0012 决策 2「结构诚实」）：narrate → END 之后，
三按钮里 confirm 由 /chat/confirm 走 HTTP 旁路直调 graph/nodes/execute_finalize.py
的 execute_finalize_node 函数完成下单，再用 aupdate_state 把终版方案 +
user_decision="confirm" 回写进本图 checkpoint（见 api/_streams/graph_confirm.py）；
refine 由前端再发一轮 /chat/turn 触发新的 graph 执行；cancel 不触发任何后端调用。

不负责（仍由旧模块管）：
- 9 工具实现       (在 backend/tools/)
- LLM 客户端       (在 agent/llm_client.py)
- 跨 turn 会话真相 (LangGraph checkpointer 自身，见 build.py 的 InMemorySaver /
                     AsyncRedisSaver；ADR-0012 决策 1)
- SSE 转换         (在 graph/sse_adapter.py，main.py 接入点)

复用纪律：
- 节点模块仅是 LangGraph 包装；不改原算法语义
- 算法核心（planner_hybrid / planner_llm_first / blueprint / critics_v2 等）零改动
- 旧 ReAct / V1 orchestrator 路径已随各自退役批次删除，不存在 fallback；
  USE_LANGGRAPH 已退役（ADR-0012 决策 5 · E-0-c）：/chat/turn 恒走图，
  /chat/confirm 也已统一到同一条 graph_confirm 流，该开关不再声明。
"""

from .build import build_graph, get_compiled_graph

__all__ = ["build_graph", "get_compiled_graph"]
