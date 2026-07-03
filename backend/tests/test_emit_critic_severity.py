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
    """HARD 违规 → CRITIC_VIOLATIONS.violations 非空（回归：曾恒为空）。

    真因修复批 item 4 有意识迁移：critic_node 的 diff 从不含 `plan_attempt`
    （那是 planner 节点专属字段，critic 只读不写），`emit_critic` 曾经直读
    `diff.get("plan_attempt")` 只是因为本测试人为把它塞进了 diff——生产环境
    的 critic 节点从来不会这样做，真实行为是 `fix_attempt` 恒为 1（看板 bug）。
    修复后 `emit_critic` 改读 `ctx.last_plan_attempt`（EmitContext 由 planner
    节点的 diff 累积而来，见 `test_emit_critic_fix_attempt_...` 组的端到端式
    验证），本测试的 fixture 同步改为通过 `ctx.last_plan_attempt` 注入，
    不再借道 `diff["plan_attempt"]`（那条路径已经死了，留着会掩盖真实 bug）。
    """
    ctx = EmitContext()
    ctx.last_plan_attempt = 2
    diff = {
        "has_critical": True,
        "violations": [_hard_violation()],
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


def test_emit_critic_fix_attempt_tracks_ctx_accumulated_plan_attempt_across_rounds():
    """fix_attempt 应跟着 `ctx.last_plan_attempt` 走、且跨轮递增（真因修复批
    item 4：两轮 backprompt 场景断言 fix_attempt 递增）。

    模拟 `sse_adapter.run_graph_stream` 主循环的真实节点分发顺序 + 累积时机
    （`_emit_context.EmitContext.update_accum_from_diff` 在每个节点的 emit
    函数跑完后才调用，见 sse_adapter.py 主循环）：
    planner(plan_attempt=1) → critic(命中违规，backprompt) →
    planner(plan_attempt=2，LLM 按反馈重出) → critic(再次命中违规)。

    第二轮 critic 的 `fix_attempt` 必须是 2，不是恒 1——这正是曾经的看板 bug
    （critic_node 自己的 diff 从不含 plan_attempt，旧实现直读 `diff.get(
    "plan_attempt")` 永远拿到 None → 恒 1）。
    """
    ctx = EmitContext()

    # 第一轮：planner 出方案（plan_attempt=1），critic 命中违规
    ctx.update_accum_from_diff({"plan_attempt": 1})
    events1 = emit_critic(
        ctx, {"has_critical": True, "violations": [_hard_violation()]}
    )
    payload1 = next(
        e.payload for e in events1 if e.type == SseEventType.CRITIC_VIOLATIONS
    )
    assert payload1["fix_attempt"] == 1

    # 第二轮：backprompt 后 planner 重出方案（plan_attempt=2），critic 再次命中
    ctx.update_accum_from_diff({"plan_attempt": 2})
    events2 = emit_critic(
        ctx, {"has_critical": True, "violations": [_hard_violation()]}
    )
    payload2 = next(
        e.payload for e in events2 if e.type == SseEventType.CRITIC_VIOLATIONS
    )
    assert payload2["fix_attempt"] == 2, (
        "两轮 backprompt 后 fix_attempt 应递增到 2（曾恒为 1 的看板 bug）"
    )


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
