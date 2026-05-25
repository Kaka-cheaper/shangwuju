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
from typing import Optional

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


def check_invariants(itinerary: Itinerary) -> list[Violation]:
    """edge_v1 三条结构不变量（防御性兜底）。

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
                severity=Severity.CRITICAL,
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
                    severity=Severity.CRITICAL,
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
                    severity=Severity.CRITICAL,
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
                    severity=Severity.CRITICAL,
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
                    severity=Severity.CRITICAL,
                    message=(
                        f"行程结构不变量违反：尾节点（home）停留时长应为 0 "
                        f"（实际 {last.duration_min} 分钟）。"
                    ),
                    field_path="nodes[-1].duration_min",
                )
            )

    return out


def check_nodes_incomplete(itinerary: Itinerary) -> list[Violation]:
    """验中间节点（非首尾 home）至少 1 个。

    退化情形：nodes=[home, home] / hops=[in_place 0min] —— 用户原地不动，
    没有任何活动节点，行程毫无意义；触发 CRITICAL 让 planner replan。

    边界：design.md 显式声明「单段方案（如只想吃饭）→ nodes=[home, R024, home]」
    是合法的；所以最小允许 mid_nodes_count == 1。
    """
    if len(itinerary.nodes) < 3:
        return [
            Violation(
                code=ViolationCode.NODES_INCOMPLETE,
                severity=Severity.CRITICAL,
                message=(
                    "行程中间没有任何活动节点（nodes 仅含首尾 home）。"
                    "请至少安排一个 POI 或餐厅作为活动主体。"
                ),
                field_path="nodes",
            )
        ]
    return []


def check_duration(itinerary: Itinerary, intent: IntentExtraction) -> list[Violation]:
    """验 total_minutes 是否落在 intent.duration_hours±30min 容差。"""
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
                severity=Severity.CRITICAL,
                message=(
                    f"行程总时长 {actual} 分钟（约 {actual / 60:.1f}h）"
                    f"不在用户期望的 {lo}-{hi}h（含 ±30min 容差）内。{advice}"
                ),
                field_path="total_minutes",
            )
        ]
    return []


def check_temporal_feasibility(itinerary: Itinerary) -> list[Violation]:
    """验时序自洽：hop.start ≈ from_node.end 且 to_node.start ≥ hop.end + buffer。

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
                    severity=Severity.CRITICAL,
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
                    severity=Severity.CRITICAL,
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
                    severity=Severity.CRITICAL,
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
) -> list[Violation]:
    """验 hop.minutes ≥ lookup_hop 实际值 - 容差。

    设计依据（design.md _check_hop_feasibility 伪代码 + Property 5）：
    - 与 assemble 共享同一 `lookup_hop` 函数 → 同输入同输出 → 不会漂移
    - in_place hop（minutes=0 / from_id == to_id）跳过：恒可达
    - hop.minutes < actual - 2 → CRITICAL（hackathon 防御性兜底）
    - 数据缺失（lookup_hop 4 级兜底返 15min）也按 actual=15 比较，仍可触发
    """
    out: list[Violation] = []

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
                    severity=Severity.CRITICAL,
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
    itinerary: Itinerary, intent: IntentExtraction
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
                    severity=Severity.CRITICAL,
                    message=(
                        f"{humanize_node(idx, node)} 停留 {duration} 分钟"
                        f"超出年龄约束（{reason_text}）"
                    ),
                    field_path=f"nodes[{idx}].duration_min",
                    expected_range=expected,
                )
            )

    return out


def check_distance(itinerary: Itinerary, intent: IntentExtraction) -> list[Violation]:
    """单个 mid node 距家距离 > intent.distance_max_km → warning。

    edge_v1：直接遍历 nodes（home 节点 distance 无意义跳过）。
    """
    out: list[Violation] = []
    max_km = intent.distance_max_km
    if max_km is None or max_km <= 0:
        return out

    pois_by_id = {p.id: p for p in safe_load_pois()}
    restaurants_by_id = {r.id: r for r in safe_load_restaurants()}

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
                    severity=Severity.WARNING,
                    message=(
                        f"{humanize_node(idx, node)} 距家 {target_distance:.1f}km，"
                        f"超过用户期望 {max_km:.1f}km。如条件允许请换距离更近的候选。"
                    ),
                    field_path=f"nodes[{idx}].target_id",
                )
            )

    return out


