"""agent.core —— 全员共享底座（spec agent-directory-restructure）。

含 LLM client / observability / feedback detector / trace 等无业务逻辑的基础设施。
不放业务编排（在 graph/）、不放业务规则（在 planning/）、不放运行时框架（在 runtime/）。
"""
