# 晌午局后端 Dockerfile（multi-stage，兼容本地 docker compose 与阿里云 FC Custom Container）
#
# 设计目标：
#   - 镜像 < 200MB（uv + python-slim，去掉 build deps）
#   - 冷启动友好：依赖装在 builder stage，runtime stage 只 copy site-packages
#   - 与 FC Custom Container 兼容：监听 0.0.0.0:9000（FC 默认）
#   - 本地 docker compose up 与 FC 同一镜像（PORT env 切端口）
#
# 构建（build context 必须是仓库根，因为要 COPY mock_data / docs/legal）：
#   docker build -t shangwujue-backend:latest -f backend/Dockerfile .
#
# 运行（本地）：
#   docker run -p 8000:8000 -e PORT=8000 --env-file backend/.env shangwujue-backend:latest
#
# 运行（FC）：
#   FC 自动注入 PORT=9000；镜像直接推 ACR 即可
#
# 镜像层级：
#   1. base       Python 3.11-slim 基础
#   2. builder    装系统 build deps + uv sync（依赖编译都在这里）
#   3. runtime    只 copy 应用代码 + site-packages，瘦身

ARG PYTHON_VERSION=3.11

# ============================================================
# Stage 1: base —— 公共基础（runtime 与 builder 都从这里继承）
# ============================================================
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# ============================================================
# Stage 2: builder —— 装依赖
# ============================================================
FROM base AS builder

# 系统 build deps（仅 builder 需要；runtime 不带）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 装 uv（官方推荐安装方式）
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

# 仅 copy 依赖描述文件，提升缓存命中率
# 注意：build context 是仓库根（docker-compose.yml → backend.build.context: .），
# 而 pyproject.toml / uv.lock 在 backend/ 下，所以必须带 backend/ 前缀，否则 COPY not found。
COPY backend/pyproject.toml backend/uv.lock ./

# 装依赖（含 runtime extras，含 fastapi / uvicorn / sse-starlette）
# 不装项目自己（--no-install-project），项目代码在 runtime stage 才 copy
RUN uv sync --frozen --no-install-project --extra runtime

# ============================================================
# Stage 3: runtime —— 最终镜像
# ============================================================
FROM base AS runtime

# 仅装运行时系统依赖（无 build deps）
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash app

# Copy 已装好的 venv（site-packages 都在 .venv）
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app"

# Copy 应用代码 + mock 数据
# 注意：mock_data 是相对仓库根目录的 ../mock_data，build context 在 backend/ 下时拿不到
# 解决：build context 用仓库根 `docker build -f backend/Dockerfile .`（见 docker-compose.yml）
COPY backend /app
COPY mock_data /mock_data
COPY docs/legal /app/docs/legal

# FC 与本地都用 PORT 环境变量；默认 8000（本地）；FC 会注入 9000
# FC Custom Container 必须 listen 0.0.0.0:$PORT 否则启动检查失败
ENV PORT=8000 \
    HOST=0.0.0.0

# Demo 默认值（生产用 .env / FC 函数环境变量覆盖）
ENV LLM_PROVIDER=stub \
    USE_LANGGRAPH=1 \
    SESSION_STORE=memory \
    DATA_PROVIDER=mock \
    NEARBY_PROVIDER=mock \
    LOG_FORMAT=json

USER app

# 健康探针：FC 启动后 30s 内首次调 /ready，180s 内首字节响应
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f "http://localhost:${PORT}/ready" || exit 1

EXPOSE 8000 9000

# 启动：uvicorn 单 worker（FC 会按并发自动横向扩；本地 dev 也用 1 worker 保持 InMemory 一致）
# --host 0.0.0.0 是 FC Custom Container 强制要求
CMD ["sh", "-c", "uvicorn main:app --host ${HOST} --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips '*'"]
