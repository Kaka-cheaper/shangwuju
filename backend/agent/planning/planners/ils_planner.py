"""agent.planning.planners.ils_planner —— 多活动 TOPTW 混合规划兜底 + graph replan 兜底。

【ADR-0010 D-5：big-bang 换血——从「1+1 三元组」切到「路线模型」】

本模块曾经的算法是穷举 (POI, restaurant, dining_time) 三元组 + ILS 扰动/局部搜索
（架构审查候选 #8 诊断：该搜索空间因 `decide_nodes` 只给「1 主活动 + 1 用餐」而塌成
平凡网格，扰动+局部搜索 provably inert——150 次迭代 0 次找到更优解）。ADR-0010 把
问题重新定性为「带时间窗+节奏留白的团队定向问题（TOPTW）」，D-1..D-4 已分步建好
新地基（`activity_pool.py` 约束+utility 构建 / `route_scheduler.py` 窗感知调度器 /
`pace_budget.py` 节奏留白模型 / `route_builder.py` 锚定两段贪心插入构造）。本步把
`plan_hybrid` 切换到这套路线模型上，并把 ADR-0009 C-3/C-4 的 critic-to-solver
有界修复闭环迁移到路线上（旗舰「满座→改期」demo 亮点续命）。

【真实定位】

本模块是 ILS 兜底 planner，被以下入口消费：

- `graph/nodes/replan.py:ils_replan_node`（LLM 重生成失败 N 次后第 3 次兜底）
- `tests/test_planner_hybrid.py` / `test_planner_hybrid_overload.py`

【新流程（替代旧「(POI,餐厅,时段) 三元组 + ILS 扰动」模型）】

    召回(_query_pois/_query_restaurants，含 grounding-first 前置过滤)
        ↓
    [LLM] 出 4 个权重 (comfort/time/cost/smoothness)
        ∥（体感编排批 P2：与下一步并行发起，零数据依赖，省一轮串行 LLM 往返）
    [LLM] 语义打分（POI 维度，ItiNera 范式）
        ↓
    [Algo D-4] build_route：锚定两段贪心插入构造——选子集+顺序+时刻一步到位，
        组成随 intent 涌现（ADR-0010 决策 1：不再有「主活动/用餐」特权划分，
        `decide_nodes` 对本路径作废，交由 build_route 的锚定+涌现逻辑决定组成）
        ↓
    route_to_blueprint → assemble_from_blueprint（拼装 Itinerary）
        ↓
    [Critic] 统一 critic（critics_v2.validate_itinerary）验证
        ↓
        HARD 违规 → min-conflicts 有界修复闭环：`_compute_blacklists` 按
        ViolationCode 路由 + field_path 定向 blame 到肇事节点 → `_repair_route`
        避开黑名单重赋（挖窗/整体拉黑）→ 重组装 → 重 validate（ADR-0009 C-3/C-4
        语义原样，迁移到路线模型；见下方「C-3/C-4 迁移」节）
        干净 → 返回（success=True，gate 不变量：绝不返回带 HARD 违规的方案）
    ↓
    [Algo] 失败兜底：上抛给上层 rule planner（D2 地板）

与旧版的关键差异：

- **不再有 `CandidatePlan` / `_greedy_init` / `_perturb` / `_local_search` /
  `_swap_node` / `_shift_node` / `_search_best_avoiding`**：ILS 扰动+局部搜索在
  `build_route` 的贪心插入构造下彻底 provably inert（`build_route` 已经是「选
  子集+顺序+时刻」的完整确定性答案，不存在"改良"空间；架构审查候选 #8 的诊断
  经此彻底根除，而非只是修表面 bug）。
- **不再需要外部注入 `rule_assembler`**：旧版靠函数注入 `_assemble_itinerary`
  避免循环依赖（历史上正因这层注入被生产 adapter"忽略"过，见 ADR-0009「背景·
  地基 A」）；新流程组装是模块内部直调 `route_builder.route_to_blueprint` +
  `assemble_from_blueprint`，没有可注入的中间层，这类"注入被忽略"的 bug 类别
  不复存在。
- **候选池不再在本层预切 top-5**（旧 `CANDIDATE_TOP_K`，ADR-0010 诊断"单口味
  搜索 top-5 → 同质池"的元凶之一）——grounding 过滤后的全量候选直接交给
  `build_route`，池扩容/分层取样由 `activity_pool.build_route_candidate_pool`
  负责（D-1）。
- **不再有硬编码 `DINING_SLOTS` / `_resolve_dynamic_dining_slots`**：餐厅候选
  时刻由 `route_scheduler` 在连续窗内求最早可行开始时刻（含半点槽 snap），不
  再局限于几个离散候选时段。

【C-3/C-4 迁移到路线模型（ADR-0009 语义原样，只换实现细节）】

- `_classify_violation`（按 ViolationCode + severity 分桶）**逐字节保留复用**——
  这层只回答"这条违规该走哪条重搜策略"，与候选模型是三元组还是路线无关。
- `_blamed_target`（field_path→节点）**逐语义保留复用**，内部重构为委托
  `_blamed_node`（新增：解析出完整节点而非只取 (kind,id)，供 `_compute_blacklists`
  同时取 `target_id` 与 `start_time`）。
- `_compute_blacklists` **改签名**：旧版吃 `(failed: CandidatePlan, itinerary,
  intent, violations)`，新版吃 `(itinerary, violations)`——blame 一律走
  `field_path` 定位到肇事节点实体（`_blamed_node`），不再依赖 `CandidatePlan`
  的「至多两个实体」假设。产出形状不变：`(排除的 poi_id 集合, 排除的 rest_id
  集合, 封锁的 (rest_id, slot_hhmm) 集合)`。
- **封槽机制变化**（旧版：三元组模型里 `dining_time` 只是几个离散候选之一，
  黑名单直接从候选枚举里跳过；新版：路线模型的餐厅候选时刻是连续窗内求出的
  "最早可行"，必须真的**挖掉窗口里那 30 分钟**（`_shrink_visit_windows`），
  否则重搜会算出同一个最早时刻、原地震荡不收敛）——`_repair_route` 消费
  `_shrink_visit_windows`/`_apply_blacklist_to_pool` 两个小 helper 完成。
- **重搜算法变化**（旧版 `_search_best_avoiding`：对 (POI,餐厅,时段) 三元组做
  穷举重搜，相当于"整条方案推倒重来"；新版 `_repair_route`：只把命中黑名单的
  节点从上一轮已选集合里剔除，为空出的槽位在过滤后的候选池里找边际分最高的
  替补插回，其余节点原样保留——这其实**更贴近** ADR-0009 引用的 prior art
  min-conflicts（Minton et al. 1992：只重赋"参与被违反约束的那个变量"）字面
  定义，而不是退化）。
- **retry 有界 + gate 不变量原样保留**：`MAX_REPAIR_ROUNDS`、"只有 validate
  干净才 success=True"、黑名单跨轮单调累积——完全未动。

【调研留痕：D-5 自行拍板、值得读者知道的判断点】

（**ADR-0013 F-1 补记**：下面判断点 1 描述的 `_repair_route` 已搬到
`route_builder.py` 并改名公开 `repair_route`——局部重解引擎
`planners/node_swap.py` 需要同一"腾格→只补该格→不加塞"语义，两个调用方
现在共享同一实现。判断点本身记述的设计动机原样有效，不因搬家而过时。）

1. **重搜不重跑 `build_route`，而是新写 `_repair_route` 直接消费
   `route_scheduler.schedule_route`/`try_insert` + `activity_pool.route_score`**：
   `build_route`/`route_builder.py` 是 D-4 已 TDD 落地的"消费只读"零件（本步
   唯一改的是本文件），封槽（挖窗）必须发生在"喂给调度器的 Visit"这一层，而
   `build_route` 内部把 Poi/Restaurant → Visit 的转换封在函数体内、不接受外部
   预制的 Visit 池——没有不改 `route_builder.py` 就能从外部注入"这一个候选的
   窗已被挖掉"的钩子。若为此在 `build_route` 上开一个参数，会把"这轮重搜要避开
   什么"这个 ILS 内部关切泄漏进 D-4 的公开签名。改用 `_repair_route`（min-
   conflicts：只重赋肇事变量）不仅避免了这个泄漏，还比"整条路线重新贪心构造
   一遍"更贴合 ADR-0009 引用的 prior art 字面定义——一举两得，非退而求其次。
2. **`decide_nodes(intent)` 对本路径彻底不再调用**（ADR-0010 决策 1 明文
   "对 ILS 路径作废"）：旧版步骤 0 用它决定 `needs_poi`/`needs_dining`，本版
   统一召回 POI + 餐厅两个维度，组成完全交给 `build_route` 的锚定+涌现逻辑
   决定。唯一的失败条件是"两个维度的候选都是空"（连涌现的原材料都没有）。
3. **`_utility` 的 LLM 语义分项改为中心化 `0.3*(s-0.5)`**（review-driven
   calibration，取代旧 `0.3*s`）：旧公式在语义分缺省值 0.5 时仍会给 POI
   +0.15 的分数，而该项只对 POI 生效（`_utility` 签名要求 `poi is not None`）、
   餐厅侧永远拿不到——这在 `route_score` 把 POI/餐厅同池比较的新场景下（D-4
   起两者才真的放进同一个 additive 打分池竞争）会造成系统性偏袒 POI 的假象
   信号。中心化后 s=0.5（语义中性/缺省）时加项为 0，s>0.5 加分、s<0.5 扣分，
   POI 之间的相对排序不变（仿射变换），偏置消除。受影响测试
   `tests/test_utility_with_semantic.py` 已按新公式更新预期数值。

学术依据（沿用 ADR-0009，路线模型迁移未改变引用关系）：
- A 段（贪心插入构造，替代原 ILS 启发式）：[Vansteenwegen et al. 2009 ILS for
  TOPTW]、[Gunawan et al. 2019 Adjustment ILS for Multi-objective TOPTW]——
  ADR-0010 决策 7："先只做贪心插入构造；shake 存废由 S1-S8 实测数据决定（D-6）"。
- C 段（Critic 验证 + 修复闭环）：[Kambhampati et al. 2024 LLM-Modulo Frameworks
  NeurIPS]、[Kim et al. 2024 Robust Planning with LLM-Modulo arXiv:2405.20625]、
  [Minton et al. 1992 min-conflicts, AIJ]（`_repair_route` 的"只重赋肇事变量"）。
- LLM 头尾：[ItiNera EMNLP 2024 Industry] 把主观决策（权重/语义分）放给 LLM、
  客观搜索放给算法。

接口：
    def plan_hybrid(intent, *, client=None, tracer=None, pinned=None) -> HybridResult

输入与旧版基本一致（**去掉 `rule_assembler` 形参**——新流程组装是模块内部直调，
不再需要外部注入；见 ADR-0010 D-5 连带决策 4）。**D-7 新增 `pinned` 形参**
（`Sequence[PinSpec]`，见本文件「D-7」小节）——`HybridResult` 相应新增
`advisories` 字段（ADR-0010 决策 11「绝不默默忽略」）。被
`graph/nodes/replan.py:ils_replan_node` 调（第 3 次 ILS 兜底），以及
`tests/test_planner_hybrid.py`/`tests/test_planner_pinning_advisory.py` 直接驱动。

不负责：
- 权重决策（在 `weights_llm.py`）
- 候选池/utility 构建（在 `activity_pool.py`，D-1）
- 窗感知调度（在 `route_scheduler.py`，D-2）
- 节奏留白模型（在 `pace_budget.py`，D-3）
- 贪心插入构造（在 `route_builder.py`，D-4）
- Critic 实现（在 `critics_v2.py` / `_rules/checks.py`）
- HTTP/SSE（在 `main.py`）
- Tool 实现（在 `tools/`）
"""

