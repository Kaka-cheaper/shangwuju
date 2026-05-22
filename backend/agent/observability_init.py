"""agent.observability_init —— 全局可观测平台初始化（Logfire + structlog 桥接）。

设计原则：
- **零成本降级**：未配 LOGFIRE_TOKEN 时自动走本地控制台输出，不阻塞 demo
- **零破坏切换**：env 配 LOGFIRE_TOKEN → 自动上报云端；现有 structlog / print 不受影响
- **生产即用**：Pydantic AI / OpenAI / FastAPI 全自动 instrumentation

调用时机：
    在 `backend/main.py` 顶部 `app = FastAPI(...)` 之前调用 `init_observability(app)`。

文档：
    https://logfire.pydantic.dev/docs/

env 变量：
    LOGFIRE_TOKEN       —— 拿到 token 后填这里；不填走本地控制台
    LOGFIRE_SERVICE_NAME —— 默认 "shangwujue-backend"
    LOGFIRE_ENVIRONMENT  —— 默认 "development"；FC 生产建议 "production"

不负责：
- 业务日志的具体调用（业务代码用 logfire.info / logfire.span）
- 前端可观测（Next.js 端用 vercel/logfire-js 或 web vitals 单独接）
"""

from __future__ import annotations

import logging
import os
from typing import Any

_initialized = False


def init_observability(app: Any | None = None) -> None:
    """初始化 Logfire + 自动 instrumentation。幂等可多次调用。

    Args:
        app: FastAPI 应用实例。提供时自动 instrument FastAPI（请求/响应 trace）。
    """
    global _initialized
    if _initialized:
        return

    try:
        import logfire
    except ImportError:
        # logfire 未装（如 dev 环境跑 pytest 没装 runtime extra）
        # 走本地 stdlib logging，业务代码 logfire.info / logfire.span 不会报错
        # 因为业务代码应该 import logfire 后再用 —— logfire 没装时不该 import
        logging.getLogger(__name__).debug(
            "logfire not installed; observability degraded to stdlib logging"
        )
        _initialized = True
        return

    token = os.getenv("LOGFIRE_TOKEN") or None
    service_name = os.getenv("LOGFIRE_SERVICE_NAME") or "shangwujue-backend"
    environment = os.getenv("LOGFIRE_ENVIRONMENT") or "development"

    # send_to_logfire="if-token-present"：有 token 上报，没有走控制台
    # 这是 demo 安全的核心：评委没配 token 也能看到本地日志
    logfire.configure(
        service_name=service_name,
        environment=environment,
        send_to_logfire="if-token-present",
        # token 会从 LOGFIRE_TOKEN env 自动读，显式传 None 让 Logfire 用 env
        token=token,
    )

    # 自动 instrumentation（每个都包裹 try：单点失败不阻塞其它）
    _safe_instrument(
        lambda: logfire.instrument_pydantic_ai(),
        "pydantic_ai",
    )
    _safe_instrument(
        lambda: logfire.instrument_openai(),
        "openai",
    )
    _safe_instrument(
        lambda: logfire.instrument_httpx(capture_headers=False),
        "httpx",
    )

    if app is not None:
        _safe_instrument(
            lambda: logfire.instrument_fastapi(app, capture_headers=False),
            "fastapi",
        )

    # LangGraph 没有官方 instrumentation；其内部 LLM 调用走 instrument_openai 已覆盖
    # 节点级 trace 评估：内部用 logfire.span 装节点函数即可（按需后补）

    _initialized = True
    logfire.info(
        "observability initialized",
        service_name=service_name,
        environment=environment,
        cloud_upload=bool(token),
    )


def _safe_instrument(fn: Any, name: str) -> None:
    """单个 instrumentation 调用包 try。失败仅记 debug log，不阻塞启动。"""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).debug(
            "logfire.instrument_%s failed: %s", name, e
        )


__all__ = ["init_observability"]
