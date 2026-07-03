"""tests.test_finalize_plan_node —— 体感编排批 P1："先出方案，后出文案"。

覆盖：

1. `agent.graph.nodes.finalize_plan.finalize_plan_node`——单测三项职责
   （从 narrate.py 拆出，字面逻辑不变，只是执行时机提前）：
   - pending_actions（`build_confirm_actions`，纯规则）
   - 规则标题写回 summary（`build_template_title`）
   - decision_trace 收尾（final_strategy 判定 + 上一条 critic_attempt 标 resolved）
   以及早退契约（intent/itinerary 缺一即 no-op）。
2. `agent.graph._emit_handlers.emit_finalize_plan`——ITINERARY_READY 推送 +
   去重（`ctx.itinerary_emitted`）契约（细节见 test_narrate_node_actions.py
   的 emit 测试组，这里不重复，只覆盖 finalize_plan 特有的部分）。
3. 图级（stub）：驱动真实编译图，断言 ITINERARY_READY（由 finalize_plan 推）
   严格早于 AGENT_NARRATION（由 narrate 推）——这是本批感知延迟优化的核心
   可观测契约："先出方案，后出文案"不能只是设计意图，必须是可验证的事件序。
   覆盖 critic 直接通过 / ils 成功兜底两条入 finalize_plan 的边。
"""

from __future__ import annotations

import asyncio

from agent.graph._emit_context import EmitContext
from agent.graph._emit_handlers import emit_finalize_plan
from agent.graph.nodes.execute_finalize import build_confirm_actions
from agent.graph.nodes.finalize_plan import finalize_plan_node
from agent.intent.narrator import build_template_title
from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
from agent.planning.blueprint.blueprint import BlueprintNode, BlueprintTargetKind, PlanBlueprint
from data.loader import load_user_profile
from schemas.decision_trace import CriticAttempt, DecisionTrace, FallbackHop
from schemas.intent import Companion, IntentExtraction


# ============================================================
# 共享 fixture helpers
# ============================================================


def _intent() -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-07-02T14:00",
        duration_hours=[3, 5],
        distance_max_km=10.0,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="测试",
        parse_confidence=0.9,
    )


def _blueprint() -> PlanBlueprint:
    return PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=120,
            ),
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
        preferred_start_time="14:00",
        rationale="finalize_plan test blueprint",
    )


def _itinerary(*, decision_trace=None):
    intent = _intent()
    itin = assemble_from_blueprint(intent, _blueprint(), load_user_profile())
    if decision_trace is not None:
        itin = itin.model_copy(update={"decision_trace": decision_trace})
    return intent, itin


# ============================================================
# 1. finalize_plan_node：单测三项职责
# ============================================================


def test_finalize_plan_node_noop_when_intent_or_itinerary_missing():
    """早退契约：与 narrate_node 对称——没有方案可定稿就不做事。"""
    intent, itin = _itinerary()
    assert finalize_plan_node({"intent": None, "itinerary": itin}) == {}
    assert finalize_plan_node({"intent": intent, "itinerary": None}) == {}
    assert finalize_plan_node({}) == {}


def test_finalize_plan_node_writes_pending_actions():
    """pending_actions 从 narrate.py 挪来，字面逻辑不变——直接复用
    build_confirm_actions 现成的结果做比对基准，不重新定义一遍"应该长什么样"。
    """
    intent, itin = _itinerary()
    assert itin.pending_actions == []  # assemble 阶段还没有

    out = finalize_plan_node({"intent": intent, "itinerary": itin})
    new_itin = out["itinerary"]

    expected = build_confirm_actions(itin, intent)
    assert [a.model_dump() for a in new_itin.pending_actions] == [
        a.model_dump() for a in expected
    ]
    assert new_itin.pending_actions, "至少应有餐厅预约/门票/转发文案等动作"


def test_finalize_plan_node_writes_rule_title_into_summary():
    """规则标题：用 build_template_title 现成的构造器写回 summary，保证
    ITINERARY_READY 推送时已经是一句人话标题（不依赖 assemble 是否已经给对）。
    """
    intent, itin = _itinerary()
    # 人为造一个"占位/不理想"的 summary，验证 finalize_plan 会覆盖它。
    stale = itin.model_copy(update={"summary": "占位摘要"})

    out = finalize_plan_node({"intent": intent, "itinerary": stale})
    new_itin = out["itinerary"]

    expected_title = build_template_title(intent, stale)
    assert new_itin.summary == expected_title
    assert new_itin.summary != "占位摘要"


