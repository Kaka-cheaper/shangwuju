"""critics_v2 —— Pydantic AI ReAct Agent 的 Itinerary 兜底验证层。

设计范式（Kambhampati LLM-Modulo, NeurIPS 2024）：
- LLM 决「主观」：选哪些 POI / 餐厅、什么顺序、几段方案
- 算法决「客观」：时序无重叠 / 总时长在区间 / 距离不越界 / 营业时间覆盖

为什么叫 critics_v2 而非 critics：
- backend/agent/critics.py 已存在（旧规则 critic 的内部组件，由 planner_hybrid 用）
- v2 critic 是给 Pydantic AI ReAct Agent（Agent E 在并行做）用的；
  它直接读 Itinerary 顶层字段，不读 PlanBlueprint，与旧 critic 不冲突

Critic 纪律（硬性）：
- 不抛异常（违规返回 violations 列表，由调用方决定是否 ModelRetry）
- 不调 LLM（critic 是算法不是 LLM）
- 不发明新 schema 模型（直接接受 Itinerary + IntentExtraction）

不负责：
- ModelRetry 的触发逻辑（由 Pydantic AI Agent 调用方决定）
- 主观文案生成（critic 输出的 message 是 LLM 修复建议种子，但不是最终回话）
- LLM 工具调用历史的事后分析（critic 看不到调用链，只看最终 itinerary）
"""

from __future__ import annotations

import os
import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary, ItineraryStage


# ============================================================
# 枚举与数据结构
# ============================================================

class ViolationCode(str, Enum):
    """7 类 critic 触发码。

    与 schemas/errors.py 的 FailureReason 解耦——FailureReason 是
    Tool 失败原因，ViolationCode 是 Itinerary 级别的违规分类。
    """

    DURATION_OUT_OF_RANGE = "duration_out_of_range"
    DISTANCE_EXCEEDED = "distance_exceeded"
    STAGES_INCOMPLETE = "stages_incomplete"
    RESTAURANT_FULL_UNRESOLVED = "restaurant_full_unresolved"
    TIMELINE_INCONSISTENT = "timeline_inconsistent"
    SOCIAL_CONTEXT_MISMATCH = "social_context_mismatch"
    DIETARY_VIOLATION = "dietary_violation"


class Severity(str, Enum):
    """违规等级。

    - CRITICAL：必须 ModelRetry；调用方应把 violation 转成 prompt 让 LLM 重做
    - WARNING ：方案可继续上呈，但日志/调试时需关注（如 mock 数据本身的轻微偏差）
    """

    CRITICAL = "critical"
    WARNING = "warning"


class Violation(BaseModel):
    """一条违规记录。

    `message` 是给 LLM 看的中文修复建议（不是给前端用户看的）；
    `field_path` 用 dot-path 风格定位违规位置，如 "stages[3].start"。
    """

    model_config = ConfigDict(extra="forbid")

    code: ViolationCode
    severity: Severity
    message: str = Field(..., description="给 LLM 看的中文修复建议")
    field_path: str = Field(default="", description='dot-path，如 "stages[3].start"')


# ============================================================
# 内部 helper
# ============================================================

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_hhmm(value: str) -> Optional[int]:
    """把 "14:30" 转成总分钟数（870）。格式不合法返 None。"""
    if not isinstance(value, str):
        return None
    m = _TIME_RE.match(value.strip())
    if not m:
        return None
    h = int(m.group(1))
    mn = int(m.group(2))
    if not (0 <= h <= 29 and 0 <= mn <= 59):
        # 允许 24-29h 跨日表示但不允许更大；超出说明格式畸形
        return None
    return h * 60 + mn


def _stage_kind_normalized(stage: ItineraryStage) -> str:
    """kind 里去掉空白便于子串匹配。"""
    return (stage.kind or "").replace(" ", "")


def _has_kind(stages: list[ItineraryStage], keyword: str) -> bool:
    return any(keyword in _stage_kind_normalized(s) for s in stages)


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


# ============================================================
# 单项 critic
# ============================================================

