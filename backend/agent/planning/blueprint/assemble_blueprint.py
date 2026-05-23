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
- 蓝图级 critic（在 `agent/blueprint.py` 的 `run_blueprint_critics`）
- Itinerary 级 critic（在 `agent/v2/critics_v2.py`）
- decision_trace 填写（由 LangGraph `agent/graph/nodes/assemble.py` 节点注入）
- share_message / orders 生成（由 narrate / execute 节点填充）
"""

from __future__ import annotations

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
) -> str:
    """根据 mid nodes 集合自适应 summary 文案。

    取 duration_min 最长的 mid node 作为主体标识；
    根据是否含 POI / Restaurant 区分「半日方案」/「轻量方案」/「用餐方案」。
    """
    has_main = any(
        n.target_kind == BlueprintTargetKind.POI for n in blueprint.nodes
    )
    has_dining = any(
        n.target_kind == BlueprintTargetKind.RESTAURANT for n in blueprint.nodes
    )

    primary = max(blueprint.nodes, key=lambda n: n.duration_min)
    primary_meta = _resolve_target_meta(
        primary.target_kind.value if isinstance(primary.target_kind, BlueprintTargetKind) else primary.target_kind,  # type: ignore[arg-type]
        primary.target_id,
        user_profile,
    )
    primary_label = primary_meta["title"]

    total_h = round(total_minutes / 60, 1)

    if has_main and has_dining:
        return f"半日方案 · {primary_label}（约 {total_h} 小时）"
    if has_dining and not has_main:
        return f"用餐方案 · {primary_label}（约 {total_h} 小时）"
    if has_main and not has_dining:
        return f"轻量方案 · {primary_label}（约 {total_h} 小时）"
    return f"短途方案（约 {total_h} 小时）"


# ============================================================
# 主入口
# ============================================================


def assemble_from_blueprint(
    intent: IntentExtraction,  # noqa: ARG001 — 留作未来 summary / title 个性化扩展
    blueprint: PlanBlueprint,
    user_profile: UserProfile,
) -> Itinerary:
    """蓝图 → Itinerary（edge_v1）。

    Args:
        intent: 用户意图（当前用于扩展余地，summary 暂不读取）。
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

    # ---------- 5. 派生 schedule + 总时长 + summary ----------
    schedule = _derive_schedule(nodes, hops)
    total_minutes = cursor_min - _parse_hhmm(blueprint.preferred_start_time)
    summary = _build_summary(blueprint, user_profile, total_minutes)

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
