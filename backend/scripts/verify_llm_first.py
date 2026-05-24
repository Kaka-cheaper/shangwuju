"""verify_llm_first —— LLM-First Planner 真 LLM 端到端验证（问题 14）。

跑法：cd backend && uv run python -m scripts.verify_llm_first

覆盖场景（每个真 LLM 跑一次，验证 LLM 决策能力）：
1. 1 小时反馈（截图复现）→ 应得 ≤ 1.5h，3 段以内
2. 只想吃顿饭（无主活动）→ 单段用餐
3. 一个人安静待几小时（无 dietary）→ 单段沉浸
4. 24h / 夜宵场景
5. 完整 4-6h 家庭场景（向后兼容）
"""

from __future__ import annotations

import sys
import time

from dotenv import load_dotenv

load_dotenv()

from agent.intent.parser import parse_intent
from agent.core.llm_client import get_llm_client
from agent.planning.planners.rule_planner import plan_itinerary_with_mode
from agent.intent.refiner import refine_intent


SCENES = [
    {
        "id": "S1_one_hour_feedback",
        "input": "周末下午约了闺蜜想找个网红的地方拍拍照吃个下午茶。",
        "feedback": "只有一个小时",
        "expect_max_minutes": 90,
        "expect_max_mid_nodes": 3,
    },
    {
        "id": "S2_dining_only",
        "input": "今晚就想找个地方吃顿饭，别的不需要。",
        "feedback": None,
        "expect_max_minutes": 180,
        "expect_min_mid_nodes": 1,
    },
    {
        "id": "S3_solo_immerse",
        "input": "这周加班加得想吐，下午想一个人安安静静待几个小时再回家。",
        "feedback": None,
        "expect_max_minutes": 300,
    },
    {
        "id": "S4_family_full",
        "input": "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
        "feedback": None,
        "expect_max_minutes": 400,
        "expect_min_mid_nodes": 2,
    },
]


def _line(ok: bool, msg: str) -> tuple[bool, str]:
    return ok, ("  ✓ " if ok else "  ✗ ") + msg


def main() -> int:
    print("=== LLM-First Planner 真 LLM e2e 验证 ===\n")
    client = get_llm_client()
    print(f"LLM provider={client.provider} model={client.model}\n")

    all_results: list[tuple[bool, str]] = []

    for scene in SCENES:
        print(f"--- {scene['id']} ---")
        print(f"  输入：{scene['input'][:50]}...")
        if scene.get("feedback"):
            print(f"  反馈：{scene['feedback']}")

        try:
            intent = parse_intent(scene["input"], client=client)
            if scene.get("feedback"):
                refinement = refine_intent(intent, scene["feedback"], client=client)
                intent = refinement.refined_intent
                print(
                    f"  refined.duration_hours = {intent.duration_hours}  "
                    f"raw_input 含反馈={'是' if '反馈' in intent.raw_input else '否'}"
                )

            t0 = time.perf_counter()
            result = plan_itinerary_with_mode(intent, "llm", llm_client=client)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if not result.success or not result.itinerary:
                all_results.append(
                    _line(False, f"{scene['id']} 失败：{result.failure_detail}")
                )
                print()
                continue

            itin = result.itinerary
            # edge_v1: 中间节点 = 跳过首尾 home 的节点
            mid_nodes = [n for n in itin.nodes if n.target_kind != "home"]
            print(
                f"  itinerary：{len(itin.nodes)} 节点（{len(mid_nodes)} 中间），"
                f"{len(itin.hops)} 通勤段，{itin.total_minutes} 分钟，{elapsed_ms:.0f}ms"
            )
            print(f"  summary: {itin.summary}")
            for n in itin.nodes:
                if n.target_kind == "home":
                    continue
                print(f"    {n.kind}: {n.start_time} | {n.title}")

            ok = True
            reasons = []
            if "expect_max_minutes" in scene:
                if itin.total_minutes > scene["expect_max_minutes"]:
                    ok = False
                    reasons.append(
                        f"总时长 {itin.total_minutes} > {scene['expect_max_minutes']}"
                    )
            if "expect_max_mid_nodes" in scene:
                if len(mid_nodes) > scene["expect_max_mid_nodes"]:
                    ok = False
                    reasons.append(
                        f"中间节点数 {len(mid_nodes)} > {scene['expect_max_mid_nodes']}"
                    )
            if "expect_min_mid_nodes" in scene:
                if len(mid_nodes) < scene["expect_min_mid_nodes"]:
                    ok = False
                    reasons.append(
                        f"中间节点数 {len(mid_nodes)} < {scene['expect_min_mid_nodes']}"
                    )

            label = scene["id"]
            if ok:
                all_results.append(_line(True, f"{label} 通过"))
            else:
                all_results.append(
                    _line(False, f"{label} 不通过：{'; '.join(reasons)}")
                )

        except Exception as e:  # noqa: BLE001
            all_results.append(
                _line(False, f"{scene['id']} 抛异常：{type(e).__name__}: {e}")
            )

        print()

    print("=" * 60)
    print("\n".join(line for _, line in all_results))
    failed = [line for ok, line in all_results if not ok]
    if failed:
        print(f"\n→ 失败 {len(failed)} 项（共 {len(SCENES)}）")
        return 1
    print(f"\n✓ 全部 {len(SCENES)} 场景通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
