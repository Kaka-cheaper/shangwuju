"""高德 JS API 安全代理（spec frontend-experience-innovation R2）。

设计动机：
    高德 JS API 2.0 强制要求 jscode 安全密钥才能加载地图。
    - 直接放前端 NEXT_PUBLIC_AMAP_JS_CODE 会被打包进 bundle 暴露
    - 走后端代理：jscode 只存在 backend/.env，前端发给 /_AMapService/xxx
      的请求由后端透传到 restapi.amap.com 并注入 jscode

协议（高德官方约定）：
    前端：window._AMapSecurityConfig = { serviceHost: "/_AMapService" }
    高德 SDK 调用 amap restapi 时，会自动改写 URL 把 host 替换成 serviceHost
    即 https://restapi.amap.com/v3/staticmap?xxx
        → /_AMapService/v3/staticmap?xxx
    本端点接到后转回真实 host 并注入 jscode。
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

router = APIRouter()

_AMAP_UPSTREAM = "https://restapi.amap.com"


def _amap_jscode() -> str:
    """每次调用时读 env，方便单测注入；缺省抛 500。"""
    return (os.getenv("AMAP_JS_CODE") or "").strip()


@router.api_route(
    "/_AMapService/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    include_in_schema=False,
)
async def amap_proxy(path: str, request: Request) -> Response:
    """高德 REST API 透传代理 + jscode 注入。

    透传策略：
        - 保留 query params + 注入 jscode
        - 保留 body（GET 通常无 body）
        - 返回内容、Content-Type、status 原样回传
        - 不缓存（高德侧已有自己的缓存策略）
    """
    jscode = _amap_jscode()
    if not jscode:
        raise HTTPException(
            status_code=500,
            detail="AMAP_JS_CODE not configured in backend/.env",
        )

    target_url = f"{_AMAP_UPSTREAM}/{path}"
    params = dict(request.query_params)
    params["jscode"] = jscode

    forward_headers: dict[str, str] = {}
    for k, v in request.headers.items():
        kl = k.lower()
        if kl in ("user-agent", "accept", "accept-language", "content-type"):
            forward_headers[k] = v

    body = await request.body() if request.method != "GET" else None

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            upstream_resp = await client.request(
                method=request.method,
                url=target_url,
                params=params,
                headers=forward_headers,
                content=body,
            )
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=502,
            detail=f"AMap upstream error: {type(e).__name__}: {e}",
        ) from e

    resp_headers: dict[str, str] = {}
    for k, v in upstream_resp.headers.items():
        kl = k.lower()
        if kl in (
            "content-encoding",
            "transfer-encoding",
            "connection",
            "content-length",  # FastAPI 会自动重算
        ):
            continue
        resp_headers[k] = v

    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        media_type=upstream_resp.headers.get("content-type"),
    )
