# 晌午局 shangwuju

> 一句话,搞定下午半天的出行。

**晌午局** 是一个本地出行管家 AI Agent:你说一句话,它听懂你的约束、规划好半天行程、订好餐厅门票、写好转发文案。

美团 AI Hackathon 命题赛道 06「本地探索 · 周末闲时活动规划」参赛作品。

线上演示：

- 前端 GitHub Pages：<https://kaka-cheaper.github.io/shangwuju/>

```text
「今天下午想和老婆孩子出去玩几个小时,别离家太远,孩子 5 岁,老婆在减肥」
   │
   ▼  输入域路由(planning / chitchat / meta / emotional / off_topic / ambiguous)
   │  planning ↓
   ▼  意图解析:一句话 → 结构化约束(亲子友好 / 低脂 / ≤5km …)
   │
   ▼  LangGraph 规划循环:意图 → 并行查询 → 组装 → critic 校验 → 异常重规划
   │  9 个 Tool 编排(POI / 餐厅 / 路线 / 可订 / 预约 / 购票 / 加购 / 转发 / 画像)
   ▼
   一条完整时间线行程(出发 · 主活动 · 转场 · 用餐 · 附加 · 返回)
   │  餐厅 17:00 满 → 自动改约 17:30
   ▼  用户确认 → 预约下单 + 一键复制转发文案
```

## 能做什么

四个动词:

| 能力 | 在做什么 | 典型表现 |
|------|----------|----------|
| **听懂** | 自然语言 → 结构化约束 | 「5 岁孩子」= 亲子友好;「减肥」= 低脂;「别太远」= ≤5km |
| **规划** | 在候选地点 / 餐厅里挑组合,排时间路线 | 综合距离 / 营业时间 / 座位 / 年龄 / 忌口筛最优 |
| **执行** | 预约餐厅 / 买门票 / 加购服务 | 返回 mock 订单号;没位自动切备选 |
| **转发** | 写适合发给家人 / 朋友 / 客户的文案 | 按社交语境切换口吻 |

更多场景(闺蜜下午茶 / 商务接待 / 独处放空 / 带父母散步 / 跨代际聚餐)见 [`docs/01-requirements/演示场景集.md`](docs/01-requirements/演示场景集.md)。

## 核心特性

**意图理解 + 输入域路由** — LLM 前置把任意输入分到 6 类(planning / chitchat / meta / emotional / off_topic / ambiguous):非规划类给暖心回话 + 引导按钮,规划类进主路径解析成结构化 `IntentExtraction`。设计上 Tool 与 Agent 对场景类型无感——代码不含 `scene_type` / `relation_type` 之类硬编码分支,所有约束通过结构化参数传递。

**LangGraph 规划引擎** — `backend/agent/graph/` 用 LangGraph `StateGraph` 编排 15 个节点:router → 意图 / 反馈 → 并行 3 worker(POI / 餐厅 / 画像)→ 组装 → critic 校验 → 条件重规划 → 叙述。`InMemorySaver` checkpointer 按 `session_id` 跨 turn 持久化。`/chat/turn` 恒定走这一条图,无条件分支(旧 ReAct / rule 兜底路径已随 ADR-0012 决策 5 退役删除)。

**9 个 Tool(Function Calling)** — 全部按 OpenAI Function Calling JSON Schema 定义(Pydantic `model_json_schema()` 自动生成),统一注册表 + 输入输出双向 Pydantic 校验:

- 查询:`search_pois` · `search_restaurants` · `check_restaurant_availability` · `estimate_route_time`
- 执行:`reserve_restaurant` · `buy_ticket` · `order_extra_service` · `generate_share_message`
- 画像:`get_user_profile`

**双 planner 范式** — rule(确定性规则规划,demo 安全网,必出方案)与 llm(LLM 自主决策调哪个 Tool),顶栏 chip 一键切换,`X-Planner-Mode` header 透传。

**异常韧性** — mock 数据埋了多处失败点,规划循环自动重规划:餐厅满(`RESTAURANT_FULL`)→ 改约下个时段;门票售罄(`TICKET_SOLD_OUT`)→ 切备选 POI;候选为空(`EMPTY_CANDIDATES`)→ critic 逐级放宽标签重试。

**个性化** — 5 个 persona × memory 累积学习。同一句话对不同用户给不同方案;确认命中的 tag 累加到偏好画像,refine 拒绝的 tag 反向惩罚。前端「偏好画像」面板可视化 + 一键清空记忆。

