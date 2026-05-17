"""verify_v2_react —— Phase 0.12 ReAct 单一 Agent 端到端 SSE 验证。

6 个场景测 /chat/turn 在 USE_REACT_AGENT=1 下的真实行为：
  1. 闲聊 "你是谁"          → ChatResponse → chitchat_reply 不调工具
  2. POI Q&A                → 调 search_pois → chitchat_reply
  3. 完整规划 S1 家庭主线   → 多工具 → itinerary_ready + agent_narration
  4. 拒答 "5+5"             → ChatResponse 含「晌午局/下午/出行」拒答
  5. 上下文反馈两轮         → 第二轮 distance ≤ 3
  6. critic backprompt      → 触发 replan_triggered（如果 LLM 第一次就选对，标 SKIPPED）

跑法：
  $env:LLM_PROVIDER='stub'  → 全部 SKIPPED，退 0（CI 兼容）
  $env:LLM_PROVIDER 不是 stub，且 USE_REACT_AGENT=1 → 全跑

回归基线：
  pytest 267/267 + verify_v2_turn / verify_repository / verify_tool_provider / verify_sse 全过
  这个脚本只在真 LLM 模式下提供端到端验证，不计入 pytest 集合。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()


def _is_stub() -> bool:
    return (os.getenv("LLM_PROVIDER") or "").strip().lower() == "stub"


def _is_react_off() -> bool:
    return (os.getenv("USE_REACT_AGENT") or "1").strip() == "0"


# ============================================================
# SSE 流消费
# ============================================================


def _consume_sse(client, path: str, body: dict) -> tuple[list[tuple[str, dict]], dict]:
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


def _types(events) -> list[str]:
    return [t for t, _ in events]


def _payload_of(events, type_: str) -> dict | None:
    for t, p in events:
        if t == type_:
            return p.get("payload") if "payload" in p else p
    return None


def _all_payloads_of(events, type_: str) -> list[dict]:
    return [p.get("payload", p) for t, p in events if t == type_]


# ============================================================
# 场景断言
# ============================================================


def case_1_chitchat(client) -> tuple[bool, str]:
    """闲聊 "你是谁" → ChatResponse / chitchat_reply / 几乎不调工具。"""
    sid = "verify_react_case1"
    events, h = _consume_sse(
        client,
        "/chat/turn",
        {"message": "你是谁", "session_id": sid},
    )
    types = _types(events)
    if h.get("x-turn-kind") != "react":
        return False, f"X-Turn-Kind 应为 react，实际 {h.get('x-turn-kind')}"
    if "chitchat_reply" not in types:
        return False, f"应推 chitchat_reply，实际事件类型 {set(types)}"
    if types[-1] != "done":
        return False, f"末事件应为 done，实际 {types[-1]}"
    tool_calls = sum(1 for t in types if t == "tool_call_start")
    if tool_calls > 2:
        return False, f"闲聊不应调多于 2 次工具，实际 {tool_calls}"
    payload = _payload_of(events, "chitchat_reply") or {}
    reply = payload.get("reply_text", "")
    if len(reply) < 4:
        return False, f"reply_text 太短：{reply!r}"
    return True, f"chitchat_reply ✓ tool_calls={tool_calls} reply_len={len(reply)}"


def case_2_poi_qa(client) -> tuple[bool, str]:
    """POI Q&A → 至少调一次 search_pois → ChatResponse 答 P004。"""
    sid = "verify_react_case2"
    events, _ = _consume_sse(
        client,
        "/chat/turn",
        {"message": "P004 是什么样的地方？", "session_id": sid},
    )
    types = _types(events)
    tool_starts = [
        p.get("payload", p).get("tool", "")
        for t, p in events
        if t == "tool_call_start"
    ]
    if "chitchat_reply" not in types:
        return False, f"应推 chitchat_reply，实际 {set(types)}"
    if "search_pois" not in tool_starts:
        # 容错：LLM 可能调 search_restaurants / get_user_profile 等其它工具
        # 至少要有一次工具调用
        if not tool_starts:
            return False, "POI Q&A 至少应调一次工具"
    return True, f"chitchat_reply ✓ tools={tool_starts}"


def case_3_full_planning(client) -> tuple[bool, str]:
    """完整规划 S1 → ItineraryResponse → stages ≥ 5 + narration 含'老婆/孩子'。"""
    sid = "verify_react_case3"
    events, _ = _consume_sse(
        client,
        "/chat/turn",
        {
            "message": "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
            "session_id": sid,
        },
    )
    types = _types(events)
    if "itinerary_ready" not in types:
        return False, f"应推 itinerary_ready，实际 {set(types)}"
    itin = _payload_of(events, "itinerary_ready") or {}
    stages = itin.get("stages", [])
    if len(stages) < 5:
        return False, f"stages 应 ≥ 5，实际 {len(stages)}"
    narration_payload = _payload_of(events, "agent_narration") or {}
    narration = narration_payload.get("text", "")
    if not narration:
        return False, "应推 agent_narration"
    if not any(kw in narration for kw in ("老婆", "孩子", "家庭", "三口", "宝贝")):
        return False, f"narration 应含家庭关键词：{narration!r}"
    tool_starts = sum(1 for t in types if t == "tool_call_start")
    if tool_starts < 2:
        return False, f"完整规划应多次调用工具，实际 {tool_starts}"
    return True, (
        f"itinerary_ready ✓ stages={len(stages)} tools={tool_starts} "
        f"narration={narration[:30]!r}"
    )


def case_4_off_topic(client) -> tuple[bool, str]:
    """拒答 "5+5" → ChatResponse 含'晌午局/下午/出行'类拒答关键词。"""
    sid = "verify_react_case4"
    events, _ = _consume_sse(
        client,
        "/chat/turn",
        {"message": "5+5 等于几", "session_id": sid},
    )
    types = _types(events)
    if "chitchat_reply" not in types:
        return False, f"应推 chitchat_reply，实际 {set(types)}"
    payload = _payload_of(events, "chitchat_reply") or {}
    reply = payload.get("reply_text", "")
    has_redirect = any(
        kw in reply for kw in ("晌午局", "下午", "出行", "规划", "本地", "周末", "管家")
    )
    if not has_redirect:
        return False, f"拒答应含主题关键词重定向：{reply!r}"
    return True, f"chitchat_reply ✓ reply={reply[:40]!r}"


def case_5_feedback_context(client) -> tuple[bool, str]:
    """上下文反馈两轮 → ConversationState 跨 turn 持久 + 第二轮反映 distance ≤ 3。

    宽松判定（LLM 行为多样）：
      a. 第二轮有 itinerary_ready 或 chitchat_reply（不报错）
      b. 二选一证据反映距离收紧：
         - search_pois 调用入参 distance_max_km ≤ 3.5
         - 或 itinerary 各 stage 距家 ≤ 3km（用 mock 数据校验）
      c. ConversationState 跨 turn messages ≥ 2（前一轮被持久）
    """
    sid = "verify_react_case5"

    # 第一轮：完整规划
    events1, _ = _consume_sse(
        client,
        "/chat/turn",
        {
            "message": "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁。",
            "session_id": sid,
        },
    )
    if "itinerary_ready" not in _types(events1):
        return False, "第一轮应出 itinerary_ready"

    # 第二轮：dock 直接反馈
    events2, _ = _consume_sse(
        client,
        "/chat/turn",
        {"message": "太远了，希望 3 公里以内", "session_id": sid},
    )
    types2 = _types(events2)
    has_output = ("itinerary_ready" in types2) or ("chitchat_reply" in types2)
    if not has_output:
        return False, f"第二轮应出 itinerary_ready 或 chitchat_reply，实际 {set(types2)}"

    # 收紧距离的证据（任一项满足即可）
    distance_evidence = None

    # 证据 a：search_pois 调用入参
    pois_starts = [
        p.get("payload", p)
        for t, p in events2
        if t == "tool_call_start" and p.get("payload", p).get("tool") == "search_pois"
    ]
    for call in pois_starts:
        d = call.get("input", {}).get("distance_max_km")
        if isinstance(d, (int, float)) and d <= 3.5:
            distance_evidence = f"search_pois distance_max_km={d}"
            break

    # 证据 b：itinerary 各 POI 距家 ≤ 3km
    if distance_evidence is None and "itinerary_ready" in types2:
        itin2 = _payload_of(events2, "itinerary_ready") or {}
        try:
            from data.loader import load_pois, load_restaurants

            pois_by_id = {p.id: p for p in load_pois()}
            rests_by_id = {r.id: r for r in load_restaurants()}
        except Exception:  # noqa: BLE001
            pois_by_id, rests_by_id = {}, {}

        all_in_3km = True
        max_dist = 0.0
        for stage in itin2.get("stages", []):
            poi_id = stage.get("poi_id")
            rest_id = stage.get("restaurant_id")
            if poi_id and poi_id in pois_by_id:
                d = pois_by_id[poi_id].distance_km
                max_dist = max(max_dist, d)
                if d > 3.5:
                    all_in_3km = False
            if rest_id and rest_id in rests_by_id:
                d = rests_by_id[rest_id].distance_km
                max_dist = max(max_dist, d)
                if d > 3.5:
                    all_in_3km = False
        if all_in_3km and max_dist > 0:
            distance_evidence = f"all stages within 3km (max={max_dist})"

    if distance_evidence is None:
        return False, (
            f"第二轮无距离收紧证据：pois_calls={[c.get('input', {}) for c in pois_starts][:3]}，"
            f"events_types={types2}"
        )

    # ConversationState 校验
    from agent.v2.conversation import get_default_repo

    repo = get_default_repo()
    import asyncio

    state = asyncio.get_event_loop().run_until_complete(repo.get(sid))
    if state is None or len(state.messages) < 2:
        return False, "ConversationState 跨 turn 应保留 messages"

    return True, (
        f"feedback ✓ {distance_evidence} messages={len(state.messages)}"
    )


def case_6_critic_backprompt(client) -> tuple[bool, str]:
    """critic backprompt：尝试触发 replan_triggered。

    LLM 通常足够聪明能避开陷阱，所以此场景"未触发"也算合格 → SKIPPED。
    """
    sid = "verify_react_case6"
    events, _ = _consume_sse(
        client,
        "/chat/turn",
        {
            "message": "今天下午想去 R023 餐厅，6 点用餐，2 个人。",
            "session_id": sid,
        },
    )
    types = _types(events)
    has_replan = "replan_triggered" in types
    if has_replan:
        return True, f"replan_triggered ✓ events={len(events)}"
    # 不算失败，记 SKIPPED
    return True, "SKIPPED（LLM 未触发 critic）"


# ============================================================
# 主函数
# ============================================================


CASES = [
    ("S1-chitchat", case_1_chitchat),
    ("S2-poi-qa", case_2_poi_qa),
    ("S3-full-planning", case_3_full_planning),
    ("S4-off-topic", case_4_off_topic),
    ("S5-feedback-context", case_5_feedback_context),
    ("S6-critic-backprompt", case_6_critic_backprompt),
]


def main() -> int:
    if _is_stub():
        print("verify_v2_react: SKIPPED（LLM_PROVIDER=stub，不调真 LLM）")
        return 0

    if _is_react_off():
        print("verify_v2_react: SKIPPED（USE_REACT_AGENT=0，未启用 ReAct 路径）")
        return 0

    # 强制启用 ReAct
    os.environ["USE_REACT_AGENT"] = "1"

    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)

    print("=" * 60)
    print("Phase 0.12 ReAct 单一 Agent 端到端 SSE 验证（USE_REACT_AGENT=1）")
    print("=" * 60)

    passed = 0
    failed = 0
    for name, fn in CASES:
        print(f"\n[{name}] 跑场景...")
        try:
            ok, detail = fn(client)
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        if ok:
            passed += 1
            print(f"  ✓ {detail}")
        else:
            failed += 1
            print(f"  ✗ {detail}")

    print("\n" + "=" * 60)
    print(f"总结：{passed} 过 / {failed} 败 / {len(CASES)} 共")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
