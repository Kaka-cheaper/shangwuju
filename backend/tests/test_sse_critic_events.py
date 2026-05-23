"""tests.test_sse_critic_events —— Step 2：critic 闭环 SSE 事件 schema。

覆盖：
1. SseEventType 含 CRITIC_VIOLATIONS / CRITIC_FIX_ATTEMPT / PLAN_FALLBACK 三个新枚举
2. 三类事件的 payload 结构能被 SseEvent 正常打包
3. validate_violations 转 dict 后能正确序列化（critics_v2.Violation → SSE payload）

不跑真 LangGraph，仅做 schema-level 单测。
"""

from __future__ import annotations

from agent.planning.critic.critics_v2 import Severity, Violation, ViolationCode
from schemas.sse import SseEvent, SseEventType


def test_new_event_types_in_enum():
    """新增 3 个事件枚举值。"""
    assert SseEventType.CRITIC_VIOLATIONS.value == "critic_violations"
    assert SseEventType.CRITIC_FIX_ATTEMPT.value == "critic_fix_attempt"
    assert SseEventType.PLAN_FALLBACK.value == "plan_fallback"


def test_critic_violations_event_payload():
    """SseEvent(CRITIC_VIOLATIONS, ...) 能正常构造（edge_v1：HOP_INFEASIBLE 替代 COMMUTE_INFEASIBLE）。"""
    v = Violation(
        code=ViolationCode.HOP_INFEASIBLE,
        severity=Severity.CRITICAL,
        message="hop 时间不足以走完通勤",
        field_path="hops[0].minutes",
    )
    ev = SseEvent(
        type=SseEventType.CRITIC_VIOLATIONS,
        seq=10,
        payload={"violations": [v.model_dump()], "fix_attempt": 2},
    )
    dumped = ev.model_dump()
    assert dumped["type"] == "critic_violations"
    assert dumped["payload"]["fix_attempt"] == 2
    assert len(dumped["payload"]["violations"]) == 1
    assert dumped["payload"]["violations"][0]["code"] == "hop_infeasible"
    assert dumped["payload"]["violations"][0]["severity"] == "critical"


def test_critic_fix_attempt_event_payload():
    ev = SseEvent(
        type=SseEventType.CRITIC_FIX_ATTEMPT,
        seq=11,
        payload={"attempt": 2, "feedback_text": "缩短主活动 30 分钟"},
    )
    dumped = ev.model_dump()
    assert dumped["type"] == "critic_fix_attempt"
    assert dumped["payload"]["attempt"] == 2
    assert "缩短" in dumped["payload"]["feedback_text"]


def test_plan_fallback_event_payload():
    """4 级 fallback 链每跳一级推一条。"""
    for src, dst, reason in [
        ("llm_first", "llm_backprompt", "critic 命中违规，让 LLM 修正重出"),
        ("llm_first", "ils", "LLM 三次失败，切 ILS 算法兜底"),
        ("ils", "rule", "ILS 未给出有效方案，回 rule 兜底"),
    ]:
        ev = SseEvent(
            type=SseEventType.PLAN_FALLBACK,
            seq=12,
            payload={"from": src, "to": dst, "reason": reason},
        )
        dumped = ev.model_dump()
        assert dumped["type"] == "plan_fallback"
        assert dumped["payload"]["from"] == src
        assert dumped["payload"]["to"] == dst


def test_violation_dump_shape_for_sse_payload():
    """critics_v2.Violation 转 dict 后字段名稳定（前端依赖此契约）。

    spec planning-quality-deep-review R4 加 expected_range 字段（Optional），
    前端 ToolTracePanel 现在可读这个字段把"建议范围"渲染到违规卡片上。
    """
    v = Violation(
        code=ViolationCode.DURATION_OUT_OF_RANGE,
        severity=Severity.CRITICAL,
        message="行程总时长 360 分钟超过用户上限",
        field_path="total_minutes",
    )
    dumped = v.model_dump()
    # 前端 ToolTracePanel 渲染依赖以下字段（spec R4 后增加 expected_range）
    assert set(dumped.keys()) == {"code", "severity", "message", "field_path", "expected_range"}
    assert dumped["code"] == "duration_out_of_range"
    assert dumped["severity"] == "critical"
    assert dumped["expected_range"] is None  # 默认 None，未填时不渲染
