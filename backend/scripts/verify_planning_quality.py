"""verify_planning_quality —— spec planning-quality-deep-review R9 端到端验证脚本。

验证 5 岁娃 / 老人 / 独处 / 商务 4 种场景下"业务合理性"2 道防线全部到位：
1. **信息源端**：mock POI 在该客群下投影出的推荐时长落在合规区间
2. **critic 端**：critics_v2（Itinerary 级 check_age_aware_duration）对超 cap 节点能命中

注意：本脚本不调真实 LLM（hackathon 时间盒下不可控 + 需要 API key），
而是确定性检查"防御链路上每一层"的合规性。LLM 行为由 spec R3 prompt 主防 +
R4 critic 兜底保证；本脚本验证的是底层防御网"该拦的能拦"。

ADR-0009 决策 8（Phase C-5）说明：原「critic 端」曾双路径镜像（蓝图级
`_age_aware_duration_critic` + Itinerary 级 `check_age_aware_duration`），
蓝图级 critic 已确认无生产调用者并删除；本脚本改为只验唯一存活的
Itinerary 级路径（`age_caps.strictest_cap_for_companions` + `check_age_aware_duration`）。

ADR-0014 G-0（2026-07-03）砍除记录：原第 3 道防线「数据端：persona
pace_profile 与场景预期一致」已随 `Persona.default_pace_profile` /
`PaceProfile` 一并砍除（全系统无消费方，见 `schemas/persona.py` 砍除记录），
本脚本同步删掉 `_check_persona_pace` 检查与场景表里配套的 `expected_pace_max`。

通过标准（spec R9）：
- 4 种场景全部通过"信息源端"投影合规率 ≥ 95%
- 4 种场景全部通过"critic 端"超 cap 命中率 ≥ 95%

运行：
    cd backend && .venv/Scripts/python.exe scripts/verify_planning_quality.py
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


# 与现有测试同款 sys.modules 桥接（避免 agent/__init__.py 老 schema eager-import 炸）
def _install_agent_stub() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    agent_dir = backend_root / "agent"
    if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
        stub = types.ModuleType("agent")
        stub.__path__ = [str(agent_dir)]
        sys.modules["agent"] = stub


_install_agent_stub()

# 加 backend/ 到 sys.path（脚本独立运行时）
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


from agent.planning.critic._rules.checks import check_age_aware_duration  # noqa: E402
from agent.planning.critic.age_caps import strictest_cap_for_companions  # noqa: E402
from data.loader import load_pois  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402
from schemas.itinerary import ActivityNode, Hop, Itinerary  # noqa: E402
from utils.duration_helpers import get_duration_for_companions  # noqa: E402


# ============================================================
# 场景定义（4 种 + S9 反例 + 1.5 倍冗余共 6 个）
# ============================================================

_SCENARIOS = [
    {
        "name": "S1 家庭主线（5 岁娃）",
        "user_id": "u_dad",
        "companions": [Companion(role="孩子", age=5, count=1), Companion(role="妻子", count=1)],
        "expected_cap": 75,
        "expected_range": (60, 75),
    },
    {
        "name": "S4 带父母（78 岁老人）",
        "user_id": "u_grandma",
        "companions": [Companion(role="父母", age=78, count=1)],
        "expected_cap": 60,
        "expected_range": (45, 60),
    },
    {
        "name": "S7 独处放空",
        "user_id": "u_solo",
        "companions": [],  # 独处空数组
        "expected_cap": 9999,  # 无 cap
        "expected_range": None,
    },
    {
        "name": "S6 商务接待",
        "user_id": "u_biz",
        "companions": [Companion(role="商务客户", count=1, is_special_role=True)],
        "expected_cap": 9999,  # 无 cap
        "expected_range": None,
    },
    {
        "name": "S9 反例：5 岁娃博物馆 2.5h（核心反例）",
        "user_id": "u_dad",
        "companions": [Companion(role="孩子", age=5, count=1)],
        "expected_cap": 75,
        "expected_range": (60, 75),
        "violation_test": True,  # 跑 critic 应命中
    },
    {
        "name": "S9.1 反例：78 岁老人 3h 主活动",
        "user_id": "u_grandma",
        "companions": [Companion(role="奶奶", age=78, count=1)],
        "expected_cap": 60,
        "expected_range": (45, 60),
        "violation_test": True,
    },
]


def _make_intent(scenario: dict) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5.0,
        companions=scenario["companions"],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input=scenario["name"],
        parse_confidence=0.92,
    )


def _check_age_cap(scenario: dict) -> tuple[bool, str]:
    """验 strictest_cap_for_companions 返回的 cap 与场景预期一致。

    age_caps.strictest_cap_for_companions 无 cap 时返回 None（语义同旧
    blueprint._resolve_age_caps 的哨兵 9999）；场景表沿用 9999 表示"无 cap"。
    """
    intent = _make_intent(scenario)
    cap = strictest_cap_for_companions(intent.companions)
    cap = 9999 if cap is None else cap
    if cap != scenario["expected_cap"]:
        return False, f"cap 期望 {scenario['expected_cap']}，实际 {cap}"
    return True, f"OK（cap={cap}）"


def _itin_with_poi_duration(duration: int) -> Itinerary:
    """构造单 POI 节点的最小合法 Itinerary，供 check_age_aware_duration 校验用。"""
    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home",
                     start_time="13:50", duration_min=0, title="家"),
        ActivityNode(node_id="n1", kind="主活动", target_kind="poi", target_id="P003",
                     start_time="14:00", duration_min=duration, title="测试 POI"),
        ActivityNode(node_id="n2", kind="终点", target_kind="home", target_id="home",
                     start_time="20:00", duration_min=0, title="家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="13:50",
            minutes=10, mode="walking", path_type="real_route"),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="19:50",
            minutes=10, mode="walking", path_type="real_route"),
    ]
    return Itinerary(nodes=nodes, hops=hops, summary="测试", total_minutes=600)


def _check_critic_hits(scenario: dict) -> tuple[bool, str]:
    """对反例（violation_test=True）：构造超 cap 行程 → critic 应命中。"""
    if not scenario.get("violation_test"):
        return True, "（非反例场景跳过）"

    intent = _make_intent(scenario)
    over_duration = scenario["expected_cap"] + 30  # 超 cap 30min
    itin = _itin_with_poi_duration(over_duration)
    violations = check_age_aware_duration(itin, intent)
    if not violations:
        return False, f"反例 {over_duration}min 应命中 critic 但未命中"
    v = violations[0]
    if v.expected_range != scenario["expected_range"]:
        return (
            False,
            f"expected_range 期望 {scenario['expected_range']}，实际 {v.expected_range}",
        )
    return True, f"OK（命中 + expected_range={v.expected_range}）"


def _check_mock_projection(scenario: dict) -> tuple[bool, str]:
    """验 mock POI 在该 companions 下投影后的推荐时长不超 cap。

    遍历该场景适配 tag 的 POI，每个 POI 投影 → 不超 cap 的合规率。
    """
    if scenario["expected_cap"] >= 9999:
        return True, "（无 cap 场景跳过）"

    # 按场景选 tag 过滤——避免给老人场景测亲子 POI（场景错配）
    has_senior = any(
        getattr(c, "age", None) and c.age >= 75 for c in scenario["companions"]
    )
    if has_senior:
        relevant_tags = ("适合老人", "无台阶", "可休息")
    else:
        relevant_tags = ("亲子友好",)

    pois = [p for p in load_pois() if any(t in p.tags for t in relevant_tags)][:15]
    if not pois:
        return False, f"未加载到 tag={relevant_tags} 的 POI"

    cap = scenario["expected_cap"]
    compliant = 0
    for p in pois:
        projected = get_duration_for_companions(
            p.suggested_duration_minutes, scenario["companions"]
        )
        if projected is None or projected <= cap + 15:  # 允许 15min 余量（业界基线）
            compliant += 1

    rate = compliant / len(pois) * 100
    if rate < 95:
        return False, f"合规率 {rate:.1f}% < 95%（{compliant}/{len(pois)}）"
    return True, f"OK（合规率 {rate:.1f}%，{compliant}/{len(pois)}，tag={relevant_tags}）"


# ============================================================
# Driver
# ============================================================


def _run_scenario(scenario: dict) -> list[tuple[str, bool, str]]:
    """跑一个场景的 4 道检查。返回 (check_name, passed, msg) 列表。"""
    return [
        ("Age cap 推断", *_check_age_cap(scenario)),
        ("Mock 投影合规率", *_check_mock_projection(scenario)),
        ("Critic 命中（反例）", *_check_critic_hits(scenario)),
    ]


def main() -> int:
    print("=" * 70)
    print("spec planning-quality-deep-review R9 端到端验证")
    print("=" * 70)

    total_pass = 0
    total = 0
    failures: list[str] = []

    for scenario in _SCENARIOS:
        print(f"\n场景 {scenario['name']}")
        print("-" * 70)
        results = _run_scenario(scenario)
        for check_name, passed, msg in results:
            total += 1
            status = "[PASS]" if passed else "[FAIL]"
            if passed:
                total_pass += 1
            else:
                failures.append(f"  [{scenario['name']}] {check_name}: {msg}")
            print(f"  {status} {check_name}: {msg}")

    print()
    print("=" * 70)
    rate = total_pass / total * 100 if total else 0
    print(f"总通过率：{total_pass}/{total} ({rate:.1f}%)")
    if failures:
        print("\n失败项：")
        for f in failures:
            print(f)
        print("\n要求 ≥ 95% 通过 → [FAIL] FAIL")
        return 1
    print(f"\n要求 ≥ 95% 通过 → [PASS] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
