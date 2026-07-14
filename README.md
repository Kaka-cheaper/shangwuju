# 晌午局 · shangwuju

[![命题赛道 06](https://img.shields.io/badge/美团_AI_Hackathon-命题赛道_06-FFD100)](https://ai-competition-hub.nocode.host/)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-14-000000?logo=nextdotjs&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-1c3c5e)

> 一句话，定下一个下午怎么过。

**晌午局**是一个面向本地生活的半日出行管家：你说一句话，它听懂你的约束、把地点和时间排成一条可执行的行程、订好餐厅门票、写好转发文案。它要解决的不是「搜得准」，而是「帮你把事做完」——从一句话，到订好为止。

美团 AI Hackathon 命题赛道 06「本地探索 · 周末闲时活动规划」参赛作品。

🔗 **在线演示**：<https://kaka-cheaper.github.io/shangwuju/>

```text
「今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆在减肥」
   │
   ▼  路由      判断输入类型（规划 / 闲聊 / 反馈 / 确认 …）
   ▼  意图解析  一句话 → 结构化约束（亲子友好 / 低脂 / ≤5km …），每个字段标注来源
   ▼  规划      大模型定偏好权重 + 打契合分，算法排选点 / 顺序 / 时刻
   ▼  校验      20 类硬指标逐条检查，不合格 → 定点修复（满座 17:00 → 自动改约 17:30）
   ▼  执行      用户确认 → 预约下单 + 一键生成转发文案
   │
   └─▶ 一条带时间线的下午：出发 · 主活动 · 转场 · 用餐 · 返回
```

## ✨ 能做什么

| 能力 | 在做什么 | 例子 |
|------|----------|------|
| **听懂** | 自然语言 → 结构化约束 | 「孩子 5 岁」= 亲子友好；「减肥」= 低脂；「别太远」= ≤5km |
| **规划** | 在候选里挑组合、排时间与路线 | 综合距离 / 营业时段 / 座位 / 年龄 / 忌口求解 |
| **执行** | 预约餐厅 / 买门票 / 加购 | 返回 mock 订单号；没位自动改期或切备选 |
| **转发** | 写适合发给家人 / 朋友 / 客户的文案 | 按社交语境切换口吻 |

八个演示场景（家庭 / 朋友 / 情侣 / 闺蜜 / 商务 / 独处 …）见 [`backend/agent/routing/canonical_shortcut.py`](backend/agent/routing/canonical_shortcut.py)。

## 🧠 技术亮点

**意图理解 + 级联路由**　任意输入先过多层确定性规则（注入检测、字面短路、会话内规则），规则读不懂才调大模型；一次调用出 6 类闭集标签 + 槽位 + 置信度，低置信度整体转为「追问」而非硬选标签。确定输入零模型成本、毫秒响应。

**规划是运筹求解，不是文本生成**　半日行程被建模为运筹学的**带时间窗的团队定向问题（TOPTW）**。大模型只做主观判断（定四维偏好权重、给每个候选打语义契合分），算法做客观计算（选点、排序、卡准时刻），营业时段与预约槽统一表达成时间窗（精确预约槽即「零宽窗口」）。两者产出再交给一个**零 LLM 的独立校验器**跑 20 类硬指标把关。

**定点修复（min-conflicts）**　校验不过时，溯源到肇事节点，只把它拉黑、挖掉冲突时段、补一个替补，其余原样不动。演示里「餐厅满座 → 自动改期」正是此机制；用户手动「换一家店」复用同一个算子。

**多级韧性**　每一步都预挂退路，分级补救（大模型改 → 换启发式算法 → 规则兜底），有次数上限。大模型整体不可用时，纯规则引擎也能出方案、照样过校验、断网可跑。

**诚实披露**　每个约束标注来源（用户亲口说 / 系统推断 / 默认补）；候选不够需放宽时按来源排序牺牲，用户亲口说的最后才碰、忌口永不放宽。方案里只展示查得到的真实数据，缺失就留空、绝不编造。

**多人协作 + 行程衍生物**　WebSocket 协作房间（提约束、对每段投票、实时广播）；高德地图行程标注（真实驾车路线，无 key 时降级为文字）、一键海报、分享二维码、语音播报。

设计上 Tool 与 Agent 对场景类型无感——代码不含 `scene_type` / `relation_type` 之类硬编码分支，所有约束通过结构化参数传递。

## 🚀 快速开始

### Docker 一键（推荐）

```bash
git clone <repo> && cd 美团AI\ Hackathon
cp backend/.env.example backend/.env   # 可选：填 LLM / 高德 key，不填走 stub
docker compose up --build
```

- 前端 <http://localhost:3000>　·　后端 <http://localhost:8000/health>
- Redis + 后端 + 前端 一起起；没有 `.env` 也能跑（stub 模式）。

### 本地开发

前置：Python 3.11+ · Node 18+ · pnpm 9 · [uv](https://docs.astral.sh/uv/)

```bash
# 后端
cd backend && uv sync --extra runtime
uv run uvicorn main:app --port 8000

# 前端（新终端）
cd frontend && pnpm install && pnpm dev
```

- **stub 模式**（无需 LLM key）：后端设 `LLM_PROVIDER=stub`，点 8 个场景按钮即可跑通完整链路。
- **真 LLM 模式**：`backend/.env` 填任意 OpenAI 兼容凭证（DeepSeek / 通义 / OpenAI / 智谱 / Ollama）：

```env
LLM_API_KEY=<your-key>
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

## 🏗️ 架构

```text
┌─ 前端 · frontend/ (Next.js 14 App Router) ─────────────────────┐
│  26 组件 · Zustand · 手写 SSE 解析 · WebSocket · 高德地图        │
└──────────────────────── HTTP + SSE + WS ───────────────────────┘
┌─ 接口 · backend/main.py + api/ ────────────────────────────────┐
│  FastAPI · /chat/turn(SSE) · /ws/{room}(WS) · /_AMapService     │
└────────────────────────────────────────────────────────────────┘
┌─ Agent 编排 · backend/agent/ ──────────────────────────────────┐
│  routing/  级联分诊路由（确定性规则前置 + 大模型兜底）           │
│  intent/   意图解析 · 反馈合并 · 叙述                            │
│  graph/    LangGraph StateGraph，十余节点 + 跨 turn checkpointer │
│  planning/ blueprint · planners(ils / rule) · critic(20 类) · commute │
│  core/     注入防护 · 对话行为判定 · coverage_gate · hedged client │
│  context/  会话上下文打包器（单人 / 房间统一）                   │
└────────────────────────────────────────────────────────────────┘
┌─ Tool 层 · backend/tools/ ─────────────────────────────────────┐
│  9 Tool（OpenAI Function Calling），统一注册 + 输入输出双校验    │
└────────────────────────────────────────────────────────────────┘
┌─ 数据层 · backend/data/ + mock_data/ ──────────────────────────┐
│  95 POI · 120 餐厅 · 215 路线 · 5 persona · 6 用户              │
│  provider 抽象：mock / 高德（真实路径已接） …                    │
└────────────────────────────────────────────────────────────────┘
```

持久化与地图 provider 都做了抽象，环境变量切换：`SESSION_STORE`（memory / redis）、`NEARBY_PROVIDER`（mock / gaode）。演示走 mock / memory / stub，高德路径服务已接真实 API。

## 📁 项目结构

```text
.
├─ backend/                FastAPI + LangGraph
│  ├─ main.py              app 装配 + 路由注册
│  ├─ api/                 chat / collab / scenarios / amap / preferences / legal …
│  ├─ agent/
│  │  ├─ routing/          级联分诊路由（route_turn · brain · canonical_shortcut）
│  │  ├─ intent/           parser · refiner · narrator
│  │  ├─ graph/            LangGraph StateGraph + nodes/
│  │  ├─ planning/         blueprint · planners(ils / rule) · critic · commute
│  │  ├─ core/             注入防护 · 对话行为 · coverage_gate · hedged client
│  │  └─ context/          会话上下文打包器
│  ├─ tools/               9 个 Function Calling Tool + registry
│  ├─ schemas/             Pydantic v2 模型（intent / itinerary / sse …）
│  └─ tests/               1896 个 pytest 用例
├─ frontend/               Next.js 14 App Router
│  ├─ app/                 首页 + 协作房间入口
│  ├─ components/          26 个组件
│  └─ lib/                 store · sse · ws · utils
├─ mock_data/              95 POI · 120 餐厅 · 215 路线 · 5 persona · 6 用户
├─ docs/                   需求 / 设计 / 商业 / 法务
└─ docker-compose.yml      Redis + 后端 + 前端 一键起
```

## 🔌 API 端点

| Method | 路径 | 作用 |
|--------|------|------|
| POST | `/chat/turn` | **SSE** 对话主入口，自动识别新需求 / 反馈，跨 turn 持久化 |
| POST | `/chat/confirm` | **SSE** 确认下单，派发执行类 Tool |
| POST | `/chat/adjust` | **SSE** 单节点定向调整 / 具名备选 |
| WS | `/ws/{room_id}` | **WebSocket** 多人协作（约束 / 投票 / 确认） |
| POST · GET | `/room/create` · `/room/{room_id}/state` | 建房 / 房间状态快照 |
| GET | `/scenarios` · `/personas` | 演示场景 / 画像 |
| GET · POST | `/preferences/{user_id}` · `/preferences/{user_id}/reset` | 画像偏好 / 清空记忆 |
| \* | `/_AMapService/{path}` | 高德 REST 代理（注入 jscode） |
| GET | `/health` · `/ready` | 存活 / 就绪探针 |

完整契约（请求 / 响应 / SSE 事件序列）见 [`backend/api_contract.md`](backend/api_contract.md)。

## ⚙️ 配置

后端 `backend/.env`（完整见 [`backend/.env.example`](backend/.env.example)）：

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | OpenAI 兼容凭证；不填走 stub |
| `LLM_TIMEOUT_S` / `LLM_MAX_RETRIES` | 调用超时与重试 |
| `PLANNER_MODE` | 规划器：`rule` / `llm` |
| `AMAP_REST_KEY` / `AMAP_JS_CODE` | 高德服务端 key + jscode（后端代理用） |

会话与数据源另通过环境变量在运行时切换：`SESSION_STORE`（`memory` / `redis`，默认 memory）、`NEARBY_PROVIDER`（`mock` / `gaode`，默认 mock）。

前端 `frontend/.env.local`：`NEXT_PUBLIC_API_BASE`（后端地址）、`NEXT_PUBLIC_BASE_PATH`（静态部署子路径）、`NEXT_PUBLIC_AMAP_KEY`（不填地图降级为文字）。

## 🧪 测试

```bash
cd backend && uv run pytest -q          # 1896 个后端用例
cd frontend && pnpm verify:all          # lint + typecheck + vitest + build
```

CI（`.github/workflows/`）：后端 pytest · 前端 typecheck / test / build · Docker 镜像构建验证。

## 🛠️ 技术栈

| 层 | 选型 |
|----|------|
| LLM | 任意 OpenAI 兼容 base_url（DeepSeek / 通义 / OpenAI / 智谱 / Ollama） |
| 后端 | Python 3.11 · FastAPI · LangGraph · Pydantic v2 · sse-starlette |
| 前端 | Next.js 14 App Router · TypeScript strict · Tailwind · Zustand · 高德 JS API |
| 数据 | mock JSON（provider 抽象）；高德路径服务已接真实 API |
| 持久化 | 内存 / Redis（抽象切换） |
| 可观测 | structlog · Logfire（OpenTelemetry 接入位） |

## 📖 文档

[`docs/`](docs/) 是我们的过程设计稿——需求分析、架构决策（ADR）、实现记录、商业与法务，记录了每个关键决策「为什么这么做、放弃了什么」。

| 想了解 | 去哪看 |
|--------|--------|
| 需求 / MVP / 验收标准 | [`docs/01-requirements/`](docs/01-requirements/) |
| 架构决策流水（ADR） | [`docs/adr/`](docs/adr/) |
| 后端 API 契约 | [`backend/api_contract.md`](backend/api_contract.md) |
| 前端 / 后端实现说明 | [`frontend/README.md`](frontend/README.md) · [`backend/README.md`](backend/README.md) |
| AI Agent 编码约定 | [`AGENTS.md`](AGENTS.md) |

## 🌐 部署

当前公开 Demo：

- **前端**：GitHub Pages 静态导出，<https://kaka-cheaper.github.io/shangwuju/>。
- **后端**：阿里云函数计算 FC（Custom Container），公网地址通过 GitHub Repository Variable 注入前端构建。
- **会话**：Demo 用 `SESSION_STORE=memory` + FC Cookie 会话亲和；产品化建议切 Redis / Tair。

GitHub Pages 部署前需在仓库 `Settings → Secrets and variables → Actions → Variables` 配置 `NEXT_PUBLIC_API_BASE`；要显示高德地图另需 `NEXT_PUBLIC_AMAP_KEY` 与后端 `AMAP_JS_CODE`。
