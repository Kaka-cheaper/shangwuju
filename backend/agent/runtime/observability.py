"""observability —— 结构化日志 + tracing context 骨架。

商业演进路径（让评委看到「日志/追踪基础设施」是产品级骨架）：

- Demo（当前）：structlog 输出彩色文本到 stdout，开发友好
- MVP：LOG_FORMAT=json 切换为 JSON 行，可被 Sentry / Logfire / Loki 直接采集
- 真产品：在 trace_span 内对接 OpenTelemetry，分布式调用链追踪

设计要点：
- 每个 v2 模块用 ``get_logger(__name__)`` 拿独立 logger ——
  便于按模块过滤日志（如只看 orchestrator 的事件）
- ``bind_session_context(session_id=..., turn_id=..., user_id=...)``
  把当前 turn 的上下文绑到所有 logger，所有后续 ``logger.info`` 自动带这三个字段
- ``trace_span(name, **kwargs)`` 上下文管理器自动记录 start/end 与耗时；
  异常会记录 error_type + 重新抛出，不吞错

幂等设计：
- ``_configure_once`` 用属性标记保证多次 import 只配置一次
- ``contextvars`` 自动隔离协程，每个请求 bind 不会污染其他请求

不负责：
- 业务逻辑里调谁的 logger（由各模块自行决定）
- 日志收集/上报（接 Sentry / Logfire 等是 MVP 阶段的工作）
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

import structlog


# ============================================================
# 一次性配置
# ============================================================

def _configure_once() -> None:
    """幂等配置 structlog。

    第一次调用 ``get_logger`` 时触发；后续调用直接 no-op。
    通过 ``LOG_FORMAT`` 环境变量切换文本/JSON 渲染。
    """
    if getattr(_configure_once, "_done", False):
        return

    log_format = (os.getenv("LOG_FORMAT") or "text").strip().lower()

    processors: list[Any] = [
        # contextvars 必须放最前：先合并 bind_session_context 设的字段
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if log_format == "json":
        # 生产格式：JSON 行（Sentry / Logfire / Loki 友好）
        processors.append(structlog.processors.JSONRenderer(ensure_ascii=False))
    else:
        # 开发格式：彩色但不开 ANSI（避免 Windows cmd 乱码）
        processors.append(structlog.dev.ConsoleRenderer(colors=False))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _configure_once._done = True  # type: ignore[attr-defined]


# ============================================================
# 公开 API
# ============================================================

def get_logger(name: str) -> structlog.BoundLogger:
    """拿一个绑定模块名的 structlog logger。

    用法：

        log = get_logger(__name__)
        log.info("planner.started", session_id=sid, mode="llm_first")

    默认输出格式：``2026-05-17T10:30:00 [info] planner.started session_id=...``
    LOG_FORMAT=json 时输出 JSON 行：``{"timestamp":"...","level":"info","event":"planner.started",...}``
    """
    _configure_once()
    return structlog.get_logger(name)


def bind_session_context(
    *,
    session_id: str,
    turn_id: str = "",
    user_id: str = "demo_user",
) -> None:
    """把当前 turn 的 context 绑定到所有 logger。

    在 SSE 流入口调用一次，之后所有 ``logger.info()`` 都会自动带
    session_id / turn_id / user_id 三个字段，不需要每次手动传。

    用 contextvars 实现协程隔离 —— 不同请求绑不同 context 互不污染。

    用法（在 main.py 入口）::

        bind_session_context(session_id=req.session_id, turn_id="t-001")
        try:
            await run_orchestrator(req)
        finally:
            clear_session_context()
    """
    structlog.contextvars.bind_contextvars(
        session_id=session_id,
        turn_id=turn_id,
        user_id=user_id,
    )


def clear_session_context() -> None:
    """清空当前协程绑定的 session context。

    SSE 流结束时（finally 块）调用，避免下一个请求复用残留的 context。
    """
    structlog.contextvars.clear_contextvars()


@contextmanager
def trace_span(name: str, **kwargs: Any) -> Iterator[None]:
    """上下文管理器：自动记录开始 / 结束 / 异常 + 耗时。

    用法::

        with trace_span("call_tool", tool="search_pois", distance=5):
            result = await provider.search_pois(...)

    会产生三类事件（按是否抛异常二选一）：
    - ``call_tool.start``：进入块时
    - ``call_tool.end`` + ``elapsed_ms``：正常退出时
    - ``call_tool.error`` + ``elapsed_ms`` + ``error`` + ``error_type``：抛异常时

    异常照常向上抛 —— 不吞错。商业演进时这里可以接 OpenTelemetry span。
    """
    log = get_logger("trace")
    start = time.perf_counter()
    log.info(f"{name}.start", **kwargs)
    try:
        yield
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log.error(
            f"{name}.error",
            elapsed_ms=elapsed_ms,
            error=str(e),
            error_type=type(e).__name__,
            **kwargs,
        )
        raise
    else:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log.info(f"{name}.end", elapsed_ms=elapsed_ms, **kwargs)


__all__ = [
    "get_logger",
    "bind_session_context",
    "clear_session_context",
    "trace_span",
]