def check_demo_restaurant_full(itinerary: Itinerary) -> list[Violation]:
    """demo-aware 满座埋点：mock 餐厅在「reservation_slots[time].available=False」是 RESTAURANT_FULL 异常埋点。

    spec planning-quality-deep-review R4：从「写死 17:00」改为查 mock 真值——
    在 mock 数据 `Restaurant.reservation_slots` 中查 node.start_time 对应的 slot
    是否 `available=False`。

    通过 ENABLE_DEMO_FULL_CHECK 环境变量控制开关（默认开）。
    """
    enabled = (os.getenv("ENABLE_DEMO_FULL_CHECK") or "1").strip().lower()
    if enabled in ("0", "false", "no", "off"):
        return []

    restaurants_by_id = {r.id: r for r in safe_load_restaurants()}

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
                severity=Severity.CRITICAL,
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


def check_capacity(itinerary: Itinerary, intent: IntentExtraction) -> list[Violation]:
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
    cap_req = getattr(intent, "capacity_requirement", None)
    if cap_req is None or cap_req <= 4:
        return []

    restaurants_by_id = {r.id: r for r in safe_load_restaurants()}
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
                severity=Severity.CRITICAL,
                message=(
                    f"{humanize_node(idx, node)}({rest.name})桌型不够 {cap_req} 人就餐"
                    f"（仅 2/4 人桌，无 6 人桌 / 8 人桌 / 包间）。"
                    "请换支持大桌或包间的餐厅。"
                ),
                field_path=f"nodes[{idx}].target_id",
            )
        )
    return out


def check_dietary(itinerary: Itinerary, intent: IntentExtraction) -> list[Violation]:
    """用餐 node 餐厅 tags 是否覆盖 intent.dietary_constraints。

    - intent 没饮食约束 → 跳过
    - node 不是 restaurant → 跳过
    - load 失败 → 跳过
    """
    if not intent.dietary_constraints:
        return []

    restaurants_by_id = {r.id: r for r in safe_load_restaurants()}
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
                severity=Severity.WARNING,
                message=(
                    f"{humanize_node(idx, node)}（{rest.name}）的标签不含用户饮食约束 "
                    f"{sorted(constraints_set)} 中任何一项。建议换符合饮食偏好的餐厅。"
                ),
                field_path=f"nodes[{idx}].target_id",
            )
        )
    return out


def check_social_context(
    itinerary: Itinerary, intent: IntentExtraction
) -> list[Violation]:
    """social_context 与候选 suitable_for 的兼容性 critic。

    设计依据：agent/v2/social_compat.py 矩阵（Step 5 升级）。
    - BLOCKING → CRITICAL（必须 backprompt LLM 重做）
    - POOR     → WARNING（不打断，仅日志）
    - MATCH/ACCEPTABLE → 不报

    edge_v1：遍历 nodes 而非 stages；POI/Restaurant 节点分别走 evaluate_poi/_restaurant。
    """
    out: list[Violation] = []
    sc = intent.social_context or ""
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

    pois_by_id = {p.id: p for p in safe_load_pois()}
    restaurants_by_id = {r.id: r for r in safe_load_restaurants()}

    for idx, node in enumerate(itinerary.nodes):
        if node.target_kind == "poi" and node.target_id in pois_by_id:
            poi = pois_by_id[node.target_id]
            level, reason = evaluate_poi(intent, poi)
            if level == CompatLevel.BLOCKING:
                out.append(
                    Violation(
                        code=ViolationCode.SOCIAL_CONTEXT_MISMATCH,
                        severity=Severity.CRITICAL,
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
                        severity=Severity.WARNING,
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
                        severity=Severity.CRITICAL,
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
                        severity=Severity.WARNING,
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
                            severity=Severity.CRITICAL,
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
    tool_results: dict | None,
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
    """
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
                severity=Severity.CRITICAL,
                message=(
                    f"{humanize_node(idx, node)}：方案中的{kind_label}「{title}」"
                    "不在候选池中，可能是 AI 编造的，请重新规划，"
                    f"只在工具实际返回的{kind_label}候选里挑选。"
                ),
                field_path=f"nodes[{idx}].target_id",
            )
        )
    return out
