"""emit_critic severity 字面值回归测试。

根因（真 LLM 冒烟发现"评委看板空白"）：ADR-0008 B-1 把 Severity 枚举从
CRITICAL/WARNING 改名为 HARD/SOFT，序列化后 violation dict 的 "severity"
字段字面值是 "hard"（不是 "critical"）；agent/graph/_emit_handlers.py 的
emit_critic 改名时漏改，仍用 `d.get("severity") == "critical"` 过滤，导致
`critical_only` 恒为空列表 —— CRITIC_VIOLATIONS 事件的 payload 永远是
`{"violations": [], ...}`，前端评委看板永远拿不到违规卡片。

本测试构造一条 HARD 违规，断言 emit_critic 产出的 CRITIC_VIOLATIONS
payload 非空（覆盖修复点），并断言 REPLAN_TRIGGERED 的 violations 摘要
也非空（同一 bug 会连带影响这条）。
"""

from __future__ import annotations

from agent.graph._emit_context import EmitContext
from agent.graph._emit_handlers import emit_critic
from agent.planning.critic._rules.types import Severity, Violation, ViolationCode
from schemas.sse import SseEventType


def _hard_violation() -> Violation:
    return Violation(
        code=ViolationCode.DISTANCE_EXCEEDED,
        severity=Severity.HARD,
        message="第 2 段通勤距离超限，建议收紧到 5km 内",
        field_path="hops[1]",
    )


def test_emit_critic_hard_violation_produces_nonempty_payload():
    """HARD 违规 → CRITIC_VIOLATIONS.violations 非空（回归：曾恒为空）。"""
    ctx = EmitContext()
    diff = {
        "has_critical": True,
        "violations": [_hard_violation()],
        "plan_attempt": 2,
    }

    events = emit_critic(ctx, diff)

    critic_events = [e for e in events if e.type == SseEventType.CRITIC_VIOLATIONS]
    assert len(critic_events) == 1
    payload = critic_events[0].payload
    assert payload["violations"] != []
    assert payload["violations"][0]["severity"] == "hard"
    assert payload["violations"][0]["code"] == ViolationCode.DISTANCE_EXCEEDED.value
    assert payload["fix_attempt"] == 2

    replan_events = [e for e in events if e.type == SseEventType.REPLAN_TRIGGERED]
    assert len(replan_events) == 1
    assert replan_events[0].payload["violations"] != []


def test_emit_critic_soft_only_is_filtered_out_of_critic_violations():
    """SOFT-only 违规不应出现在 CRITIC_VIOLATIONS（该事件仅推 hard，避免噪声）；

    has_critical=False 时 emit_critic 走"验证通过"分支，不产 CRITIC_VIOLATIONS。
    """
    ctx = EmitContext()
    diff = {
        "has_critical": False,
        "violations": [
            Violation(
                code=ViolationCode.MEAL_TIME_UNREASONABLE,
                severity=Severity.SOFT,
                message="用餐时间稍早，建议延后",
            )
        ],
    }

    events = emit_critic(ctx, diff)

    assert all(e.type != SseEventType.CRITIC_VIOLATIONS for e in events)
    thought_events = [e for e in events if e.type == SseEventType.AGENT_THOUGHT]
    assert len(thought_events) == 1
    assert "1 条提示" in thought_events[0].payload["text"]
