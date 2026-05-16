"""verify_sse —— 走通 /chat/stream 整条 SSE 事件序列，给 P3 W3 的联调自检用。

人类视角验收：
- 看到 14+ 条事件、首事件 = intent_parsed、末事件 = done
- 中间出现至少 1 次 replan_triggered（家庭主场景埋的 E1）
- itinerary_ready 含 ≥ 5 段 stages
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from main import app  # noqa: E402


def _consume_sse(client: TestClient, path: str, body: dict) -> list[tuple[str, dict]]:
    """同步消费一次 SSE 流。返回 [(event_type, decoded_json), ...]。"""
    events: list[tuple[str, dict]] = []
    with client.stream("POST", path, json=body) as r:
        assert r.status_code == 200, r.status_code
        assert "text/event-stream" in r.headers.get("content-type", ""), r.headers

        block_event: str | None = None
        block_data: str | None = None
        for line in r.iter_lines():
            # httpx iter_lines 会自动 strip \r\n；空行 = 一个 SSE 块结束
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
        # 最后可能没有空行收尾
        if block_event and block_data:
            events.append((block_event, json.loads(block_data)))
    return events


def main() -> int:
    c = TestClient(app)

    # /health
    h = c.get("/health").json()
    assert h["status"] == "ok", h
    print(f"[health] {h}")

    # /scenarios
    s = c.get("/scenarios").json()
    assert len(s["scenarios"]) == 8, len(s["scenarios"])
    print(f"[scenarios] {len(s['scenarios'])} 个场景；首条 {s['scenarios'][0]['title']}")

    # /chat/stream
    print("[chat/stream] 开始拉取 SSE 流...")
    events = _consume_sse(
        c,
        "/chat/stream",
        {
            "message": "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
            "session_id": "verify_sse_001",
            "scenario_id": "S1",
        },
    )

    print(f"[chat/stream] 收到 {len(events)} 条事件")
    for i, (etype, e) in enumerate(events):
        seq = e.get("seq")
        print(f"  #{i} seq={seq} type={etype}")

    types = [t for t, _ in events]
    assert types[0] == "intent_parsed", types[0]
    assert types[-1] == "done", types[-1]
    assert "replan_triggered" in types, "异常重规划必须出现"
    itin_idx = types.index("itinerary_ready")
    itin = events[itin_idx][1]["payload"]
    assert len(itin["stages"]) >= 5, len(itin["stages"])
    print(f"[chat/stream] ✓ 首事件 intent_parsed，末事件 done")
    print(f"[chat/stream] ✓ 含异常重规划")
    print(f"[chat/stream] ✓ 行程含 {len(itin['stages'])} 段")

    # /chat/confirm
    print("[chat/confirm] 验证执行流...")
    confirm_events = _consume_sse(
        c,
        "/chat/confirm",
        {"session_id": "verify_sse_001", "decision": "confirm"},
    )

    types2 = [t for t, _ in confirm_events]
    print(f"[chat/confirm] 收到 {len(confirm_events)} 条事件 → {types2}")
    assert types2[-1] == "done"
    final_itin_payload = next(
        (p for t, p in reversed(confirm_events) if t == "itinerary_ready"), None
    )
    assert final_itin_payload is not None, "confirm 后应再推一次 itinerary_ready"
    assert final_itin_payload["payload"]["share_message"], "应含转发文案"
    assert len(final_itin_payload["payload"]["orders"]) >= 1, "应含至少一条订单"
    print(f"[chat/confirm] ✓ 含 share_message 与 orders")

    print("\n✓ /health /scenarios /chat/stream /chat/confirm 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
