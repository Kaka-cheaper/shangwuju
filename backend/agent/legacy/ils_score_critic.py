# FROZEN: 详见 AGENTS.md §3.3.1，仅 fallback / safety-net，不改业务
"""agent.critics —— LLM-Modulo 风格的 Critic 验证层（A+C 混合方案的 C 段，edge_v1）。

学术依据：[Kambhampati et al. 2024 LLMs Can't Plan, But Can Help Planning in
LLM-Modulo Frameworks (NeurIPS 2024)] + [Kim et al. Robust Planning with
LLM-Modulo Framework arXiv:2405.20625]——LLM 直接生成的方案常违背硬约束，
解法是用一组规则化、便宜、可证伪的 Critic 验证后再决定是否反馈给 LLM 重写。

【edge_v1 字段路径迁移（Wave 5 Task 9）】

旧 hybrid critic 用 `plan.stages` 遍历，按 `stage.kind`（"主活动" / "用餐"）找目标段。
edge_v1 起 `Itinerary.stages` 已被 `Itinerary.nodes` 替换：

```
旧：next(s for s in plan.stages if s.kind == "用餐").restaurant_id
新：next(n for n in plan.nodes if n.target_kind == "restaurant").target_id
```

ActivityNode 的字段映射：
- 旧 stage.kind="主活动"  → 新 node.target_kind="poi"
- 旧 stage.kind="用餐"    → 新 node.target_kind="restaurant"
- 旧 stage.poi_id         → 新 node.target_id（target_kind="poi"）
- 旧 stage.restaurant_id  → 新 node.target_id（target_kind="restaurant"）
- 旧 stage.start          → 新 node.start_time
- 旧 stage.kind 中文标签   → 新 node.kind 中文标签（"主活动"/"用餐" 仍保留）

「段缺失」语义现在 == 「中间节点 kind 缺失」：把 decide_nodes(intent) 的输出与
itinerary 中 mid nodes 的 kind 集合对比。home 节点（target_kind="home"）不参与
段缺失判定。

本模块提供 4 个 Critic：
- HardConstraintCritic ：距离上限、总时长、节点 kind 完整度
- TimeWindowCritic     ：餐厅节点 start_time 真的可订（mock 数据 reservation_slots）
- BudgetCritic         ：人均预算是否超限（user.default_budget）
- StyleCritic          ：主活动 POI / 用餐餐厅 suitable_for 含 social_context

Critic 不抛异常；通过 CriticReport.passed + violations 表达结果。
违反时 violations 列出可读中文原因，给 ILS 重排或 LLM backprompt 用。

⚠️ 与 `agent/v2/critics_v2.py` 的关系：
v2/critics_v2.py 是 LangGraph 主路径用的 Itinerary 级 critic（含 hop 可达性 / 营业时间
精确校验等 8 项）。本文件是 A+C 混合范式（hybrid ILS）专用的轻量 critic，仅做 4 项
快速判定，且不验通勤可达性（hybrid 内部已通过 utility 函数粗筛距离）。

不负责：
- 候选生成、搜索（在 planner_hybrid.py）
- 权重决策（在 weights_llm.py）
- Tool 调用
- hop 可达性 / 时间轴精确校验（在 v2/critics_v2.py）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from data.loader import load_restaurants, load_user_profile
from schemas.intent import IntentExtraction
from schemas.itinerary import ActivityNode, Itinerary


# ============================================================
# 通用结果类型
# ============================================================

@dataclass
class CriticViolation:
    """单条 Critic 违规，前端友好。"""

    critic: str          # Critic 名字（如 hard_constraint）
    severity: str        # "hard" / "soft"
    message: str         # 中文原因
    field_hint: str = "" # 命中字段提示（前端高亮用）


@dataclass
class CriticReport:
    """全部 Critic 跑完的总报告。"""

    passed: bool
    violations: list[CriticViolation] = field(default_factory=list)
    soft_score: float = 1.0  # 软违规越多分越低（[0,1]）

    def hard_violations(self) -> list[CriticViolation]:
        return [v for v in self.violations if v.severity == "hard"]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "soft_score": round(self.soft_score, 3),
            "violations": [
                {
                    "critic": v.critic,
                    "severity": v.severity,
                    "message": v.message,
                    "field_hint": v.field_hint,
                }
                for v in self.violations
            ],
        }


# ============================================================
# 节点查找辅助（edge_v1：替代旧 plan.stages 遍历）
# ============================================================


def _find_main_node(plan: Itinerary) -> Optional[ActivityNode]:
    """找主活动节点：第一个 target_kind="poi" 的中间节点。

    `plan.nodes` 首尾固定为 home（不参与查找）；中间节点按时间序排列。
    取第一个 POI 节点作为「主活动」（与 rule planner 构造顺序对齐）。
    """
    for node in plan.nodes:
        if node.target_kind == "poi":
            return node
    return None


def _find_dining_node(plan: Itinerary) -> Optional[ActivityNode]:
    """找用餐节点：第一个 target_kind="restaurant" 的中间节点。"""
    for node in plan.nodes:
        if node.target_kind == "restaurant":
            return node
    return None


def _mid_node_kinds(plan: Itinerary) -> set[str]:
    """收集 itinerary 的中间节点 kind 集合（不含首尾 home）。

    edge_v1 中 ActivityNode.kind 是中文标签（"主活动" / "用餐" / ...）；
    与 decide_nodes(intent) 的返回元素类型对齐，可直接做集合差比对。
    """
    return {
        n.kind
        for n in plan.nodes
        if n.target_kind in ("poi", "restaurant")
    }


# ============================================================
# 4 个 Critic
# ============================================================

def _hard_constraint_critic(
    plan: Itinerary, intent: IntentExtraction
) -> list[CriticViolation]:
    """C1：距离 / 总时长 / 节点 kind 完整度。

    节点完整度判定（edge_v1）：按 decide_nodes(intent) 决定本场景应有的中间节点
    kind 列表，与 itinerary 中 mid nodes 的 kind 集合对比；不再硬要 5 段。
    """
    out: list[CriticViolation] = []

    # 总时长：超过 duration_hours 上限 30 分钟以内允许（软）；以上为硬
    total_min = plan.total_minutes
    max_min = max(intent.duration_hours) * 60
    min_min = min(intent.duration_hours) * 60
    if total_min > max_min + 30:
        out.append(
            CriticViolation(
                critic="hard_constraint",
                severity="hard",
                message=f"总耗时 {total_min} 分钟，超过用户上限 {max_min} 分钟",
                field_hint="total_minutes",
            )
        )
    elif total_min < max(60, min_min - 30):
        # 太短也不好（demo 评委会觉得行程单薄）
        out.append(
            CriticViolation(
                critic="hard_constraint",
                severity="soft",
                message=f"总耗时仅 {total_min} 分钟，低于用户期望 {min_min}-{max_min} 分钟",
                field_hint="total_minutes",
            )
        )

    # 节点 kind 完整度：按 intent 决定的 mid_nodes 判（edge_v1：不再硬要 5 段）
    from ..planning.blueprint.node_decider import decide_nodes
    required_kinds = set(decide_nodes(intent))
    have_kinds = _mid_node_kinds(plan)
    missing = required_kinds - have_kinds
    if missing:
        out.append(
            CriticViolation(
                critic="hard_constraint",
                severity="hard",
                message=(
                    f"行程中间节点缺失：{sorted(missing)}"
                    f"（按 intent 应有 {sorted(required_kinds)}，实际 {sorted(have_kinds)}）"
                ),
                field_hint="nodes",
            )
        )

    return out


def _time_window_critic(
    plan: Itinerary, intent: IntentExtraction  # noqa: ARG001
) -> list[CriticViolation]:
    """C2：用餐节点的餐厅时段真的可订（查 mock_data）。

    edge_v1：用餐节点 == ActivityNode(target_kind="restaurant")；
    时段 == node.start_time（HH:MM）。
    """
    out: list[CriticViolation] = []

    dining_node = _find_dining_node(plan)
    if dining_node is None or not dining_node.target_id:
        out.append(
            CriticViolation(
                critic="time_window",
                severity="hard",
                message="用餐节点未关联餐厅 target_id",
                field_hint="nodes[target_kind=restaurant].target_id",
            )
        )
        return out

    restaurants = {r.id: r for r in load_restaurants()}
    rest = restaurants.get(dining_node.target_id)
    if rest is None:
        out.append(
            CriticViolation(
                critic="time_window",
                severity="hard",
                message=f"餐厅 {dining_node.target_id} 不存在 mock 数据",
                field_hint=f"nodes[restaurant].target_id={dining_node.target_id}",
            )
        )
        return out

    # dining_node.start_time 形如 "17:30"
    want_time = dining_node.start_time
    slot = next((s for s in rest.reservation_slots if s.time == want_time), None)
    if slot is None:
        out.append(
            CriticViolation(
                critic="time_window",
                severity="hard",
                message=f"餐厅 {rest.id} 无 {want_time} 时段配置",
                field_hint=f"nodes[restaurant].start_time={want_time}",
            )
        )
    elif not slot.available:
        suggest = next(
            (s.time for s in rest.reservation_slots if s.available and s.time > want_time),
            None,
        )
        msg = f"餐厅 {rest.id} {want_time} 已满"
        if suggest:
            msg += f"（建议改 {suggest}）"
        out.append(
            CriticViolation(
                critic="time_window",
                severity="hard",
                message=msg,
                field_hint=f"nodes[restaurant].start_time={want_time}",
            )
        )
    return out


def _budget_critic(
    plan: Itinerary, intent: IntentExtraction  # noqa: ARG001
) -> list[CriticViolation]:
    """C3：餐厅人均 + POI 门票总价 ≤ user.default_budget × party_size × 1.5（容忍 50%）。"""
    out: list[CriticViolation] = []
    profile = load_user_profile()
    party = max(1, sum(c.count for c in intent.companions) or 1)
    budget_cap = profile.default_budget * party * 1.5

    # 餐厅人均
    dining = _find_dining_node(plan)
    rest_cost = 0.0
    if dining and dining.target_id:
        rest = next(
            (r for r in load_restaurants() if r.id == dining.target_id), None
        )
        if rest:
            rest_cost = float(rest.avg_price) * party

    # POI 门票（取 price_range 下限作下界估算）
    main = _find_main_node(plan)
    poi_cost = 0.0
    if main and main.target_id:
        from data.loader import load_pois

        poi = next((p for p in load_pois() if p.id == main.target_id), None)
        if poi and poi.price_range:
            poi_cost = float(poi.price_range[0]) * party

    total = rest_cost + poi_cost
    if total > budget_cap:
        out.append(
            CriticViolation(
                critic="budget",
                severity="soft",  # 不硬卡，纪念日场景预算无所谓
                message=(
                    f"预估总价 {total:.0f} 元（餐厅 {rest_cost:.0f} + 门票 {poi_cost:.0f}）"
                    f"超过预算上限 {budget_cap:.0f} 元"
                ),
                field_hint="default_budget",
            )
        )
    return out


def _style_critic(
    plan: Itinerary, intent: IntentExtraction
) -> list[CriticViolation]:
    """C4：主活动 POI / 用餐餐厅的 suitable_for 含场景的 social_context。"""
    out: list[CriticViolation] = []
    ctx = intent.social_context

    main = _find_main_node(plan)
    if main and main.target_id:
        from data.loader import load_pois

        poi = next((p for p in load_pois() if p.id == main.target_id), None)
        if poi is not None and ctx not in poi.suitable_for:
            out.append(
                CriticViolation(
                    critic="style",
                    severity="soft",
                    message=(
                        f"主活动 POI 「{poi.name}」未适配场景调性 {ctx}（实际 "
                        f"suitable_for={poi.suitable_for}）"
                    ),
                    field_hint=f"nodes[poi].target_id={poi.id}",
                )
            )

    dining = _find_dining_node(plan)
    if dining and dining.target_id:
        rest = next(
            (r for r in load_restaurants() if r.id == dining.target_id), None
        )
        if rest is not None and ctx not in rest.suitable_for:
            out.append(
                CriticViolation(
                    critic="style",
                    severity="soft",
                    message=(
                        f"用餐餐厅「{rest.name}」未适配场景调性 {ctx}（实际 "
                        f"suitable_for={rest.suitable_for}）"
                    ),
                    field_hint=f"nodes[restaurant].target_id={rest.id}",
                )
            )
    return out


# ============================================================
# 主入口
# ============================================================

def run_critics(plan: Itinerary, intent: IntentExtraction) -> CriticReport:
    """跑全部 Critic 返聚合报告。

    硬违规 → passed=False，整体应被拒；软违规 → 仅扣 soft_score。
    """
    all_violations: list[CriticViolation] = []
    all_violations.extend(_hard_constraint_critic(plan, intent))
    all_violations.extend(_time_window_critic(plan, intent))
    all_violations.extend(_budget_critic(plan, intent))
    all_violations.extend(_style_critic(plan, intent))

    hard = [v for v in all_violations if v.severity == "hard"]
    soft = [v for v in all_violations if v.severity == "soft"]

    # soft_score：每条软违规扣 0.15，下限 0
    soft_score = max(0.0, 1.0 - 0.15 * len(soft))

    return CriticReport(
        passed=not hard,
        violations=all_violations,
        soft_score=soft_score,
    )
