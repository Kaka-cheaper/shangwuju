"""agent.v2.router_agent —— 输入域路由 thin wrapper。

设计决策（与 intent_agent.py 同源）：
    Pydantic AI 的 OpenAI Function Calling 模式（ToolOutput）在 DeepSeek 上
    对结构化输出（含 enum + nested list）不稳定，常输出 "a" 等垃圾字符。
    旧 agent/router.py 用 response_format={"type":"json_object"}，DeepSeek
    官方推荐模式，输出稳定且能严格走我们的 Pydantic v2 校验。

    结论：保留旧 router 实现，v2 这里做 thin wrapper 提供异步接口与
    AgentDeps 风格的统一入口。

后续 narrator / planner 可以用 Pydantic AI 因为：
- narrator 输出纯文本，不依赖工具调用 schema 严格性
- planner 用 Pydantic AI 是为了 @tool 装饰器 + message_history 跨 turn 持久化
"""

from __future__ import annotations

import asyncio

from agent.llm_client import get_llm_client
from agent.router import classify_input, fallback_decision, RouterError
from schemas import RouterDecision


def classify_input_v2_sync(
    user_input: str,
    *,
    user_id: str = "demo_user",
    session_id: str = "",
) -> RouterDecision:
    """同步路由分类（旧实现包装）。

    Args:
        user_input: 用户原始输入
        user_id: 当前用户（router 不强依赖，但保留接口对齐）
        session_id: 仅用于日志关联

    Raises:
        RouterError: LLM 抛错或 schema 校验失败
    """
    client = get_llm_client()
    return classify_input(user_input, client=client)


async def classify_input_v2(
    user_input: str,
    *,
    user_id: str = "demo_user",
    session_id: str = "",
) -> RouterDecision:
    """异步入口（在 to_thread 跑同步实现）。"""
    return await asyncio.to_thread(
        classify_input_v2_sync,
        user_input,
        user_id=user_id,
        session_id=session_id,
    )


def fallback_decision_v2(
    user_input: str,
    *,
    reason: str = "router_fallback",
) -> RouterDecision:
    """LLM 不可用时兜底（保留 v2 命名风格）。"""
    return fallback_decision(user_input, reason=reason)


__all__ = [
    "classify_input_v2",
    "classify_input_v2_sync",
    "fallback_decision_v2",
    "RouterError",
]
