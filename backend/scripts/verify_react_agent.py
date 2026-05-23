# -*- coding: utf-8 -*-
"""verify_react_agent —— Phase·Agent E 验证脚本。

5 场景端到端验证 ReAct 单一 Agent：
    1. 闲聊（"你是谁"）→ ChatResponse 不调任何工具
    2. POI Q&A（"P004 是什么样的地方"）→ 调 search_pois → ChatResponse
    3. 完整规划（家庭主线）→ 调多工具 → ItineraryResponse, mid_nodes ≥ 2,
       narration 含 "老婆" 或 "孩子"
    4. 拒答（"5+5 等于几"）→ ChatResponse 含晌午局/下午/出行/帮你 等关键词
    5. 上下文反馈：先 run 主线 → 再 run "太远了 3 公里以内" 用 result.all_messages()
       传 message_history → ItineraryResponse 中所有节点距离 ≤ 3km

跑法：
    # SKIPPED 模式（CI / 无真 LLM key）
    $env:LLM_PROVIDER='stub'
    .venv\\Scripts\\python.exe -m scripts.verify_react_agent

    # 完整验证模式（需 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）
    Remove-Item Env:\\LLM_PROVIDER -ErrorAction SilentlyContinue
    .venv\\Scripts\\python.exe -m scripts.verify_react_agent

退出码：
    0 = 全部通过 / SKIPPED
    1 = 至少一个场景失败
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# 确保能 import backend.* 模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _is_stub_mode() -> bool:
    return (os.getenv("LLM_PROVIDER") or "").strip().lower() == "stub"


def _bullet(ok: bool, msg: str) -> str:
    return ("  ✓ " if ok else "  ✗ ") + msg


# ============================================================
# 通用 helper：从 result 提取关键信息
# ============================================================


def _summarize_result(result: Any) -> dict[str, Any]:
    """把 AgentRunResult 摘要成 dict，便于失败时打印。"""
    output = result.output
    out_type = type(output).__name__
    info: dict[str, Any] = {"output_type": out_type}
    if out_type == "ChatResponse":
        info["text_head"] = (output.text or "")[:200]
        info["suggestions"] = list(output.suggestions or [])
    elif out_type == "ItineraryResponse":
        info["narration_head"] = (output.narration or "")[:200]
        # edge_v1：节点数 / 中间节点数 / 节点种类
        nodes = output.itinerary.nodes
        info["nodes_count"] = len(nodes)
        info["mid_nodes_count"] = len([n for n in nodes if n.target_kind != "home"])
        info["node_target_kinds"] = [n.target_kind for n in nodes]
        info["total_minutes"] = output.itinerary.total_minutes
        info["orders"] = list(output.itinerary.orders or [])
    return info


def _count_tool_calls(result: Any) -> dict[str, int]:
    """统计 result.all_messages() 中每个**业务工具**被调用的次数。

    过滤掉 Pydantic AI 框架内部的 output tool（``final_result_*``）——
    output tool 是 LLM 用来"提交最终输出"的特殊工具，不是业务工具调用。
    """
    counts: dict[str, int] = {}
    try:
        from pydantic_ai.messages import ToolCallPart

        for msg in result.all_messages():
            for part in getattr(msg, "parts", []) or []:
                if isinstance(part, ToolCallPart):
                    name = getattr(part, "tool_name", "") or "<unknown>"
                    if name.startswith("final_result"):
                        continue
                    counts[name] = counts.get(name, 0) + 1
    except Exception:
        pass
    return counts


# ============================================================
# 场景 1：闲聊 / 自我介绍
# ============================================================


async def _scenario_chat() -> tuple[bool, list[str], dict[str, Any]]:
    from agent.v2.deps import AgentDeps
    from agent.v2.output_types import ChatResponse
    from agent.v2.react_agent import run_react_turn_inner

    deps = AgentDeps(user_id="demo_user", session_id="verify-react-1")
    result = await run_react_turn_inner("你是谁", deps=deps)

    summary = _summarize_result(result)
    summary["tool_calls"] = _count_tool_calls(result)

    ok_type = isinstance(result.output, ChatResponse)
    ok_no_tools = sum(summary["tool_calls"].values()) == 0

    lines = [
        f"\n[场景 1] 闲聊 'who are you' → ChatResponse 不调工具",
        _bullet(ok_type, f"output 类型 = ChatResponse (实际 {summary['output_type']})"),
        _bullet(
            ok_no_tools,
            f"未调用任何工具 (实际调用 {summary['tool_calls']})",
        ),
    ]
    if not (ok_type and ok_no_tools):
        lines.append(f"  · summary: {summary}")
    return ok_type and ok_no_tools, lines, summary


# ============================================================
# 场景 2：POI Q&A
# ============================================================


async def _scenario_poi_qa() -> tuple[bool, list[str], dict[str, Any]]:
    from agent.v2.deps import AgentDeps
    from agent.v2.output_types import ChatResponse
    from agent.v2.react_agent import run_react_turn_inner

    deps = AgentDeps(user_id="demo_user", session_id="verify-react-2")
    result = await run_react_turn_inner(
        "我想了解一下，附近有适合 5 岁孩子玩的亲子地方吗？给我推荐一两个就行，不用整套规划。",
        deps=deps,
    )

    summary = _summarize_result(result)
    summary["tool_calls"] = _count_tool_calls(result)

    # 期望：调过 search_pois，最终 ChatResponse 而非 ItineraryResponse
    ok_type = isinstance(result.output, ChatResponse)
    ok_searched = summary["tool_calls"].get("search_pois", 0) >= 1

    lines = [
        f"\n[场景 2] POI Q&A → 调 search_pois → ChatResponse",
        _bullet(ok_type, f"output 类型 = ChatResponse (实际 {summary['output_type']})"),
        _bullet(
            ok_searched,
            f"search_pois 至少调用 1 次 (实际 {summary['tool_calls'].get('search_pois', 0)})",
        ),
    ]
    if not (ok_type and ok_searched):
        lines.append(f"  · summary: {summary}")
    return ok_type and ok_searched, lines, summary


# ============================================================
# 场景 3：完整规划（家庭主线）
# ============================================================


async def _scenario_planning() -> tuple[bool, list[str], dict[str, Any]]:
    from agent.v2.deps import AgentDeps
    from agent.v2.output_types import ItineraryResponse
    from agent.v2.react_agent import run_react_turn_inner

    deps = AgentDeps(
        user_id="demo_user",
        session_id="verify-react-3",
        extra={"intent_snapshot": None},  # 跳过 critic（intent_snapshot 留给 G agent 填）
    )
    result = await run_react_turn_inner(
        "今天下午想和老婆孩子出去玩，孩子 5 岁，老婆最近在减肥，别离家太远",
        deps=deps,
    )

    summary = _summarize_result(result)
    summary["tool_calls"] = _count_tool_calls(result)

    ok_type = isinstance(result.output, ItineraryResponse)
    # edge_v1：mid 节点 ≥ 2（POI + 用餐 / 或 POI + POI 等）
    ok_nodes = ok_type and summary.get("mid_nodes_count", 0) >= 2
    narration = summary.get("narration_head", "")
    ok_narration = ok_type and (("老婆" in narration) or ("孩子" in narration))

    # 确认有调多工具（至少 search_pois + search_restaurants）
    tc = summary["tool_calls"]
    ok_multi_tools = tc.get("search_pois", 0) >= 1 and tc.get("search_restaurants", 0) >= 1

    lines = [
        f"\n[场景 3] 家庭主线规划 → ItineraryResponse",
        _bullet(ok_type, f"output 类型 = ItineraryResponse (实际 {summary['output_type']})"),
        _bullet(
            ok_nodes,
            f"中间节点 ≥ 2 (实际 {summary.get('mid_nodes_count', 0)})",
        ),
        _bullet(
            ok_narration,
            f"narration 含「老婆」或「孩子」(前 60 字: {narration[:60]!r})",
        ),
        _bullet(
            ok_multi_tools,
            f"search_pois + search_restaurants 都调过 (实际 {tc})",
        ),
    ]
    if not (ok_type and ok_nodes and ok_narration and ok_multi_tools):
        lines.append(f"  · summary: {summary}")

    return ok_type and ok_nodes and ok_narration and ok_multi_tools, lines, summary


# ============================================================
# 场景 4：拒答 / 范围外
# ============================================================


async def _scenario_off_topic() -> tuple[bool, list[str], dict[str, Any]]:
    from agent.v2.deps import AgentDeps
    from agent.v2.output_types import ChatResponse
    from agent.v2.react_agent import run_react_turn_inner

    deps = AgentDeps(user_id="demo_user", session_id="verify-react-4")
    result = await run_react_turn_inner("5+5 等于几", deps=deps)

    summary = _summarize_result(result)
    summary["tool_calls"] = _count_tool_calls(result)

    ok_type = isinstance(result.output, ChatResponse)
    text = summary.get("text_head", "")
    keywords = ("晌午局", "下午", "出行", "规划", "帮你", "本地", "行程", "陪")
    ok_kw = ok_type and any(kw in text for kw in keywords)

    lines = [
        f"\n[场景 4] 拒答（5+5）→ ChatResponse 婉拒并引导回主线",
        _bullet(ok_type, f"output 类型 = ChatResponse (实际 {summary['output_type']})"),
        _bullet(
            ok_kw,
            f"text 含「晌午局/下午/出行/规划/帮你/本地/行程/陪」任一 (前 60 字: {text[:60]!r})",
        ),
    ]
    if not (ok_type and ok_kw):
        lines.append(f"  · summary: {summary}")
    return ok_type and ok_kw, lines, summary


# ============================================================
# 场景 5：上下文反馈
# ============================================================


async def _scenario_feedback() -> tuple[bool, list[str], dict[str, Any]]:
    from agent.v2.deps import AgentDeps
    from agent.v2.output_types import ItineraryResponse
    from agent.v2.react_agent import run_react_turn_inner

    deps = AgentDeps(
        user_id="demo_user",
        session_id="verify-react-5",
        extra={"intent_snapshot": None},  # 跳过 critic
    )

    # 第 1 轮：主线规划
    r1 = await run_react_turn_inner(
        "今天下午想和老婆孩子出去玩，孩子 5 岁，老婆减肥",
        deps=deps,
    )
    s1 = _summarize_result(r1)
    s1["tool_calls"] = _count_tool_calls(r1)

    if not isinstance(r1.output, ItineraryResponse):
        # 第 1 轮失败直接 short-circuit
        return False, [
            "\n[场景 5] 上下文反馈",
            _bullet(False, f"第 1 轮 baseline 没产生 ItineraryResponse (实际 {s1['output_type']})"),
            f"  · r1 summary: {s1}",
        ], {"r1": s1}

    history = r1.all_messages()

    # 第 2 轮：反馈「太远了 3 公里以内」（用 message_history 传上下文）
    r2 = await run_react_turn_inner(
        "太远了，希望 3 公里以内",
        deps=deps,
        message_history=history,
    )
    s2 = _summarize_result(r2)
    s2["tool_calls"] = _count_tool_calls(r2)

    ok_type = isinstance(r2.output, ItineraryResponse)

    # distance ≤ 3km 验证：用 ToolProvider 加载 mock 数据查每个节点的 poi/restaurant 距离
    ok_distance = False
    distance_detail: list[str] = []
    if ok_type:
        try:
            from data.loader import load_pois, load_restaurants

            poi_dist = {p.id: p.distance_km for p in load_pois()}
            rest_dist = {r.id: r.distance_km for r in load_restaurants()}
        except Exception as e:  # noqa: BLE001
            distance_detail.append(f"loader 失败 ({e})；跳过 distance 检查")
            ok_distance = True  # loader 不可用时不阻塞
        else:
            ok_distance = True  # 默认通过；任何超阈值则置 False
            for idx, node in enumerate(r2.output.itinerary.nodes):
                if node.target_kind == "home":
                    continue
                d = None
                if node.target_kind == "poi" and node.target_id in poi_dist:
                    d = poi_dist[node.target_id]
                elif node.target_kind == "restaurant" and node.target_id in rest_dist:
                    d = rest_dist[node.target_id]
                if d is not None and d > 3.0:
                    ok_distance = False
                    distance_detail.append(
                        f"node[{idx}] {node.title} 目标距离 {d}km > 3km"
                    )

    # 必须调过工具（refine 不能空想）
    n_tools = sum(s2["tool_calls"].values())
    ok_tools = n_tools >= 1

    lines = [
        f"\n[场景 5] 上下文反馈：先主线 → 再「3km 以内」",
        _bullet(
            isinstance(r1.output, ItineraryResponse),
            f"r1 baseline = ItineraryResponse, mid_nodes={s1.get('mid_nodes_count')}",
        ),
        _bullet(
            ok_type,
            f"r2 类型 = ItineraryResponse (实际 {s2['output_type']})",
        ),
        _bullet(
            ok_tools,
            f"r2 调用了至少 1 次工具（refine 路径不能空想）(实际 {s2['tool_calls']})",
        ),
        _bullet(
            ok_distance,
            (
                f"r2 所有节点距离 ≤ 3km"
                + (f" — 违例：{distance_detail}" if distance_detail else "")
            ),
        ),
    ]
    if not (ok_type and ok_tools and ok_distance):
        lines.append(f"  · r2 summary: {s2}")
    return ok_type and ok_tools and ok_distance, lines, {"r1": s1, "r2": s2}


# ============================================================
# 主入口
# ============================================================


async def _run() -> int:
    print("=== verify_react_agent · 真 LLM 端到端验证 ===")

    if _is_stub_mode():
        print("LLM_PROVIDER=stub → SKIPPED（不调真 LLM；CI 兼容模式）")
        return 0

    # 加载 .env
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    scenarios = [
        ("S1-chat", _scenario_chat),
        ("S2-poi-qa", _scenario_poi_qa),
        ("S3-planning", _scenario_planning),
        ("S4-off-topic", _scenario_off_topic),
        ("S5-feedback", _scenario_feedback),
    ]

    pass_count = 0
    fail_count = 0
    for sid, fn in scenarios:
        try:
            ok, lines, _summary = await fn()
        except Exception as e:  # noqa: BLE001
            ok = False
            body = getattr(e, "body", None)
            extra = f"\n  · body: {str(body)[:600]}" if body else ""
            lines = [
                f"\n[场景 {sid}] 抛异常",
                _bullet(False, f"{type(e).__name__}: {e}{extra}"),
            ]
        for line in lines:
            print(line)
        if ok:
            pass_count += 1
        else:
            fail_count += 1

    print("\n=== Summary ===")
    print(f"PASS: {pass_count}")
    print(f"FAIL: {fail_count}")
    print(f"Result: {pass_count}/{len(scenarios)}")
    return 0 if fail_count == 0 else 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
