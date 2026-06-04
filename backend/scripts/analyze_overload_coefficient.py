"""分析 _overload_penalty 系数 0.5 / 1.5 / 1.2 在真实候选下的作用。

回答用户的疑问：
- 系数 0.5 不够 → 1.5 是否够？
- 1.5 是否会影响其他场景（家庭外 / 老人外 / 商务接待）？

方法：
1. 加载真实 mock POI
2. 模拟 5 岁娃 S1 场景下，对比 P040（合规）vs P033 梦幻乐园（违规）的 utility
3. 模拟 70 岁老人场景，对比类似两个候选
4. 看不同系数下哪种 POI 排在前面
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _install_agent_stub() -> None:
    import types

    if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
        agent_dir = _BACKEND / "agent"
        stub = types.ModuleType("agent")
        stub.__path__ = [str(agent_dir)]
        sys.modules["agent"] = stub


_install_agent_stub()

import math

from agent.planning.planners.ils_planner import (  # noqa: E402
    _overload_penalty,
    _resolve_age_cap,
    _utility,
)
from agent.planning.weights_llm import PlanningWeights  # noqa: E402
from data.loader import load_pois, load_restaurants  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402


def _make_intent(
    *,
    companions: list[Companion],
    duration: list[int] = [3, 5],
    distance: float = 5.0,
) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=duration,
        distance_max_km=distance,
        companions=companions,
        physical_constraints=["亲子友好", "适合 5-10 岁"],
        dietary_constraints=["低脂", "健康轻食"],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="",
        parse_confidence=0.92,
    )


def _utility_with_coef(poi, rest, dining_time, intent, w, *, overload_coef: float):
    """重写 utility 用任意系数计算（跳过 fail 检查只取 score）。

    复用 _utility 但局部减系数差异（hack：先按 0.5 算再调整）。
    """
    score, fail = _utility(poi, rest, dining_time, intent, w)
    # _utility 内部：score -= 0.5 * overload
    # 我们想要 score' = score - new_coef * overload = score + (0.5 - new_coef) * overload
    overload = _overload_penalty(poi, intent)
    return score + (0.5 - overload_coef) * overload


def _show_scenario(
    title: str,
    intent: IntentExtraction,
    poi_ids: list[str],
    rest_id: str,
    coefs: list[float],
):
    pois = {p.id: p for p in load_pois()}
    rests = {r.id: r for r in load_restaurants()}
    rest = rests[rest_id]
    weights = PlanningWeights(comfort=0.45, time=0.25, cost=0.10, smoothness=0.20)

    print(f"\n=== {title} ===")
    cap = _resolve_age_cap(intent)
    print(f"  age cap = {cap if cap < 9999 else '无'}")

    rows = []
    for pid in poi_ids:
        poi = pois.get(pid)
        if poi is None:
            print(f"  [skip] {pid} not in mock")
            continue
        sd = poi.suggested_duration_minutes
        ovl = _overload_penalty(poi, intent)
        # 算每个系数下的 score
        scores = {c: _utility_with_coef(poi, rest, "17:00", intent, weights, overload_coef=c) for c in coefs}
        rows.append((pid, poi.name[:14], poi.distance_km, sd, ovl, scores))

    # 按 coef=0.5 score 排序看默认序
    print(f"  {'POI':<8}{'name':<16}{'dist':<7}{'suggested':<35}{'ovl':<6}", end="")
    for c in coefs:
        print(f"score(c={c:.1f}) ", end="")
    print()
    print("  " + "-" * 100)
    for pid, name, dist, sd, ovl, scores in rows:
        sd_str = str(sd)[:32]
        print(f"  {pid:<8}{name:<16}{dist:<7.1f}{sd_str:<35}{ovl:<6.1f}", end="")
        for c in coefs:
            print(f"   {scores[c]:.3f}    ", end="")
        print()

    # 选最高 score 的 POI（按各个系数）
    print(f"\n  各系数下排名第一的 POI:")
    for c in coefs:
        ranked = sorted(rows, key=lambda r: r[5][c], reverse=True)
        winner = ranked[0]
        ovl_flag = "⚠ 违规" if winner[4] > 0 else "✓ 合规"
        print(f"    coef={c:.1f}: {winner[0]} ({winner[1]}) score={winner[5][c]:.3f} {ovl_flag}")


def main() -> int:
    coefs = [0.5, 1.0, 1.5, 2.0, 3.0]

    # ========== Scenario 1: 5 岁娃家庭 ==========
    intent_kid = _make_intent(
        companions=[
            Companion(role="妻子", count=1),
            Companion(role="孩子", age=5, count=1),
        ]
    )
    # 候选 POI：P003/P040（合规亲子博物馆，60-75min）vs P033 梦幻乐园（违规 180min）
    # P019 陶艺工坊（180min 也违规） vs P002 西溪艺术展（合规）
    _show_scenario(
        "5 岁娃 S1 家庭主线",
        intent_kid,
        ["P003", "P040", "P033", "P019", "P002", "P004", "P008"],
        "R024",
        coefs,
    )

    # ========== Scenario 2: 78 岁老人 ==========
    intent_old = _make_intent(
        companions=[Companion(role="奶奶", age=78, count=1)],
        duration=[3, 4],
        distance=3.0,
    )
    intent_old.physical_constraints = ["适合老人", "无台阶"]
    intent_old.dietary_constraints = ["软烂"]
    _show_scenario(
        "78 岁老人 S4 带父母散步",
        intent_old,
        ["P006", "P007", "P040", "P003", "P033", "P019"],
        "R024",
        coefs,
    )

    # ========== Scenario 3: 成人独处 ==========
    intent_solo = _make_intent(
        companions=[],
        duration=[3, 4],
    )
    intent_solo.physical_constraints = []
    intent_solo.experience_tags = ["独处舒缓", "安静聊天"]
    _show_scenario(
        "成人独处 S7 放空",
        intent_solo,
        ["P008", "P009", "P011", "P033", "P019", "P040"],
        "R024",
        coefs,
    )

    # ========== Scenario 4: 商务接待 ==========
    intent_biz = _make_intent(
        companions=[Companion(role="商务客户", count=1, is_special_role=True)],
        duration=[3, 4],
    )
    intent_biz.physical_constraints = []
    intent_biz.dietary_constraints = ["高人均", "有包间"]
    intent_biz.experience_tags = ["商务体面"]
    intent_biz.social_context = "商务接待"
    _show_scenario(
        "商务接待 S6",
        intent_biz,
        ["P016", "P017", "P033", "P019", "P040"],
        "R024",
        coefs,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
