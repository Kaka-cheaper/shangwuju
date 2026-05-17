"""verify_v2_turn —— v2 单一入口 /chat/turn + ConversationStore 端到端验证。

测试场景：
  1. 用户首次输入「家庭主线」 → /chat/turn 自动走 fresh 路径 → 行程出来
  2. 用户在 dock 直接输入「太远了，3 公里以内」 → /chat/turn 自动识别为 feedback
     → 走 refine 路径 → 行程被调整为 3km 内
  3. 验证 ConversationState.messages 含完整 4 条对话（用户 1 + Agent 1 + 反馈 1 + Agent 2）
  4. 验证 X-Turn-Kind 响应头（fresh / feedback）

注意：Phase 0.12 起 /chat/turn 默认走 ReAct 路径（USE_REACT_AGENT=1）。
本脚本强制设 USE_REACT_AGENT=0 来测试旧路径回归；ReAct 路径有独立 verify_v2_react.py。

跑法：
  $env:LLM_PROVIDER='stub'
  .venv\Scripts\python.exe -m scripts.verify_v2_turn
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# 必须在 import main 之前设环境变量（main.py 端点读 USE_REACT_AGENT）
os.environ["USE_REACT_AGENT"] = "0"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from main import app


def _consume_sse(client: TestClient, path: str, body: dict) -> tuple[list[tuple[str, dict]], dict]:
    """同步消费 SSE 流。返回 (events, response_headers)。"""
    events: list[tuple[str, dict]] = []
    headers: dict = {}
    with client.stream("POST", path, json=body) as r:
        assert r.status_code == 200, r.status_code
        headers = dict(r.headers)
        block_event: str | None = None
        block_data: str | None = None
        for line in r.iter_lines():
            if line == "":
                if block_event and block_data:
                    events.append((block_event, json.loads(block_data)))
                block_event = None
                block_data = None
                continue
            if line.startswith("event:"):
                block_event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                block_data = line[len("data:") :].strip()
        if block_event and block_data:
            events.append((block_event, json.loads(block_data)))
    return events, headers


async def _run() -> int:
    c = TestClient(app)
    session_id = "verify_v2_turn_001"

    # ---- Turn 1：首次规划 ----
    print("\n[Turn 1] /chat/turn 首次输入...")
    events1, h1 = _consume_sse(
        c,
        "/chat/turn",
        {
            "message": "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
            "session_id": session_id,
            "scenario_id": "S1",
        },
    )
    types1 = [t for t, _ in events1]
    print(f"  X-Turn-Kind = {h1.get('x-turn-kind')}")
    print(f"  events: {len(events1)}; types tail = {types1[-3:]}")
    assert h1.get("x-turn-kind") == "fresh", h1
    assert "itinerary_ready" in types1
    assert "agent_narration" in types1, "应推 narration"

    # 找 itinerary_ready 的 distance_max_km 痕迹
    itin1 = next((p["payload"] for t, p in events1 if t == "itinerary_ready"), None)
    assert itin1 is not None
    print(f"  itinerary summary: {itin1['summary'][:40]}")

    # ---- Turn 2：用户在 dock 直接说「太远了，3 公里以内」 ----
    print("\n[Turn 2] /chat/turn 直接输入反馈（不点「说说哪不对」）...")
    events2, h2 = _consume_sse(
        c,
        "/chat/turn",
        {
            "message": "太远了，希望 3 公里以内",
            "session_id": session_id,
        },
    )
    types2 = [t for t, _ in events2]
    print(f"  X-Turn-Kind = {h2.get('x-turn-kind')}")
    print(f"  events: {len(events2)}; types head = {types2[:5]}")

    assert h2.get("x-turn-kind") == "feedback", h2
    assert types2[0] == "refinement_start", types2[0]
    assert "refinement_done" in types2
    assert "itinerary_ready" in types2

    refine_done = next(
        (p["payload"] for t, p in events2 if t == "refinement_done"), None
    )
    assert refine_done is not None
    refined = refine_done["refined_intent"]
    print(f"  refined distance_max_km = {refined['distance_max_km']} (期望 3)")
    assert refined["distance_max_km"] == 3.0, refined["distance_max_km"]

    # changed_fields 中文摘要
    print(f"  changed_fields: {refine_done.get('changed_fields')}")
    assert any("距离" in s for s in refine_done.get("changed_fields", []))

    # ---- 验证 ConversationStore 状态 ----
    print("\n[ConversationStore] 验证持久化...")
    from agent.v2.conversation import get_default_store

    store = get_default_store()
    state = await store.get(session_id)
    assert state is not None, "ConversationState 应存在"
    print(f"  user_id: {state.user_id}")
    print(f"  messages count: {len(state.messages)} (期望 ≥ 4)")
    assert len(state.messages) >= 4, len(state.messages)
    print(f"  intent_snapshot.distance_max_km: {state.intent_snapshot['distance_max_km']}")
    assert state.intent_snapshot["distance_max_km"] == 3.0

    # 抽几条消息看内容
    print("  --- messages 内容 ---")
    for i, msg in enumerate(state.messages[-4:]):
        parts = getattr(msg, "parts", None) or []
        for p in parts:
            content = getattr(p, "content", None)
            if isinstance(content, str):
                print(f"    [{i}] {type(msg).__name__}: {content[:60]}")
                break

    print("\n✓ /chat/turn + ConversationStore 全部通过")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
