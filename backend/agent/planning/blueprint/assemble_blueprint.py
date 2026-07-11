"""agent.assemble_blueprint —— PlanBlueprint → Itinerary（edge_v1 拼装层）。

【职责】

把 LLM 出的 `PlanBlueprint`（仅 mid nodes + preferred_start_time）拼装成
完整的 `Itinerary`（含首尾 home 节点 + 自动计算的 hops + schedule 派生视图）。

这是 LLM-Modulo（Kambhampati NeurIPS 2024）框架里的「客观侧」：
- LLM 决定**主观**项：在哪里、做什么、停留多久（mid nodes）
- 系统决定**客观**项：home 起终点、节点间通勤分钟、时间游标推进
  （通勤通过 `lookup_hop` 查 `routes.json` / haversine 三级降级）

【关键算法（见 design.md「assemble_from_blueprint」伪代码；候选 A 追补见
`_assemble_forward`/`_find_backschedule_anchor`/`_backschedule_departure`
三个函数的 docstring）】

```
# 内核（_assemble_forward，cursor 单调推进，候选 A 外层循环调用两次，内核不变）
cursor = parse(preferred_start_time)
nodes = [home_start_node]
hops = []
for i, bp_node in enumerate(blueprint.nodes):
    commute, mode, path_type = lookup_hop(prev.target_id, bp_node.target_id, ...)
    hop_start = cursor             # 离开 prev 的时刻
    cursor += commute              # 抵达 bp_node 的时刻
    buffer = 5 if i > 0 else 0     # 首跳不留 buffer
    next_start = cursor + buffer
    hops.append(Hop(start=hop_start, minutes=commute, buffer_min=buffer))
    nodes.append(ActivityNode(start=next_start, duration=bp_node.duration_min))
    cursor = next_start + bp_node.duration_min

# 返程：mid_nodes[-1] → home，buffer_min=0（终点不需要等 buffer）
commute_back = lookup_hop(last.target_id, "home", ...)
hops.append(Hop(start=cursor, minutes=commute_back, buffer_min=0))
nodes.append(home_end_node(start=cursor+commute_back, duration=0))

# 外层循环（assemble_from_blueprint，backward scheduling from anchor，方案 1.3 候选 A）：
# 1. 先跑一次内核（预演）
# 2. 找时间序上第一个被吸附顶出正差额的餐厅节点作锚
# 3. 从锚点减去它之前全部 hop+停留时长，反推 preferred_start_time
#    早于原下限 → 维持原时刻（差额留作中段合法等待）
#    否则         → 用新出发时刻重跑一次内核（仍是纯单次正向遍历）
# 4. 尾部后处理：首段等待折叠（_fold_leading_wait，候选 A 在"锚点=首个 mid
#    节点"时的退化子集/收尾兜底）——首站吸附/钉窗挤出的出门后等待，整体
#    后移 n0/h0 的出发时刻吸收掉
```

【不变量手工断言（RuntimeError，先于 Pydantic 校验）】

1. `len(hops) == len(nodes) - 1`
2. `nodes[0].target_kind == "home"` 且 `nodes[-1].target_kind == "home"`

任一失败即 RuntimeError，让诊断信息比 Pydantic ValidationError 友好。

【schedule 派生视图】

按生产顺序展平 `nodes + hops`（生产顺序 == 时间序，因 cursor 单调推进）：
- node entry：`hidden = (target_kind == "home")` —— home 节点不渲染时间块
- hop  entry：`hidden = (path_type == "in_place")` —— 同地复用 hop 不渲染

【不负责】

- LLM 调用与 prompt（在 `agent/blueprint_llm.py` / `agent/prompts/blueprint_prompt.py`）
- 蓝图级 critic（ADR-0009 决策 8 已删——曾在 `blueprint.py` 的 `run_blueprint_critics`，
  确认无生产调用者后随 Phase C-5 移除）
- Itinerary 级 critic（在 `agent/planning/critic/critics_v2.py`）
- decision_trace 填写（由 LangGraph `agent/graph/nodes/assemble.py` 节点注入）
- share_message / orders 生成（由 narrate / execute 节点填充）
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from data.loader import load_pois, load_restaurants
from schemas.domain import UserProfile
from schemas.intent import IntentExtraction
from schemas.itinerary import (
    ActivityNode,
    Hop,
    Itinerary,
    NodeTargetKind,
    ScheduleEntry,
)

from .blueprint import BlueprintNode, BlueprintTargetKind, PlanBlueprint
from ..commute.lookup_hop import lookup_hop

logger = logging.getLogger(__name__)


# ============================================================
# 时间工具（HH:MM ↔ 分钟）
# ============================================================

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _parse_hhmm(t: str) -> int:
    """把 "HH:MM" 解析为分钟数（00:00 → 0，14:30 → 870）。"""
    if not _TIME_RE.match(t):
        raise ValueError(f"时间字符串必须是 HH:MM 格式，实际 {t!r}")
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _fmt_hhmm(total: int) -> str:
    """把分钟数转回 "HH:MM"；超 24h 按 mod 24 截断（hackathon 简化）。"""
    total = max(0, total) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


# ============================================================
# 目标元数据解析（target_kind, target_id → title / lat / lng / address）
# ============================================================


def _resolve_target_meta(
    target_kind: NodeTargetKind,
    target_id: str,
    user_profile: UserProfile,
    *,
    fallback_title: Optional[str] = None,
) -> dict:
    """根据 (target_kind, target_id) 取节点元数据。

    Args:
        target_kind: poi / restaurant / home。
        target_id: 对应 mock id 或 "home"。
        user_profile: 含 home_location。
        fallback_title: home 节点专用占位标题（"出发" / "回家"）；
                        其它情况找不到 entity 时也用作兜底。

    Returns:
        {"title", "lat", "lng", "address"} 四元 dict（lat/lng/address 可为 None）。
    """
    if target_kind == "home":
        loc = user_profile.home_location
        return {
            "title": fallback_title or "回家",
            "lat": loc.lat,
            "lng": loc.lng,
            "address": loc.name,
        }

    if target_kind == "poi":
        poi = next((p for p in load_pois() if p.id == target_id), None)
        if poi is not None:
            return {
                "title": poi.name,
                "lat": poi.location.lat,
                "lng": poi.location.lng,
                "address": poi.location.name,
            }

    if target_kind == "restaurant":
        rest = next((r for r in load_restaurants() if r.id == target_id), None)
        if rest is not None:
            return {
                "title": rest.name,
                "lat": rest.location.lat,
                "lng": rest.location.lng,
                "address": rest.location.name,
            }

    # entity 查不到（critic 应该已经拒了）—— 返回最小可用占位
    return {
        "title": fallback_title or target_id,
        "lat": None,
        "lng": None,
        "address": None,
    }


# ============================================================
# 餐厅预约槽吸附（真因修复批 item 1）
# ============================================================


def _earliest_available_slot_min(target_id: str, natural_arrival_min: int) -> Optional[int]:
    """给定餐厅 target_id + 自然到达分钟数，找该店 `reservation_slots` 里
    「不早于自然到达时刻」且 `available=True` 的最早一个槽，返回其分钟数；
    找不到（餐厅不存在 / 无槽表 / 当日剩余时段全不可用）→ None（不吸附，让
    critic 照常拦——诚实，不硬造一个不存在的可预约时刻）。

    【为什么必须存在（真 LLM 复测真因）】
    `blueprint_llm.py` 故意不让 LLM 输出任何时间字段——edge_v1 设计里 LLM 只
    决定节点顺序/kind/时长，start_time 这个"客观项"完全由 assemble 按 home/
    上一站累加通勤+停留分钟数算出（精确到分钟，如 15:17）。但 mock 餐厅
    `Restaurant.reservation_slots` 是离散槽位列表（多为 30 分钟网格，如
    17:00/17:30/18:00，个别店甚至有整点缺口——如 mock_data/restaurants.json
    的 R004 只有 14:30/15:00/16:00，15:30 是缺口），精确分钟几乎不可能命中
    任何一个真实槽。真 LLM 复测 8/8 轮全部滑向 ILS 兜底，
    `restaurant_full_unresolved` 场景 21/21 全中 `RESTAURANT_FULL_UNRESOLVED`
    ——根因不是模型能力：蓝图 schema 本就不给 LLM 时间字段的写权限，2 轮
    backprompt 冲着一个"LLM 改不动的字段"重试，物理上必败。

    【口径对齐：与 check_demo_restaurant_full 读同一份真相，不是各算各的】
    `agent.planning.critic._rules.checks.check_demo_restaurant_full` 校验
    `node.start_time` 是否**精确等于**该店 `reservation_slots` 里某条
    `time` 字段、且该条 `available=True`。本函数在"排定之前"用同一张
    `reservation_slots` 表找"不早于自然到达的最早可用槽"并吸附上去——让
    assemble 产出的 start_time 本就等于 critic 会认可的那个真实槽位，
    两处读同一份 mock 真值，不是吸附算一套、校验又是另一套、靠运气对上。

    【为什么不直接复用 route_scheduler._snap_to_slot_grid（ILS 侧机制）】
    `agent.planning.planners.route_scheduler` 模块 docstring 判断点 3 明确
    记录它是"向上取整到最近半点"的**通用网格近似**，刻意不读
    `Visit.entity.reservation_slots`——理由是那一层要守住"纯函数、不消费
    具体实体字段"的边界（D-2 层定位）。但真实 mock 数据并非处处严格半点
    网格（R004 的 15:30 缺口即一例），通用网格 snap 可能落在该店根本没开放
    的时刻，反而制造新的不一致。`assemble_blueprint.py` 本就在拼装层直接
    消费 `load_restaurants()`（`_resolve_target_meta` 已经在读实体字段），
    不是纯函数模块，没有"不碰实体字段"的边界要守；直接读该店真实
    `reservation_slots` 精确吸附，比套用通用网格更准——两处"槽机制"因此
    刻意不共享同一份实现，是分层职责使然，不是遗漏（rule/ILS 路径的
    `not_before_start` 已经是调度器算好的、必然对得上真实槽的时刻，见下方
    调用点判断点）。

    Args:
        target_id: 餐厅 mock id（如 "R001"）。
        natural_arrival_min: 吸附前、按当前排程逐段累加算出的到达分钟数。

    Returns:
        最早可用槽的分钟数；查不到餐厅 / 无 `reservation_slots` / 无任何
        不早于到达时刻的可用槽 → None。
    """
    rest = next((r for r in load_restaurants() if r.id == target_id), None)
    if rest is None or not rest.reservation_slots:
        return None

    candidates: list[int] = []
    for slot in rest.reservation_slots:
        if not slot.available:
            continue
        if not _TIME_RE.match(slot.time):
            continue  # 防御性：mock 数据理应合法，格式错的槽跳过不吸附
        slot_min = _parse_hhmm(slot.time)
        if slot_min >= natural_arrival_min:
            candidates.append(slot_min)

    return min(candidates) if candidates else None


# ============================================================
# 出发时刻倒推（backward scheduling from anchor，方案 1.3 候选 A，ADR-0017 追补）
# ============================================================


def _find_backschedule_anchor(
    nodes: list[ActivityNode], hops: list[Hop], blueprint: PlanBlueprint
) -> Optional[tuple[int, int]]:
    """在预演一次的正向拼装结果里，找「时间序上第一个被硬约束顶出等待」的锚点。

    【这是什么问题】backward scheduling from a due date（frePPLe/VRPTW 式：
    先按到达/预约锚点倒推最早可行开始时刻）——`_fold_leading_wait`（候选 B）
    只处理"锚点恰好是首个 mid 节点"这一种情形；候选 A 是它的完整超集：锚点
    可以出现在链路任意位置（如"先看展、后吃饭"，餐厅是第二个 mid 节点）。

    【锚点判据：不是"有 reservation_slots"，是"被吸附顶出了真实等待"】
    任务书原文说"时间序上第一个有 reservation_slots 硬约束的节点"，但逐字照办
    会把 A8 类"到达时刻晚于全部槽、根本没吸附"的餐厅也当成锚点——这种情形
    压根没有等待可消（自然到达就是最终 start_time，`snapped_min is None`），
    把它当锚点反推只会得出荒谬的"提前"结论（该店没有更晚的槽，倒推无意义，
    真正问题是 critic 该拦的 RESTAURANT_FULL_UNRESOLVED）。精确判据改为
    "该餐厅节点的 `start_time` 确实晚于其自然到达时刻"（即真正被吸附产生了
    正差额）——这是对任务书算法的忠实但更健壮的实现，差异见报告「待裁决」。

    【范围限定：只认 LLM 路径的 `_earliest_available_slot_min` 吸附，不碰
    ILS/rule 路径的 `not_before_start` 钉窗（方案 1.11-b「两套槽机制不合并」
    的直接推论）】`route_builder.route_to_blueprint` 已经把 `route_scheduler`
    算好的、必要最小 slack 的绝对时刻钉进 `not_before_start`（对所有
    `slack_min > 0` 的节点，不限餐厅），且 `preferred_start_time` 本就是该
    调度器自己算出的 `depart_min`——这条时间线已经是另一套独立机制的最优解，
    它的"正差额"不是"可以倒推消除的死等"，而是 VRPTW 意义上的必要 slack
    （见 route_scheduler 模块 docstring 判断点 3）。若不加区分地对它再跑一次
    「零松弛重新摊平」的倒推，等于用 LLM 路径的朴素倒推覆盖 ILS 路径的精细
    调度结果——违反两套槽机制刻意分层、不合并的既有边界。判据：跳过
    `blueprint.nodes[idx-1].not_before_start` 已设置的节点（`idx-1` 是因为
    `nodes[1:]` 与 `blueprint.nodes` 一一对应，`nodes[0]` 是 home 起点）。

    【已知范围限定：只倒推第一个锚点，多餐厅链路的下游锚点不连带处理】
    任务书原文明确是"时间序上**第一个**有硬约束的节点"——本实现严格按此
    取第一个。多餐厅场景（如"晚饭+夜宵"）下，若晚饭已吸附产生 gap、夜宵
    也吸附产生 gap，本函数只用晚饭反推出发时刻；重跑一次之后晚饭的 gap
    精确清零（单轮收敛，见 `_backschedule_departure` docstring），但夜宵
    自己的 gap 不受影响（它的自然到达只取决于晚饭的**绝对**结束时刻，与
    出发时刻整体前移量无关——backward 传播只消掉了它前面那一个锚点的
    差额，不会连带消掉更下游锚点自己的差额）。这不是 bug：任务书按"第一个"
    定义了倒推目标，多锚点连续消除是一般化的 VRPTW 多阶段 backward slack
    传播，超出本次任务范围（报告「待裁决」列出，供主代理判断是否需要
    扩展成"从最后一个锚点开始向前逐个消除"的多轮版本）。

    Args:
        nodes: 预演一次的完整节点列表（首尾 home，`_fold_leading_wait` 之前）。
        hops: 对应 hop 列表。
        blueprint: 本轮蓝图（读 `nodes[i].not_before_start` 判断该节点是否
            已被 ILS/rule 路径的调度器钉死，不是本函数的倒推对象）。

    Returns:
        `(anchor_node_index, anchor_target_min)`——锚点在 `nodes` 里的下标、
        锚点应该到达的绝对分钟数（即吸附后的 `start_time`）。找不到锚点
        （没有任何被顶出等待的硬约束节点）→ None。
    """
    for idx in range(1, len(nodes) - 1):
        node = nodes[idx]
        if node.target_kind != "restaurant":
            continue
        bp_node = blueprint.nodes[idx - 1]
        if getattr(bp_node, "not_before_start", None):
            # ILS/rule 路径钉窗——不是本函数的倒推对象（见上方范围限定）。
            continue
        hop_before = hops[idx - 1]
        natural_arrival_min = (
            _parse_hhmm(hop_before.start_time)
            + hop_before.minutes
            + hop_before.buffer_min
        )
        actual_start_min = _parse_hhmm(node.start_time)
        if actual_start_min > natural_arrival_min:
            # 真正被吸附顶出了正差额——这是任务书"硬约束节点"的精确含义。
            return idx, actual_start_min
    return None


def _backschedule_departure(
    nodes: list[ActivityNode],
    hops: list[Hop],
    anchor_idx: int,
    anchor_target_min: int,
    original_start_min: int,
) -> Optional[int]:
    """从锚点减去它之前所有 hop+停留时长，反推 `preferred_start_time` 应几点。

    把锚点之前的链路当成"背靠背、零松弛"重新摊平：新出发时刻 = 锚点目标到达
    时刻 - (锚点之前全部 hop 分钟+buffer 之和) - (锚点之前全部 mid 节点停留
    时长之和)。这与现有 `_fold_leading_wait` 是同一个代数关系在"锚点=首个
    mid 节点"这一特殊情形下的退化版本（此时求和项只有 h0，与 `_fold_leading_
    wait` 的 `gap` 计算完全等价）。

    【边界（方案已列，必须处理）】倒推出的出发时刻若早于用户可接受下限
    （本系统无实时时钟概念，下限取蓝图原始 `preferred_start_time`——口语
    "几点出门"默认是 release time，即最早可出发时刻，见 ADR-0017 释义）→
    维持原时刻，返回 None（差额留作中段合法等待，讲得出因由：这段等待不是
    "出门太早"，而是"就算不能再早出门，这段路线本身留了这么多余量"）；
    否则返回倒推出的更晚出发时刻。

    【实现纪律记录：本实现下此边界数学上不可达，但代码仍必须保留判断】
    `_find_backschedule_anchor` 只在 `actual_start_min(锚点) > natural_
    arrival_min` 时才判定为锚点——而 `natural_arrival_min` 正是本函数
    `span` 计算所依据的同一条预演链路上算出来的（`natural_arrival_min ==
    original_start_min + span`，两者是同一次 `_assemble_forward` 调用的
    产物）。代入：`new_start_min = anchor_target_min - span >
    (original_start_min + span) - span == original_start_min`——即"存在
    锚点"这个前提本身已经保证 `new_start_min > original_start_min` 恒成立，
    这条边界在当前"单锚点、同一预演链路反推"的实现下不会被触发。保留判断
    仍然必要：① 这是任务书明确列出的必做边界，属于契约的一部分，不能因为
    "现在推不出反例"就删除防御；②若未来扩展成多锚点/跨预演链路取值（如
    改用外部声明的到达截止时间做锚点，而不是本次预演自己算出的吸附时刻），
    这条边界会立刻变得可达——先量好尺子，后面才不会失守。

    Args:
        nodes / hops: 预演一次的完整拼装结果（首尾 home）。
        anchor_idx: `_find_backschedule_anchor` 定位的锚点节点下标。
        anchor_target_min: 锚点应到达的绝对分钟数。
        original_start_min: 蓝图原始 `preferred_start_time`（分钟）——同时
            充当"用户可接受下限"。

    Returns:
        新的 `preferred_start_time`（分钟）；倒推结果早于下限时返回 None
        （维持原时刻，不替换）。
    """
    span = 0
    for i in range(anchor_idx):
        hop = hops[i]
        span += hop.minutes + hop.buffer_min
        if i > 0:
            # nodes[i] 是锚点之前的 mid 节点（i=0 时 nodes[0] 是 home，
            # duration_min 恒 0，不需要特判也不影响结果，写 i>0 只为可读性）。
            span += nodes[i].duration_min

    new_start_min = anchor_target_min - span
    if new_start_min < original_start_min:
        return None
    return new_start_min


# ============================================================
# 首段等待折叠（I1「出门即行程」，ADR-0017，2026-07-11）
# ============================================================


def _fold_leading_wait(nodes: list[ActivityNode], hops: list[Hop]) -> int:
    """把「出门后在首站门口罚站」的首段等待折叠进出发时刻，返回折叠分钟数。

    【这是什么问题】backward scheduling from anchor（frePPLe 式排程）的最小
    切片：正向游标 + 餐厅槽吸附的组合会把等待放在**到达之后**（19:00 出门
    19:03 到店、吸附到 20:00 落座 → 57 分钟"自由休息"排在店门口），而 I1
    不变式要求出发时刻回答"我几点出门"——等待应该被出发时刻吸收（19:55 出门
    20:00 落座）。

    【与候选 A（`assemble_from_blueprint` 外层倒推循环）的关系，2026-07-11
    追补】本函数只处理"锚点恰好是首个 mid 节点"这一种情形——这正是候选 A 在
    "链路第一个 mid 节点就是硬约束节点"时的退化子集，两者用的是同一条代数
    关系（`_backschedule_departure` 在 `anchor_idx==1` 时，求和项只有 h0，与本
    函数的 `gap` 计算完全等价）。`assemble_from_blueprint` 现在总是先跑一次
    候选 A 外层循环（`_find_backschedule_anchor` + `_backschedule_departure`，
    锚点可能在任意位置、可能倒推出更晚的出发时刻并整体重跑一次正向
    assemble）；本函数仍在其后无条件调用一次，作为
    最终收尾——如果锚点就是首个 mid 节点，倒推已经把首段差额消成 0（等号
    对齐，见下方单轮收敛论证），本函数在此处必然是 no-op（幂等）；如果锚点
    倒推被下限挡回（`_backschedule_departure` 返回 None，差额留作中段合法
    等待），本函数依旧兜底处理"首个 mid 节点自身也恰好有独立首段差额"这种
    锚点不存在时的普通情形（如首站是 POI、全程没有任何餐厅硬约束）。**不
    删除本函数**——它是候选 A 的必要子例程，不是被替代的旧代码。

    【为什么放 assemble 尾部】LLM / ILS / rule 三条规划路径全部经
    `assemble_from_blueprint` 拼装（ILS 见 ils_planner.py route_to_blueprint →
    assemble 调用链），在这里后处理一次即三路径同时覆盖，无需各路径独立改动。

    【单轮收敛（实证钉死，见 test_assemble_fold.py）】吸附规则是"不早于自然
    到达的最早可用槽"（`_earliest_available_slot_min`，`>=` 判定）：折叠量 g
    恰好使新自然到达 == 原选中槽时刻，`slot_min >= natural_arrival_min` 以
    等号成立，重跑吸附必选同一个槽——一轮收敛，不振荡。ILS 路径的
    `not_before_start` 是绝对时刻，后移出发只会让自然到达逼近但不超过它，
    同理收敛。对已折叠行程再折 → 差额恒 0 → no-op（幂等）。

    【安全性质】只改写 nodes[0]（home 起点）与 hops[0] 两个 start_time 且
    保持精确对齐（h0_end + buffer == n1.start 以等号成立），活动节点时刻/
    槽位/窗全部不动 → critic 各 check 输入不变或更优，折叠不制造任何新违规。
    跨午夜防御：n1.start_time 若因上游异常回卷（mod-24），差额算出负值 →
    直接 no-op（此类行程本就会被 check_temporal_alignment HARD 拦下）。

    Args:
        nodes: assemble 拼好的完整节点列表（首尾 home）。原地改写 nodes[0]。
        hops: 对应 hop 列表。原地改写 hops[0]。

    Returns:
        折叠的分钟数 g（≥0；0 表示无首段等待，未做任何改写）。
    """
    if len(nodes) < 3 or not hops:
        # 无 mid 节点（不构成"出门去做事"的行程）——没有可折叠的首段
        return 0

    h0 = hops[0]
    n1 = nodes[1]
    h0_start_min = _parse_hhmm(h0.start_time)
    # 首跳 buffer 按构造恒为 0；公式仍带上 buffer_min，若未来首跳规则改变，
    # 折叠依旧保持"精确对齐"语义（幂等单测会先叫）。
    gap = _parse_hhmm(n1.start_time) - (h0_start_min + h0.minutes + h0.buffer_min)
    if gap <= 0:
        return 0

    nodes[0].start_time = _fmt_hhmm(_parse_hhmm(nodes[0].start_time) + gap)
    h0.start_time = _fmt_hhmm(h0_start_min + gap)
    return gap


# ============================================================
# Schedule 派生视图
# ============================================================


def _derive_schedule(
    nodes: list[ActivityNode], hops: list[Hop]
) -> list[ScheduleEntry]:
    """把 nodes + hops 按生产顺序（== 时间序）展平成 ScheduleEntry 列表。

    生产顺序：n0, h0, n1, h1, ..., n_{k}, h_{k}, n_{k+1}（k=len(hops)-1）。
    cursor 单调推进保证生产顺序就是时间序。

    渲染策略：
    - node entry：home 节点 hidden=True（前端不渲染空白块）
    - hop  entry：path_type=in_place 的 hop hidden=True（同地复用不渲染）
    """
    out: list[ScheduleEntry] = []

    for i, node in enumerate(nodes):
        node_start = _parse_hhmm(node.start_time)
        out.append(
            ScheduleEntry(
                entry_kind="node",
                ref_id=node.node_id,
                start=node.start_time,
                end=_fmt_hhmm(node_start + node.duration_min),
                title=node.title,
                minutes=node.duration_min,
                mode=None,
                hidden=(node.target_kind == "home"),
            )
        )

        # 在每个 node 之后插入对应 hop（除了最后一个 node）
        if i < len(hops):
            hop = hops[i]
            hop_start = _parse_hhmm(hop.start_time)
            out.append(
                ScheduleEntry(
                    entry_kind="hop",
                    ref_id=hop.hop_id,
                    start=hop.start_time,
                    end=_fmt_hhmm(hop_start + hop.minutes),
                    title=f"通勤 {hop.minutes} 分钟",
                    minutes=hop.minutes,
                    mode=hop.mode,
                    hidden=(hop.path_type == "in_place"),
                )
            )

    return out


# ============================================================
# Summary 适配
# ============================================================


def _build_summary(
    blueprint: PlanBlueprint,
    user_profile: UserProfile,
    total_minutes: int,
    intent: Optional[IntentExtraction] = None,
) -> str:
    """小红书风格行程卡片大标题（itinerary.summary 最底层兜底）。

    最底层保底：narrate 节点未覆盖时（如直接调 assemble，或 ReAct/planner_stream
    在 narrate 写回前推 itinerary_ready）显示的标题，必须**信息全**——遍历全部
    mid nodes（跳过 home），用动作短语 + 同行短语 + 时长拼一句口语标题，
    而不是旧的「max 单站 + 半日方案·前缀 +（约X小时）」（旧 bug 漏掉其它站）。
    """
    from agent.intent.title_builder import (
        build_xiaohongshu_title,
        companions_to_title_phrase,
        node_to_title_phrase,
    )

    station_phrases: list[str] = []
    for n in blueprint.nodes:
        tk = (
            n.target_kind.value
            if isinstance(n.target_kind, BlueprintTargetKind)
            else n.target_kind
        )
        meta = _resolve_target_meta(tk, n.target_id, user_profile)  # type: ignore[arg-type]
        phrase = node_to_title_phrase(
            title=meta.get("title") or "",
            kind=n.kind or "",
            target_kind=tk or "",
        )
        if phrase:
            station_phrases.append(phrase)

    companions_phrase = (
        companions_to_title_phrase(
            [
                c.model_dump() if hasattr(c, "model_dump") else c
                for c in (intent.companions or [])
            ]
        )
        if intent is not None
        else ""
    )
    total_hours = total_minutes / 60
    return build_xiaohongshu_title(
        station_phrases=station_phrases,
        companions_phrase=companions_phrase,
        total_hours=total_hours,
    )


# ============================================================
# 正向拼装核心（cursor 单调推进，候选 A 外层循环调用两次，内核本身不变）
# ============================================================


def _assemble_forward(
    blueprint: PlanBlueprint,
    user_profile: UserProfile,
) -> tuple[list[ActivityNode], list[Hop], int]:
    """纯正向拼装：cursor 单调推进算出 nodes/hops，含不变量手工断言。

    【为什么单列一个函数】方案 1.11-c 已拍板"cursor 单调推进不打破"——候选 A
    的倒推是"预演一次正向 assemble → 倒推调整 preferred_start_time → 重跑一次
    正向 assemble"的外层循环，内核（本函数）必须原样保持纯单次正向遍历，不
    引入任何双向推进的复杂度。`assemble_from_blueprint` 用同一个纯函数调用
    两次（预演 + 定稿），而不是维护两份逻辑或让内核感知"这是第几次跑"。

    Returns:
        `(nodes, hops, cursor_min)`——`cursor_min` 是返程到家后的最终游标
        （分钟数），供调用方算 `total_minutes`。
    """
    transport_pref = (
        user_profile.transport_preference
        if user_profile.transport_preference in {"walking", "taxi", "bus"}
        else "taxi"
    )
    cursor_min: int = _parse_hhmm(blueprint.preferred_start_time)

    # ---------- 1. 首部插入 home 起点节点 n0 ----------
    home_start_meta = _resolve_target_meta(
        "home", "home", user_profile, fallback_title="出发"
    )
    nodes: list[ActivityNode] = [
        ActivityNode(
            node_id="n0",
            kind="起点",
            target_kind="home",
            target_id="home",
            start_time=blueprint.preferred_start_time,
            duration_min=0,
            title=home_start_meta["title"],
            lat=home_start_meta["lat"],
            lng=home_start_meta["lng"],
            address=home_start_meta["address"],
            note=None,
        )
    ]
    hops: list[Hop] = []

    # ---------- 2. 遍历 blueprint.nodes，逐对计算 hop + 下一个 node ----------
    for i, bp_node in enumerate(blueprint.nodes):
        prev_node = nodes[-1]
        commute_min, mode, path_type = lookup_hop(
            prev_node.target_id,
            bp_node.target_id,
            transport_pref,  # type: ignore[arg-type]
            user_profile,
        )

        hop_start_min = cursor_min
        cursor_min += commute_min
        # 首跳（i=0，即 home → mid_nodes[0]）不留 buffer；非首跳留 5min
        buffer = 0 if i == 0 else 5

        hops.append(
            Hop(
                hop_id=f"h{i}",
                from_node_id=prev_node.node_id,
                to_node_id=f"n{i + 1}",
                start_time=_fmt_hhmm(hop_start_min),
                minutes=commute_min,
                mode=mode,
                path_type=path_type,
                buffer_min=buffer,
            )
        )

        node_start_min = cursor_min + buffer

        # 乙（ADR-0009 决策 2）：节点可声明「最早开始时刻」not_before_start（如餐厅
        # 预约 chosen_time）。自然到达早于它时，把节点开始推迟到该时刻——差额是餐前
        # 空闲/休息，让排定时刻与 note/reservation 自洽（cap 砍短 POI 后餐厅仍准点）。
        # buffer 保持真实过渡值不变；check_temporal_alignment 因 to_start ≥
        # hop_end + buffer 仍通过（推迟只会让 to_start 更大，不会更小）。
        nb_raw = getattr(bp_node, "not_before_start", None)
        if nb_raw and _TIME_RE.match(nb_raw):
            # rule/ILS 路径：route_builder.route_to_blueprint 已经把调度器
            # （route_scheduler，窗感知 + 槽网格 snap）算好的可行时刻钉进
            # not_before_start，直接采信，不重新吸附一遍。
            node_start_min = max(node_start_min, _parse_hhmm(nb_raw))
        elif bp_node.target_kind == BlueprintTargetKind.RESTAURANT:
            # LLM 路径：not_before_start 恒为 None（LLM 从未获得时间字段的
            # 写权限，见 blueprint_llm.py），此时吸附到该店真实可用预约槽
            # （_earliest_available_slot_min 判断点已详述真因 + 口径对齐）。
            snapped_min = _earliest_available_slot_min(bp_node.target_id, node_start_min)
            if snapped_min is not None:
                node_start_min = snapped_min
            # 找不到任何可用槽 → 不吸附，原样保留自然到达时刻，让
            # check_demo_restaurant_full 照常拦（诚实反映"这确实订不上"，
            # 不是伪造一个不存在的槽位掩盖真实容量约束）。

        # bp_node.target_kind 是 BlueprintTargetKind 枚举（poi/restaurant），
        # 与 ActivityNode.target_kind 的 Literal["poi","restaurant","home"] 兼容
        target_kind_str: NodeTargetKind = bp_node.target_kind.value  # type: ignore[assignment]
        meta = _resolve_target_meta(
            target_kind_str, bp_node.target_id, user_profile
        )
        nodes.append(
            ActivityNode(
                node_id=f"n{i + 1}",
                kind=bp_node.kind,
                target_kind=target_kind_str,
                target_id=bp_node.target_id,
                start_time=_fmt_hhmm(node_start_min),
                duration_min=bp_node.duration_min,
                title=meta["title"],
                lat=meta["lat"],
                lng=meta["lng"],
                address=meta["address"],
                note=bp_node.note,
            )
        )
        cursor_min = node_start_min + bp_node.duration_min

    # ---------- 3. 尾部追加返程 hop + home 终点节点 ----------
    last_mid_node = nodes[-1]
    commute_back, mode_back, path_back = lookup_hop(
        last_mid_node.target_id,
        "home",
        transport_pref,  # type: ignore[arg-type]
        user_profile,
    )
    return_hop_start = cursor_min
    cursor_min += commute_back

    hops.append(
        Hop(
            hop_id=f"h{len(blueprint.nodes)}",
            from_node_id=last_mid_node.node_id,
            to_node_id=f"n{len(blueprint.nodes) + 1}",
            start_time=_fmt_hhmm(return_hop_start),
            minutes=commute_back,
            mode=mode_back,
            path_type=path_back,
            buffer_min=0,  # 返程不留 buffer：到家就到家
        )
    )

    home_end_meta = _resolve_target_meta(
        "home", "home", user_profile, fallback_title="回家"
    )
    nodes.append(
        ActivityNode(
            node_id=f"n{len(blueprint.nodes) + 1}",
            kind="终点",
            target_kind="home",
            target_id="home",
            start_time=_fmt_hhmm(cursor_min),
            duration_min=0,
            title=home_end_meta["title"],
            lat=home_end_meta["lat"],
            lng=home_end_meta["lng"],
            address=home_end_meta["address"],
            note=None,
        )
    )

    # ---------- 4. 不变量手工断言（RuntimeError，先于 Pydantic 校验） ----------
    if len(hops) != len(nodes) - 1:
        raise RuntimeError(
            f"assemble 不变量违反：hops 长度 {len(hops)} ≠ nodes 长度 - 1 = {len(nodes) - 1}"
            f"（nodes={[n.node_id for n in nodes]}，hops={[h.hop_id for h in hops]}）"
        )
    if nodes[0].target_kind != "home" or nodes[0].target_id != "home":
        raise RuntimeError(
            f"assemble 不变量违反：首节点必须是 home，"
            f"实际 target_kind={nodes[0].target_kind!r} target_id={nodes[0].target_id!r}"
        )
    if nodes[-1].target_kind != "home" or nodes[-1].target_id != "home":
        raise RuntimeError(
            f"assemble 不变量违反：尾节点必须是 home，"
            f"实际 target_kind={nodes[-1].target_kind!r} target_id={nodes[-1].target_id!r}"
        )
    if nodes[0].duration_min != 0 or nodes[-1].duration_min != 0:
        raise RuntimeError(
            f"assemble 不变量违反：首尾 home 节点 duration_min 必须为 0，"
            f"实际 首={nodes[0].duration_min} 尾={nodes[-1].duration_min}"
        )

    return nodes, hops, cursor_min


# ============================================================
# 主入口
# ============================================================


def assemble_from_blueprint(
    intent: IntentExtraction,
    blueprint: PlanBlueprint,
    user_profile: UserProfile,
) -> Itinerary:
    """蓝图 → Itinerary（edge_v1）。

    Args:
        intent: 用户意图（companions 用于 summary 同行短语；小红书风格大标题）。
        blueprint: LLM 输出的 mid nodes + preferred_start_time。
        user_profile: 含 home_location 与 transport_preference。

    Returns:
        合法的 Itinerary 对象（含 nodes + hops + schedule + total_minutes）。

    Raises:
        RuntimeError: 不变量校验失败（hops 长度不匹配 / 首尾不是 home）。
                      Pydantic ValidationError 是兜底（schemas/itinerary.py 里
                      的 model_validator 也会再校验一次）。

    保证：
        - len(hops) == len(nodes) - 1
        - 首尾 nodes 均为 target_kind="home" / target_id="home" / duration_min=0
        - schedule 与 nodes/hops 时间一致

    【候选 A 外层循环，方案 1.3/1.11-c，ADR-0017 追补】
    1. 先跑一次 `_assemble_forward`（预演）；
    2. `_find_backschedule_anchor` 在预演结果里找时间序上第一个被吸附顶出
       正差额的餐厅节点（"硬约束节点"的精确判据见该函数 docstring）；
    3. 找到锚点则 `_backschedule_departure` 反推新的 `preferred_start_time`；
       倒推结果不早于原下限时，用新出发时刻的 blueprint **重跑一次**
       `_assemble_forward`（cursor 单调推进的内核完全不变，只是外层多调了
       一次同一个纯函数——1.11-c 已拍板的边界）；
    4. 无论是否倒推，最终结果都过一次 `_fold_leading_wait`——它是候选 A 在
       "锚点就是首个 mid 节点"时的退化子集，此处保留作为收尾兜底（若倒推
       已把首段差额精确清零，这里必然 no-op；若锚点不存在或被下限挡回，
       这里兜住普通的首段吸附差额，行为与改动前的 C2 完全一致）。
    """
    nodes, hops, cursor_min = _assemble_forward(blueprint, user_profile)

    # ---------- 出发时刻倒推（候选 A：任意位置锚点）----------
    anchor = _find_backschedule_anchor(nodes, hops, blueprint)
    if anchor is not None:
        anchor_idx, anchor_target_min = anchor
        original_start_min = _parse_hhmm(blueprint.preferred_start_time)
        new_start_min = _backschedule_departure(
            nodes, hops, anchor_idx, anchor_target_min, original_start_min
        )
        if new_start_min is not None and new_start_min > original_start_min:
            backscheduled_blueprint = blueprint.model_copy(
                update={"preferred_start_time": _fmt_hhmm(new_start_min)}
            )
            nodes, hops, cursor_min = _assemble_forward(
                backscheduled_blueprint, user_profile
            )
            logger.info(
                "backschedule applied: %s→%s (anchor idx=%d target=%s)",
                blueprint.preferred_start_time,
                backscheduled_blueprint.preferred_start_time,
                anchor_idx,
                _fmt_hhmm(anchor_target_min),
            )
            # 倒推重跑之后，_build_summary/_fold_leading_wait 等下游一律读
            # 重跑后的 nodes/hops——但 fold 的 logger.info /
            # finalize_plan._fold_minutes 仍以「原始 blueprint.preferred_
            # start_time」为基准算总位移（见下方 fold_min 与 finalize_plan
            # 现算逻辑），倒推位移与首段折叠位移在观测口径上自然合并成一个
            # 总差值，不需要额外传递"这段位移来自倒推还是折叠"的元数据。

    # ---------- 首段等待折叠（I1「出门即行程」，ADR-0017，候选 A 收尾）----------
    # 必须先于 schedule 派生与 total_minutes 计算：schedule 从折叠后的
    # nodes/hops 展平，total_minutes 从折叠后的出发时刻起算——首段等待
    # 被出发时刻吸收后，总时长如实缩小（时长本来就是虚胖的机制口径）。
    fold_min = _fold_leading_wait(nodes, hops)
    if fold_min > 0:
        logger.info(
            "fold applied: %s→%s (+%dmin), anchor=%s@%s",
            blueprint.preferred_start_time,
            nodes[0].start_time,
            fold_min,
            nodes[1].title or nodes[1].target_id,
            nodes[1].start_time,
        )

    # ---------- 派生 schedule + 总时长 + summary ----------
    schedule = _derive_schedule(nodes, hops)
    # 折叠/倒推后 nodes[0].start_time 即真实出发时刻（未改动时等于
    # blueprint.preferred_start_time，三种情况下这一个表达式都正确）。
    # 注意：不改 total_minutes 的字段语义（"出发到到家的墙钟时间差"）——
    # 折叠/倒推改变的是出发时刻本身，不是口径。
    total_minutes = cursor_min - _parse_hhmm(nodes[0].start_time)
    summary = _build_summary(blueprint, user_profile, total_minutes, intent)

    # ---------- 构造 Itinerary（Pydantic 二次兜底校验） ----------
    return Itinerary(
        schema_version="edge_v1",
        summary=summary,
        nodes=nodes,
        hops=hops,
        schedule=schedule,
        orders=[],          # execute 节点后续填充
        share_message=None, # narrate 节点后续填充
        total_minutes=total_minutes,
        decision_trace=None,  # LangGraph assemble 节点后续注入
    )
