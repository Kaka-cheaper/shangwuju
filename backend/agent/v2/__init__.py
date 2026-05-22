"""agent.v2 —— Pydantic AI ReAct 路径（已冻结，仅作 LangGraph fallback）。

⚠️ 冻结声明（2026-05-22）：
    本子包是 Phase 0.12 的 ReAct 主路径，自 Phase 0.20 LangGraph 上线后
    降级为 fallback。**不再添加新功能**，所有新功能改动应在 `agent/graph/` 下完成。

    保留理由：
    - main.py /chat/turn 的 USE_REACT_AGENT=1 fallback 链（LangGraph 路径异常时启用）
    - critics_v2 被 LangGraph critic_node 与 react_agent 共用（不要乱动）
    - tool_provider / observability / conversation 的「商业演进抽象」叙事还在文档/路演引用

    可以做的：
    - bug fix（不改公共接口）
    - 删除真死代码（无引用的子模块）

    禁止做的：
    - 加新 Agent / 新输出类型 / 新 critic 规则
    - 修改 react_agent.py 行为以匹配 LangGraph 行为（要改去 graph/）

模块职责：
- model_factory.py    OpenAI 兼容 model 工厂（react_agent 用）
- deps.py             AgentDeps（依赖注入容器）
- conversation.py     ConversationRepository 抽象（v0.11 创新点；商业演进路径叙事）
- output_types.py     ChatResponse / ItineraryResponse / AgentOutput Union
- tool_provider.py    ToolProvider Protocol + Mock + Gaode/Dianping stub（商业演进叙事）
- observability.py    structlog 包装 + trace_span（演进叙事）
- react_agent.py      Pydantic AI 主 Agent（fallback 入口）
- orchestrator.py     run_react_turn 流式包装 + 跨 turn 持久化 hooks
- critics_v2.py       Itinerary critic 兜底（被 graph/critic_node 共用）

已删（2026-05-22 冷代码清理）：
- intent_agent.py / router_agent.py：被 react_agent.py 内部 unified_agent 取代后无引用
"""
