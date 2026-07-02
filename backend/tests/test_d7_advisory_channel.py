"""D7 advisory channel —— planner「绝不默默忽略」的结构化告知贯通到 SSE。

ADR-0010 决策 11 + D-7：`ils_planner.plan_hybrid` 产出的 `advisories`
（`HybridResult.advisories`）要能从 `ils_replan_node` 一路走到
`AGENT_NARRATION` 的 SSE payload——narration 文案里含告知语，payload 里含
结构化条目（`{"kind": "advisory", "code": ..., "text": ...}`，ADR-0011
决策 5「统一 agent 消息面」形状）。

测试套路复用 `test_d2_failure_drain.py`：monkeypatch 生产函数强制走一条具体
路径，在 stub 模式下驱动真实编译图跑 `run_graph_stream`，断言推出的 SSE 事件。

本文件强制路径的手法：`planner_node.generate_blueprint` 恒返回 `None`
（blueprint 恒空 → assemble 恒产 `itinerary=None` → critic 恒 `has_critical=
True`）——`replan_router_node` 因此连续judge 到 `retry_count` 超过
`_MAX_LLM_RETRIES`（默认 2）自然切到 `ils_fallback` → `ils_replan_node`，
不需要另外 monkeypatch 重试阈值。到达 `ils_replan_node` 后 monkeypatch
`ils_planner.plan_hybrid`（`ils_replan_node` 内部 `from ... import plan_hybrid`
是函数体内的局部 import，每次调用都会重新取模块属性，monkeypatch 模块属性
对它生效）为一个带 advisory 的确定性 `HybridResult`，与真实 `plan_hybrid`
内部算法逻辑解耦——本文件只验证「wiring」（state → narrate → SSE），advisory
的产出条件已由 `test_planner_pinning_advisory.py` 覆盖。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

# ============================================================
# agent 命名空间桥接（与 test_d2_failure_drain.py 同款）
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.graph import sse_adapter as sse  # noqa: E402
from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint  # noqa: E402
from agent.planning.blueprint.blueprint import (  # noqa: E402
    BlueprintNode,
    BlueprintTargetKind,
    PlanBlueprint,
)
from data.loader import load_user_profile  # noqa: E402
from schemas.advisory import Advisory, AdvisoryCode  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402


# ============================================================
# Helpers
# ============================================================


def _drive(*, user_input: str, session_id: str) -> list:
    """驱动真实编译图跑一次 run_graph_stream，收集所有 SseEvent。"""

    async def _run() -> list:
        evs: list = []
        async for ev in sse.run_graph_stream(
            user_input=user_input,
            session_id=session_id,
            user_id="demo_user",
        ):
            evs.append(ev)
        return evs

    return asyncio.run(_run())


def _types(evs: list) -> list[str]:
    return [e.type.value for e in evs]


def _stub_intent() -> IntentExtraction:
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


def _blueprint_always_none(*args, **kwargs):
    """`planner_node.generate_blueprint` 的替身——恒返回 None，逼流程一路
    llm_backprompt 直到自然切到 ils_fallback（见模块 docstring）。"""
    return None


_OVER_BUDGET_MESSAGE = (
    "这次预估花费约 500 元，比你平时 300 元左右的预算高一些——"
    "不介意的话可以直接用，想省钱也可以告诉我砍掉哪一站。"
)


def _fake_hybrid_result():
    """构造一个 success=True、带一条 OVER_BUDGET advisory 的 `HybridResult`。

    itinerary 用真实 `assemble_from_blueprint`（P040 亲子博物馆 + R001 轻食，
    与 test_d2_failure_drain.py 的 `_valid_blueprint` 同一组 mock id）拼出，
    保证是一个 schema 合法、narrate_node 能正常处理的 Itinerary。
    """
    from agent.planning.planners.ils_planner import HybridResult

    blueprint = PlanBlueprint(
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
        rationale="D7 test blueprint",
    )
    itinerary = assemble_from_blueprint(_stub_intent(), blueprint, load_user_profile())
    advisories = [Advisory(code=AdvisoryCode.OVER_BUDGET, message=_OVER_BUDGET_MESSAGE)]
    return HybridResult(success=True, itinerary=itinerary, advisories=advisories)


_USER_INPUT = "今天下午想带孩子出去玩"


def test_ils_replan_advisory_reaches_agent_narration(monkeypatch):
    """走 ils_replan 路径且 plan_hybrid 产出 advisory 的场景：
    AGENT_NARRATION 的 text 含告知语，payload 含结构化 advisory 条目。
    """
    import agent.graph.nodes.planner as planner_mod
    import agent.planning.planners.ils_planner as ils_planner_mod

    monkeypatch.setattr(planner_mod, "generate_blueprint", _blueprint_always_none)
    monkeypatch.setattr(
        ils_planner_mod, "plan_hybrid", lambda *a, **k: _fake_hybrid_result()
    )

    evs = _drive(user_input=_USER_INPUT, session_id="d7_advisory_channel")
    t = _types(evs)

    assert "stream_error" not in t, f"不该裸 STREAM_ERROR，events={t}"
    assert "itinerary_ready" in t, f"ILS 兜底方案应正常推出，events={t}"
    # 确认真走了 ils_fallback（而非侥幸从别处拿到方案）
    fallback_targets = [
        e.payload.get("to") for e in evs if e.type.value == "plan_fallback"
    ]
    assert "ils" in fallback_targets, f"应经过 ils_fallback，plan_fallback 目标={fallback_targets}"

    narr = [e for e in evs if e.type.value == "agent_narration"]
    assert narr, f"应推 AGENT_NARRATION，events={t}"
    payload = narr[-1].payload
    text = payload.get("text", "")
    assert "预算" in text, f"narration 文案应带出 advisory 告知语，payload={payload}"

    messages = payload.get("messages") or []
    assert messages, f"payload 应含结构化 advisory 条目，payload={payload}"
    assert messages[0]["kind"] == "advisory"
    assert messages[0]["code"] == AdvisoryCode.OVER_BUDGET.value
    assert "预算" in messages[0]["text"]


def test_ils_replan_without_advisory_omits_messages_field(monkeypatch):
    """对照组：plan_hybrid 无 advisory 时，AGENT_NARRATION payload 不应出现
    空的 "messages" 字段（保持 payload 精简，向后兼容旧断言）。"""
    import agent.graph.nodes.planner as planner_mod
    import agent.planning.planners.ils_planner as ils_planner_mod
    from agent.planning.planners.ils_planner import HybridResult

    def _fake_no_advisory():
        blueprint = PlanBlueprint(
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
            rationale="D7 test blueprint (no advisory)",
        )
        itinerary = assemble_from_blueprint(
            _stub_intent(), blueprint, load_user_profile()
        )
        return HybridResult(success=True, itinerary=itinerary, advisories=[])

    monkeypatch.setattr(planner_mod, "generate_blueprint", _blueprint_always_none)
    monkeypatch.setattr(
        ils_planner_mod, "plan_hybrid", lambda *a, **k: _fake_no_advisory()
    )

    evs = _drive(user_input=_USER_INPUT, session_id="d7_advisory_channel_none")
    narr = [e for e in evs if e.type.value == "agent_narration"]
    assert narr, "应推 AGENT_NARRATION"
    assert "messages" not in narr[-1].payload, narr[-1].payload
