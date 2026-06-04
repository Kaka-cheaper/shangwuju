# 晌午局前端 Dockerfile（multi-stage，next standalone）
#
# 设计目标：
#   - 镜像 < 100MB（next standalone 输出）
#   - 仅含运行时必需文件（不带 src / .git / 测试）
#   - 与后端同套部署节奏（docker-compose / FC / Vercel 都能跑）
#
# 构建：
#   docker build -t shangwujue-frontend:latest -f frontend/Dockerfile frontend
#
# 运行：
#   docker run -p 3000:3000 \
#     -e NEXT_PUBLIC_API_BASE=http://localhost:8000 \
#     -e NEXT_PUBLIC_AMAP_KEY=xxx \
#     shangwujue-frontend:latest
#
# 注意：NEXT_PUBLIC_* 是 build-time 注入（next build 时打进 bundle），
# 所以镜像里的值是 build 时定的；切换部署环境要 rebuild。
# 真生产建议：在 CI 里按目标环境 build 不同 tag 的镜像。

ARG NODE_VERSION=20

# ============================================================
# Stage 1: deps —— 装依赖（仅 production 需要的）
# ============================================================
FROM node:${NODE_VERSION}-alpine AS deps

# alpine 用的是 musl，next.js swc 需要这个 compat
RUN apk add --no-cache libc6-compat

WORKDIR /app

# 仅 copy 依赖描述文件，提升缓存命中
COPY package.json pnpm-lock.yaml ./

# pnpm 用 corepack（Node 16.10+ 自带）管理。
# ⚠ 必须钉死 pnpm 版本：corepack 不 pin 会从 npm 拉【最新】pnpm。
#   新版 pnpm(11.x)要求 Node ≥22.13（内部 require "node:sqlite"），
#   本镜像是 node:20 → pnpm 启动即崩：ERR_UNKNOWN_BUILTIN_MODULE: node:sqlite。
#   9.15.9 = pnpm 9 系最新，匹配 pnpm-lock.yaml 的 lockfileVersion 9.0，
#   也与 CI(.github/workflows/ci.yml → pnpm/action-setup version:9)保持一致。
RUN corepack enable pnpm \
    && corepack prepare pnpm@9.15.9 --activate \
    && pnpm install --frozen-lockfile

# ============================================================
# Stage 2: builder —— next build
# ============================================================
FROM node:${NODE_VERSION}-alpine AS builder

WORKDIR /app

# Copy 已装好的 node_modules
COPY --from=deps /app/node_modules ./node_modules

# Copy 源码（package.json / pnpm-lock 在 deps 阶段已经有了，但 next build 需要）
COPY . .

# build-time 公开变量：FC 部署时前端 build 一般不在 FC 上做（在 GitHub Actions / Vercel）
# 这里支持通过 --build-arg 传入；不传时取默认值
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ARG NEXT_PUBLIC_AMAP_KEY=
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE} \
    NEXT_PUBLIC_AMAP_KEY=${NEXT_PUBLIC_AMAP_KEY} \
    NEXT_TELEMETRY_DISABLED=1

# 同 deps 阶段：钉死 pnpm 9.15.9（否则 corepack 拉最新版在 node:20 上崩）
RUN corepack enable pnpm \
    && corepack prepare pnpm@9.15.9 --activate \
    && pnpm build

# 兜底：本项目当前没有 public/ 目录（git 不跟踪空目录，clone 出的干净副本更不会有），
# 但下面 runtime stage 会 COPY /app/public → 不兜底则报 "/app/public": not found 炸 build。
# mkdir -p 幂等，将来在源码加了 public 静态资源也不受影响。
RUN mkdir -p /app/public

# ============================================================
# Stage 3: runtime —— 仅 standalone 必需文件
# ============================================================
FROM node:${NODE_VERSION}-alpine AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

WORKDIR /app

# 创建 nextjs 用户（与 Next.js 官方 Dockerfile 模板一致）
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

# Copy public 静态资源
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

# Copy standalone server（含极简 node_modules）
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./

# Copy static 资源（JS/CSS/图片）
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

# 健康探针：访问首页（Next.js standalone 默认 server.js）
# 探针用 127.0.0.1 而非 localhost：alpine 里 localhost 可能解析到 IPv6 ::1，
# 而 Next standalone server 监听 0.0.0.0(IPv4) → wget 连 ::1 会 Connection refused 误报 unhealthy。
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget --quiet --tries=1 --spider "http://127.0.0.1:${PORT}" || exit 1

CMD ["node", "server.js"]
