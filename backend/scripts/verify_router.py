# -*- coding: utf-8 -*-
"""verify_router —— Phase 0.8 输入域路由端到端验证。

跑两组用例：
- stub 模式：5 类非规划输入 + 主路径透传
- 真链路兜底验证：真 LLM 失败时仍能落到保守地板

不依赖真 LLM API key（默认 stub 模式）。

ADR-0011 决策 2（E-1）行为更新：
- StubLLMClient 对任何 prompt 都固定返回"意图抽取"形状的 JSON（不含
  input_kind），classify_input 在 stub 模式下**恒抛异常**——这在 E-1 之前
  就已是事实，不是本次改动引入的。旧地板"LLM 不可用→PLANNING"因此让 stub
  模式下的 meta/chitchat/emotional/off_topic/ambiguous 5 类全部被误判成
  planning（ADR-0011 背景 2 实测钉死的病灶）。新地板保守退让：无方案 →
  一律 chitchat 陪聊引导（不再冒充精确的 6 类分类结果）。这 5 个 case 的
  expected_kind 因此统一改为 "chitchat"（不再是各自的类别名）——是"落到
  保守地板"这件事本身的验证，不是"LLM 精确分类出 5 个不同类别"的验证
  （那需要真 LLM 或更细的 mock，不在本脚本 stub-only 范围内）。

已知无关缺陷（未修复，超出 ADR-0011 范围）：
    /chat/stream 端点已在更早的重构中随 V1 legacy 一并退役（现为 /chat/turn，
    见 api/chat.py），本脚本调用它会得到 HTTP 404——这是本脚本自身的 bit-rot，
    与路由行为无关，不在本次 E-1 修复范围内（需要单独校对 /chat/turn 的
    request/response 契约才能安全重写，留给触碰该端点的改动去修）。

运行：
    cd backend
    python -m scripts.verify_router
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

# 强制 stub 避免误打外部 LLM
os.environ["LLM_PROVIDER"] = "stub"
os.environ.pop("PLANNER_USE_REAL", None)

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


def _parse_sse(content: str) -> list[dict[str, Any]]:
    """简易 SSE 块解析：支持 \\r\\n\\r\\n 与 \\n\\n。"""
    blocks: list[dict[str, Any]] = []
    raw = content.replace("\r\n", "\n")
    for chunk in raw.split("\n\n"):
        if not chunk.strip():
            continue
        event_type = ""
        data_lines: list[str] = []
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not event_type:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            payload = {"raw": "\n".join(data_lines)}
        blocks.append({"event": event_type, "data": payload})
    return blocks


def _stream_post(client: TestClient, message: str, session_id: str) -> list[dict[str, Any]]:
    resp = client.post(
        "/chat/stream",
        json={"message": message, "session_id": session_id, "user_id": "demo_user"},
        headers={"X-Planner-Mode": "rule"},
    )
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:200]}"
    return _parse_sse(resp.text)


def _chitchat_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    for ev in events:
        if ev["event"] == "chitchat_reply":
            return ev["data"].get("payload", ev["data"])
    raise AssertionError("no chitchat_reply event")


def main() -> int:
    client = TestClient(app)

    # ADR-0011 决策 2（E-1）：stub 模式下 classify_input 恒抛异常（见模块
    # docstring），5 类都会落到保守地板——无方案时地板恒返 chitchat，不再冒充
    # 精确的 6 类分类结果。
    cases: list[tuple[str, str, str]] = [
        ("S-meta",      "你是谁",        "chitchat"),
        ("S-chitchat",  "你好",          "chitchat"),
        ("S-emotional", "我累死了",       "chitchat"),
        ("S-off_topic", "1+1=?",         "chitchat"),
        ("S-ambiguous", "出去玩",         "chitchat"),
    ]

    failures: list[str] = []
    for case_id, message, expected_kind in cases:
        try:
            events = _stream_post(client, message, f"sess_{case_id}")
            # 验证：序列首条 chitchat_reply，末条 done，没有 itinerary_ready
            event_types = [e["event"] for e in events]
            assert "chitchat_reply" in event_types, f"{case_id}: 缺 chitchat_reply"
            assert "done" in event_types, f"{case_id}: 缺 done"
            assert "itinerary_ready" not in event_types, (
                f"{case_id}: 不该走主规划，但出现 itinerary_ready"
            )
            payload = _chitchat_payload(events)
            assert payload.get("input_kind") == expected_kind, (
                f"{case_id}: input_kind {payload.get('input_kind')} != {expected_kind}"
            )
            assert payload.get("reply_text"), f"{case_id}: 缺 reply_text"
            chips = payload.get("cta_chips") or []
            # 至少 1 个 chip（emotional 也至少 1）
            assert len(chips) >= 1, f"{case_id}: 至少 1 个 chip"
            print(
                f"  ✓ {case_id}: 输入='{message}' → kind={payload['input_kind']} "
                f"chips={len(chips)} tone={payload.get('tone')}"
            )
        except AssertionError as e:
            failures.append(f"  ✗ {case_id}: {e}")

    # 主路径透传：含明确出行意图 → 不走 chitchat_reply
    main_msg = "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。"
    try:
        events = _stream_post(client, main_msg, "sess_main")
        event_types = [e["event"] for e in events]
        assert "chitchat_reply" not in event_types, (
            "主路径不应推 chitchat_reply（关键词 fast path 应不命中）"
        )
        assert "itinerary_ready" in event_types, "主路径应产出 itinerary_ready"
        assert "intent_parsed" in event_types, "主路径应产出 intent_parsed"
        print(f"  ✓ S-planning: 输入主场景 → 主路径透传，含 itinerary_ready")
    except AssertionError as e:
        failures.append(f"  ✗ S-planning: {e}")

    # 422 校验：fake input
    try:
        resp = client.post("/chat/stream", json={"session_id": "x"})
        assert resp.status_code in (422, 400), f"HTTP {resp.status_code}"
        print(f"  ✓ S-422: 缺 message 字段返 {resp.status_code}")
    except AssertionError as e:
        failures.append(f"  ✗ S-422: {e}")

    print()
    if failures:
        print(f"❌ {len(failures)} 项失败：")
        for f in failures:
            print(f)
        return 1
    print(f"✅ 全部 {len(cases) + 2} 项通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
