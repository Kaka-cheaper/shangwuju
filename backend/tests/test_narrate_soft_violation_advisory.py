"""narrate 节点：主 LLM 蓝图路径 SOFT 违规上告知面。

【背景（ADR-0010 决策 11「绝不默默忽略」+ D-7 advisory 通道遗留缺口）】

D-7（commit 6c3c65d）把 advisory 通道从 planner 贯通到 SSE，但只在 ILS 兜底路径
（`ils_planner.plan_hybrid` 成功时）兑现：`ils_replan_node` 把 `HybridResult.
advisories` 写进 `state.advisories`，`narrate_node` 读它拼进文案。

主 LLM 蓝图路径（`planner_node` → `assemble_node` → `critic_node` → `narrate_node`，
critic 放行、无 HARD 违规）此前从不产 advisory——SOFT 违规（如 `check_duration`
判定的"时长不足"）只写进 `state.violations` 供 trace / SSE `critic_violations`
展示，narrate 从不读它，"方案比你要的短了些"这句话在主路径永远说不出口。

本文件验证 `agent/graph/nodes/narrate.py` 新增的转换：
`_extract_soft_violation_advisories` 把 `state.violations` 里 severity==SOFT 的
条目转成 advisory dict，与既有 `state.advisories`（ILS 路径产源）经
`_merge_advisories` 按 message 去重合并，再走 D-7 已建好的下游管道（narration
文案拼接 + `emit_narrate` 透传给 SSE `AGENT_NARRATION.payload.messages`）。

测试分两层：
1. 直调 `narrate_node` 喂手工构造的 state（覆盖：主映射码 SHORTER_THAN_REQUESTED、
   未映射码原样透传、HARD 违规不转、与既有 advisories 同 message 去重）。
2. 图级测试（复用 `test_d2_failure_drain.py` / `test_d7_advisory_channel.py` 的
   monkeypatch 手法）：驱动真实编译图跑主 LLM 蓝图路径，critic 侧 monkeypatch
   `validate_itinerary` 恒产一条 SOFT 违规且不触发 HARD，断言 AGENT_NARRATION
   payload 含转换出的 advisory 条目、narration 文案含告知语。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

# ============================================================
# agent 命名空间桥接（与 test_d2_failure_drain.py / test_d7_advisory_channel.py 同款）
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.graph import sse_adapter as sse  # noqa: E402
from agent.graph.nodes.narrate import narrate_node  # noqa: E402
from agent.planning.blueprint.blueprint import (  # noqa: E402
    BlueprintNode,
    BlueprintTargetKind,
    PlanBlueprint,
)
from agent.planning.critic.critics_v2 import (  # noqa: E402
    Severity,
    Violation,
    ViolationCode,
)
from schemas.advisory import AdvisoryCode  # noqa: E402


# ============================================================
# 共用素材
# ============================================================

_SOFT_DURATION_MESSAGE = (
    "行程总时长 200 分钟（约 3.3h）比你期望的 3-5h 短了一些——"
    "附近符合条件的候选比较有限，先按这个方案呈现给你；"
    "如果想延长，可以放宽筛选范围或告诉我想加什么活动。"
)
_SOFT_DISTANCE_MESSAGE = "第 1 站（xx 博物馆）距家 6.2km，超过用户期望 5.0km。如条件允许请换距离更近的候选。"
_SOFT_SOCIAL_MESSAGE = "第 2 站（xx 餐厅）调性偏差：亲子场景不太合适（仍可接受，但更优候选可考虑换）。"
_HARD_MESSAGE = "行程总时长 400 分钟（约 6.7h）超出用户期望的 3-5h（含 ±30min 容差）。请压缩节点停留。"


def _build_state(*, violations=None, advisories=None):
    """构造 narrate_node 所需的最小合法 state（真实 intent + 真实 itinerary，来自
    rule_planner 的确定性产出——与 test_planner_mode_dispatch.py 的
    test_narrate_node_rule_mode_uses_template_not_llm 同一手法）。"""
    from agent.core.llm_client_stub import StubLLMClient
    from agent.intent.parser import parse_intent
    from agent.planning.planners.rule_planner import plan_itinerary

    client = StubLLMClient()
    intent = parse_intent("今天下午想出去玩", client=client)
    plan_result = plan_itinerary(intent)
    assert plan_result.success and plan_result.itinerary is not None

    return {
        "intent": intent,
        "itinerary": plan_result.itinerary,
        "user_id": "demo_user",
        "violations": violations or [],
        "advisories": advisories or [],
    }


# ============================================================
# 1) 直调 narrate_node：主映射码
# ============================================================


def test_soft_duration_violation_becomes_shorter_than_requested_advisory():
    """SOFT + DURATION_OUT_OF_RANGE → advisory code 复用 D-7 既有映射
    AdvisoryCode.SHORTER_THAN_REQUESTED（与 ils_planner._build_success_advisories
    同一条映射），message 原样复用 Violation.message（不重写文案）。"""
    state = _build_state(
        violations=[
            Violation(
                code=ViolationCode.DURATION_OUT_OF_RANGE,
                severity=Severity.SOFT,
                message=_SOFT_DURATION_MESSAGE,
                field_path="total_minutes",
            )
        ],
    )

    result = narrate_node(state)

    advisories = result.get("advisories") or []
    assert advisories == [
        {"code": AdvisoryCode.SHORTER_THAN_REQUESTED.value, "message": _SOFT_DURATION_MESSAGE}
    ], advisories

    narration = result.get("narration") or ""
    assert _SOFT_DURATION_MESSAGE in narration, narration
    assert "说明一下" in narration, narration


# ============================================================
# 2) 直调 narrate_node：无对应 AdvisoryCode 的违规码原样透传
# ============================================================


def test_soft_distance_and_social_violations_pass_through_raw_violation_code():
    """DISTANCE_EXCEEDED / SOCIAL_CONTEXT_MISMATCH（POOR 档）没有对应
    AdvisoryCode（schemas/advisory.py 的 5 码未覆盖这两个 critic 维度）——
    直接沿用 ViolationCode 字符串值当 code（判断点：见 narrate.py
    _SOFT_VIOLATION_CODE_TO_ADVISORY_CODE 上方注释，emit_narrate 对 code
    只透传不做枚举校验，侵入最小）。"""
    state = _build_state(
        violations=[
            Violation(
                code=ViolationCode.DISTANCE_EXCEEDED,
                severity=Severity.SOFT,
                message=_SOFT_DISTANCE_MESSAGE,
                field_path="nodes[0].target_id",
            ),
            Violation(
                code=ViolationCode.SOCIAL_CONTEXT_MISMATCH,
                severity=Severity.SOFT,
                message=_SOFT_SOCIAL_MESSAGE,
                field_path="nodes[1].target_id",
            ),
        ],
    )

    result = narrate_node(state)

    advisories = result.get("advisories") or []
    codes = [a["code"] for a in advisories]
    messages = [a["message"] for a in advisories]
    assert codes == [
        ViolationCode.DISTANCE_EXCEEDED.value,
        ViolationCode.SOCIAL_CONTEXT_MISMATCH.value,
    ], codes
    assert messages == [_SOFT_DISTANCE_MESSAGE, _SOFT_SOCIAL_MESSAGE]

    narration = result.get("narration") or ""
    assert _SOFT_DISTANCE_MESSAGE in narration
    assert _SOFT_SOCIAL_MESSAGE in narration


# ============================================================
# 3) 直调 narrate_node：HARD 违规不转 advisory
# ============================================================


def test_hard_violation_not_converted_to_advisory():
    """HARD 违规是「方案缺陷须修」（Severity 分轴，见 schemas/advisory.py
    docstring），不属于「限制/建议告知」——即便（防御性场景）它和 SOFT 违规
    混在同一份 state.violations 里，也只转 SOFT 那条。"""
    state = _build_state(
        violations=[
            Violation(
                code=ViolationCode.DURATION_OUT_OF_RANGE,
                severity=Severity.HARD,
                message=_HARD_MESSAGE,
                field_path="total_minutes",
            ),
            Violation(
                code=ViolationCode.DISTANCE_EXCEEDED,
                severity=Severity.SOFT,
                message=_SOFT_DISTANCE_MESSAGE,
                field_path="nodes[0].target_id",
            ),
        ],
    )

    result = narrate_node(state)

    advisories = result.get("advisories") or []
    messages = [a["message"] for a in advisories]
    assert _HARD_MESSAGE not in messages, messages
    assert _SOFT_DISTANCE_MESSAGE in messages, messages
    assert len(advisories) == 1, advisories


# ============================================================
# 4) 直调 narrate_node：与既有 state.advisories 同 message 去重
# ============================================================


def test_same_message_from_ils_advisory_and_violation_conversion_not_duplicated():
    """ILS 路径的 SHORTER_THAN_REQUESTED advisory 与主路径 critic 的
    DURATION_OUT_OF_RANGE(SOFT) 违规都复用同一句 check_duration message
    （ils_planner._build_success_advisories 原文："复用 check_duration 已经
    写好的用户向文案"）。若两路都在 state 里出现（本测试人为构造，覆盖
    "两路都有时必须只说一遍"这条验收要求），narrate 合并后只应保留一份，
    narration 文案里这句话也只出现一次。"""
    existing_advisory = {
        "code": AdvisoryCode.SHORTER_THAN_REQUESTED.value,
        "message": _SOFT_DURATION_MESSAGE,
    }
    state = _build_state(
        violations=[
            Violation(
                code=ViolationCode.DURATION_OUT_OF_RANGE,
                severity=Severity.SOFT,
                message=_SOFT_DURATION_MESSAGE,
                field_path="total_minutes",
            )
        ],
        advisories=[existing_advisory],
    )

    result = narrate_node(state)

    advisories = result.get("advisories") or []
    assert advisories == [existing_advisory], advisories

    narration = result.get("narration") or ""
    assert narration.count(_SOFT_DURATION_MESSAGE) == 1, narration


# ============================================================
# 5) 图级测试：主 LLM 蓝图路径 SOFT 违规贯通到 AGENT_NARRATION
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


def _valid_blueprint(*args, **kwargs) -> PlanBlueprint:
    """合法蓝图（P040 亲子博物馆 + R001 轻食）——与 test_d2_failure_drain.py /
    test_d7_advisory_channel.py 同一组 mock id，让 assemble/critic 真正跑到
    核心函数（而非因候选/蓝图为空提前降级）。"""
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
        rationale="narrate soft-advisory graph test blueprint",
    )


def _fake_validate_soft_only(*args, **kwargs):
    """替身 validate_itinerary：恒产一条 SOFT 违规、无 HARD——critic_node
    据此算出 has_critical=False，route_after_critic 直接走 narrate（不进
    replan_router / ils_fallback），确保测的是主 LLM 蓝图路径而非 ILS 兜底。"""
    return [
        Violation(
            code=ViolationCode.DURATION_OUT_OF_RANGE,
            severity=Severity.SOFT,
            message=_SOFT_DURATION_MESSAGE,
            field_path="total_minutes",
        )
    ]


_USER_INPUT = "今天下午想带孩子出去玩"


def test_main_llm_path_soft_violation_reaches_agent_narration(monkeypatch):
    """主 LLM 蓝图路径（非 ILS）：critic 产 SOFT 违规且放行 → narration 文案
    含告知语，AGENT_NARRATION payload 的 messages 含转换出的 advisory 条目。"""
    import agent.graph.nodes.critic as critic_mod
    import agent.graph.nodes.planner as planner_mod

    monkeypatch.setattr(planner_mod, "generate_blueprint", _valid_blueprint)
    monkeypatch.setattr(critic_mod, "validate_itinerary", _fake_validate_soft_only)

    evs = _drive(user_input=_USER_INPUT, session_id="narrate_soft_advisory_main_llm")
    t = _types(evs)

    assert "stream_error" not in t, f"不该裸 STREAM_ERROR，events={t}"
    assert "itinerary_ready" in t, f"应正常出方案，events={t}"
    # 确认没有滑进 ILS 兜底（本测试要测的是主 LLM 蓝图路径）
    fallback_targets = [e.payload.get("to") for e in evs if e.type.value == "plan_fallback"]
    assert "ils" not in fallback_targets, f"不应经过 ils_fallback，plan_fallback 目标={fallback_targets}"

    narr = [e for e in evs if e.type.value == "agent_narration"]
    assert narr, f"应推 AGENT_NARRATION，events={t}"
    payload = narr[-1].payload
    text = payload.get("text", "")
    assert _SOFT_DURATION_MESSAGE in text, f"narration 文案应带出 SOFT 违规告知语，payload={payload}"

    messages = payload.get("messages") or []
    assert messages, f"payload 应含结构化 advisory 条目，payload={payload}"
    assert messages[0]["kind"] == "advisory"
    assert messages[0]["code"] == AdvisoryCode.SHORTER_THAN_REQUESTED.value
    assert messages[0]["text"] == _SOFT_DURATION_MESSAGE