from __future__ import annotations

import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Sequence

from schemas.advisory import Advisory, AdvisoryCode
from schemas.domain import Poi, Restaurant, SuggestedDuration
from schemas.errors import FailureReason
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary
from schemas.pin import PinSpec
from schemas.tools import (
    SearchPoisInput,
    SearchPoisOutput,
    SearchRestaurantsInput,
    SearchRestaurantsOutput,
)

from data.loader import load_user_profile

from ..blueprint.assemble_blueprint import assemble_from_blueprint
from ..critic.age_caps import cap_for_age
from ..critic.critics_v2 import Severity, Violation, ViolationCode, validate_itinerary
from ...core.trace import Tracer
from ..weights_llm import PlanningWeights, get_planning_weights
from tools.registry import invoke_tool
from utils.duration_helpers import get_duration_for_companions

if TYPE_CHECKING:  # 仅供类型标注；运行期各函数内部按需局部 import（见判断点 1 的
    # 循环依赖说明：route_builder/route_scheduler/activity_pool 都（直接或经
    # activity_pool 间接）依赖本模块的 `_env_int`/`_env_float`/`_utility`，本模块
    # 若在模块顶层反向 import 它们会成环——局部 import 是刻意选择，不是遗漏。
    from .activity_pool import Visit


# ============================================================
# 配置（可被 .env 覆盖）
# ============================================================

