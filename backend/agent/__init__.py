"""agent —— Agent 编排层（Planner + Executor）。

P2 落地内容（A 同学 owner）：
- llm_client.py     LLM 客户端 wrapper（DeepSeek 主 / 通义备 / Stub）
- intent_parser.py  意图解析（输出 IntentExtraction）
- planner.py        规则化 ReAct 主循环 + plan_itinerary_with_mode 双范式入口
- llm_planner.py    LLM Function Calling 自主规划（Phase 0.6 新增）
- refiner.py        用户反馈合并（Phase 0.6 新增）
- executor.py       执行类 Tool 派发
- prompts/          system prompt + few-shot

不负责：
- Tool 实现（在 backend/tools/）
- Mock 数据加载（在 backend/data/）
- HTTP/SSE 传输（在 backend/main.py）
"""

from .planner import (
    PlannerResult,
    plan_itinerary,
    plan_itinerary_with_mode,
    MAX_TOOL_CALLS_PER_KIND,
    MAX_TOTAL_TOOL_CALLS,
)
from .llm_planner import plan_itinerary_llm
from .refiner import refine_intent, RefinementError
from .intent_parser import parse_intent, IntentParseError
from .executor import execute_plan, ExecutionResult
from .trace import Tracer, TraceRecord


__all__ = [
    # planner
    "PlannerResult",
    "plan_itinerary",
    "plan_itinerary_with_mode",
    "plan_itinerary_llm",
    "MAX_TOOL_CALLS_PER_KIND",
    "MAX_TOTAL_TOOL_CALLS",
    # refiner
    "refine_intent",
    "RefinementError",
    # intent
    "parse_intent",
    "IntentParseError",
    # executor
    "execute_plan",
    "ExecutionResult",
    # trace
    "Tracer",
    "TraceRecord",
]
