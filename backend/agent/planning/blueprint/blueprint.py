"""agent.blueprint —— LLM-First Planner 的"行程蓝图"数据结构 + 蓝图级 Critic（edge_v1）。

【为什么需要蓝图】（参考 problem.md 问题 14 / pitfalls.md P1-2026-05-17）

历史包袱：rule planner 把"5 段写死 + 14:00 起 + POI→餐厅顺序"当默认，
导致 24h 营业餐厅、夜宵、早茶、单段方案被强行套到下午局模板，违反
LLM-Modulo（Kambhampati NeurIPS 2024）"LLM 决主观、算法决客观"原则。

【edge_v1 的本质转变】

旧 BlueprintStage（已删）：`kind / start_time / duration_min / target_kind / target_id`
- LLM 既要选目标，又要算时刻、又要算通勤——典型职责漂移
- target_kind="none" 用来表达"出发 / 转场 / 返回"过程段，与 hop 概念重叠

新 BlueprintNode：`kind / target_kind / target_id / duration_min / note`
- LLM 只决定 **「在哪里、做什么、停留多久」**（mid nodes）
- 系统（assemble_from_blueprint）自动补 home 首尾节点 + 自动按 routes.json 算 hop 通勤
- target_kind 只允许 poi / restaurant，**没有 NONE 过程段**——通勤是 hop 不是 node

【蓝图级 Critic 的职责（也变小了）】

旧 critic 验「段时序 + 段时长 + 段间通勤」——通勤校验现已下沉到
`agent/v2/critics_v2._check_hop_feasibility`（在拿到 Itinerary 后验 hop 实际可达）。

新 blueprint critic 只验：
1. _temporal_critic：nodes 顺序累加后时间区间不重叠（结构合法性兜底）
2. _duration_critic：每个 node.duration_min 在合理区间（≥ MIN，≤ MAX）
3. _opening_hours_critic：按 preferred_start_time + 累积 duration 推算每个 node 的
   开始时刻，粗略校验目标 POI/餐厅营业时间覆盖

注意：blueprint critic **不验通勤可达性**——LLM 不输出通勤时间，谈何"通勤够不够"。
hop 时间够不够由后续 critics_v2 在 Itinerary 拼装完毕后验。

不负责：
- LLM 调用与 prompt（在 agent/blueprint_llm.py / agent/prompts/blueprint_prompt.py）
- 蓝图→Itinerary 拼装（在 agent/assemble_blueprint.py）
- Itinerary 级别校验（在 agent/v2/critics_v2.py）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, field_validator

from data.loader import load_pois, load_restaurants
from schemas.intent import IntentExtraction


# ============================================================
# 单段时长合理区间（仅作硬下/上限兜底；细分场景由 Itinerary 级 critic 把关）
# ============================================================

MIN_NODE_DURATION_MIN: int = 10
"""单个节点的最短停留时长（分钟）。低于此视为 LLM 误填。"""

MAX_NODE_DURATION_MIN: int = 300
"""单个节点的最长停留时长（分钟，5 小时）。超过此视为 LLM 误填。"""


# ============================================================
# 时间工具（HH:MM ↔ 分钟）
# ============================================================

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _parse_time_to_minutes(t: str) -> int:
    """把 "HH:MM" 解析为分钟数（00:00 → 0，14:30 → 870）。"""
    if not _TIME_RE.match(t):
        raise ValueError(f"时间字符串必须是 HH:MM 格式，实际 {t!r}")
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _minutes_to_time(total: int) -> str:
    """把分钟数转回 "HH:MM"；超 24h 按 mod 24 截断。"""
    total = max(0, total) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


# ============================================================
# 节点目标类型（删除了 NONE）
# ============================================================


class BlueprintTargetKind(str, Enum):
    """蓝图节点的目标实体类型。

    edge_v1 移除了 NONE：通勤过程在新模型里是 hop（边），不是 node（节点）。
    LLM 蓝图里只输出"具体目标"——POI 或餐厅。
    """

    POI = "poi"
    RESTAURANT = "restaurant"


# ============================================================
# BlueprintNode（LLM 输出契约）
# ============================================================


class BlueprintNode(BaseModel):
    """LLM 输出的中间节点契约。

    LLM 只决定 `target_id + duration_min + kind`，**不决定时间、不决定通勤**。
    首尾的 home 节点由 assemble_from_blueprint 自动补；
    节点间的 hop（通勤）由 lookup_hop 自动算。
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        ...,
        min_length=1,
        description='节点性质中文标签：主活动 / 用餐 / 夜宵 / 自由 / 早茶 / 晨练 等自由文本',
    )
    target_kind: BlueprintTargetKind = Field(
        ..., description="节点目标类型：poi / restaurant（不允许 home / 过程段）"
    )
    target_id: str = Field(
        ..., min_length=1, description="对应 mock_data.pois.id / mock_data.restaurants.id"
    )
    duration_min: NonNegativeInt = Field(
        ..., description="在该节点的停留时长（分钟，不含通勤）；建议 ≥10 ≤300"
    )
    note: Optional[str] = Field(
        default=None,
        description='给前端的补充提示文案，如"已预约 17:00 三人位"',
    )
    not_before_start: Optional[str] = Field(
        default=None,
        description=(
            '节点最早开始时刻 "HH:MM"（如餐厅预约 chosen_time）。'
            "assemble_from_blueprint 在自然到达早于此刻时，把节点开始推迟到此刻"
            "（差额为餐前空闲/休息），让排定时刻与 note/reservation 自洽"
            "（ADR-0009 决策 2·乙）。默认 None=不约束，LLM 路径不设即 no-op。"
        ),
    )


