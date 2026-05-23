"""scripts.verify_langgraph —— LangGraph Plan-and-Execute 端到端验证。

3 个场景：
  1. planning 主路径：S1 家庭主线
  2. chitchat 路径：用户说"你好"
  3. feedback 路径：先 S1 拿到 itinerary，再"太远了 3 公里以内"

LLM_PROVIDER=stub 时跳过（CI 兼容）。

运行方式：
  cd backend && uv run scripts/verify_langgraph.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()


def _is_stub() -> bool:
    return (os.getenv("LLM_PROVIDER") or "").strip().lower() == "stub"


async def case_planning() -> bool:
    """S1 家庭主线：完整 plan-and-execute 路径。"""
    print("\n[1/3] planning 主路径（S1 家庭主线）")
    from agent.graph.sse_adapter import run_graph_stream

    events: list = []
    t0 = time.time()
    async for ev in run_graph_stream(
        user_input=(
            "今天下午想和老婆孩子出去玩几个小时，"
            "别离家太远，孩子 5 岁，老婆最近在减肥。"
        ),
        session_id="verify_langgraph_S1",
        user_id="demo_user",
    ):
        events.append(ev)
        # 简短打印
        kind = ev.type.value
        payload_preview = ""
        if kind == "agent_thought":
            payload_preview = ev.payload.get("text", "")[:60]
        elif kind == "tool_call_start":
            payload_preview = ev.payload.get("tool", "")
        elif kind == "intent_parsed":
            ints = ev.payload
            payload_preview = (
                f"distance={ints.get('distance_max_km')}km "
                f"social={ints.get('social_context')}"
            )
        elif kind == "itinerary_ready":
            nodes = ev.payload.get("nodes", [])
            hops = ev.payload.get("hops", [])
            mid = [n for n in nodes if n.get("target_kind") != "home"]
            payload_preview = (
                f"{len(nodes)} 节点（{len(mid)} 中间）/ {len(hops)} 通勤段"
            )
        elif kind == "agent_narration":
            payload_preview = ev.payload.get("text", "")[:60]
        elif kind == "stream_error":
            payload_preview = ev.payload.get("detail", "")[:80]
        print(f"      [{ev.seq}] {kind} - {payload_preview}")

    elapsed = time.time() - t0
    types = [e.type.value for e in events]
    print(f"\n      共 {len(events)} 个事件 ({elapsed:.2f}s)")

    # 断言：必须有 intent_parsed / itinerary_ready / done
    asserts = {
        "intent_parsed": "intent_parsed" in types,
        "itinerary_ready": "itinerary_ready" in types,
        "agent_narration": "agent_narration" in types,
        "done": "done" in types,
        "no_stream_error": "stream_error" not in types,
    }
    for k, ok in asserts.items():
        print(f"      {'OK' if ok else 'FAIL'}  {k}")
    return all(asserts.values())


async def case_chitchat() -> bool:
    """闲聊路径：用户说「你好」。"""
    print("\n[2/3] chitchat 路径（用户『你好』）")
    from agent.graph.sse_adapter import run_graph_stream

    events: list = []
    async for ev in run_graph_stream(
        user_input="你好啊，你能干嘛？",
        session_id="verify_langgraph_chitchat",
        user_id="demo_user",
    ):
        events.append(ev)

    types = [e.type.value for e in events]
    print(f"      事件序列：{types}")

    # 必须有 chitchat_reply + done，且**不应该**有 itinerary_ready
    asserts = {
        "chitchat_reply": "chitchat_reply" in types,
        "no_itinerary": "itinerary_ready" not in types,
        "done": "done" in types,
    }
    for k, ok in asserts.items():
        print(f"      {'OK' if ok else 'FAIL'}  {k}")
    return all(asserts.values())


async def case_feedback() -> bool:
    """feedback 路径：先 S1 跑一次，再发反馈。

    注意：LangGraph InMemorySaver 用 thread_id 同会话保存 messages 状态；
    但本次测试简化为「单 turn 直接走 feedback 路径」——先初始化已有 itinerary 的状态比较麻烦，
    用 looks_like_feedback 启发式：如果 state.itinerary 不存在，feedback 关键词不会匹配，
    会被 router LLM 当 planning。

    所以这里测的是「LLM 把『太远了 3km』分类为 planning 时下游不会崩」，feedback 路径的真测
    放到浏览器实测里（需要前端 itinerary 状态保留）。
    """
    print("\n[3/3] feedback-like 输入鲁棒性")
    from agent.graph.sse_adapter import run_graph_stream

    events: list = []
    async for ev in run_graph_stream(
        user_input="带老婆孩子出去玩，距离要 3 公里以内",
        session_id="verify_langgraph_feedback",
        user_id="demo_user",
    ):
        events.append(ev)

    types = [e.type.value for e in events]
    asserts = {
        "no_stream_error": "stream_error" not in types,
        "done": "done" in types,
    }
    for k, ok in asserts.items():
        print(f"      {'OK' if ok else 'FAIL'}  {k}")
    return all(asserts.values())


async def main() -> int:
    if _is_stub():
        print("[SKIPPED] LLM_PROVIDER=stub（设为真 LLM 后再跑）")
        return 0

    print("=" * 60)
    print("LangGraph 端到端验证")
    print("=" * 60)

    results = []
    for case_fn in (case_planning, case_chitchat, case_feedback):
        try:
            ok = await case_fn()
        except Exception as e:  # noqa: BLE001
            print(f"      EXCEPTION: {e}")
            import traceback

            traceback.print_exc()
            ok = False
        results.append(ok)

    passed = sum(1 for r in results if r)
    total = len(results)
    print("\n" + "=" * 60)
    print(f"结果：{passed}/{total} 通过")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
