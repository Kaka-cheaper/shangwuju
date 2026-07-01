"""11 个 _check_xxx 函数实现（spec code-modularization-refactor H6）。

从 critics_v2.py 抽出，每个函数对应一类违规：

- _check_invariants            INVARIANT_BROKEN（结构不变量）
- _check_nodes_incomplete      NODES_INCOMPLETE（mid 节点至少 1 个）
- _check_duration              DURATION_OUT_OF_RANGE（总时长容差）
- _check_temporal_feasibility  TIMELINE_INCONSISTENT（hop / node 时间自洽）
- _check_hop_feasibility       HOP_INFEASIBLE（hop.minutes vs lookup_hop）
- _check_distance              DISTANCE_EXCEEDED（distance_max_km）
- _check_demo_restaurant_full  RESTAURANT_FULL_UNRESOLVED（mock 满座埋点）
- _check_dietary               DIETARY_VIOLATION（饮食约束）
- _check_social_context        SOCIAL_CONTEXT_MISMATCH（social_compat 矩阵）
- _check_age_aware_duration    AGE_DURATION_MISMATCH（年龄感知时长 cap）
- _check_tool_consistency      TOOL_RESPONSE_INCONSISTENCY（hallucination 防护）
- _check_capacity              CAPACITY_REQUIREMENT_VIOLATED（≥5 人桌型）

所有函数：
- 输入：Itinerary（+ 可选 IntentExtraction / user_profile / tool_results）
- 输出：list[Violation]（可能为空）
- 副作用：仅查 mock 数据（_safe_load_*），不改任何状态
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary

from .helpers import (
    fmt_hhmm,
    humanize_node,
    parse_hhmm,
    resolve_transport_preference,
    safe_load_pois,
    safe_load_restaurants,
)

if TYPE_CHECKING:  # 仅类型标注用，运行时不 import，避免 checks ↔ context 任何环依赖
    from agent.planning.critic.context import CriticContext


# ============================================================
# CriticContext 接缝（ADR-0008 Phase A）
# ============================================================
#
# 每个 check 新增 keyword-only 形参 `ctx: CriticContext | None`：
# - validate() 走注册表统一以 `fn(plan, ctx=ctx)` 调用 → 数据从 ctx 读（一次性加载）。
# - 历史直调（测试 / 脚本如 `check_meal_time(itin)` / `_check_tool_consistency(itin, None)`）
#   不传 ctx → 各 check 回退到旧的 `safe_load_*()` 自加载，**行为逐字节不变**。
# 因为 ctx 与回退路径都走同一组 `safe_load_*`，两条路径产出的数据完全一致。
#
# `_UNSET`：区分「显式传入 tool_results=None（跳过反幻觉）」与「未传、应从 ctx 取」。

_UNSET = object()
from .types import (
    DEMO_FULL_TIME,  # noqa: F401  保留兼容：旧引用方可能 import
    DISTANCE_TOLERANCE_KM,
    DURATION_TOLERANCE_MIN,
    HOP_FEASIBILITY_TOLERANCE_MIN,
    TEMPORAL_TOLERANCE_MIN,
    Severity,
    Violation,
    ViolationCode,
)


def _get_lookup_hop():
    """动态拿 lookup_hop 函数：优先从 critics_v2 module level（兼容 monkeypatch spy），
    回退直接 import。让 test_critics_v2_hop.py 的 `monkeypatch.setattr(critic_mod, "lookup_hop", spy)`
    能真正拦截 hop 校验调用。"""
    try:
        from agent.planning.critic import critics_v2 as _cv2

        fn = getattr(_cv2, "lookup_hop", None)
        if fn is not None:
            return fn
    except ImportError:
        pass
    from agent.planning.commute.lookup_hop import lookup_hop

    return lookup_hop


# ============================================================
# 单项 critic
# ============================================================


def check_invariants(
    itinerary: Itinerary, *, ctx: "CriticContext | None" = None
) -> list[Violation]:
    """edge_v1 三条结构不变量（防御性兜底）。

    `ctx` 在本 check 不消费（纯结构断言，不查任何外部数据）；保留形参仅为注册表统一调用。

    通常 Pydantic `Itinerary._check_invariants` 已在 model_validator 阶段拦下；
    本 critic 仅在「有人手工 bypass Pydantic 构造」或「下游 mutate 后破坏不变量」
    时触发。所有违反一律 CRITICAL。

    校验三条：
    1. len(hops) == len(nodes) - 1
    2. nodes[0] / nodes[-1] target_kind == "home"
    3. nodes[0] / nodes[-1] duration_min == 0
    """
    out: list[Violation] = []

    expected_hops = len(itinerary.nodes) - 1
    if len(itinerary.hops) != expected_hops:
        out.append(
            Violation(
                code=ViolationCode.INVARIANT_BROKEN,
                severity=Severity.HARD,
                message=(
                    f"行程结构不变量违反：hops 数量 {len(itinerary.hops)} "
                    f"应等于 nodes 数量减 1（{expected_hops}）。"
                    "请重新生成节点序列让相邻节点之间各有一条通勤段。"
                ),
                field_path="hops",
            )
        )

    if itinerary.nodes:
        first = itinerary.nodes[0]
        if first.target_kind != "home":
            out.append(
                Violation(
                    code=ViolationCode.INVARIANT_BROKEN,
                    severity=Severity.HARD,
                    message=(
                        f"行程结构不变量违反：首节点必须是 home（实际 target_kind={first.target_kind!r}）。"
                        "请把出发起点设为家，由系统自动注入即可。"
                    ),
                    field_path="nodes[0]",
                )
            )
        elif first.duration_min != 0:
            out.append(
                Violation(
                    code=ViolationCode.INVARIANT_BROKEN,
                    severity=Severity.HARD,
                    message=(
                        f"行程结构不变量违反：首节点（home）停留时长应为 0 "
                        f"（实际 {first.duration_min} 分钟）。home 是抽象起终点，不表达「在家停留」。"
                    ),
                    field_path="nodes[0].duration_min",
                )
            )

        last = itinerary.nodes[-1]
        if last.target_kind != "home":
            out.append(
                Violation(
                    code=ViolationCode.INVARIANT_BROKEN,
                    severity=Severity.HARD,
                    message=(
                        f"行程结构不变量违反：尾节点必须是 home（实际 target_kind={last.target_kind!r}）。"
                        "请把回家终点设为家，由系统自动注入即可。"
                    ),
                    field_path="nodes[-1]",
                )
            )
        elif last.duration_min != 0:
            out.append(
                Violation(
                    code=ViolationCode.INVARIANT_BROKEN,
                    severity=Severity.HARD,
                    message=(
                        f"行程结构不变量违反：尾节点（home）停留时长应为 0 "
                        f"（实际 {last.duration_min} 分钟）。"
                    ),
                    field_path="nodes[-1].duration_min",
                )
            )

    return out


def check_nodes_incomplete(
    itinerary: Itinerary, *, ctx: "CriticContext | None" = None
) -> list[Violation]:
    """验行程必要节点完整性（B-2a: 按 target_kind，非自由文本 kind）。

    ADR-0008 红队 B1 修订：节点完整性必须对照 `decide_nodes(intent)` 返回的
    required kinds，并按 **target_kind** 判断，绝不比对自由文本 `node.kind`
    （LLM 可能标 "夜宵" / "早茶" / "自由" 等任意标签）。

    规则：
    - `ctx.intent` 可用时：
        * `KIND_MAIN` (主活动) required ⇒ 需要 ≥1 个 `target_kind=="poi"` 节点
        * `KIND_DINING` (用餐) required ⇒ 需要 ≥1 个 `target_kind=="restaurant"` 节点
        * 缺任何一种 → 一条 HARD NODES_INCOMPLETE，消息列出所有缺失 kind
    - `ctx` 为 None 或 intent 缺失（向后兼容直调）→ 回退到旧「len<3」结构检查

    Emit HARD，Stage 0（结构门：命中即短路）。
    """
    intent = ctx.intent if ctx is not None else None

    if intent is not None:
        try:
            from agent.planning.blueprint.node_decider import (  # 运行时 import 避免循环依赖
                KIND_DINING,
                KIND_MAIN,
                decide_nodes,
            )
            required_kinds = decide_nodes(intent)
        except ImportError:
            required_kinds = []

        if required_kinds:
            mid_nodes = [n for n in itinerary.nodes if n.target_kind != "home"]
            missing: list[str] = []
            if KIND_MAIN in required_kinds and not any(
                n.target_kind == "poi" for n in mid_nodes
            ):
                missing.append("主活动节点（POI）")
            if KIND_DINING in required_kinds and not any(
                n.target_kind == "restaurant" for n in mid_nodes
            ):
                missing.append("用餐节点（餐厅）")

            if missing:
                return [
                    Violation(
                        code=ViolationCode.NODES_INCOMPLETE,
                        severity=Severity.HARD,
                        message=(
                            f"行程缺少必要节点：{'  /  '.join(missing)}。"
                            "请在候选池中补充对应类型节点后重新规划。"
                        ),
                        field_path="nodes",
                    )
                ]
            return []

    # 向后兼容回退：ctx/intent 缺失 或 decide_nodes 返回空列表
    if len(itinerary.nodes) < 3:
        return [
            Violation(
                code=ViolationCode.NODES_INCOMPLETE,
                severity=Severity.HARD,
                message=(
                    "行程中间没有任何活动节点（nodes 仅含首尾 home）。"
                    "请至少安排一个 POI 或餐厅作为活动主体。"
                ),
                field_path="nodes",
            )
        ]
    return []


def check_duration(
    itinerary: Itinerary,
    intent: Optional[IntentExtraction] = None,
    *,
    ctx: "CriticContext | None" = None,
) -> list[Violation]:
    """验 total_minutes 是否落在 intent.duration_hours±30min 容差。"""
    if intent is None and ctx is not None:
        intent = ctx.intent
    if intent is None:
        return []  # 无 intent 无从校验（旧路径恒有 intent，此处仅防御）
    duration = intent.duration_hours
    if not duration or len(duration) != 2:
        return []  # schema 校验已保证此处必有值，但防御一下

    lo, hi = int(duration[0]), int(duration[1])
    lo_tol = lo * 60 - DURATION_TOLERANCE_MIN
    hi_tol = hi * 60 + DURATION_TOLERANCE_MIN

    actual = int(itinerary.total_minutes)
    if actual < lo_tol or actual > hi_tol:
        if actual < lo_tol:
            advice = f"请扩展节点停留或增加候选活动，将总时长拉到 {lo}-{hi}h 区间"
        else:
            advice = f"请压缩节点停留或减少候选活动，将总时长压到 {lo}-{hi}h 区间"

        return [
            Violation(
                code=ViolationCode.DURATION_OUT_OF_RANGE,
                severity=Severity.HARD,
                message=(
                    f"行程总时长 {actual} 分钟（约 {actual / 60:.1f}h）"
                    f"不在用户期望的 {lo}-{hi}h（含 ±30min 容差）内。{advice}"
                ),
                field_path="total_minutes",
            )
        ]
    return []


def check_temporal_feasibility(
    itinerary: Itinerary, *, ctx: "CriticContext | None" = None
) -> list[Violation]:
    """验时序自洽：hop.start ≈ from_node.end 且 to_node.start ≥ hop.end + buffer。

    `ctx` 在本 check 不消费（只读 itinerary 自身的时间戳）；保留形参仅为注册表统一调用。

    设计依据（design.md _check_temporal_feasibility 伪代码）：
    - 容差 2min（assemble 内部按整数分钟取整，可能轻微浮动）
    - 仅在「assemble 输出严格自洽」前提下兜底；正常路径下永远不触发
    """
    out: list[Violation] = []
    nodes = itinerary.nodes
    hops = itinerary.hops

    bound = min(len(hops), len(nodes) - 1)
    for i in range(bound):
        hop = hops[i]
        from_node = nodes[i]
        to_node = nodes[i + 1]

        from_start = parse_hhmm(from_node.start_time)
        hop_start = parse_hhmm(hop.start_time)
        to_start = parse_hhmm(to_node.start_time)

        if from_start is None or hop_start is None or to_start is None:
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.HARD,
                    message=(
                        f"{humanize_node(i, from_node)} 或下一段时间格式不合法（应为 HH:MM）。"
                        "请重新生成行程使所有时间戳为合法 HH:MM。"
                    ),
                    field_path=f"hops[{i}].start_time",
                )
            )
            continue

        from_end = from_start + from_node.duration_min
        hop_end = hop_start + hop.minutes

        # 1. hop.start 与 from_node.end 必须紧接（容差 2min）
        if abs(hop_start - from_end) > TEMPORAL_TOLERANCE_MIN:
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.HARD,
                    message=(
                        f"{humanize_node(i, from_node)} 结束于 {fmt_hhmm(from_end)}，"
                        f"但下一段通勤却从 {hop.start_time} 开始（错位 "
                        f"{abs(hop_start - from_end)} 分钟）。请让通勤紧接节点结束时刻。"
                    ),
                    field_path=f"hops[{i}].start_time",
                )
            )

        # 2. to_node.start 必须 ≥ hop.end + buffer（容差 2min）
        required_to_start = hop_end + hop.buffer_min
        if to_start < required_to_start - TEMPORAL_TOLERANCE_MIN:
            shortage = required_to_start - to_start
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.HARD,
                    message=(
                        f"{humanize_node(i + 1, to_node)} 开始于 {to_node.start_time}，"
                        f"早于通勤完成（{fmt_hhmm(hop_end)}）+ buffer({hop.buffer_min}min) "
                        f"应有的 {fmt_hhmm(required_to_start)}，缺 {shortage} 分钟。"
                        "请把下一段开始时间推迟到通勤完成 + buffer 之后。"
                    ),
                    field_path=f"nodes[{i + 1}].start_time",
                )
            )

    return out


def check_time_parseable(
    itinerary: Itinerary, *, ctx: "CriticContext | None" = None
) -> list[Violation]:
    """Stage 0 结构门：验所有节点 / hop 的 start_time 可解析为 HH:MM。

    ADR-0008 红队 G2 拆位：时间**可解析**属结构不变量 → Stage 0（命中短路）。
    hop/buffer **对齐**（TIMELINE_INCONSISTENT）→ Stage 1（check_temporal_alignment）。

    检查所有 node.start_time 和 hop.start_time；任意一处无法解析 → HARD 违规。
    `ctx` 不消费（只读 itinerary 自身时间戳）；保留形参供注册表统一调用。
    """
    out: list[Violation] = []

    for idx, node in enumerate(itinerary.nodes):
        if parse_hhmm(node.start_time) is None:
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.HARD,
                    message=(
                        f"{humanize_node(idx, node)} 的开始时间 {node.start_time!r} "
                        "格式不合法（应为 HH:MM）。"
                        "请重新生成行程使所有时间戳为合法 HH:MM 格式。"
                    ),
                    field_path=f"nodes[{idx}].start_time",
                )
            )

    for i, hop in enumerate(itinerary.hops):
        if parse_hhmm(hop.start_time) is None:
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.HARD,
                    message=(
                        f"第 {i + 1} 段通勤的开始时间 {hop.start_time!r} "
                        "格式不合法（应为 HH:MM）。"
                        "请重新生成行程使所有时间戳为合法 HH:MM 格式。"
                    ),
                    field_path=f"hops[{i}].start_time",
                )
            )

    return out


def check_temporal_alignment(
    itinerary: Itinerary, *, ctx: "CriticContext | None" = None
) -> list[Violation]:
    """Stage 1 hard：验 hop.start ≈ from_node.end 且 to_node.start ≥ hop.end + buffer。

    ADR-0008 红队 G2 拆位：hop/buffer 对齐属语义校验 → Stage 1（check_temporal_alignment）；
    时间可解析性 → Stage 0（check_time_parseable）。

    设计依据（design.md _check_temporal_feasibility 伪代码）：
    - 容差 2min（assemble 内部按整数分钟取整，可能轻微浮动）
    - Stage 0 已确保所有时间戳可解析；本 check 若遇到解析失败则静默跳过该 hop
      （防御性兜底，正常路径 Stage 0 已短路不会走到此处）
    `ctx` 不消费（只读 itinerary 自身时间戳）；保留形参供注册表统一调用。
    """
    out: list[Violation] = []
    nodes = itinerary.nodes
    hops = itinerary.hops

    bound = min(len(hops), len(nodes) - 1)
    for i in range(bound):
        hop = hops[i]
        from_node = nodes[i]
        to_node = nodes[i + 1]

        from_start = parse_hhmm(from_node.start_time)
        hop_start = parse_hhmm(hop.start_time)
        to_start = parse_hhmm(to_node.start_time)

        # Stage 0 应已拦截解析失败；此处静默跳过（避免 TypeError，防御性兜底）
        if from_start is None or hop_start is None or to_start is None:
            continue

        from_end = from_start + from_node.duration_min
        hop_end = hop_start + hop.minutes

        # 1. hop.start 与 from_node.end 必须紧接（容差 2min）
        if abs(hop_start - from_end) > TEMPORAL_TOLERANCE_MIN:
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.HARD,
                    message=(
                        f"{humanize_node(i, from_node)} 结束于 {fmt_hhmm(from_end)}，"
                        f"但下一段通勤却从 {hop.start_time} 开始（错位 "
                        f"{abs(hop_start - from_end)} 分钟）。请让通勤紧接节点结束时刻。"
                    ),
                    field_path=f"hops[{i}].start_time",
                )
            )

        # 2. to_node.start 必须 ≥ hop.end + buffer（容差 2min）
        required_to_start = hop_end + hop.buffer_min
        if to_start < required_to_start - TEMPORAL_TOLERANCE_MIN:
            shortage = required_to_start - to_start
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.HARD,
                    message=(
                        f"{humanize_node(i + 1, to_node)} 开始于 {to_node.start_time}，"
                        f"早于通勤完成（{fmt_hhmm(hop_end)}）+ buffer({hop.buffer_min}min) "
                        f"应有的 {fmt_hhmm(required_to_start)}，缺 {shortage} 分钟。"
                        "请把下一段开始时间推迟到通勤完成 + buffer 之后。"
                    ),
                    field_path=f"nodes[{i + 1}].start_time",
                )
            )

    return out


def check_hop_feasibility(
    itinerary: Itinerary,
    user_profile=None,
    *,
    ctx: "CriticContext | None" = None,
) -> list[Violation]:
    """验 hop.minutes ≥ lookup_hop 实际值 - 容差。

    设计依据（design.md _check_hop_feasibility 伪代码 + Property 5）：
    - 与 assemble 共享同一 `lookup_hop` 函数 → 同输入同输出 → 不会漂移
    - in_place hop（minutes=0 / from_id == to_id）跳过：恒可达
    - hop.minutes < actual - 2 → CRITICAL（hackathon 防御性兜底）
    - 数据缺失（lookup_hop 4 级兜底返 15min）也按 actual=15 比较，仍可触发
    """
    out: list[Violation] = []

    if user_profile is None and ctx is not None:
        user_profile = ctx.profile
    if user_profile is None:
        return out

    transport_pref = resolve_transport_preference(user_profile)
    nodes = itinerary.nodes
    hops = itinerary.hops
    bound = min(len(hops), len(nodes) - 1)

    for i in range(bound):
        hop = hops[i]
        # 1 级降级：in_place 永远可达
        if hop.path_type == "in_place":
            continue

        from_node = nodes[i]
        to_node = nodes[i + 1]

        actual_min, actual_mode, actual_path_type = _get_lookup_hop()(
            from_node.target_id,
            to_node.target_id,
            transport_pref,  # type: ignore[arg-type]
            user_profile,
        )

        if hop.minutes < actual_min - HOP_FEASIBILITY_TOLERANCE_MIN:
            shortage = actual_min - hop.minutes
            out.append(
                Violation(
                    code=ViolationCode.HOP_INFEASIBLE,
                    severity=Severity.HARD,
                    message=(
                        f"{humanize_node(i, from_node)} 去往 "
                        f"{humanize_node(i + 1, to_node)} 的通勤实际需要约 "
                        f"{actual_min} 分钟（{actual_mode}），"
                        f"但行程里这段 hop 只留了 {hop.minutes} 分钟，"
                        f"缺 {shortage} 分钟（容差 2 分钟内不算违规）。"
                        f"请改为更近的目标点，或让系统按 routes.json 重算 hop 分钟。"
                    ),
                    field_path=f"hops[{i}].minutes",
                )
            )

    return out


def check_age_aware_duration(
    itinerary: Itinerary,
    intent: Optional[IntentExtraction] = None,
    *,
    ctx: "CriticContext | None" = None,
) -> list[Violation]:
    """spec planning-quality-deep-review R4：ILS 路径年龄感知单段时长 critic（镜像）。

    与 `agent/blueprint.py:_age_aware_duration_critic` 业务等价，但作用对象是
    Itinerary（已 assemble）而非 PlanBlueprint——LangGraph LLM 主路径走 blueprint
    critic，ILS / fallback 路径走本 critic。**两者镜像防绕过**。

    业务规则（与 blueprint.py 同源 _resolve_age_caps）：
    - companions 含 ≤3 岁 → cap 45min
    - companions 含 4-6 岁 → cap 75min
    - companions 含 7-12 岁 → cap 120min
    - companions 含 ≥75 岁 → cap 60min
    - 多代际取最严

    仅对 target_kind=poi 的 mid node 校验（餐厅按 typical_dining_min 是另一规则）。
    """
    if intent is None and ctx is not None:
        intent = ctx.intent
    if intent is None or not getattr(intent, "companions", None):
        return []

    cap_candidates: list[tuple[int, str]] = []
    for c in intent.companions:
        age = getattr(c, "age", None)
        role = getattr(c, "role", "同行")
        if not isinstance(age, int) or age < 0:
            continue
        if age <= 3:
            cap_candidates.append((45, f"含 {age} 岁{role}（婴幼儿 ≤45min）"))
        elif age <= 6:
            cap_candidates.append((75, f"含 {age} 岁{role}（学龄前 ≤75min）"))
        elif age <= 12:
            cap_candidates.append((120, f"含 {age} 岁{role}（学童 ≤120min）"))
        elif age >= 75:
            cap_candidates.append((60, f"含 {age} 岁{role}（高龄 ≤60min）"))

    if not cap_candidates:
        return []

    min_cap = min(c[0] for c in cap_candidates)
    reason_text = "；".join(c[1] for c in cap_candidates if c[0] == min_cap)
    expected = (max(45, min_cap - 15), min_cap)

    out: list[Violation] = []
    for idx, node in enumerate(itinerary.nodes):
        if node.target_kind != "poi":
            continue
        # node duration 优先取 node.duration_min，否则用 end - start
        duration = getattr(node, "duration_min", None)
        if not isinstance(duration, int):
            start_min = parse_hhmm(getattr(node, "start_time", "")) if hasattr(node, "start_time") else None
            end_min = parse_hhmm(getattr(node, "end_time", "")) if hasattr(node, "end_time") else None
            if start_min is None or end_min is None:
                continue
            duration = end_min - start_min

        if duration > min_cap:
            out.append(
                Violation(
                    code=ViolationCode.AGE_DURATION_MISMATCH,
                    severity=Severity.HARD,
                    message=(
                        f"{humanize_node(idx, node)} 停留 {duration} 分钟"
                        f"超出年龄约束（{reason_text}）"
                    ),
                    field_path=f"nodes[{idx}].duration_min",
                    expected_range=expected,
                )
            )

    return out


def check_distance(
    itinerary: Itinerary,
    intent: Optional[IntentExtraction] = None,
    *,
    ctx: "CriticContext | None" = None,
) -> list[Violation]:
    """单个 mid node 距家距离 > intent.distance_max_km → warning。

    edge_v1：直接遍历 nodes（home 节点 distance 无意义跳过）。
    """
    if intent is None and ctx is not None:
        intent = ctx.intent
    out: list[Violation] = []
    if intent is None:
        return out
    max_km = intent.distance_max_km
    if max_km is None or max_km <= 0:
        return out

    pois_by_id = ctx.pois_by_id if ctx is not None else {p.id: p for p in safe_load_pois()}
    restaurants_by_id = (
        ctx.restaurants_by_id
        if ctx is not None
        else {r.id: r for r in safe_load_restaurants()}
    )

    for idx, node in enumerate(itinerary.nodes):
        if node.target_kind == "home":
            continue

        target_distance: Optional[float] = None
        target_label = node.title or ""

        if node.target_kind == "poi" and node.target_id in pois_by_id:
            poi = pois_by_id[node.target_id]
            target_distance = poi.distance_km
            target_label = target_label or poi.name
        elif node.target_kind == "restaurant" and node.target_id in restaurants_by_id:
            rest = restaurants_by_id[node.target_id]
            target_distance = rest.distance_km
            target_label = target_label or rest.name

        if target_distance is None:
            continue
        if target_distance > max_km + DISTANCE_TOLERANCE_KM:
            out.append(
                Violation(
                    code=ViolationCode.DISTANCE_EXCEEDED,
                    severity=Severity.SOFT,
                    message=(
                        f"{humanize_node(idx, node)} 距家 {target_distance:.1f}km，"
                        f"超过用户期望 {max_km:.1f}km。如条件允许请换距离更近的候选。"
                    ),
                    field_path=f"nodes[{idx}].target_id",
                )
            )

    return out


def check_demo_restaurant_full(
    itinerary: Itinerary, *, ctx: "CriticContext | None" = None
) -> list[Violation]:
    """demo-aware 满座埋点：mock 餐厅在「reservation_slots[time].available=False」是 RESTAURANT_FULL 异常埋点。

    spec planning-quality-deep-review R4：从「写死 17:00」改为查 mock 真值——
    在 mock 数据 `Restaurant.reservation_slots` 中查 node.start_time 对应的 slot
    是否 `available=False`。

    通过 ENABLE_DEMO_FULL_CHECK 环境变量控制开关（默认开）。
    """
    enabled = (os.getenv("ENABLE_DEMO_FULL_CHECK") or "1").strip().lower()
    if enabled in ("0", "false", "no", "off"):
        return []

    restaurants_by_id = (
        ctx.restaurants_by_id
        if ctx is not None
        else {r.id: r for r in safe_load_restaurants()}
    )

    out: list[Violation] = []
    for idx, node in enumerate(itinerary.nodes):
        if node.target_kind != "restaurant":
            continue
        node_time = (node.start_time or "").strip()
        if not node_time:
            continue
        rest = restaurants_by_id.get(node.target_id or "")
        if rest is None:
            continue
        full_slot = next(
            (s for s in rest.reservation_slots if s.time == node_time and not s.available),
            None,
        )
        if full_slot is None:
            continue

        out.append(
            Violation(
                code=ViolationCode.RESTAURANT_FULL_UNRESOLVED,
                severity=Severity.HARD,
                message=(
                    f"{humanize_node(idx, node)} 在 {node_time} 已满座"
                    f"（mock 餐厅 reservation_slots 标记 available=False）。"
                    "请调用 check_restaurant_availability 验证实际可用性，"
                    "或换到其它有空档的时段。"
                ),
                field_path=f"nodes[{idx}].start_time",
            )
        )
    return out


def check_capacity(
    itinerary: Itinerary,
    intent: Optional[IntentExtraction] = None,
    *,
    ctx: "CriticContext | None" = None,
) -> list[Violation]:
    """spec innovation-review M3：capacity_requirement critic（≥6 人但餐厅无 8 人桌型）。

    业务背景：
    - intent.capacity_requirement 由意图层设置，同行 ≥4 时必填（schemas/intent.py:144）
    - mock Restaurant.capacity 字段含 two/four/six/eight + private_room 五种桌型存在性
    - 当 capacity_requirement ≥ 6 但餐厅 six=False 且 eight=False 且 private_room=False
      → 桌型不够，必须 backprompt LLM 换餐厅

    设计纪律：
    - 仅对 target_kind=restaurant 节点校验
    - 无 capacity_requirement 或 ≤ 4 → 跳过（4 人桌业界默认有）
    - 餐厅 load 失败 → 跳过（无数据不误伤）
    - severity=CRITICAL（≥6 人没桌等于不能就餐，比 dietary warning 严重）
    """
    if intent is None and ctx is not None:
        intent = ctx.intent
    cap_req = getattr(intent, "capacity_requirement", None)
    if cap_req is None or cap_req <= 4:
        return []

    restaurants_by_id = (
        ctx.restaurants_by_id
        if ctx is not None
        else {r.id: r for r in safe_load_restaurants()}
    )
    if not restaurants_by_id:
        return []

    out: list[Violation] = []
    for idx, node in enumerate(itinerary.nodes):
        if node.target_kind != "restaurant":
            continue
        rid = node.target_id
        if not rid or rid not in restaurants_by_id:
            continue
        rest = restaurants_by_id[rid]
        cap = rest.capacity

        # 判定够不够：≥6 人 → 需要 six/eight/private_room 至少一种
        if cap_req >= 6:
            has_seat = cap.six or cap.eight or cap.private_room
            if has_seat:
                continue
        else:  # 5 人——four 桌坐不下，需要 six/eight/private_room
            has_seat = cap.six or cap.eight or cap.private_room
            if has_seat:
                continue

        out.append(
            Violation(
                code=ViolationCode.CAPACITY_REQUIREMENT_VIOLATED,
                severity=Severity.HARD,
                message=(
                    f"{humanize_node(idx, node)}({rest.name})桌型不够 {cap_req} 人就餐"
                    f"（仅 2/4 人桌，无 6 人桌 / 8 人桌 / 包间）。"
                    "请换支持大桌或包间的餐厅。"
                ),
                field_path=f"nodes[{idx}].target_id",
            )
        )
    return out


def check_dietary(
    itinerary: Itinerary,
    intent: Optional[IntentExtraction] = None,
    *,
    ctx: "CriticContext | None" = None,
) -> list[Violation]:
    """用餐 node 餐厅 tags 是否覆盖 intent.dietary_constraints。

    - intent 没饮食约束 → 跳过
    - node 不是 restaurant → 跳过
    - load 失败 → 跳过
    """
    if intent is None and ctx is not None:
        intent = ctx.intent
    if intent is None or not intent.dietary_constraints:
        return []

    restaurants_by_id = (
        ctx.restaurants_by_id
        if ctx is not None
        else {r.id: r for r in safe_load_restaurants()}
    )
    if not restaurants_by_id:
        return []

    constraints_set = set(intent.dietary_constraints)
    out: list[Violation] = []

    for idx, node in enumerate(itinerary.nodes):
        if node.target_kind != "restaurant":
            continue
        rid = node.target_id
        if not rid or rid not in restaurants_by_id:
            continue
        rest = restaurants_by_id[rid]
        rest_tags = set(rest.tags or [])
        if rest_tags & constraints_set:
            continue  # 至少命中一项，OK
        out.append(
            Violation(
                code=ViolationCode.DIETARY_VIOLATION,
                severity=Severity.HARD,  # B-2a: 升级为 HARD（gate 修复）
                message=(
                    f"{humanize_node(idx, node)}（{rest.name}）的标签不含用户饮食约束 "
                    f"{sorted(constraints_set)} 中任何一项。请换符合饮食偏好的餐厅。"
                ),
                field_path=f"nodes[{idx}].target_id",
            )
        )
    return out


def check_social_context(
    itinerary: Itinerary,
    intent: Optional[IntentExtraction] = None,
    *,
    ctx: "CriticContext | None" = None,
) -> list[Violation]:
    """social_context 与候选 suitable_for 的兼容性 critic。

    设计依据：agent/v2/social_compat.py 矩阵（Step 5 升级）。
    - BLOCKING → CRITICAL（必须 backprompt LLM 重做）
    - POOR     → WARNING（不打断，仅日志）
    - MATCH/ACCEPTABLE → 不报

    edge_v1：遍历 nodes 而非 stages；POI/Restaurant 节点分别走 evaluate_poi/_restaurant。
    """
    if intent is None and ctx is not None:
        intent = ctx.intent
    out: list[Violation] = []
    sc = (intent.social_context or "") if intent is not None else ""
    if not sc:
        return out

    try:
        from agent.planning.critic.social_compat import (
            CompatLevel,
            evaluate_poi,
            evaluate_restaurant,
        )
    except ImportError:
        return out

    pois_by_id = ctx.pois_by_id if ctx is not None else {p.id: p for p in safe_load_pois()}
    restaurants_by_id = (
        ctx.restaurants_by_id
        if ctx is not None
        else {r.id: r for r in safe_load_restaurants()}
    )

    for idx, node in enumerate(itinerary.nodes):
        if node.target_kind == "poi" and node.target_id in pois_by_id:
            poi = pois_by_id[node.target_id]
            level, reason = evaluate_poi(intent, poi)
            if level == CompatLevel.BLOCKING:
                out.append(
                    Violation(
                        code=ViolationCode.SOCIAL_CONTEXT_MISMATCH,
                        severity=Severity.HARD,
                        message=(
                            f"{humanize_node(idx, node)}（{poi.name}）与场景调性严重不匹配："
                            f"{reason}。请在候选预览中换其它 social_context 适配的 POI。"
                        ),
                        field_path=f"nodes[{idx}].target_id",
                    )
                )
            elif level == CompatLevel.POOR:
                out.append(
                    Violation(
                        code=ViolationCode.SOCIAL_CONTEXT_MISMATCH,
                        severity=Severity.SOFT,
                        message=(
                            f"{humanize_node(idx, node)}（{poi.name}）调性偏差："
                            f"{reason}（仍可接受，但更优候选可考虑换）。"
                        ),
                        field_path=f"nodes[{idx}].target_id",
                    )
                )
        elif node.target_kind == "restaurant" and node.target_id in restaurants_by_id:
            rest = restaurants_by_id[node.target_id]
            level, reason = evaluate_restaurant(intent, rest)
            if level == CompatLevel.BLOCKING:
                out.append(
                    Violation(
                        code=ViolationCode.SOCIAL_CONTEXT_MISMATCH,
                        severity=Severity.HARD,
                        message=(
                            f"{humanize_node(idx, node)}（{rest.name}）与场景调性严重不匹配："
                            f"{reason}。请在候选预览中换其它 social_context 适配的餐厅。"
                        ),
                        field_path=f"nodes[{idx}].target_id",
                    )
                )
            elif level == CompatLevel.POOR:
                out.append(
                    Violation(
                        code=ViolationCode.SOCIAL_CONTEXT_MISMATCH,
                        severity=Severity.SOFT,
                        message=(
                            f"{humanize_node(idx, node)}（{rest.name}）调性偏差："
                            f"{reason}（仍可接受，但更优候选可考虑换）。"
                        ),
                        field_path=f"nodes[{idx}].target_id",
                    )
                )

    # 保留旧的「order detail 多人位 vs 独处」检查（OrderRecord.detail 含人数文本）
    if "独处" in sc:
        for order in itinerary.orders:
            kind = order.kind or ""
            if "餐厅" in kind or order.target_kind == "restaurant":
                detail = order.detail or ""
                multi_signals = ["2 人", "三人", "四人", "六人", "≥2"]
                if any(sig in detail for sig in multi_signals):
                    out.append(
                        Violation(
                            code=ViolationCode.SOCIAL_CONTEXT_MISMATCH,
                            severity=Severity.HARD,
                            message=(
                                f"独处放空场景，但 {order.target_name} 预约 {detail}。"
                                "请改为单人位，或换符合「独处放空」的餐厅。"
                            ),
                            field_path="orders",
                        )
                    )

    return out


def check_tool_consistency(
    itinerary: Itinerary,
    tool_results: "dict | None | object" = _UNSET,
    *,
    ctx: "CriticContext | None" = None,
) -> list[Violation]:
    """验 itinerary 中 POI/Restaurant target_id 是否在工具实际返回的候选池里。

    防护场景：LLM 编造一个不存在的 POI ID（如 "P999"），蓝图 critic 通不过结构性检查
    但 target_id 实际不在搜索结果里——这是典型 hallucination，必须 backprompt。

    Args:
        itinerary: 待验证方案
        tool_results: dict 包含候选池，约定 key：
            - "pois": list[Poi]（可能为空 / 缺失）
            - "restaurants": list[Restaurant]
            为 None 时跳过（向后兼容旧调用）；候选池为空时跳过（避免 stub mode 误报）

    Returns:
        Violation 列表；每个不在候选池的 target_id 单独发一条 CRITICAL

    设计纪律：
    - 错误 message **不**暴露字段名（不写 "target_id"），用「方案中『XX』不在候选池中」
    - target_kind=home 不检查（home 不来自工具）
    - tool_results=None 或两个候选池都为空 → 跳过（stub mode / 无候选场景）

    `tool_results` 用 `_UNSET` 哨兵：显式传 None（含历史直调 `(itin, None)`）= 跳过反幻觉；
    完全不传 = 从 ctx.tool_results 取（注册表路径）。两者都解析为「None → 跳过」是等价的。
    """
    if tool_results is _UNSET:
        tool_results = ctx.tool_results if ctx is not None else None
    if tool_results is None:
        return []

    pois = tool_results.get("pois") or []
    restaurants = tool_results.get("restaurants") or []

    if not pois and not restaurants:
        # 候选池为空——可能是 stub mode 或候选耗尽，避免误报
        return []

    poi_ids = {getattr(p, "id", None) for p in pois}
    restaurant_ids = {getattr(r, "id", None) for r in restaurants}

    out: list[Violation] = []
    for idx, node in enumerate(itinerary.nodes):
        target_kind = node.target_kind
        if target_kind not in ("poi", "restaurant"):
            continue
        target_id = node.target_id
        if not target_id:
            continue

        valid_ids = poi_ids if target_kind == "poi" else restaurant_ids
        if not valid_ids:
            # 该类候选池为空，跳过（避免「只查了 POI 没查餐厅」时误报餐厅节点）
            continue
        if target_id in valid_ids:
            continue

        # 不在候选池——可能是 hallucination
        kind_label = "POI" if target_kind == "poi" else "餐厅"
        title = node.title or target_id
        out.append(
            Violation(
                code=ViolationCode.TOOL_RESPONSE_INCONSISTENCY,
                severity=Severity.HARD,
                message=(
                    f"{humanize_node(idx, node)}：方案中的{kind_label}「{title}」"
                    "不在候选池中，可能是 AI 编造的，请重新规划，"
                    f"只在工具实际返回的{kind_label}候选里挑选。"
                ),
                field_path=f"nodes[{idx}].target_id",
            )
        )
    return out


# ============================================================
# 用餐时段合理性（spec planning-pipeline-consolidation R1）
# ============================================================

# 茶点类 cuisine：可落午后非饭点时段（下午茶 / 咖啡 / 甜品）
_TEAHOUSE_CUISINES: frozenset[str] = frozenset({"下午茶", "咖啡", "烘焙甜品"})

# 午餐窗口 11:00-13:30；晚餐窗口 17:00-20:00；夜宵窗口 21:00-次日 2:00
_LUNCH_START_MIN = 11 * 60        # 11:00
_LUNCH_END_MIN = 13 * 60 + 30     # 13:30
_DINNER_START_MIN = 17 * 60       # 17:00
_DINNER_END_MIN = 20 * 60         # 20:00
_SUPPER_START_MIN = 21 * 60       # 21:00（夜宵；含烧烤/火锅等夜宵正餐）


def check_meal_time(
    itinerary: Itinerary, *, ctx: "CriticContext | None" = None
) -> list[Violation]:
    """正餐节点 start_time 是否落在合理饭点窗口（R1）。

    规则：
    - 茶点类餐厅（下午茶 / 咖啡 / 甜品）→ 跳过（可落午后任意时段）
    - 正餐类餐厅 → start_time 应落在午餐(11:00-13:30) / 晚餐(17:00-20:00) /
      夜宵(21:00 之后) 之一；否则触发 WARNING
    - WARNING 级不阻断 demo，但让 narration 体现「时段已调整」+ 可选 backprompt

    设计动机（S4 实测 bug）：下午 14:05 安排正餐火锅不符合常识。本 check 让
    critic 能检出"非饭点正餐"，触发 LLM 自纠或 narration 提示。
    """
    restaurants_by_id = (
        ctx.restaurants_by_id
        if ctx is not None
        else {r.id: r for r in safe_load_restaurants()}
    )
    if not restaurants_by_id:
        return []

    out: list[Violation] = []
    for idx, node in enumerate(itinerary.nodes):
        if node.target_kind != "restaurant":
            continue
        rid = node.target_id
        if not rid or rid not in restaurants_by_id:
            continue
        rest = restaurants_by_id[rid]
        cuisine = getattr(rest, "cuisine", "") or ""
        if cuisine in _TEAHOUSE_CUISINES:
            continue  # 茶点类不约束时段

        try:
            start_min = parse_hhmm(node.start_time)
        except (ValueError, AttributeError):
            continue  # 时间解析失败跳过（其它 check 会报）

        # None-guard (O4)：parse_hhmm 返回 None（不抛异常），
        # 直接用 None 做比较会抛 TypeError；跳过，让 check_time_parseable 报告。
        if start_min is None:
            continue

        in_lunch = _LUNCH_START_MIN <= start_min <= _LUNCH_END_MIN
        in_dinner = _DINNER_START_MIN <= start_min <= _DINNER_END_MIN
        in_supper = start_min >= _SUPPER_START_MIN
        if in_lunch or in_dinner or in_supper:
            continue  # 落在合理饭点窗口

        out.append(
            Violation(
                code=ViolationCode.MEAL_TIME_UNREASONABLE,
                severity=Severity.HARD,  # B-2a: 升级为 HARD（gate 修复）
                message=(
                    f"{humanize_node(idx, node)}（{rest.name}· {cuisine}）"
                    f"安排在 {node.start_time}，不在常规饭点（午餐 11:00-13:30 / "
                    f"晚餐 17:00-20:00 / 夜宵 21:00 后）。请把正餐调整到饭点时段，"
                    f"或在该时段安排下午茶 / 轻食类。"
                ),
                field_path=f"nodes[{idx}].start_time",
            )
        )
    return out
