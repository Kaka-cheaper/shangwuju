"""agent.planning.planners.activity_pool —— ADR-0010 D-1：约束 + utility 构建层。

【定位（ADR-0010「三层解耦架构」的①层）】

多活动 TOPTW 重构把规划问题从"1 主活动 + 1 餐"升级为"从均质活动池（POI ∪ 餐厅）
里选子集 + 顺序 + 时刻"。三层架构里，本模块是**①约束 + utility 构建层**（域知识
住这）：把每个候选 POI/餐厅变成一个"活动（visit）"——带自然时长、候选时间窗、
utility 基分。**不含**②通用 TOPTW 求解器（D-2 窗感知调度器）、**不含**③组装/
critic 兜底（D-4/既有 critic）。

本模块是 D-1 子步的产出，**纯新增，不接线**：不改 `ils_planner.py` 的
`plan_hybrid`/`_greedy_init`/`_utility`/`CandidatePlan` 等现有流程（那是 D-4
"big-bang" 步的事）——只读它的 `_utility`/`_overload_penalty` 做纵向复用。

【公开接口】

- `TimeWindow`：[start_min, end_min] 闭区间时间窗值对象（分钟坐标）。
- `build_poi_time_windows` / `build_restaurant_time_windows`：候选时间窗构建
  （餐厅默认=饭点 ∩ 营业时间；POI=营业时间），支持可选 `pin` 收窄接口。
- `Visit`：均质活动值对象（kind/target_id/自然时长/候选窗/utility 基分/类别/花费）。
- `build_visit_from_poi` / `build_visit_from_restaurant`：由候选实体构造 `Visit`。
- `poi_category` / `restaurant_category`：多样性罚用的类别标签来源。
- `build_route_candidate_pool`（+ `build_poi_route_pool` / `build_restaurant_route_pool`
  两个便捷封装）：为路线构造扩容/分层取样候选池（不改 `_query_pois`/`_query_restaurants`
  本身，只对它们的输出做池准备）。
- `route_commute_compactness` / `route_diversity_penalty` / `route_budget_penalty`：
  路线级 utility 三项（通勤紧凑 / 轻量多样性罚 / 预算软罚）。
- `route_score` / `marginal_score`：路线级 utility 组合 + 插入边际分 helper
  （D-4 贪心插入构造消费）。

【调研留痕：几个本步自行拍板、值得读者知道的判断点】

1. **窗的表示形式与语义**：`list[TimeWindow]`（可能多窗），且**统一表示「允许的
   开始时刻」**（start-time windows），不是「允许停留的时段」。原因：两个 critic
   的复检语义不同——`check_opening_hours` 要求**整段停留** [start, start+duration]
   落在营业时间内，`check_meal_time` 只要求**开始时刻**落在饭点窗内；若把两种窗
   直接求交后交给调度器，无论按哪种语义消费都会出错（要么排出打烊前吃不完的晚餐，
   要么白扔 13:00 开吃的合法午餐）。构建期 duration 已知，故在本层就把营业时间窗
   换算成开始时刻窗（`end - duration`），与饭点窗（天然 start 语义）同语义求交——
   D-2 调度器拿到的窗**单一语义、直接可用**，且与两个 critic 的复检双双对齐。
   （多窗保留：餐厅天然有午/晚/夜宵三个不相交窗，TOPTW 文献里"multiple time
   windows"是有名有姓的变体，非本模块自创。）
2. **多窗餐厅**：不裁成"取第一个可行窗"，而是把三个窗都构造出来交给 D-2 调度器
   自行挑——D-1 只管"哪些时刻物理/惯例上可能"，不替调度器做选择。
3. **budget 数据来源**：`route_budget_penalty` 不自己加载 `UserProfile`——
   `IntentExtraction` 目前没有预算字段，唯一现存来源是
   `data.loader.load_user_profile().default_budget`（默认 300 元）。本模块保持
   纯函数（budget 由调用方传入），避免为了"顺手加载"引入对 `data.loader` 的
   隐式依赖、也避免在单测里被迫 mock 全局加载。调用方（D-4）负责决定从 intent
   还是 profile 取值；若未来 intent 加了预算字段，两者谁优先是 D-4 该决定的事。
4. **类别信号来源（POI 用 `type`，餐厅用 `cuisine`）**：调研过 mock 数据后选定，
   理由见 `poi_category`/`restaurant_category` docstring——`tags` 是多值控制词典，
   判"同类"要设交集阈值，容易主观；`type`/`cuisine` 精确匹配无需调参，且恰好
   命中 mock 目录里真实的"同款扎堆"案例（猫咖 P022/P023、KTV P026/P027 等）。
5. **pin 收窄接口的语义**：POI 侧 pin 与营业时间**求交**（物理约束不可协商）；
   餐厅侧 pin **覆盖**饭点惯例默认、但仍与营业时间求交（ADR-0010 决策 2 原话
   "用户明确需求在这层覆盖默认"）。两侧都不在本层判断"pin 与物理冲突"是否要
   报错/告知用户——那是 D-2 调度器 + D-7 advisory 通道的职责，本层只如实收窄，
   collapse 到空列表也不报错（表示"这一时段物理不可行"）。
6. **每活动基分与 `_overload_penalty` 的边界**：`build_visit_from_poi` 复用
   `ils_planner._utility(poi, None, ...)` 取六项分量（rating/标签命中/age_range
   惩罚/语义分/social 匹配/cost），但手动加回 `0.5 * _overload_penalty(poi, intent)`
   精确抵消——ADR-0010 把"时长 vs 年龄 cap"的耦合记在 D-3（"年龄 cap 夹紧归 D-3，
   本步不做"），提前带进 base_score 会让 D-1 偷跑 D-3 的语义。`Visit.duration_min`
   同理是**未夹年龄 cap** 的自然投影值。

不负责：
- 可行性判定 / 排程（在 D-2 窗感知调度器；ADR-0010 决策 5："可行性从 _utility
  剥离，utility 退成纯打分"，本模块的 base_score/route_score 都不产 fail_detail）。
- 贪心插入构造 / 锚点两段构造（在 D-4）。
- 用户明确需求（pin）的抽取（intent 层解析 prompt，D-7 的跨层依赖；本模块只留
  `pin` 形参接口）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, TypeVar, Union

from schemas.domain import Poi, Restaurant
from schemas.intent import IntentExtraction

from ..critic._rules.helpers import _BUSINESS_HOURS_RE
from ..critic.meal_windows import (
    DINNER_END_MIN,
    DINNER_START_MIN,
    LUNCH_END_MIN,
    LUNCH_START_MIN,
    SUPPER_END_MIN,
    SUPPER_START_MIN,
    TEAHOUSE_CUISINES,
)
from ..weights_llm import PlanningWeights
from utils.duration_helpers import get_duration_for_companions
from .ils_planner import _env_float, _env_int, _overload_penalty, _utility

T = TypeVar("T")


# ============================================================
# 1. TimeWindow 值对象
# ============================================================


@dataclass(frozen=True)
class TimeWindow:
    """[start_min, end_min] 闭区间时间窗（分钟坐标）。

    坐标系与 `critic._rules.helpers.parse_hhmm` 一致（允许 0-29h 表示跨日，如
    26*60 表示次日 02:00）——D-2 调度器、critic 复检、本模块三方在同一坐标系下
    工作，不需要来回换算。**注意 `fmt_hhmm` 会把输出 clamp 在 23:59**（且
    `Itinerary` 的时间戳系统性不支持跨日，见 design.md）——窗坐标允许 >24h 只是
    构造层的表示能力，**D-2 调度器必须把路线整体约束在当日内**（排定时刻 ≤ 23:59），
    跨午夜的窗尾对排程而言不可达。
    """

    start_min: int
    end_min: int

    def __post_init__(self) -> None:
        if self.end_min < self.start_min:
            raise ValueError(
                f"TimeWindow 非法：end_min({self.end_min}) < start_min({self.start_min})"
            )

    def intersect(self, other: "TimeWindow") -> Optional["TimeWindow"]:
        """两窗交集；不相交返回 None。

        端点相接（如 [600,700] 与 [700,800]）保留零宽窗口 [700,700]，不视为
        不相交——"这一刻是否可用"是 D-2 调度器的判定，本层只如实收窄。
        """
        lo = max(self.start_min, other.start_min)
        hi = min(self.end_min, other.end_min)
        if lo > hi:
            return None
        return TimeWindow(lo, hi)

    def contains(self, minute: int) -> bool:
        return self.start_min <= minute <= self.end_min

    @property
    def duration_min(self) -> int:
        return self.end_min - self.start_min


# 无约束哨兵：与 parse_hhmm 的最大可表示时刻（29:59）对齐。
FULL_DAY_WINDOW = TimeWindow(0, 29 * 60 + 59)


def _intersect_many(a: list[TimeWindow], b: list[TimeWindow]) -> list[TimeWindow]:
    """两组窗两两求交，丢弃空交集，其余保留（用于"惯例窗 ∩ 物理窗"这类组合）。"""
    out: list[TimeWindow] = []
    for wa in a:
        for wb in b:
            w = wa.intersect(wb)
            if w is not None:
                out.append(w)
    return out


# ============================================================
# 2. opening_hours 解析（复用 critic._rules.helpers 的正则，不重写一份）
# ============================================================


def _opening_hours_window(opening_hours: str) -> TimeWindow:
    """把 opening_hours 字符串解析成单个 TimeWindow；解析不出 → 无约束。

    复用 `critic._rules.helpers._BUSINESS_HOURS_RE`（同一份正则，不重写一份），
    与 `_is_in_business_hours` 判定同一套语义：
    - 空字符串 / 不识别格式 → 不约束（对齐其"不误伤"策略）
    - 跨日营业（close_t <= open_t，如"22:00-04:00"）→ 不约束（hackathon 简化，
      与 `_is_in_business_hours` 一致，见该函数 docstring）
    """
    if not opening_hours:
        return FULL_DAY_WINDOW
    m = _BUSINESS_HOURS_RE.match(opening_hours.strip())
    if not m:
        return FULL_DAY_WINDOW
    open_h, open_m, close_h, close_m = map(int, m.groups())
    open_t = open_h * 60 + open_m
    close_t = close_h * 60 + close_m
    if close_t <= open_t:
        return FULL_DAY_WINDOW
    return TimeWindow(open_t, close_t)


# ============================================================
# 3. 饭点惯例窗（与 check_meal_time 共读 critic.meal_windows 常量）
# ============================================================


def _meal_convention_windows(cuisine: str) -> Optional[list[TimeWindow]]:
    """按菜系推餐厅默认饭点窗；茶点类返回 None（表示"不受饭点约束"）。

    共读 `critic.meal_windows` 的常量——与 `check_meal_time` 判定同一组边界，
    保证"构造时给的窗"与"复检时的判定"不漂移（ADR-0010 D-1 单一真相源要求）。
    """
    if cuisine in TEAHOUSE_CUISINES:
        return None
    return [
        TimeWindow(LUNCH_START_MIN, LUNCH_END_MIN),
        TimeWindow(DINNER_START_MIN, DINNER_END_MIN),
        TimeWindow(SUPPER_START_MIN, SUPPER_END_MIN),
    ]


# ============================================================
# 4. 候选时间窗构建（含 pin 收窄接口）
#
# 【窗语义契约（D-2 调度器消费的唯一语义）】返回的窗一律表示**允许的开始时刻**：
# start ∈ 窗 ⇒ 该活动从 start 开呆满 duration 既不违反营业时间（整段停留在
# 营业窗内，对齐 check_opening_hours），也不违反饭点惯例（开始时刻在饭点窗内，
# 对齐 check_meal_time——它只判 start）。营业时间是「停留窗」，在此处用
# `end - duration` 换算成开始时刻窗；饭点窗/pin 天然就是开始时刻语义，直接求交。
# ============================================================


def _stay_to_start_window(
    stay: TimeWindow, duration_min: int
) -> Optional[TimeWindow]:
    """把「允许停留的时段」换算成「允许的开始时刻窗」：[start, end - duration]。

    停留窗宽度不足 duration → None（这个时段根本呆不满，如 90 分钟夜宵没法在
    22:00 打烊前从 21:30 开吃）；恰好等长 → 零宽窗（只此一个可行开始时刻）。
    """
    latest_start = stay.end_min - duration_min
    if latest_start < stay.start_min:
        return None
    return TimeWindow(stay.start_min, latest_start)


def build_poi_time_windows(
    poi: Poi, *, duration_min: int, pin: Optional[TimeWindow] = None
) -> list[TimeWindow]:
    """POI 的候选开始时刻窗：营业时间（物理约束，停留窗）按 duration 换算。

    `duration_min`：该活动的自然停留时长——营业窗是「整段停留必须落在其中」的
    物理约束（对齐 check_opening_hours），换算成开始时刻窗要收掉尾部 duration。

    `pin`：用户明确指定的开始时刻/窗（intent 层抽取归 D-7，本步只留接口）——与
    营业换算窗**求交**（不是覆盖）：物理约束不可协商，pin 落在打烊时段就是真实
    冲突，是否报告/如何取舍是 D-2 调度器 + D-7 advisory 通道的职责，本层只如实
    收窄（可能收窄到空列表，表示"这一时段物理不可行"）。
    """
    start_w = _stay_to_start_window(
        _opening_hours_window(poi.opening_hours), duration_min
    )
    windows = [start_w] if start_w is not None else []
    if pin is not None:
        windows = _intersect_many(windows, [pin])
    return windows


def build_restaurant_time_windows(
    rest: Restaurant, *, duration_min: int, pin: Optional[TimeWindow] = None
) -> list[TimeWindow]:
    """餐厅的候选开始时刻窗：饭点惯例（start 语义）∩ 营业时间换算窗。

    - 营业时间是「整段用餐必须落在其中」的停留窗 → 先按 duration 换算成开始
      时刻窗（防止排出"打烊前吃不完"的开始时刻，对齐 check_opening_hours）。
    - 饭点窗本来就是开始时刻语义（check_meal_time 只判 start），直接与之求交。
    - `pin`（用户明说"6 点吃饭"=开始时刻）**取代**饭点惯例默认（ADR-0010 决策 2
      "用户明确需求在这层覆盖默认"），但仍与营业换算窗求交——物理事实 pin 无法
      覆盖（打烊了就是打烊了，冲突交给 D-2/D-7 处理）。
    - 无 pin 时：饭点惯例窗（午/晚/夜宵，茶点类除外）∩ 营业换算窗；某个惯例窗
      与之无交集时自然丢弃（如只在傍晚营业的餐厅没有午餐窗、21:00 起的夜宵在
      22:00 打烊的店呆不满 90 分钟）。
    """
    start_w = _stay_to_start_window(
        _opening_hours_window(rest.opening_hours), duration_min
    )
    if start_w is None:
        return []
    if pin is not None:
        return _intersect_many([start_w], [pin])

    convention = _meal_convention_windows(rest.cuisine)
    if convention is None:  # 茶点类：不受饭点约束，只受营业换算窗约束
        return [start_w]
    return _intersect_many(convention, [start_w])


# ============================================================
# 5. Visit（均质活动值对象）
# ============================================================


@dataclass(frozen=True)
class Visit:
    """均质活动（visit）——POI 和餐厅统一表示为"一次访问"（ADR-0010 决策 1）。

    D-1 只产出这个值对象，不消费它——D-2（调度器）按 windows+duration_min 排
    时刻，D-4（贪心构造）按 base_score/边际分选子集，都是后续步骤的事。

    字段：
    - kind: "poi" | "restaurant"
    - target_id: 对应 `Poi.id` / `Restaurant.id`
    - duration_min: 自然时长（分钟）。POI 经 `get_duration_for_companions` 投影
      `SuggestedDuration`；餐厅直接用 `typical_dining_min`。**未夹年龄 cap**——
      ADR-0010 D-3 才做 `min(suggested, cap)` 的夹紧（本字段是"夹紧前"的输入）。
    - windows: 候选**开始时刻**窗列表（可能不止一个——餐厅天然有午/晚/夜宵三个
      不相交窗）。语义契约见「4. 候选时间窗构建」节头注释：start ∈ 窗 ⇒ 从
      start 呆满 duration_min 不违营业时间、开始时刻在饭点内——D-2 直接消费，
      无需再做窗语义换算。
    - base_score: 单活动 utility 基分（ADR 决策 6"甲"：额外可加性，不含路线级项）。
    - category: 多样性罚的类别标签（POI 用 `type`；餐厅用 `cuisine`）。
    - cost: 人均花费（元）——POI 起步价 `price_range[0]`（无则 0）；餐厅 `avg_price`。
      供 `route_budget_penalty` 累加用。
    - entity: 原始 Poi/Restaurant 引用（下游组装/复检要用真实字段，如坐标/名字）。
    """

    kind: str
    target_id: str
    duration_min: int
    windows: list[TimeWindow]
    base_score: float
    category: str
    cost: float
    entity: Union[Poi, Restaurant]


_DEFAULT_DURATION_FALLBACK_MIN = 60
"""防御性兜底：mock 数据里 suggested_duration_minutes/typical_dining_min 100% 填充，
正常不会触发；只防 schema 允许的 Optional=None 场景导致下游拿到非法 duration。"""


def _poi_natural_duration(poi: Poi, intent: IntentExtraction) -> int:
    projected = get_duration_for_companions(
        poi.suggested_duration_minutes, intent.companions if intent else []
    )
    return projected if projected is not None else _DEFAULT_DURATION_FALLBACK_MIN


def _restaurant_natural_duration(rest: Restaurant) -> int:
    return (
        rest.typical_dining_min
        if rest.typical_dining_min is not None
        else _DEFAULT_DURATION_FALLBACK_MIN
    )


def build_visit_from_poi(
    poi: Poi,
    intent: IntentExtraction,
    weights: PlanningWeights,
    *,
    semantic_scores: dict[str, float] | None = None,
    pin: Optional[TimeWindow] = None,
) -> Visit:
    """由候选 POI 构造一个 `Visit`（ADR-0010 D-1）。

    base_score 复用 `ils_planner._utility(poi, None, "", intent, weights,
    semantic_scores)` 的既有分量（rating/标签命中/age_range 惩罚/POI 语义分/
    social 匹配/cost——ADR 决策 6 列出的全部六项，`_utility` 的 poi-only 分支
    恰好逐项覆盖，不用重新推导公式、不会有转写误差）。

    唯一手动剔除的一项：`_utility` 内嵌的 `-0.5 * _overload_penalty(poi, intent)`
    （suggested_duration 是否超年龄 cap 的强惩罚）——这是"时长 vs 年龄 cap"的
    耦合，ADR-0010 把它记在 D-3（"年龄 cap 夹紧归 D-3，本步不做"），提前带进来
    会让 D-1 的 base_score 偷跑 D-3 才该管的语义。加回
    `0.5 * _overload_penalty(...)` 精确抵消这一项，不影响其余分量。

    fail_detail（`_utility` 第二个返回值）不使用——ADR 决策 5：可行性归 D-2
    调度器，D-1 的 utility 是纯打分。
    """
    score, _fail = _utility(poi, None, "", intent, weights, semantic_scores=semantic_scores)
    score += 0.5 * _overload_penalty(poi, intent)

    duration = _poi_natural_duration(poi, intent)
    return Visit(
        kind="poi",
        target_id=poi.id,
        duration_min=duration,
        windows=build_poi_time_windows(poi, duration_min=duration, pin=pin),
        base_score=score,
        category=poi_category(poi),
        cost=float(poi.price_range[0]) if poi.price_range else 0.0,
        entity=poi,
    )


def build_visit_from_restaurant(
    rest: Restaurant,
    intent: IntentExtraction,
    weights: PlanningWeights,
    *,
    pin: Optional[TimeWindow] = None,
) -> Visit:
    """由候选餐厅构造一个 `Visit`。

    base_score 复用 `ils_planner._utility(None, rest, "", intent, weights)` 的
    既有分量（rating/dietary 标签命中/social 匹配/cost）。`_overload_penalty`
    只吃 `poi` 参数，这里传 `poi=None` 天然是 0，无需手动抵消——与 POI 分支的
    不对称处理由 `_utility` 自身签名决定，不是本函数遗漏。
    """
    score, _fail = _utility(None, rest, "", intent, weights)
    duration = _restaurant_natural_duration(rest)
    return Visit(
        kind="restaurant",
        target_id=rest.id,
        duration_min=duration,
        windows=build_restaurant_time_windows(rest, duration_min=duration, pin=pin),
        base_score=score,
        category=restaurant_category(rest),
        cost=float(rest.avg_price),
        entity=rest,
    )


# ============================================================
# 6. 类别信号（多样性罚用）
# ============================================================


def poi_category(poi: Poi) -> str:
    """POI 多样性罚的类别标签：取 `Poi.type`（如"猫咖"/"KTV"/"电影院"）。

    调研过 `tags` 字段的可行性并放弃：`tags` 是控制词典（`schemas/tags.py` 的
    22 个物理/体验标签）的多值集合，同类场馆常出现不同 tag 组合（如"展览"与
    "亲子博物馆"都含"室内"但主打标签不同），用 tag 交集判"同类"需要一个任意
    阈值、容易误判/漏判。`type` 虽是自由文本、粒度不一（mock 目录 51 个 POI 里
    47 个不同 type），但恰好精确覆盖 ADR 诊断的真实同质池风险——mock 数据里
    真正"同款扎堆"的案例（猫咖 P022/P023、剧本杀 P024/P025、KTV P026/P027、
    电影院 P028/P029、室内运动馆 P034/P035）全部是 `type` 完全相同；换 tags
    交集反而抓不准这几组真实重复。且 `type` 精确匹配无需调参（不像 tag-overlap
    阈值），是本步"轻量"多样性罚更稳的信号来源。
    """
    return poi.type


def restaurant_category(rest: Restaurant) -> str:
    """餐厅多样性罚的类别标签：取 `Restaurant.cuisine`（菜系）。

    与 POI 同理，但 `cuisine` 本身就是较低基数的准控制词典（mock 目录 51 家
    餐厅仅 16 种菜系，对比 POI `type` 47/51 接近全不同），比 POI 更适合直接
    当类别键，进一步印证"类型字段"是两侧都该选的来源。
    """
    return rest.cuisine


# ============================================================
# 7. 候选池扩容 / 分层取样（ADR-0010 D-1 明记）
# ============================================================

ROUTE_POOL_TOP_K = _env_int("PLANNER_ROUTE_POOL_TOP_K", 15)
"""路线构造备池大小——显著大于 `ils_planner.CANDIDATE_TOP_K`(=5)。ADR-0010 D-1
原话："单口味搜索 top-5 会给出同质池（5 个展馆），多样性罚无米下锅"；扩容 +
按类别分层取样让池里能同时出现"馆+园+咖啡"级别的多样性，供 D-4 的路线构造挑。"""


def build_route_candidate_pool(
    candidates: Sequence[T],
    *,
    category_of: Callable[[T], str],
    top_k: int = ROUTE_POOL_TOP_K,
) -> list[T]:
    """按类别分层轮转取样，扩容/去同质化搜索结果，供路线构造消费。

    **不改** `_query_pois`/`_query_restaurants` 本身（它们还有 ILS 单活动路径等
    其它消费者）——本函数只对**它们的输出**做池准备（ADR-0010 D-1 铁律）。

    算法：按输入原有顺序（假定已是相关性/评分排序，即调用方传入的 tool 返回顺序）
    把候选分组到各自类别桶，再跨桶轮转取样——每一轮依次从每个类别桶里取下一个
    未取过的候选，直到取满 `top_k` 或所有桶耗尽。这样"馆+园+咖啡"这类跨类别
    多样性优先于"同类扎堆"，同时保留组内原有相对排序。

    候选池不大于 `top_k` 时原样返回（不足以谈"扩容"，直接全给）。
    """
    if len(candidates) <= top_k:
        return list(candidates)

    buckets: dict[str, list[T]] = {}
    order: list[str] = []
    for c in candidates:
        cat = category_of(c)
        if cat not in buckets:
            buckets[cat] = []
            order.append(cat)
        buckets[cat].append(c)

    pointers = {cat: 0 for cat in order}
    out: list[T] = []
    while len(out) < top_k:
        progressed = False
        for cat in order:
            p = pointers[cat]
            if p < len(buckets[cat]):
                out.append(buckets[cat][p])
                pointers[cat] = p + 1
                progressed = True
                if len(out) >= top_k:
                    break
        if not progressed:
            break  # 所有桶都耗尽
    return out


def build_poi_route_pool(
    candidates: Sequence[Poi], *, top_k: int = ROUTE_POOL_TOP_K
) -> list[Poi]:
    """`build_route_candidate_pool` 的 POI 便捷封装（类别键=`type`）。"""
    return build_route_candidate_pool(candidates, category_of=poi_category, top_k=top_k)


def build_restaurant_route_pool(
    candidates: Sequence[Restaurant], *, top_k: int = ROUTE_POOL_TOP_K
) -> list[Restaurant]:
    """`build_route_candidate_pool` 的餐厅便捷封装（类别键=`cuisine`）。"""
    return build_route_candidate_pool(
        candidates, category_of=restaurant_category, top_k=top_k
    )


# ============================================================
# 8. 路线级 utility（ADR-0010 决策 6："甲=additive"）
# ============================================================


def route_commute_compactness(visits: Sequence[Visit]) -> float:
    """路线通勤紧凑度：相邻活动 distance_km 差的衰减均值 → (0,1]。

    沿用 `ils_planner._utility` 现有 smoothness 分量里 `smooth_distance` 的
    衰减思路（`math.exp(-inter_distance**2/4)`），但作用对象从"POI vs 餐厅"
    单一对子扩展到路线里每一对相邻活动——ADR 决策 6 把它从"单元组联合打分"里
    的一项升格为独立的"路线级"项，不再局限于恰好一 POI 一餐厅的旧模型。

    少于 2 个活动 → 1.0（中性满分；没有"相邻"可言，不应因活动数少被扣分）。
    """
    entities = [v.entity for v in visits]
    if len(entities) < 2:
        return 1.0
    decays = [
        math.exp(-(abs(a.distance_km - b.distance_km) ** 2) / 4)
        for a, b in zip(entities, entities[1:])
    ]
    return sum(decays) / len(decays)


DIVERSITY_REPEAT_PENALTY = _env_float("PLANNER_DIVERSITY_REPEAT_PENALTY", 0.15)
"""同类别第二次起，每次扣这么多（轻量，非硬约束）。ADR-0010 决策 6"轻量多样性罚"。"""


def route_diversity_penalty(visits: Sequence[Visit]) -> float:
    """路线内同类别第二个起扣分（ADR-0010 决策 6 的"轻量多样性罚"）。

    类别键见 `poi_category`/`restaurant_category`。第 1 次出现不罚，第 2/3/...
    次每次罚 `DIVERSITY_REPEAT_PENALTY`——足够轻量，不会让"同类别但确实都想去"
    的路线被判死，只作为平局时的偏好信号（沿 ADR 决策 6 "轻量"定性）。
    """
    counts: dict[str, int] = {}
    penalty = 0.0
    for v in visits:
        n = counts.get(v.category, 0)
        if n >= 1:
            penalty += DIVERSITY_REPEAT_PENALTY
        counts[v.category] = n + 1
    return penalty


def route_budget_penalty(visits: Sequence[Visit], budget: float) -> float:
    """路线总花费超预算的软罚（ADR-0010 决策 10"钱(软)"）。

    `budget` 由调用方传入——**数据来源判断点**：`IntentExtraction` 目前无预算
    字段，实际取值应来自 `UserProfile.default_budget`
    （`data.loader.load_user_profile().default_budget`，默认 300 元）。本函数
    刻意不自己加载 profile（保持纯函数、可测、不引入隐式 I/O），budget 从哪来
    由调用方（D-4）决定。

    衰减形状沿用 `_utility.cost_score` 的 exp 衰减风格（模块一贯做法）：未超
    预算 → 0（不罚）；超出比例越大罚分越接近 1 但不会真正到 1，留一点"超一点点
    也不是世界末日"的宽容度——这是"软"罚的本意，不做硬阈值一刀切。
    """
    if budget <= 0:
        return 0.0
    total_cost = sum(v.cost for v in visits)
    overage_ratio = max(0.0, (total_cost - budget) / budget)
    if overage_ratio <= 0:
        return 0.0
    return 1.0 - math.exp(-(overage_ratio ** 2) / 0.5)


def route_score(
    visits: Sequence[Visit],
    weights: PlanningWeights,
    budget: float,
) -> float:
    """路线级 utility：Σ每活动基分 + 路线级项（ADR-0010 决策 6"甲=additive"）。

    路线级项按语义就近挂到 LLM 4 权重上（复用既有权重，不新增维度——ADR 决策 6
    "LLM 权重照旧套新结构"）：
    - 通勤紧凑 + 多样性都是"路线读起来顺不顺"的同族关切 → 按 `weights.smoothness`
      定权重
    - 预算软罚 → 按 `weights.cost` 定权重（cost 维度本就管钱敏感度）
    """
    if not visits:
        return 0.0
    activity_total = sum(v.base_score for v in visits)
    compactness = route_commute_compactness(visits)
    diversity_penalty = route_diversity_penalty(visits)
    budget_penalty = route_budget_penalty(visits, budget)
    route_term = (
        weights.smoothness * (compactness - diversity_penalty)
        - weights.cost * budget_penalty
    )
    return activity_total + route_term


def marginal_score(
    visits_with: Sequence[Visit],
    visits_without: Sequence[Visit],
    weights: PlanningWeights,
    budget: float,
) -> float:
    """插入一个活动带来的边际分：`route_score(带它) - route_score(不带它)`。

    D-4（贪心插入构造）用它挑"插入哪个候选收益最大"，本步只提供 helper
    （ADR 决策 6 明确点名"插入用边际分挑"）。
    """
    return route_score(visits_with, weights, budget) - route_score(
        visits_without, weights, budget
    )
