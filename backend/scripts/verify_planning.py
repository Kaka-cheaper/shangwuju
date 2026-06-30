"""verify_planning —— 规则规划范式 8 场景烟测（评分项 2 链路自检）。

跑法：
    cd backend && uv run python -m scripts.verify_planning

输出：
- 代表性场景下 rule 规划（plan_itinerary）的方案 + Critic 验证报告
- 不需要真 LLM API key（纯规则路径，毫秒级出方案，断网可跑）

注：原「rule vs A+C 混合对比」的 hybrid 段随 V1 双范式分发入口退役删除（规划层收口）。
hybrid / ILS 路径的回归由 tests/test_planner_hybrid.py 覆盖；本脚本现仅自检 rule planner
（plan_itinerary）链路。
"""

from __future__ import annotations

import sys
import time

from agent.planning.critic.ils_score_critic import run_critics
from agent.planning.planners.rule_planner import plan_itinerary
from schemas.intent import Companion, IntentExtraction


# ============================================================
# 8 场景输入（与 test_8_scenarios.py 同源）
# ============================================================

INTENTS: dict[str, IntentExtraction] = {
    "S1_家庭": IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[
            Companion(role="妻子", count=1),
            Companion(role="孩子", age=5, count=1),
        ],
        physical_constraints=["亲子友好", "适合 5-10 岁"],
        dietary_constraints=["低脂", "健康轻食"],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="今天下午想和老婆孩子出去玩几个小时，孩子 5 岁，老婆减肥",
        parse_confidence=0.92,
    ),
    "S4_老人": IntentExtraction(
        start_time="sunday_afternoon",
        start_weekday="sunday",
        duration_hours=[3, 5],
        distance_max_km=3,
        companions=[
            Companion(role="外公", count=1, is_special_role=True),
            Companion(role="外婆", count=1, is_special_role=True),
        ],
        physical_constraints=["适合老人", "无台阶", "可休息"],
        dietary_constraints=["软烂"],
        experience_tags=[],
        social_context="老人伴助",
        raw_input="周日下午带外公外婆走走，腿不好",
        parse_confidence=0.88,
    ),
    "S6_商务": IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[Companion(role="商务客户", count=1, is_special_role=True)],
        physical_constraints=[],
        dietary_constraints=["高人均", "有包间"],
        experience_tags=["商务体面", "礼仪感"],
        social_context="商务接待",
        raw_input="临时要接外地客户，商务体面些",
        parse_confidence=0.82,
    ),
    "S8_纪念日": IntentExtraction(
        start_time="sunday_lunch",
        start_weekday="sunday",
        duration_hours=[3, 4],
        distance_max_km=5,
        companions=[
            Companion(role="母亲", count=1, is_birthday=True, is_special_role=True),
            Companion(role="全家", count=6),
        ],
        physical_constraints=["适合老人"],
        dietary_constraints=["粤菜"],
        experience_tags=["礼仪感"],
        social_context="纪念日仪式感",
        capacity_requirement=6,
        extra_services=["蛋糕"],
        raw_input="周日妈妈生日，全家 6 人吃粤菜",
        parse_confidence=0.84,
    ),
}


# ============================================================
# 辅助：方案打印
# ============================================================

def _summarize(label: str, result) -> None:
    print(f"  [{label}] success={result.success}", end="")
    if not result.success:
        print(f"，failure={result.failure_detail}")
        return
    itin = result.itinerary
    # edge_v1：从 nodes 里找主活动 / 用餐节点（首尾 home 跳过）
    main = next(
        (n for n in itin.nodes if n.target_kind == "poi"),
        None,
    )
    dining = next(
        (n for n in itin.nodes if n.target_kind == "restaurant"),
        None,
    )
    main_id = main.target_id if main else "?"
    dining_id = dining.target_id if dining else "?"
    dining_start = dining.start_time if dining else "?"
    print(f"，主活动={main_id} 用餐={dining_id}@{dining_start}")


def _critic_brief(itinerary, intent) -> str:
    rep = run_critics(itinerary, intent)
    if not rep.violations:
        return "Critic：全过"
    return (
        f"Critic：硬{len([v for v in rep.violations if v.severity == 'hard'])} "
        f"软{len([v for v in rep.violations if v.severity == 'soft'])} "
        f"soft_score={rep.soft_score:.2f}"
    )


# ============================================================
# 主流程
# ============================================================

def main() -> int:
    print("=== 规则规划范式 8 场景烟测（plan_itinerary）===\n")

    results: list[tuple[str, bool]] = []
    for sid, intent in INTENTS.items():
        print(f"--- {sid} | {intent.social_context} | {intent.raw_input[:30]}... ---")

        t0 = time.perf_counter()
        rule_result = plan_itinerary(intent)
        rule_ms = (time.perf_counter() - t0) * 1000
        _summarize(f"rule    ({rule_ms:.0f}ms)", rule_result)
        if rule_result.success:
            print(f"           {_critic_brief(rule_result.itinerary, intent)}")

        results.append((sid, rule_result.success))
        print()

    # 总结
    print("=" * 60)
    failed = [r for r in results if not r[1]]
    if failed:
        print(f"→ 失败 {len(failed)} 项：{[r[0] for r in failed]}")
        return 1
    print(f"✓ 全部 {len(results)} 个场景 rule 路径跑通")
    return 0


if __name__ == "__main__":
    sys.exit(main())
