"""agent.assemble_blueprint —— PlanBlueprint → Itinerary（edge_v1 拼装层）。

【职责】

把 LLM 出的 `PlanBlueprint`（仅 mid nodes + preferred_start_time）拼装成
完整的 `Itinerary`（含首尾 home 节点 + 自动计算的 hops + schedule 派生视图）。

这是 LLM-Modulo（Kambhampati NeurIPS 2024）框架里的「客观侧」：
- LLM 决定**主观**项：在哪里、做什么、停留多久（mid nodes）
- 系统决定**客观**项：home 起终点、节点间通勤分钟、时间游标推进
  （通勤通过 `lookup_hop` 查 `routes.json` / haversine 三级降级）

【关键算法（见 design.md「assemble_from_blueprint」伪代码）】

```
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

# 尾部后处理：首段等待折叠（I1「出门即行程」，ADR-0017）——首站吸附/钉窗
# 挤出的出门后等待，整体后移 n0/h0 的出发时刻吸收掉（见 _fold_leading_wait）
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
# 首段等待折叠（I1「出门即行程」，ADR-0017，2026-07-11）
# ============================================================


def _fold_leading_wait(nodes: list[ActivityNode], hops: list[Hop]) -> int:
    """把「出门后在首站门口罚站」的首段等待折叠进出发时刻，返回折叠分钟数。

    【这是什么问题】backward scheduling from anchor（frePPLe 式排程）的最小
    切片：正向游标 + 餐厅槽吸附的组合会把等待放在**到达之后**（19:00 出门
    19:03 到店、吸附到 20:00 落座 → 57 分钟"自由休息"排在店门口），而 I1
    不变式要求出发时刻回答"我几点出门"——等待应该被出发时刻吸收（19:55 出门
    20:00 落座）。完整倒推（对任意位置锚点回推出发时刻）挂路演后；本函数只
    处理首段：首段差额的唯一物理语义就是"出门太早"（首跳 buffer=0，差额没有
    任何其它成因），后移出发时刻是无损的。中段 gap 不折——那可能有正当因由
    （等座/消食），归完整倒推批处理。

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
    """
    # ---------- 准备：交通偏好 + 时间游标 ----------
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

    # ---------- 4.5 首段等待折叠（I1「出门即行程」，ADR-0017）----------
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

    # ---------- 5. 派生 schedule + 总时长 + summary ----------
    schedule = _derive_schedule(nodes, hops)
    # 折叠后 nodes[0].start_time 即真实出发时刻（未折叠时等于
    # blueprint.preferred_start_time，两种情况下这一个表达式都正确）。
    # 注意：不改 total_minutes 的字段语义（"出发到到家的墙钟时间差"）——
    # 折叠改变的是出发时刻本身，不是口径。
    total_minutes = cursor_min - _parse_hhmm(nodes[0].start_time)
    summary = _build_summary(blueprint, user_profile, total_minutes, intent)

    # ---------- 6. 构造 Itinerary（Pydantic 二次兜底校验） ----------
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