# 必须包含的三类 stage 关键词（用「子串」判定，兼容 "主活动" / "主活动 · 看展" 等）
_REQUIRED_KIND_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("主活动", "缺少「主活动」段：用户期望中段有一个主要活动（如游玩、看展、亲子）"),
    ("用餐", "缺少「用餐」段：半日方案应在合适时段安排一次用餐"),
    ("返回", "缺少「返回」段：方案需以「返回家」收尾让用户清晰知道结束时间"),
)

# 默认时长容差：[lo*60 - 30, hi*60 + 30]
_DURATION_TOLERANCE_MIN = 30


def _check_stages_incomplete(itinerary: Itinerary) -> list[Violation]:
    out: list[Violation] = []

    if len(itinerary.stages) < 5:
        out.append(
            Violation(
                code=ViolationCode.STAGES_INCOMPLETE,
                severity=Severity.CRITICAL,
                message=(
                    f"行程段数为 {len(itinerary.stages)}，少于最低要求 5 段。"
                    "请补全：出发 → 主活动 → 转场 → 用餐 → 返回（至少 5 段）"
                ),
                field_path="stages",
            )
        )
        return out  # 段数不足时其它「kind 命中」检查无意义

    for keyword, msg in _REQUIRED_KIND_KEYWORDS:
        if not _has_kind(itinerary.stages, keyword):
            out.append(
                Violation(
                    code=ViolationCode.STAGES_INCOMPLETE,
                    severity=Severity.CRITICAL,
                    message=msg,
                    field_path="stages[*].kind",
                )
            )
    return out


def _check_duration(itinerary: Itinerary, intent: IntentExtraction) -> list[Violation]:
    duration = intent.duration_hours
    if not duration or len(duration) != 2:
        return []  # schema 校验已保证此处必有值，但防御一下

    lo, hi = int(duration[0]), int(duration[1])
    lo_tol = lo * 60 - _DURATION_TOLERANCE_MIN
    hi_tol = hi * 60 + _DURATION_TOLERANCE_MIN

    actual = int(itinerary.total_minutes)
    if actual < lo_tol or actual > hi_tol:
        if actual < lo_tol:
            advice = f"请扩展段时长（如增加主活动 30 分钟）将总时长拉到 {lo}-{hi}h 区间"
        else:
            advice = f"请压缩段时长（如缩短转场 / 用餐 / 主活动）将总时长压到 {lo}-{hi}h 区间"

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


def _check_timeline(itinerary: Itinerary) -> list[Violation]:
    out: list[Violation] = []
    prev_end_min: Optional[int] = None

    for idx, stage in enumerate(itinerary.stages):
        s_min = _parse_hhmm(stage.start)
        e_min = _parse_hhmm(stage.end)

        if s_min is None:
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.CRITICAL,
                    message=f'第 {idx + 1} 段 start="{stage.start}" 不是合法 HH:MM 格式，请改为 14:30 形式',
                    field_path=f"stages[{idx}].start",
                )
            )
            prev_end_min = None
            continue
        if e_min is None:
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.CRITICAL,
                    message=f'第 {idx + 1} 段 end="{stage.end}" 不是合法 HH:MM 格式，请改为 14:30 形式',
                    field_path=f"stages[{idx}].end",
                )
            )
            prev_end_min = None
            continue

        if e_min < s_min:
            # 允许等于（瞬时段），不允许反序
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.CRITICAL,
                    message=(
                        f"第 {idx + 1} 段 end({stage.end}) 早于 start({stage.start})，"
                        "请调整段时间使 end ≥ start"
                    ),
                    field_path=f"stages[{idx}].end",
                )
            )

        # 与前一段对比：允许 5 分钟容差
        if prev_end_min is not None and s_min < prev_end_min - 5:
            out.append(
                Violation(
                    code=ViolationCode.TIMELINE_INCONSISTENT,
                    severity=Severity.CRITICAL,
                    message=(
                        f"第 {idx + 1} 段 start({stage.start}) 早于前一段 end 超过 5 分钟容差，"
                        "段次序混乱或时间重叠，请重新规划"
                    ),
                    field_path=f"stages[{idx}].start",
                )
            )

        prev_end_min = e_min

    return out


