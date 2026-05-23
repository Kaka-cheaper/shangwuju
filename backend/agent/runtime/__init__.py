"""agent.runtime —— Pydantic AI ReAct 运行时框架（spec agent-directory-restructure）。

含 ReActAgent / Orchestrator / Conversation / ToolProvider 等运行时框架模块。
不放业务编排（在 graph/）、不放业务规则（在 planning/）、不放底座（在 core/）。

注：本目录的 observability.py 与 core/observability_init.py 不同——
后者是 OpenTelemetry instrument 入口，前者是 Pydantic AI 专用的 observability helper。
"""
