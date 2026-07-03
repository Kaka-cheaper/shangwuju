"""agent —— Agent 编排层（Planner + Executor）。

P2 落地内容（A 同学 owner）：
- llm_client.py     LLM 客户端 wrapper（DeepSeek 主 / 通义备 / Stub）
- intent_parser.py  意图解析（输出 IntentExtraction）
- planner.py        规则化 ReAct 主循环（plan_itinerary，rule 范式 Demo 安全网）
- refiner.py        用户反馈合并（Phase 0.6 新增）
- prompts/          system prompt + few-shot

LLM 主路径走 LangGraph（agent/graph/），ILS 加分路径走 ils_planner.plan_hybrid；
V1 双范式分发入口与两套 LLM planner（function_calling / llm_first 子策略）已退役删除（规划层收口）。

不负责：
- Tool 实现（在 backend/tools/）
- Mock 数据加载（在 backend/data/）
- HTTP/SSE 传输（在 backend/main.py）
"""

from .planning.planners.rule_planner import (
    PlannerResult,
    plan_itinerary,
    MAX_TOOL_CALLS_PER_KIND,
    MAX_TOTAL_TOOL_CALLS,
)
from .intent.refiner import refine_intent, RefinementError
from .intent.router import fallback_decision, make_planning_decision
from .intent.parser import parse_intent, IntentParseError
from .core.trace import Tracer, TraceRecord


__all__ = [
    # planner
    "PlannerResult",
    "plan_itinerary",
    "MAX_TOOL_CALLS_PER_KIND",
    "MAX_TOTAL_TOOL_CALLS",
    # refiner
    "refine_intent",
    "RefinementError",
    # router 壳2/壳3 决策构造器（ADR-0011 E-2-c：classify_input/RouterError 随
    # Layer 2 退役已删除，见 agent/intent/router.py 模块 docstring）
    "fallback_decision",
    "make_planning_decision",
    # intent
    "parse_intent",
    "IntentParseError",
    # trace
    "Tracer",
    "TraceRecord",
]
