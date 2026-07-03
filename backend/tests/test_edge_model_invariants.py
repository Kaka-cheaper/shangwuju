"""tests.test_edge_model_invariants —— edge_v1 Itinerary 8 条不变量 fuzz 测试。

【任务上下文】

`itinerary-edge-model-refactor` Task 15（Wave 7）：在随机 blueprint 输入下批量
跑 `assemble_from_blueprint`，每次都断言 8 条结构 / 时序 / 同地复用相关
不变量；任一断言失败立即让整轮 fuzz 失败（pytest 会带种子复现）。

【为什么是 fuzz 不是单元用例】

`test_assemble_blueprint.py` 已用 4 个具名场景（A1 标准 / A2 单段 / A3 同地复用 /
A4 反序）覆盖典型 happy path；本测试在更大输入空间随机抽样，专门拍
edge case：mid nodes 个数 1-5 任选、target_kind 半半概率、target_id 从 mock 候选
随机抽、30% 概率制造同地复用（连续两 node 同 target_id），让 lookup_hop 的
2/3 级降级、in_place 分支被反复触发。

【8 条不变量来源】

`.kiro/specs/itinerary-edge-model-refactor/design.md` Correctness Properties 1-8。
均为 model 级公共合约，由 Pydantic `model_validator` 与 assemble 算法共同保证：

    | # | 不变量                                        | 出处                       |
    |---|----------------------------------------------|---------------------------|
    | 1 | hops 长度 = nodes - 1                          | Property 1                |
    | 2 | 首尾节点 target_kind == "home"                 | Property 2                |
    | 3 | 首尾节点 target_id == "home"                   | Property 8                |
    | 4 | 首尾节点 duration_min == 0                     | Property 3                |
    | 5 | hop.start_time == from_node 结束时刻           | 时序 invariant（Property 5）|
    | 6 | to_node.start_time >= hop.end_time + buffer    | 时序 invariant（Property 5，真因修复批 item 1 放宽为不等式：餐厅槽吸附/not_before_start 合法产生 slack）|
    | 7 | in_place hop minutes=0 且 from/to 同 target_id | Property 7                |
    | 8 | total_minutes == last_node.start - first_hop.start | Property 6              |

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.7, 3.1, 3.2, 3.3, 3.4, 3.6, 4.1, 5.3**

【可复现性】

种子由 `pytest.mark.parametrize("seed", range(10))` 注入，每次 fuzz 用
`random.Random(seed)` 私有 RNG，**不**碰模块级 `random.random` 全局态。
任一种子失败 → `pytest -k "seed-3"` 即可复现。

【过渡态桥】

`agent/__init__.py` eager-import `planner.py`（依赖旧 ItineraryStage——Task 9 修），
但 `agent.assemble_blueprint / agent.blueprint / agent.lookup_hop` 自身不依赖
兄弟模块。下面把 `agent` 注册为空命名空间包，让 from-import 跳过 __init__.py
副作用——参考 `tests/test_assemble_blueprint.py` 同款套路。
"""

from __future__ import annotations

import random
import sys
import types

import pytest


# ============================================================
# 桥接：绕过 agent/__init__.py eager-import 旧 planner
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    from pathlib import Path as _Path

    _agent_dir = _Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]  # 让 Python 把它当包，子模块按文件解析
    sys.modules["agent"] = _stub

from agent.planning.commute import lookup_hop as _lookup_hop_mod  # noqa: E402
from agent.planning.blueprint.assemble_blueprint import (  # noqa: E402
    _parse_hhmm,
    assemble_from_blueprint,
)
from agent.planning.blueprint.blueprint import (  # noqa: E402
    BlueprintNode,
    BlueprintTargetKind,
    PlanBlueprint,
)
from data.loader import load_pois, load_restaurants, load_user_profile  # noqa: E402
from schemas.domain import Poi, Restaurant, UserProfile  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402
from schemas.itinerary import Itinerary  # noqa: E402


