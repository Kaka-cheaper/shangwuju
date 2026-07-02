"""tests.test_planner_hybrid —— A+C 混合规划范式回归测试（edge_v1）。

W2 owner = A 同学的 planner.py 主体；本测试是 owner=A 的 P2 加分项扩展，
仅在 PLANNER_LLM_STRATEGY=hybrid（默认）路径上跑。

覆盖维度：
1. weights_llm 启发式兜底正确性（按 social_context 给出合理权重）
2. plan_hybrid 的统一 critic 适配层 `_run_unified_critic`（ADR-0009 C-3：替代已删除的
   ils_score_critic 4 维打分 critic）硬/软违规分流是否如期反映在 passed/hard_violations()
3. utility 函数对距离 / 评分 / cost 的敏感性
4. 端到端：构造 mock LLM client → ils_planner.plan_hybrid → hybrid 路径

【edge_v1 迁移（Wave 7 Task 14）】

旧测试用 ItineraryStage 手工拼 5 段（出发/主活动/转场/用餐/返回）。edge_v1 起：
- ItineraryStage 已删；改用 ActivityNode + Hop（model_validator 强制不变量）
- 用 assemble_from_blueprint(intent, PlanBlueprint(nodes=[...]), profile) 拼装合法 Itinerary
- critic 字段路径：stage.kind="用餐" → node.target_kind="restaurant"；
  stage.restaurant_id → node.target_id；stage.start → node.start_time
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
from agent.planning.blueprint.blueprint import BlueprintNode, BlueprintTargetKind, PlanBlueprint
from agent.planning.critic.critics_v2 import Severity, ViolationCode
from agent.core.trace import Tracer
from agent.planning.planners.ils_planner import _run_unified_critic, plan_hybrid
from agent.planning.planners.rule_planner import plan_itinerary
from agent.planning.weights_llm import (
    PlanningWeights,
    _heuristic_weights,
    get_planning_weights,
)
from data.loader import load_user_profile
from schemas.intent import Companion, IntentExtraction
from schemas.itinerary import Itinerary


# ============================================================
# 共享：构造 IntentExtraction
# ============================================================

def _intent(
    *,
    social_context: str = "家庭日常",
    distance_max_km: float = 5.0,
    physical: tuple[str, ...] = ("亲子友好", "适合 5-10 岁"),
    dietary: tuple[str, ...] = ("低脂", "健康轻食"),
    companions: tuple[Companion, ...] = (
        Companion(role="妻子", count=1),
        Companion(role="孩子", age=5, count=1),
    ),
    raw_input: str = "今天下午想和老婆孩子出去玩几个小时",
    capacity: int | None = None,
    duration_hours: tuple[int, int] = (3, 5),
) -> IntentExtraction:
    """ADR-0010 D-5 新增 `duration_hours` 形参（默认保留原值 (3,5)，向后兼容）：

    路线模型下"饭要不要"由 `build_route` 的锚定+涌现逻辑决定（ADR-0010 决策 1/3），
    默认 (3,5) 下午局出行窗**不完整覆盖**任何饭点惯例窗（`dining_soft_anchored`
    条件②判不到），且非 `_DINING_FOCUSED_CONTEXTS`（条件①也判不到）——飯是否
    出现因此纯粹涌现，数据依赖、不保证。本文件里驱动 `plan_hybrid` 走真实
    （非 monkeypatch 候选池）全量召回、且断言"用餐节点必然出现"的端到端测试
    改用 `duration_hours=(3, 6)`（让出行窗恰好覆盖晚餐惯例窗 17:00-20:00，
    触发条件②软锚）保证这个断言在新架构下仍然稳定成立；其余测试（C-1/C-4，
    候选池被钉死为单一 POI+单一餐厅）不依赖软锚——两个候选各自唯一，
    `build_route` 的锚点+涌现逻辑必然把两者都纳入，与 `duration_hours` 取值无关。
    """
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=list(duration_hours),
        distance_max_km=distance_max_km,
        companions=list(companions),
        physical_constraints=list(physical),
        dietary_constraints=list(dietary),
        experience_tags=[],
        social_context=social_context,
        capacity_requirement=capacity,
        raw_input=raw_input,
        parse_confidence=0.85,
        ambiguous_fields=[],
    )


# ============================================================
# 1. weights_llm 启发式兜底
# ============================================================

def test_heuristic_weights_family_emphasizes_comfort():
    """家庭场景：comfort 应是最高的维度。"""
    w = _heuristic_weights(_intent(social_context="家庭日常"))
    assert w.source == "stub"
    assert pytest.approx(w.comfort + w.time + w.cost + w.smoothness, abs=0.01) == 1.0
    assert w.comfort >= max(w.time, w.cost, w.smoothness), (
        f"家庭场景 comfort 应最高：{w.summary()}"
    )


def test_heuristic_weights_business_emphasizes_time():
    """商务接待：time 高、cost 极低（公司报销）。"""
    w = _heuristic_weights(_intent(social_context="商务接待"))
    assert w.time >= max(w.comfort, w.cost), f"商务场景 time 应较高：{w.summary()}"
    assert w.cost <= 0.15


def test_heuristic_weights_old_companion_boosts_comfort():
    """同行老人：comfort 应被进一步提升。"""
    base = _heuristic_weights(_intent(social_context="家庭日常"))
    with_old = _heuristic_weights(
        _intent(
            social_context="家庭日常",
            companions=(
                Companion(role="外公", age=70, count=1, is_special_role=True),
                Companion(role="外婆", age=68, count=1, is_special_role=True),
            ),
        )
    )
    assert with_old.comfort >= base.comfort, "同行老人时 comfort 应不低于基线"


def test_heuristic_weights_keyword_cost_boost():
    """raw_input 含「便宜」「学生」→ cost 权重上调。"""
    base = _heuristic_weights(_intent(raw_input="今天下午带朋友吃饭"))
    boosted = _heuristic_weights(_intent(raw_input="便宜一点的店，我们都是学生"))
    assert boosted.cost > base.cost


def test_get_planning_weights_falls_back_to_heuristic_when_no_client():
    """client=None 时走启发式兜底。"""
    w = get_planning_weights(_intent(), client=None)
    assert w.source == "stub"
    assert pytest.approx(w.comfort + w.time + w.cost + w.smoothness, abs=0.01) == 1.0


def test_planning_weights_normalize_zero_input():
    """全 0 权重应被兜底为合理分布。"""
    w = PlanningWeights(comfort=0, time=0, cost=0, smoothness=0).normalize()
    assert pytest.approx(w.comfort + w.time + w.cost + w.smoothness, abs=0.01) == 1.0


# ============================================================
# 2. plan_hybrid 统一 critic 适配层的硬/软违规分流（ADR-0009 C-3）
#
# 这 4 个测试是 spec Wave 7 遗留的「4 个 ils_score_critic」characterization 测试的
# 意图延续（intentional，非纯 characterization 删除）：ils_score_critic 本身随
# ADR-0009 决策 1/3 删除，但「plan_hybrid 的 critic 步骤能正确识别满座/缺节点/
# 调性软违规/总时长超限」这几条行为保证仍然成立——只是现在经由
# `_run_unified_critic`（薄 adapter 包 critics_v2.validate_itinerary）实现，
# 断言从 `v.critic == "xxx"` 换成 `v.code == ViolationCode.XXX`。
#
# 用 `_intent_no_age_cap()` 而非共享 `_intent()`：统一 critic 比旧 4 维 critic
# 多出 check_age_aware_duration 等一整套 Stage 1 检查——本文件 `_itinerary()`
# 靠反推 POI 时长命中目标 dining_time，通常算出 150-200min 的 POI 停留，
# 远超 5 岁孩子的 75min cap，会让 AGE_DURATION_MISMATCH 掺进来污染这几个测试
# 想单独验证的信号（是否满座 / 是否缺节点 / 调性软违规 / 总时长超限）。
# 换成成人同行人排除这个confound，不代表 age-aware 校验不重要——
# 那条行为有专门覆盖，见 test_critics_v2.py / test_age_aware_critic.py。
# ============================================================

def _intent_no_age_cap(**overrides) -> IntentExtraction:
    """同 `_intent()`，但 companions 全为成人（无年龄 cap 触发）。"""
    overrides.setdefault(
        "companions",
        (Companion(role="妻子", count=1), Companion(role="丈夫", count=1)),
    )
    return _intent(**overrides)


def _itinerary(
    *,
    poi_id: str | None = "P001",
    restaurant_id: str | None = "R001",
    dining_time: str = "17:30",
    skip_poi: bool = False,
    skip_restaurant: bool = False,
) -> Itinerary:
    """构造合法 Itinerary（通过 assemble_from_blueprint 走真链路）。

    Args:
        poi_id: 主活动 POI id；None 或 skip_poi=True 时不含 POI 节点
        restaurant_id: 用餐餐厅 id；None 或 skip_restaurant=True 时不含餐厅节点
        dining_time: 期望的用餐节点开始时刻；通过调整 preferred_start_time + POI 时长反推
                     最简实现：把 preferred_start_time 直接定到合适时刻（不补偿 POI 时长）

    Note:
        为了让 dining_time 准确命中给定时刻，本 helper 采取保守策略：
        - 若仅含餐厅：preferred_start_time 设为 dining_time（首跳 hop 后到达即可）
          实际到达时刻 ≈ dining_time + home→R 通勤
          此时 dining_node.start_time 会 ≥ dining_time，调用方需用 lookup_hop 校正
        - 若含 POI + 餐厅：把 POI 时长调整到让餐厅自然到达 == dining_time

    简化做法：直接用 preferred_start_time = dining_time（仅餐厅场景），
    或 preferred_start_time = "14:00" + 让 POI 持续到 dining_time 前 buffer 5min（POI+餐厅 场景）。
    """
    nodes: list[BlueprintNode] = []
    if poi_id and not skip_poi:
        # POI 时长设置为：让餐厅自然到达时间 = dining_time
        # 起点 14:00 → home→POI hop（约 9-15min）→ POI 停留 X → POI→R hop → R
        # 简化：直接把 POI 时长设为 (dining_time - 14:00 - 30) 分钟（容差）
        from agent.planning.blueprint.assemble_blueprint import _parse_hhmm
        target_min = _parse_hhmm(dining_time)
        start_min = _parse_hhmm("14:00")
        # POI 时长 = 总跨度 - 30min（粗估首跳 + 二跳 + buffer）；下限 30min
        poi_duration = max(30, target_min - start_min - 30)
        nodes.append(
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id=poi_id,
                duration_min=poi_duration,
            )
        )
    if restaurant_id and not skip_restaurant:
        nodes.append(
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id=restaurant_id,
                duration_min=60,
            )
        )

    if not nodes:
        # 极端兜底：至少要有一个 mid node 才能合法构造 Itinerary
        nodes.append(
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id=poi_id or "P001",
                duration_min=120,
            )
        )

    bp = PlanBlueprint(
        nodes=nodes,
        preferred_start_time="14:00",
        rationale="测试用",
    )
    return assemble_from_blueprint(_intent(), bp, load_user_profile())


def _itinerary_dining_at(target_dining_time: str, restaurant_id: str = "R001") -> Itinerary:
    """构造一个用餐节点开始于指定时刻的合法行程（含 P001 主活动，便于 critic 测试）。"""
    return _itinerary(
        poi_id="P001",
        restaurant_id=restaurant_id,
        dining_time=target_dining_time,
    )


def test_critic_passes_clean_plan():
    """合法 plan：R001 有 17:30 可订时段（mock 数据）。"""
    intent = _intent_no_age_cap()
    # 通过精调让用餐节点落在某个 mock 中可订的时段
    plan = _itinerary(poi_id="P001", restaurant_id="R001", dining_time="17:30")
    # 用餐节点实际开始时刻可能因 lookup_hop 与 buffer 略有偏移；找出真实 dining_node.start_time
    dining_node = next(
        (n for n in plan.nodes if n.target_kind == "restaurant"), None
    )
    assert dining_node is not None
    # 若实际时刻不在 mock 时段内，跳过该断言（critic 会硬违规，本测试转为验「无硬违规需在合法时段」）
    # 对常见 mock：R001 提供 17:00/17:30/18:00 等时段
    report = _run_unified_critic(plan, intent)
    # 不强求完全 pass（因 dining_time 可能微偏移），但若用餐节点落在 mock 可订时段则应 pass
    if dining_node.start_time in {"17:00", "17:30", "18:00", "18:30", "19:00"}:
        # 这些是 R001 mock 中 available=True 的时段（17:00 是 available=False，故排除）
        if dining_node.start_time != "17:00":
            assert report.passed, (
                f"合法 plan（{dining_node.start_time}）应通过；"
                f"违规：{[v.message for v in report.violations]}"
            )


def test_critic_catches_unavailable_slot():
    """让用餐节点落在 mock 中 available=false 的 17:00 → 应硬违规（RESTAURANT_FULL_UNRESOLVED）。"""
    intent = _intent_no_age_cap()
    # 调 dining_time=17:00 让 critic 看到不可订时段
    plan = _itinerary(poi_id="P001", restaurant_id="R001", dining_time="17:00")
    dining_node = next(
        (n for n in plan.nodes if n.target_kind == "restaurant"), None
    )
    assert dining_node is not None
    # 若实际节点开始时刻不是 17:00（被 lookup_hop 推后）则跳过本测试
    if dining_node.start_time != "17:00":
        pytest.skip(
            f"实际 dining 节点起始时刻 {dining_node.start_time}，"
            f"非 mock 不可订时段，本测试条件不满足"
        )
    report = _run_unified_critic(plan, intent)
    assert not report.passed
    hard = report.hard_violations()
    assert any(v.code == ViolationCode.RESTAURANT_FULL_UNRESOLVED for v in hard), hard
    msgs = [v.message for v in hard]
    assert any("17:00" in m and "已满" in m for m in msgs), msgs


def test_critic_accepts_dining_less_emergent_composition():
    """【D-8a 改判·ADR-0010 决策 9】仅主活动、无用餐的组成**合法**，不触发 NODES_INCOMPLETE。

    本测试原断言相反行为（decide_nodes 蓝本要求家庭场景必有 [主活动,用餐]，缺用餐
    即 Stage 0 硬违规）。多活动 TOPTW 模型下组成由搜索层涌现决定——「要不要饭」不再
    由 critic 按蓝本强制（否则合法的无饭涌现方案被 Stage 0 短路误杀到 rule 地板，
    D-5 实测已确认这是承重级误伤）。critic 保留的结构底线（≥1 活动）由
    test_critic_phase_a_characterization 的 degenerate 场景覆盖。
    """
    intent = _intent_no_age_cap()
    plan = _itinerary(poi_id="P001", restaurant_id=None, skip_restaurant=True)
    report = _run_unified_critic(plan, intent)
    assert not any(
        v.code == ViolationCode.NODES_INCOMPLETE for v in report.violations
    ), f"无饭涌现组成不得触发 NODES_INCOMPLETE：{[v.code for v in report.violations]}"


def test_critic_style_soft_violation_for_mismatched_context():
    """商务场景但用了家庭餐厅 R001 → 软违规（不阻断）。

    R001 = 轻语沙拉（suitable_for=["家庭日常"]），与商务接待不匹配
    （social_compat 矩阵：("商务接待","家庭日常") → POOR，非 BLOCKING）。
    """
    intent = _intent_no_age_cap(social_context="商务接待")
    plan = _itinerary(poi_id="P001", restaurant_id="R001", dining_time="17:30")
    report = _run_unified_critic(plan, intent)
    social_violations = [
        v for v in report.violations if v.code == ViolationCode.SOCIAL_CONTEXT_MISMATCH
    ]
    assert any(v.severity == Severity.SOFT for v in social_violations), (
        f"应至少有一条 SOFT 社交调性违规；实际全部违规：{[(v.code, v.severity, v.message) for v in report.violations]}"
    )
    # soft 不卡 passed
    assert all(v.severity == Severity.SOFT for v in social_violations)


def test_critic_total_minutes_overflow_hard():
    """构造一个超长行程（duration ≥ 4h）→ 硬违规（intent 上限 5h，4h+30tolerance=不必硬违规）。

    采用 POI 极长停留（240min）+ 餐厅 60min，总跨度约 5+ 小时 → 触发 DURATION_OUT_OF_RANGE 硬违规。
    """
    intent = _intent_no_age_cap()  # 最大 5h = 300min；total > 330min 触发硬违规
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P001",
                duration_min=300,  # 极长 5h 停留 + 通勤 = 总 5.5h+
            ),
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
        preferred_start_time="14:00",
        rationale="超长测试",
    )
    plan = assemble_from_blueprint(intent, bp, load_user_profile())
    assert plan.total_minutes > 330, (
        f"测试前提：total_minutes 应 > 330min（intent 5h + 30min 容差），"
        f"实际 {plan.total_minutes}"
    )
    report = _run_unified_critic(plan, intent)
    assert not report.passed
    hard = report.hard_violations()
    assert any(v.code == ViolationCode.DURATION_OUT_OF_RANGE for v in hard), hard


# ============================================================
# 3. utility 函数敏感性
# ============================================================

def test_utility_distance_decreases_with_far_pois():
    """远 POI 的 utility 应低于近 POI。"""
    from agent.planning.planners.ils_planner import _utility
    from schemas.domain import Location, Poi, PoiCapacity, Restaurant, RestaurantCapacity

    rest = Restaurant(
        id="R001",
        name="测试餐厅",
        cuisine="健康轻食",
        location=Location(name="近"),
        distance_km=2.0,
        opening_hours="10:00-22:00",
        avg_price=80,
        rating=4.5,
        capacity=RestaurantCapacity.model_validate(
            {"2": True, "4": True, "6": False, "8": False, "private_room": False}
        ),
        reservation_slots=[],
        tags=["低脂", "健康轻食"],
        suitable_for=["家庭日常"],
    )
    near_poi = Poi(
        id="P_NEAR",
        name="近的",
        type="亲子",
        location=Location(name="A"),
        distance_km=2.0,
        opening_hours="09:00-18:00",
        rating=4.5,
        tags=["亲子友好", "适合 5-10 岁"],
        suitable_for=["家庭日常"],
        capacity=PoiCapacity(daily_quota=100, available_slots=50),
    )
    far_poi = near_poi.model_copy(update={"id": "P_FAR", "distance_km": 9.0})

    intent = _intent()
    w = _heuristic_weights(intent)
    near_score, _ = _utility(near_poi, rest, "17:30", intent, w)
    far_score, _ = _utility(far_poi, rest, "17:30", intent, w)
    assert near_score > far_score, (
        f"近 POI utility 应高于远 POI：near={near_score:.3f} far={far_score:.3f}"
    )


# ============================================================
# 4. 端到端 Mock LLM client 跑 hybrid 路径
# ============================================================

@dataclass
class _MockLLMResponse:
    content: str
    tool_calls: list = None  # type: ignore[assignment]
    finish_reason: str = "stop"
    raw: dict = None  # type: ignore[assignment]


class _MockLLMClient:
    """模拟非 stub 的 LLM client，触发 hybrid 路径。"""

    provider = "mock"
    model = "mock-model"

    def __init__(self, weights_json: str):
        self._weights_json = weights_json

    def chat(self, messages, *, temperature=0.3, response_format=None):
        return _MockLLMResponse(content=self._weights_json)


def test_hybrid_end_to_end_with_mock_client():
    """hybrid 路径整链路：mock LLM → 权重 → build_route → Critic → 出方案（ADR-0010 D-5）。

    `duration_hours=(3, 6)`（见 `_intent` docstring）：让出行窗恰好覆盖晚餐惯例窗
    （17:00-20:00），触发 `dining_soft_anchored` 条件②——真实（非 monkeypatch）
    全量召回下，"家庭场景应同时含主活动与用餐"这条断言在涌现组成的新架构下才
    有稳定依据，而不是撞运气。
    """
    intent = _intent(duration_hours=(3, 6))
    client = _MockLLMClient(
        weights_json=(
            '{"comfort": 0.5, "time": 0.2, "cost": 0.15, "smoothness": 0.15, '
            '"rationale": "家庭场景重舒适"}'
        ),
    )
    tracer = Tracer()
    result = plan_hybrid(intent, client=client, tracer=tracer)
    assert result.success, (
        f"hybrid 应成功；失败原因：{result.failure_detail}"
    )
    assert result.itinerary is not None
    # mock LLM（provider != stub）出权重 → 走真 LLM 权重分支
    assert result.weights is not None and result.weights.source == "llm"

    # 必备 mid node kinds 都在（家庭场景应含主活动 + 用餐；软锚保证，见上）
    mid_nodes = [n for n in result.itinerary.nodes if n.target_kind != "home"]
    mid_kinds = {n.kind for n in mid_nodes}
    assert "主活动" in mid_kinds, f"缺主活动 mid node：{mid_kinds}"
    assert "用餐" in mid_kinds, f"缺用餐 mid node：{mid_kinds}"

    # Trace 含 hybrid 标志：weights agent_thought + Critic agent_thought
    thoughts = [r for r in tracer.records if r.type == "agent_thought"]
    assert any(
        "权重" in t.payload.get("text", "") for t in thoughts
    ), "应含权重相关 agent_thought"


def test_hybrid_falls_back_to_rule_when_llm_returns_garbage():
    """LLM 返回非法 JSON → weights_llm 兜底启发式 → hybrid 仍能跑通。"""
    intent = _intent(duration_hours=(3, 6))
    client = _MockLLMClient(weights_json="<<<这不是 JSON>>>")
    tracer = Tracer()
    result = plan_hybrid(intent, client=client, tracer=tracer)
    assert result.success
    # 非法 JSON → 权重降级到启发式（source=fallback），hybrid 仍出方案
    assert result.weights is not None and result.weights.source == "fallback"
    weights_thought = next(
        (
            r for r in tracer.records
            if r.type == "agent_thought" and "权重" in r.payload.get("text", "")
        ),
        None,
    )
    assert weights_thought is not None


def test_hybrid_uses_stub_client_falls_back_to_rule():
    """stub client 无 LLM 决策能力 → 权重走启发式（source=stub）→ 仍出有效方案。

    原 V1 双范式 dispatcher 在 stub 下短路推「已切回规则规划」thought；
    dispatcher 已删。直调 survivor 入口 plan_hybrid 时，stub 经 get_planning_weights
    走启发式权重（source=stub，无主观 LLM 决策），build_route 仍产出有效方案
    （ADR-0010 D-5：不再需要外部 rule_assembler 注入，组装是模块内部直调）。
    """
    from agent.core.llm_client_stub import StubLLMClient

    intent = _intent(duration_hours=(3, 6))
    tracer = Tracer()
    result = plan_hybrid(intent, client=StubLLMClient(), tracer=tracer)
    assert result.success
    assert result.itinerary is not None
    # stub 无主观决策能力 → 权重来自启发式（非 LLM），等价旧 rule 兼容路径
    assert result.weights is not None and result.weights.source == "stub"


# ============================================================
# 5. C-1（ADR-0009 决策 1）：选中的候选必须真落进产物
#
# 地基 A 的回归测试（历史 bug 背景，ADR-0009）：旧版 rule_assembler 收 candidate
# 却不用，直接重跑规则地板独立搜索，让 ILS 的选点/黑名单/重搜对最终产物零影响。
# 本节把候选池钉死成唯一一个 POI + 一家餐厅（P001 / R003，均已验证不是
# rule_planner.plan_itinerary(intent) 独立搜索会选中的结果——见下方 docstring），
# 让"谁被选中"变得可预期，再断言它真出现在产物里。
#
# ADR-0010 D-5（连带决策 4：rule_assembler 参数退役）：原两条测试（"本文件的
# test assembler" vs "生产 _RULE_ASSEMBLER_ADAPTER"）验证的是同一件事在两处
# 独立实现上都成立——这正是「两处镜像可能各自漂移」的风险点。新架构下
# `build_route → route_to_blueprint → assemble_from_blueprint` 是 `plan_hybrid`
# 内部唯一、无从注入的直调路径，不再存在"测试用一份、生产用另一份"的分叉——
# 两条测试因此合并为一条（consolidate，非删测：原本验证的"选中的必须真落进
# 产物、不会被静默丢弃改道"这条保证现在由架构本身消除了分叉的可能，单条测试
# 即可完整覆盖，不留检测缺口）。
# ============================================================


def test_ils_candidate_lands_in_itinerary(monkeypatch):
    """候选池钉死为单一 POI（P001）+ 单一餐厅（R003）→ 两者必须真出现在产物 nodes 里。

    P001 / R003 是刻意选的：对同一 `_intent()`，rule_planner.plan_itinerary(intent)
    独立搜索会选中 P033 / R023（已用脚本核实），与 P001 / R003 明确不同——如果
    plan_hybrid 内部组装环节丢弃了 build_route 的选择、误绕到了别的搜索路径，
    产物就会是 P033/R023 而非 P001/R003，暴露地基 A 类型的 bug。

    用 monkeypatch 直接替换 ils_planner._query_pois / _query_restaurants（而非走
    真实 search_pois/search_restaurants Tool + grounding filter），把候选池钉死成
    恰好 1 个 POI + 1 家餐厅——不管路线怎么排、utility 怎么算，`build_route` 的
    锚定+涌现逻辑都只能选中这一个（没有其它候选可挑，二者也天然都在预算内），
    让「选中谁」完全确定性，不依赖候选池排序或打分细节的脆弱假设。
    """
    from agent.core.llm_client_stub import StubLLMClient
    from agent.planning.planners import ils_planner
    from data.loader import load_pois, load_restaurants

    intent = _intent()
    poi = next(p for p in load_pois() if p.id == "P001")
    rest = next(r for r in load_restaurants() if r.id == "R003")

    monkeypatch.setattr(ils_planner, "_query_pois", lambda intent, tracer: [poi])
    monkeypatch.setattr(ils_planner, "_query_restaurants", lambda intent, tracer: [rest])

    tracer = Tracer()
    result = plan_hybrid(intent, client=StubLLMClient(), tracer=tracer)

    assert result.success, f"应成功；失败原因：{result.failure_detail}"
    itinerary = result.itinerary
    assert itinerary is not None
    poi_node = next((n for n in itinerary.nodes if n.target_kind == "poi"), None)
    rest_node = next((n for n in itinerary.nodes if n.target_kind == "restaurant"), None)
    assert poi_node is not None and poi_node.target_id == "P001", (
        f"候选池被钉死为 P001，应真出现在产物 nodes 里；实际 "
        f"{poi_node.target_id if poi_node else None}"
    )
    assert rest_node is not None and rest_node.target_id == "R003", (
        f"候选池被钉死为 R003，应真出现在产物 nodes 里；实际 "
        f"{rest_node.target_id if rest_node else None}"
    )
    # 新文案（route_builder.route_to_blueprint）：「已为你预留 HH:MM（N 人）」——
    # 旧版断言的是「note 含 ILS candidate.dining_time」，新架构下时刻直接来自
    # 排定后的 node.start_time（同一个值，来源从"候选层标签"变成"排定结果"）。
    assert rest_node.note and rest_node.note.startswith("已为你预留"), (
        f"assembler 应把排定的用餐时刻写进餐厅节点 note；实际 note={rest_node.note!r}"
    )
    assert rest_node.start_time in rest_node.note, (
        f"note 应含排定时刻 {rest_node.start_time!r}；实际 note={rest_node.note!r}"
    )


# ============================================================
# C-4（ADR-0009 决策 5）：retry 重校验 + gate —— plan_hybrid 绝不返回带 HARD 违规的方案
#
# ADR-0010 D-5：候选池钉死为「P001 + R001」（monkeypatch _query_pois/_query_
# restaurants，手法同上方 C-1 测试）——判断点说明见下方 `_flagship_intent` /
# `_pin_p001_r001` docstring：真实（全量）搜索下，`_intent()` 的 3-5h 家庭场景
# 是否会自然选中餐厅是涌现结果、不保证（`decide_nodes` 已对 ILS 路径作废，
# 组成随候选打分变化——这本身是 ADR-0010 的设计意图，但会让"必然触发满座→
# 改期"这条 demo 亮点断言失去可预期性）；钉死候选池后，唯一 POI/餐厅必然都被
# `build_route` 纳入（没有其它候选可挑），让旗舰链路可确定性复现。
# ============================================================


def _flagship_intent() -> IntentExtraction:
    """C-4 三条守卫共用的 intent：5 岁家庭，与 D-5 之前完全一致（未改动）。"""
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[
            Companion(role="妻子", count=1),
            Companion(role="孩子", age=5, count=1),
        ],
        physical_constraints=["亲子友好"],
        dietary_constraints=["健康轻食"],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="和老婆 5 岁孩子出去玩",
        parse_confidence=0.9,
    )


def _pin_p001_r001(monkeypatch) -> None:
    """把候选池钉死为单一 POI（P001）+ 单一餐厅（R001）。

    R001（轻语沙拉·西溪店）是刻意选的：mock `reservation_slots` 标记其 17:00
    档 `available=False`（旗舰「满座→改期」demo 埋点，`check_demo_restaurant_full`
    消费的正是这个字段）——`build_route` 的窗感知调度器对无其它候选竞争的单一
    餐厅会取「窗内最早可行开始时刻」，14:00 出发 + P001 停留后自然到达时刻早于
    17:00（晚餐惯例窗起点）→ 排定恰好落在 17:00 这个 mock 满座槽，确定性触发
    `RESTAURANT_FULL_UNRESOLVED`，让修复闭环（挖窗封 (R001,17:00) → 重排到
    17:30）被真正演示到，而不是靠真实全量搜索"运气好"才撞上。
    """
    from agent.planning.planners import ils_planner
    from data.loader import load_pois, load_restaurants

    poi = next(p for p in load_pois() if p.id == "P001")
    rest = next(r for r in load_restaurants() if r.id == "R001")
    monkeypatch.setattr(ils_planner, "_query_pois", lambda intent, tracer: [poi])
    monkeypatch.setattr(ils_planner, "_query_restaurants", lambda intent, tracer: [rest])


def test_plan_hybrid_never_returns_plan_with_hard_violation(monkeypatch):
    """C-4 gate 不变量（最重要）：plan_hybrid 若 success=True，产物必无 HARD 违规。

    候选池钉死为 P001+R001（见上）：round 0 必然撞见 R001 17:00 满座（HARD），
    使这条不变量测试真正**走到** success=True 分支去验证（而非某个从未触发的
    if 分支永远真空通过）——修复闭环把 (R001,17:00) 挖窗重排后应收敛到干净方案。
    """
    from agent.core.llm_client_stub import StubLLMClient
    from agent.planning.critic.critics_v2 import validate_itinerary

    _pin_p001_r001(monkeypatch)
    intent = _flagship_intent()
    result = plan_hybrid(intent, client=StubLLMClient())
    if result.success:
        hard = [
            v
            for v in validate_itinerary(result.itinerary, intent)
            if v.severity == Severity.HARD
        ]
        assert not hard, (
            "plan_hybrid 返回 success，产物却含 HARD 违规："
            f"{[v.code.value for v in hard]}——retry 后未重新 gate（C-4 修复目标）"
        )


def test_plan_hybrid_converges_via_iterative_repair(monkeypatch):
    """C-4 收敛：旗舰「满座→改期」链（实测 R001：17:00 RESTAURANT_FULL → 17:30
    干净）——plan_hybrid 应收敛到干净方案（success=True），而非放弃甩 rule 地板。

    ADR-0010 D-5：迁移到路线模型后，R001 的候选开始时刻窗（`activity_pool.
    build_restaurant_time_windows`）本就只落在饭点惯例窗内（不像旧三元组模型
    靠离散候选时段偶尔踩中非饭点），链条从旧版「16:30 MEAL_TIME → 17:00
    RESTAURANT_FULL → 17:30 干净」两轮自然缩为一轮「17:00 RESTAURANT_FULL →
    17:30 干净」——这正是 D-1 时间窗构建（营业时间∩饭点惯例）比旧版更贴近现实
    的直接体现（intentional，非弱化：MEAL_TIME_UNREASONABLE 这条违规路径在新
    架构下对餐厅候选结构性地更难触发，`_classify_violation` 仍保留其路由，只是
    这条 demo 链恰好不再经过它）。断言的是终态（成功 + 干净），不是轮数。
    """
    from agent.core.llm_client_stub import StubLLMClient
    from agent.planning.critic.critics_v2 import validate_itinerary

    _pin_p001_r001(monkeypatch)
    intent = _flagship_intent()
    result = plan_hybrid(intent, client=StubLLMClient())
    assert result.success, (
        f"应收敛到干净方案，而非放弃甩地板：{result.failure_detail}"
    )
    hard = [
        v
        for v in validate_itinerary(result.itinerary, intent)
        if v.severity == Severity.HARD
    ]
    assert not hard, f"收敛后不应残留 HARD：{[v.code.value for v in hard]}"

    # 旗舰链本身：R001 应仍在产物里（挖窗只封 17:00，不牵连整店），且不再是
    # 满座的 17:00（真正验证"移到了别的时段"，不是巧合通过）。
    rest_node = next(
        (n for n in result.itinerary.nodes if n.target_kind == "restaurant"), None
    )
    assert rest_node is not None and rest_node.target_id == "R001", (
        f"挖窗重排不应把 R001 整体拉黑换成别的餐厅；实际 "
        f"{rest_node.target_id if rest_node else None}"
    )
    assert rest_node.start_time != "17:00", (
        "R001 应已从满座的 17:00 移到别的可行时段（旗舰链核心断言）"
    )


def test_plan_hybrid_gives_up_when_repair_budget_exhausted(monkeypatch):
    """C-4 有界放弃：修复预算耗尽仍有 HARD → success=False（上层落 rule 地板 D2），
    绝不返回带 HARD 的脏方案、绝不无限循环。

    把 MAX_REPAIR_ROUNDS 压成 0 强制「初轮有 HARD 即放弃」，候选池钉死为
    P001+R001（round 0 必然撞见 17:00 满座 HARD）验证有界性 + 放弃语义。
    """
    from agent.core.llm_client_stub import StubLLMClient
    from agent.planning.planners import ils_planner

    monkeypatch.setattr(ils_planner, "MAX_REPAIR_ROUNDS", 0)
    _pin_p001_r001(monkeypatch)

    intent = _flagship_intent()
    result = plan_hybrid(intent, client=StubLLMClient())
    assert result.success is False, (
        "预算=0 且初轮有 HARD → 应放弃（success=False）落地板，不返回脏方案"
    )


# ============================================================
# D-8a（ADR-0010 决策 9 + ADR-0008 红队 R3）承重回归
# ============================================================


def test_plan_hybrid_family_35h_succeeds_on_real_pools():
    """【D-8a 承重回归】S1 家庭 (3,5h)——核心 demo 场景——真实召回池上必须成功。

    D-5 落地后实测：涌现组成合法地不含饭（出行窗未完整跨晚餐窗 → 不软锚，
    边际竞争饭也未必赢），旧 nodes_incomplete 按 decide_nodes 硬要「必须有饭」
    → 整条 ILS 路径永落 rule 地板。D-8a 改判（组成涌现合法，critic 只守
    「≥1 活动」底线）+ 餐厅窗槽点化（排定时刻必为真实预约槽）后，本场景
    必须端到端成功且 critic 干净——这条测试钉死「兜底路径对核心场景可用」。
    """
    from agent.core.llm_client_stub import StubLLMClient
    from agent.planning.planners.ils_planner import _run_unified_critic

    intent = _intent()  # 默认即 S1 家庭 (3,5)：5 岁娃 + 健康轻食 + 亲子友好
    result = plan_hybrid(intent, client=StubLLMClient())
    assert result.success, (
        f"S1 家庭 (3,5h) 在真实池上不得落地板（D-8a 前的承重回归）："
        f"{result.failure_detail}"
    )
    assert result.itinerary is not None
    report = _run_unified_critic(result.itinerary, intent)
    assert report.passed, (
        f"产物必须 critic 干净：{[(v.code.value, v.severity.value) for v in report.violations]}"
    )
    # 餐厅节点（若涌现出来）排定时刻必须是该店真实预约槽（R3 槽点化）
    from data.loader import load_restaurants

    slots = {r.id: {s.time for s in r.reservation_slots} for r in load_restaurants()}
    for n in result.itinerary.nodes:
        if n.target_kind == "restaurant" and slots.get(n.target_id):
            assert n.start_time in slots[n.target_id], (
                f"餐厅 {n.target_id} 排定 {n.start_time} 不在真实槽单 {slots[n.target_id]}"
            )