def test_finalize_plan_node_does_not_touch_summary_when_already_matching_rule_title():
    """summary 已经等于规则标题时不产生多余 diff 差异（幂等）。"""
    intent, itin = _itinerary()
    rule_title = build_template_title(intent, itin)
    already_titled = itin.model_copy(update={"summary": rule_title})

    out = finalize_plan_node({"intent": intent, "itinerary": already_titled})
    assert out["itinerary"].summary == rule_title


def test_finalize_plan_node_decision_trace_final_strategy_and_resolved():
    """decision_trace 收尾（叙事无关部分）：从 narrate.py 原样挪来。

    - final_strategy 取 fallback_chain 最后一跳的 to_stage。
    - 上一条未 resolved 的 critic_attempt 被标 resolved=True（能走到
      finalize_plan 说明 critic 已放行，反馈已被消化）。
    """
    trace = DecisionTrace(
        blueprint_rationale="测试",
        weights_explanation="舒适 0.4",
        critic_attempts=[
            CriticAttempt(
                attempt_n=1,
                violation_codes=["age_duration_mismatch"],
                feedback_summary="超时长",
                resolved=False,
            ),
        ],
        fallback_chain=[
            FallbackHop(from_stage="llm_first", to_stage="llm_backprompt", reason="critic 命中违规"),
        ],
        final_strategy="llm_first",
    )
    intent, itin = _itinerary(decision_trace=trace)

    out = finalize_plan_node({"intent": intent, "itinerary": itin})
    new_trace = out["itinerary"].decision_trace

    assert new_trace.final_strategy == "llm_backprompt"
    assert new_trace.critic_attempts[0].resolved is True


def test_finalize_plan_node_decision_trace_no_fallback_chain_defaults_llm_first():
    trace = DecisionTrace(
        blueprint_rationale="测试",
        weights_explanation="舒适 0.4",
        critic_attempts=[],
        fallback_chain=[],
        final_strategy="llm_first",
    )
    intent, itin = _itinerary(decision_trace=trace)

    out = finalize_plan_node({"intent": intent, "itinerary": itin})
    assert out["itinerary"].decision_trace.final_strategy == "llm_first"


# ============================================================
# 2. emit_finalize_plan：去重契约
# ============================================================


def test_emit_finalize_plan_does_not_double_emit_when_already_emitted():
    ctx = EmitContext()
    ctx.itinerary_emitted = True
    _intent_, itin = _itinerary()

    events = emit_finalize_plan(ctx, {"itinerary": itin})
    assert events == []


def test_emit_finalize_plan_noop_when_no_itinerary_in_diff():
    ctx = EmitContext()
    events = emit_finalize_plan(ctx, {})
    assert events == []
    assert ctx.itinerary_emitted is False


# ============================================================
# 3. 图级（stub）：ITINERARY_READY（finalize_plan）严格早于 AGENT_NARRATION（narrate）
# ============================================================


def _drive(*, user_input: str, session_id: str, planner_mode=None) -> list:
    async def _run() -> list:
        from agent.graph import sse_adapter as sse

        evs = []
        async for ev in sse.run_graph_stream(
            user_input=user_input,
            session_id=session_id,
            user_id="demo_user",
            planner_mode=planner_mode,
        ):
            evs.append(ev)
        return evs

    return asyncio.run(_run())


def _assert_ready_precedes_narration(evs: list, *, label: str) -> None:
    types = [e.type.value for e in evs]
    assert "itinerary_ready" in types, f"{label}：应出方案，events={types}"
    assert "agent_narration" in types, f"{label}：应有叙事文案，events={types}"
    ready_seq = next(e.seq for e in evs if e.type.value == "itinerary_ready")
    narration_seq = next(e.seq for e in evs if e.type.value == "agent_narration")
    assert ready_seq < narration_seq, (
        f"{label}：ITINERARY_READY（finalize_plan）必须严格早于 "
        f"AGENT_NARRATION（narrate）——体感编排批 P1 的核心可观测契约，"
        f"ready_seq={ready_seq} narration_seq={narration_seq}"
    )


