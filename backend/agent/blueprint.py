"""agent.blueprint —— LLM-First Planner 的"行程蓝图"数据结构 + 蓝图级 Critic。

【为什么需要蓝图】（参考 problem.md 问题 14 / pitfalls.md P1-2026-05-17）

历史包袱：rule planner 把"5 段写死 + 14:00 起 + POI→餐厅顺序"当默认，导致：
- 24 小时营业餐厅、夜宵、早茶等场景被强行套到下午局模板
- 用户说"只想吃饭不去玩"被强加主活动
- 每次出新反例都要在 plan_itinerary 里加 if，违反 LLM-Modulo
  (Kambhampati NeurIPS 2024) "LLM 决主观、算法决客观" 的原则

修复方向：把"段集合 / 段顺序 / 每段时长 / 目标 id"全交给 LLM 决策，算法只做：
1. 蓝图级 Critic 验证（时序、营业时间、时长边界）
2. 把 Blueprint 拼成 Itinerary 时间轴

蓝图字段是**意图最小可执行表达**：
- kind: 自由中文文本（早茶 / 晨练 / 夜宵 / 单独购物 / city walk 任意）
- target_kind: poi/restaurant/none（"出发"和"返回"通常 none）
- target_id: 关联到 mock_data 的具体 id；none 段为空
- start_time + duration_min: HH:MM + 分钟数
- note: 给前端的提示文案

不负责：
- LLM 调用与 prompt（在 agent/planner_llm_first.py）
- 蓝图→Itinerary 拼装（在 planner.assemble_from_blueprint）
- Tool 实现 / Mock 数据加载
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from data.loader import load_pois, load_restaurants
from schemas.intent import IntentExtraction


# ============================================================
# 字段类型
# ============================================================

class BlueprintTargetKind(str, Enum):
    """段的目标实体类型。"""

    POI = "poi"
    RESTAURANT = "restaurant"
    NONE = "none"  # "出发" / "返回" / 自由活动等


_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _parse_time_to_minutes(t: str) -> int:
    if not _TIME_RE.match(t):
        raise ValueError(f"start_time 必须是 HH:MM 格式，实际 {t!r}")
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _minutes_to_time(total: int) -> str:
    """允许跨日（24+）但截到合理范围。"""
    total = max(0, min(total, 24 * 60 - 1))
    return f"{total // 60:02d}:{total % 60:02d}"


# ============================================================
# BlueprintStage
# ============================================================

@dataclass
class BlueprintStage:
    """蓝图中的一段。

    LLM 直接产出此结构；下游 assemble_from_blueprint 按 stages 顺序拼时间轴。

    Args:
        kind: 段类型（自由中文）；"出发"/"主活动"/"用餐"/"转场"/"返回" 是惯用值，
              但允许任意如"夜宵"/"晨练"/"早茶"/"逛街"。
        start_time: HH:MM 格式
        duration_min: 段时长（分钟），≥ 0
        target_kind: poi / restaurant / none
        target_id: 关联实体 id（none 时应为 None）
        note: 给前端的提示文案
    """

    kind: str
    start_time: str
    duration_min: int
    target_kind: BlueprintTargetKind = BlueprintTargetKind.NONE
    target_id: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.kind or not self.kind.strip():
            raise ValueError("kind 不能为空")
        # 校验 start_time 格式
        _parse_time_to_minutes(self.start_time)
        if self.duration_min < 0:
            raise ValueError(f"duration_min 必须 ≥ 0，实际 {self.duration_min}")
        if self.target_kind != BlueprintTargetKind.NONE and not self.target_id:
            raise ValueError(
                f"target_kind={self.target_kind.value} 时 target_id 不能为空"
            )
        if self.target_kind == BlueprintTargetKind.NONE and self.target_id:
            # 容忍：自动清掉
            object.__setattr__(self, "target_id", None)

    def end_time(self) -> str:
        """段结束时间（HH:MM）。"""
        return _minutes_to_time(
            _parse_time_to_minutes(self.start_time) + self.duration_min
        )

    def start_minutes(self) -> int:
        return _parse_time_to_minutes(self.start_time)

    def end_minutes(self) -> int:
        return self.start_minutes() + self.duration_min

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "start_time": self.start_time,
            "end_time": self.end_time(),
            "duration_min": self.duration_min,
            "target_kind": self.target_kind.value,
            "target_id": self.target_id,
            "note": self.note,
        }


# ============================================================
# PlanBlueprint
# ============================================================

@dataclass
class PlanBlueprint:
    """LLM 的完整行程蓝图。"""

    stages: list[BlueprintStage]
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("PlanBlueprint.stages 不能为空")

    def total_minutes(self) -> int:
        if not self.stages:
            return 0
        first = self.stages[0].start_minutes()
        last_end = max(s.end_minutes() for s in self.stages)
        return last_end - first

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [s.to_dict() for s in self.stages],
            "rationale": self.rationale,
            "total_minutes": self.total_minutes(),
        }


# ============================================================
# Critic
# ============================================================

@dataclass
class BlueprintViolation:
    """蓝图违规条目。"""

    critic: str           # blueprint_temporal / blueprint_duration / blueprint_opening_hours
    severity: str         # hard / soft
    message: str
    field_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "critic": self.critic,
            "severity": self.severity,
            "message": self.message,
            "field_hint": self.field_hint,
        }


@dataclass
class BlueprintReport:
    """蓝图 Critic 全跑完的聚合。"""

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


# ============================================================
# Critic 实现
# ============================================================

def _temporal_critic(blueprint: PlanBlueprint) -> list[BlueprintViolation]:
    """C1：段无重叠 + 时间单调递增。"""
    out: list[BlueprintViolation] = []
    if len(blueprint.stages) < 2:
        return out
    prev_end = blueprint.stages[0].end_minutes()
    prev_kind = blueprint.stages[0].kind
    for s in blueprint.stages[1:]:
        cur_start = s.start_minutes()
        if cur_start < prev_end:
            out.append(
                BlueprintViolation(
                    critic="blueprint_temporal",
                    severity="hard",
                    message=(
                        f"段「{prev_kind}」与「{s.kind}」时序重叠："
                        f"前者 {prev_kind} 结束于 {_minutes_to_time(prev_end)}，"
                        f"后者 {s.kind} 开始于 {s.start_time}"
                    ),
                    field_hint=f"stages.{s.kind}.start_time",
                )
            )
        prev_end = s.end_minutes()
        prev_kind = s.kind
    return out


def _duration_critic(
    blueprint: PlanBlueprint, intent: IntentExtraction
) -> list[BlueprintViolation]:
    """C2：蓝图总时长不超过 intent.duration_hours[1] + 15min 容忍。

    总时长 = 末段 end - 首段 start。
    """
    out: list[BlueprintViolation] = []
    total = blueprint.total_minutes()
    max_min = max(intent.duration_hours) * 60 + 15
    min_min = min(intent.duration_hours) * 60 - 15
    if total > max_min:
        out.append(
            BlueprintViolation(
                critic="blueprint_duration",
                severity="hard",
                message=(
                    f"蓝图总时长 {total} 分钟，超过用户上限 "
                    f"{max(intent.duration_hours) * 60}+15 分钟容忍"
                ),
                field_hint="stages",
            )
        )
    elif total < max(60, min_min):
        # 软违规：太短
        out.append(
            BlueprintViolation(
                critic="blueprint_duration",
                severity="soft",
                message=(
                    f"蓝图总时长 {total} 分钟，低于用户期望下限 "
                    f"{min(intent.duration_hours) * 60} 分钟"
                ),
                field_hint="stages",
            )
        )
    return out


def _opening_hours_critic(
    blueprint: PlanBlueprint,
) -> list[BlueprintViolation]:
    """C3：每段 target 在营业时间内（poi/restaurant 都查 mock_data.opening_hours）。"""
    out: list[BlueprintViolation] = []

    pois_by_id = {p.id: p for p in load_pois()}
    rests_by_id = {r.id: r for r in load_restaurants()}

    for s in blueprint.stages:
        if s.target_kind == BlueprintTargetKind.NONE or not s.target_id:
            continue

        if s.target_kind == BlueprintTargetKind.POI:
            target = pois_by_id.get(s.target_id)
            entity_label = "POI"
        else:
            target = rests_by_id.get(s.target_id)
            entity_label = "餐厅"

        if target is None:
            out.append(
                BlueprintViolation(
                    critic="blueprint_opening_hours",
                    severity="hard",
                    message=f"未找到 {entity_label} id={s.target_id}",
                    field_hint=f"stages.{s.kind}.target_id={s.target_id}",
                )
            )
            continue

        if not _is_in_business_hours(
            s.start_time, s.end_time(), target.opening_hours
        ):
            out.append(
                BlueprintViolation(
                    critic="blueprint_opening_hours",
                    severity="hard",
                    message=(
                        f"{entity_label}「{target.name}」营业时间 "
                        f"{target.opening_hours}，不覆盖蓝图段 "
                        f"{s.start_time}-{s.end_time()}"
                    ),
                    field_hint=f"stages.{s.kind}.target_id={target.id}",
                )
            )

    return out


_BUSINESS_HOURS_RE = re.compile(
    r"^([01]\d|2[0-3]):([0-5]\d)\s*[-–]\s*([01]\d|2[0-3]):([0-5]\d)$"
)


def _is_in_business_hours(
    start: str, end: str, opening_hours: str
) -> bool:
    """判断 [start, end] 是否完全落在 opening_hours 内。

    支持 "10:30-21:30" / "00:00-23:59" / "08:00 - 22:00" 格式。
    跨日营业（如 "22:00-04:00"）暂按全天通过——hackathon 范围不做精确处理。
    """
    if not opening_hours:
        return True  # 无营业时间约束默认通过
    m = _BUSINESS_HOURS_RE.match(opening_hours.strip())
    if not m:
        return True  # 不识别格式时不报错（让其它 critic 兜）
    open_h, open_m, close_h, close_m = map(int, m.groups())
    open_min = open_h * 60 + open_m
    close_min = close_h * 60 + close_m
    if close_min <= open_min:
        return True  # 跨日营业，简化通过
    s_min = _parse_time_to_minutes(start)
    e_min = _parse_time_to_minutes(end)
    return open_min <= s_min and e_min <= close_min


# ============================================================
# 主入口
# ============================================================

def run_blueprint_critics(
    blueprint: PlanBlueprint, intent: IntentExtraction
) -> BlueprintReport:
    """跑全部蓝图 Critic 返聚合 BlueprintReport。

    硬违规 → passed=False（让上层 backprompt LLM 重生成）。
    软违规 → 仅扣 soft_score。
    """
    all_violations: list[BlueprintViolation] = []
    all_violations.extend(_temporal_critic(blueprint))
    all_violations.extend(_duration_critic(blueprint, intent))
    all_violations.extend(_opening_hours_critic(blueprint))

    hard = [v for v in all_violations if v.severity == "hard"]
    soft = [v for v in all_violations if v.severity == "soft"]
    soft_score = max(0.0, 1.0 - 0.15 * len(soft))

    return BlueprintReport(
        passed=not hard, violations=all_violations, soft_score=soft_score
    )