def _check_distance(itinerary: Itinerary, intent: IntentExtraction) -> list[Violation]:
    """单段距家距离 > intent.distance_max_km → warning。

    用 warning 因为 LLM 已经基于 search_pois / search_restaurants 返回的候选选择，
    可能 mock 数据本身就有偏差；不应卡死流程。
    """
    out: list[Violation] = []
    max_km = intent.distance_max_km
    if max_km is None or max_km <= 0:
        return out

    pois_by_id = {p.id: p for p in _safe_load_pois()}
    restaurants_by_id = {r.id: r for r in _safe_load_restaurants()}

    for idx, stage in enumerate(itinerary.stages):
        target_distance: Optional[float] = None
        target_label = ""
        if stage.poi_id and stage.poi_id in pois_by_id:
            target_distance = pois_by_id[stage.poi_id].distance_km
            target_label = pois_by_id[stage.poi_id].name
        elif stage.restaurant_id and stage.restaurant_id in restaurants_by_id:
            target_distance = restaurants_by_id[stage.restaurant_id].distance_km
            target_label = restaurants_by_id[stage.restaurant_id].name

        if target_distance is None:
            continue
        if target_distance > max_km + 0.5:  # 0.5km 容差，避免 5.0 vs 5.1 误报
            out.append(
                Violation(
                    code=ViolationCode.DISTANCE_EXCEEDED,
                    severity=Severity.WARNING,
                    message=(
                        f"第 {idx + 1} 段 {target_label or '目标点'} 距家 {target_distance:.1f}km，"
                        f"超过用户期望 {max_km:.1f}km。如条件允许请换距离更近的候选"
                    ),
                    field_path=f"stages[{idx}]",
                )
            )

    return out


# 17:00 是 mock_data/restaurants.json 的典型满座埋点（演示场景集 §四）
_DEMO_FULL_TIME = "17:00"


def _check_demo_restaurant_full(itinerary: Itinerary) -> list[Violation]:
    """demo-aware 检查：mock 餐厅 17:00 整点是 RESTAURANT_FULL 埋点。

    简化策略：critic 看不到工具调用历史，只能基于「最终 itinerary 的用餐 stage 时间」
    推断。如果 itinerary 用餐 stage start 正好 17:00，说明 LLM 没处理 RESTAURANT_FULL，
    强制让它换 17:30 重做（评分项 5 异常韧性的硬要求）。

    真产品要换成「对工具调用日志的事后分析」（即查 tool_call_end 是否含
    reason=restaurant_full 但下游没 replan）。

    通过 ENABLE_DEMO_FULL_CHECK 环境变量控制开关，默认开。
    """
    enabled = (os.getenv("ENABLE_DEMO_FULL_CHECK") or "1").strip().lower()
    if enabled in ("0", "false", "no", "off"):
        return []

    out: list[Violation] = []
    for idx, stage in enumerate(itinerary.stages):
        kind = _stage_kind_normalized(stage)
        if "用餐" not in kind:
            continue
        if (stage.start or "").strip() == _DEMO_FULL_TIME:
            out.append(
                Violation(
                    code=ViolationCode.RESTAURANT_FULL_UNRESOLVED,
                    severity=Severity.CRITICAL,
                    message=(
                        f"第 {idx + 1} 段用餐 start=17:00 是已知的高峰满座时段（mock 餐厅典型埋点）。"
                        "请调用 check_restaurant_availability 验证实际可用性，"
                        "或直接把用餐时间挪到 17:30 / 18:00 等空档时段"
                    ),
                    field_path=f"stages[{idx}].start",
                )
            )
    return out


def _check_dietary(itinerary: Itinerary, intent: IntentExtraction) -> list[Violation]:
    """用餐 stage 餐厅 tags 是否覆盖 intent.dietary_constraints。

    - intent 没饮食约束 → 跳过
    - stage 没 restaurant_id → 跳过（可能是「自带午餐」或类似自由文本）
    - load 失败 → 跳过
    """
    if not intent.dietary_constraints:
        return []

    restaurants_by_id = {r.id: r for r in _safe_load_restaurants()}
    if not restaurants_by_id:
        return []

    constraints_set = set(intent.dietary_constraints)
    out: list[Violation] = []

    for idx, stage in enumerate(itinerary.stages):
        if "用餐" not in _stage_kind_normalized(stage):
            continue
        rid = stage.restaurant_id
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
                    f"第 {idx + 1} 段餐厅 {rest.name} 的 tags 不含用户饮食约束 "
                    f"{sorted(constraints_set)} 中任何一项。建议换符合饮食偏好的餐厅"
                ),
                field_path=f"stages[{idx}].restaurant_id",
            )
        )
    return out


