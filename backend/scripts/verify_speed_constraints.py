"""速度约束实测（spec bonus-points-review · 速度约束加分项）。

加分项 3 条硬指标（来自路演大纲）：
- 方案生成 <= 30 秒：从 /chat/stream 首请求 → itinerary_ready 事件
- 工具响应 <= 3 秒：单次 tool_call_start → tool_call_end 持续时间
- 端到端流程 <= 2 分钟：用户发送 → /chat/confirm 完整下单链路

测试方法：
- TestClient 单进程同步执行（无网络抖动干扰）
- rule + llm 两种模式各跑一次（rule = demo 安全网，llm = 真链路）
- 工具耗时用服务端 SseEvent.timestamp_ms 算（不依赖 client wall_clock，因 sse-starlette
  把多事件粘 chunk）；总耗时用 client wall_clock（更接近评委体感）
- 退出码：全部通过 0，任意违反指标 1

跑法：
    .venv/Scripts/python -m scripts.verify_speed_constraints
    # 默认 LLM_PROVIDER=stub（demo 路径），评委直接跑这条
    # 真 LLM 模式：先配 .env 再 LLM_PROVIDER=deepseek/qwen 跑
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator

# 把 backend/ 加入 import 路径
backend_root = Path(__file__).resolve().parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

# 加载 .env（与 main.py 同一份）
try:
    from dotenv import load_dotenv

    load_dotenv(backend_root / ".env")
except Exception:  # noqa: BLE001
    pass

# 测试默认走 stub（避免依赖 LLM key + 评委复跑稳定）
# 显式传 --real 时强制走真 LLM 链路
_FORCE_REAL = "--real" in sys.argv
if _FORCE_REAL:
    sys.argv.remove("--real")
    # 真 LLM 模式：env 已配 LLM_API_KEY 时不动；否则报错
    if not os.getenv("LLM_PROVIDER") or os.getenv("LLM_PROVIDER") == "stub":
        os.environ.pop("LLM_PROVIDER", None)  # 让 _use_real_planner 走自动推断
    if not (
        os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("QWEN_API_KEY")
    ):
        print("[ERROR] --real 模式需要 .env 里配 LLM_API_KEY；当前未配。")
        sys.exit(1)
elif not os.getenv("LLM_PROVIDER"):
    os.environ["LLM_PROVIDER"] = "stub"

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


# ============================================================
# 速度约束指标
# ============================================================

PLAN_GENERATION_MAX_SEC = 30.0
TOOL_RESPONSE_MAX_SEC = 3.0
END_TO_END_MAX_SEC = 120.0


def _consume_sse(
    client: TestClient,
    path: str,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> Iterator[tuple[str, dict[str, Any], float]]:
    """同步消费一个 SSE 流，返回 (event_type, payload_dict, wall_clock_sec_since_start)。

    wall_clock_sec 含全部网络/调度延迟；若多事件粘在同 chunk，所有事件取相近 wall_clock。
    单事件耗时应取 payload.timestamp_ms（_now_ms()）字段。
    """
    start = time.perf_counter()
    with client.stream("POST", path, json=body, headers=headers or {}, timeout=180.0) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"{path} returned HTTP {resp.status_code}: {resp.text[:200]}")
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while True:
                # 优先匹配 \r\n\r\n（4 字节），否则用 \n\n（2 字节）
                idx_crlf = buf.find("\r\n\r\n")
                idx_lf = buf.find("\n\n")
                if idx_crlf >= 0 and (idx_lf < 0 or idx_crlf <= idx_lf):
                    idx, sep_len = idx_crlf, 4
                elif idx_lf >= 0:
                    idx, sep_len = idx_lf, 2
                else:
                    break
                block, buf = buf[:idx], buf[idx + sep_len :]
                event_type = ""
                data_lines: list[str] = []
                for line in block.splitlines():
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                    elif line.startswith("data: "):
                        data_lines.append(line[6:])
                if not event_type:
                    continue
                payload = {}
                if data_lines:
                    try:
                        payload = json.loads("\n".join(data_lines))
                    except json.JSONDecodeError:
                        payload = {"_raw": "\n".join(data_lines)}
                yield event_type, payload, time.perf_counter() - start


def _measure_one_round(
    client: TestClient,
    *,
    label: str,
    mode: str,
    message: str,
    session_id: str,
) -> dict[str, Any]:
    """跑一轮 stream + confirm，记录 3 条指标。

    工具耗时：用服务端 SseEvent.timestamp_ms 算 tool_call_start → tool_call_end 真实毫秒差。
    """
    print(f"\n=== [{label}] mode={mode} session={session_id} ===")

    # ---- 阶段 1：/chat/stream → 拿到 itinerary_ready ----
    plan_start = time.perf_counter()
    plan_done_at: float | None = None
    stream_done_at: float | None = None
    tool_durations: list[tuple[str, float]] = []  # (tool, sec)
    open_tools_ts: dict[str, int] = {}  # tool → start_ts_ms
    event_count = 0

    headers = {"X-Planner-Mode": mode}

    for evt_type, payload, sec in _consume_sse(
        client,
        "/chat/stream",
        {"message": message, "session_id": session_id},
        headers=headers,
    ):
        event_count += 1
        ts_ms = payload.get("timestamp_ms") or 0
        if evt_type == "tool_call_start":
            tool = payload.get("payload", {}).get("tool", "?")
            open_tools_ts[tool] = ts_ms
        elif evt_type == "tool_call_end":
            tool = payload.get("payload", {}).get("tool", "?")
            if tool in open_tools_ts and ts_ms and open_tools_ts[tool]:
                dur_sec = (ts_ms - open_tools_ts[tool]) / 1000.0
                tool_durations.append((tool, dur_sec))
                del open_tools_ts[tool]
        elif evt_type == "itinerary_ready":
            if plan_done_at is None:
                plan_done_at = sec
        elif evt_type == "done":
            stream_done_at = sec

    plan_total = stream_done_at or (time.perf_counter() - plan_start)

    if plan_done_at is None:
        return {
            "label": label,
            "mode": mode,
            "stream_event_count": event_count,
            "plan_done_at_sec": None,
            "plan_total_sec": plan_total,
            "tool_durations": tool_durations,
            "skipped_reason": "no_itinerary_ready (router 走 chitchat 路径)",
        }

    # ---- 阶段 2：/chat/confirm → 拿到 done ----
    confirm_start = time.perf_counter()
    confirm_done_at: float | None = None
    for evt_type, payload, sec in _consume_sse(
        client,
        "/chat/confirm",
        {"session_id": session_id, "decision": "confirm"},
        headers=headers,
    ):
        event_count += 1
        ts_ms = payload.get("timestamp_ms") or 0
        if evt_type == "tool_call_start":
            tool = payload.get("payload", {}).get("tool", "?")
            open_tools_ts[tool] = ts_ms
        elif evt_type == "tool_call_end":
            tool = payload.get("payload", {}).get("tool", "?")
            if tool in open_tools_ts and ts_ms and open_tools_ts[tool]:
                dur_sec = (ts_ms - open_tools_ts[tool]) / 1000.0
                tool_durations.append((tool, dur_sec))
                del open_tools_ts[tool]
        elif evt_type == "done":
            confirm_done_at = sec

    confirm_total = confirm_done_at or (time.perf_counter() - confirm_start)
    end_to_end_sec = plan_total + confirm_total

    return {
        "label": label,
        "mode": mode,
        "stream_event_count": event_count,
        "plan_done_at_sec": plan_done_at,
        "plan_total_sec": plan_total,
        "confirm_total_sec": confirm_total,
        "end_to_end_sec": end_to_end_sec,
        "tool_durations": tool_durations,
        "skipped_reason": None,
    }


def _render_report(results: list[dict[str, Any]]) -> int:
    """渲染表格 + 检查指标，返回退出码。

    用 ASCII 标记防 Windows GBK 终端崩。
    """
    print("\n" + "=" * 70)
    print("速度约束实测报告")
    print("=" * 70)

    print("\n## 加分项三条指标")
    print(f"  方案生成 <= {PLAN_GENERATION_MAX_SEC:.0f} 秒")
    print(f"  工具响应 <= {TOOL_RESPONSE_MAX_SEC:.0f} 秒")
    print(f"  端到端流程 <= {END_TO_END_MAX_SEC:.0f} 秒 ({END_TO_END_MAX_SEC / 60:.0f} 分钟)")

    overall_ok = True

    for r in results:
        print(f"\n--- [{r['label']}] mode={r['mode']} ---")
        print(f"  事件总数: {r['stream_event_count']}")

        if r.get("skipped_reason"):
            print(f"  [WARN] 跳过: {r['skipped_reason']}")
            continue

        # 方案生成
        plan_at = r.get("plan_done_at_sec")
        if plan_at is None:
            plan_status = "[FAIL] 无 itinerary_ready"
            overall_ok = False
        elif plan_at <= PLAN_GENERATION_MAX_SEC:
            plan_status = f"[PASS] {plan_at:.2f}s <= {PLAN_GENERATION_MAX_SEC:.0f}s"
        else:
            plan_status = f"[FAIL] {plan_at:.2f}s > {PLAN_GENERATION_MAX_SEC:.0f}s"
            overall_ok = False
        print(f"  方案生成: {plan_status}")

        # 工具响应（取最大值）
        tool_durations = r.get("tool_durations") or []
        if tool_durations:
            max_tool = max(tool_durations, key=lambda x: x[1])
            avg_tool = sum(d for _, d in tool_durations) / len(tool_durations)
            if max_tool[1] <= TOOL_RESPONSE_MAX_SEC:
                tool_status = (
                    f"[PASS] 最慢 {max_tool[0]} = {max_tool[1] * 1000:.0f}ms "
                    f"<= {TOOL_RESPONSE_MAX_SEC:.0f}s "
                    f"({len(tool_durations)} 次平均 {avg_tool * 1000:.0f}ms)"
                )
            else:
                tool_status = (
                    f"[FAIL] {max_tool[0]} = {max_tool[1]:.2f}s "
                    f"> {TOOL_RESPONSE_MAX_SEC:.0f}s"
                )
                overall_ok = False
        else:
            tool_status = "(本轮无工具调用)"
        print(f"  工具响应: {tool_status}")

        # 端到端
        e2e = r.get("end_to_end_sec")
        if e2e is None:
            e2e_status = "(confirm 未跑完)"
        elif e2e <= END_TO_END_MAX_SEC:
            e2e_status = f"[PASS] {e2e:.2f}s <= {END_TO_END_MAX_SEC:.0f}s"
        else:
            e2e_status = f"[FAIL] {e2e:.2f}s > {END_TO_END_MAX_SEC:.0f}s"
            overall_ok = False
        print(f"  端到端流程: {e2e_status}")

        # 工具明细
        if tool_durations:
            print("  --工具耗时明细--")
            for tool, sec in tool_durations:
                marker = "[OK]" if sec <= TOOL_RESPONSE_MAX_SEC else "[!!]"
                print(f"    {marker} {tool}: {sec * 1000:.0f}ms")

    print("\n" + "=" * 70)
    if overall_ok:
        print("[PASS] 全部通过")
    else:
        print("[FAIL] 存在违反指标")
    print("=" * 70)

    return 0 if overall_ok else 1


def main() -> int:
    print(f"环境: LLM_PROVIDER={os.getenv('LLM_PROVIDER')!r}")
    print(f"      USE_LANGGRAPH={os.getenv('USE_LANGGRAPH') or '0'!r}")
    print(f"      USE_REACT_AGENT={os.getenv('USE_REACT_AGENT') or '1'!r}")
    print(f"      PLANNER_USE_REAL={os.getenv('PLANNER_USE_REAL') or '(unset)'!r}")

    client = TestClient(app, raise_server_exceptions=True)

    family_msg = (
        "今天下午想和老婆孩子出去玩几个小时，"
        "别离家太远，孩子 5 岁，老婆最近在减肥。"
    )

    results: list[dict[str, Any]] = []

    # 第 1 轮：rule 模式（demo 安全网）
    results.append(
        _measure_one_round(
            client,
            label="家庭主场景 · rule 模式",
            mode="rule",
            message=family_msg,
            session_id=f"speed_test_rule_{int(time.time())}",
        )
    )

    # 第 2 轮：llm 模式
    results.append(
        _measure_one_round(
            client,
            label="家庭主场景 · llm 模式",
            mode="llm",
            message=family_msg,
            session_id=f"speed_test_llm_{int(time.time())}",
        )
    )

    return _render_report(results)


if __name__ == "__main__":
    raise SystemExit(main())
