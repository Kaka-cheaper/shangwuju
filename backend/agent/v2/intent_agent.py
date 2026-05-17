"""agent.v2.intent_agent —— 意图解析 thin wrapper。

设计决策（重要）：
    最初尝试用 Pydantic AI 全量替换旧 intent_parser，但 DeepSeek 通过
    OpenAI Function Calling 输出 nested array of objects（companions /
    *_constraints）时倾向于"省略未明示字段"——尽管 prompt 里有强约束
    抽取规则，工具描述生成的 schema 让 LLM 觉得这些字段"可选"。

    旧 intent_parser 用 response_format={"type":"json_object"} + 手动
    json.loads + Pydantic.model_validate，DeepSeek 在这种"prompt 引导
    JSON"模式下输出更完整。这是它官方文档推荐的模式。

    所以：保留旧 intent_parser 实现，v2 这里只做 thin wrapper，
    把"用 v2 接口（含 user_id 注入 prior）+ 旧 LLM 客户端"组合好。

    后续 router/refiner/narrator/planner 的迁移仍走 Pydantic AI，因为
    它们要么是简单结构化输出，要么是多工具调用 + message_history，
    Pydantic AI 在这些场景的优势压倒一切。
"""

from __future__ import annotations

from agent.intent_parser import IntentParseError, parse_intent
from agent.llm_client import get_llm_client
from schemas import IntentExtraction


def parse_intent_v2_sync(
    user_input: str,
    *,
    user_id: str = "demo_user",
    session_id: str = "",
) -> IntentExtraction:
    """同步意图解析（旧路径包装，保持 v2 接口风格）。

    Args:
        user_input: 用户原始输入
        user_id: 当前用户（注入 persona / memory prior 到 system prompt）
        session_id: 仅用于日志关联

    Raises:
        IntentParseError: schema 校验失败 + 重试用尽
    """
    client = get_llm_client()
    return parse_intent(user_input, client=client, user_id=user_id, max_retries=1)


async def parse_intent_v2(
    user_input: str,
    *,
    user_id: str = "demo_user",
    session_id: str = "",
) -> IntentExtraction:
    """异步入口（其实是同步在 to_thread 跑；旧 intent_parser 是同步的）。"""
    import asyncio

    return await asyncio.to_thread(
        parse_intent_v2_sync,
        user_input,
        user_id=user_id,
        session_id=session_id,
    )


__all__ = ["parse_intent_v2", "parse_intent_v2_sync", "IntentParseError"]
