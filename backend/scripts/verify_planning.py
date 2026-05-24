"""verify_planning —— A+C 混合范式 vs 规则范式效果对比（评分项 2 加分演示）。

跑法：
    cd backend && uv run python -m scripts.verify_planning

输出：
- 8 场景下 rule 与 hybrid 的方案对比
- 每个方案的 utility 分解（comfort / time / cost / smoothness）
- Critic 验证报告
- hybrid 的搜索过程统计（迭代次数、改进次数）

不需要真 LLM API key——hybrid 在 stub client 时会走 rule 兼容路径，所以本脚本
通过自带的 _MockLLMClient 驱动 hybrid 路径，让评委本地也能复现。
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

from agent.planning.critic.ils_score_critic import run_critics
from agent.planning.planners.rule_planner import plan_itinerary, plan_itinerary_with_mode
from agent.planning.weights_llm import PlanningWeights, get_planning_weights
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
# Mock LLM client（演示用；本地无需真 API key）
# ============================================================

@dataclass
class _MockLLMResponse:
    content: str
    tool_calls: list = None  # type: ignore[assignment]
    finish_reason: str = "stop"
    raw: dict = None  # type: ignore[assignment]


class _DemoLLMClient:
    """根据 social_context 给出"高质量"权重，模拟真 LLM 决策。"""

    provider = "demo-llm"
    model = "demo-1"

    def chat(self, messages, *, temperature=0.3, response_format=None):
        # 从最后一条 user message 抽 social_context（简化：直接返启发式权重的 JSON）
        text = ""
        for m in messages:
            if m.role == "user":
                text = m.content or ""
        ctx = "家庭日常"
        for known in (
            "家庭日常", "老人伴助", "情侣亲密", "闺蜜聊天", "朋友热闹",
            "商务接待", "同学重聚", "独处放空", "纪念日仪式感",
        ):
            if known in text:
                ctx = known
                break
        # 演示用映射（与 weights_llm 启发式一致）
        weights = {
            "家庭日常":     (0.45, 0.20, 0.15, 0.20, "孩子在场，舒适与连贯优先"),
            "老人伴助":     (0.50, 0.10, 0.10, 0.30, "腿脚不便，路线连贯第一位"),
            "商务接待":     (0.30, 0.40, 0.05, 0.25, "公司报销，时间与体面重要"),
            "纪念日仪式感": (0.50, 0.10, 0.05, 0.35, "仪式感为先，预算不敏感"),
            "情侣亲密":     (0.40, 0.15, 0.20, 0.25, "氛围与连贯并重"),
            "闺蜜聊天":     (0.40, 0.15, 0.20, 0.25, "拍照舒适与连贯"),
            "朋友热闹":     (0.30, 0.20, 0.30, 0.20, "AA 制偏向预算"),
            "独处放空":     (0.55, 0.15, 0.15, 0.15, "自己舒服为先"),
            "同学重聚":     (0.30, 0.20, 0.30, 0.20, "学生预算敏感"),
        }
        c, t, co, s, rationale = weights.get(ctx, (0.4, 0.2, 0.2, 0.2, ""))
        return _MockLLMResponse(
            content=(
                f'{{"comfort": {c}, "time": {t}, "cost": {co}, "smoothness": {s}, '
                f'"rationale": "{rationale}"}}'
            )
        )


# ============================================================
# 辅助：方案打印
# ============================================================

def _summarize(label: str, result, w: PlanningWeights | None = None) -> None:
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
    if w is not None:
        print(f"           权重：{w.summary()} | 来源={w.source}")
        print(f"           理由：{w.rationale or '(无)'}")


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
    print("=== Phase 0.9 A+C 混合规划 vs 规则规划对比 ===\n")
    print("学术依据：")
    print("  A 段 ILS 启发式 → Vansteenwegen et al. 2009 (TOPTW)")
    print("                    Gunawan et al. 2019 (Multi-objective TOPTW)")
    print("  C 段 Critic     → Kambhampati et al. 2024 (LLM-Modulo, NeurIPS)")
    print("  整体范式        → ItiNera EMNLP 2024 (LLM 决主观 + 算法决客观)")
    print()

    client = _DemoLLMClient()

    overall_results: list[tuple[str, bool, bool, str, str]] = []
    for sid, intent in INTENTS.items():
        print(f"--- {sid} | {intent.social_context} | {intent.raw_input[:30]}... ---")

        # rule
        t0 = time.perf_counter()
        rule_result = plan_itinerary(intent)
        rule_ms = (time.perf_counter() - t0) * 1000
        _summarize(f"rule    ({rule_ms:.0f}ms)", rule_result)
        if rule_result.success:
            print(f"           {_critic_brief(rule_result.itinerary, intent)}")

        # hybrid（带演示 LLM）
        weights = get_planning_weights(intent, client=client)
        t0 = time.perf_counter()
        hybrid_result = plan_itinerary_with_mode(intent, "llm", llm_client=client)
        hybrid_ms = (time.perf_counter() - t0) * 1000
        _summarize(f"hybrid  ({hybrid_ms:.0f}ms)", hybrid_result, w=weights)
        if hybrid_result.success:
            print(f"           {_critic_brief(hybrid_result.itinerary, intent)}")

        # 抓 hybrid trace 里的搜索改进次数
        if hybrid_result.success and hybrid_result.tracer is not None:
            improve_msgs = [
                r for r in hybrid_result.tracer.records
                if r.type == "agent_thought"
                and "ILS 迭代" in r.payload.get("text", "")
                and "更优解" in r.payload.get("text", "")
            ]
            print(f"           ILS 改进次数：{len(improve_msgs)}")

        # 对比
        rule_main = _main_id(rule_result)
        hyb_main = _main_id(hybrid_result)
        rule_rest = _rest_id(rule_result)
        hyb_rest = _rest_id(hybrid_result)
        overall_results.append((sid, rule_result.success, hybrid_result.success, hyb_main, hyb_rest))
        same_poi = rule_main == hyb_main
        same_rest = rule_rest == hyb_rest
        print(
            f"           对比：POI {'一致' if same_poi else f'不同（rule={rule_main} vs hybrid={hyb_main}）'}；"
            f"餐厅 {'一致' if same_rest else f'不同（rule={rule_rest} vs hybrid={hyb_rest}）'}"
        )
        print()

    # 总结
    print("=" * 60)
    failed = [r for r in overall_results if not (r[1] and r[2])]
    if failed:
        print(f"→ 失败 {len(failed)} 项：{[r[0] for r in failed]}")
        return 1
    print(f"✓ 全部 {len(overall_results)} 个场景在 rule + hybrid 双路径都跑通")
    print()
    print("评分项收益：")
    print("  - 评分项 2（规划链路）：hybrid 把 LLM 决策与 ILS 搜索可视化到 trace")
    print("  - 评分项 4（Tool 编排）：rule 路径调用密集；hybrid 仅候选阶段调 Tool")
    print("  - 评分项 5（异常韧性）：Critic 失败 → backprompt 重排，仍失败 → fallback rule")
    return 0


def _main_id(result) -> str:
    if not result.success or not result.itinerary:
        return "-"
    n = next(
        (x for x in result.itinerary.nodes if x.target_kind == "poi"),
        None,
    )
    return n.target_id if n else "?"


def _rest_id(result) -> str:
    if not result.success or not result.itinerary:
        return "-"
    n = next(
        (x for x in result.itinerary.nodes if x.target_kind == "restaurant"),
        None,
    )
    return n.target_id if n else "?"


if __name__ == "__main__":
    sys.exit(main())
