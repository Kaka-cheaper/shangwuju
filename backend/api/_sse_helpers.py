"""SSE 包装与异常兜底（main.py 拆分后的共享 helper）。

- `to_sse(event)`：SseEvent → sse-starlette 接受的 dict 形式
- `safe_stream(inner)`：兜底装饰器，inner 异常时推 stream_error + done 不漏
- `delay(ms)`：让前端可见动画节奏
- `now_ms()`：当前毫秒时间戳

行为契约：与拆分前的 main.py 内 `_to_sse / _safe_stream / _delay / _now_ms` 完全一致。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

from schemas import SseEvent, SseEventType


def to_sse(event: SseEvent) -> dict[str, Any]:
    """把 SseEvent 转成 sse-starlette 接受的 dict 形式。

    sse-starlette 约定每条事件含 event / id / data 三键。
    前端按 SseEvent.type 解析 payload。
    """
    return {
        "event": event.type.value,
        "id": str(event.seq),
        "data": event.model_dump_json(),
    }


async def safe_stream(
    inner: AsyncIterator[SseEvent],
) -> AsyncIterator[dict[str, Any]]:
    """把内部 SseEvent 流转成 sse-starlette dict 流；中途异常 → stream_error + done。"""
    last_seq = -1
    try:
        async for ev in inner:
            last_seq = ev.seq
            yield to_sse(ev)
    except asyncio.CancelledError:
        # 客户端断开：静默退出，不再推事件
        raise
    except Exception as e:  # noqa: BLE001
        err = SseEvent(
            type=SseEventType.STREAM_ERROR,
            seq=last_seq + 1,
            payload={"reason": "unexpected", "detail": f"{type(e).__name__}: {e}"},
            timestamp_ms=int(time.time() * 1000),
        )
        yield to_sse(err)
        yield to_sse(SseEvent(type=SseEventType.DONE, seq=last_seq + 2))


async def delay(ms: int = 350) -> None:
    """让前端可见动画节奏——评委能看清每一步。"""
    await asyncio.sleep(ms / 1000.0)


def now_ms() -> int:
    return int(time.time() * 1000)
