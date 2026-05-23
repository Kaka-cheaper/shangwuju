"""agent.trace —— Agent 内部事件追踪。

收集 ReAct 循环里的每一步（意图解析 / Tool 调用 / 重规划 / 方案产出）。
后续 P3 的 SSE 网关把这些事件直接转成 SseEvent 推给前端。

不负责：
- SSE 协议传输（在 backend/main.py）
- 事件序列化为 SSE 格式字符串（在 backend/main.py 中按 schemas.sse 转）

设计取舍：
- Trace 用 dataclass，避免和 schemas.sse 强耦合（避免一改 SSE schema 就要改 Agent）
- 每个 record 里 payload 是 dict，前端契约要求的 type / seq / payload 由上层映射
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


@dataclass
class TraceRecord:
    """Agent 中间过程的一条事件。"""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))


class Tracer:
    """Agent 内部事件采集器。

    用法：
        tracer = Tracer()
        tracer.emit("intent_parsed", {...})
        for record in tracer.records: ...

    支持订阅者：注册 callback 后每次 emit 同步通知（用于 SSE 推送）。
    """

    def __init__(self) -> None:
        self.records: list[TraceRecord] = []
        self._subscribers: list[Callable[[TraceRecord], None]] = []

    def subscribe(self, fn: Callable[[TraceRecord], None]) -> None:
        self._subscribers.append(fn)

    def emit(self, type_: str, payload: dict[str, Any] | None = None) -> TraceRecord:
        record = TraceRecord(type=type_, payload=payload or {})
        self.records.append(record)
        for fn in self._subscribers:
            try:
                fn(record)
            except Exception:  # noqa: BLE001
                # 订阅者异常不应影响主流程
                pass
        return record

    def filter(self, type_: str) -> Iterable[TraceRecord]:
        return [r for r in self.records if r.type == type_]
