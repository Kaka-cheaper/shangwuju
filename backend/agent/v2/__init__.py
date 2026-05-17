"""agent.v2 —— Pydantic AI 重构版 Agent 编排层。

为什么有 v2/：
- 旧 agent/ 是手写编排（intent_parser / router / planner / refiner / narrator 等 18 个 .py，5878 行）
- v2/ 用 Pydantic AI 框架重写，淘汰大量样板代码
- 提供原生 message_history 上下文管理（解决「dock 直接反馈无上下文」根因）
- 增量迁移：每个 Agent 单独定义，旧路径同时保留作 fallback

设计纪律：
- v2/ 内部不再写 LLM SDK 调用、retry、围栏剥离等基础设施——全部交给 Pydantic AI
- 业务逻辑（tools/）继续复用，用 @agent.tool 装饰器接入
- schema/ Pydantic 模型直接作为 output_type，零适配
- session_id 升级为 conversation_id，承载真 message_history

模块职责：
- model_factory.py    创建 OpenAI 兼容 model（DeepSeek / Qwen / 任何兼容服务）
- deps.py             AgentDeps（依赖注入：tools / mock data / user_id）
- conversation.py     ConversationStore（message_history 持久化）
- intent_agent.py     意图解析 Agent（替代 agent/intent_parser.py）
- router_agent.py     输入域路由 Agent（替代 agent/router.py）
- refiner_agent.py    反馈合并 Agent（替代 agent/refiner.py）
- narrator_agent.py   暖心开场白 Agent（替代 agent/narrator.py）
- planner_agent.py    主规划 Agent + @tool 装饰所有 backend.tools（替代 agent/planner*.py）
- orchestrator.py     单一入口编排：router → planner / refiner，message_history 跨 turn 持久
"""
