"""LangGraph → SSE 适配的可变状态容器（spec code-modularization-refactor H3）。

run_graph_stream 拆分前是一个 489 行的大函数，13 个 LangGraph 节点的事件转换全压在
一个 for-switch 里；拆分时每个节点 emit 函数需要共享：

- seq        ：单调递增事件序号（每 yield 一条递增）
- itinerary_emitted / chitchat_emitted ：去重 flag，避免重复推
- last_*     ：累积 DONE payload 6 字段总结
- final_itinerary ：流末尾兜底拿到的 Itinerary

把这些变量收进一个 dataclass 让每个 emit 函数显式拿走它，主函数只负责
分发 + yield from emit 函数返回的 list[SseEvent]。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from schemas.sse import SseEvent, SseEventType


def now_ms() -> int:
    return int(time.time() * 1000)


def make_event(
    seq: int, type_: SseEventType, payload: dict[str, Any] | None = None
) -> SseEvent:
    return SseEvent(
        type=type_,
        seq=seq,
        payload=payload or {},
        timestamp_ms=now_ms(),
    )


@dataclass
class EmitContext:
    """可变状态容器：emit_xxx 函数共享的累积变量。

    每个 emit_* 函数从这里读 seq、写回 seq+=N、可能 set flag，然后返回
    生成的 SseEvent 列表。
    """

    seq: int = 0
    start_ms: int = field(default_factory=now_ms)
    itinerary_emitted: bool = False
    chitchat_emitted: bool = False
    last_state: dict[str, Any] | None = None
    final_itinerary: Any = None
    last_plan_attempt: int = 0
    last_critic_attempts: list[Any] = field(default_factory=list)
    last_fallback_chain: list[Any] = field(default_factory=list)

    def emit(
        self, type_: SseEventType, payload: dict[str, Any] | None = None
    ) -> SseEvent:
        """生成一个 SseEvent 并自动递增 seq。"""
        ev = make_event(self.seq, type_, payload)
        self.seq += 1
        return ev

    def update_accum_from_diff(self, node_diff: dict[str, Any]) -> None:
        """从 LangGraph 节点 diff 累积 DONE payload 需要的统计字段。"""
        self.last_state = node_diff
        if "plan_attempt" in node_diff and node_diff["plan_attempt"] is not None:
            self.last_plan_attempt = max(
                self.last_plan_attempt, int(node_diff["plan_attempt"])
            )
        if "critic_attempts" in node_diff and node_diff["critic_attempts"] is not None:
            self.last_critic_attempts = list(node_diff["critic_attempts"])
        if "fallback_chain" in node_diff and node_diff["fallback_chain"] is not None:
            self.last_fallback_chain = list(node_diff["fallback_chain"])
        if "itinerary" in node_diff and node_diff["itinerary"] is not None:
            self.final_itinerary = node_diff["itinerary"]
