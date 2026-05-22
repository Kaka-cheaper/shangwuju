# 晌午局 shangwuju

> 一句话搞定下午行程。

美团 AI Hackathon 命题赛道 06「本地探索 · 周末闲时活动规划」参赛作品。

用户说一句话 → AI Agent 编排 Tool → 输出可执行 + 可转发的下午行程。

```text
用户输入「今天下午陪老婆孩子出去玩，孩子 5 岁，老婆减肥」
   ↓
LLM 前置 6 类输入路由（planning / chitchat / meta / emotional / off_topic / ambiguous）
   ↓ planning
意图解析（注入 persona prior + memory）→ §5.7 IntentExtraction
   ↓
ReAct 规划循环（rule mode 或 LLM Function Calling 自主调用）
   ↓ 8 个 Tool 编排（POI/餐厅/路线/可用性/预约/购票/用户画像/转发文案）
   ↓ 异常自动重规划（餐厅满 / 门票售罄）
六段行程（出发 / 主活动 / 转场 / 用餐 / 附加 / 返回）
   ↓ 用户确认后执行
预约下单 + 转发文案一键复制
```

## 当前状态

```
| 阶段              | 完成度  | 说明                                                        |
|-------------------|---------|-------------------------------------------------------------|
| MVP-1             | 100% ✅ | 6 Tool + 主场景闭环 + E1 异常显式触发 + Web UI + SSE          |
| MVP-2             | 95%  ✅ | 8 Tool / 8 场景全跑通 / 用户确认 / 双 planner mode / 反馈重规划 |
| MVP-2.5 LLM 解耦  | 100% ✅ | 任意 OpenAI 兼容 base_url（DeepSeek/通义/OpenAI/智谱/Ollama 等）|
| MVP-3 个性化       | 100% ✅ | 5 persona prior 注入 + memory 累积学习 + 偏好画像面板（P0.7）|
| MVP-3 输入域路由   | 100% ✅ | LLM 前置 6 类分类 + 暖心气泡 + 引导按钮（Phase 0.8）         |
| MVP-3 演示         | 阻塞    | 真 LLM 链路已实测；剩录屏 3 版本 + 现场 dry run               |
```

## 一键部署

> 评委 / 新同事 git clone 后，**两分钟**跑起来。

```bash
git clone <repo>
cd 美团AI\ Hackathon

# 1. 复制配置（按需填 LLM key）
cp backend/.env.example backend/.env
cp frontend/.env.local.example frontend/.env.local

# 2. 一键起栈（Redis + 后端 + 前端 三件套）
docker compose up --build

# 3. 访问
#    前端：http://localhost:3000
#    后端：http://localhost:8000/health
#    就绪：http://localhost:8000/ready  ← 看 LLM/Redis/mock_data 探活
```

**架构层抽象都已就位，比赛后切真实数据源**：

```
环境变量切换                          | 默认值（demo） | 真上线值
--------------------------------------|--------------|-------------------------
LLM_PROVIDER                          | stub / 真 LLM | deepseek / qwen
SESSION_STORE                         | memory       | redis（已是真实现，非 stub）
NEARBY_PROVIDER（附近 POI 搜索）        | mock         | gaode / meituan
DATA_PROVIDER（数据源）                 | mock         | gaode / dianping
LOG_FORMAT                            | text         | json
LOGFIRE_TOKEN                         | （留空）      | lf_xxx（自动上传 trace）
OAUTH_PROVIDER                        | （留空）      | wechat / google / dingtalk
```

**生产部署路径**：阿里云函数计算 FC Custom Container + Redis Tair + ACR 镜像仓库。详见 [`docs/06-business/02-阿里云FC部署.md`](docs/06-business/02-阿里云FC部署.md)。

