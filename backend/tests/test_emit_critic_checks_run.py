"""tests.test_emit_critic_checks_run —— 信任带⑦拍质检收据后端数据源回归。

覆盖（见 路演PPT/信任带设计终稿.md 2026-07-11 修订「五收据」质检行 +
`agent.graph._emit_handlers.emit_critic` docstring）：

1. critic 通过（has_critical=False）→ AGENT_THOUGHT payload 携带
   "checks_run" 字段，值等于 `agent.planning.critic.validate.REGISTRY` 的
   真实长度（不是硬编码常量——REGISTRY 增删 check 时这个数字应自动跟着变，
   本测试用 `len(REGISTRY)` 而非字面数字断言，防止未来 REGISTRY 变化时
   测试本身变成新的"写死数字"）。
2. critic 未通过（has_critical=True）→ 不产生这个字段（该分支走的是另一条
   CRITIC_VIOLATIONS payload，不是这条 AGENT_THOUGHT）。
"""

from __future__ import annotations

from agent.graph._emit_context import EmitContext
from agent.graph._emit_handlers import emit_critic
from agent.planning.critic._rules.types import Severity, Violation, ViolationCode
from agent.planning.critic.validate import REGISTRY
from schemas.sse import SseEventType


def test_critic_passed_emits_checks_run_from_registry_length():
    ctx = EmitContext()
    diff = {"has_critical": False, "violations": []}

    events = emit_critic(ctx, diff)

    thought_events = [e for e in events if e.type == SseEventType.AGENT_THOUGHT]
    assert len(thought_events) == 1
    assert thought_events[0].payload["checks_run"] == len(REGISTRY)


def test_critic_passed_with_soft_violations_still_emits_checks_run():
    ctx = EmitContext()
    diff = {
        "has_critical": False,
        "violations": [
            Violation(
                code=ViolationCode.DISTANCE_EXCEEDED,
                severity=Severity.SOFT,
                message="距离稍远，建议收紧",
            )
        ],
    }

    events = emit_critic(ctx, diff)

    thought_events = [e for e in events if e.type == SseEventType.AGENT_THOUGHT]
    assert thought_events[0].payload["checks_run"] == len(REGISTRY)
    assert "1 条提示" in thought_events[0].payload["text"]


def test_critic_failed_branch_has_no_checks_run_field():
    """has_critical=True 分支走 CRITIC_VIOLATIONS，不是这条 AGENT_THOUGHT——
    不该出现 checks_run 字段（那个分支完全不产 AGENT_THOUGHT）。"""
    ctx = EmitContext()
    diff = {
        "has_critical": True,
        "violations": [
            Violation(
                code=ViolationCode.DISTANCE_EXCEEDED,
                severity=Severity.HARD,
                message="距离超限",
            )
        ],
    }

    events = emit_critic(ctx, diff)

    assert all(e.type != SseEventType.AGENT_THOUGHT for e in events)
