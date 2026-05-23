"""critics_v2 —— Itinerary 客观约束兜底验证层（edge_v1）。

【为什么叫 critics_v2 而非 critics】

`backend/agent/critics.py` 已存在（旧规则 critic 的内部组件，由 planner_hybrid 用）。
本模块（v2）是 LangGraph `agent/graph/nodes/critic.py` 节点 + Pydantic AI ReAct
fallback 路径共用的 Itinerary 级 critic，与旧 critic 不冲突。

【edge_v1 重构后的 critic 模型】

输入是 `Itinerary(nodes=[home, ..., home], hops=[...])`，单位天然清晰：
- nodes：「在哪里、做什么、停留多久」
- hops：「相邻两节点间怎么过去、几分钟」

旧 stage 模型把"在 home 停 N 分钟"与"home→POI 通勤 N 分钟"塞同一字段，导致
critic 双重计算触发死循环（pitfalls P1-2026-05-22-commute-critic）。重构后：
- `_check_temporal_feasibility`：from_node.end + hop.minutes + buffer ≤ to_node.start（容差 2min）
- `_check_hop_feasibility`：遍历 hops，非 in_place 段调 `lookup_hop` 取 actual_min，
  断言 `hop.minutes >= actual_min - 2`（与 assemble 共享同一函数 → 同输入同输出）
- `_check_invariants`：hops 长度 / 首尾 home / home duration=0 三条结构断言（防御性兜底）

【Critic 纪律（硬性）】

- 不抛异常（违规返回 violations 列表，由调用方决定是否 ModelRetry / replan）
- 不调 LLM（critic 是算法不是 LLM）
- 不发明新 schema 模型（直接接受 Itinerary + IntentExtraction）
- field_path 字段仅供 trace / 调试使用，**format_violations_for_llm 不暴露 dot-path**
  给 LLM——LLM 只看人话「第 N 段」「目标点」（design.md 强约束）

【7 类 ViolationCode】

```
| Code                       | Severity (默认) | 触发条件                                       |
|----------------------------|----------------|-----------------------------------------------|
| INVARIANT_BROKEN           | CRITICAL       | hops 长度 / 首尾 home / home duration=0 任一违反 |
| NODES_INCOMPLETE           | CRITICAL       | mid nodes 数 < 1（行程退化为只有 home）         |
| DURATION_OUT_OF_RANGE      | CRITICAL       | total_minutes 不在 intent.duration_hours±30min |
| TIMELINE_INCONSISTENT      | CRITICAL       | hop.start 与 from_node.end / to_node.start 错位（容差 2min） |
| HOP_INFEASIBLE             | CRITICAL       | hop.minutes < lookup_hop(actual) - 2          |
| DISTANCE_EXCEEDED          | WARNING        | 单个 mid node 距家 > intent.distance_max_km   |
| RESTAURANT_FULL_UNRESOLVED | CRITICAL       | demo-aware：用餐 node start_time=17:00         |
| DIETARY_VIOLATION          | WARNING        | 餐厅 node tags 不覆盖 intent.dietary_constraints |
| SOCIAL_CONTEXT_MISMATCH    | CRITICAL/WARN  | social_compat 矩阵 BLOCKING/POOR              |
```

【不负责】

- ModelRetry / replan 触发逻辑（由 LangGraph critic_node / react_agent 决定）
- 主观文案生成（critic 输出 message 只是 LLM 修复种子，不是最终回话）
- 工具调用历史的事后分析（critic 看不到调用链，只看最终 itinerary）
- 节点级营业时间校验（在 agent.blueprint._opening_hours_critic 阶段处理）
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agent.lookup_hop import lookup_hop
from schemas.intent import IntentExtraction
from schemas.itinerary import ActivityNode, Hop, Itinerary


# ============================================================
# 枚举与数据结构
# ============================================================

class ViolationCode(str, Enum):
    """edge_v1 critic 触发码。

    与 schemas/errors.py 的 FailureReason 解耦——FailureReason 是
    Tool 失败原因，ViolationCode 是 Itinerary 级别的违规分类。

    edge_v1 重命名映射：
    - STAGES_INCOMPLETE → NODES_INCOMPLETE
    - COMMUTE_INFEASIBLE → HOP_INFEASIBLE
    - 新增 INVARIANT_BROKEN
    """

    INVARIANT_BROKEN = "invariant_broken"
    NODES_INCOMPLETE = "nodes_incomplete"
    DURATION_OUT_OF_RANGE = "duration_out_of_range"
    TIMELINE_INCONSISTENT = "timeline_inconsistent"
    HOP_INFEASIBLE = "hop_infeasible"
    DISTANCE_EXCEEDED = "distance_exceeded"
    RESTAURANT_FULL_UNRESOLVED = "restaurant_full_unresolved"
    DIETARY_VIOLATION = "dietary_violation"
    SOCIAL_CONTEXT_MISMATCH = "social_context_mismatch"
    AGE_DURATION_MISMATCH = "age_duration_mismatch"  # spec planning-quality-deep-review R4


class Severity(str, Enum):
    """违规等级。

    - CRITICAL：必须 backprompt / replan；调用方应把 violation 转成 prompt 让 LLM 重做
    - WARNING ：方案可继续上呈，但日志/调试时需关注（如 mock 数据本身的轻微偏差）
    """

    CRITICAL = "critical"
    WARNING = "warning"


class Violation(BaseModel):
    """一条违规记录。

    `message` 是给 LLM / 用户看的中文修复建议（必须自包含完整定位信息）；
    `field_path` 是 dot-path 风格的内部定位（如 "hops[2]" / "nodes[1].duration_min"），
    **仅用于 trace / 调试**——不暴露给 LLM（design.md 强约束）。
    """

    model_config = ConfigDict(extra="forbid")

    code: ViolationCode
    severity: Severity
    message: str = Field(
        ...,
        description="给 LLM / 用户看的中文修复建议；必须自包含「第几段、什么目标」",
    )
    field_path: str = Field(
        default="",
        description='内部 dot-path 定位，如 "hops[2]" / "nodes[1]"；不进 LLM prompt',
    )
    expected_range: Optional[tuple[int, int]] = Field(
        default=None,
        description=(
            "建议收敛区间 (lo, hi)。spec planning-quality-deep-review R4 引入。"
            "format_violations_for_llm 拼成「建议范围 lo-hi min」自然语言喂回 LLM——"
            "**不**暴露字段名 expected_range / nodes[i] / dot-path 给 LLM。"
        ),
    )


# ============================================================
# 常量
# ============================================================

# 时序容差（分钟）：hop / temporal feasibility 的浮动窗口
_TEMPORAL_TOLERANCE_MIN: int = 2

# hop_feasibility 容差（分钟）：hop.minutes 允许比 actual_min 少 2min
_HOP_FEASIBILITY_TOLERANCE_MIN: int = 2

# 默认时长容差（分钟）：[lo*60 - 30, hi*60 + 30]
_DURATION_TOLERANCE_MIN: int = 30

# distance critic 容差（km）
_DISTANCE_TOLERANCE_KM: float = 0.5

# demo-aware 17:00 满座埋点
_DEMO_FULL_TIME: str = "17:00"


# ============================================================
# 时间 / 数据加载 helper
# ============================================================


def _parse_hhmm(value: str) -> Optional[int]:
    """把 "14:30" 转成总分钟数（870）。格式不合法返 None。"""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        h = int(parts[0])
        mn = int(parts[1])
    except ValueError:
        return None
    # 允许 24-29h 跨日表示（夜宵 23:00-01:00 已在 schema 兼容）
    if not (0 <= h <= 29 and 0 <= mn <= 59):
        return None
    return h * 60 + mn


def _safe_load_pois():
    """容错加载 POI；mock 数据缺失时返空列表，跳过相关检查。"""
    try:
        from data.loader import load_pois  # 延迟 import，避免无 mock 数据时炸
        return load_pois()
    except Exception:
        return []


def _safe_load_restaurants():
    try:
        from data.loader import load_restaurants
        return load_restaurants()
    except Exception:
        return []


def _safe_load_user_profile(user_id: str = "demo_user"):
    """容错加载 UserProfile（含 transport_preference / home_location 坐标）。"""
    try:
        from data.loader import load_user_profile, load_user_profiles
        # 优先按 user_id 查多用户字典，找不到回退默认
        try:
            profiles = load_user_profiles()
            if user_id in profiles:
                return profiles[user_id]
        except Exception:
            pass
        return load_user_profile()
    except Exception:
        return None


def _resolve_node_location(node: ActivityNode, *, user_profile=None) -> tuple[Optional[float], Optional[float]]:
    """从 ActivityNode 提取 (lat, lng)。

    - target_kind="home"：优先 user_profile.home_location，回退 node 自带坐标
    - 其它：直接读 node.lat/lng（assemble 已写入）

    edge_v1 简化：node 永远有明确 target，不存在「过程段」需要兜底坐标。
    """
    if node.target_kind == "home" and user_profile is not None:
        loc = getattr(user_profile, "home_location", None)
        if loc is not None and loc.lat is not None and loc.lng is not None:
            return loc.lat, loc.lng
    return node.lat, node.lng


def _humanize_node(idx: int, node: ActivityNode) -> str:
    """把 nodes[idx] 翻译成人话「第 N 段「kind · title」」。"""
    label = node.title or node.target_id or "未命名"
    return f"第 {idx + 1} 段「{node.kind} · {label}」"


def _resolve_transport_preference(profile) -> str:
    """从 user_profile 取 transport_preference，越界值回退 taxi。"""
    pref = getattr(profile, "transport_preference", "taxi") or "taxi"
    if pref in ("walking", "taxi", "bus"):
        return pref
    return "taxi"


# ============================================================
# 单项 critic
# ============================================================


def _check_invariants(itinerary: Itinerary) -> list[Violation]:
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


def _check_nodes_incomplete(itinerary: Itinerary) -> list[Violation]:
    """验中间节点（非首尾 home）至少 1 个。

    退化情形：nodes=[home, home] / hops=[in_place 0min] —— 用户原地不动，
    没有任何活动节点，行程毫无意义；触发 CRITICAL 让 planner replan。

    边界：design.md 显式声明「单段方案（如只想吃饭）→ nodes=[home, R024, home]」
    是合法的；所以最小允许 mid_nodes_count == 1。
    """
    if len(itinerary.nodes) < 3:
        # 仅 [home, home] 这种退化 itinerary
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


def _check_duration(itinerary: Itinerary, intent: IntentExtraction) -> list[Violation]:
    """验 total_minutes 是否落在 intent.duration_hours±30min 容差。"""
    duration = intent.duration_hours
    if not duration or len(duration) != 2:
        return []  # schema 校验已保证此处必有值，但防御一下

    lo, hi = int(duration[0]), int(duration[1])
    lo_tol = lo * 60 - _DURATION_TOLERANCE_MIN
    hi_tol = hi * 60 + _DURATION_TOLERANCE_MIN

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


def _check_temporal_feasibility(itinerary: Itinerary) -> list[Violation]:
    """验时序自洽：hop.start ≈ from_node.end 且 to_node.start ≥ hop.end + buffer。

    设计依据（design.md _check_temporal_feasibility 伪代码）：
    - 容差 2min（assemble 内部按整数分钟取整，可能轻微浮动）
    - 仅在「assemble 输出严格自洽」前提下兜底；正常路径下永远不触发

    edge_v1 优势：hop / node 时间字段彻底分离，不再有「stage.duration 同时表达
    停留与通勤」的歧义。
    """
    out: list[Violation] = []
    nodes = itinerary.nodes
    hops = itinerary.hops

    # invariant 已由 _check_invariants 兜底；此处仅遍历 min(len(hops), len(nodes)-1)
    bound = min(len(hops), len(nodes) - 1)
    for i in range(bound):
        hop = hops[i]
        from_node = nodes[i]
        to_node = nodes[i + 1]

        from_start = _parse_hhmm(from_node.start_time)
        hop_start = _parse_hhmm(hop.start_time)
        to_start = _parse_hhmm(to_node.start_time)

        if from_start is None or hop_start is None or to_start is None:
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.CRITICAL,
                    message=(
                        f"{_humanize_node(i, from_node)} 或下一段时间格式不合法（应为 HH:MM）。"
                        "请重新生成行程使所有时间戳为合法 HH:MM。"
                    ),
                    field_path=f"hops[{i}].start_time",
                )
            )
            continue

        from_end = from_start + from_node.duration_min
        hop_end = hop_start + hop.minutes

        # 1. hop.start 与 from_node.end 必须紧接（容差 2min）
        if abs(hop_start - from_end) > _TEMPORAL_TOLERANCE_MIN:
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.CRITICAL,
                    message=(
                        f"{_humanize_node(i, from_node)} 结束于 {_fmt_hhmm(from_end)}，"
                        f"但下一段通勤却从 {hop.start_time} 开始（错位 "
                        f"{abs(hop_start - from_end)} 分钟）。请让通勤紧接节点结束时刻。"
                    ),
                    field_path=f"hops[{i}].start_time",
                )
            )

        # 2. to_node.start 必须 ≥ hop.end + buffer（容差 2min）
        required_to_start = hop_end + hop.buffer_min
        if to_start < required_to_start - _TEMPORAL_TOLERANCE_MIN:
            shortage = required_to_start - to_start
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.CRITICAL,
                    message=(
                        f"{_humanize_node(i + 1, to_node)} 开始于 {to_node.start_time}，"
                        f"早于通勤完成（{_fmt_hhmm(hop_end)}）+ buffer({hop.buffer_min}min) "
                        f"应有的 {_fmt_hhmm(required_to_start)}，缺 {shortage} 分钟。"
                        "请把下一段开始时间推迟到通勤完成 + buffer 之后。"
                    ),
                    field_path=f"nodes[{i + 1}].start_time",
                )
            )

    return out


def _check_hop_feasibility(
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
        # 没用户画像无法解析交通偏好，跳过——critic 不应因数据缺失误伤
        return out

    transport_pref = _resolve_transport_preference(user_profile)
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

        actual_min, actual_mode, actual_path_type = lookup_hop(
            from_node.target_id,
            to_node.target_id,
            transport_pref,  # type: ignore[arg-type]
            user_profile,
        )

        if hop.minutes < actual_min - _HOP_FEASIBILITY_TOLERANCE_MIN:
            shortage = actual_min - hop.minutes
            out.append(
                Violation(
                    code=ViolationCode.HOP_INFEASIBLE,
                    severity=Severity.CRITICAL,
                    message=(
                        f"{_humanize_node(i, from_node)} 去往 "
                        f"{_humanize_node(i + 1, to_node)} 的通勤实际需要约 "
                        f"{actual_min} 分钟（{actual_mode}），"
                        f"但行程里这段 hop 只留了 {hop.minutes} 分钟，"
                        f"缺 {shortage} 分钟（容差 2 分钟内不算违规）。"
                        f"请改为更近的目标点，或让系统按 routes.json 重算 hop 分钟。"
                    ),
                    field_path=f"hops[{i}].minutes",
                )
            )

    return out


def _check_age_aware_duration(
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

    # 取最严 cap（与 blueprint.py:_resolve_age_caps 同源逻辑，避免循环 import 在此重写）
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
            start_min = _parse_hhmm(getattr(node, "start_time", "")) if hasattr(node, "start_time") else None
            end_min = _parse_hhmm(getattr(node, "end_time", "")) if hasattr(node, "end_time") else None
            if start_min is None or end_min is None:
                continue
            duration = end_min - start_min

        if duration > min_cap:
            out.append(
                Violation(
                    code=ViolationCode.AGE_DURATION_MISMATCH,
                    severity=Severity.CRITICAL,
                    message=(
                        f"{_humanize_node(idx, node)} 停留 {duration} 分钟"
                        f"超出年龄约束（{reason_text}）"
                    ),
                    field_path=f"nodes[{idx}].duration_min",
                    expected_range=expected,
                )
            )

    return out


def _check_distance(itinerary: Itinerary, intent: IntentExtraction) -> list[Violation]:
    """单个 mid node 距家距离 > intent.distance_max_km → warning。

    edge_v1：直接遍历 nodes（home 节点 distance 无意义跳过）。
    """
    out: list[Violation] = []
    max_km = intent.distance_max_km
    if max_km is None or max_km <= 0:
        return out

    pois_by_id = {p.id: p for p in _safe_load_pois()}
    restaurants_by_id = {r.id: r for r in _safe_load_restaurants()}

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
        if target_distance > max_km + _DISTANCE_TOLERANCE_KM:
            out.append(
                Violation(
                    code=ViolationCode.DISTANCE_EXCEEDED,
                    severity=Severity.WARNING,
                    message=(
                        f"{_humanize_node(idx, node)} 距家 {target_distance:.1f}km，"
                        f"超过用户期望 {max_km:.1f}km。如条件允许请换距离更近的候选。"
                    ),
                    field_path=f"nodes[{idx}].target_id",
                )
            )

    return out


def _check_demo_restaurant_full(itinerary: Itinerary) -> list[Violation]:
    """demo-aware 满座埋点：mock 餐厅在「reservation_slots[time].available=False」是 RESTAURANT_FULL 异常埋点。

    edge_v1：从「找含 restaurant_id 的 stage」改为「找 target_kind=restaurant 的 node」。

    spec planning-quality-deep-review R4：从「写死 17:00」改为查 mock 真值——
    在 mock 数据 `Restaurant.reservation_slots` 中查 node.start_time 对应的 slot
    是否 `available=False`。如果是，强制让 LLM 换其它时段（不再依赖 17:00 硬编码）。

    通过 ENABLE_DEMO_FULL_CHECK 环境变量控制开关（默认开）。
    """
    enabled = (os.getenv("ENABLE_DEMO_FULL_CHECK") or "1").strip().lower()
    if enabled in ("0", "false", "no", "off"):
        return []

    restaurants_by_id = {r.id: r for r in _safe_load_restaurants()}

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
        # 查 reservation_slots[time].available
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
                    f"{_humanize_node(idx, node)} 在 {node_time} 已满座"
                    f"（mock 餐厅 reservation_slots 标记 available=False）。"
                    "请调用 check_restaurant_availability 验证实际可用性，"
                    "或换到其它有空档的时段。"
                ),
                field_path=f"nodes[{idx}].start_time",
            )
        )
    return out


def _check_dietary(itinerary: Itinerary, intent: IntentExtraction) -> list[Violation]:
    """用餐 node 餐厅 tags 是否覆盖 intent.dietary_constraints。

    - intent 没饮食约束 → 跳过
    - node 不是 restaurant → 跳过
    - load 失败 → 跳过
    """
    if not intent.dietary_constraints:
        return []

    restaurants_by_id = {r.id: r for r in _safe_load_restaurants()}
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
                    f"{_humanize_node(idx, node)}（{rest.name}）的标签不含用户饮食约束 "
                    f"{sorted(constraints_set)} 中任何一项。建议换符合饮食偏好的餐厅。"
                ),
                field_path=f"nodes[{idx}].target_id",
            )
        )
    return out


def _check_social_context(
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
        from agent.v2.social_compat import (
            CompatLevel,
            evaluate_poi,
            evaluate_restaurant,
        )
    except ImportError:
        return out

    pois_by_id = {p.id: p for p in _safe_load_pois()}
    restaurants_by_id = {r.id: r for r in _safe_load_restaurants()}

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
                            f"{_humanize_node(idx, node)}（{poi.name}）与场景调性严重不匹配："
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
                            f"{_humanize_node(idx, node)}（{poi.name}）调性偏差："
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
                            f"{_humanize_node(idx, node)}（{rest.name}）与场景调性严重不匹配："
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
                            f"{_humanize_node(idx, node)}（{rest.name}）调性偏差："
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


# ============================================================
# 时间格式化（critic 内部用）
# ============================================================


def _fmt_hhmm(total: int) -> str:
    """工具函数：分钟数 → HH:MM。"""
    total = max(0, min(total, 24 * 60 - 1))
    return f"{total // 60:02d}:{total % 60:02d}"


# ============================================================
# 主入口
# ============================================================


def validate_itinerary(
    itinerary: Itinerary,
    intent: IntentExtraction,
    *,
    user_id: str = "demo_user",
) -> list[Violation]:
    """跑全套 critic 检查。返回 violations 列表（可能为空）。

    顺序约定（先「结构性 / 强制性」后「语义性 / 偏好性」）：
    1. INVARIANT_BROKEN（防御性兜底）
    2. NODES_INCOMPLETE（mid 节点至少 1 个）
    3. DURATION_OUT_OF_RANGE（总时长容差）
    4. TIMELINE_INCONSISTENT（_check_temporal_feasibility）
    5. HOP_INFEASIBLE（_check_hop_feasibility）
    6. DISTANCE_EXCEEDED（warning）
    7. RESTAURANT_FULL_UNRESOLVED（demo-aware）
    8. DIETARY_VIOLATION（warning）
    9. SOCIAL_CONTEXT_MISMATCH（critical / warning 分级）

    Args:
        itinerary: 要校验的方案（已通过 Pydantic 构造）。
        intent:    用户意图，提供 duration_hours / distance_max_km / dietary 等约束。
        user_id:   用于查 UserProfile（含 home_location / transport_preference）。

    Returns:
        Violation 列表；调用方据 severity 决定是否 backprompt / replan。
    """
    profile = _safe_load_user_profile(user_id)

    violations: list[Violation] = []
    violations.extend(_check_invariants(itinerary))
    violations.extend(_check_nodes_incomplete(itinerary))
    violations.extend(_check_duration(itinerary, intent))
    violations.extend(_check_temporal_feasibility(itinerary))
    violations.extend(_check_hop_feasibility(itinerary, user_profile=profile))
    violations.extend(_check_distance(itinerary, intent))
    violations.extend(_check_demo_restaurant_full(itinerary))
    violations.extend(_check_dietary(itinerary, intent))
    violations.extend(_check_social_context(itinerary, intent))
    # spec planning-quality-deep-review R4：ILS 路径年龄感知 critic（镜像）
    violations.extend(_check_age_aware_duration(itinerary, intent))
    return violations


def format_violations_for_llm(violations: list[Violation]) -> str:
    """把 critical violations 格式化成给 LLM 的 backprompt 消息。

    【人话约束（design.md 强约束）】

    输出**不暴露 dot-path** 字段路径——LLM 只看「第 N 段」「目标点」「分钟」等
    自然语言。`Violation.field_path` 仅用于 trace / 调试，绝不进 LLM prompt。

    spec planning-quality-deep-review R4：
    - 若 violation 含 `expected_range=(lo, hi)`，message 末尾追加「（建议范围 lo-hi min）」
    - **不**暴露字段名 `expected_range` / `nodes[i]` 等 dot-path

    - 0 critical → 返回空字符串（调用方据此决定不 backprompt）
    - ≥1 critical → 返回中文修复 prompt（编号 + message）
    - warning 级别**不**进入此消息（避免噪声把 LLM 注意力分散）
    """
    critical = [v for v in violations if v.severity == Severity.CRITICAL]
    if not critical:
        return ""

    lines = [f"你产出的行程方案有 {len(critical)} 处违规需要修复："]
    for i, v in enumerate(critical, 1):
        # 注意：刻意不拼接 v.field_path（design.md：不暴露 dot-path）
        msg = v.message
        if v.expected_range is not None:
            lo, hi = v.expected_range
            msg = f"{msg}（建议范围 {lo}-{hi} min）"
        lines.append(f"{i}. {msg}")
    lines.append("请按上述建议重新调用工具或调整方案，重新输出 ItineraryResponse。")
    return "\n".join(lines)


__all__ = [
    "ViolationCode",
    "Severity",
    "Violation",
    "validate_itinerary",
    "format_violations_for_llm",
]