# ============================================================
# Fixtures：清缓存 + 公共数据
# ============================================================


@pytest.fixture(autouse=True)
def _reset_lookup_cache() -> None:
    """每个测试前后清空 lookup_hop 模块级 lru_cache，避免被相邻测试 monkeypatch 污染。"""
    _lookup_hop_mod.reset_cache()
    yield
    _lookup_hop_mod.reset_cache()


@pytest.fixture(scope="module")
def profile() -> UserProfile:
    """demo_user 画像（含 home_location 坐标 + transport_preference）。"""
    return load_user_profile()


@pytest.fixture(scope="module")
def pois() -> list[Poi]:
    """所有候选 POI，从 mock_data/pois.json 加载。"""
    return load_pois()


@pytest.fixture(scope="module")
def restaurants() -> list[Restaurant]:
    """所有候选餐厅，从 mock_data/restaurants.json 加载。"""
    return load_restaurants()


def _make_minimal_intent() -> IntentExtraction:
    """fuzz 测试用的最小合法意图。

    assemble 当前不读 intent 字段（仅作签名占位），任何合法值都行；
    保持与 test_assemble_blueprint 同款最小构造，避免 Pydantic 校验报错。
    """
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 6],
        distance_max_km=5,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="fuzz 输入",
        parse_confidence=0.9,
    )


# ============================================================
# Fuzz 蓝图生成器
# ============================================================


_DAY_END_MIN: int = 24 * 60
"""一天 24h 折算分钟数，用于约束累计 duration 不跨日。"""

_HOP_BUDGET_MIN: int = 240
"""hop+buffer 总预算（分钟）：6 hops × ~30min + 5×buffer ≈ 205min，留 35min 余量。

实测 mock_data 里 home↔POI 走 haversine 估算偶尔到 25-30min；6 hops 极端值 ~180min，
加上中间 5 个 buffer × 5 = 25min，总开销不会超过 240min。
"""


def _restaurant_latest_slot_min(r: Restaurant) -> int | None:
    """该餐厅 `reservation_slots` 里最晚的时刻（分钟坐标）；无槽 → None。"""
    slots_min = [_parse_hhmm(s.time) for s in r.reservation_slots]
    return max(slots_min) if slots_min else None


def _restaurant_worst_case_end(
    r: Restaurant, *, start_min: int, accumulated: int, duration: int
) -> int:
    """该餐厅节点计入 `duration` 后的"最坏情形累计"（真因修复批 item 1 追加）。

    assemble 会把餐厅节点吸附到该店最早的"不早于自然到达"的可用槽——如果
    自然到达（约等于 `start_min + accumulated`）早于该店最晚的槽，最坏情形
    就是被吸附到那个最晚的槽。`accumulated` 只是"从 start_min 起算的偏移"，
    与真实分钟坐标的换算即 `start_min + accumulated` vs 槽的绝对分钟坐标。
    """
    latest_slot = _restaurant_latest_slot_min(r)
    if latest_slot is None:
        worst_case_offset = accumulated
    else:
        worst_case_offset = max(accumulated, latest_slot - start_min)
    return worst_case_offset + duration


