"""backend.main —— 晌午局 FastAPI 入口。

本文件仅做 4 件事，**不再含任何端点实现 / SSE fixture / Request 模型**：

1. 加载 .env（双重保险）
2. 初始化 FastAPI app 实例（含 description / openapi_tags / middleware）
3. 接入 11 个 router 子模块
4. 通过 init_observability 启用 Logfire 探针（失败降级，不阻塞启动）

端点实现位置（spec code-modularization-refactor）：

```
| URL                              | 模块                          |
|----------------------------------|------------------------------|
| GET  /health, /ready             | api/health.py                 |
| GET  /scenarios                  | api/scenarios.py              |
| /_AMapService/{path:path}        | api/amap.py                   |
| GET  /personas, /preferences/*   | api/preferences.py            |
| GET  /legal/*                    | api/legal.py                  |
| GET  /auth/*                     | api/oauth.py                  |
| POST /room/* + WS /ws/{room_id}  | api/collab.py                 |
| POST /chat/{stream,confirm,refine,turn} | api/chat.py            |
```

`api/_streams/` package 容纳 chat 端点共用的 SSE 流实现（_stub_stream 等）；
`api/_session_store.py` + `api/_sse_helpers.py` 是跨 router 共享的内部 helper。

参考：
- pitfalls P3-跨项目「dotenv 双重保险加载」：CLI 入口与服务入口都要 load_dotenv()
- AGENTS.md §3.3 4 层架构边界：UI 不直连 LLM；HTTP 层只做转发与 SSE 序列化
"""

from __future__ import annotations

from dotenv import load_dotenv

# 双重保险加载 .env（uvicorn --reload 子进程会跳过 CLI 入口）
load_dotenv()

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import amap, chat, collab, health, legal, oauth, preferences, scenarios
from api.health import VERSION


# ============================================================
# Lifespan：仅 SESSION_STORE=redis 时做 async 预初始化
#   - memory 模式（默认 / 裸机）：此处零开销、绝不 import redis、行为同改造前
#   - redis 模式：预编译带 Redis checkpointer 的 graph + 从 Redis 预热会话快照
# ============================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    session_store = (os.getenv("SESSION_STORE") or "memory").strip().lower()
    if session_store == "redis":
        # 1) LangGraph 用 AsyncRedisSaver（需 await asetup）——/chat/turn 恒走图、
        #    /chat/confirm 也已统一到同一条 graph_confirm 流（ADR-0012 决策 5，
        #    USE_LANGGRAPH 开关已退役），预热不再有条件可判断，SESSION_STORE=redis
        #    即预热。
        try:
            from agent.graph.build import warm_up_graph

            backend = await warm_up_graph()
            logging.getLogger("main").info("graph checkpointer backend: %s", backend)
        except Exception:  # noqa: BLE001
            logging.getLogger("main").warning("graph warm-up failed", exc_info=True)
        # 2) 会话快照从 Redis 预热回内存（confirm / 协作创建依赖它）
        try:
            from api._session_store import SESSION_STORE

            if hasattr(SESSION_STORE, "warm_from_redis"):
                await SESSION_STORE.warm_from_redis()
        except Exception:  # noqa: BLE001
            logging.getLogger("main").warning(
                "session snapshot warm-up failed", exc_info=True
            )
    yield
    # shutdown：Redis 连接池随进程退出释放，无需显式清理


# ============================================================
# 应用实例化
# ============================================================

app = FastAPI(
    title="晌午局 Backend",
    version=VERSION,
    description=(
        "本地半日出行管家 Agent 后端（FastAPI + SSE）\n\n"
        "## 给小团团队的接入说明\n\n"
        "本服务把「半日规划」能力以 HTTP API 形式开放给小团 App。集成形态：\n\n"
        "- 用户在小团 App 主页输入一句话 → 调 `POST /chat/turn` 拿规划事件流\n"
        "- 小团 App 渲染行程卡片 + 候选发现链路（SSE 实时流）\n"
        "- 用户点「确认预约」→ 调 `POST /chat/confirm` 触发执行类工具\n"
        "- 用户在卡片里说「换近一点」→ 同一接口 `POST /chat/turn` 自动识别为反馈走重规划\n\n"
        "完整接入指南：见 `docs/06-business/07-小团能力接入指南.md`。\n\n"
        "## 部署形态\n\n"
        "- 本地试跑：`docker compose up`，2 分钟跑起来\n"
        "- 接入小团：把本服务部署到内网，小团 App 网关转发上述 4 个端点即可"
    ),
    openapi_tags=[
        {
            "name": "小团接入",
            "description": (
                "面向小团 App 集成的核心 4 端点：对话主入口、确认下单、独立反馈、独立重规划。"
                "小团评委关注的就是这一组——demo 现场可访问 /docs 直接联调。"
            ),
        },
        {
            "name": "健康探活",
            "description": "liveness / readiness 健康探针，给 K8s / FC / docker compose 健康检查用。",
        },
        {
            "name": "演示场景",
            "description": "8 个演示场景的输入文案配置 + persona 切换器后端。",
        },
        {
            "name": "用户与偏好",
            "description": "persona 列表 + 跨 session 偏好读取与重置。",
        },
        {
            "name": "协作房间",
            "description": "多人协作下单：创建房间、拉状态、广播事件（demo 加分项）。",
        },
        {
            "name": "运营辅助",
            "description": "OAuth provider 接入位、用户协议、隐私政策（占位草案，真上线前需法务审核）。",
        },
    ],
    lifespan=lifespan,
)


# ============================================================
# 可观测平台初始化（Logfire；未配 token 时降级本地控制台输出）
# ============================================================
# 自动 instrument Pydantic AI / OpenAI / httpx / FastAPI
try:
    from agent.core.observability_init import init_observability

    init_observability(app)
except Exception:  # noqa: BLE001
    # 初始化失败不阻塞应用启动（demo 安全）
    pass


# ============================================================
# Middleware
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Demo 模式：允许所有来源（含 WebSocket）
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Router 接入（按业务域分组；具体端点定义见 api/*.py）
# ============================================================

# 健康探活与基础设施
app.include_router(health.router)
app.include_router(scenarios.router)
app.include_router(amap.router)

# 用户与偏好
app.include_router(preferences.router)

# 运营辅助（法务 / OAuth）
app.include_router(legal.router)
app.include_router(oauth.router)

# 业务核心：对话 / 协作
app.include_router(chat.router)
app.include_router(collab.router)