# ============================================================
# PlanBlueprint（LLM 输出的完整蓝图）
# ============================================================


class PlanBlueprint(BaseModel):
    """LLM 输出的完整行程蓝图（mid nodes 列表）。

    **不**含首尾 home（assemble 自动加），**不**含 hops（assemble 自动算）。
    """

    model_config = ConfigDict(extra="forbid")

    nodes: list[BlueprintNode] = Field(
        ...,
        min_length=1,
        description="按时间顺序排列的中间节点（mid nodes，不含 home 首尾）",
    )
    preferred_start_time: str = Field(
        default="14:00",
        description='蓝图整体偏好的开始时刻 HH:MM（assemble 算时间从此刻起）',
    )
    rationale: str = Field(
        default="", description="LLM 对方案的简短中文 rationale（用于 DecisionTrace）"
    )

    @field_validator("preferred_start_time")
    @classmethod
    def _check_start_time_format(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError(
                f"preferred_start_time 必须是 HH:MM 格式，实际 {v!r}"
            )
        return v


# ============================================================
# 推导：根据 preferred_start_time + 累积 duration 算每个 node 的 [start, end]
# ============================================================


def _derive_node_windows(blueprint: PlanBlueprint) -> list[tuple[int, int]]:
    """按 preferred_start_time 起，把 nodes 的 duration_min 累加为 [start, end] 区间（分钟）。

    新蓝图不含 hop，本函数**不**给 hop 留时间——只用于粗略校验「营业时间覆盖」
    与「时序结构合法」。真实带 hop 的时间轴由 assemble_from_blueprint 计算。

    Returns:
        list of (start_min, end_min)，长度与 blueprint.nodes 相同。
    """
    cursor = _parse_time_to_minutes(blueprint.preferred_start_time)
    out: list[tuple[int, int]] = []
    for node in blueprint.nodes:
        start = cursor
        end = cursor + node.duration_min
        out.append((start, end))
        cursor = end
    return out


# ============================================================
# Critic 函数（任务 R2：返回 list[str] 的违规描述）
# ============================================================


def _temporal_critic(blueprint: PlanBlueprint) -> list[str]:
    """C1：nodes 时间区间不重叠 + 单调递增 + 不跨越 24:00 边界。

    新蓝图按 preferred_start_time + 累加 duration 推算每个 node 的 [start, end]，
    数学上**只要 duration_min ≥ 0 即不可能重叠**。本 critic 主要兜底以下异常：

    - 末尾时间溢出 24:00（导致跨日，前端时间轴渲染会异常）
    - 万一上游构造蓝图时强塞了非顺序数据（防御性）

    Returns:
        list[str]：每条违规一段中文描述；合法时返空 list。
    """
    out: list[str] = []
    if not blueprint.nodes:
        return out

    windows = _derive_node_windows(blueprint)

    # 检查 1：相邻 node 区间不重叠（[start, end) 严格单调）
    for i in range(1, len(windows)):
        prev_end = windows[i - 1][1]
        cur_start = windows[i][0]
        if cur_start < prev_end:
            prev_kind = blueprint.nodes[i - 1].kind
            cur_kind = blueprint.nodes[i].kind
            out.append(
                f"节点「{prev_kind}」与「{cur_kind}」时序重叠："
                f"前者结束于 {_minutes_to_time(prev_end)}，"
                f"后者开始于 {_minutes_to_time(cur_start)}"
            )

    # 检查 2：末尾不超过 24:00（粗略，跨日场景由前端 v2 处理；hackathon 范围拒绝）
    last_end = windows[-1][1]
    if last_end > 24 * 60:
        out.append(
            f"蓝图末尾时间 {_minutes_to_time(last_end)} 跨越 24:00，"
            f"暂不支持跨日蓝图（preferred_start_time={blueprint.preferred_start_time}，"
            f"累计 duration={sum(n.duration_min for n in blueprint.nodes)} 分钟）"
        )

    return out


def _duration_critic(blueprint: PlanBlueprint) -> list[str]:
    """C2：每个 node 的停留时长在合理区间（[MIN_NODE_DURATION_MIN, MAX_NODE_DURATION_MIN]）。

    旧版还会拿 IntentExtraction.duration_hours 校验"总时长不超用户上限"——
    这一职责已下沉到 Itinerary 级别（critics_v2._check_duration），blueprint critic
    在还没拼出 hop 的阶段去算总时长会偏小，不准。这里只做**单段合理性**兜底。
    """
    out: list[str] = []
    for i, node in enumerate(blueprint.nodes):
        if node.duration_min < MIN_NODE_DURATION_MIN:
            out.append(
                f"节点「{node.kind}」（target={node.target_id}）停留时长 "
                f"{node.duration_min} 分钟过短（< {MIN_NODE_DURATION_MIN}min 下限）"
                f"——LLM 可能把通勤时间误填给了 node"
            )
        elif node.duration_min > MAX_NODE_DURATION_MIN:
            out.append(
                f"节点「{node.kind}」（target={node.target_id}）停留时长 "
                f"{node.duration_min} 分钟过长（> {MAX_NODE_DURATION_MIN}min 上限）"
                f"——单段 5h 以上请考虑拆成多个节点"
            )
        # 兼容防御：duration_min == 0（NonNegativeInt 允许，但 mid node 不应为 0）
        if node.duration_min == 0:
            out.append(
                f"节点[{i}]「{node.kind}」duration_min=0；"
                f"中间节点不应为零停留（home 节点才是 0，由 assemble 自动加）"
            )
    return out


# ============================================================
# 营业时间解析（与旧版语义一致，迁移过来）
# ============================================================


_BUSINESS_HOURS_RE = re.compile(
    r"^([01]\d|2[0-3]):([0-5]\d)\s*[-–]\s*([01]\d|2[0-3]):([0-5]\d)$"
)


def _is_in_business_hours(
    start_min: int, end_min: int, opening_hours: str
) -> bool:
    """判断 [start_min, end_min]（分钟）是否完全落在 opening_hours 内。

    支持 "10:30-21:30" / "00:00-23:59" / "08:00 - 22:00" 等格式。
    跨日营业（如 "22:00-04:00"）暂按全天通过——hackathon 范围简化处理。
    """
    if not opening_hours:
        return True  # 无营业时间约束默认通过
    m = _BUSINESS_HOURS_RE.match(opening_hours.strip())
    if not m:
        return True  # 不识别格式时不报错（让其它 critic 兜）
    open_h, open_m, close_h, close_m = map(int, m.groups())
    open_t = open_h * 60 + open_m
    close_t = close_h * 60 + close_m
    if close_t <= open_t:
        return True  # 跨日营业，简化通过
    return open_t <= start_min and end_min <= close_t


def _opening_hours_critic(blueprint: PlanBlueprint) -> list[str]:
    """C3：每个 node 的目标 POI/餐厅在该 node 的推算开始时刻仍在营业。

    由于 LLM 不输出 start_time，本 critic 用 preferred_start_time + 累加 duration
    粗略推算每个 node 的开始时刻，再去查营业时间。

    注意：本估算**不包含 hop 通勤时间**，所以推算出的开始时刻会比实际偏早。
    这只是 blueprint 级粗筛——精确营业时间检查在 assemble 完成后由
    critics_v2 完成（届时已知 hop 真实分钟数）。
    """
    out: list[str] = []

    pois_by_id = {p.id: p for p in load_pois()}
    rests_by_id = {r.id: r for r in load_restaurants()}

    windows = _derive_node_windows(blueprint)

    for node, (start_min, end_min) in zip(blueprint.nodes, windows):
        if node.target_kind == BlueprintTargetKind.POI:
            target = pois_by_id.get(node.target_id)
            entity_label = "POI"
        else:  # RESTAURANT
            target = rests_by_id.get(node.target_id)
            entity_label = "餐厅"

        if target is None:
            out.append(
                f"未找到 {entity_label} id={node.target_id}（节点「{node.kind}」）"
            )
            continue

        if not _is_in_business_hours(start_min, end_min, target.opening_hours):
            out.append(
                f"{entity_label}「{target.name}」营业时间 {target.opening_hours}，"
                f"不覆盖节点「{node.kind}」推算时段 "
                f"{_minutes_to_time(start_min)}-{_minutes_to_time(end_min)}"
            )

    return out


# ============================================================
# spec planning-quality-deep-review R4：年龄感知单段时长 critic
# ============================================================


# Companion age tier 时长 cap（minute）。业界基线（Smithsonian SEEC 等）：
# - 婴幼儿（≤3）：45（注意力 ≤ 30，余量给过渡）
# - 学龄前（4-6）：75（与 prompt 学龄前 cap 一致）
# - 学童（7-12）：120
# - 老人（≥75）：60（含台阶 / 长走再砍，但 critic 不感知场地坡度，给统一 cap）
# 多代际取最严（含 ≤6 → 75；含 ≥75 → 60；同时含 → 60）。
_AGE_CAP_TODDLER = 45  # ≤ 3 岁
_AGE_CAP_PRESCHOOL = 75  # 4-6 岁
_AGE_CAP_SCHOOL_AGE = 120  # 7-12 岁
_AGE_CAP_ELDER_60_74 = 90  # 60-74 岁（保留作为参考，当前未触发）
_AGE_CAP_SENIOR = 60  # ≥ 75 岁


def _resolve_age_caps(intent: IntentExtraction) -> tuple[int, list[str]]:
    """从 intent.companions 推单段最严 cap + 触发原因列表。

    Returns:
        (cap_min, reasons)
        - cap_min: 单段时长上限（分钟）。无 age 信息时返回 9999（实质不约束）。
        - reasons: 触发的人话原因列表（如 ["含 5 岁孩（学龄前 ≤75min）"]），
                   留给 _age_aware_duration_critic 拼 message 用。

    取最严策略：从 companions 各自 age 推一个 cap，再取 min。
    """
    if intent is None or not getattr(intent, "companions", None):
        return 9999, []

    cap_candidates: list[tuple[int, str]] = []  # (cap_min, reason)

    for c in intent.companions:
        age = getattr(c, "age", None)
        role = getattr(c, "role", "同行")
        if not isinstance(age, int) or age < 0:
            continue
        if age <= 3:
            cap_candidates.append(
                (_AGE_CAP_TODDLER, f"含 {age} 岁{role}（婴幼儿 ≤{_AGE_CAP_TODDLER}min）")
            )
        elif age <= 6:
            cap_candidates.append(
                (_AGE_CAP_PRESCHOOL, f"含 {age} 岁{role}（学龄前 ≤{_AGE_CAP_PRESCHOOL}min）")
            )
        elif age <= 12:
            cap_candidates.append(
                (_AGE_CAP_SCHOOL_AGE, f"含 {age} 岁{role}（学童 ≤{_AGE_CAP_SCHOOL_AGE}min）")
            )
        elif age >= 75:
            cap_candidates.append(
                (_AGE_CAP_SENIOR, f"含 {age} 岁{role}（高龄 ≤{_AGE_CAP_SENIOR}min）")
            )

    if not cap_candidates:
        return 9999, []

    # 取最严
    min_cap = min(c[0] for c in cap_candidates)
    reasons = [c[1] for c in cap_candidates if c[0] == min_cap]
    return min_cap, reasons


def _age_aware_duration_critic(
    blueprint: PlanBlueprint,
    intent: IntentExtraction | None,
) -> list[BlueprintViolation]:
    """C4 [spec R4]：按 companion age 校验每个 POI 节点的单段时长。

    仅对 target_kind=POI 节点验（餐厅按 typical_dining_min 是其他规则，不在此 critic）。

    违规消息含 expected_range，由 critics_v2 / format_violations_for_llm
    拼成"建议范围 X-Y min"自然语言喂回 LLM（不暴露字段名）。
    """
    if intent is None:
        return []

    cap, reasons = _resolve_age_caps(intent)
    if cap >= 9999:
        return []

    # 期望区间：[max(45, cap-15), cap]——给 LLM 收敛空间
    lo = max(45, cap - 15)
    hi = cap
    expected = (lo, hi)

    out: list[BlueprintViolation] = []
    reason_text = "；".join(reasons) if reasons else f"同行约束 ≤{cap}min"

    for node in blueprint.nodes:
        if node.target_kind != BlueprintTargetKind.POI:
            continue
        if node.duration_min > cap:
            msg = (
                f"节点「{node.kind}」（target={node.target_id}）"
                f"停留 {node.duration_min} 分钟超出年龄约束（{reason_text}）"
            )
            out.append(
                BlueprintViolation(
                    critic="blueprint_age_aware_duration",
                    severity="hard",
                    message=msg,
                    expected_range=expected,
                )
            )

    return out


# ============================================================
# 兼容封装：旧 run_blueprint_critics / BlueprintReport / BlueprintViolation
# ============================================================
#
# planner_llm_first.py（冻结路径）仍调用 run_blueprint_critics → BlueprintReport，
# 这里保留入口但内部委托给上面 critic，把消息包装成 BlueprintViolation。


@dataclass
class BlueprintViolation:
    """蓝图违规条目（兼容封装）。

    edge_v1 只有"hard"严重度——blueprint critic 一旦命中即触发 LLM 重生成；
    soft 违规的概念已下沉到 Itinerary 级 critic。

    spec planning-quality-deep-review R4 引入 `expected_range`：
    当 critic 能给出明确收敛区间时（如 _age_aware_duration_critic 的
    "5 岁娃应 45-75min"），expected_range 携带 (lo, hi) tuple，
    `format_violations_for_llm` 时拼成"建议范围 X-Y min"自然语言喂回 LLM
    （**不暴露字段名 expected_range / nodes[i] / dot-path**，遵守
    design.md "不暴露内部 schema 给 LLM" 原则）。
    """

    critic: str  # blueprint_temporal / blueprint_duration / blueprint_opening_hours / blueprint_age_aware_duration
    severity: str  # 当前一律 "hard"
    message: str
    field_hint: str = ""
    expected_range: Optional[tuple[int, int]] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "critic": self.critic,
            "severity": self.severity,
            "message": self.message,
            "field_hint": self.field_hint,
        }
        if self.expected_range is not None:
            out["expected_range"] = list(self.expected_range)
        return out