def _make_random_blueprint(
    rng: random.Random,
    pois: list[Poi],
    restaurants: list[Restaurant],
) -> PlanBlueprint:
    """随机构造一个合法、不跨日的 PlanBlueprint。

    生成策略：
    - mid nodes 数量在 [1, 5] 内随机；
    - 每个节点以 50% 概率从 POI 候选选，否则从 restaurant 候选选；
    - duration_min 区间：POI [30, 180] / 餐厅 [40, 120]，落在原蓝图 critic 的
      [10, 300] 合理区间内（该 critic 已随 ADR-0009 决策 8 删除，此处仅延续
      同一输入域，绝不触发 RuntimeError）；
    - 累计 duration 上限：(24h - start_min - HOP_BUDGET) → 防止 cursor 跨过 24:00
      让 `_fmt_hhmm` 静默 mod 24h（design.md 明确不支持跨日，fuzz 须自行约束
      输入域）。
      若新节点会让累计超额，**改用合理上限内的随机值**，保证至少有一节点入选；
    - **餐厅节点的"最坏情形已消耗时长"按其真实 `reservation_slots` 里最晚的
      那个槽估（真因修复批 item 1 追加，见下方专门说明），不是只看
      `duration_min`**——否则真实槽吸附（自然到达对不上槽时顺延到最近可用槽）
      会在累计预算之外再吃掉数十到数百分钟，让后续节点的 cursor 静默跨过
      24:00（`_fmt_hhmm` 会 mod 24h 截断成"看起来合法但实际错位"的时刻，
      正是这条 fuzz 本来要防的输入域越界）；
    - 30% 概率把第二个节点的 target_id/target_kind 复制给第一个节点，
      触发 lookup_hop 的 1 级 in_place 分支；
    - preferred_start_time 限制在 10:00-13:00，给 6 hops（~180min）留出余量。

    【真因修复批 item 1 追加说明：为什么"累计"要按餐厅最晚槽估算，不能只按
    duration_min 估算】

    本 fuzz 生成器一直以来的隐含前提是"assemble 产出的时刻只由 duration_min +
    通勤累加决定"——`accumulated` 只track这两者之和，配合 `_HOP_BUDGET_MIN`
    这个通勤/buffer 上限估计，保证不越过当日。但 assemble 现在多了一条第三
    条腿：LLM 路径的餐厅节点会被吸附到该店 `reservation_slots` 里"不早于
    自然到达"的最早可用槽——如果自然到达远早于该店最早的槽（如中午到达一家
    只营业晚市的餐厅），吸附可能一口气吃掉几个小时，这段"槽等待"从未被
    `accumulated` 计入过。真实 mock 数据里相当一部分餐厅只有晚市槽位（如
    R001 仅 17:00-18:00），fuzz 随机抽样很容易抽到"仍在上午/中午时段却选中
    一家晚市专门店"的组合，此前的预算公式对这种情形完全没有防御——本次追加
    "餐厅节点按其最晚槽估算最坏情形"，让生成阶段就诚实地把这份潜在等待计入
    预算，而不是等 assemble 吸附后才在 `_fmt_hhmm` 里悄悄 wrap 到次日。

    Args:
        rng: 私有 random.Random 实例（外部按 seed 构造，保证可复现）。
        pois: 候选 POI 列表（取真实 mock 数据）。
        restaurants: 候选餐厅列表（取真实 mock 数据）。

    Returns:
        合法 PlanBlueprint（mid nodes 1~5 个，累计停留 + hop 预算 ≤ 当日剩余）。
    """
    start_hour = rng.choice([10, 11, 12, 13])
    start_min = start_hour * 60
    duration_budget = _DAY_END_MIN - start_min - _HOP_BUDGET_MIN

    n_mid = rng.randint(1, 5)
    nodes: list[BlueprintNode] = []
    accumulated = 0

    for _ in range(n_mid):
        # 当前节点的合理区间（POI / 餐厅）
        is_poi = rng.random() < 0.5 and pois
        if is_poi:
            base_lo, base_hi = 30, 180
        else:
            base_lo, base_hi = 40, 120

        # 剩余预算不足下限 → 整体提前结束（保证至少 1 节点已生成；空 nodes Pydantic 会拒）
        remaining = duration_budget - accumulated
        if remaining < base_lo:
            if not nodes:
                # 还没生成节点 → 用最小值兜底，保证 PlanBlueprint(min_length=1) 满足
                duration = max(10, remaining if remaining > 0 else base_lo)
            else:
                break
        else:
            duration = rng.randint(base_lo, min(base_hi, remaining))

        if is_poi:
            poi = rng.choice(pois)
            nodes.append(
                BlueprintNode(
                    kind=rng.choice(["主活动", "自由", "夜场"]),
                    target_kind=BlueprintTargetKind.POI,
                    target_id=poi.id,
                    duration_min=duration,
                )
            )
            accumulated += duration
        else:
            # 真因修复批 item 1 追加：优先从"最坏情形（槽吸附吸到该店最晚槽）
            # 仍在预算内"的餐厅里选——避免抽到一家早已过号的晚市专门店
            # （如只有 17:00-18:00 槽的店），却在上午/中午时段被安排，
            # worst-case 直接顶穿当日上限（见本函数 docstring 追加说明）。
            # 全都超预算时（预算已经很紧）才退回全量随机，接受残余风险——
            # 此时下一轮 `remaining < base_lo` 通常已经会让生成提前结束。
            safe_restaurants = [
                cand
                for cand in restaurants
                if _restaurant_worst_case_end(
                    cand, start_min=start_min, accumulated=accumulated, duration=duration
                )
                <= duration_budget
            ]
            r = rng.choice(safe_restaurants) if safe_restaurants else rng.choice(restaurants)
            nodes.append(
                BlueprintNode(
                    kind=rng.choice(["用餐", "夜宵", "下午茶"]),
                    target_kind=BlueprintTargetKind.RESTAURANT,
                    target_id=r.id,
                    duration_min=duration,
                )
            )
            accumulated = _restaurant_worst_case_end(
                r, start_min=start_min, accumulated=accumulated, duration=duration
            )

    # 30% 概率：把第二个节点的 target 复制为第一个 → 连续同 target_id → in_place hop
    if len(nodes) >= 2 and rng.random() < 0.3:
        nodes[1] = nodes[1].model_copy(
            update={
                "target_id": nodes[0].target_id,
                "target_kind": nodes[0].target_kind,
            }
        )

    return PlanBlueprint(
        nodes=nodes,
        preferred_start_time=f"{start_hour:02d}:00",
        rationale=f"fuzz random（{len(nodes)} mid nodes, start={start_hour}）",
    )


