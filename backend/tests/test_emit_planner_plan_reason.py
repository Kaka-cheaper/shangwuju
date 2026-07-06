"""tests.test_emit_planner_plan_reason —— 信任带 §四③ plan_reason 流前端路径回归。

选定路径（见任务交付说明）：复用**已有** `emit_planner` 产出的蓝图 AGENT_THOUGHT
事件，加一个可选兄弟字段 `plan_reason`（PlanBlueprint.plan_reason 非空时才挂），
不新造 SSE 事件类型——同 `emit_critic`/`emit_narrate` 一贯的"无内容不加字段"纪律
（见 `agent/graph/_emit_handlers.py` 模块内其它 emit_xxx 函数的同类先例）。

覆盖：
1. blueprint.plan_reason 非空 → AGENT_THOUGHT payload 携带 plan_reason 兄弟字段。
2. blueprint.plan_reason 为空串（stub / 未跑到 LLM 蓝图路径）→ 不挂这个键
   （前端信任带③拍据此静默跳过，不渲染空句子）。
3. text 字段本身不受影响（仍是既有的"蓝图 N 个节点：rationale[:80]"文案，
   plan_reason 是新增兄弟字段，不是替换）。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.graph._emit_context import EmitContext
from agent.graph._emit_handlers import emit_planner
from schemas.sse import SseEventType


@dataclass
class _FakeWeights:
    comfort: float = 0.3
    time: float = 0.3
    cost: float = 0.2
    smoothness: float = 0.2

    def summary(self) -> str:
        return "重舒适 0.3"


@dataclass
class _FakeNode:
    target_id: str = "P001"


@dataclass
class _FakeBlueprint:
    nodes: list
    rationale: str = ""
    plan_reason: str = ""


def test_emit_planner_attaches_plan_reason_when_present():
    ctx = EmitContext()
    diff = {
        "weights": _FakeWeights(),
        "blueprint": _FakeBlueprint(
            nodes=[_FakeNode()],
            rationale="经典下午局：先逛后吃",
            plan_reason="用户同行年轻人多，所以先用 KTV 带动气氛",
        ),
        "plan_attempt": 1,
    }

    events = emit_planner(ctx, diff)

    thought_events = [e for e in events if e.type == SseEventType.AGENT_THOUGHT]
    # 两条 AGENT_THOUGHT：weights 一条 + blueprint 一条（第二条才带 plan_reason）
    blueprint_thought = thought_events[-1]
    assert blueprint_thought.payload["plan_reason"] == "用户同行年轻人多，所以先用 KTV 带动气氛"
    # text 字段不受影响，仍是既有文案（plan_reason 是新增兄弟字段，不是替换）
    assert "蓝图 1 个节点" in blueprint_thought.payload["text"]
    assert "经典下午局" in blueprint_thought.payload["text"]


def test_emit_planner_omits_plan_reason_key_when_empty():
    """空串 plan_reason（stub / ILS / rule 兜底路径未经此 LLM 调用）→ 不挂键。"""
    ctx = EmitContext()
    diff = {
        "weights": _FakeWeights(),
        "blueprint": _FakeBlueprint(nodes=[_FakeNode()], rationale="ok", plan_reason=""),
        "plan_attempt": 1,
    }

    events = emit_planner(ctx, diff)

    thought_events = [e for e in events if e.type == SseEventType.AGENT_THOUGHT]
    blueprint_thought = thought_events[-1]
    assert "plan_reason" not in blueprint_thought.payload