**反馈重规划** — 用户拒绝方案 + 自然语言反馈 → refiner 合并新约束 → 复用主路径重规划,前端对比新旧方案差异。

**实时多人协作** — WebSocket 协作房间(`/ws/{room_id}`):房主建房、成员加入、各自提约束、对行程每段投票,规划事件实时广播给房间所有人。

**地图 + 行程衍生物** — 高德地图行程标注(Marker 编号 + 真实驾车路线,无 key 时降级为文字列表;安全 jscode 由后端 `/_AMapService` 代理注入,不暴露在前端);一键生成行程海报、Web Speech 语音播报、房间分享二维码。

**流式 + 可观测** — 全程 SSE 流式(手写解析,16 种事件类型),前端实时展示意图 / Tool 调用链 / AI 思考过程。内置 Logfire 接入位(配 `LOGFIRE_TOKEN` 即可云端查看完整链路 trace)。

## 快速开始

### 方式一:Docker 一键(推荐)

```bash
git clone <repo> && cd 美团AI\ Hackathon

# 可选:接真 LLM / 真地图(不配也能起,走 stub 默认值)
cp backend/.env.example backend/.env        # 填 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL

docker compose up --build
```

- 前端:http://localhost:3000
- 后端:http://localhost:8000/health · 就绪检查 http://localhost:8000/ready

三件套(Redis + 后端 + 前端)一起起;没有 `backend/.env` 也能跑(stub 模式)。

### 方式二:本地开发

