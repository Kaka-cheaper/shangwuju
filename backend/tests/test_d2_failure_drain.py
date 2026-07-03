"""D2 / failure-drain —— 图节点【非预期】异常的输出降级阶梯（resilience safety net）。

问题命名（prior art）：graceful degradation / output degradation ladder。
今天一个节点里【没预料到】的异常会冒泡到 sse_adapter 的 try/except，变成裸
STREAM_ERROR + DONE(has_itinerary=False)——用户看到一轮崩掉、没有方案。D2 让每一轮
都落到「输出降级阶梯」上：planner/assemble/critic/replan 异常 → 规则地板方案；
narrate 异常 → 推已通过的方案、跳过文案；search worker 异常 → 空候选继续；
intent 异常 → 兜底意图继续。同时【绝不静默】：原始异常完整 traceback 仍 loudly 落日志。

关键纪律：LangGraph 控制流异常（GraphBubbleUp / GraphInterrupt 及兄弟）必须原样
re-raise，绝不当普通错误吞掉——否则会破坏 HITL/interrupt 控制流。

测试套路：复用既有 graph-driving 测试模式（test_planner_mode_dispatch /
test_narrator_active_query），在 stub 模式下驱动真实编译图跑 run_graph_stream，
monkeypatch 目标抛【非预期】异常（裸 RuntimeError，不是 BlueprintGenError 这类领域错）。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import types
from pathlib import Path

import pytest

# ============================================================
# agent 命名空间桥接（与 test_graph_confirm_stream / test_narrator_active_query 同款）
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.graph import sse_adapter as sse  # noqa: E402
from agent.graph.nodes import router as router_mod  # noqa: E402
from agent.planning.blueprint.blueprint import (  # noqa: E402
    BlueprintNode,
    BlueprintTargetKind,
    PlanBlueprint,
)
from schemas.intent import Companion, IntentExtraction  # noqa: E402


# ============================================================
# ADR-0011 决策 2（E-1）垫桩：本文件所有用例走的 _USER_INPUT 曾靠已删除的规划
# 信号表 fast path（Layer 1.5）确定性落进 planning。词表删除后，同样的文本要
# 走到脑子判定才能判 planning——但 stub 模式下脑子对任何输入都必然解析失败
# （StubLLMClient.chat 恒返 intent 形状 JSON，RouteJudgment 校验必失败），会落
# 到新的保守地板（无方案 → chitchat 引导），导致这批"测规划链降级阶梯"的用例
# 连 intent 节点都进不去。这批用例测的是 planner/assemble/critic/narrate/intent/
# search worker 的异常降级，不是路由本身，故 monkeypatch 钉住脑子恒返 planning
# （ADR-0011 前置核实②-B 类：垫桩而非改期望值；E-2-c 更新：垫桩对象从
# `classify_input`/RouterDecision 换成 `classify_turn`/RouteJudgment）。
# ============================================================


@pytest.fixture(autouse=True)
def _pin_router_to_planning(monkeypatch):
    from agent.routing.brain import RouteJudgment

    def _always_planning(*args, **kwargs):
        return RouteJudgment(
            label="planning",
            confidence=0.9,
            reply_text="正在为你规划下午行程……",
            tone="warm",
            cta_chips=[],
            rationale="test_d2_failure_drain 垫桩：钉住 planning，测降级阶梯不测路由",
        )

    monkeypatch.setattr(router_mod, "classify_turn", _always_planning)


# ============================================================
# Helpers
# ============================================================


def _drive(*, user_input: str, session_id: str, planner_mode=None) -> list:
    """驱动真实编译图跑一次 run_graph_stream，收集所有 SseEvent。"""

    async def _run() -> list:
        evs: list = []
        async for ev in sse.run_graph_stream(
            user_input=user_input,
            session_id=session_id,
            user_id="demo_user",
            planner_mode=planner_mode,
        ):
            evs.append(ev)
        return evs

    return asyncio.run(_run())


def _types(evs: list) -> list[str]:
    return [e.type.value for e in evs]


def _valid_blueprint(*args, **kwargs) -> PlanBlueprint:
    """合法蓝图（P040 亲子博物馆 + R001 轻食），让 assemble/critic 能真正跑到核心函数。

    ids 取自 test_assemble_blueprint.py 的 A1 标准两段场景（mock 数据已覆盖）。
    """
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
        rationale="D2 test blueprint",
    )


def _stub_intent() -> IntentExtraction:
    """可被 plan_itinerary 规划的极简意图（rule 地板恢复用）。"""
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5.0,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="带娃下午出去玩",
        parse_confidence=0.9,
    )


_USER_INPUT = "今天下午想带孩子出去玩"


def _boom(*args, **kwargs):
    raise RuntimeError("d2-unexpected-boom")


# ============================================================
# 1) planner 非预期异常 → 规则地板
# ============================================================


def test_planner_unexpected_exception_drains_to_rule_floor(monkeypatch):
    """planner 里 generate_blueprint 抛 RuntimeError（非 BlueprintGenError）→
    降级到规则地板方案，流出 ITINERARY_READY，绝不 STREAM_ERROR。"""
    import agent.graph.nodes.planner as planner_mod

    monkeypatch.setattr(planner_mod, "generate_blueprint", _boom)

    evs = _drive(user_input=_USER_INPUT, session_id="d2_planner_drain")
    t = _types(evs)

    assert "stream_error" not in t, f"planner 异常不该裸 STREAM_ERROR，events={t}"
    assert "itinerary_ready" in t, f"应降级出规则地板方案，events={t}"


# ============================================================
# 2) assemble 非预期异常 → 规则地板
# ============================================================


def test_assemble_exception_drains_to_rule_floor(monkeypatch):
    """assemble 核心 assemble_from_blueprint 抛 → 降级规则地板，出 ITINERARY_READY。"""
    import agent.graph.nodes.assemble as assemble_mod
    import agent.graph.nodes.planner as planner_mod

    # 先让 planner 出一个合法蓝图，assemble 才会真正调 assemble_from_blueprint
    monkeypatch.setattr(planner_mod, "generate_blueprint", _valid_blueprint)
    monkeypatch.setattr(assemble_mod, "assemble_from_blueprint", _boom)

    evs = _drive(user_input=_USER_INPUT, session_id="d2_assemble_drain")
    t = _types(evs)

    assert "stream_error" not in t, f"assemble 异常不该裸 STREAM_ERROR，events={t}"
    assert "itinerary_ready" in t, f"应降级出规则地板方案，events={t}"


# ============================================================
# 3) critic 非预期异常 → 规则地板
# ============================================================


def test_critic_exception_drains_to_rule_floor(monkeypatch):
    """critic 核心 validate_itinerary 抛 → 降级规则地板，出 ITINERARY_READY。"""
    import agent.graph.nodes.critic as critic_mod
    import agent.graph.nodes.planner as planner_mod

    # 合法蓝图 → assemble 出真 itinerary → critic 才会真正调 validate_itinerary
    monkeypatch.setattr(planner_mod, "generate_blueprint", _valid_blueprint)
    monkeypatch.setattr(critic_mod, "validate_itinerary", _boom)

    evs = _drive(user_input=_USER_INPUT, session_id="d2_critic_drain")
    t = _types(evs)

    assert "stream_error" not in t, f"critic 异常不该裸 STREAM_ERROR，events={t}"
    assert "itinerary_ready" in t, f"应降级出规则地板方案，events={t}"


# ============================================================
# 4) narrate 非预期异常 → 推方案、跳文案
# ============================================================


def test_narrate_exception_emits_plan_without_prose(monkeypatch):
    """narrate 的文案/标题生成抛 → 仍把 critic 已通过的方案推出去（ITINERARY_READY），
    narration 为空/None（不推 AGENT_NARRATION），绝不 STREAM_ERROR。"""
    import agent.graph.nodes.narrate as narrate_mod

    monkeypatch.setattr(narrate_mod, "generate_title_and_narration", _boom)

    # rule 模式：planner 直接出规则 itinerary → critic 放行 → narrate（最干净的 narrate 入口）
    evs = _drive(
        user_input=_USER_INPUT, session_id="d2_narrate_drain", planner_mode="rule"
    )
    t = _types(evs)

    assert "stream_error" not in t, f"narrate 异常不该裸 STREAM_ERROR，events={t}"
    assert "itinerary_ready" in t, f"critic 已通过的方案应照样推出，events={t}"
    # 文案被跳过：没有 AGENT_NARRATION
    assert "agent_narration" not in t, f"narration 应被跳过，events={t}"


# ============================================================
# 5) intent 非预期异常 → 兜底意图继续
# ============================================================


def test_intent_unexpected_exception_uses_fallback_intent(monkeypatch):
    """parse_intent 抛【非】IntentParseError（RuntimeError）→ 用兜底意图继续这一轮，
    带 quality_issue，绝不 STREAM_ERROR。"""
    import agent.graph.nodes.intent as intent_mod

    monkeypatch.setattr(intent_mod, "parse_intent", _boom)

    evs = _drive(
        user_input=_USER_INPUT, session_id="d2_intent_drain", planner_mode="rule"
    )
    t = _types(evs)

    assert "stream_error" not in t, f"intent 非预期异常不该裸 STREAM_ERROR，events={t}"

    # intent_parsed 的 payload 带兜底意图签名（parse_confidence=0.3 / ambiguous_fields=["all"]）
    parsed = [e for e in evs if e.type.value == "intent_parsed"]
    assert parsed, f"应仍推 intent_parsed（兜底意图），events={t}"
    payload = parsed[0].payload
    assert payload.get("parse_confidence") == 0.3, payload
    assert payload.get("ambiguous_fields") == ["all"], payload

    # 这一轮继续走完出方案
    assert "itinerary_ready" in t, f"兜底意图应继续出方案，events={t}"

    # 直接核验 intent_node：非预期异常路径也写了 quality_issue（诚实告知）
    out = intent_mod.intent_node({"user_input": _USER_INPUT, "user_id": "demo_user"})
    assert out.get("quality_issues"), "兜底意图应写 quality_issue 让 narrator 诚实告知"


# ============================================================
# 6) search worker 非预期异常 → 空结果继续
# ============================================================


def test_search_worker_exception_returns_empty(monkeypatch):
    """搜索 worker 调的工具抛 → worker 降级为空候选，规划继续，绝不 STREAM_ERROR。"""
    import agent.graph.nodes.execute as execute_mod

    monkeypatch.setattr(execute_mod, "search_pois_for_intent", _boom)

    evs = _drive(user_input=_USER_INPUT, session_id="d2_search_drain")
    t = _types(evs)

    assert "stream_error" not in t, f"worker 异常不该裸 STREAM_ERROR，events={t}"

    # worker 降级为空：search_pois 的 tool_call_end count=0
    pois_end = [
        e
        for e in evs
        if e.type.value == "tool_call_end" and e.payload.get("tool") == "search_pois"
    ]
    assert pois_end, f"应仍合成 search_pois 的 tool_call_end，events={t}"
    assert pois_end[0].payload.get("output", {}).get("count") == 0, (
        f"worker 应降级为空候选，payload={pois_end[0].payload}"
    )

    # 规划继续走完（rule 兜底加载自己的 mock 数据，不依赖 state.pois）
    assert "done" in t
    assert "itinerary_ready" in t, f"规划应继续到出方案，events={t}"


# ============================================================
# 7) 连规则地板都失败 → 诚实 STREAM_ERROR（不吞、不死循环）
# ============================================================


def test_rule_floor_also_fails_then_honest_error(monkeypatch):
    """planner 路径 + 规则地板 plan_itinerary 都抛 → 落到诚实 STREAM_ERROR，
    且不无限循环 / 不挂起（证明地板也失败时我们不吞）。"""
    import agent.graph.nodes.planner as planner_mod
    import agent.planning.planners.rule_planner as rule_mod

    monkeypatch.setattr(planner_mod, "generate_blueprint", _boom)

    floor_calls = {"n": 0}

    def _floor_boom(*args, **kwargs):
        floor_calls["n"] += 1
        raise RuntimeError("d2-rule-floor-boom")

    monkeypatch.setattr(rule_mod, "plan_itinerary", _floor_boom)

    evs = _drive(user_input=_USER_INPUT, session_id="d2_floor_fails")
    t = _types(evs)

    # 规则地板确实被尝试过（证明降级走到了地板，而非别处提前崩）
    assert floor_calls["n"] >= 1, "规则地板必须被尝试过"
    # 地板也失败 → 诚实 STREAM_ERROR，绝不伪造方案
    assert "stream_error" in t, f"地板也失败应落 STREAM_ERROR，events={t}"
    assert "itinerary_ready" not in t, f"地板失败不该伪造方案，events={t}"


# ============================================================
# 8) LangGraph 控制流异常必须 re-raise（不转规则地板）
# ============================================================


def test_graph_control_exception_not_swallowed(monkeypatch):
    """decorator 必须原样 re-raise LangGraph 控制流异常（GraphBubbleUp 及子类
    GraphInterrupt），绝不当普通错误转规则地板——否则破坏 HITL/interrupt 控制流。"""
    from agent.graph._resilience import drain_on_error
    from langgraph.errors import GraphBubbleUp, GraphInterrupt
    import agent.planning.planners.rule_planner as rule_mod

    # spy 规则地板：控制流异常路径下绝不应被调用
    floor_calls = {"n": 0}

    def _spy(*args, **kwargs):
        floor_calls["n"] += 1
        raise AssertionError("控制流异常下不应触发规则地板恢复")

    monkeypatch.setattr(rule_mod, "plan_itinerary", _spy)

    def raise_interrupt(state):
        raise GraphInterrupt(())

    wrapped = drain_on_error(raise_interrupt, "rule_floor")
    with pytest.raises(GraphInterrupt):
        wrapped({"intent": None})

    def raise_bubble(state):
        raise GraphBubbleUp()

    wrapped_bubble = drain_on_error(raise_bubble, "rule_floor")
    with pytest.raises(GraphBubbleUp):
        wrapped_bubble({"intent": None})

    assert floor_calls["n"] == 0, "控制流异常下绝不该触发规则地板恢复"

    # 对照：普通 RuntimeError 仍降级（emit_plan 策略返回恢复 delta，不抛）
    def raise_runtime(state):
        raise RuntimeError("ordinary-error")

    wrapped_runtime = drain_on_error(raise_runtime, "emit_plan")
    out = wrapped_runtime({"itinerary": "EXISTING"})
    assert out == {"itinerary": "EXISTING", "narration": None}


# ============================================================
# 9) 原始异常仍 loudly 落日志（降级但不静默）
# ============================================================


def test_original_exception_is_logged(caplog):
    """节点被降级时，原始异常的完整 traceback 仍被 loudly 记录（degrade, don't go silent）。"""
    from agent.graph._resilience import drain_on_error

    intent = _stub_intent()

    def boom(state):
        raise RuntimeError("d2-boom-marker-xyz")

    wrapped = drain_on_error(boom, "rule_floor")

    with caplog.at_level(logging.ERROR):
        result = wrapped({"intent": intent})

    # 降级成功（规则地板出了方案）
    assert result.get("planner_mode") == "rule"
    assert result.get("itinerary") is not None

    # 原始异常 + traceback 都进了日志
    assert "d2-boom-marker-xyz" in caplog.text, caplog.text
    assert "RuntimeError" in caplog.text, caplog.text
