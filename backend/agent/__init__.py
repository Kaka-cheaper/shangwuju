"""agent —— Agent 编排层（Planner + Executor）。

P2 落地内容（A 同学 owner）：
- llm_client.py     LLM 客户端 wrapper（DeepSeek 主 / 通义备）
- intent_parser.py  意图解析（输出 IntentExtraction）
- planner.py        ReAct 循环主体
- executor.py       执行类 Tool 派发
- prompts/          system prompt + few-shot

不负责：
- Tool 实现（在 backend/tools/）
- Mock 数据加载（在 backend/data/）
- HTTP/SSE 传输（在 backend/main.py）
"""