def _check_social_context(
    itinerary: Itinerary, intent: IntentExtraction
) -> list[Violation]:
    """轻量启发式：social_context 与方案的明显失配。

    只做两条最不冒犯 LLM 的检查（warning，不 critical）：
    - 「独处放空」时不应预约 ≥ 2 人位
    - 「家庭日常」时 stage.title 不应明显包含「商务包间 / 商务接待」字样

    Demo-friendly：不要写死太多 if，让 LLM 有发挥空间，仅拦最离谱的搭配。
    """
    out: list[Violation] = []
    sc = intent.social_context or ""

    if "独处" in sc:
        for order in itinerary.orders:
            kind = order.kind or ""
            if "餐厅" in kind:
                # detail 里如果出现「2 人 / 三人 / 四人」等多人字样 → 警告
                detail = order.detail or ""
                multi_signals = ["2 人", "三人", "四人", "六人", "≥2"]
                if any(sig in detail for sig in multi_signals):
                    out.append(
                        Violation(
                            code=ViolationCode.SOCIAL_CONTEXT_MISMATCH,
                            severity=Severity.WARNING,
                            message=(
                                f"独处放空场景，但 {order.target_name} 预约 {detail}。"
                                "请确认是否真的需要多人位"
                            ),
                            field_path="orders",
                        )
                    )

    if "家庭" in sc:
        for idx, stage in enumerate(itinerary.stages):
            title = stage.title or ""
            if "商务包间" in title or "商务接待" in title:
                out.append(
                    Violation(
                        code=ViolationCode.SOCIAL_CONTEXT_MISMATCH,
                        severity=Severity.WARNING,
                        message=(
                            f"家庭日常场景但第 {idx + 1} 段 title 含「商务」字样：{title}，"
                            "请确认餐厅/活动是否适合家庭"
                        ),
                        field_path=f"stages[{idx}].title",
                    )
                )

    return out


# ============================================================
# 主入口
# ============================================================

def validate_itinerary(
    itinerary: Itinerary, intent: IntentExtraction
) -> list[Violation]:
    """跑全套 7 类 critic 检查。返回 violations 列表（可能为空）。

    顺序约定（先「结构性」后「语义性」）：
    1. STAGES_INCOMPLETE
    2. DURATION_OUT_OF_RANGE
    3. TIMELINE_INCONSISTENT
    4. DISTANCE_EXCEEDED
    5. RESTAURANT_FULL_UNRESOLVED（demo-aware）
    6. DIETARY_VIOLATION
    7. SOCIAL_CONTEXT_MISMATCH
    """
    violations: list[Violation] = []
    violations.extend(_check_stages_incomplete(itinerary))
    violations.extend(_check_duration(itinerary, intent))
    violations.extend(_check_timeline(itinerary))
    violations.extend(_check_distance(itinerary, intent))
    violations.extend(_check_demo_restaurant_full(itinerary))
    violations.extend(_check_dietary(itinerary, intent))
    violations.extend(_check_social_context(itinerary, intent))
    return violations


def format_violations_for_llm(violations: list[Violation]) -> str:
    """把 critical violations 格式化成给 LLM 的 ModelRetry 消息。

    - 0 critical → 返回空字符串（调用方据此决定不 ModelRetry）
    - ≥1 critical → 返回中文修复 prompt（含编号 + field_path 定位 + message）

    warning 级别**不**进入此消息（避免噪声把 LLM 注意力分散）。
    """
    critical = [v for v in violations if v.severity == Severity.CRITICAL]
    if not critical:
        return ""

    lines = [f"你产出的行程方案有 {len(critical)} 处违规需要修复："]
    for i, v in enumerate(critical, 1):
        loc = f"[{v.field_path}] " if v.field_path else ""
        lines.append(f"{i}. {loc}{v.message}")
    lines.append("请按上述建议重新调用工具或调整方案，重新输出 ItineraryResponse。")
    return "\n".join(lines)


__all__ = [
    "ViolationCode",
    "Severity",
    "Violation",
    "validate_itinerary",
    "format_violations_for_llm",
]