@dataclass
class BlueprintReport:
    """蓝图 Critic 全跑完的聚合（兼容封装）。"""

    passed: bool
    violations: list[BlueprintViolation] = field(default_factory=list)
    soft_score: float = 1.0

    def hard_violations(self) -> list[BlueprintViolation]:
        return [v for v in self.violations if v.severity == "hard"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "soft_score": round(self.soft_score, 3),
            "violations": [v.to_dict() for v in self.violations],
        }


def run_blueprint_critics(
    blueprint: PlanBlueprint,
    intent: IntentExtraction | None = None,
) -> BlueprintReport:
    """跑全部蓝图 Critic 返聚合 BlueprintReport。

    Args:
        blueprint: LLM 输出的蓝图。
        intent: IntentExtraction，spec planning-quality-deep-review R4 引入消费——
                `_age_aware_duration_critic` 用 intent.companions[].age 推单段 cap。
                None 时降级（不跑年龄 critic），保持向后兼容。

    Returns:
        BlueprintReport：硬违规 → passed=False（让上层 backprompt LLM 重生成）。
    """
    all_violations: list[BlueprintViolation] = []

    for msg in _temporal_critic(blueprint):
        all_violations.append(
            BlueprintViolation(
                critic="blueprint_temporal", severity="hard", message=msg
            )
        )
    for msg in _duration_critic(blueprint):
        all_violations.append(
            BlueprintViolation(
                critic="blueprint_duration", severity="hard", message=msg
            )
        )
    for msg in _opening_hours_critic(blueprint):
        all_violations.append(
            BlueprintViolation(
                critic="blueprint_opening_hours", severity="hard", message=msg
            )
        )

    # spec planning-quality-deep-review R4：年龄感知单段时长 critic
    # 仅在 intent 非空时跑（无 age 信号时降级为 no-op）
    if intent is not None:
        all_violations.extend(_age_aware_duration_critic(blueprint, intent))

    hard = [v for v in all_violations if v.severity == "hard"]
    return BlueprintReport(
        passed=not hard,
        violations=all_violations,
        soft_score=1.0,  # edge_v1 取消 soft 概念
    )
