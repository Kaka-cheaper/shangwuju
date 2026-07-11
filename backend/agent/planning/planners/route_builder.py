"""agent.planning.planners.route_builder —— ADR-0010 D-4：锚定两段贪心插入构造。

【定位（ADR-0010「三层解耦架构」的④——D-2/D-3 之上的构造层，暂不接线）】

D-1（`activity_pool.py`）给出候选池 + `Visit` + utility；D-2（`route_scheduler.py`）
给出「一组活动能否排开、怎么排」；D-3（`pace_budget.py`）给出「这一路线该填多满、
留多少白」。三者都只回答各自的子问题，**没有一处决定「选哪些活动」**——这正是
本模块的职责：**贪心插入构造**，把 D-1/D-2/D-3 的产出串成「选子集 + 排定」的
完整答案（ADR 决策 3「锚定两段」+ 决策 7「先只做贪心插入构造」）。

本模块是 D-4 子步产出，**纯新增，不接线**：不改 `ils_planner.py` 的
`plan_hybrid`/`_greedy_init`/`CandidatePlan`，也不改 `rule_planner.py`/
`replan.py`——接线是 D-5 的事（ADR 原文点名 D-4/D-5 拆开是为了不让 C-4 已上库
的收敛守卫测试因大改动一次性变红）。

【构造两段（ADR 决策 3）】

1. **锚点段**：`pinned`（D-4 时只留列表接口；D-7 起由 `ils_planner.plan_hybrid`
   经 `_resolve_pinned` 灌入真实值——resolve 逻辑归 D-7，本模块仍只消费已经是
   `Visit` 形态的列表）逐个 `try_insert`；
   插不进——不静默丢弃，记入 `unmet_pinned`（advisory 数据，D-7 消费）。
   **软锚饭**（`dining_soft_anchored`，规则见该函数 docstring）命中且尚无餐厅
   入选时，从餐厅候选池挑 `base_score` 最高且 `try_insert` 可行的一家先放——
   防止后续被高分 POI 挤成配角（ADR 决策 3 原话）。
2. **涌现段**：循环对候选池剩余活动逐个 `try_insert` 试可行性、算 `marginal_score`，
   选边际分最高者插入；四条停止条件按「先做能立刻判的，后做需要先跑一轮候选搜索
   才能判的」顺序实现——细节见 `_greedy_fill_emergent` 的函数体注释与本文件
   模块级判断点 2。

【公开接口】

- `dining_soft_anchored(intent) -> bool`：判"饭是否被软锚"（ADR 决策 10）。
- `RouteBuildResult`：`build_route` 的产出值对象（排程 + 选中 visits + 未满足
  清单 + 诊断用 pace/targets）。
- `build_route(pois, restaurants, intent, weights, *, depart_min, commute_fn,
  semantic_scores=None, pinned=None) -> RouteBuildResult`：核心构造入口。
- `route_to_blueprint(schedule, intent, depart_min) -> PlanBlueprint`：
  `RouteSchedule` → `PlanBlueprint`（下游 `assemble_from_blueprint` 消费）。
- `make_commute_fn(user_profile) -> CommuteFn`：生产环境 `commute_fn` 的
  生产包装（绑定 transport_pref + `functools.lru_cache`）。
- `repair_route(previous_scheduled, poi_visits, rest_visits, weights, *, depart_min,
  budget_min, commute_fn, money_budget, blacklist_poi, blacklist_rest,
  blacklist_rest_time, preserve_position=None, reorder_flag_out=None)
  -> Optional[RouteSchedule]`：min-conflicts 风格有界修复
  （**ADR-0013 F-1 起共享 seam**：原 `ils_planner._repair_route`，模块私有，只
  服务 critic-to-solver 修复闭环；F-1 局部重解引擎 `planners/node_swap.py`
  需要同一"腾格→只补该格→不加塞"语义——`build_route` 的涌现填充循环在替换品
  更短时会加塞额外活动，破坏"只动一格"承诺，故不能复用 `build_route`——两个
  调用方现在共享同一实现，提升为本模块公开接口。**行为逐字节未变**（纯挪移
  + 去掉前导下划线，未改一行算法逻辑），`ils_planner.py` 原调用点与其既有
  回归测试（`test_planner_hybrid*.py`）不受影响。见其自身 docstring。
  **2026-07-10 追加**：`preserve_position`/`reorder_flag_out` 是换菜"位置
  丢失" bug 修复新增的换菜专用 opt-in 形参，`ils_planner.py` 不传，默认值
  下行为与追加前逐字节一致——见函数自身 docstring「`preserve_position`」节。

【调研留痕：本步自行拍板、值得读者知道的判断点】

1. **`build_route` 签名不含 money `budget` 参数——D-4 内部取，不新增形参**：
   `activity_pool.route_budget_penalty`/`route_score`/`marginal_score` 都需要一个
   花费预算，但 `IntentExtraction` 无预算字段；`activity_pool.py` 模块 docstring
   已把"从哪取"这个判断点显式留给 D-4。本模块选择在 `build_route` 内部调
   `data.loader.load_user_profile().default_budget`（`_default_budget()`），**不**
   把 `budget` 加进 `build_route` 的公开签名——ADR 任务原文给出的签名本就没有
   这个形参，且 `_RULE_ASSEMBLER_ADAPTER`（`agent/graph/nodes/replan.py`）已有
   先例：这一层（构造/装配层，区别于 D-1/D-2/D-3 的纯函数子层）本就直接调
   `load_user_profile()` 取 `transport_preference`/`party_size` 这类"运行时环境"
   数据，本函数取 `default_budget` 是同一性质的操作，不引入新的耦合方向。
2. **涌现段循环的停止条件实现顺序，与 ADR 任务原文「①②③④」的编号顺序不同，
   是刻意的，不是遗漏**：原文四条——① 无可行候选；② 活动数达上限；③ 总时长
   ≥ 下限且已超软目标；④ 最高边际分 ≤0 且下限已满足。这四条共同决定"是否停"，
   逻辑上是**析取**（任一为真就停）——按什么顺序检查不影响"停或不停"这个最终
   结果，只影响**效率**（先做便宜的检查，避免做一次完整的候选搜索又扔掉）与
   **哪个检查被最先短路**。① 和 ④ 都依赖"先跑一轮候选搜索"（① 是"搜索结果为
   空"，④ 是"搜索结果里最高边际分 ≤0"），② 和 ③ 只看"已选活动的当前状态"、
   完全不需要搜索。本实现因此按**先②后③，再做搜索，再①后④**的顺序求值——
   累计效果与原文顺序完全等价（同样的输入下"停"与"继续"的判断结果不变），
   只是把可以提前短路的检查提到搜索之前，避免"明知已经该停了，还要先扫一遍
   候选池才发现"的浪费。
3. **软锚饭：若 pinned 已经选中一家餐厅，跳过软锚挑选**：ADR 决策 3 说软锚是
   为了"防止饭被高分 POI 挤成配角"——如果锚点段已经通过 pinned 钉入了一家
   餐厅，"饭有没有"这件事已经被满足，再挑一家would 是画蛇添足（且会不当占用
   活动数上限）。故软锚只在 `not any(v.kind == "restaurant" for v in selected)`
   时触发，`selected` 取锚点段结束时**真正插入成功**的集合（pinned 插不进的
   不算"已满足"，仍应触发软锚兜底）。
4. **`RouteBuildResult.visits` 是构造（插入）顺序，不是排定（时间）顺序**：
   `schedule.scheduled` 已经是**时间序**（`route_scheduler.schedule_route` 的
   契约），`visits` 额外暴露**插入序**（pinned → 软锚 → 涌现依次插入的顺序）——
   两者服务不同的断言："软锚饭先于涌现活动被放"这类"构造决策顺序"的断言要
   看插入序（时间序上饭可能被排在中后段，那是 D-2 flow tie-break 的事，与
   "构造时先确定要不要饭"是两回事，混用会让测试意图含混）。
5. **餐厅/POI 去重按 `(kind, target_id)`，不按对象身份**：涌现候选池在锚点段
   结束后会剔除已经出现在 `selected` 里的 `(kind, target_id)`——防止"同一个
   实际地点"被 pinned 钉入后又在涌现段被当成不同候选重复插入第二次（pinned
   来自 D-7 的结构化输入，candidate pool 来自搜索结果，两者引用的可能是不同
   `Visit` 对象但指向同一实体）。
6. **`route_to_blueprint` 的 `kind` 标签复用 `node_decider.KIND_MAIN`/
   `KIND_DINING`（"主活动"/"用餐"），不按菜系/时段细分"早茶"/"夜宵"**：
   `BlueprintNode.kind` 允许任意自由文本（"早茶"/"夜宵"/"自由" 等），但现有
   `rule_planner._assemble_itinerary` 对所有餐厅节点统一打"用餐"标签（不管
   茶点类/正餐类/夜宵），本模块延续这一既有词汇口径而非自创一套更细的标签——
   细分是叙事层的活（ADR「边界」节点名"精细叙事弧归 LLM"），本步只负责
   `target_kind` 对不对、时长对不对、时刻对不对。
7. **`not_before_start` 施加于所有 `slack_min > 0` 的节点，不限餐厅**：
   `assemble_from_blueprint` 的 `not_before_start` 钉窗机制（ADR-0009 决策 2·乙）
   本就是通用节点级机制，不是餐厅专属——只要调度器把某节点排定晚于其自然
   到达时刻（`ScheduledVisit.slack_min > 0`，可能是 POI 也可能是餐厅），把
   这个"排定时刻"钉成 `not_before_start` 就能让 assemble 重算出同一个时刻
   （证明见下条判断点 8）。
8. **`fmt` 选 `critic._rules.helpers.fmt_hhmm`（clamp 版），不选
   `blueprint.assemble_blueprint._fmt_hhmm`（mod-24 版）**：两者在 D-2 保证的
   定义域内（一切排定分钟数 ≤ `route_scheduler.MAX_DAY_MIN` = 23*60+59）
   **数值上完全等价**（`min(x, 1439) == x % 1440` 当 `0 <= x <= 1439`），选谁
   不影响任何已算出的正确排程被格式化后的字符串。选 `fmt_hhmm` 而非
   `_fmt_hhmm` 出于两点：① 其 clamp 上界字面就是 `MAX_DAY_MIN` 同一个"当日
   上限 23:59"概念，与 D-2 的防跨日纪律同源，出现算错（万一未来有 bug 让
   超界分钟数流入）时**饱和到边界而非静默 wrap 到次日同一钟点**，更容易在
   人工核对时被发现是"顶到头了"而非误读成合法时刻；②
   `activity_pool.py`（D-1，本模块的姊妹层）已有先例直接从 `critic._rules.
   helpers` 取用（`_BUSINESS_HOURS_RE`），本模块保持同一耦合方向（`planners`
   → `critic._rules.helpers` 的工具函数），而非反向引用 `blueprint` 层的
   私有名 `_fmt_hhmm`（本就带下划线，非公开接口，也是更下游的兄弟层）。
9. **`route_to_blueprint` 要求 `schedule.scheduled` 非空**：`PlanBlueprint.nodes`
   的 Pydantic 约束是 `min_length=1`，零活动路线（理论上"候选池整体为空/全部
   不可行"时才会发生）无法表示成合法蓝图——这属于 ADR 决策 11"无匹配候选"
   的 advisory 出口（D-7 范围），本步不吞掉这个边界，直接让 `route_to_
   blueprint` 对空排程抛 `ValueError`，调用方（D-5/D-7）负责在喂给它之前
   先检查 `schedule.scheduled` 是否非空、走 advisory 通道告知用户。

不负责：
- 候选池/utility 构建（D-1 `activity_pool.py`，本模块只 import 消费）。
- 排程可行性算法本身（D-2 `route_scheduler.py`，本模块只调 `schedule_route`/
  `try_insert`，不重实现窗内判定/槽网格 snap/全排列枚举）。
- 节奏/区间填充参数的推导（D-3 `pace_budget.py`，本模块只消费 `pace()`/
  `interval_fill_targets()` 的输出）。
- 接入 `plan_hybrid`/`rule_planner`/`replan`（D-5）。
- pinned 的实际抽取（intent 层解析，D-7）；本模块只接受已经是 `Visit` 形态
  的 pinned 列表。
- critic 复检兜底（既有 `agent.planning.critic`，本模块产出的 blueprint 仍要
  过 `assemble_from_blueprint` + `validate_itinerary` 这两道既有工序）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Optional, Sequence

from data.loader import load_user_profile
from schemas.domain import Poi, Restaurant, UserProfile
from schemas.intent import IntentExtraction

from ..blueprint.blueprint import BlueprintNode, BlueprintTargetKind, PlanBlueprint
from ..blueprint.demand_scope import is_main_meal_cuisine
from ..blueprint.node_decider import KIND_DINING, KIND_MAIN, _DINING_FOCUSED_CONTEXTS
from ..commute.lookup_hop import lookup_hop
from ..critic._rules.helpers import fmt_hhmm, parse_hhmm
from ..critic.meal_windows import (
    DINNER_END_MIN,
    DINNER_START_MIN,
    LUNCH_END_MIN,
    LUNCH_START_MIN,
    SUPPER_END_MIN,
    SUPPER_START_MIN,
)
from ..weights_llm import PlanningWeights
from .activity_pool import (
    TimeWindow,
    Visit,
    build_poi_route_pool,
    build_restaurant_route_pool,
    build_visit_from_poi,
    build_visit_from_restaurant,
    route_score,
)
from .pace_budget import IntervalFillTargets, interval_fill_targets, pace
from .rule_planner import DEFAULT_DEPART_TIME, _parse_start_time_hour
from .route_scheduler import (
    RESERVATION_SLOT_GRID_MIN,
    CommuteFn,
    RouteSchedule,
    schedule_route,
    try_insert,
    try_order_fixed,
)

# ============================================================
# 1. 软锚判定（ADR-0010 决策 10，规则定死）
# ============================================================

_MEAL_CONVENTION_WINDOWS_MIN: tuple[tuple[int, int], ...] = (
    (LUNCH_START_MIN, LUNCH_END_MIN),
    (DINNER_START_MIN, DINNER_END_MIN),
    (SUPPER_START_MIN, SUPPER_END_MIN),
)
"""午/晚/夜宵三个饭点惯例窗（分钟坐标），与 `activity_pool._meal_convention_windows`
共读同一组 `critic.meal_windows` 常量——判"出行窗是否完整跨过某饭点窗"只需要
端点，不需要 `TimeWindow` 的交集/包含方法，故这里直接用 `(start, end)` 元组，
不为这一处引入 `TimeWindow` 依赖。"""

_DEFAULT_DEPART_HOUR: int = int(DEFAULT_DEPART_TIME.split(":")[0])
"""`intent.start_time` 解析失败时的出发小时兜底——与 `rule_planner.
DEFAULT_DEPART_TIME`（"14:00"）取同一个值，不新发明一套默认时间。"""


def _resolve_depart_min(start_time: str) -> int:
    """把 `intent.start_time`（ISO-like / "today_afternoon" 等口语标签）解析成
    出发分钟数，复用 `rule_planner._parse_start_time_hour`（ADR-0010 D-4 任务
    原文点名的既有机制），不新造一套标签→时刻映射。解析不出 → 14:00 兜底
    （与 `rule_planner._resolve_time_window` 的兜底行为一致）。
    """
    hour = _parse_start_time_hour(start_time)
    return (hour if hour is not None else _DEFAULT_DEPART_HOUR) * 60


def dining_soft_anchored(
    intent: IntentExtraction, *, depart_min: Optional[int] = None
) -> bool:
    """判断"饭是否被软锚"（ADR-0010 决策 10，规则定死，不可扩大解释）。

    饭被软锚 **iff**：
    ① `intent.social_context` 落在 `_DINING_FOCUSED_CONTEXTS`（商务接待/
       纪念日仪式感——复用 `node_decider` 的既有集合，不重复定义一份）；
    **或**
    ② 出行窗 `[depart_min, depart_min + hi_min]`（`hi_min` = `duration_hours`
       上限换算的分钟数）的**结束点落在某个饭点惯例窗内**（`w_start <=
       window_end <= w_end`）**或完整跨过**该饭点窗（`depart_min <= w_start
       and window_end >= w_end`）（午/晚/夜宵三选一即可）**且**
       `intent.dietary_constraints` 非空。

    否则涌现（utility 说了算，是否有饭由贪心插入段自然决定）。

    【规则②放宽记录（用户拍板，修订 ADR-0010 决策 10 共识）】
    旧规则要求出行窗"完整覆盖"饭点窗才软锚，真 LLM 复测实证：14:00-19:00
    的家庭局（有 dietary 信号）与晚餐惯例窗 17:00-20:00 只差 60 分钟没盖满，
    旧规则判"不软锚"，饭在涌现段被放弃——但"14 点出门、19 点到家、有忌口"
    分明是打算吃这顿晚饭的家庭局，旧规则过严、违背常识。放宽为"出行窗结束点
    踩进饭点窗内"（仍要求真的"踩线"到饭点，不是任意窗都算）；"完整覆盖"作为
    更强的特例继续保留（两个条件用 `or`，不是谁替代谁）。

    `depart_min`：出行窗起点。**调用方已有真实出发时刻时必须传入**（code-review
    finding #9：`build_route` 的 depart_min 可能是协商/修正过的，与
    `intent.start_time` 的天真解析分叉——软锚判定必须和真实排程用同一个窗）；
    不传（None）时才回退到自行解析 `intent.start_time`（独立调用/测试便利）。
    """
    if intent.social_context in _DINING_FOCUSED_CONTEXTS:
        return True
    if not intent.dietary_constraints:
        return False

    if depart_min is None:
        depart_min = _resolve_depart_min(intent.start_time)
    hi_min = int(intent.duration_hours[1] * 60)
    window_end = depart_min + hi_min

    return any(
        (w_start <= window_end <= w_end)  # 结束点踩进饭点窗内（放宽后新增）
        or (depart_min <= w_start and window_end >= w_end)  # 完整跨过（旧规则，保留）
        for w_start, w_end in _MEAL_CONVENTION_WINDOWS_MIN
    )


# ============================================================
# 2. 贪心插入构造（核心）
# ============================================================

MAX_ACTIVITIES: int = 5
"""路线规模的"延迟安全上限"（ADR-0010 决策 11 层③）。与 `route_scheduler.
MAX_DAY_MIN`/`RESERVATION_SLOT_GRID_MIN` 同类——结构性安全边界，不是可按
UX 偏好调的权重，故不走 `_env_int`（那是给 comfort/time/cost/smoothness
这类"值多少合适见仁见智"的量用的）。"""


@dataclass(frozen=True)
class RouteBuildResult:
    """`build_route` 的产出：排程 + 选中活动 + 未满足清单 + 诊断留痕。

    字段：
    - `schedule`：最终 `RouteSchedule`（时间序，可直接喂 `route_to_blueprint`）。
      `visits` 为空时仍是合法值（`schedule_route([])` 的平凡可行排程），不返回
      `None`——"选不出任何活动"本身是一个有效结果（ADR"稀缺兜底"：宁可给一个
      空/短的诚实结果，也不假装成功）。
    - `visits`：选中活动，**插入序**（pinned → 软锚饭 → 涌现，依次插入的顺序），
      与 `schedule.scheduled` 的**时间序**是两回事——见模块 docstring 判断点 4。
    - `unmet_pinned`：锚点段里 `try_insert` 失败的 pinned 活动（ADR 决策 11
      "绝不静默忽略"——不静默丢弃，留给 D-7 的 advisory 通道消费）。
    - `pace_tier` / `fill_targets`：本次构造实际使用的节奏档与区间填充参数
      （诊断留痕，便于测试/trace 核对"为什么停在这里"）。
    """

    schedule: RouteSchedule
    visits: tuple[Visit, ...]
    unmet_pinned: tuple[Visit, ...]
    pace_tier: str
    fill_targets: IntervalFillTargets


def _default_budget() -> float:
    """路线级预算(元)——判断点 1：为何是这个来源、为何不进公开签名，见模块
    docstring。"""
    return load_user_profile().default_budget


def _try_insert_best_by_key(
    candidates: Sequence[Visit],
    selected: list[Visit],
    *,
    depart_min: int,
    budget_min: int,
    commute_fn: CommuteFn,
    key,
) -> Optional[tuple[Visit, RouteSchedule, float]]:
    """在 `candidates` 里找 `try_insert` 可行、且 `key(visit, 排程)` 最大的一个。

    `key: (visit, candidate_schedule) -> float` 由调用方给：锚点段的软锚饭用
    `base_score`（忽略排程），涌现段用**按时间序算的边际分**（code-review
    finding #2：边际分必须在 candidate_schedule 的时间序上算，不能在插入序上算——
    3+ 活动时两者相邻关系不同，compactness 会评错对象；candidate_schedule 恰好
    就在手边，直接给 key）。两处"选最高分插入"的搜索骨架相同，只有打分函数不同，
    故抽成一个私有 helper。返回 `None` 表示没有任何候选可行；否则返回
    `(visit, schedule, score)`——分数一并带出，调用方（`_greedy_fill_emergent`
    的④号停止条件要用它）不必再重算。
    """
    best: Optional[tuple[Visit, RouteSchedule, float]] = None
    for v in candidates:
        candidate_schedule = try_insert(
            selected, v, depart_min=depart_min, budget_min=budget_min, commute_fn=commute_fn
        )
        if candidate_schedule is None:
            continue
        score = key(v, candidate_schedule)
        if best is None or score > best[2]:
            best = (v, candidate_schedule, score)
    return best


def _is_main_meal_visit(v: Visit) -> bool:
    """visit 是否是一个「正餐」餐厅节点（茶点/咖啡不算；供 B3 用餐上限计数）。"""
    return v.kind == "restaurant" and is_main_meal_cuisine(
        getattr(getattr(v, "entity", None), "cuisine", None)
    )


def _meal_window_cap(depart_min: int, budget_min: int) -> int:
    """出行窗 [depart_min, depart_min+budget_min] 跨过几个饭点惯例窗 → 正餐节点上限。

    Bug B·B3：跨午+晚才允许 2 顿正餐；只跨 1 个（或介于两窗之间）→ 1 顿。**下限恒
    为 1**——「点了一个吃的」永远该拿到那一顿；上限只用来挡「第二顿正餐」。茶点
    （咖啡/下午茶/甜品）不受本上限约束（`_is_main_meal_visit` 不计它们）。
    """
    window_start, window_end = depart_min, depart_min + budget_min
    overlap = sum(
        1
        for w_start, w_end in _MEAL_CONVENTION_WINDOWS_MIN
        if window_start < w_end and window_end > w_start
    )
    return max(1, overlap)


def _greedy_fill_emergent(
    selected: list[Visit],
    schedule: RouteSchedule,
    pool: list[Visit],
    *,
    depart_min: int,
    budget_min: int,
    commute_fn: CommuteFn,
    weights: PlanningWeights,
    money_budget: float,
    targets: IntervalFillTargets,
) -> RouteSchedule:
    """涌现段循环：反复挑边际分最高的可行候选插入，直至命中某条停止条件。

    停止条件实现顺序与 ADR 任务原文编号顺序的差异、为何不影响最终结果，
    见模块 docstring 判断点 2。本函数原地修改 `selected`/`pool`，返回最新
    `RouteSchedule`。

    Bug B 两处根治（都只作用本 ILS 路径，rule_planner 不调本函数）：
    - **B2**：④号停止条件去掉 `and lo_reached`——不再为凑 `lo_min` 硬塞负收益
      活动（`lo_min` 退化为非绑定 aspiration）。真·多站方案的好候选边际分为正、
      照插不误，只砍「没好候选还硬凑」的 padding。
    - **B3**：正餐（`_is_main_meal_visit`）节点数封顶 `_meal_window_cap`；已达上限
      时本轮候选里排除正餐餐厅（茶点/POI 不受限）。`main_meal_count` **从
      `selected` 已选中的正餐数初始化**（含锚点段的 pinned / 软锚饭），不从 0 起，
      否则「软锚正餐 + 涌现正餐」会漏封成两顿。
    """
    meal_cap = _meal_window_cap(depart_min, budget_min)
    main_meal_count = sum(1 for v in selected if _is_main_meal_visit(v))
    while True:
        # ② 活动数上限（不依赖候选搜索，最先短路）
        if len(selected) >= MAX_ACTIVITIES:
            break

        current_total = schedule.total_minutes
        # lo 判「在外时长」达没达下限 —— 用全量 total（含 slack；与 check_duration
        # 的 total_minutes 口径一致）
        lo_reached = current_total >= targets.lo_min
        # ③ 已到下限之上、且「活动+通勤」已超软目标 —— "填够下限之后，按节奏该停就停"。
        # code-review finding #1：软目标 activity_budget_min 的定义是「活动+通勤
        # （不含 slack）」（pace_budget.py，= hi×(1−slack_fraction)）——这里必须
        # 用 total − total_slack 比较；若用全量 total，窗逼出的等待（本身就是 slack）
        # 会被双重计费，等待多的路线被系统性欠填。
        if lo_reached and (current_total - schedule.total_slack_min) > targets.activity_budget_min:
            break

        # B3：正餐已达上限 → 本轮候选排除正餐餐厅（茶点/POI 仍可进）
        eligible = pool
        if main_meal_count >= meal_cap:
            eligible = [v for v in pool if not _is_main_meal_visit(v)]
            if not eligible:
                break

        # 基准分每轮只算一次（code-review finding #7：它是轮内循环不变量），
        # 且按**当前排程的时间序**算（finding #2：打分对象=真实产出的路线）。
        base_route_score = route_score(
            [sv.visit for sv in schedule.scheduled], weights, money_budget
        )
        found = _try_insert_best_by_key(
            eligible,
            selected,
            depart_min=depart_min,
            budget_min=budget_min,
            commute_fn=commute_fn,
            key=lambda v, cs: route_score(
                [sv.visit for sv in cs.scheduled], weights, money_budget
            )
            - base_route_score,
        )
        # ① 无可行候选
        if found is None:
            break

        visit, candidate_schedule, margin = found
        # ④ B2：负收益即停（不再 `and lo_reached`；见函数 docstring）
        if margin <= 0:
            break

        selected.append(visit)
        schedule = candidate_schedule
        pool.remove(visit)
        if _is_main_meal_visit(visit):
            main_meal_count += 1

    return schedule


def build_route(
    pois: Sequence[Poi],
    restaurants: Sequence[Restaurant],
    intent: IntentExtraction,
    weights: PlanningWeights,
    *,
    depart_min: int,
    commute_fn: CommuteFn,
    semantic_scores: Optional[dict[str, float]] = None,
    pinned: Optional[Sequence[Visit]] = None,
) -> RouteBuildResult:
    """锚定两段贪心插入构造（ADR-0010 决策 3/7）：选子集 + 排定，一步到位。

    Args:
        pois / restaurants: 候选实体（调用方负责先按 intent 查询召回；本函数
            内部做 D-1 的池扩容/分层取样，不重复调用方已做的召回过滤）。
        intent / weights: 同 D-1/D-3 的既有消费方式。
        depart_min: 出发时刻（分钟坐标），由调用方解析 `intent.start_time` 后
            传入（`build_route` 本身不重复解析——`dining_soft_anchored` 内部
            另需独立解析是因为它是一个可单独调用的纯判定函数，两处解析同一
            机制但不共享同一次调用结果，因为调用方可能出于其它理由已经算出
            了不同于"天真解析"的 `depart_min`，如 rule 地板的时段协商）。
        commute_fn: 通勤查询，生产环境用 `make_commute_fn(user_profile)` 生成。
        semantic_scores: 转发给 `build_visit_from_poi` 的语义分（可选）。
        pinned: 用户明确需求对应的 `Visit` 列表（D-7 起由 `ils_planner.plan_hybrid`
            经 `_resolve_pinned` 灌入真实值；传 `None` 等同于"无锚点"）。

    Returns:
        `RouteBuildResult`：见其 docstring。
    """
    pace_tier = pace(intent)
    targets = interval_fill_targets(intent, pace_tier)
    budget_min = targets.hi_min
    money_budget = _default_budget()

    poi_pool = build_poi_route_pool(list(pois))
    rest_pool = build_restaurant_route_pool(list(restaurants))
    poi_visits = [
        build_visit_from_poi(p, intent, weights, semantic_scores=semantic_scores)
        for p in poi_pool
    ]
    rest_visits = [build_visit_from_restaurant(r, intent, weights) for r in rest_pool]

    selected: list[Visit] = []
    unmet_pinned: list[Visit] = []

    # ---- 锚点段·1：pinned ----
    for anchor in pinned or []:
        candidate = try_insert(
            selected, anchor, depart_min=depart_min, budget_min=budget_min, commute_fn=commute_fn
        )
        if candidate is None:
            unmet_pinned.append(anchor)
            continue
        selected.append(anchor)

    schedule = schedule_route(
        selected, depart_min=depart_min, budget_min=budget_min, commute_fn=commute_fn
    )
    if schedule is None:  # 防御性：逐个 try_insert 已验可行，重跑同一集合必可行；
        # 不用 assert（-O 下会被剥离，None 会静默流向下游，code-review finding #10）
        raise RuntimeError(
            "build_route 内部不变量违反：锚点集合逐个 try_insert 可行，"
            "但整体 schedule_route 返回 None——route_scheduler 语义变了？"
        )

    # ---- 锚点段·2：软锚饭（判断点 3：pinned 已含餐厅则跳过；finding #9：
    # 软锚判定与真实排程共用同一个 depart_min，不各自解析）----
    if dining_soft_anchored(intent, depart_min=depart_min) and not any(
        v.kind == "restaurant" for v in selected
    ):
        found = _try_insert_best_by_key(
            rest_visits,
            selected,
            depart_min=depart_min,
            budget_min=budget_min,
            commute_fn=commute_fn,
            key=lambda v, _schedule: v.base_score,
        )
        if found is not None:
            chosen_restaurant, schedule, _base_score = found
            selected.append(chosen_restaurant)

    # ---- 涌现段：从候选池剔除已选中的 (kind, target_id)，循环边际分插入 ----
    selected_keys = {(v.kind, v.target_id) for v in selected}
    emergent_pool = [
        v for v in (poi_visits + rest_visits) if (v.kind, v.target_id) not in selected_keys
    ]
    schedule = _greedy_fill_emergent(
        selected,
        schedule,
        emergent_pool,
        depart_min=depart_min,
        budget_min=budget_min,
        commute_fn=commute_fn,
        weights=weights,
        money_budget=money_budget,
        targets=targets,
    )

    return RouteBuildResult(
        schedule=schedule,
        visits=tuple(selected),
        unmet_pinned=tuple(unmet_pinned),
        pace_tier=pace_tier,
        fill_targets=targets,
    )


# ============================================================
# 3. RouteSchedule → PlanBlueprint
# ============================================================


def route_to_blueprint(
    schedule: RouteSchedule, intent: IntentExtraction, depart_min: int
) -> PlanBlueprint:
    """把已排定的 `RouteSchedule` 转成 `PlanBlueprint`（下游 `assemble_from_
    blueprint` 消费）。

    Raises:
        ValueError: `schedule.scheduled` 为空——`PlanBlueprint.nodes` 硬性
            要求 `min_length=1`，零活动路线无法表示成合法蓝图（判断点 9，
            这类"选不出任何活动"的情形属于 D-7 advisory 出口，调用方应在
            调用本函数前先检查）。
    """
    if not schedule.scheduled:
        raise ValueError(
            "route_to_blueprint 收到空排程（0 个活动）——PlanBlueprint 无法表示"
            "零节点方案；这是「无匹配候选」的 advisory 情形（ADR-0010 决策 11），"
            "调用方应在此之前检测并走 advisory 通道，不应直接喂给本函数。"
        )

    party_size = sum(c.count for c in intent.companions) or 1

    nodes: list[BlueprintNode] = []
    for sv in schedule.scheduled:
        v = sv.visit
        not_before = fmt_hhmm(sv.start_min) if sv.slack_min > 0 else None

        if v.kind == "restaurant":
            chosen_time = fmt_hhmm(sv.start_min)
            nodes.append(
                BlueprintNode(
                    kind=KIND_DINING,
                    target_kind=BlueprintTargetKind.RESTAURANT,
                    target_id=v.target_id,
                    duration_min=v.duration_min,
                    note=f"已为你预留 {chosen_time}（{party_size} 人）",
                    not_before_start=not_before,
                )
            )
        else:
            nodes.append(
                BlueprintNode(
                    kind=KIND_MAIN,
                    target_kind=BlueprintTargetKind.POI,
                    target_id=v.target_id,
                    duration_min=v.duration_min,
                    note=None,
                    not_before_start=not_before,
                )
            )

    return PlanBlueprint(
        nodes=nodes,
        preferred_start_time=fmt_hhmm(depart_min),
        rationale="ADR-0010 D-4：锚定两段贪心插入构造",
    )


# ============================================================
# 4. commute_fn 生产包装
# ============================================================


def make_commute_fn(user_profile: UserProfile) -> CommuteFn:
    """生产环境 `CommuteFn` 包装：绑定 transport_pref + `functools.lru_cache`。

    transport_pref 归一化逻辑对齐 `replan._RULE_ASSEMBLER_ADAPTER`（"合法三选一
    否则回退 taxi"），不新发明一套规则。`lru_cache` 是主代理点名要求——`build_
    route` 内部 `schedule_route`/`try_insert` 对同一组活动做全排列枚举，加上
    涌现段逐个候选试插入，同一 `(from_id, to_id)` 通勤对会被反复查询，缓存在
    这层收益明显（`lookup_hop` 本身对同输入保证同输出，缓存不改变语义）。

    `user_profile` 未必可哈希（Pydantic BaseModel 默认不 frozen），因此不能把
    它当 `lru_cache` 函数的参数——本函数把它放进闭包，被缓存的内层函数只接受
    `(from_id, to_id)` 两个 `str` 参数，天然可哈希。每次调用 `make_commute_fn`
    都会创建一个新的闭包 + 新的独立缓存（不是跨调用共享的全局缓存）——这正是
    "同一次路线构造内部反复查询"这个场景要的粒度，不多不少。
    """
    transport_pref = (
        user_profile.transport_preference
        if user_profile.transport_preference in {"walking", "taxi", "bus"}
        else "taxi"
    )

    @lru_cache(maxsize=None)
    def _commute(from_id: str, to_id: str) -> int:
        minutes, _mode, _path_type = lookup_hop(from_id, to_id, transport_pref, user_profile)
        return minutes

    return _commute


# ============================================================
# 5. 修复闭环重搜（min-conflicts 风格有界修复；ADR-0009 决策 3/4/5/6 引入、
# ADR-0010 D-5 迁移到路线模型、**ADR-0013 F-1 从 `ils_planner.py` 模块私有
# 提升为本模块公开 seam**——见模块 docstring「公开接口」节 `repair_route` 条
# 与 D-5 原始设计动机；本节三个函数**逐字节原样迁移**，只去掉前导下划线
# （`repair_route` 一个）与调整 import 位置（模块顶层 vs 函数内局部），未改
# 一行判定/计算逻辑——`ils_planner.py` 里的历史「调研留痕」叙事对应的正是
# 这三个函数，原样保留在那边不必重写，只是实现挪到了这里）。
# ============================================================


def _shrink_visit_windows(visit: Visit, blocked_slot_hhmm: str) -> Optional[Visit]:
    """把 `visit.windows` 挖掉 `[slot, slot+GRID-1]` 这一段（半点槽宽度，
    `route_scheduler.RESERVATION_SLOT_GRID_MIN`）。

    `Visit` 是 frozen dataclass，用 `dataclasses.replace` 复制改 `windows`；某个
    窗被挖穿则拆成左右两段（若非空）；全部窗都被挖空 → 返回 None（该实体这轮
    彻底不可用，调用方据此把它当整体拉黑处理）。

    这是路线模型下"封 (餐厅,时段)"黑名单真正生效的机制——`route_scheduler.
    _earliest_feasible_start` 总是取窗内**最早**可行开始时刻；不挖窗，同一批
    候选原样重搜会算出同一个时刻，黑名单形同虚设、陷入原地震荡。

    `blocked_slot_hhmm` 解析失败（防御性，正常不会发生）→ 原样返回 `visit`
    （保守不挖，不误伤）。
    """
    blocked_start = parse_hhmm(blocked_slot_hhmm)
    if blocked_start is None:
        return visit

    blocked_end = blocked_start + RESERVATION_SLOT_GRID_MIN - 1

    new_windows: list[TimeWindow] = []
    for w in visit.windows:
        if blocked_end < w.start_min or blocked_start > w.end_min:
            new_windows.append(w)  # 与挖除区间无交集，原样保留
            continue
        if w.start_min < blocked_start:
            new_windows.append(TimeWindow(w.start_min, blocked_start - 1))
        if w.end_min > blocked_end:
            new_windows.append(TimeWindow(blocked_end + 1, w.end_min))

    if not new_windows:
        return None
    return replace(visit, windows=new_windows)


def _apply_blacklist_to_pool(
    visits: list[Visit],
    blacklist_ids: set[str],
    blacklist_time: set[tuple[str, str]],
) -> list[Visit]:
    """按黑名单过滤/挖窗候选池（小 helper，供 `repair_route` 复用，不埋进
    `plan_hybrid` 主体）。

    整体拉黑（`blacklist_ids`）优先于挖窗——同一实体若既整体拉黑又有封槽记录
    （理论上不会同时发生，防御性处理）直接跳过。挖窗挖穿（返回 None）等价于
    该实体本轮不可用，同样跳过。
    """
    out: list[Visit] = []
    for v in visits:
        if v.target_id in blacklist_ids:
            continue
        blocked_slots = {slot for rid, slot in blacklist_time if rid == v.target_id}
        cur: Optional[Visit] = v
        for slot in blocked_slots:
            if cur is None:
                break
            cur = _shrink_visit_windows(cur, slot)
        if cur is None:
            continue
        out.append(cur)
    return out


def repair_route(
    previous_scheduled: Sequence[Any],
    poi_visits: list[Visit],
    rest_visits: list[Visit],
    weights: PlanningWeights,
    *,
    depart_min: int,
    budget_min: int,
    commute_fn: CommuteFn,
    money_budget: float,
    blacklist_poi: set[str],
    blacklist_rest: set[str],
    blacklist_rest_time: set[tuple[str, str]],
    preserve_position: Optional[int] = None,
    reorder_flag_out: Optional[dict] = None,
) -> Optional[RouteSchedule]:
    """min-conflicts 风格有界修复（ADR-0009 引用的 prior art：Minton et al. 1992）：
    把上一轮方案里命中黑名单的节点从 `previous_scheduled` 剔除（POI/餐厅整黑
    直接剔除；封槽餐厅挖窗后仍不可用才剔除），再从（同样按黑名单过滤/挖窗后的）
    候选池里为每个空出的槽位找边际分最高的替补插回。找不到替补则该槽位空出，
    不强凑（ADR-0010 决策 10「稀缺兜底」：宁可短而好，不塞次优凑数）。

    与"整条方案推倒重来"式重搜（旧 `_search_best_avoiding`）的关键差异：本函数
    只重赋"参与被违反约束的那个变量"，其余节点原样保留——更贴近 min-conflicts
    的字面定义，也让"仍是最优、只是这个时刻不行"的候选（如旗舰 demo 的 R001）
    在挖窗后继续参与竞争，而不会被误伤为整店拉黑。

    **ADR-0013 F-1 新消费方（`planners/node_swap.py`）的复用方式**：局部重解
    只拉黑**用户点名要换的那一个** target_id（`blacklist_poi={target_id}` 或
    `blacklist_rest={target_id}`，二选一，从不同时用两者），`blacklist_rest_time`
    恒传空集——"换菜"永远是换整个实体，不是同一实体挪时段（挪时段是 ILS 修复
    闭环自己的用法，与"用户点名要换这一格"是两回事）。`poi_visits`/`rest_visits`
    形参是"这一轮允许被选中的替补候选池"——F-1 按降级序列（同子类→同大类异
    子类→近似）传入逐级放宽的**候选子集**，不是全量池；`removed_kinds` 只会
    含一个元素（target 自身的 kind），故 `poi_visits`/`rest_visits` 中与
    target_kind 不同的那一个可以安全传空列表（本函数不会尝试用它）。
    `previous_scheduled` 为空且 `schedule_route([...])` 返回 None 是"钉住的
    其余节点在去掉目标后本身就排不到一块儿了"的信号（如中间站被抽走后两端
    直达通勤暴涨）——F-1 把这一 None 结果映射为 `AdvisoryCode.
    SWAP_KEPT_NODE_UNFIT`，复用 D-7 `PINNED_UNSATISFIABLE` 的"绝不静默、如实
    告知"先例语义（见 `node_swap.py` 模块 docstring）。

    **`preserve_position`（换菜"位置丢失" bug 修复，2026-07-10）——为什么需要**：
    `schedule_route(kept, ...)`（无此形参时的既有路径）对 `kept` 做**全排列
    枚举**求最优序（`route_scheduler` 判断点 1），这对"首次构造"（顺序本就
    未定）是对的，但换菜场景里 `kept` 已经是**用户看到过的、排定妥当的顺序**
    ——全排列允许把保留节点整体重排，真实症状：两活动方案换 1 号位，替补与
    2 号位保留节点互换了位置（成员级换对、位置级错位，用户读作"换错了卡"）。
    `try_insert` 同一根因（其 docstring 原话"不保留 existing 原有顺序"）——
    `ils_planner.plan_hybrid` 消费 `repair_route` 服务 critic 修复闭环，那里
    "整体重排"合理且必须保留（blame 驱动的修复本就可能需要挪动多个节点），
    不能改 `schedule_route`/`try_insert` 本身语义，故做成**换菜专用 opt-in**：
    只有 `node_swap.py` 传入 `preserve_position` 才走"钉死顺序"路径，ILS
    调用点不传此参数，行为与本次改动前逐字节一致。

    语义：`preserve_position` 是目标节点在**去掉它之后的 `kept` 列表**里应该
    被插回的下标（0-indexed，只数非 home 的已选节点——`kept`/`previous_
    scheduled` 本就不含 home，见 D-2 `schedule_route` 的 `home_id` 处理，不
    是"整个 itinerary.nodes 下标"，调用方 `node_swap.py` 负责按这个口径换算）。
    替补候选逐个按 `kept[:preserve_position] + [v] + kept[preserve_position:]`
    这个**固定顺序**用 `route_scheduler.try_order_fixed` 求可行排程（不枚举、
    不重排，见其 docstring）——固定顺序内哪个候选胜出，selection 逻辑与既有
    行为一致（仍按 `route_score` 边际分挑最优，只是"可行"的判据从"存在某个
    可行序"换成"这一个序可行"）。

    **`preserve_position` 只有 `len(removed_kinds) == 1` 时才生效**（F-1 恒
    只黑名单一个 target_id，`removed_kinds` 天然只含一个元素；若未来有调用方
    在一次调用里塞进多个黑名单目标却仍传了 `preserve_position`，"这一个下标
    该对应哪个槽位"本就歧义不明，因此直接退化为忽略 `preserve_position`、按
    原有全排列路径处理其余全部槽位——保守选择，不是本次要覆盖的场景）。

    **降级到全排列的边界（诚实退让，不强行凑一个不可行的定序）**：固定顺序
    对当前候选池里**所有**候选都排不开（如替补时长远超原节点、挤压后续节点
    窗口）时，整个替补搜索退回现行为——对这一个 kind 的槽位改用 `schedule_
    route`/`try_insert` 的全排列路径重新搜索一遍（选中的候选可能因此与固定
    顺序搜索不同，全排列有更大的可行空间）。是否发生了这次降级通过
    `reorder_flag_out`（见 Args）告知调用方，`node_swap.py` 据此产出
    `AdvisoryCode.SWAP_REORDERED`——"绝不静默"纪律的同一先例（`SWAP_KEPT_
    NODE_UNFIT`/`SWAP_NO_ALTERNATIVE_FOUND` 已确立）：宁可如实说"为了排开，
    顺序调整了"，不要悄悄把用户点的那一格换到别的位置上。

    Args:
        previous_scheduled: 上一轮 `RouteSchedule.scheduled`
            （`route_scheduler.ScheduledVisit` 序列，或任何暴露 `.visit`
            属性的等价对象——本函数只读这一个属性，不依赖具体类型）。
        poi_visits / rest_visits: 完整候选 Visit 池（未按本轮黑名单过滤，
            由调用方缓存跨轮复用）。
        blacklist_poi / blacklist_rest / blacklist_rest_time: 跨轮单调累积的
            黑名单（`plan_hybrid` 维护；F-1 场景下是一次性的单元素集合）。
        preserve_position: 换菜专用 opt-in（见上）。`None`（默认）→ 行为与
            本次改动前逐字节一致，ILS 消费方零感知。
        reorder_flag_out: 换菜专用 opt-in 的输出信道——调用方传入一个空
            `dict`，本函数在发生"固定顺序降级为全排列"时写入
            `reorder_flag_out["reordered"] = True`（不发生则保持调用方
            传入时的原样，通常是空 dict，键不存在）。选用"调用方提供的
            输出容器"而非改变本函数返回类型（如 `(schedule, bool)` 二元组），
            是为了让 `repair_route` 的返回类型 `Optional[RouteSchedule]`
            对**所有**调用方、**所有**参数组合保持字面不变——包括提前返回
            分支（`schedule is None` / `not removed_kinds`）——不需要在这些
            分支里区分"这次调用有没有传 preserve_position"来决定返回形状，
            心智负担和出错面都更小；`ils_planner.py` 原调用点不传这两个新
            形参，函数签名、返回类型、调用写法三者均与本次改动前逐字节一致。

    Returns:
        新的 `RouteSchedule`；`kept` 为空且无任何替补时返回一个 0 活动的平凡
        排程（`schedule_route([], ...)` 的既有语义）——调用方按"scheduled 为空"
        判断本轮修复是否产出可用方案。理论上 `kept` 恒可行（此前已知可行集合
        的子集，去掉约束只会更容易排开），仍做防御性 None 检查以防未来
        `route_scheduler` 语义变化时静默吞掉异常（这正是 F-1 `SWAP_KEPT_NODE_
        UNFIT` 复用的那个判据——`kept` 恒可行的假设对"抽走一个中间站"这个
        F-1 特有场景不总成立，见上方「新消费方」段落）。
    """
    kept: list[Visit] = []
    removed_kinds: list[str] = []

    for sv in previous_scheduled:
        v = sv.visit
        if v.kind == "poi" and v.target_id in blacklist_poi:
            removed_kinds.append("poi")
            continue
        if v.kind == "restaurant" and v.target_id in blacklist_rest:
            removed_kinds.append("restaurant")
            continue
        if v.kind == "restaurant":
            blocked_slots = {slot for rid, slot in blacklist_rest_time if rid == v.target_id}
            cur: Optional[Visit] = v
            for slot in blocked_slots:
                if cur is None:
                    break
                cur = _shrink_visit_windows(cur, slot)
            if cur is None:
                removed_kinds.append("restaurant")
                continue
            v = cur
        kept.append(v)

    schedule = schedule_route(
        kept, depart_min=depart_min, budget_min=budget_min, commute_fn=commute_fn
    )
    if schedule is None:
        return None  # 防御性：kept 是此前已知可行集合的子集，理论恒可行

    if not removed_kinds:
        return schedule

    # 位置保持只在"恰好一个槽位"时语义明确（见上方 docstring）；否则退化为
    # 全排列路径处理全部槽位，preserve_position 被忽略。
    fixed_slot = preserve_position if len(removed_kinds) == 1 else None
    reordered = False

    poi_pool = _apply_blacklist_to_pool(poi_visits, blacklist_poi, set())
    rest_pool = _apply_blacklist_to_pool(rest_visits, blacklist_rest, blacklist_rest_time)
    kept_keys = {(v.kind, v.target_id) for v in kept}
    poi_pool = [v for v in poi_pool if (v.kind, v.target_id) not in kept_keys]
    rest_pool = [v for v in rest_pool if (v.kind, v.target_id) not in kept_keys]

    for kind in removed_kinds:
        pool = poi_pool if kind == "poi" else rest_pool
        if not pool:
            continue
        base_score = route_score(
            [sv.visit for sv in schedule.scheduled], weights, money_budget
        )

        best: Optional[tuple[Visit, RouteSchedule, float]] = None
        used_fixed_position = fixed_slot is not None
        if fixed_slot is not None:
            best = _best_fixed_position_candidate(
                pool,
                kept,
                fixed_slot,
                depart_min=depart_min,
                budget_min=budget_min,
                commute_fn=commute_fn,
                weights=weights,
                money_budget=money_budget,
                base_score=base_score,
            )
            if best is None:
                # 固定顺序对这个池子里所有候选都排不开——诚实退让到全排列
                # （见上方 docstring「降级到全排列的边界」），不强凑一个不
                # 可行的定序。只有退让之后确实选中了某个候选（下方 best is
                # not None 分支）才算"真的发生了重排"，若全排列也一无所获，
                # 这个槽位本就空着（既有"稀缺兜底"语义），不算重排。
                used_fixed_position = False

        if best is None:
            best = _best_reordering_candidate(
                pool,
                kept,
                depart_min=depart_min,
                budget_min=budget_min,
                commute_fn=commute_fn,
                weights=weights,
                money_budget=money_budget,
                base_score=base_score,
            )

        if best is not None:
            chosen, candidate_schedule, _margin = best
            kept.append(chosen)
            schedule = candidate_schedule
            pool.remove(chosen)
            if fixed_slot is not None and not used_fixed_position:
                reordered = True

    if reordered and reorder_flag_out is not None:
        reorder_flag_out["reordered"] = True
    return schedule


def _best_reordering_candidate(
    pool: list[Visit],
    kept: list[Visit],
    *,
    depart_min: int,
    budget_min: int,
    commute_fn: CommuteFn,
    weights: PlanningWeights,
    money_budget: float,
    base_score: float,
) -> Optional[tuple[Visit, RouteSchedule, float]]:
    """现行为（`try_insert` 全排列重排，边际分最高者胜出）——`repair_route`
    改动前的替补搜索循环体原样抽出（未改一行判定逻辑，只是给了个名字），供
    固定顺序搜索找不到可行候选时降级复用，避免同一循环写两遍。"""
    best: Optional[tuple[Visit, RouteSchedule, float]] = None
    for v in pool:
        candidate_schedule = try_insert(
            kept, v, depart_min=depart_min, budget_min=budget_min, commute_fn=commute_fn
        )
        if candidate_schedule is None:
            continue
        margin = route_score(
            [sv.visit for sv in candidate_schedule.scheduled], weights, money_budget
        ) - base_score
        if best is None or margin > best[2]:
            best = (v, candidate_schedule, margin)
    return best


def _best_fixed_position_candidate(
    pool: list[Visit],
    kept: list[Visit],
    fixed_slot: int,
    *,
    depart_min: int,
    budget_min: int,
    commute_fn: CommuteFn,
    weights: PlanningWeights,
    money_budget: float,
    base_score: float,
) -> Optional[tuple[Visit, RouteSchedule, float]]:
    """位置保持替补搜索：每个候选按 `kept[:fixed_slot] + [v] + kept[fixed_slot:]`
    这一个**固定顺序**用 `try_order_fixed` 求可行排程（不枚举、不重排），
    "可行候选里边际分最高者胜出"的选优逻辑与 `_best_reordering_candidate`
    对称——两者唯一的差异是可行性判据从"存在某个可行序"（`try_insert`）换成
    "这一个序可行"（`try_order_fixed`），见 `repair_route` docstring
    「`preserve_position`」节。`fixed_slot` 越界（理论上不会——`node_swap.py`
    按 `kept` 实际长度换算）由 `list` 切片语义自然处理（切片不因越界抛异常，
    只是不产生额外元素），不做额外校验。
    """
    best: Optional[tuple[Visit, RouteSchedule, float]] = None
    for v in pool:
        order = kept[:fixed_slot] + [v] + kept[fixed_slot:]
        candidate_schedule = try_order_fixed(
            order, depart_min=depart_min, budget_min=budget_min, commute_fn=commute_fn
        )
        if candidate_schedule is None:
            continue
        margin = route_score(
            [sv.visit for sv in candidate_schedule.scheduled], weights, money_budget
        ) - base_score
        if best is None or margin > best[2]:
            best = (v, candidate_schedule, margin)
    return best
