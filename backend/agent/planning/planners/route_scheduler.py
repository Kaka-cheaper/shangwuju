"""agent.planning.planners.route_scheduler —— ADR-0010 D-2：窗感知调度器。

【定位（ADR-0010「三层解耦架构」的②层，决策 5 原话"真正的硬核"）】

给定「已选活动集合（`Visit` 列表，来自 D-1 `activity_pool.py`）+ 出发时刻 +
在外总预算」，求「顺序 + 各活动开始时刻」使**全部约束同时满足**——这是一个
小型 CSP + 迷你 TSP（活动数 ≤5-6，ADR 决策 11 的"延迟安全上限"），不是按
时间游标顺序 append（那样餐厅会被排到饭点窗外）。可行性判定完全在本层，
**不掺 utility**——ADR 决策 5 原话"可行性从 `_utility` 剥离，utility 退成
纯打分"；插入哪个候选、选哪个子集是 D-4 贪心插入构造的事，本层只回答
"这一组活动、这个顺序，排得开吗、排成什么样"。

【公开接口】

- `ScheduledVisit`：一次已排定的访问（`visit` + `start_min` + `natural_arrival_min`）。
  `start_min - natural_arrival_min` 即 D-4 换算 `BlueprintNode.not_before_start`
  所需的"排定晚于自然到达"的差额（slack）。
- `RouteSchedule`：一整条可行排程（`scheduled` 有序元组 + `depart_min` +
  `return_arrival_min`），派生 `total_minutes` / `total_slack_min`。
- `schedule_route(visits, *, depart_min, budget_min, commute_fn, home_id="home")`
  → `RouteSchedule | None`：核心入口，给定**恰好这一组**活动求排程或宣告不可行。
- `try_insert(existing, new_visit, *, ...)` → `RouteSchedule | None`：D-4 贪心
  插入每一步问"把它加进来还排得开吗"的便捷封装。
- `try_order_fixed(order, *, ...)` → `RouteSchedule | None`：换菜位置保持
  （ADR-0013 F-1 换菜 bug 修复）需要的"钉死这个顺序，排不排得开"原语——
  `_try_order` 的薄公开封装，不枚举、不重排，与 `schedule_route`/`try_insert`
  的"允许重排"语义互补而非替代。见其自身 docstring。

【纯函数契约（ADR 决策 5 "可孤立测"）】

不做 I/O、不加载 mock 数据、不调 LLM——通勤查询通过 `commute_fn: (from_id,
to_id) -> minutes` 由调用方注入（测试注入假表；生产 D-4 注入
`agent.planning.commute.lookup_hop.lookup_hop` 的包装，绑定 transport_pref/
user_profile 后收窄成这个二元签名）。

【调研留痕：本步自行拍板、值得读者知道的判断点】

1. **顺序搜索用全排列枚举，不用「插入位搜索」**——与 ADR 决策 5 原文字面
   有出入，特此记录以防后来者对着 ADR 摘抄本文件时看不懂差异：ADR 决策 5
   的"插入位搜索（保持既有顺序，试各插入位；非全排列枚举）"描述的是 **D-4**
   贪心构造逐步插入新活动时的效率手法（保持已确定的既有顺序不动，只试新
   活动插入 k+1 个位置之一，避免每插入一个新活动就对整条已定路线重新做
   O(n!) 搜索）。本模块（D-2）在**给定"这一组"活动**时求可行顺序是另一个
   更小的子问题——ADR 决策 11 把活动数上限钉在 ≤5-6，全排列至多 120/720
   种，每种线性推时间轴（毫秒级），全排列枚举既更简单又不牺牲最优性
   （插入位搜索是局部启发式，可能漏掉全排列才能找到的可行解）。`try_insert`
   同理直接"新集合重跑全量调度"而非真正的插入位搜索——量级下这已足够快，
   真正的插入位优化留给 D-4/D-5 按需引入（过早优化）。
2. **可行顺序间怎么选（utility 之外的必要 tie-break）**：全排列会产出多个
   同样"可行"的顺序，必须选一个返回，但这一步**不是**在算 utility（那是
   D-4 的事）——是纯粹的"时间性"偏好，标准是"这条路线看起来不松垮"：
   第一关键字 **总 slack 最小**（`total_slack_min`，见下），第二关键字
   **结束最早**（`return_arrival_min`，即路线整体越紧凑越好）。选 slack
   而非"随便选第一个可行的"，是因为 ADR 决策 5"slack 摆放策略"要求 slack
   只在窗约束逼出来时才出现、且应该是必要最小——多个可行顺序里如果有一个
   总等待明显更少，没理由选等待更多的那个（"让路线不松垮"）。**这不是
   utility**：它不比较活动质量/多样性/预算，只比较同一组活动在不同顺序下
   的时间几何——D-4 的边际分选择建立在本层已经"排得开、且排得不松垮"的
   前提之上，两层判断维度正交，不重复。
3. **餐厅槽网格 snap 用"通用半点网格"而非读 `visit.entity.reservation_slots`
   的具体槽位**：ADR 决策 5 原话"半点粒度 :00/:30——mock reservation_slots
   全是这个粒度"，本模块因此直接实现"向上取整到最近半点"（`_snap_to_slot_grid`），
   不去读 `Visit.entity`（真实 `Restaurant.reservation_slots` 列表）。两个
   理由：① 保持纯函数边界——读 `entity` 里的业务字段是"消费真实实体"，
   越过了 D-2"只认活动的窗+时长，不知道也不关心是哪个具体餐厅"的定位
   （ADR 决策 5 语境这本是给通用 TOPTW 求解器定的界，D-2 虽是甲的简化实现，
   仍应守住这条线）；② 与 `agent.graph.nodes.execute_finalize._ceil_to_half_hour`
   **同构**（同为"向上取整到最近 30 分钟刻度"），ADR 决策 5 原话点名要防的
   正是"与 execute 的 `_ceil_to_half_hour` 预约时刻错位（重蹈 C-2 修过的
   自洽 bug）"——本模块特意不 import 那个函数（`execute_finalize` 在
   `agent/graph/nodes/`，是比 `agent/planning/` 更上层的编排层，反向 import
   会颠倒依赖方向），而是独立实现同样的取整算法，两处数值行为保持一致
   （`_snap_to_slot_grid(17:04)==17:30`、`(17:30)==17:30`、`(17:31)==18:00`，
   与 `_ceil_to_half_hour` 逐一对齐，测试见 `test_route_scheduler.py`）。
   若未来两处 grid 粒度需要联动修改，这是需要读者知道的耦合点。
4. **跨活动窗选择"全局最早可行"，不依赖 `Visit.windows` 的列表顺序**：一个
   活动可能有多个不相交窗（如餐厅午/晚/夜宵），本模块对每个窗独立算候选
   开始时刻、取窗内可行的那些里的全局最小值——不是"数组里第一个可行的窗"。
   这样即使调用方传入乱序的 windows（如先晚窗后午窗），结果仍是"物理上
   最早能吃上的那一顿"，不受 D-1 构造顺序的隐式约定牵连（防止未来 D-1
   调整窗构造顺序时静默改变 D-2 行为）。
5. **`home_id` 默认值 `"home"`**：与 `agent.planning.commute.lookup_hop` 的
   既有约定一致（`from_id == "home"` 走 `user_profile.home_location`），保留
   为可覆盖参数只是为了不在测试里强耦合这个字符串字面量。
6. **≤5-6 活动的上限是调用方（D-4/critic）的约定，本模块不做保护性拒绝**：
   全排列枚举是 `O(n!)`，本模块不对 `len(visits)` 设上限校验——超出 ADR
   设计范围调用方应先做子集筛选（D-4 的活），此处不重复防御，遵循
   `activity_pool.py` 已确立的"记录前提、不越权校验"分层习惯。

不负责：
- 子集选择 / 贪心插入构造（D-4）。
- utility 打分（D-1 `route_score`/`marginal_score`；本模块的排程结果是它们
  的必要前提，不是它们的替代）。
- `not_before_start` 字符串格式化 / `BlueprintNode` 构造（D-4，用
  `ScheduledVisit.start_min` 与 `.natural_arrival_min` 的差值即可换算）。
- critic 复检兜底（既有 `agent.planning.critic`）。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .activity_pool import Visit

# ============================================================
# 常量
# ============================================================

MAX_DAY_MIN: int = 23 * 60 + 59
"""排定时刻不可跨越的当日上限（23:59，分钟坐标）。与 `assemble_blueprint._fmt_hhmm`
的 clamp 行为、`Itinerary` 系统性不支持跨日时间戳的契约对齐（D-1 `TimeWindow`
docstring 已点名："窗坐标允许 >24h 只是构造层的表示能力，D-2 调度器必须把
路线整体约束在当日内"）。本模块对每个排定 start 与最终回家到达时刻都校验
这条线，越界即此顺序不可行。"""

RESERVATION_SLOT_GRID_MIN: int = 30
"""餐厅预约槽网格粒度（分钟）。ADR-0010 决策 5："半点粒度 :00/:30——mock
reservation_slots 全是这个粒度"。"""

