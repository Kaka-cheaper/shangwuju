"""verify_refine —— Phase 0.6 端到端验证 /chat/refine 闭环。

验收对象：
- POST /chat/refine 端点（B 实现，main.py 内 _refine_stream）
- SSE 事件序列含 refinement_start / refinement_done
- 反馈「太远了，希望 3 公里以内」→ refined_intent.distance_max_km == 3
- 后续 search_pois.input.distance_max_km 也反映新值（证明 stub_stream 真的接受 override）
- 主路径事件序列保留：tool_call_start/end / replan_triggered / itinerary_ready / done

跑法：
    cd backend && uv run python -m scripts.verify_refine
"""

from __future__ import annotations

import json
import sys
from typing import Any

from fastapi.testclient import TestClient

from main import _SESSION_STORE, app


# 演示场景集 §三 S1 输入
S1_MESSAGE = "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。"
SESSION_ID = "verify_refine_session"


def _line(ok: bool, msg: str) -> tuple[bool, str]:
    return ok, ("  ✓ " if ok else "  ✗ ") + msg


def _parse_sse_block(block: str) -> dict[str, Any] | None:
    """从一段 SSE 文本块解析单个事件（event/id/data 三键）。"""
    event_type: str | None = None
    data_lines: list[str] = []
    for raw in block.splitlines():
        if raw.startswith(":"):
            continue
        if raw.startswith("event:"):
            event_type = raw[6:].strip()
        elif raw.startswith("data:"):
            data_lines.append(raw[5:].lstrip())
    if not event_type or not data_lines:
        return None
    try:
        payload = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return None
    return {"event": event_type, **payload}


