"""agent.runtime —— 运行时框架模块目录（spec agent-directory-restructure）。

不放业务编排（在 graph/）、不放业务规则（在 planning/）、不放底座（在 core/）。

历史：ReActAgent / Orchestrator（V1/V2 运行时）与 Conversation（ConversationState/
Repository，ADR-0012 决策 3「旧仓库葬礼」）均已随各自退役批次删除。当前目录下
仅剩 tools/（agent.runtime.tools.search_adapter 等运行时工具适配）。
"""
