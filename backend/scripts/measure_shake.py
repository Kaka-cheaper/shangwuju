"""scripts.measure_shake —— ADR-0010 D-6：贪心 vs 贪心+shake 结构差异实测。

【为什么要跑这个】

ADR-0010 决策 7："先只做贪心插入构造；再实测『贪心 vs 贪心+shake』在 S1-S8 上的
结构差异——shake 明显让某场景更对才留，说不出哪更对就砍。" 本脚本是这条决策的
实测落地：对 S1-S8（+ 若干 duration/出发时刻变体扩样本）各跑一次纯贪心
（`route_builder.build_route`）与贪心+shake（额外套
`route_shake.shake_route`，K=20 轮，固定 seed），对照 route_score / 活动构成 /
耗时，把数据摆出来——**去留判断单独写在脚本末尾的注释里，不混进数据输出**（复核
纪律：数据和判读分开，报告使用者要能独立核对）。

跑法：
    cd backend && python scripts/measure_shake.py

不需要真 LLM（全程 StubLLMClient + 真实 mock 候选池，确定性）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

os.environ.setdefault("SHANGWUJU_MOCK_DIR", str(Path(__file__).resolve().parents[2] / "mock_data"))
os.environ.setdefault("LLM_PROVIDER", "stub")

from agent.core.llm_client_stub import StubLLMClient
from agent.core.trace import Tracer
from agent.planning.planners.activity_pool import (
    build_poi_route_pool,
    build_restaurant_route_pool,
    build_visit_from_poi,
    build_visit_from_restaurant,
    route_score,
)
from agent.planning.planners.ils_planner import _query_pois, _query_restaurants, _resolve_depart_min
from agent.planning.planners.pace_budget import interval_fill_targets, pace
from agent.planning.planners.route_builder import build_route, make_commute_fn
from agent.planning.planners.route_shake import shake_route
from agent.planning.weights_llm import get_planning_weights
from data.loader import load_user_profile, reset_cache
from schemas.intent import IntentExtraction

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from test_e2e_refinement import SCENARIOS, _intent  # noqa: E402

SHAKE_K = 20
SHAKE_SEED = 42


# ============================================================
# 样本：S1-S8（原样）+ 3 组 duration/出发时刻变体（扩样本，D-6 任务原文要求）
# ============================================================


def _variant(base_scenario: str, **overrides) -> tuple[str, IntentExtraction]:
    payload = dict(SCENARIOS[base_scenario])
    payload.update(overrides)
    label = f"{base_scenario}+" + ",".join(f"{k}={v}" for k, v in overrides.items())
    return label, _intent(payload)


def build_cases() -> list[tuple[str, IntentExtraction]]:
    cases = [(sid, _intent(payload)) for sid, payload in SCENARIOS.items()]
    cases.append(_variant("S2", duration_hours=[5, 7]))  # 朋友热闹，拉长预算看貪心是否仍只挑 2 个大块
    cases.append(_variant("S2", distance_max_km=8))  # 朋友热闹，放宽距离看候选池是否给出更多短时长选项
    cases.append(_variant("S7", duration_hours=[3, 5]))  # 独处放空，拉长窗口看是否仍收在 2-3 站
    return cases


# ============================================================
# 单场景跑 greedy / greedy+shake，产出对照行
# ============================================================


def _fmt_visits(visits) -> str:
    return "、".join(f"{v.kind}:{v.target_id}" for v in visits)


def run_case(case_id: str, intent: IntentExtraction) -> dict:
    tracer = Tracer()
    pois = _query_pois(intent, tracer)
    restaurants = _query_restaurants(intent, tracer)
    weights = get_planning_weights(intent, client=StubLLMClient())
    depart_min = _resolve_depart_min(intent.start_time)
    user_profile = load_user_profile()
    commute_fn = make_commute_fn(user_profile)
    money_budget = user_profile.default_budget

    # ---- greedy（route_builder.build_route，D-4 既有实现，未改动）----
    t0 = time.perf_counter()
    build_result = build_route(
        pois, restaurants, intent, weights,
        depart_min=depart_min, commute_fn=commute_fn,
    )
    t_greedy_ms = (time.perf_counter() - t0) * 1000

    greedy_visits = list(build_result.visits)
    greedy_schedule = build_result.schedule
    greedy_score = route_score(
        [sv.visit for sv in greedy_schedule.scheduled], weights, money_budget
    )

    # ---- greedy+shake：在 build_route 产出的路线上跑 K 轮扰动 ----
    # 候选池按 build_route 内部同款构造重算（route_shake 不重复召回/建池逻辑，
    # 见 route_shake.py 模块 docstring「不负责」）。
    poi_pool = build_poi_route_pool(list(pois))
    rest_pool = build_restaurant_route_pool(list(restaurants))
    poi_visits = [build_visit_from_poi(p, intent, weights) for p in poi_pool]
    rest_visits = [build_visit_from_restaurant(r, intent, weights) for r in rest_pool]
    full_pool = poi_visits + rest_visits
    selected_keys = {(v.kind, v.target_id) for v in greedy_visits}
    available_pool = [v for v in full_pool if (v.kind, v.target_id) not in selected_keys]

    pace_tier = pace(intent)
    targets = interval_fill_targets(intent, pace_tier)

    t0 = time.perf_counter()
    shake_result = shake_route(
        greedy_visits, greedy_schedule, available_pool, weights,
        depart_min=depart_min, budget_min=targets.hi_min, commute_fn=commute_fn,
        money_budget=money_budget, targets=targets, k=SHAKE_K, seed=SHAKE_SEED,
    )
    t_shake_ms = (time.perf_counter() - t0) * 1000

    greedy_keys = {(v.kind, v.target_id) for v in greedy_visits}
    shake_keys = {(v.kind, v.target_id) for v in shake_result.visits}
    composition_changed = greedy_keys != shake_keys

    return {
        "case_id": case_id,
        "pace_tier": pace_tier,
        "greedy_n": len(greedy_visits),
        "greedy_score": greedy_score,
        "greedy_visits": _fmt_visits(greedy_visits),
        "greedy_total_min": greedy_schedule.total_minutes,
        "shake_n": len(shake_result.visits),
        "shake_score": shake_result.score_after,
        "shake_visits": _fmt_visits(shake_result.visits),
        "shake_total_min": shake_result.schedule.total_minutes,
        "accepted_rounds": shake_result.accepted_rounds,
        "score_delta": shake_result.score_after - greedy_score,
        "composition_changed": composition_changed,
        "t_greedy_ms": t_greedy_ms,
        "t_shake_ms": t_shake_ms,
    }


def main() -> None:
    reset_cache()
    cases = build_cases()
    rows = [run_case(cid, intent) for cid, intent in cases]

    print("=" * 120)
    print(f"{'case':<22}{'pace':<10}{'greedy_n':<9}{'shake_n':<8}{'score_delta':<12}"
          f"{'accepted':<9}{'comp_chg':<9}{'t_greedy_ms':<12}{'t_shake_ms':<11}")
    print("-" * 120)
    for r in rows:
        print(
            f"{r['case_id']:<22}{r['pace_tier']:<10}{r['greedy_n']:<9}{r['shake_n']:<8}"
            f"{r['score_delta']:<12.4f}{r['accepted_rounds']:<9}{str(r['composition_changed']):<9}"
            f"{r['t_greedy_ms']:<12.2f}{r['t_shake_ms']:<11.2f}"
        )
    print("=" * 120)
    print()
    for r in rows:
        print(f"--- {r['case_id']} ---")
        print(f"  greedy ({r['greedy_n']} 活动, score={r['greedy_score']:.4f}, "
              f"total={r['greedy_total_min']}min): {r['greedy_visits']}")
        print(f"  shake  ({r['shake_n']} 活动, score={r['shake_score']:.4f}, "
              f"total={r['shake_total_min']}min): {r['shake_visits']}")
        print(f"  accepted_rounds={r['accepted_rounds']}/{SHAKE_K}  "
              f"score_delta={r['score_delta']:+.4f}  composition_changed={r['composition_changed']}")
        print()


if __name__ == "__main__":
    main()
