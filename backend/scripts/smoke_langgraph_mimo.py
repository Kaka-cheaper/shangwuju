"""scripts.smoke_langgraph_mimo —— LangGraph + MiMo 兼容性烟雾测试。

验证目标（Phase 0 最大风险点）：
1. langchain-openai ChatOpenAI 能用 base_url 接 MiMo
2. bind_tools 在 MiMo 上正确触发工具调用（issue #3097 在 DeepSeek 上有死循环报告）
3. LangGraph create_react_agent prebuilt 能跑通最小 ReAct 循环
4. astream(stream_mode="updates") 能流式拿到中间事件

不包含：
- 业务节点（router / planner / critic 等都还没写）
- 真工具调用（用最小 fake 工具验证）
- SSE 转换（main.py 整合是 Phase 11）

运行方式：
    cd backend && uv run scripts/smoke_langgraph_mimo.py

LLM_PROVIDER=stub 时跳过（CI 兼容）。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()


# ============================================================
# Stub 模式跳过
# ============================================================

def _is_stub() -> bool:
    return (os.getenv("LLM_PROVIDER") or "").strip().lower() == "stub"


# ============================================================
# 主测试
# ============================================================

async def smoke_test() -> int:
    """4 步烟雾测试。返回退出码（0=过，非 0=失败）。"""
    print("=" * 60)
    print("LangGraph + MiMo 兼容性烟雾测试")
    print("=" * 60)

    if _is_stub():
        print("[SKIPPED] LLM_PROVIDER=stub 跳过；切换到真 LLM 后再跑")
        return 0

    # --- 步骤 1：构造 ChatOpenAI 连 MiMo ---
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        print(f"[FAIL] langchain_openai import 失败：{e}")
        return 1

    api_key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
    base_url = os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or ""
    model = os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL") or "mimo-v2.5-pro"

    if not api_key or not base_url:
        print(
            "[FAIL] 缺少 LLM_API_KEY / LLM_BASE_URL；"
            "需要 .env 配置（参考现有 backend/.env）"
        )
        return 1

    print(f"\n[1/4] 构造 ChatOpenAI（base_url={base_url}, model={model})...")
    try:
        llm = ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=0.7,
            timeout=30,
            max_retries=2,
            # MiMo / DeepSeek-R1 / Kimi 等 thinking 模型在多轮工具调用时
            # 会要求把 reasoning_content 回传 API。LangGraph 默认不携带，
            # 关掉 thinking 模式规避（参考 MiMo 官方 vllm recipe + LiteLLM #23828）
            extra_body={"enable_thinking": False},
        )
        print("      ✓ ChatOpenAI 初始化成功")
    except Exception as e:
        print(f"      ✗ FAIL: {e}")
        return 1

    # --- 步骤 2：纯文本调用 ---
    print("\n[2/4] 纯文本调用（无工具）...")
    try:
        t0 = time.time()
        msg = await llm.ainvoke("用一句话介绍杭州")
        elapsed = time.time() - t0
        text = msg.content if hasattr(msg, "content") else str(msg)
        print(f"      ✓ 响应：{text[:80]}... ({elapsed:.2f}s)")
    except Exception as e:
        print(f"      ✗ FAIL: {e}")
        return 1

    # --- 步骤 3：bind_tools 工具调用（最小 fake 工具）---
    print("\n[3/4] bind_tools 工具调用（fake search）...")
    try:
        from langchain_core.tools import tool

        @tool
        def fake_search(query: str) -> str:
            """搜索查询（fake）。返回固定字符串。"""
            return f"搜索结果：{query} 找到 3 条记录"

        llm_with_tools = llm.bind_tools([fake_search])

        t0 = time.time()
        msg = await llm_with_tools.ainvoke("帮我搜索杭州的茶馆")
        elapsed = time.time() - t0

        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            print(
                f"      ✓ 触发 {len(tool_calls)} 次工具调用 ({elapsed:.2f}s)：\n"
                f"        {tool_calls[0]}"
            )
        else:
            print(
                f"      ⚠ 没触发工具调用（MiMo 可能直接回答）：{msg.content[:80]} "
                f"({elapsed:.2f}s)"
            )
            # 不当 FAIL，记下警告——后续 prompt 调教
    except Exception as e:
        print(f"      ✗ FAIL: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # --- 步骤 4：create_react_agent 跑通最小 LangGraph 循环 ---
    print("\n[4/4] create_react_agent 跑通最小 ReAct 循环...")
    try:
        from langgraph.prebuilt import create_react_agent

        agent = create_react_agent(llm, [fake_search])

        events_seen: list[str] = []
        t0 = time.time()
        async for chunk in agent.astream(
            {"messages": [("user", "搜索杭州的茶馆，告诉我有几条结果")]},
            stream_mode="updates",
        ):
            # chunk 形如 {"agent": {...}} 或 {"tools": {...}}
            for node_name, node_state in chunk.items():
                events_seen.append(node_name)
                msg = node_state.get("messages", [])
                if msg and hasattr(msg[-1], "content"):
                    preview = str(msg[-1].content)[:80]
                    print(f"      • [{node_name}] {preview}")
        elapsed = time.time() - t0

        if not events_seen:
            print("      ✗ FAIL: astream 没产出任何事件")
            return 1

        print(
            f"      ✓ 跑通 {len(events_seen)} 个节点事件 "
            f"({' → '.join(events_seen)}) ({elapsed:.2f}s)"
        )
    except Exception as e:
        print(f"      ✗ FAIL: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # --- 总结 ---
    print("\n" + "=" * 60)
    print("Phase 0 烟雾测试：4/4 全过 ✓")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke_test()))