**可观测**：内置 [Logfire](https://logfire.pydantic.dev) 接入位（Pydantic 出品 OTEL 平台），自动 instrument Pydantic AI / OpenAI / FastAPI / httpx；配 `LOGFIRE_TOKEN` 后每次规划完整链路云端可见。

**法务**：[`docs/legal/`](docs/legal/) 已就位用户协议 + 隐私政策占位草案（真上线前需律师审核）。后端 `/legal/terms` `/legal/privacy` 端点直接 serve markdown。

**OAuth**：[`backend/auth/providers.py`](backend/auth/providers.py) 已就位 wechat / google / dingtalk 三个 provider 抽象，每个含「真接入步骤」锚点；GET `/auth/info` 列出当前状态。

**测试矩阵**：155 后端 pytest + 30 前端 vitest + 13 verify_refine + 7 verify_router = 205 项全过

**真 LLM 链路实测**：MimMo (mimo-v2.5-pro) 浏览器端到端跑通——意图解析 / 双 mode / 反馈重规划 / persona 切换 / memory 学习 / 输入域路由全部过线。

详见 [`docs/00-overview/progress.md`](docs/00-overview/progress.md)。

## 5 分钟跑起来

### 前置

- Python 3.11+
- Node 18+
- pnpm 8+
- `uv`（Python 包管理）：`pip install uv` 或参考 [uv 安装文档](https://docs.astral.sh/uv/getting-started/installation/)

### Stub 模式（最快路径，无需 LLM API key）

```bash
# 1. 后端
cd backend
uv sync
$env:LLM_PROVIDER='stub'   # PowerShell；Bash 用 export
uv run uvicorn main:app --port 8000

# 2. 前端（新开终端）
cd frontend
pnpm install
pnpm dev
```

打开 http://localhost:3000 ，点 8 个场景按钮任一即可看到完整链路（约 10 秒一条 demo）。

### 真 LLM 模式（推荐，体验完整）

`backend/.env` 填一份 OpenAI 兼容凭证（任意服务都行）：

```env
LLM_API_KEY=<your-key>
LLM_BASE_URL=https://api.deepseek.com/v1     # 或通义/OpenAI/智谱/Ollama 等
LLM_MODEL=deepseek-chat
PLANNER_USE_REAL=1
```

然后照常 `uv run uvicorn main:app --port 8000`，前端不动。在浏览器里输入「我累死了」「你是谁」「1+1=?」试试输入域路由的暖心气泡。

### 一键校验

```bash
# 后端：155 项 pytest
cd backend && uv run pytest -q

# 前端：lint + typecheck + vitest + build
cd frontend && pnpm verify:all

# 端到端：8 场景 SSE 压测（需后端先起来）
node frontend/scripts/pressure-test-scenarios.mjs

# 端到端：输入域路由（stub 模式）
cd backend && uv run python -m scripts.verify_router

# 端到端：反馈重规划
cd backend && uv run python -m scripts.verify_refine
```

## 核心特性

### 1. 输入域 LLM 前置路由（Phase 0.8 · 演示加分项）

评委即兴扔的输入也能优雅处理。LLM 一次性产出**结构化**输出（input_kind + 暖心回话 + 可点击引导按钮）：

```text
| 输入类型     | 示例           | Agent 响应                        |
|--------------|----------------|-----------------------------------|
| planning     | 「带老婆孩子出去玩」 | 进主路径（意图解析 → 8 Tool 编排）|
| chitchat     | 「你好」          | 暖心问候 + 引导按钮               |
| meta         | 「你是谁」        | 自我介绍 + 3 个引导按钮            |
| emotional    | 「我累死了」      | 共情回话 + 推荐独处场景            |
| off_topic    | 「1+1=?」        | 婉拒 + 拉回主路径                 |
| ambiguous    | 「出去玩」        | 反问澄清 + 引导按钮选            |
```

引导按钮的 `send` 字段经白名单校验（防 LLM 发明输入文本污染下游）。失败兜底链路三级：LLM 抛错 → 关键词 fast path → fallback_decision 返 PLANNING。

### 2. 个性化（Phase 0.7）

5 个 mock persona × memory 累积学习。同一句话「今天下午想出去玩」对不同 user 出完全不同方案：

```text
| 当前 user      | 输出方案                              |
|----------------|---------------------------------------|
| 新手爸爸 u_dad | 亲子绘本馆 + 健康简餐（亲子+低脂注入）  |
| 商务白领 u_biz | 商务茶室 + 高人均日料（商务接待走向）   |
| 退休阿姨 u_grandma | 适老 POI + 软烂餐厅（老人伴助走向） |
```

confirm 后命中 tag 累计 `accepted_tags`；refine 拒绝的 tag 累计 `rejected_tags`（1.5× 强惩罚）。前端「偏好画像」面板可视化 top 5 + 一键清空记忆。

### 3. 双 planner 范式（rule + llm）

- **rule mode**：顺序确定的 ReAct 规则规划（demo 安全网，必出方案）
- **llm mode**：LLM Function Calling 自主决策调哪个 Tool（评分项 4 加分，失败自动 fallback 到 rule）

顶栏点 chip 一键切换；header `X-Planner-Mode` 透传到响应。

### 4. 反馈重规划（Phase 0.6）

用户拒绝方案 + 自然语言反馈 → refiner 合并新约束 → 复用主路径重规划。

```text
原方案太远 → 用户反馈「太远了希望 3 公里以内」
   ↓ refiner 合并：distance 5km → 3km
   ↓ 复用主路径事件序列重新跑
新方案：候选压到 3km 内（仅 P007 童趣沙池公园 2.8km）
```

### 5. 异常韧性（评分项 5 核心证据）

至少 9 处 mock 数据失败埋点：

- **E1 RESTAURANT_FULL**：R001 17:00 满 → 自动改约 17:30
- **E2 TICKET_SOLD_OUT**：P_SOLD 售罄 → 切换备选 POI
- **E3 EMPTY_CANDIDATES**：5 级降级（剥离 prior tag 重试到候选非空）

## 架构（4 层 + D9 硬条款）

```text
┌─────────────────────────────────────────────────────┐
│  Web UI 层（frontend/）                              │
│  Next.js 14 + Tailwind + Zustand + 自手写 SSE 解析   │
│  组件：HomeView / QuickScenarios / ChatPanel /       │
│       ChitchatBubble / IntentSummary /              │
│       ToolTracePanel / ItineraryCard /              │
│       RefinementDialog / PlannerModeBadge /         │
│       UserSwitcher / PreferencesPanel               │
├─────────────────────────────────────────────────────┤
│  HTTP / SSE 网关（backend/main.py）                  │
│  FastAPI + sse-starlette：/chat/stream /chat/refine  │
│  /chat/confirm /scenarios /personas /preferences     │
├─────────────────────────────────────────────────────┤
│  Agent 编排层（backend/agent/）                      │
│  router.py（输入域路由）→ intent_parser.py（意图解析） │
│  → planner.py（rule mode）/ llm_planner.py（llm mode）│
│  → refiner.py（反馈合并）→ executor.py（执行类 Tool） │
│  Tracer 实时事件流→SSE                               │
├─────────────────────────────────────────────────────┤
│  Tool 层（backend/tools/）                           │
│  8 个 Tool 严格按 OpenAI Function Calling JSON Schema│
│  search_pois / search_restaurants /                 │
│  check_restaurant_availability / estimate_route_time│
│  / reserve_restaurant / buy_ticket /                │
│  generate_share_message / get_user_profile          │
├─────────────────────────────────────────────────────┤
│  Mock 数据层（mock_data/）                           │
│  21 POI + 30 餐厅 + 56 路线 + 5 persona 静态 JSON    │
│  9+ 处失败埋点；7 种 social_context 走向覆盖         │
└─────────────────────────────────────────────────────┘
```

**D9 硬条款**：Tool 与 Agent 对场景类型完全无感——代码里**禁止**出现 `scene_type` / `relation_type` / `if scene == "family"`，所有约束通过参数传递。

## 8 个演示场景（demo 现场快捷按钮）

```
| ID | 场景         | 触发约束                          |
|----|--------------|-----------------------------------|
| S1 | 家庭主线     | 5 岁孩 + 减肥老婆 + 距离近          |
| S2 | 朋友 4 人     | 2 男 2 女 + 热闹                   |
| S3 | 情侣看展     | 安静 + 拍照友好                    |
| S4 | 带父母散步   | 老人 + 无台阶 + 软烂                |
| S5 | 闺蜜下午茶   | 网红 + 拍照                        |
| S6 | 商务接待     | 商务体面 + 包间                    |
| S7 | 独处放空     | 安静 + 独处舒缓                    |
| S8 | 跨代际纪念日 | 全家 6 人 + 妈妈生日 + 粤菜         |
```

8 场景共用同一套 Tool / Agent 代码（D9 验证证据）。

## 文档导航

```
| 我想知道                   | 去哪看                                                   |
|----------------------------|----------------------------------------------------------|
| 题目原文                   | 比赛详情.md                                              |
| 产品逻辑（人类友好）        | 项目说明.md                                              |
| 架构选型记录                | docs/01-requirements/架构选型.md                         |
| 当前进度                   | docs/00-overview/progress.md                            |
| 必须做什么、优先级          | docs/01-requirements/需求分析.md                         |
| 第一版做到什么算完          | docs/01-requirements/MVP定义.md                          |
| 验收证据                   | docs/01-requirements/验收标准.md                         |
| 8 演示场景输入文案          | docs/01-requirements/演示场景集.md                       |
| 设计文档（≤2 页）           | docs/05-design/设计文档.md                               |
| 已知踩过的坑                | docs/03-implementation/pitfalls.md                       |
| 上次某问题怎么处理的        | problem.md（会话日志）                                   |
| AI Agent 编码铁律           | AGENTS.md                                                |
| 前端启动 / 录屏脚本         | frontend/README.md                                       |
| API 契约（HTTP + SSE 序列） | backend/api_contract.md                                  |
| 怎么用这套文档              | docs/00-overview/如何使用这套文档.md                     |
```

## 团队协作

3 人 · 1 个月时间盒（至 2026-06-08）。开发流程已收敛为「写代码 → 测试 → CodeSee sync → git commit」默认流水线。

详见 [`docs/00-overview/团队分工.md`](docs/00-overview/团队分工.md)。

## 技术栈（已锁定）

```
| 层      | 选型                                                        |
|---------|-------------------------------------------------------------|
| LLM     | 任意 OpenAI 兼容 base_url（DeepSeek/通义/OpenAI/智谱/Ollama）|
| 后端     | Python 3.11+ / FastAPI / Pydantic v2 / sse-starlette         |
| 前端     | Next.js 14 App Router / TypeScript strict / Tailwind / Zustand|
| 数据     | Mock JSON（不接真实美团/高德/支付 API，按赛题要求）            |
| 包管理   | 后端 uv / 前端 pnpm                                          |
| 状态可视 | CodeSee（.codesee/features.json，25 features）               |
```


## 产品化路线图

晌午局不是「能跑就行」的 Demo——三层抽象已经在 Demo 阶段就位，上线工作量已经被工程化预估。

**Demo 阶段（现在）**：8 场景端到端跑通 + 9+ 处异常显式触发 + persona × memory 双驱动个性化 + LLM 解耦（任意 OpenAI 兼容 base_url 三件套切换）。数据走 `mock_data/` 静态 JSON、持久化走单进程 dict、观测性走 structlog text format。

**MVP 阶段（1-2 月）**：切高德 Web Service POI（个人开发者免费 30 万次/月）+ Redis 跨实例共享 + Sentry 错误监控 + structlog json format。三层抽象（`backend/agent/v2/conversation.py` `tool_provider.py` `observability.py`）已在 Demo 阶段就位（含 `MockToolProvider` + `Gaode/Dianping` 接入位 + `trace_span` + `LOG_FORMAT` 切换）。8 场景端到端测试走的是抽象接口，切换工作量预估 ~14h（数据源）+ ~13h（持久化）。

**真产品阶段（3-6 月）**：高德 + 大众点评开放 API + 直签商家 webhook 三源混合 + PostgreSQL 用户行为分析 + OpenTelemetry 完整 trace。商业模式分阶段叠加：先免费验证 PMF → 流量分发（餐厅广告位）→ 抽佣（5%）+ 订阅（¥30/月）+ 美团生态合作。详见 [`docs/06-business/`](docs/06-business/) 的六篇商业演进文档与 [`docs/07-pitch/路演大纲.md`](docs/07-pitch/路演大纲.md)。
