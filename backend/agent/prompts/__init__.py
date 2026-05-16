"""prompts —— Agent 用的 system prompt 与 few-shot。

不负责：
- LLM 调用（在 agent/llm_client.py）
- 业务逻辑（在 agent/{intent_parser,planner}.py）
"""

from .system_prompt import (
    INTENT_PARSER_SYSTEM_PROMPT,
    INTENT_PARSER_FEW_SHOTS,
    PLANNER_SYSTEM_PROMPT,
)

__all__ = [
    "INTENT_PARSER_SYSTEM_PROMPT",
    "INTENT_PARSER_FEW_SHOTS",
    "PLANNER_SYSTEM_PROMPT",
]
