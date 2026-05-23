"""scripts.experiment_critic_value —— 实证 LLM + 算法 (critic + ILS) 的真实收益。

核心问题：critic backprompt 和 ILS 兜底，到底有没有真正解决问题？还是为了创新而创新？

实验方法：
1. 跑 6 个不同特征的 intent（覆盖 7 类 ViolationCode 触发条件）
2. 每个 intent 跑一次完整 graph（带 critic + backprompt + ILS 兜底）
3. 抓 graph.astream 流，统计：
   - LLM 第一次出 plan 的违规数（关键：第一次有几条 critical）
   - 经过 backprompt 后违规变成几条
   - ILS 是否被触发
   - 最终方案是否合规
4. 输出表格让数据自己说话

判断标准（必须客观）：
- 如果 LLM 第一次几乎都过 → critic 没用，是 over-engineering
- 如果 critic 多次触发 + backprompt 真修对了 → critic 有真实价值
- 如果 LLM 反复修不好 + ILS 兜底起作用 → ILS 有真实价值

LLM_PROVIDER=stub 时跳过（实验必须真 LLM 跑）。
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


# ============================================================
# 6 个测试 intent
# ============================================================
# 设计：覆盖不同潜在违规类型，让 critic 有发挥空间

CASES = [
    {
        "id": "S1-family",
        "input": (
            "今天下午想和老婆孩子出去玩几个小时，"
            "别离家太远，孩子 5 岁，老婆最近在减肥。"
        ),
        "expected_challenges": "标准家庭场景；约束密度高（亲子+低脂+距离）",
    },
    {
        "id": "S2-elderly",
        "input": (
            "周日下午想带外公外婆出去走走，"
            "别走太远他们腿不好，找个能坐能歇的地方。"
        ),
        "expected_challenges": "DISTANCE_EXCEEDED 容易触发；适老低强度需求",
    },
    {
        "id": "S3-solo",
        "input": (
            "这周加班加得想吐，下午想一个人安安静静"
            "待几个小时再回家，最好室内不晒。"
        ),
        "expected_challenges": "SOCIAL_CONTEXT 独处；可能 NODES_INCOMPLETE（只想 POI 不想用餐）",
    },
    {
        "id": "S4-business",
        "input": (
            "下午临时被叫去接个外地客户，对方是商务人士，"
            "帮我安排两小时左右的活动，体面些。"
        ),
        "expected_challenges": "短时长（2h 容易 DURATION 边界）+ 商务调性（容易 SOCIAL_CONTEXT_MISMATCH）",
    },
    {
        "id": "S5-tea",
        "input": (
            "周末下午约了闺蜜想找个网红的地方拍拍照吃个下午茶，"
            "辣的不要太重。"
        ),
        "expected_challenges": "DIETARY_VIOLATION 风险（辣）+ 用餐 17:00 RESTAURANT_FULL 埋点",
    },
    {
        "id": "S6-short",
        "input": "今天下午想出去走走，1 个小时就回来。",
        "expected_challenges": "极短 1h DURATION_OUT_OF_RANGE 重灾区（5 段塞不下）",
    },
]


# ============================================================
# 实证测量
# ============================================================

async def measure_one(graph, case_input: str, session_id: str) -> dict:
    """跑一次 graph，统计真实关键指标。"""
    from agent.graph.state import make_initial_state

    initial = make_initial_state(
        user_input=case_input,
        user_id="demo_user",
        session_id=session_id,
    )
    config = {"configurable": {"thread_id": session_id}}

    plan_attempts = 0
    critic_runs: list[dict] = []
    ils_triggered = False
    backprompt_count = 0
    final_itinerary = None
    final_violations = None

    t0 = time.time()
    try:
        async for chunk in graph.astream(
            initial, config=config, stream_mode="updates"
        ):
            for node_name, diff in chunk.items():
                if diff is None:
                    continue
                if node_name == "planner":
                    new_attempt = diff.get("plan_attempt") or (plan_attempts + 1)
                    if new_attempt > plan_attempts:
                        if plan_attempts > 0:
                            backprompt_count += 1
                        plan_attempts = new_attempt
                elif node_name == "critic":
                    vios = diff.get("violations") or []
                    critic_runs.append(
                        {
                            "attempt": plan_attempts,
                            "has_critical": bool(diff.get("has_critical")),
                            "codes": [
                                getattr(v.code, "value", str(v))
                                if hasattr(v, "code")
                                else str(v)
                                for v in vios
                            ],
                        }
                    )
                elif node_name == "ils_replan":
                    ils_triggered = True
                elif node_name == "assemble":
                    if diff.get("itinerary") is not None:
                        final_itinerary = diff["itinerary"]
    except Exception as e:  # noqa: BLE001
        return {
            "error": f"{type(e).__name__}: {str(e)[:120]}",
            "elapsed": time.time() - t0,
        }

    elapsed = time.time() - t0

    # 统计违规清单
    first_run = critic_runs[0] if critic_runs else None
    last_run = critic_runs[-1] if critic_runs else None

    return {
        "elapsed": elapsed,
        "plan_attempts": plan_attempts,
        "backprompt_count": backprompt_count,
        "ils_triggered": ils_triggered,
        "critic_runs_count": len(critic_runs),
        "first_critic_codes": first_run["codes"] if first_run else [],
        "first_has_critical": first_run["has_critical"] if first_run else None,
        "final_critic_codes": last_run["codes"] if last_run else [],
        "final_has_critical": last_run["has_critical"] if last_run else None,
        "final_mid_nodes": (
            len([n for n in final_itinerary.nodes if n.target_kind != "home"])
            if final_itinerary
            else 0
        ),
        "final_total_minutes": (
            final_itinerary.total_minutes if final_itinerary else None
        ),
    }


# ============================================================
# 主流程
# ============================================================

def _format_codes(codes: list) -> str:
    if not codes:
        return "无违规 ✓"
    return ", ".join(codes[:3]) + ("..." if len(codes) > 3 else "")


async def main() -> int:
    if _is_stub():
        print("[SKIPPED] LLM_PROVIDER=stub；切换到真 LLM 后再跑")
        return 0

    # 强制 unbuffered，日志实时落盘
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    print("=" * 78, flush=True)
    print("LLM + Critic + ILS 真实收益实证（6 个真 LLM case）", flush=True)
    print("=" * 78, flush=True)

    from agent.graph.build import build_graph

    print("\n[setup] 编译 graph...", flush=True)
    graph = build_graph(with_checkpointer=True)
    print("        OK", flush=True)

    results = []
    for i, case in enumerate(CASES, 1):
        print(f"\n[{i}/{len(CASES)}] {case['id']}", flush=True)
        print(f"  input: {case['input'][:60]}...", flush=True)
        print(f"  challenges: {case['expected_challenges']}", flush=True)
        result = await measure_one(graph, case["input"], f"exp_{case['id']}_{i}")
        result["case_id"] = case["id"]
        results.append(result)
        if "error" in result:
            print(f"  ✗ FAIL: {result['error']}", flush=True)
        else:
            print(
                f"  ✓ done ({result['elapsed']:.1f}s) "
                f"plan_attempts={result['plan_attempts']} "
                f"backprompt={result['backprompt_count']} "
                f"ils={result['ils_triggered']} "
                f"final_pass={not result['final_has_critical']}",
                flush=True,
            )

    # ============================================================
    # 数据表
    # ============================================================
    print("\n" + "=" * 78)
    print("【实证数据表】")
    print("=" * 78)
    print()
    print(
        f"{'Case':<14} | {'plan':<5} | {'bp':<3} | {'ILS':<5} | "
        f"{'1st 违规':<22} | {'final':<7} | {'mid节点':<7} | 总分钟"
    )
    print("-" * 100)
    for r in results:
        if "error" in r:
            print(f"{r['case_id']:<14} | ERROR: {r['error']}")
            continue
        first_codes_str = _format_codes(r["first_critic_codes"])
        final_pass = "✓ pass" if not r["final_has_critical"] else "✗ FAIL"
        print(
            f"{r['case_id']:<14} | "
            f"{r['plan_attempts']:<5} | "
            f"{r['backprompt_count']:<3} | "
            f"{'YES' if r['ils_triggered'] else 'no':<5} | "
            f"{first_codes_str:<22} | "
            f"{final_pass:<7} | "
            f"{r['final_mid_nodes']:<7} | "
            f"{r['final_total_minutes']}"
        )

    # ============================================================
    # 关键统计
    # ============================================================
    successful = [r for r in results if "error" not in r]
    if not successful:
        print("\n所有 case 都失败，无法分析")
        return 1

    print("\n" + "=" * 78)
    print("【关键发现】")
    print("=" * 78)

    one_shot = sum(1 for r in successful if r["plan_attempts"] == 1)
    needed_backprompt = sum(1 for r in successful if r["backprompt_count"] > 0)
    needed_ils = sum(1 for r in successful if r["ils_triggered"])
    final_passes = sum(1 for r in successful if not r["final_has_critical"])
    first_failures = sum(1 for r in successful if r["first_has_critical"])

    n = len(successful)
    print(f"\n样本量: {n} 个真 LLM 跑")
    print(f"  - LLM 一次出对（无 backprompt）: {one_shot}/{n}")
    print(f"  - LLM 第一次违规（critic 拦下）: {first_failures}/{n}")
    print(f"  - 需要 backprompt 修复:        {needed_backprompt}/{n}")
    print(f"  - 需要 ILS 兜底:              {needed_ils}/{n}")
    print(f"  - 最终通过 critic 验证:       {final_passes}/{n}")

    print("\n【critic 修复了什么】")
    fixed_codes = []
    for r in successful:
        if r["first_has_critical"] and not r["final_has_critical"]:
            fixed_codes.append(
                f"  - {r['case_id']}: 第一次 {r['first_critic_codes']} → 最终通过"
            )
    if fixed_codes:
        for line in fixed_codes:
            print(line)
    else:
        print("  （无 case 触发 critic 修复，要么 LLM 一次过，要么修复也没过）")

    # 客观结论
    print("\n【客观结论】")
    if one_shot == n:
        print(
            "  ⚠️  所有 case LLM 一次出对——critic 在本批次没有发挥作用，"
            "可能 over-engineering"
        )
    elif first_failures > 0 and final_passes >= first_failures:
        print(
            f"  ✓ {first_failures} 个 case 第一次 LLM 出违规方案，"
            f"经 backprompt 后 {final_passes - one_shot} 个被修对——"
            "critic 有真实价值"
        )
        if needed_ils:
            print(f"  ✓ {needed_ils} 个 case 触发了 ILS 兜底——双层防御真的兜住了")
    else:
        print(
            f"  ⚠️  critic 拦下 {first_failures} 个违规但 backprompt 修复率不足，"
            "需要审视 prompt / ILS 路径"
        )

    return 0 if final_passes == n else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
