"""liveness / readiness 健康探针。

- /health：进程活着即返回 ok，给 K8s/FC liveness probe 用
- /ready ：依赖都通 + 配置都对才返回 ok，给 readiness probe 用
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter

from schemas import current_env_mode

VERSION = "0.1.0"

router = APIRouter(tags=["健康探活"])


def _use_real_planner() -> bool:
    """是否启用真 planner 链路（意图解析 + 规划出方案）。

    解析顺序（优先级递减）：
    1. PLANNER_USE_REAL 显式开关（1/true/yes/on → 真，0/false/no/off → 假）
    2. LLM_PROVIDER=stub  → 假（开发/单测兼容）
    3. 有任意 LLM credential（LLM_API_KEY 或旧名 DEEPSEEK_API_KEY/QWEN_API_KEY）→ 真
    4. 默认 → 假（即纯 stub fixture，不调任何 LLM）
    """
    raw = os.getenv("PLANNER_USE_REAL")
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() in ("1", "true", "yes", "on")

    explicit_provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if explicit_provider == "stub":
        return False

    has_credential = bool(
        (os.getenv("LLM_API_KEY") or "").strip()
        or (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        or (os.getenv("QWEN_API_KEY") or "").strip()
    )
    return has_credential


@router.get("/health", summary="liveness 探针")
def health() -> dict[str, str]:
    """健康检查 + 当前生效配置。

    `llm_provider` 与 `planner_real` 反映**当前真实**配置（解耦后由 base_url 自动推断 +
    _use_real_planner() 判断），不再被 .env 中是否显式设 LLM_PROVIDER 干扰。
    """
    if (os.getenv("LLM_PROVIDER") or "").strip().lower() == "stub":
        provider_display = "stub"
    else:
        try:
            from agent.core.llm_client import _resolve_creds

            _, _, _, provider_display = _resolve_creds(None)
        except Exception:  # noqa: BLE001
            provider_display = "openai-compatible"
    return {
        "status": "ok",
        "version": VERSION,
        "llm_provider": provider_display,
        "planner_mode": current_env_mode(),
        "planner_real": "1" if _use_real_planner() else "0",
    }


@router.get("/ready", summary="readiness 探针")
async def ready() -> dict[str, Any]:
    """就绪检查（Kubernetes / FC / docker compose 用）。

    与 /health 的区别：
    - /health 是「我活着吗」（进程跑了就 OK）→ liveness probe
    - /ready  是「我能接流量吗」（依赖都通 + 配置都对）→ readiness probe

    探活清单：
    - LLM 配置（base_url / key 至少有一项可用，stub 视为可用）
    - Redis 可达（仅当 SESSION_STORE=redis 或 REDIS_URL 配了才探，
      InMemory 模式下默认 always ready）
    - mock 数据可加载（防镜像漏 copy mock_data 卷）

    任何子项失败 → HTTP 503 Service Unavailable，前端 / FC 健康检查会重试。
    """
    checks: dict[str, dict[str, Any]] = {}
    overall_ok = True

    # 1. LLM 配置
    llm_provider_env = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if llm_provider_env == "stub":
        checks["llm"] = {"ok": True, "provider": "stub"}
    else:
        try:
            from agent.core.llm_client import _resolve_creds

            _, _, _, provider_display = _resolve_creds(None)
            checks["llm"] = {"ok": True, "provider": provider_display}
        except Exception as e:  # noqa: BLE001
            checks["llm"] = {"ok": False, "error": str(e)[:200]}
            overall_ok = False

    # 2. Redis（仅 SESSION_STORE=redis 或显式配 REDIS_URL 时探）
    redis_url = os.getenv("REDIS_URL")
    session_store = (os.getenv("SESSION_STORE") or "memory").strip().lower()
    if session_store == "redis" or redis_url:
        try:
            import redis.asyncio as redis_async  # type: ignore[import-not-found]

            client = redis_async.from_url(
                redis_url or "redis://localhost:6379/0",
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            pong = await client.ping()
            await client.aclose()
            checks["redis"] = {"ok": bool(pong), "url": redis_url or "redis://localhost:6379/0"}
            if not pong:
                overall_ok = False
        except Exception as e:  # noqa: BLE001
            checks["redis"] = {"ok": False, "error": str(e)[:200]}
            overall_ok = False
    else:
        checks["redis"] = {"ok": True, "skipped": "session_store=memory"}

    # 3. mock 数据可加载（POI/餐厅/路线 + 两者内嵌评论数）
    #
    # 计数字段供前端 MockModeBadge（spec bonus-points-review M1）运行时展示
    # 真实数据规模——徽章不再硬编码数字，改为读这里，永不过时（见该组件
    # docstring 与 2026-07-12 修复）。routes/reviews 是新增维度：routes 来自
    # load_routes()，reviews 是 Poi/Restaurant.reviews 字段的内嵌评论逐条求和
    # （数据里没有独立 reviews.json，评论挂在各 POI/餐厅记录下）。
    try:
        from data.loader import load_pois, load_restaurants, load_routes

        pois = load_pois()
        rests = load_restaurants()
        pois_count = len(pois)
        rests_count = len(rests)
        routes_count = len(load_routes())
        reviews_count = sum(len(p.reviews) for p in pois) + sum(len(r.reviews) for r in rests)
        checks["mock_data"] = {
            "ok": pois_count > 0 and rests_count > 0,
            "pois": pois_count,
            "restaurants": rests_count,
            "routes": routes_count,
            "reviews": reviews_count,
        }
        if pois_count == 0 or rests_count == 0:
            overall_ok = False
    except Exception as e:  # noqa: BLE001
        checks["mock_data"] = {"ok": False, "error": str(e)[:200]}
        overall_ok = False

    body = {
        "status": "ready" if overall_ok else "not_ready",
        "version": VERSION,
        "checks": checks,
    }
    if not overall_ok:
        from fastapi.responses import JSONResponse

        return JSONResponse(body, status_code=503)
    return body