TRANSFER_BUFFER_MIN: int = 5
"""非首跳的过渡缓冲分钟数，与 `assemble_blueprint.py`「buffer = 0 if i == 0
else 5」逐字对齐——两处若要联动调整须同步改（D-2 与 assemble 各自独立实现，
不共享常量模块，是刻意的层间独立而非遗漏，见模块 docstring 判断点 3 的
同类考量：assemble 在更上层，D-2 不应反向依赖它）。"""


CommuteFn = Callable[[str, str], int]
"""通勤查询签名：`(from_id, to_id) -> minutes`。调用方注入——生产环境是
`lookup_hop` 绑定 transport_pref/user_profile 后的收窄包装。"""


# ============================================================
# 槽网格 snap（判断点 3）
# ============================================================


def _snap_to_slot_grid(minute: int) -> int:
    """向上取整到最近的 `RESERVATION_SLOT_GRID_MIN` 刻度。

    与 `agent.graph.nodes.execute_finalize._ceil_to_half_hour` 同构（本模块
    独立实现的原因见模块 docstring 判断点 3）：`_snap_to_slot_grid(17:04)==
    17:30`、恰好在网格上则原样返回、`(17:31)==18:00`。
    """
    grid = RESERVATION_SLOT_GRID_MIN
    return -(-minute // grid) * grid  # 向上整除（ceil division）


# ============================================================
# 单活动最早可行开始时刻（约束 1 + 5：窗内 + 餐厅 snap）
# ============================================================


def _earliest_feasible_start(visit: Visit, natural_arrival_min: int) -> Optional[int]:
    """给定"自然到达时刻"，求这个活动在它任一候选窗内的最早可行开始时刻。

    - `windows` 为空 → None（该活动物理不可排，D-1 module docstring 已声明
      这个语义："collapse 到空列表，表示这一时段物理不可行"）。
    - 每个窗独立算候选（`max(natural_arrival, 窗起点)`，餐厅再 snap 到槽
      网格），snap 后仍需落在窗内（`<= 窗尾`）才算这个窗可行；跨窗取全局
      最小（判断点 4：不依赖 windows 的列表顺序，选物理上最早能排上的）。
    - 只对 `kind == "restaurant"` snap（约束 5 只钉餐厅；POI 无预约槽概念）。
    """
    if not visit.windows:
        return None

    is_restaurant = visit.kind == "restaurant"
    feasible_candidates: list[int] = []
    for window in visit.windows:
        candidate = max(natural_arrival_min, window.start_min)
        if is_restaurant:
            candidate = _snap_to_slot_grid(candidate)
        if candidate <= window.end_min:
            feasible_candidates.append(candidate)

    if not feasible_candidates:
        return None
    return min(feasible_candidates)


# ============================================================
# 排程结果值对象
# ============================================================


@dataclass(frozen=True)
class ScheduledVisit:
    """一次已排定的访问：活动本身 + 排定开始时刻 + 排定前的自然到达时刻。

    `start_min - natural_arrival_min`（见 `slack_min`）就是 D-4 构造
    `BlueprintNode.not_before_start` 时要表达的"餐前等待/留白"差额——排定
    晚于自然到达是合法机制（ADR 决策 5"slack 摆放策略"），本值对象把两个
    时刻都暴露出来，换算交给 D-4（D-2 不产 `BlueprintNode`，见模块 docstring
    「不负责」）。
    """

    visit: Visit
    start_min: int
    natural_arrival_min: int

    @property
    def end_min(self) -> int:
        return self.start_min + self.visit.duration_min

    @property
    def slack_min(self) -> int:
        """排定开始 晚于 自然到达 的差额（≥0；约束 2/6 保证不会出现负值——
        `_earliest_feasible_start` 的候选恒 `>= natural_arrival_min`）。"""
        return self.start_min - self.natural_arrival_min


@dataclass(frozen=True)
class RouteSchedule:
    """一整条可行排程：有序的 `ScheduledVisit` + 出发/到家时刻。"""

    scheduled: tuple[ScheduledVisit, ...]
    depart_min: int
    return_arrival_min: int

    @property
    def total_minutes(self) -> int:
        """在外总时长：出发到到家（含全部通勤/活动/slack），约束 3 的判据。"""
        return self.return_arrival_min - self.depart_min

    @property
    def total_slack_min(self) -> int:
        """全路线 slack 总和——顺序选择策略（判断点 2）的第一关键字。"""
        return sum(sv.slack_min for sv in self.scheduled)


# ============================================================
# 单一顺序的时间轴推演（约束 1-4 的落地）
# ============================================================


def _try_order(
    order: Sequence[Visit],
    *,
    depart_min: int,
    budget_min: int,
    commute_fn: CommuteFn,
    home_id: str,
) -> Optional[RouteSchedule]:
    """给定一个具体顺序，推演时间轴；任一约束不满足即返回 None。

    约束落地对应关系：
    - 约束 1（窗内）+ 约束 5（snap）：委托 `_earliest_feasible_start`。
    - 约束 2（时间轴自洽）：`natural_arrival = 上一活动结束 + 通勤 + buffer`
      （首跳 buffer=0，对齐 `assemble_from_blueprint` 的规则）；排定 start
      恒 `>= natural_arrival`（`_earliest_feasible_start` 的 `max(...)` 保证），
      不会早到。
    - 约束 4（不跨午夜）：每个排定 start、每个活动结束、以及最终回家到达，
      任一 `> MAX_DAY_MIN` 即此顺序不可行。
    - 约束 3（总时长 ≤ budget）：`return_arrival_min - depart_min > budget_min`
      即不可行，含回家通勤（约束 6）。
    - 约束 6（slack 取最早可行，不人为拖晚）：`_earliest_feasible_start` 本身
      就是"取所有可行候选里的最小值"，没有额外拖延逻辑。
    """
    cursor = depart_min
    prev_id = home_id
    scheduled: list[ScheduledVisit] = []

    for i, visit in enumerate(order):
        commute = commute_fn(prev_id, visit.target_id)
        buffer_min = 0 if i == 0 else TRANSFER_BUFFER_MIN
        natural_arrival = cursor + commute + buffer_min

        start = _earliest_feasible_start(visit, natural_arrival)
        if start is None or start > MAX_DAY_MIN:
            return None

        end = start + visit.duration_min
        if end > MAX_DAY_MIN:
            return None

        scheduled.append(
            ScheduledVisit(
                visit=visit, start_min=start, natural_arrival_min=natural_arrival
            )
        )
        cursor = end
        prev_id = visit.target_id

    if scheduled:
        return_arrival = cursor + commute_fn(prev_id, home_id)
    else:
        return_arrival = cursor  # 空路线：从未离开，无需查回家通勤

    if return_arrival > MAX_DAY_MIN:
        return None
    if return_arrival - depart_min > budget_min:
        return None

    return RouteSchedule(
        scheduled=tuple(scheduled),
        depart_min=depart_min,
        return_arrival_min=return_arrival,
    )


# ============================================================
# 主入口：全排列枚举 + 顺序选择策略（判断点 1 + 2）
# ============================================================


def schedule_route(
    visits: Sequence[Visit],
    *,
    depart_min: int,
    budget_min: int,
    commute_fn: CommuteFn,
    home_id: str = "home",
) -> Optional[RouteSchedule]:
    """给定「这一组」活动，求可行的「顺序 + 各自开始时刻」，或 None。

    Args:
        visits: 已选活动集合（子集选择是 D-4 的事，本函数只排**恰好这些**）。
        depart_min: 出发时刻（分钟坐标）。
        budget_min: 在外总时长上限（分钟）。
        commute_fn: `(from_id, to_id) -> minutes`；调用方注入。
        home_id: 出发/返回的地点 id，默认 `"home"`（与 `lookup_hop` 约定一致）。

    Returns:
        `visits` 为空 → 零活动的平凡可行排程（`return_arrival_min == depart_min`）。
        否则：全部顺序枚举后仍无一可行 → None；有可行顺序 → 按"总 slack 最小，
        其次结束最早"（判断点 2）挑一个返回。
    """
    best: Optional[RouteSchedule] = None
    best_key: Optional[tuple[int, int]] = None

    for order in itertools.permutations(visits):
        candidate = _try_order(
            order,
            depart_min=depart_min,
            budget_min=budget_min,
            commute_fn=commute_fn,
            home_id=home_id,
        )
        if candidate is None:
            continue
        key = (candidate.total_slack_min, candidate.return_arrival_min)
        if best_key is None or key < best_key:
            best = candidate
            best_key = key

    return best


def try_insert(
    existing: Sequence[Visit],
    new_visit: Visit,
    *,
    depart_min: int,
    budget_min: int,
    commute_fn: CommuteFn,
    home_id: str = "home",
) -> Optional[RouteSchedule]:
    """D-4 贪心插入的可行性试探："把 `new_visit` 加进来还排得开吗？"

    实现为「新集合重跑调度」（判断点 1：≤5-6 活动规模下 O(n!) 全排列已经是
    毫秒级，真正的"插入位搜索"是留给更大规模才需要的优化，此处过早优化无
    意义）——不保留 `existing` 原有顺序，允许整体重新排列（这本来就是全排列
    枚举语义的自然结果，不是额外让步）。
    """
    return schedule_route(
        list(existing) + [new_visit],
        depart_min=depart_min,
        budget_min=budget_min,
        commute_fn=commute_fn,
        home_id=home_id,
    )


def try_order_fixed(
    order: Sequence[Visit],
    *,
    depart_min: int,
    budget_min: int,
    commute_fn: CommuteFn,
    home_id: str = "home",
) -> Optional[RouteSchedule]:
    """给定**恰好这个顺序**（不枚举、不重排），推演时间轴求可行排程或 None。

    【为什么需要这个，`schedule_route`/`try_insert` 为什么不够】

    换菜场景（`route_builder.repair_route` 的 `preserve_position` 形参）需要
    "替补插回原节点的序位、其余保留节点顺序原封不动"这一具体顺序的可行性，
    而 `schedule_route` 的全排列枚举语义（判断点 1）是"给定一组活动，允许
    重排"——两者是不同问题："这一组活动排不排得开"（本模块主入口回答的）
    vs "这一组活动、钉死这个顺序，排不排得开"。`try_insert` 同样是"重排语义"
    （其 docstring 原话"不保留 existing 原有顺序，允许整体重新排列"），换菜
    场景不能拿它当"定序检查"用，否则正是 bug 的根因（详见 `route_builder.
    repair_route` 的 `preserve_position` 参数 docstring）。

    这不是新算法——`_try_order` 本就是 `schedule_route` 全排列枚举内部逐个
    顺序调用的那个原语（"给定一个具体顺序，推演时间轴"，见其 docstring），
    只是原来只在模块内部私有使用。本函数是它的薄公开封装（同一实现，不
    拷贝/不改一行时间轴推演逻辑），供换菜场景需要"钉死顺序求可行"时复用，
    不必新写第二份定序调度逻辑。

    Returns:
        `order` 为空 → 零活动的平凡可行排程（与 `schedule_route([])` 同语义）。
        任一约束不满足（窗外/跨日/超预算）→ None。可行 → 恰好这一个顺序的
        `RouteSchedule`（没有"多个可行顺序选一个"的 tie-break 问题，因为
        顺序已经钉死，见判断点 2 只在"允许重排"时才需要 tie-break）。
    """
    return _try_order(
        order,
        depart_min=depart_min,
        budget_min=budget_min,
        commute_fn=commute_fn,
        home_id=home_id,
    )