# ============================================================
# 8 条不变量断言
# ============================================================


def _assert_invariants(itin: Itinerary, blueprint: PlanBlueprint) -> None:
    """edge_v1 八条不变量（design.md Correctness Properties 1-8）。

    任一不成立即直接 `assert` 失败，pytest 报告会含失败的种子（parametrize id）
    与失败的具体不变量名称，便于后续复现 / 追根因。
    """
    nodes = itin.nodes
    hops = itin.hops

    # I1：hops 长度 = nodes - 1
    assert len(hops) == len(nodes) - 1, (
        f"[I1] hops 长度 {len(hops)} ≠ nodes-1 = {len(nodes) - 1}"
    )

    # I2：首尾节点必为 home
    assert nodes[0].target_kind == "home", (
        f"[I2] nodes[0].target_kind={nodes[0].target_kind!r} 应为 'home'"
    )
    assert nodes[-1].target_kind == "home", (
        f"[I2] nodes[-1].target_kind={nodes[-1].target_kind!r} 应为 'home'"
    )

    # I3：首尾节点 target_id == "home"
    assert nodes[0].target_id == "home", (
        f"[I3] nodes[0].target_id={nodes[0].target_id!r} 应为 'home'"
    )
    assert nodes[-1].target_id == "home", (
        f"[I3] nodes[-1].target_id={nodes[-1].target_id!r} 应为 'home'"
    )

    # I4：首尾节点 duration_min == 0
    assert nodes[0].duration_min == 0, (
        f"[I4] nodes[0].duration_min={nodes[0].duration_min} 应为 0"
    )
    assert nodes[-1].duration_min == 0, (
        f"[I4] nodes[-1].duration_min={nodes[-1].duration_min} 应为 0"
    )

    # I5：hop.start_time 必须等于 from_node 的结束时刻
    for i, hop in enumerate(hops):
        from_node = nodes[i]
        expected_hop_start = (
            _parse_hhmm(from_node.start_time) + from_node.duration_min
        )
        assert _parse_hhmm(hop.start_time) == expected_hop_start, (
            f"[I5] hops[{i}].start_time={hop.start_time} 应等于 from_node "
            f"{from_node.node_id}({from_node.start_time}+{from_node.duration_min}min)"
        )

    # I6：to_node.start_time >= hop.start + hop.minutes + hop.buffer_min
    # （真因修复批 item 1 有意识放宽：曾是严格 ==，隐含"到达即入座、零等待"
    # 假设。但本批新增的餐厅预约槽吸附（自然到达对不上 mock 离散槽位时，
    # 排定时刻推迟到最近可用槽）与既有的 not_before_start 钉窗机制
    # （ADR-0009 决策 2·乙）都会合法地把 to_node 排得比"hop 结束+buffer"更晚
    # ——多出的分钟是餐前等待 slack，前端渲染为"自由休息"块，不是 bug。
    # fuzz 用真实 mock 餐厅数据 + 随机时刻，命中槽吸附是预期内的常态（本文件
    # 目的正是覆盖这类随机 edge case），不是应该被这条不变量拦下的异常。
    # critic 侧 check_temporal_alignment 用的就是同一条 `>=` 判据，本不变量
    # 与其对齐。
    for i, hop in enumerate(hops):
        to_node = nodes[i + 1]
        expected_to_start = (
            _parse_hhmm(hop.start_time) + hop.minutes + hop.buffer_min
        )
        assert _parse_hhmm(to_node.start_time) >= expected_to_start, (
            f"[I6] nodes[{i + 1}].start_time={to_node.start_time} 应不早于 "
            f"hop {hop.hop_id}({hop.start_time}+{hop.minutes}min+buf{hop.buffer_min})"
        )

    # I7：in_place hop minutes=0 且 from/to 同 target_id
    for i, hop in enumerate(hops):
        if hop.path_type == "in_place":
            assert hop.minutes == 0, (
                f"[I7] hops[{i}] path_type=in_place 但 minutes={hop.minutes} ≠ 0"
            )
            assert nodes[i].target_id == nodes[i + 1].target_id, (
                f"[I7] hops[{i}] in_place 但 from={nodes[i].target_id!r} "
                f"≠ to={nodes[i + 1].target_id!r}"
            )

    # I8：total_minutes 自洽（last_node.start - first_hop.start）
    expected_total = (
        _parse_hhmm(nodes[-1].start_time) - _parse_hhmm(hops[0].start_time)
    )
    assert itin.total_minutes == expected_total, (
        f"[I8] total_minutes={itin.total_minutes} 应等于 "
        f"last_node({nodes[-1].start_time}) - first_hop({hops[0].start_time}) "
        f"= {expected_total}"
    )

    # 兼容：blueprint mid nodes 个数 = nodes - 2（首尾 home）
    assert len(nodes) - 2 == len(blueprint.nodes), (
        f"mid nodes 数量 {len(nodes) - 2} ≠ blueprint mid nodes {len(blueprint.nodes)}"
    )


# ============================================================
# Fuzz 主测试：10 个种子各跑一次
# ============================================================


@pytest.mark.parametrize("seed", list(range(10)))
def test_fuzz_invariants_hold(
    seed: int,
    profile: UserProfile,
    pois: list[Poi],
    restaurants: list[Restaurant],
) -> None:
    """以 `seed` 驱动随机 blueprint，跑 assemble，断言 8 条不变量。

    任一不变量失败则 pytest 报失败；测试 id 自带 seed，按
    `pytest -k "test_fuzz_invariants_hold[3]"` 即可复现单种子。
    """
    rng = random.Random(seed)
    intent = _make_minimal_intent()
    blueprint = _make_random_blueprint(rng, pois, restaurants)

    itinerary = assemble_from_blueprint(intent, blueprint, profile)
    _assert_invariants(itinerary, blueprint)