def _drain_sse(client: TestClient, path: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    """把 SSE 响应分块解析为事件列表。"""
    events: list[dict[str, Any]] = []
    with client.stream("POST", path, json=body) as resp:
        if resp.status_code != 200:
            raise AssertionError(f"{path} 返回 {resp.status_code}: {resp.text}")
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while True:
                # 兼容 \r\n\r\n / \n\n 两种分隔符
                idx_lf = buf.find("\n\n")
                idx_crlf = buf.find("\r\n\r\n")
                candidates = [i for i in (idx_lf, idx_crlf) if i >= 0]
                if not candidates:
                    break
                idx = min(candidates)
                sep_len = 4 if idx == idx_crlf else 2
                block = buf[:idx]
                buf = buf[idx + sep_len:]
                ev = _parse_sse_block(block)
                if ev is not None:
                    events.append(ev)
    return events


def main() -> int:
    print("=== Phase 0.6 /chat/refine 端到端自检 ===")
    results: list[tuple[bool, str]] = []
    client = TestClient(app)

    # ---- 0. 准备：先跑 /chat/stream 建立 session ----
    _SESSION_STORE.pop(SESSION_ID, None)
    seed_events = _drain_sse(
        client,
        "/chat/stream",
        {"message": S1_MESSAGE, "session_id": SESSION_ID, "scenario_id": "S1"},
    )
    seed_done = any(e["event"] == "done" for e in seed_events)
    seed_intent = next(
        (e for e in seed_events if e["event"] == "intent_parsed"), None
    )
    results.append(_line(seed_done, f"前置 /chat/stream 跑通（{len(seed_events)} 事件）"))
    seed_distance = (
        seed_intent["payload"]["distance_max_km"] if seed_intent else None
    )
    results.append(_line(seed_distance == 5, f"原 intent distance_max_km={seed_distance}"))

    # ---- 1. 调 /chat/refine（反馈"太远了，希望 3 公里以内"）----
    refine_events = _drain_sse(
        client,
        "/chat/refine",
        {"session_id": SESSION_ID, "feedback_text": "太远了，希望 3 公里以内"},
    )
    types_in_order = [e["event"] for e in refine_events]
    results.append(
        _line(
            types_in_order[:2] == ["refinement_start", "refinement_done"],
            f"refine 流首两条事件：{types_in_order[:2]}",
        )
    )
    results.append(
        _line(types_in_order[-1] == "done", f"refine 流以 done 收尾：{types_in_order[-1]}")
    )

    # ---- 2. refinement_done.refined_intent.distance_max_km == 3 ----
    done_event = next(
        (e for e in refine_events if e["event"] == "refinement_done"), None
    )
    refined_distance = None
    changed_fields: list[str] = []
    if done_event is not None:
        payload = done_event["payload"]
        refined_distance = payload["refined_intent"]["distance_max_km"]
        changed_fields = payload["changed_fields"]
    results.append(
        _line(
            refined_distance == 3,
            f"refined_intent.distance_max_km={refined_distance}（期望 3）",
        )
    )
    results.append(
        _line(
            any("距离" in c for c in changed_fields),
            f"changed_fields 含距离变更：{changed_fields}",
        )
    )

    # ---- 3. 后续 search_pois.input.distance_max_km == 3 ----
    sp_starts = [
        e
        for e in refine_events
        if e["event"] == "tool_call_start"
        and e["payload"]["tool"] == "search_pois"
    ]
    sp_distance = (
        sp_starts[0]["payload"]["input"]["distance_max_km"] if sp_starts else None
    )
    results.append(
        _line(
            sp_distance == 3,
            f"search_pois.input.distance_max_km={sp_distance}（验证 stub_stream 真用 refined）",
        )
    )

    # ---- 4. POI 候选必然有 distance ≤ 3 ----
    sp_ends = [
        e
        for e in refine_events
        if e["event"] == "tool_call_end"
        and e["payload"]["tool"] == "search_pois"
    ]
    if sp_ends:
        candidates = sp_ends[0]["payload"]["output"]["candidates"]
        all_within = all(c["distance_km"] <= 3 for c in candidates)
        results.append(
            _line(
                all_within,
                f"POI 候选全在 3km 内：{[(c['id'], c['distance_km']) for c in candidates]}",
            )
        )
    else:
        results.append(_line(False, "未捕获 search_pois.tool_call_end"))

    # ---- 5. 主路径事件序列保留（itinerary_ready + replan_triggered）----
    has_itin = any(e["event"] == "itinerary_ready" for e in refine_events)
    has_replan = any(e["event"] == "replan_triggered" for e in refine_events)
    results.append(_line(has_itin, "refine 流仍输出 itinerary_ready"))
    results.append(_line(has_replan, "refine 流仍触发 replan_triggered（E1 异常韧性）"))

    # ---- 6. session_id 不存在 → 422 ----
    bad = client.post(
        "/chat/refine",
        json={"session_id": "not_exists_session", "feedback_text": ""},
    )
    results.append(
        _line(
            bad.status_code == 422,
            f"未知 session_id 返 422：实际 {bad.status_code}",
        )
    )

    # ---- 7. /health 暴露 planner_mode ----
    h = client.get("/health").json()
    results.append(
        _line(
            "planner_mode" in h and h["planner_mode"] in ("rule", "llm"),
            f"/health.planner_mode={h.get('planner_mode')!r}",
        )
    )

    # ---- 8. X-Planner-Mode header 透传到响应 ----
    with client.stream(
        "POST",
        "/chat/stream",
        json={"message": S1_MESSAGE, "session_id": "header_test"},
        headers={"X-Planner-Mode": "llm"},
    ) as resp:
        echoed = resp.headers.get("X-Planner-Mode")
        # 消费 body 防 client warn
        for _ in resp.iter_text():
            pass
    results.append(
        _line(
            echoed == "llm",
            f"X-Planner-Mode header 透传：{echoed}",
        )
    )

    print("\n".join(line for _, line in results))
    print()
    failed = [line for ok, line in results if not ok]
    if failed:
        print(f"→ 失败 {len(failed)} 项")
        return 1
    print(f"✓ 全部 {len(results)} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