前置:Python 3.11+ · Node 18+ · pnpm 9 · [uv](https://docs.astral.sh/uv/)

```bash
# 后端
cd backend && uv sync --extra runtime
uv run uvicorn main:app --port 8000

# 前端(新终端)
cd frontend && pnpm install && pnpm dev
```

- **stub 模式**(无需 LLM key,最快):后端设 `LLM_PROVIDER=stub`,前端点 8 个场景按钮即可跑通完整链路。
- **真 LLM 模式**(完整体验):`backend/.env` 填任意 OpenAI 兼容凭证:

```env
LLM_API_KEY=<your-key>
LLM_BASE_URL=https://api.deepseek.com/v1   # 或通义 / OpenAI / 智谱 / Ollama
LLM_MODEL=deepseek-chat
```

## 架构

```text
┌─ Web UI · frontend/ (Next.js 14 App Router) ───────────────────┐
│  29 组件 · 2 Zustand store · 手写 SSE 解析 · WebSocket · 高德地图  │
└────────────────────────── HTTP + SSE + WS ─────────────────────┘
┌─ 网关 · backend/main.py + api/ ────────────────────────────────┐
│  FastAPI · 9 router · /chat/turn(SSE) · /ws/{room}(WS)          │
│  /_AMapService(高德代理) · /scenarios /personas /preferences    │
└────────────────────────────────────────────────────────────────┘
┌─ Agent 编排 · backend/agent/ ──────────────────────────────────┐
│  graph/    LangGraph StateGraph,15 节点 + 跨 turn checkpointer  │
│  intent/   输入域路由 · 意图解析 · 反馈合并 · 叙述               │
│  planning/ LLM-First planner · rule / ils planner · critic      │
│  runtime/  工具适配层(search_adapter);旧 ReAct 运行时已退役      │
└────────────────────────────────────────────────────────────────┘
┌─ Tool 层 · backend/tools/ ─────────────────────────────────────┐
│  9 Tool,OpenAI Function Calling JSON Schema,统一注册表         │
└────────────────────────────────────────────────────────────────┘
┌─ 数据层 · backend/data/ + mock_data/ ──────────────────────────┐
│  loader(mock JSON) · provider 抽象(mock / gaode / …)          │
│  95 POI · 120 餐厅 · 215 路线 · 430 评论(内嵌字段)· 5 persona · 6 用户 │
└────────────────────────────────────────────────────────────────┘
```

数据源 / 持久化 / 认证都做了抽象,通过环境变量切换:`DATA_PROVIDER`(mock / gaode / dianping)、`NEARBY_PROVIDER`(mock / gaode / meituan)、`SESSION_STORE`(memory / redis)、`OAUTH_PROVIDER`(wechat / google / dingtalk)。demo 走 mock / memory / stub,真实 provider 已留接入位(其中 Redis 持久化已是真实现)。

## API 端点

| Method | 路径 | 作用 |
|--------|------|------|
| POST | `/chat/turn` | **SSE** 对话主入口,自动识别新需求 / 反馈,跨 turn 持久化 |
| POST | `/chat/confirm` | **SSE** 确认下单,派发执行类 Tool(预约 / 购票 / 加购 / 转发) |
| POST | `/chat/adjust` | **SSE** 单人节点定向调整 / 具名备选(ADR-0013 F-4) |
| WS | `/ws/{room_id}` | **WebSocket** 多人协作(约束 / 投票 / 确认) |
| POST · GET | `/room/create` · `/room/{id}/state` | 建房 / 房间状态快照 |
| GET | `/scenarios` | 8 个演示场景文案 |
| GET · POST | `/personas` · `/preferences/{user_id}` · `/preferences/{user_id}/reset` | 画像 / 偏好 / 清空记忆 |
| * | `/_AMapService/{path}` | 高德 REST 代理(注入 jscode) |
| GET | `/health` · `/ready` | 存活 / 就绪探针 |
| GET | `/legal/terms` · `/legal/privacy` | 用户协议 / 隐私政策 |
| GET | `/auth/info` · `/auth/{provider}/…` | OAuth 接入位状态(demo stub) |

完整契约(请求 / 响应 / SSE 事件序列)见 [`backend/api_contract.md`](backend/api_contract.md)。

## 项目结构

```text
.
├─ backend/                FastAPI + LangGraph
│  ├─ main.py              app 装配 + 9 router
│  ├─ api/                 health / scenarios / chat / adjust / collab / amap / preferences / legal / oauth
│  ├─ agent/
│  │  ├─ graph/            LangGraph StateGraph + nodes/
│  │  ├─ intent/           router · parser · refiner · narrator
│  │  ├─ planning/         planners(llm_first / rule / ils)· critic · blueprint
│  │  ├─ runtime/          tools/search_adapter(旧 ReAct 运行时已退役)
│  │  └─ core/             llm_client · prompt_guard · trace
│  ├─ tools/               9 个 Function Calling Tool + registry
│  ├─ schemas/             Pydantic v2 模型(intent / itinerary / sse / …)
│  ├─ data/                loader · nearby_provider · memory_store
│  ├─ auth/                OAuth provider 抽象
│  └─ tests/               1876 用例(pytest --collect-only 口径)
├─ frontend/               Next.js 14 App Router
│  ├─ app/                 page.tsx(首页)· room(协作房间静态入口)
│  ├─ components/          29 个组件
│  ├─ lib/                 store · collab-store · sse · ws · utils
│  └─ scripts/             clean-next · verify-all · pressure-test
├─ mock_data/              95 POI · 120 餐厅 · 215 路线 · 430 评论(内嵌字段)· 5 persona · 6 用户
├─ docs/                   需求 / 设计 / 商业 / 路演 / 法务
└─ docker-compose.yml      Redis + 后端 + 前端 一键起
```

## 配置

后端(`backend/.env`,完整见 `backend/.env.example`):

| 变量 | demo 默认 | 说明 |
|------|-----------|------|
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | — | OpenAI 兼容凭证;不填走 stub |
| `PLANNER_MODE` | `rule` | rule / llm |
| `SESSION_STORE` | `memory` | memory / redis(redis 已真实现) |
| `DATA_PROVIDER` / `NEARBY_PROVIDER` | `mock` | 数据源,真实 provider 留接入位 |
| `AMAP_REST_KEY` / `AMAP_JS_CODE` | — | 高德服务端 key + jscode(后端代理用) |
| `LOG_FORMAT` | `text` | text / json |
| `LOGFIRE_TOKEN` | (空) | 配后自动上传 trace |
| `OAUTH_PROVIDER` | (空) | wechat / google / dingtalk(stub) |

前端(`frontend/.env.local`):

| 变量 | 说明 |
|------|------|
| `NEXT_PUBLIC_API_BASE` | 后端地址,默认 `http://localhost:8000` |
| `NEXT_PUBLIC_BASE_PATH` | 静态部署子路径;GitHub Pages 为 `/shangwuju`,本地为空 |
| `NEXT_PUBLIC_AMAP_KEY` | 高德 JS key;不填地图降级为文字列表 |

## 测试

```bash
# 后端:1876 pytest 用例(--collect-only 口径)
cd backend && uv run pytest -q

# 前端:lint + typecheck + vitest + build 一键校验
cd frontend && pnpm verify:all

# 端到端:8 场景 SSE 压测(需后端先起;该脚本硬编码打已删除的 /chat/stream,
# 已知会全部 404,待清理——新验证请手动 curl /chat/turn)
node frontend/scripts/pressure-test-scenarios.mjs
```

CI(`.github/workflows/ci.yml`):后端 pytest · 前端 typecheck / test / build · Docker 镜像构建验证。

## 技术栈

| 层 | 选型 |
|----|------|
| LLM | 任意 OpenAI 兼容 base_url(DeepSeek / 通义 / OpenAI / 智谱 / Ollama) |
| 后端 | Python 3.11 · FastAPI · LangGraph 1.2 · Pydantic AI · Pydantic v2 · sse-starlette |
| 前端 | Next.js 14 App Router · TypeScript strict · Tailwind · Zustand · 高德 JS API |
| 数据 | mock JSON(provider 抽象,未接真实美团 / 高德数据 API) |
| 持久化 | 内存 / Redis(抽象切换) |
| 可观测 | structlog · Logfire(OpenTelemetry) |
| 包管理 | 后端 uv · 前端 pnpm |

## 文档

| 想了解 | 去哪看 |
|--------|--------|
| 项目当前进度 / 决策流水 | [`docs/00-overview/progress.md`](docs/00-overview/progress.md) |
| 文档入口说明 | [`docs/00-overview/如何使用这套文档.md`](docs/00-overview/如何使用这套文档.md) |
| 团队分工 | [`docs/00-overview/团队分工.md`](docs/00-overview/团队分工.md) |
| 需求分析 / MVP / 验收标准 / 演示场景 | [`docs/01-requirements/`](docs/01-requirements/) |
| 系统设计文档 | [`docs/05-design/设计文档.md`](docs/05-design/设计文档.md) |
| 数据源、持久化、观测性、商业化、小团接入 | [`docs/06-business/`](docs/06-business/) |
| 阿里云 FC 后端部署 | [`docs/06-business/02-阿里云FC部署.md`](docs/06-business/02-阿里云FC部署.md) |
| 路演大纲 | [`docs/07-pitch/路演大纲.md`](docs/07-pitch/路演大纲.md) |
| 交付说明 docx | [`docs/08-delivery/系统交付说明-简约版.docx`](docs/08-delivery/系统交付说明-简约版.docx) |
| 隐私政策 / 服务条款 | [`docs/legal/`](docs/legal/) |
| 后端 API 契约 | [`backend/api_contract.md`](backend/api_contract.md) |
| 前端实现说明 | [`frontend/README.md`](frontend/README.md) |
| 后端实现说明 | [`backend/README.md`](backend/README.md) |
| 技术陷阱与防再犯 | [`docs/03-implementation/pitfalls.md`](docs/03-implementation/pitfalls.md) |
| AI Agent 编码约定 | [`AGENTS.md`](AGENTS.md) |

## 部署

当前公开 Demo 的部署形态：

- 前端：GitHub Pages 静态导出，地址 <https://kaka-cheaper.github.io/shangwuju/>。
- 后端：阿里云函数计算 FC Custom Container，公网地址通过 GitHub Repository Variable 注入前端构建，不在文档中明文公开。
- 会话：比赛公开 Demo 暂用 `SESSION_STORE=memory` + FC Cookie 会话亲和，避免为短期展示额外购买 Redis；产品化部署仍建议切到 Redis/Tair。
- API 基址：GitHub Pages workflow 在构建时从仓库变量 `NEXT_PUBLIC_API_BASE` 注入；本地开发不注入时默认 `http://localhost:8000`。

GitHub Pages 部署前需要在仓库 `Settings → Secrets and variables → Actions → Variables` 配置 `NEXT_PUBLIC_API_BASE`。如果线上要显示高德地图，还需要配置 `NEXT_PUBLIC_AMAP_KEY`，并确保 FC 后端环境变量里有对应的 `AMAP_JS_CODE`。

后端容器仍监听 `0.0.0.0:$PORT`（FC 注入 9000），部署细节见 [`docs/06-business/02-阿里云FC部署.md`](docs/06-business/02-阿里云FC部署.md)。