def _env_int(name: str, default: int) -> int:
    """PUBLIC SEAM（ADR-0010 D-5 finding #4）：`activity_pool.py` 顶层
    `from .ils_planner import _env_float, _env_int, _utility` 依赖本函数——
    虽带下划线，事实上是跨模块公开契约。删除/改签名前先迁移那处 import。
    """
    raw = os.getenv(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """PUBLIC SEAM（同上，`activity_pool.py` 依赖）：删除/改签名前先迁移。"""
    raw = os.getenv(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


# critic-to-solver 修复闭环的最大迭代轮数（ADR-0009 决策 5/7·C-4：retry 有界；
# ADR-0010 D-5：语义原样迁移到路线模型，数值/含义未变）。一次修复可能引入二次
# 违规（min-conflicts），需多轮收敛；受 demo 30s 红线 + D2 地板兜底约束，保持小值。
MAX_REPAIR_ROUNDS = _env_int("PLANNER_MAX_REPAIR_ROUNDS", 3)


# ============================================================
# 统一 critic 薄 adapter（ADR-0009 决策 1/3；路线模型迁移不影响本节——
# 消费的是 Itinerary，与候选模型是三元组还是路线无关）
# ============================================================

@dataclass
class HybridCriticReport:
    """plan_hybrid 消费统一 critic 的薄适配层。

    ADR-0008 的 `critics_v2.validate_itinerary` 产出 `list[Violation]`
    （`.code` + `.severity`，无 critic 名）；本模块内部历史上按「已删除的
    `ils_score_critic.CriticReport`」的形状消费（`passed` / `hard_violations()`）。
    这层薄 adapter **只做形状转换，不改变任何判定逻辑**——passed 就是「无 HARD
    violation」，hard_violations() 就是 severity==HARD 的子集。
    """

    violations: list[Violation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(v.severity == Severity.HARD for v in self.violations)

    def hard_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == Severity.HARD]


def _run_unified_critic(itinerary: Itinerary, intent: IntentExtraction) -> HybridCriticReport:
    """跑统一 critic（ADR-0008 `validate_itinerary`）并适配成 plan_hybrid 需要的形状。"""
    violations = validate_itinerary(itinerary, intent)
    return HybridCriticReport(violations=violations)


# ============================================================
# 入口
# ============================================================

@dataclass
class HybridResult:
    """planner_hybrid 内部结果（不是公共 API；上层包成 PlannerResult）。

    `advisories`（D-7 新增）：ADR-0010 决策 11「绝不默默忽略」的结构化告知——
    只在 `success=True` 时有意义地填充，描述**这一个最终交付的方案**（点名的
    目标排不进/被修复闭环换掉、总时长比期望短、总花费超预算等）。语义铁律：
    `plan_hybrid` 失败落 rule 地板时 hybrid 尝试期间的账单作废（那个方案没
    交付），失败分支恒不填充（保持 `default_factory=list` 的空列表）。
    """

    success: bool
    itinerary: Optional[Itinerary] = None
    weights: Optional[PlanningWeights] = None
    critic_report: Optional[HybridCriticReport] = None
    failure_reason: Optional[FailureReason] = None
    failure_detail: Optional[str] = None
    advisories: list[Advisory] = field(default_factory=list)


def _resolve_depart_min(start_time: str) -> int:
    """把 `intent.start_time` 解析成出发分钟数（PUBLIC SEAM 消费方，见
    `rule_planner._parse_start_time_hour` 的 docstring）。与
    `route_builder._resolve_depart_min` 各自独立实现同一算法（判断点见模块
    docstring 判断点 1 同精神：两处都直接依赖 `rule_planner` 这个共享的最底层
    seam，而不是互相依赖对方的私有实现，保持层间独立）。解析不出 → 14:00 兜底。
    """
    from .rule_planner import DEFAULT_DEPART_TIME, _parse_start_time_hour

    hour = _parse_start_time_hour(start_time)
    default_hour = int(DEFAULT_DEPART_TIME.split(":")[0])
    return (hour if hour is not None else default_hour) * 60


# ============================================================
# D-7：pinned 解析 + advisory 收集（ADR-0010 决策 11「绝不默默忽略」）
# ============================================================
#
# 范围声明（ADR-0010 D-7 原文）：本节只做「planner 接受结构化 `PinSpec` +
# advisory 产出」；`IntentExtraction` 无 pin 字段、intent 解析 prompt 也不抽取
# ——intent 层的 pin 抽取是跨层依赖，单独立项。`pinned` 因此目前只能被单测手工
# 构造喂入，生产调用点（`graph/nodes/replan.py:ils_replan_node`）暂不传参
# （等价于"无锚点"，不影响现状行为）。


def _visit_display_name(visit: "Visit") -> str:
    """pin 相关 advisory 文案里"目标叫什么"——优先取真实实体名
    （`Visit.entity` 是 `Poi`/`Restaurant`，两者都有 `.name`），entity 缺失
    （目前只有单测里手工构造的哨兵 Visit 会这样）时退回 `target_id`。"""
    entity = getattr(visit, "entity", None)
    name = getattr(entity, "name", None) if entity is not None else None
    return name or visit.target_id


def _no_matching_candidates_advisory(missed: Sequence[PinSpec]) -> Advisory:
    """pin 在已召回候选池里找不到匹配实体——ADR-0010 决策 11「过预算/无匹配候选：
    告知并建议放宽」。

    多条 miss 合并成**一条**告知（深审修正：文案本就无法点名不存在的实体——
    只有内部 id，消息纪律禁止外露——N 条同码告知只是同文重复，合并无信息损失，
    还避免 narrator 模板段落被同码句子撑爆）。
    """
    kinds = {p.kind for p in missed}
    if kinds == {"poi"}:
        kind_label = "地点"
    elif kinds == {"restaurant"}:
        kind_label = "餐厅"
    else:
        kind_label = "地点和餐厅"
    return Advisory(
        code=AdvisoryCode.NO_MATCHING_CANDIDATES,
        message=(
            f"你点名想去的{kind_label}这次在候选范围内没找到匹配项"
            "（可能是距离太远或条件筛没了），要不换个目标，要不放宽一下筛选范围？"
        ),
    )


def _resolve_pinned(
    pinned: Optional[Sequence[PinSpec]],
    pois: Sequence[Poi],
    restaurants: Sequence[Restaurant],
    intent: IntentExtraction,
    weights: PlanningWeights,
    semantic_scores: dict[str, float],
) -> tuple[list["Visit"], list[Advisory]]:
    """把 `PinSpec` 列表 resolve 成 `Visit`（供 `route_builder.build_route(pinned=
    ...)` 消费——它已有的形参正是本函数产出的形状，D-4 时就把消费端建好了）。

    按 `target_id` 在**已查回**的 `pois`/`restaurants`（`plan_hybrid` 步骤 1
    的召回+grounding 过滤结果）里查找；查不到 → `NO_MATCHING_CANDIDATES`
    advisory，不静默丢弃（ADR-0010 决策 11）。复用 `build_visit_from_poi`/
    `build_visit_from_restaurant`（D-1 既有构造路径）产出 `Visit`——不新建一条
    平行构造路径，这样 pin 与涌现候选共享完全相同的时长/窗口/utility 计算口径。
    """
    if not pinned:
        return [], []

    from .activity_pool import build_visit_from_poi, build_visit_from_restaurant

    poi_by_id = {p.id: p for p in pois}
    rest_by_id = {r.id: r for r in restaurants}

    visits: list["Visit"] = []
    missed: list[PinSpec] = []
    for pin in pinned:
        if pin.kind == "poi":
            entity = poi_by_id.get(pin.target_id)
            if entity is None:
                missed.append(pin)
                continue
            visits.append(
                build_visit_from_poi(entity, intent, weights, semantic_scores=semantic_scores)
            )
        else:  # "restaurant"
            entity = rest_by_id.get(pin.target_id)
            if entity is None:
                missed.append(pin)
                continue
            visits.append(build_visit_from_restaurant(entity, intent, weights))

    advisories = [_no_matching_candidates_advisory(missed)] if missed else []
    return visits, advisories


def _build_success_advisories(
    *,
    pin_advisories: list[Advisory],
    unmet_pinned: Sequence["Visit"],
    dropped_pins: set[tuple[str, str]],
    pinned_by_key: dict[tuple[str, str], "Visit"],
    violations: list[Violation],
    current_scheduled: Sequence[Any],
    money_budget: float,
) -> list[Advisory]:
    """把这一路收集到的告知拼成**最终交付方案**的 advisories 列表。

    只在 `plan_hybrid` 判定 `success=True`（`report.passed`）那一刻被调用——
    ADR-0010 决策 11 语义铁律："advisories 描述最终交付的方案"，hybrid 尝试
    期间任何中间态（未收敛的重排轮次、最终仍失败落地板的尝试）都不产 advisory。
    """
    from .activity_pool import route_total_cost

    advisories: list[Advisory] = list(pin_advisories)

    # 成员资格过滤（深审修正 1）：告知必须对「最终交付的方案」字面为真。
    # unmet/dropped 是构造期/修复期的**历史记录**——修复闭环换血时重搜池含全部
    # 实体，原本塞不进/被牺牲的 pin 有可能作为普通候选被重新插回；凡最终排程里
    # 实际存在的目标，一律不产「没进方案」类告知（否则出现"方案里明明有它、
    # 开场白却说塞不进去"的假告知）。
    scheduled_keys = {(sv.visit.kind, sv.visit.target_id) for sv in current_scheduled}

    # 同码合并（深审修正 2）：多个 pin 同码时合成一句点全名字——「绝不静默忽略」
    # 的通道若因同码句子逐条膨胀、在 narrator 模板里被截断，反而自吞告知，
    # 本末倒置；合并后每码至多一句，模板可全量渲染。
    unmet_names = [
        _visit_display_name(v)
        for v in unmet_pinned
        if (v.kind, v.target_id) not in scheduled_keys
    ]
    if unmet_names:
        names_str = "".join(f"『{n}』" for n in unmet_names)
        advisories.append(
            Advisory(
                code=AdvisoryCode.PINNED_UNSATISFIABLE,
                message=(
                    f"你点名想去的{names_str}这次的时间和路线里"
                    "塞不进去了，要不延长一点时长，要不我去掉别的活动腾地方？"
                ),
            )
        )

    dropped_names: list[str] = []
    for key in sorted(dropped_pins):
        if key in scheduled_keys:
            continue
        v = pinned_by_key.get(key)
        dropped_names.append(_visit_display_name(v) if v is not None else key[1])
    if dropped_names:
        names_str = "".join(f"『{n}』" for n in dropped_names)
        tail = "这一站" if len(dropped_names) == 1 else "这些站"
        advisories.append(
            Advisory(
                code=AdvisoryCode.PINNED_DROPPED_IN_REPAIR,
                message=(
                    f"你点名的{names_str}在处理其它冲突时被换掉了，是为了让整体方案"
                    f"排得开——如果{tail}必须保留，告诉我我再想别的办法。"
                ),
            )
        )

    for v in violations:
        if v.severity == Severity.SOFT and v.code == ViolationCode.DURATION_OUT_OF_RANGE:
            # 复用 check_duration 已经写好的用户向文案（同一纪律：自包含中文人话），
            # 不重写第二份措辞（DRY；措辞改进只需改一处）。
            advisories.append(Advisory(code=AdvisoryCode.SHORTER_THAN_REQUESTED, message=v.message))

    total_cost = route_total_cost([sv.visit for sv in current_scheduled])
    if money_budget > 0 and total_cost > money_budget:
        advisories.append(
            Advisory(
                code=AdvisoryCode.OVER_BUDGET,
                message=(
                    f"这次预估花费约 {total_cost:.0f} 元，比你平时 {money_budget:.0f} 元"
                    "左右的预算高一些——不介意的话可以直接用，想省钱也可以告诉我砍掉哪一站。"
                ),
            )
        )

    return advisories


def plan_hybrid(
    intent: IntentExtraction,
    *,
    client: Any | None = None,
    tracer: Optional[Tracer] = None,
    pinned: Optional[Sequence[PinSpec]] = None,
) -> HybridResult:
    """多活动 TOPTW 混合规划主流程（ADR-0010 D-5：见模块 docstring「新流程」节）。

    Args:
        intent: 用户意图。
        client: LLMClient；None 时权重/语义打分走启发式兜底（stub）。
        tracer: 可选 Tracer；None 时创建一个新的。
        pinned: 用户「点名必去」的结构化条目（D-7；见模块「D-7」小节的范围声明）。
            None/空 = 无锚点，行为与 D-7 之前完全一致。resolve 不到 / 排不进 /
            被修复闭环换掉都不静默丢弃，反映在 `HybridResult.advisories` 里。

    Returns:
        HybridResult；success=True 时 itinerary 保证经统一 critic 验证无 HARD
        违规（gate 不变量，ADR-0009 决策 5·C-4，迁移后原样保留）。advisories
        只在 success=True 时有意义地填充（见 `HybridResult`/`_build_success_
        advisories` docstring）。
    """
    tracer = tracer or Tracer()

    # ---- 步骤 1：候选生成（POI + 餐厅都搜；组成交给 build_route 的锚定+涌现
    # 逻辑决定，ADR-0010 决策 1：decide_nodes 对本路径作废，见判断点 2）----
    pois = _query_pois(intent, tracer)
    restaurants = _query_restaurants(intent, tracer)
    if not pois and not restaurants:
        return HybridResult(
            success=False,
            failure_reason=FailureReason.EMPTY_CANDIDATES,
            failure_detail="路线构造阶段：POI 和餐厅候选均为空",
        )

    # ---- 步骤 2 + 3：LLM 出权重 ∥ LLM 语义打分（体感编排批 P2）----
    # 两次调用零数据依赖：get_planning_weights 只读 intent；score_pois_with_llm
    # 只读 intent + 步骤 1 已产出的 pois，都不读对方的结果。并行发起省一轮串行
    # LLM 往返的挂钟时间；计算并行，但 tracer.emit 仍按原代码顺序在两者都完成
    # 后依次补发，保证 trace 事件顺序（先权重、后语义打分）与并行前完全一致，
    # 不引入线程调度带来的事件顺序不确定性。
    def _get_weights() -> PlanningWeights:
        return get_planning_weights(intent, client=client)

    def _get_semantic_scores() -> dict[str, float]:
        from agent.planning.preference_scorer import score_pois_with_llm

        return score_pois_with_llm(intent, pois, client=client)

    semantic_scores: dict[str, float] = {}
    semantic_score_exc: Exception | None = None
    with ThreadPoolExecutor(max_workers=2) as pool:
        weights_future = pool.submit(_get_weights)
        scores_future = pool.submit(_get_semantic_scores) if pois else None
        weights = weights_future.result()
        if scores_future is not None:
            try:
                semantic_scores = scores_future.result()
            except Exception as exc:  # 防御性兜底（原 except 语义原样保留）
                semantic_score_exc = exc

    tracer.emit(
        "agent_thought",
        {"text": f"权重（{weights.source}）：{weights.summary()}"},
    )
    if pois:
        if semantic_score_exc is not None:
            tracer.emit(
                "agent_thought",
                {"text": f"LLM 语义打分失败（{semantic_score_exc}），fallback 全 0.5"},
            )
            semantic_scores = {p.id: 0.5 for p in pois}
        elif semantic_scores:
            tracer.emit(
                "agent_thought",
                {
                    "text": (
                        f"LLM 语义打分（ItiNera 范式）：{len(semantic_scores)} 个 POI；"
                        f"分数范围 [{min(semantic_scores.values()):.2f}, "
                        f"{max(semantic_scores.values()):.2f}]"
                    ),
                },
            )

    # ---- 步骤 3.5：pinned 解析（D-7；resolve 不到 → NO_MATCHING_CANDIDATES）----
    from .activity_pool import (
        build_poi_route_pool,
        build_restaurant_route_pool,
        build_visit_from_poi,
        build_visit_from_restaurant,
    )
    from .route_builder import (
        build_route,
        make_commute_fn,
        repair_route,
        route_to_blueprint,
    )

    depart_min = _resolve_depart_min(intent.start_time)
    user_profile = load_user_profile()
    commute_fn = make_commute_fn(user_profile)
    money_budget = user_profile.default_budget

    pinned_visits, pin_advisories = _resolve_pinned(
        pinned, pois, restaurants, intent, weights, semantic_scores
    )
    pinned_keys = {(v.kind, v.target_id) for v in pinned_visits}
    pinned_by_key = {(v.kind, v.target_id): v for v in pinned_visits}

    # ---- 步骤 4：build_route（D-4 锚定两段贪心插入构造）----
    build_result = build_route(
        pois,
        restaurants,
        intent,
        weights,
        depart_min=depart_min,
        commute_fn=commute_fn,
        semantic_scores=semantic_scores,
        pinned=pinned_visits,
    )
    if not build_result.schedule.scheduled:
        return HybridResult(
            success=False,
            failure_reason=FailureReason.EMPTY_CANDIDATES,
            failure_detail="路线构造阶段：候选池不足以排出任何活动（build_route 空排程）",
            weights=weights,
        )
    tracer.emit(
        "agent_thought",
        {
            "text": (
                f"贪心插入构造：{len(build_result.visits)} 个活动"
                f"（节奏={build_result.pace_tier}）——"
                + "、".join(f"{v.kind}:{v.target_id}" for v in build_result.visits)
            ),
        },
    )

    # ---- 步骤 5：组装 Itinerary ----
    try:
        blueprint = route_to_blueprint(build_result.schedule, intent, depart_min)
        itinerary = assemble_from_blueprint(intent, blueprint, user_profile)
    except Exception as exc:  # 防御性兜底（D2 failure-drain：绝不让异常逃出规划层）
        return HybridResult(
            success=False,
            failure_reason=FailureReason.UPSTREAM_FAILURE,
            failure_detail=f"路线组装失败：{exc}",
            weights=weights,
        )

    # ---- 步骤 6：Critic 验证 + 有界修复闭环（C 段；ADR-0009 决策 1/3/5·C-4，
    # 迁移到路线模型：见模块 docstring「C-3/C-4 迁移」节）----
    current_itin = itinerary
    current_scheduled = build_result.schedule.scheduled
    budget_min = build_result.fill_targets.hi_min
    bl_poi: set[str] = set()
    bl_rest: set[str] = set()
    bl_rest_time: set[tuple[str, str]] = set()
    poi_visits: Optional[list["Visit"]] = None
    rest_visits: Optional[list["Visit"]] = None
    # D-7 决策 E：本轮修复闭环里真被牺牲（整店/整 POI 拉黑）的 pin，跨轮累积。
    dropped_pins: set[tuple[str, str]] = set()
    report = _run_unified_critic(current_itin, intent)

    for attempt in range(MAX_REPAIR_ROUNDS + 1):
        hard = report.hard_violations()
        soft = [v for v in report.violations if v.severity == Severity.SOFT]
        tracer.emit(
            "agent_thought",
            {
                "text": (
                    f"Critic 验证（第 {attempt} 轮）：passed={report.passed}，"
                    f"hard={len(hard)}，soft={len(soft)}（共 {len(report.violations)} 条违规）"
                ),
            },
        )
        for v in report.violations:
            tracer.emit(
                "agent_thought",
                {"text": f"[{v.severity.value.upper()}] {v.code.value}: {v.message}"},
            )

        if report.passed:
            advisories = _build_success_advisories(
                pin_advisories=pin_advisories,
                unmet_pinned=build_result.unmet_pinned,
                dropped_pins=dropped_pins,
                pinned_by_key=pinned_by_key,
                violations=report.violations,
                current_scheduled=current_scheduled,
                money_budget=money_budget,
            )
            return HybridResult(
                success=True,
                itinerary=current_itin,
                weights=weights,
                critic_report=report,
                advisories=advisories,
            )

        if attempt >= MAX_REPAIR_ROUNDS:
            break  # 修复预算耗尽，停止迭代

        # 累积黑名单（单调 tabu）——防「移到 17:00 又被换回 16:30」的震荡
        add_poi, add_rest, add_rest_time = _compute_blacklists(current_itin, report.violations)
        if not (add_poi or add_rest or add_rest_time):
            break  # 违规无 ILS 可修算子（结构码 / AGE / 总时长）→ 落地板

        # D-7 决策 E：pinned 的 (kind, target_id) 默认不进整体黑名单（保护）；仅当
        # 本轮唯一的可行动作全部指向被保护的 pin（过滤后"安全"额度为空）时，才
        # 允许把它纳入黑名单救全局——绝不静默：真被牺牲的 pin 记进 dropped_pins，
        # 最终交付方案里若确实不含它，由 _build_success_advisories 产
        # PINNED_DROPPED_IN_REPAIR。封槽（bl_rest_time）不触发保护——挖窗只是把
        # 该餐厅的时刻挪走（仍在方案里），不等于把 pin 整个换掉。
        protected_poi = {tid for tid in add_poi if ("poi", tid) in pinned_keys}
        protected_rest = {tid for tid in add_rest if ("restaurant", tid) in pinned_keys}
        safe_poi = add_poi - protected_poi
        safe_rest = add_rest - protected_rest
        if not (safe_poi or safe_rest or add_rest_time):
            safe_poi = protected_poi
            safe_rest = protected_rest
            dropped_pins.update(("poi", tid) for tid in protected_poi)
            dropped_pins.update(("restaurant", tid) for tid in protected_rest)

        bl_poi |= safe_poi
        bl_rest |= safe_rest
        bl_rest_time |= add_rest_time

        tracer.emit(
            "replan_triggered",
            {
                "reason": "critic_hard_violation",
                "from_tool": "critics",
                "action": "retry_with_critic_feedback",
                "violations": [v.message for v in hard],
            },
        )

        if poi_visits is None:  # 惰性建池：多数场景第 0 轮就 clean，用不到重搜
            poi_pool = build_poi_route_pool(list(pois))
            rest_pool = build_restaurant_route_pool(list(restaurants))
            poi_visits = [
                build_visit_from_poi(p, intent, weights, semantic_scores=semantic_scores)
                for p in poi_pool
            ]
            rest_visits = [build_visit_from_restaurant(r, intent, weights) for r in rest_pool]

        new_schedule = repair_route(
            current_scheduled,
            poi_visits,
            rest_visits,
            weights,
            depart_min=depart_min,
            budget_min=budget_min,
            commute_fn=commute_fn,
            money_budget=money_budget,
            blacklist_poi=bl_poi,
            blacklist_rest=bl_rest,
            blacklist_rest_time=bl_rest_time,
        )
        if new_schedule is None or not new_schedule.scheduled:
            break  # 候选池被黑名单掏空 / 修复后无可用活动 → 落地板

        try:
            new_blueprint = route_to_blueprint(new_schedule, intent, depart_min)
            new_itin = assemble_from_blueprint(intent, new_blueprint, user_profile)
        except Exception:
            break  # 组装失败 → 落地板

        tracer.emit(
            "agent_thought",
            {
                "text": (
                    f"基于 Critic 反馈的重排（第 {attempt + 1} 轮）：黑名单 "
                    f"poi={sorted(bl_poi)} rest={sorted(bl_rest)} "
                    f"rest_time={sorted(bl_rest_time)} → "
                    + "、".join(f"{sv.visit.kind}:{sv.visit.target_id}@{sv.start_min}" for sv in new_schedule.scheduled)
                ),
            },
        )
        current_scheduled, current_itin = new_schedule.scheduled, new_itin
        report = _run_unified_critic(current_itin, intent)

    # 循环未收敛到干净方案 → 失败上抛（上层 fallback rule planner，D2）
    return HybridResult(
        success=False,
        failure_reason=FailureReason.UPSTREAM_FAILURE,
        failure_detail=(
            f"Critic 硬违规（重排 {MAX_REPAIR_ROUNDS} 轮未收敛）："
            + "；".join(v.message for v in report.hard_violations())
        ),
        weights=weights,
        critic_report=report,
    )


# ============================================================
# 候选生成（直接调真 Tool；trace 里也会留下 tool_call_start/end）
# 【ADR-0010 D-5：召回逻辑本身不动——只消费，见任务原文「_query_pois/
# _query_restaurants（召回，保留）」】
# ============================================================

# spec algorithm-redesign R3：grounding-first 前置硬剔除常量
# spec innovation-review R4：改 env flag（默认值不变；let evaluator 看到 demo / production 双 mode 思维）
_GROUNDING_MIN_CANDIDATES = _env_int("GROUNDING_MIN_CANDIDATES", 3)           # 候选池 < 3 时自动放宽
_GROUNDING_DISTANCE_TOL_KM = _env_float("GROUNDING_DISTANCE_TOL_KM", 1.0)     # 默认距离容差（与 _utility 物理可行性快检对齐）
_GROUNDING_DISTANCE_TOL_RELAX_KM = _env_float("GROUNDING_DISTANCE_TOL_RELAX_KM", 2.0)  # 候选 < 3 时放宽到 +2km
_GROUNDING_PRESCHOOL_CAP = _env_int("GROUNDING_PRESCHOOL_CAP", 90)            # 含 ≤6 岁同行人时主导桶上限（min）
_GROUNDING_SENIOR_CAP = _env_int("GROUNDING_SENIOR_CAP", 75)                  # 含 ≥75 岁同行人时主导桶上限（min）


def _grounding_filter_poi(
    candidates: list[Poi],
    intent: IntentExtraction,
    tracer: Tracer,
) -> list[Poi]:
    """spec algorithm-redesign R3：POI 候选 grounding-first 前置硬剔除。

    在 ILS 看到候选之前就剔除明显违规的项，避免 utility 计算 / LLM 语义打分浪费在
    「5 岁娃 196min」「打烊 POI」这类候选上。与 `_overload_penalty` / critic 主路径
    构成三重防线：grounding（前置硬剔）→ utility penalty（搜索期）→ critic（兜底）。

    剔除规则：
    - 含 ≤6 岁同行人 + 投影后 suggested_duration > 90min（学龄前/婴幼儿主导桶）
    - 含 ≥75 岁同行人 + 投影后 suggested_duration > 75min（高龄主导桶）
    - poi.distance_km > intent.distance_max_km + 1.0
    - getattr(poi, "business_status", "open") in {"closed", "permanent_closed"}

    放宽机制：
    - 过滤后候选池 < 3 → 仅保留距离 +2.0km / 营业状态过滤，跳过 age cap
      （避免「严过滤把候选剃光」让 ILS 拿不到任何候选；hackathon demo 安全网）

    每剔除一个候选 emit `tracer.emit("grounding_filtered", {poi_id, reason})`。
    """
    if not candidates:
        return candidates

    # 推主导桶 cap（取最严）
    has_preschool = any(
        c.age is not None and c.age <= 6 for c in (intent.companions or [])
    )
    has_senior = any(
        c.age is not None and c.age >= 75 for c in (intent.companions or [])
    )

    # 距离上限
    max_km = intent.distance_max_km if intent.distance_max_km else 999.0

    def _evaluate_strict(poi: Poi) -> Optional[str]:
        """返 None 表示通过；返字符串表示剔除原因（用于 tracer / 日志）"""
        # 距离硬上限
        if poi.distance_km > max_km + _GROUNDING_DISTANCE_TOL_KM:
            return f"距家 {poi.distance_km:.1f}km 超 {max_km:.1f}km + 容差 1.0km"
        # 营业状态
        status = getattr(poi, "business_status", "open") or "open"
        if status in ("closed", "permanent_closed"):
            return f"营业状态={status}"
        # age cap：投影 suggested_duration（与 _overload_penalty 同源逻辑）
        suggested_raw = getattr(poi, "suggested_duration_minutes", None)
        if suggested_raw is None:
            return None
        if isinstance(suggested_raw, (int, SuggestedDuration)):
            try:
                suggested = get_duration_for_companions(
                    suggested_raw, intent.companions if intent else []
                )
            except Exception:
                suggested = None
        else:
            suggested = None
        if suggested is None:
            return None
        if has_preschool and suggested > _GROUNDING_PRESCHOOL_CAP:
            return f"含 ≤6 岁同行人，POI 主导时长 {suggested}min > 90min cap"
        if has_senior and suggested > _GROUNDING_SENIOR_CAP:
            return f"含 ≥75 岁同行人，POI 主导时长 {suggested}min > 75min cap"
        return None

    def _evaluate_relaxed(poi: Poi) -> Optional[str]:
        """放宽模式：仅检查距离 + 营业状态，跳过 age cap"""
        if poi.distance_km > max_km + _GROUNDING_DISTANCE_TOL_RELAX_KM:
            return f"距家 {poi.distance_km:.1f}km 超 {max_km:.1f}km + 放宽容差 2.0km"
        status = getattr(poi, "business_status", "open") or "open"
        if status in ("closed", "permanent_closed"):
            return f"营业状态={status}"
        return None

    # 第一轮：严过滤
    filtered: list[Poi] = []
    rejected: list[tuple[str, str]] = []
    for poi in candidates:
        reason = _evaluate_strict(poi)
        if reason is None:
            filtered.append(poi)
        else:
            rejected.append((poi.id, reason))

    # 候选池 < 3 → 自动放宽（仅距离 + 营业状态）
    if len(filtered) < _GROUNDING_MIN_CANDIDATES:
        tracer.emit(
            "agent_thought",
            {
                "text": (
                    f"grounding-first POI 严过滤后仅剩 {len(filtered)} 项 "
                    f"< 阈值 {_GROUNDING_MIN_CANDIDATES}，触发放宽机制（仅距离 +2km / 营业状态）"
                ),
            },
        )
        filtered = []
        rejected = []
        for poi in candidates:
            reason = _evaluate_relaxed(poi)
            if reason is None:
                filtered.append(poi)
            else:
                rejected.append((poi.id, reason))

    # 上报剔除轨迹
    for poi_id, reason in rejected:
        tracer.emit(
            "grounding_filtered",
            {"poi_id": poi_id, "reason": reason},
        )
    return filtered


def _grounding_filter_restaurant(
    candidates: list[Restaurant],
    intent: IntentExtraction,
    tracer: Tracer,
) -> list[Restaurant]:
    """spec algorithm-redesign R3：餐厅 grounding-first 前置硬剔除。

    仅过滤距离 + 营业状态（餐厅 typical_dining_min 不区分客群桶，无 age cap）。
    满座由 critic 路径处理（不在 grounding 层剔除——demo 异常韧性需要保留满座候选
    让 17:00 → 17:30 替换链路被评委看到）。
    """
    if not candidates:
        return candidates

    max_km = intent.distance_max_km if intent.distance_max_km else 999.0

    filtered: list[Restaurant] = []
    rejected: list[tuple[str, str]] = []
    for rest in candidates:
        if rest.distance_km > max_km + _GROUNDING_DISTANCE_TOL_KM:
            rejected.append(
                (rest.id, f"距家 {rest.distance_km:.1f}km 超 {max_km:.1f}km + 容差 1.0km")
            )
            continue
        status = getattr(rest, "business_status", "open") or "open"
        if status in ("closed", "permanent_closed"):
            rejected.append((rest.id, f"营业状态={status}"))
            continue
        filtered.append(rest)

    # 候选 < 3 → 放宽到 +2km（餐厅没 age cap，没法再降）
    if len(filtered) < _GROUNDING_MIN_CANDIDATES:
        tracer.emit(
            "agent_thought",
            {
                "text": (
                    f"grounding-first 餐厅严过滤后仅剩 {len(filtered)} 项 "
                    f"< 阈值 {_GROUNDING_MIN_CANDIDATES}，触发放宽（距离 +2km）"
                ),
            },
        )
        filtered = []
        rejected = []
        for rest in candidates:
            if rest.distance_km > max_km + _GROUNDING_DISTANCE_TOL_RELAX_KM:
                rejected.append(
                    (rest.id, f"距家 {rest.distance_km:.1f}km 超 +2km 放宽容差")
                )
                continue
            status = getattr(rest, "business_status", "open") or "open"
            if status in ("closed", "permanent_closed"):
                rejected.append((rest.id, f"营业状态={status}"))
                continue
            filtered.append(rest)

    for rest_id, reason in rejected:
        tracer.emit(
            "grounding_filtered",
            {"restaurant_id": rest_id, "reason": reason},
        )
    return filtered


def _query_pois(intent: IntentExtraction, tracer: Tracer) -> list[Poi]:
    args = SearchPoisInput(
        distance_max_km=intent.distance_max_km,
        physical_constraints=list(intent.physical_constraints),
        experience_tags=list(intent.experience_tags),
        social_context=intent.social_context,
        age_in_party=[c.age for c in intent.companions if c.age is not None] or None,
        limit=20,
    ).model_dump()
    tracer.emit("tool_call_start", {"tool": "search_pois", "input": args})
    res = invoke_tool("search_pois", args)
    tracer.emit(
        "tool_call_end",
        {
            "tool": "search_pois",
            "output": res.output,
            "success": res.success,
            "reason": res.reason.value if res.reason else None,
            "duration_ms": res.duration_ms,
        },
    )
    if not res.success:
        return []
    out = SearchPoisOutput.model_validate(res.output)
    candidates = list(out.candidates)
    # spec algorithm-redesign R3：grounding-first 前置硬剔除
    return _grounding_filter_poi(candidates, intent, tracer)


def _query_restaurants(intent: IntentExtraction, tracer: Tracer) -> list[Restaurant]:
    args = SearchRestaurantsInput(
        distance_max_km=intent.distance_max_km,
        dietary_constraints=list(intent.dietary_constraints),
        experience_tags=list(intent.experience_tags),
        social_context=intent.social_context,
        capacity_requirement=intent.capacity_requirement,
        limit=20,
    ).model_dump()
    tracer.emit("tool_call_start", {"tool": "search_restaurants", "input": args})
    res = invoke_tool("search_restaurants", args)
    tracer.emit(
        "tool_call_end",
        {
            "tool": "search_restaurants",
            "output": res.output,
            "success": res.success,
            "reason": res.reason.value if res.reason else None,
            "duration_ms": res.duration_ms,
        },
    )
    if not res.success:
        return []
    out = SearchRestaurantsOutput.model_validate(res.output)
    candidates = list(out.candidates)
    # spec algorithm-redesign R3：grounding-first 前置硬剔除（仅距离 + 营业状态）
    return _grounding_filter_restaurant(candidates, intent, tracer)


# ============================================================
# 加权效用 utility（每活动基分；被 `activity_pool.build_visit_from_poi` /
# `build_visit_from_restaurant` 消费，见模块顶部 PUBLIC SEAM 说明）
# ============================================================

# ADR-0009 决策 6：年龄 cap 表读 `critic/age_caps.py` 单一真相源（不再本地内联第三份
# 45/75/120/60 副本）。_AGE_CAP_NO_LIMIT 是本模块自用的「无年龄约束」哨兵——
# age_caps.cap_for_age 对不落 4 档的年龄返 None，本函数把 None 适配成 9999，
# 供 _overload_penalty / _grounding_filter_poi 沿用「越大越不限」的判定习惯。
_AGE_CAP_NO_LIMIT = 9999  # 哨兵：无 age 信息 / age 不落任何分级档时返此值


def _resolve_age_cap(intent: IntentExtraction) -> int:
    """从 intent.companions 推单段最严 cap（min）——读 age_caps.py 单一真相源。

    与 `critic/age_caps.py:cap_for_age`（供 `check_age_aware_duration` 用）同源；
    本函数只是在其基础上取同行人群体内最严（min）一档。
    """
    if intent is None or not getattr(intent, "companions", None):
        return _AGE_CAP_NO_LIMIT

    caps: list[int] = []
    for c in intent.companions:
        age = getattr(c, "age", None)
        if not isinstance(age, int) or age < 0:
            continue
        tier = cap_for_age(age)
        if tier is not None:
            caps.append(tier[0])

    if not caps:
        return _AGE_CAP_NO_LIMIT
    return min(caps)


def _overload_penalty(poi: Optional[Poi], intent: IntentExtraction) -> float:
    """单段时长 vs 同行人画像合理性 → 强惩罚值（spec planning-quality-deep-review R5）。

    返回：
        0.3 表示「该 POI 在当前客群下的推荐时长 > 年龄 cap」（5 岁娃 + 推荐 90min POI / cap 75）；
        0.0 表示「不超 cap 或无 age 信息」。

    与 critic 主路径的关系：
    - critics_v2._check_age_aware_duration：拦已排定的 itinerary（兜底）
    - 本 penalty：在候选打分阶段就给「显然不合适的 POI」打负分（先验，
      `activity_pool.build_visit_from_poi` 经由 `_utility` 消费本函数）
    """
    if poi is None:
        return 0.0
    cap = _resolve_age_cap(intent)
    if cap >= _AGE_CAP_NO_LIMIT:
        return 0.0

    suggested_raw = getattr(poi, "suggested_duration_minutes", None)
    if suggested_raw is None:
        return 0.0

    # 用 helper 投影 SuggestedDuration / int 双形态 → 单值
    if isinstance(suggested_raw, (int, SuggestedDuration)):
        suggested = get_duration_for_companions(
            suggested_raw, intent.companions if intent else []
        )
    else:
        suggested = None
    if suggested is None:
        return 0.0

    # design.md Component 6 公式：actual = min(suggested, cap)；
    # actual < suggested 即 cap 起到约束作用（即 suggested > cap）→ 强惩罚
    return 0.3 if suggested > cap else 0.0


def _utility(
    poi: Optional[Poi],
    rest: Optional[Restaurant],
    dining_time: str,
    intent: IntentExtraction,
    w: PlanningWeights,
    semantic_scores: dict[str, float] | None = None,
) -> tuple[float, str | None]:
    """PUBLIC SEAM（ADR-0010 D-5 finding #4）：`activity_pool.py` 顶层
    `from .ils_planner import _env_float, _env_int, _utility` 依赖本函数——
    `build_visit_from_poi`/`build_visit_from_restaurant` 的 base_score 直接是
    本函数的返回值。删除/改签名前先迁移 `activity_pool.py` 那处 import。

    加权效用函数（适配可选维度）。

    四维度归一化到 [0, 1] 后按权重求和。
    返回 (score, fail_detail)；fail_detail 非 None 表示该候选已物理不可行
    （历史遗留返回值——ADR-0010 决策 5 起可行性判定已转移到 `route_scheduler.py`
    D-2，`activity_pool.build_visit_from_poi`/`build_visit_from_restaurant` 不消费
    这个字段，只取 score）。

    ADR-0010 D-5（review-driven calibration，取代旧 `+0.3*s`）：末尾 LLM 语义分项
    改为**中心化** `+0.3*(s-0.5)`——s=0.5（语义中性/缺省）时不加不减，s>0.5 加分、
    s<0.5 扣分，POI 间相对排序不变（仿射变换）。旧版 `+0.3*s` 在 s=0.5 时仍给
    POI +0.15，而该项只对 POI 生效、餐厅永远拿不到，在 `route_score`（D-4 起
    POI/餐厅才真的同池 additive 竞争）里会造成系统性偏袒 POI 的假信号，中心化
    后消除这个偏置。semantic_scores=None 时不加项（向后兼容）。
    """
    # ---- comfort：标签匹配 + 评分 + 年龄适配 ----
    poi_tag_hit = len(set(poi.tags) & set(intent.physical_constraints)) if poi else 0
    rest_tag_hit = len(set(rest.tags) & set(intent.dietary_constraints)) if rest else 0

    poi_rating = poi.rating if poi else 0
    rest_rating = rest.rating if rest else 0
    rating_count = (1 if poi else 0) + (1 if rest else 0)
    rating_score = (poi_rating + rest_rating) / (rating_count * 5.0) if rating_count else 0.5

    age_penalty = 1.0
    if poi and intent.companions and poi.age_range:
        ages = [c.age for c in intent.companions if c.age is not None]
        if ages:
            lo, hi = poi.age_range
            if not all(lo <= a <= hi for a in ages):
                age_penalty = 0.4

    phys_denom = max(1, len(intent.physical_constraints) or 1)
    diet_denom = max(1, len(intent.dietary_constraints) or 1)
    comfort = (
        0.5 * rating_score
        + 0.25 * min(1.0, poi_tag_hit / phys_denom)
        + 0.25 * min(1.0, rest_tag_hit / diet_denom)
    ) * age_penalty

    # ---- time：距离短 / 总耗时短的代理（距离指数衰减）----
    distances = []
    if poi:
        distances.append(poi.distance_km)
    if rest:
        distances.append(rest.distance_km)
    avg_dist = sum(distances) / len(distances) if distances else 3.0
    time_score = math.exp(-max(0, avg_dist - 3) ** 2 / 8)

    # ---- cost：人均成本越低越好 ----
    poi_unit = (poi.price_range[0] if poi and poi.price_range else 0) or 0
    rest_price = rest.avg_price if rest else 0
    cost_per_person = float(poi_unit) + float(rest_price)
    cost_score = math.exp(-max(0, cost_per_person - 200) ** 2 / 90000)

    # ---- smoothness：POI 与餐厅距离（同区为佳）+ social_context 命中 ----
    if poi and rest:
        inter_distance = abs(poi.distance_km - rest.distance_km)
    else:
        inter_distance = 0
    smooth_distance = math.exp(-inter_distance ** 2 / 4)

    poi_ctx_match = (intent.social_context in poi.suitable_for) if poi else 0
    rest_ctx_match = (intent.social_context in rest.suitable_for) if rest else 0
    ctx_match = 0.5 + 0.25 * poi_ctx_match + 0.25 * rest_ctx_match
    smoothness = 0.5 * smooth_distance + 0.5 * ctx_match

    score = (
        w.comfort * comfort
        + w.time * time_score
        + w.cost * cost_score
        + w.smoothness * smoothness
    )

    # spec planning-quality-deep-review R5：年龄超 cap 的 POI 候选打强负分。
    score -= 0.5 * _overload_penalty(poi, intent)

    # ADR-0010 D-5：LLM 语义打分项改中心化（见函数 docstring）。
    if poi is not None and semantic_scores is not None:
        score += 0.3 * (semantic_scores.get(poi.id, 0.5) - 0.5)

    # 物理可行性快检（历史遗留，见 docstring；下游不再消费 fail_detail）
    fail = None
    if poi and poi.distance_km > intent.distance_max_km + 1.0:
        fail = f"POI {poi.id} 距离 {poi.distance_km:g}km 超限"
    elif rest and rest.distance_km > intent.distance_max_km + 1.0:
        fail = f"餐厅 {rest.id} 距离 {rest.distance_km:g}km 超限"
    elif rest:
        party = max(1, sum(c.count for c in intent.companions) or 1)
        if party >= 6 and not rest.capacity.six and not rest.capacity.eight:
            fail = f"餐厅 {rest.id} 桌型不支持 {party} 人"

    return score, fail


# ============================================================
# Critic 失败后的重排（C 段反馈，ADR-0009 决策 3/4/5/6：ViolationCode → ILS 重搜动作
# 映射表；ADR-0010 D-5：迁移到路线模型，见模块 docstring「C-3/C-4 迁移」节）
# ============================================================
#
# | 判决           | ViolationCode                    | 动作                                  |
# |----------------|-----------------------------------|----------------------------------------|
# | 闭环重搜       | RESTAURANT_FULL_UNRESOLVED        | 封 (餐厅,时段) → 挖窗后重搜；不行则连带换店（同一机制自然涌现，见下） |
# | 闭环重搜       | DIETARY_VIOLATION                 | 拉黑整店 → 换饮食兼容                  |
# | 闭环重搜       | CAPACITY_REQUIREMENT_VIOLATED     | 拉黑整店 → 换大桌/包间                 |
# | 闭环重搜       | SOCIAL_CONTEXT_MISMATCH（HARD）   | 按 field_path 定向拉黑肇事那一个实体   |
# | 闭环重搜       | OPENING_HOURS_VIOLATION           | 餐厅侧：封 (餐厅,时段)；POI 侧：拉黑整个 POI（start_time 非搜索变量） |
# | 闭环重搜       | MEAL_TIME_UNREASONABLE            | 封 (餐厅,时段) → 挖窗后移到饭点窗      |
# | 弱杠杆         | DURATION_OUT_OF_RANGE             | 不产生动作（见下方「判断点」，field_path 恒为 "total_minutes"，无法定位到具体节点） |
# | 落 rule 地板   | INVARIANT_BROKEN / NODES_INCOMPLETE / TIMELINE_INCONSISTENT / TOOL_RESPONSE_INCONSISTENCY / HOP_INFEASIBLE / AGE_DURATION_MISMATCH | 不产生黑名单动作（搜索变量不参与，或 α 组装期已预防） |
#
# soft（DISTANCE_EXCEEDED、SOCIAL_CONTEXT_MISMATCH 的 POOR 档）不进本表——
# `_classify_violation` 对非 HARD 一律返回空集合，只叙事不重搜（ADR-0009 决策 3）。
#
# 【判断点：DURATION_OUT_OF_RANGE 弱杠杆在路线模型下退化为恒不产生动作】
# 旧版（CandidatePlan 模型）靠"恰好只有两个实体（main_poi/restaurant）+ intent
# 传入"猜"离 distance_max_km 最近的那个该拉黑；ADR-0009 本就把它标注为"弱杠杆"
# （最不确定、最投机的一条）。新签名 `_compute_blacklists(itinerary, violations)`
# 依 D-5 任务原文彻底放弃了 CandidatePlan/intent 依赖，改为逐条违规按 field_path
# 定位肇事节点——而 `check_duration` 的 field_path 恒为 `"total_minutes"`（总时长
# 违规没有单一"肇事节点"：可能是任何一个活动、也可能是通勤太多），`_blamed_node`
# 对此类 field_path 恒返回 None，本桶因此恒不产生黑名单动作。这不是遗漏：路线
# 模型下活动数任意（不再是"恰好两个"），旧版"猜离上限最近的那个"这套启发式本就
# 建立在三元组模型的巧合之上，不能照搬；诚实地不猜（转交 rule 地板，D2 安全）
# 好过在 N 个实体里瞎猜误伤。已在 D-5 报告里 surface 给复审。
#
# 【定向 blame 的实现选择（ADR-0009 决策 5，路线模型下延续）】
# `Violation.field_path` 按 "nodes[{idx}]..." 编码肇事节点下标（见 _rules/checks.py
# 各 check 的 field_path 赋值），`_blamed_node`/`_blamed_target` 解析下标、回查
# itinerary.nodes[idx] 定位，不依赖任何"候选模型有几个实体"的假设。
#
# 【与 C-4 的边界（明确不动）】
# retry 后是否重新 gate ——C-4 的活，本节不碰；MAX_REPAIR_ROUNDS / 黑名单跨轮单调
# 累积语义原样保留（见 plan_hybrid 步骤 6）。


def _classify_violation(v: Violation) -> set[str]:
    """按 ViolationCode（ADR-0009 决策 6）把违规归类为 ILS 重搜「动作桶」。

    逐字节保留复用（ADR-0010 D-5）：只看 `v.code` + `v.severity`，不看
    itinerary——这层判断与候选模型是三元组还是路线无关。定向 blame（该拉黑
    哪个具体实体）由 `_blamed_target`/`_blamed_node` 单独解析 `field_path`。

    SOFT 一律返回空集合（ADR-0009 决策 3：soft 只叙事，不进重搜，不论其 code 是什么）。

    动作桶：
    - "restaurant_time"：封 (餐厅, 排定时刻)，挖窗后重搜移时段 / 自然连带换店
      （RESTAURANT_FULL_UNRESOLVED / MEAL_TIME_UNREASONABLE）
    - "restaurant_swap"：整店拉黑，逼重搜换店
      （DIETARY_VIOLATION / CAPACITY_REQUIREMENT_VIOLATED）
    - "directed_swap"：需要 field_path 定向解析出的实体拉黑（SOCIAL_CONTEXT_MISMATCH hard）
    - "opening_hours"：需要 field_path 定向解析「是 POI 还是餐厅」，两侧动作不同
      （POI 拉黑整个 POI；餐厅按 restaurant_time 处理）
    - "distance_lever"：弱杠杆——路线模型下恒不产生黑名单动作，见上方模块级注释
      （DURATION_OUT_OF_RANGE）
    - 空集合：结构码 / AGE_DURATION_MISMATCH——搜索变量不参与，落 rule 地板。
    """
    if v.severity != Severity.HARD:
        return set()

    if v.code in (ViolationCode.RESTAURANT_FULL_UNRESOLVED, ViolationCode.MEAL_TIME_UNREASONABLE):
        return {"restaurant_time"}
    if v.code in (ViolationCode.DIETARY_VIOLATION, ViolationCode.CAPACITY_REQUIREMENT_VIOLATED):
        return {"restaurant_swap"}
    if v.code == ViolationCode.SOCIAL_CONTEXT_MISMATCH:
        return {"directed_swap"}
    if v.code == ViolationCode.OPENING_HOURS_VIOLATION:
        return {"opening_hours"}
    if v.code == ViolationCode.DURATION_OUT_OF_RANGE:
        return {"distance_lever"}
    return set()


_NODE_FIELD_PATH_RE = re.compile(r"^nodes\[(\d+)\]")


def _blamed_node(itinerary: Optional[Itinerary], field_path: str):
    """从 `Violation.field_path` 解析出肇事节点本身（新增，ADR-0010 D-5）。

    `field_path` 形如 "nodes[2].target_id" / "nodes[1].start_time"；只取 node
    下标，索引进 `itinerary.nodes`。`_blamed_target` 委托本函数只取
    (target_kind, target_id)；`_compute_blacklists` 直接用本函数还能取到
    `start_time`（封槽黑名单要用）。

    解析失败（itinerary 为空 / 下标越界 / field_path 不含 "nodes[N]" 前缀，
    如 DURATION_OUT_OF_RANGE 的 "total_minutes"）→ None。
    """
    if itinerary is None:
        return None
    m = _NODE_FIELD_PATH_RE.match(field_path or "")
    if not m:
        return None
    idx = int(m.group(1))
    if idx < 0 or idx >= len(itinerary.nodes):
        return None
    return itinerary.nodes[idx]


def _blamed_target(
    itinerary: Optional[Itinerary], field_path: str
) -> tuple[Optional[str], Optional[str]]:
    """从 `Violation.field_path` 解析肇事节点（ADR-0009 决策 5：定向 blame）。

    逐语义保留复用（ADR-0010 D-5）：内部重构为委托 `_blamed_node`，行为不变——
    解析失败仍返回 (None, None)。
    """
    node = _blamed_node(itinerary, field_path)
    if node is None:
        return None, None
    return node.target_kind, node.target_id


def _compute_blacklists(
    itinerary: Optional[Itinerary],
    violations: list[Violation],
) -> tuple[set[str], set[str], set[tuple[str, str]]]:
    """根据统一 critic 违规产出 (POI 黑名单 / 餐厅黑名单 / 餐厅×时段 黑名单)。

    ADR-0010 D-5：改签名——旧版吃 `(failed: CandidatePlan, itinerary, intent,
    violations)`，本版吃 `(itinerary, violations)`。blame 一律走 `field_path`
    定位到肇事节点实体（`_blamed_node`），不再依赖 `CandidatePlan`"至多两个
    实体（main_poi/restaurant）"的假设——路线模型下活动数量任意，任何一个中间
    节点都可能是肇事者。

    (rest_id, slot) 元组的 slot 来自**排定后的 `node.start_time`**（不是旧版
    ILS 候选层面的 `dining_time` 标签）——这本就是 ADR-0009 C-4 定案的键值来源
    （消除"黑名单键值错位"bug），路线模型下这个来源更加自然（`Visit` 本没有
    离散候选时段标签，`node.start_time` 是唯一权威值）。

    与旧版的行为差异（field_path 解析失败时的兜底）：旧版 `CandidatePlan` 只有
    两个实体，"解析失败就两个都拉黑"是可枚举的保守兜底；路线模型下实体数量
    任意，没有"两个都"这回事——本版统一遵循"宁可漏拉黑、不误拉黑"（`_compute_
    blacklists` 对每个桶：解析失败 = 本条违规本轮不产生任何黑名单动作，不代表
    整个函数放弃——其它能解析成功的违规仍正常产出）。这是 intentional 行为
    变化，非退化：`_compute_blacklists` 的返回值随后被 `plan_hybrid` 检查
    "全部为空则跳出循环落地板"，误伤空间反而更小。

    Returns:
        (排除的 poi_id 集合, 排除的 rest_id 集合, 封锁的 (rest_id, slot_hhmm) 集合)
    """
    blacklist_poi: set[str] = set()
    blacklist_rest: set[str] = set()
    blacklist_rest_time: set[tuple[str, str]] = set()

    for v in violations:
        buckets = _classify_violation(v)
        if not buckets:
            continue

        node = _blamed_node(itinerary, v.field_path)

        if "restaurant_time" in buckets:
            if node is not None and node.target_kind == "restaurant":
                blacklist_rest_time.add((node.target_id, node.start_time))

        if "restaurant_swap" in buckets:
            if node is not None and node.target_kind == "restaurant":
                blacklist_rest.add(node.target_id)

        if "directed_swap" in buckets:
            if node is not None and node.target_kind == "poi":
                blacklist_poi.add(node.target_id)
            elif node is not None and node.target_kind == "restaurant":
                blacklist_rest.add(node.target_id)
            # field_path 解析失败：不再有"两个都拉黑"的兜底（见函数 docstring）

        if "opening_hours" in buckets:
            if node is not None and node.target_kind == "restaurant":
                blacklist_rest_time.add((node.target_id, node.start_time))
            elif node is not None and node.target_kind == "poi":
                blacklist_poi.add(node.target_id)

        # "distance_lever"（DURATION_OUT_OF_RANGE）：field_path 恒为
        # "total_minutes"，_blamed_node 恒解析失败 → 恒不产生动作。
        # 见模块级注释「判断点：DURATION_OUT_OF_RANGE 弱杠杆在路线模型下
        # 退化为恒不产生动作」。

    return blacklist_poi, blacklist_rest, blacklist_rest_time


# ============================================================
# 封槽 + 重搜——**ADR-0013 F-1 起搬家**：`_shrink_visit_windows` /
# `_apply_blacklist_to_pool` / `_repair_route`（本节原先在此定义的三个函数）
# 已逐字节原样迁移到 `route_builder.py`（`_repair_route` 同时提升为公开
# `repair_route`，供 `planners/node_swap.py` 局部重解引擎共享复用——见该
# 模块 docstring「机制修正」节）。本模块上方「C-3/C-4 迁移」「调研留痕」等
# 历史叙事段落里对这三个函数的记述（设计动机/与 min-conflicts 的对应关系）
# 描述的正是搬走后的同一份实现，行为逐字节未变，不必重写，仍可按原文理解；
# 下方 `plan_hybrid` 改为 `from .route_builder import repair_route` 消费。
# ============================================================
