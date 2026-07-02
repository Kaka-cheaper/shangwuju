"""agent.planning.planners.route_shake —— ADR-0010 D-6：shake 存废实测用的最小扰动。

【定位（实验性，刻意不接入 plan_hybrid）】

ADR-0010 决策 7 原话："先只做贪心插入构造；再实测『贪心 vs 贪心+shake』在 S1-S8
上的结构差异——shake 明显让某场景更对才留，说不出哪更对就砍"。`route_builder.py`
（D-4）已经落地纯贪心插入构造，**不含**任何扰动/局部搜索——这正是 ADR 背景诊断
的架构审查候选 #8 教训（旧 `ils_planner._perturb`/`_local_search` provably inert，
150 次迭代 0 次命中）要求"先证明有用再留"的直接回应：本模块的产出**只喂
`backend/scripts/measure_shake.py`**，不被 `plan_hybrid`/`route_builder.build_route`
调用——是否接线由该脚本产出的 S1-S8 实测数据决定（D-6 任务原文），不是本模块自己
拍板。

【算法（prior art：标准 ILS shake，非本模块自创）】

[Vansteenwegen et al. 2009 · ILS for TOPTW] 与 [Gunawan et al. 2019 · Adjustment
ILS for multi-objective TOPTW] 的 shake/perturbation 结构：**扰动**（从当前解里
移除一个元素制造"坑"）+ **局部搜索**（贪心重新填坑，可能填回原元素、也可能填入
更优的替代组合）+ **接受准则**（新解更优才接受，否则保留原解）。本模块直接复用
`route_builder._greedy_fill_emergent`（D-4 已实现的贪心插入循环）作局部搜索算子
——不重新发明一套插入逻辑，"移除后重填"与"从空路线开始贪心填"是同一个循环，
唯一差异是起点状态，复用既有实现更贴近 prior art 的"局部搜索"字面含义（用同一套
邻域算子，只是从扰动后的解重新收敛），也避免第二套插入逻辑与 D-4 那套产生行为
分叉。

Args/Returns 见 `shake_route` 与 `ShakeResult` docstring。

【调研留痕】

1. **接受准则用"严格更优才接受"（`new_score > current_score`），不用模拟退火/
   概率接受**：ADR 决策 7 原文与 D-6 任务原文都只要求"更优才接受"（局部最优
   逃逸靠**多轮独立随机移除**而非退火温度），标准 ILS（不是模拟退火）本就是
   这个接受准则——引入温度表会引入本任务不需要的额外超参数，且"是否保留 shake"
   这个决策本身依赖的是"能否找到更优解"这个简单事实，不依赖退火细节。
2. **每轮移除后，"被移除的活动自己"也重新加入候选池**：移除不代表"拉黑"——
   贪心重填应该能在权衡了所有候选（含原来那个）后，认为原来的选择依然最优
   （此时该轮等价于"重跑一次局部收敛"，score 不变，判定 `new_score > current`
   为 False 而拒绝，行为等价于"什么都没变"，这是正确的、非 bug）。
3. **K 轮的"当前解"是跨轮累积的**（不是每轮都从同一个初始解出发扰动）：标准
   ILS 的 "iterated" 语义——第 2 轮扰动的是第 1 轮被接受后的解（如果被接受），
   不是每次都从贪心初始解重来。这样才有"多轮持续爬坡"的意义，而不是 K 次独立
   的单步扰动实验。
4. **固定 seed（`random.Random(seed)` 局部实例，不用全局 `random` 模块状态）**：
   D-6 任务原文"固定 seed 可复现"——用局部实例而非 `random.seed(...)` 全局调用，
   避免污染调用方（`measure_shake.py` 在同一进程里跑多个场景时）的全局随机状态。

不负责：
- 是否接入 `plan_hybrid`/`build_route`（D-6 任务原文明确"接线由数据决定"，本模块
  只提供机制）。
- 候选池构建（复用 `activity_pool.py`/`route_builder.py` 已有的 `build_route`
  内部同款构造，调用方——`measure_shake.py`——负责准备 `pool` 参数）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

from ..weights_llm import PlanningWeights
from .activity_pool import Visit, route_score
from .pace_budget import IntervalFillTargets
from .route_builder import _greedy_fill_emergent
from .route_scheduler import CommuteFn, RouteSchedule, schedule_route


@dataclass(frozen=True)
class ShakeResult:
    """`shake_route` 的产出：K 轮扰动后收敛的最优解 + 诊断留痕。

    - `schedule` / `visits`：K 轮后被接受的最新（=最优，接受准则单调不降）解。
    - `accepted_rounds`：K 轮里"扰动后重填 score 严格更高、被接受"的轮数——
      0 即"shake 全程没找到过更优解"，是 D-6 决策规则的核心证据。
    - `score_before` / `score_after`：起点 / 终点 `route_score`（两者之差是
      "shake 到底值不值"的量化证据；结构性证据——组成变了没有——看 `visits`
      与起点 `visits` 的集合差）。
    - `history`：每一轮"扰动+重填"后的 score（不论是否被接受）——供
      `measure_shake.py` 画收敛曲线/诊断"是不是前几轮就已经收敛"。
    """

    schedule: RouteSchedule
    visits: tuple[Visit, ...]
    accepted_rounds: int
    score_before: float
    score_after: float
    history: tuple[float, ...]


def shake_route(
    initial_visits: Sequence[Visit],
    initial_schedule: RouteSchedule,
    pool: Sequence[Visit],
    weights: PlanningWeights,
    *,
    depart_min: int,
    budget_min: int,
    commute_fn: CommuteFn,
    money_budget: float,
    targets: IntervalFillTargets,
    k: int = 20,
    seed: int = 42,
) -> ShakeResult:
    """K 轮「随机移除 1 个已选活动 → 贪心重填 → 更优才接受」（ADR-0010 决策 7）。

    Args:
        initial_visits: `build_route` 产出的已选活动集合（起点解）。
        initial_schedule: 与 `initial_visits` 对应的 `RouteSchedule`。
        pool: 完整候选池中**未被选中**的部分（`build_route` 内部 emergent_pool
            同款构造，调用方负责准备——见模块 docstring「不负责」）。
        weights / money_budget: 同 `route_score` 消费方式。
        depart_min / budget_min / commute_fn: 同 `route_scheduler.schedule_route`。
        targets: 传给 `_greedy_fill_emergent` 的区间填充参数（与起点解构造时
            用的必须是同一组，否则停止条件的"软目标"语义会漂移）。
        k: 扰动轮数（ADR-0010 D-6 任务原文"K 取 10-20"）。
        seed: 固定种子，保证可复现（判断点 4）。

    Returns:
        `ShakeResult`；`initial_visits` 为空（零活动路线）时直接原样返回
        （无"移除"这个操作可做，`accepted_rounds=0`）。
    """
    current_visits: list[Visit] = list(initial_visits)
    current_schedule = initial_schedule
    current_score = route_score(
        [sv.visit for sv in current_schedule.scheduled], weights, money_budget
    )
    score_before = current_score

    if not current_visits:
        return ShakeResult(
            schedule=current_schedule,
            visits=tuple(current_visits),
            accepted_rounds=0,
            score_before=score_before,
            score_after=current_score,
            history=(),
        )

    rng = random.Random(seed)
    available_pool: list[Visit] = list(pool)
    accepted = 0
    history: list[float] = []

    for _ in range(k):
        if not current_visits:
            break  # 防御性：正常不会发生（初始非空，接受准则不会把解清空到 0）

        remove_idx = rng.randrange(len(current_visits))
        removed = current_visits[remove_idx]
        remaining = current_visits[:remove_idx] + current_visits[remove_idx + 1 :]

        remaining_schedule = schedule_route(
            remaining, depart_min=depart_min, budget_min=budget_min, commute_fn=commute_fn
        )
        if remaining_schedule is None:
            # 防御性：remaining 是此前已知可行集合的真子集，理论恒可行；
            # 万一不可行（route_scheduler 语义变化），跳过本轮不崩溃。
            continue

        # 判断点 2：被移除的活动本身也重新参与候选竞争（不是被拉黑）。
        refill_pool = available_pool + [removed]
        candidate_visits = list(remaining)
        new_schedule = _greedy_fill_emergent(
            candidate_visits,
            remaining_schedule,
            refill_pool,
            depart_min=depart_min,
            budget_min=budget_min,
            commute_fn=commute_fn,
            weights=weights,
            money_budget=money_budget,
            targets=targets,
        )
        new_score = route_score(
            [sv.visit for sv in new_schedule.scheduled], weights, money_budget
        )
        history.append(new_score)

        if new_score > current_score:
            accepted += 1
            current_visits = candidate_visits
            current_schedule = new_schedule
            current_score = new_score
            selected_keys = {(v.kind, v.target_id) for v in current_visits}
            available_pool = [v for v in pool if (v.kind, v.target_id) not in selected_keys]
        # 否则拒绝：current_visits/current_schedule/current_score/available_pool
        # 原样保留，下一轮从「上一轮被接受的解」继续扰动（判断点 3）。

    return ShakeResult(
        schedule=current_schedule,
        visits=tuple(current_visits),
        accepted_rounds=accepted,
        score_before=score_before,
        score_after=current_score,
        history=tuple(history),
    )