def test_finalize_plan_ready_precedes_narration_rule_mode_direct_critic_pass():
    """critic 直接通过（走 finalize_plan → narrate 的第一条边）：rule 模式
    最快、最确定性地触发这条路径（不调 LLM 出蓝图，直接出 itinerary）。

    用户原话走壳2 canonical 字面短路（`DEMO_SCENARIOS[1]`）确定性直达
    planning——stub 模式下 `classify_input` 对任意输入必然抛异常，会落到
    保守地板 chitchat（同 test_d2_failure_drain.py 模块 docstring的既有说明），
    不依赖 LLM 分类结果。
    """
    from agent.routing.canonical_shortcut import DEMO_SCENARIOS

    evs = _drive(
        user_input=DEMO_SCENARIOS[1]["input"],  # S2
        session_id="p1_finalize_ready_before_narration_rule",
        planner_mode="rule",
    )
    _assert_ready_precedes_narration(evs, label="rule 模式直接通过")


def test_finalize_plan_ready_precedes_narration_ils_fallback_path(monkeypatch):
    """ils 成功兜底（走 finalize_plan → narrate 的第三条边）：复用
    test_d7_advisory_channel.py 的手法——强制 blueprint 恒为 None 逼流程
    自然切到 ils_fallback，monkeypatch plan_hybrid 返回一个确定性成功结果。
    """
    import agent.graph.nodes.planner as planner_mod
    import agent.planning.planners.ils_planner as ils_planner_mod
    from agent.planning.planners.ils_planner import HybridResult
    from agent.routing.canonical_shortcut import DEMO_SCENARIOS

    def _blueprint_always_none(*args, **kwargs):
        return None

    def _fake_hybrid_result(*args, **kwargs) -> HybridResult:
        intent, itin = _itinerary()
        return HybridResult(success=True, itinerary=itin, advisories=[])

    monkeypatch.setattr(planner_mod, "generate_blueprint", _blueprint_always_none)
    monkeypatch.setattr(ils_planner_mod, "plan_hybrid", _fake_hybrid_result)

    user_input = DEMO_SCENARIOS[1]["input"]  # S2：确定性直达 planning，不依赖 LLM 分类
    evs = _drive(
        user_input=user_input,
        session_id="p1_finalize_ready_before_narration_ils",
    )
    fallback_targets = [e.payload.get("to") for e in evs if e.type.value == "plan_fallback"]
    assert "ils" in fallback_targets, f"前置：应经过 ils_fallback，实际={fallback_targets}"
    _assert_ready_precedes_narration(evs, label="ils 成功兜底")


def test_ils_success_done_payload_final_strategy_is_ils(monkeypatch):
    """真因修复批 item 3 回归：ILS 一次成功兜底后，DONE payload 的
    final_strategy 必须是 "ils"，不能漏成 sse_adapter 的默认值 "llm_first"。

    根因回顾：ILS 成功产出的 itinerary 从不经过 assemble_node（decision_trace
    唯一注入点），itinerary.decision_trace 原生是 None；finalize_plan_node
    修复前对 None 直接跳过收尾，decision_trace 永远补不上，sse_adapter 读不到
    trace 就落回默认值 "llm_first"——无论方案实际是哪条链路兜出来的，看板
    永远显示"LLM 一次过"。

    复用同组 ils_fallback 驱动手法（强制 blueprint 恒为 None + monkeypatch
    plan_hybrid 成功），跑满整条 SSE 流，直接断言 DONE 事件 payload。
    """
    import agent.graph.nodes.planner as planner_mod
    import agent.planning.planners.ils_planner as ils_planner_mod
    from agent.planning.planners.ils_planner import HybridResult
    from agent.routing.canonical_shortcut import DEMO_SCENARIOS

    def _blueprint_always_none(*args, **kwargs):
        return None

    def _fake_hybrid_result(*args, **kwargs) -> HybridResult:
        intent, itin = _itinerary()
        return HybridResult(success=True, itinerary=itin, advisories=[])

    monkeypatch.setattr(planner_mod, "generate_blueprint", _blueprint_always_none)
    monkeypatch.setattr(ils_planner_mod, "plan_hybrid", _fake_hybrid_result)

    user_input = DEMO_SCENARIOS[1]["input"]
    evs = _drive(
        user_input=user_input,
        session_id="p3_ils_success_final_strategy_is_ils",
    )

    fallback_targets = [e.payload.get("to") for e in evs if e.type.value == "plan_fallback"]
    assert "ils" in fallback_targets, f"前置：应经过 ils_fallback，实际={fallback_targets}"

    done_events = [e for e in evs if e.type.value == "done"]
    assert len(done_events) == 1
    assert done_events[0].payload["final_strategy"] == "ils", (
        f"ILS 成功兜底后 DONE.final_strategy 应为 'ils'，实际 payload={done_events[0].payload}"
    )
