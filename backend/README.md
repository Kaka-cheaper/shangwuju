# 晌午局后端

> FastAPI + LangGraph 的「半日出行规划」Agent 后端。

用户在 App 里说一句话（如「今天下午想和老婆孩子出去玩几个小时，别离家太远」），后端通过一个多步 Agent 完成意图理解、候选发现、行程规划、方案校验与文案生成，并以 SSE 流式把规划过程和最终行程实时推给前端；用户确认后再派发预约餐厅、买票、加购、生成转发文案等执行类工具。

本目录是后端子项目，面向开发者。下文涵盖：怎么跑起来、对外 API、Agent 架构、Tool 层、数据层、环境变量、测试与目录结构。

---

## 1. 技术栈与版本

包管理使用 [uv](https://github.com/astral-sh/uv)。核心版本（取自 `pyproject.toml` 与 `uv.lock`）：

| 类别 | 组件 | 版本 |
|------|------|------|
| 运行时 | Python | >= 3.11 |
| Web 框架 | fastapi | 0.136.1 |
| ASGI 服务器 | uvicorn[standard] | 0.47.0 |
| SSE | sse-starlette | 3.4.4 |
| Agent 编排 | langgraph | 1.2.0 |
| LLM 适配 | langchain-openai | 1.2.1 |
| | langchain-core | 1.4.0 |
| ReAct Agent | pydantic-ai-slim[openai] | 1.97.0 |
| LLM SDK | openai | 2.37.0 |
| 数据模型 | pydantic | 2.13.4 |
| HTTP 客户端 | httpx[http2] | 0.28.1 |
| 结构化日志 | structlog | 25.5.0 |
| 可观测平台 | logfire | 4.33.0 |
| 会话持久化 | redis | 7.4.0 |
| 测试 | pytest | 9.0.3 |

`pyproject.toml` 把依赖分成三组：

- `dependencies`：核心算法依赖（pydantic / openai / langgraph / langchain / pydantic-ai 等），纯逻辑层、单测即可跑。
- `[optional-dependencies].runtime`：HTTP/SSE 网关运行时——`fastapi` / `uvicorn` / `sse-starlette` / `logfire` / `redis`。起服务必须装这一组。
- `[optional-dependencies].dev`：`pytest`。

---

## 2. 快速开始

前置：Python 3.11+、已安装 `uv`。

### 2.1 安装依赖

```bash
uv sync --extra runtime
```

> 只跑单测、不起服务时 `uv sync` 即可；起 HTTP 服务必须带 `--extra runtime`。

### 2.2 启动服务

```bash
uv run uvicorn main:app --port 8000
```

启动后：

- API 文档（Swagger UI）：http://localhost:8000/docs
- 健康探针：http://localhost:8000/health

### 2.3 两种运行模式

**Stub 模式（无需真 LLM key，最快）**——加载 `StubLLMClient` 返回固定 fixture，不调任何真 LLM，适合离线开发、联调、断网应急：

```bash
# bash
export LLM_PROVIDER=stub
uv run uvicorn main:app --port 8000
```

**真 LLM 模式**——把 `backend/.env.example` 复制成 `backend/.env`，填入任意 OpenAI 兼容凭证（DeepSeek / 通义 / OpenAI / 智谱 / Ollama 等都可）：

```env
LLM_API_KEY=<your-key>
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

只要填了 `LLM_API_KEY`，规划链路会自动走真 LLM（无需额外开关）。注意：缺 key 时**不会**自动回退 stub，而是构造真客户端并在网络层报错——这是刻意设计，避免「假装在工作」。需要显式离线请设 `LLM_PROVIDER=stub`。

`.env.example` 含全部可选参数（性能优化、算法常量、部署演进位）的详细说明。

---

## 3. 对外 API

所有端点在 `main.py` 接入，按业务域分布在 8 个 router 模块（`api/*.py`）。SSE 端点返回 `text/event-stream`，WS 端点用 WebSocket 协议。

### 业务核心：对话（`api/chat.py`）

| 方法 | 路径 | 类型 | 作用 |
|------|------|------|------|
| POST | `/chat/turn` | **SSE** | **主入口（推荐）**：一句话 → 规划事件流；自动识别「新需求」vs「对已有方案的反馈」，跨 turn 持久上下文 |
| POST | `/chat/confirm` | **SSE** | 用户确认方案 → 派发执行类工具（预约餐厅 / 买票 / 加购 / 生成转发文案），支持白名单防 hallucination |
| POST | `/chat/refine` | **SSE** | 独立反馈：给定 session 的反馈文本 → refiner 合并约束 → 重新规划 |
| POST | `/chat/stream` | **SSE** | 旧版主入口：一句话 → SSE 流式输出（保留供内部 e2e 测试；新接入用 `/chat/turn`） |

### 协作房间（`api/collab.py`）

| 方法 | 路径 | 类型 | 作用 |
|------|------|------|------|
| POST | `/room/create` | HTTP | 创建多人协作房间，可带入当前 session 的行程作为初始方案 |
| GET | `/room/{room_id}/state` | HTTP | 拉房间当前状态快照（WS 连接前预加载用） |
| WS | `/ws/{room_id}` | **WS** | 多人协作通道：上行加约束 / 投票 / 确认，下行广播房间事件 |

### 演示场景与用户偏好（`api/scenarios.py` / `api/preferences.py`）

| 方法 | 路径 | 类型 | 作用 |
|------|------|------|------|
| GET | `/scenarios` | HTTP | 拉 8 个演示场景的输入文案配置 |
| GET | `/personas` | HTTP | 拉所有 mock persona 列表（前端 user 切换器用） |
| GET | `/preferences/{user_id}` | HTTP | 读取某用户合并后的偏好画像（persona prior + 累积 memory） |
| POST | `/preferences/{user_id}/reset` | HTTP | 清空某用户的累积 memory（演示完清场用） |

### 高德地图代理（`api/amap.py`）

| 方法 | 路径 | 类型 | 作用 |
|------|------|------|------|
| GET/POST/... | `/_AMapService/{path:path}` | HTTP | 高德 REST API 透传代理 + 注入 `AMAP_JS_CODE`（jscode 只存后端，浏览器看不到） |

### 健康探活（`api/health.py`）

| 方法 | 路径 | 类型 | 作用 |
|------|------|------|------|
| GET | `/health` | HTTP | liveness 探针，返回 version / llm_provider / planner_mode / planner_real |
| GET | `/ready` | HTTP | readiness 探针，检查 LLM 配置 / Redis（如启用）/ mock 数据可加载，任一失败返 503 |

### 运营辅助（`api/legal.py` / `api/oauth.py`）

| 方法 | 路径 | 类型 | 作用 |
|------|------|------|------|
| GET | `/legal/terms` | HTTP | 用户协议（占位草案，真上线前需法务审核） |
| GET | `/legal/privacy` | HTTP | 隐私政策（占位草案） |
| GET | `/auth/info` | HTTP | 列出所有 OAuth provider 接入位与状态（demo 阶段全部 stub） |
| GET | `/auth/{provider}/authorize` | HTTP | 构造 provider 授权 URL（wechat / google / dingtalk；当前返友好 stub 提示） |
| GET | `/auth/{provider}/callback` | HTTP | provider 回调（当前返 stub 提示） |

完整请求/响应字段与 SSE 事件序列见 [`api_contract.md`](./api_contract.md)。

### SSE 事件类型（`schemas/sse.py`）

主要事件：`intent_parsed`、`tool_call_start`、`tool_call_end`、`replan_triggered`、`critic_violations`、`critic_fix_attempt`、`plan_fallback`、`agent_thought`、`itinerary_ready`、`agent_narration`、`refinement_start`、`refinement_done`、`chitchat_reply`、`memory_persisted`、`stream_error`、`done`。

---

## 4. Agent 架构

后端的规划能力由一个基于 **LangGraph `StateGraph`** 的多步 Agent 实现（`agent/graph/build.py`，约 15 个节点）。

### 拓扑（节点）

```
START
  → router ── 条件分支 ──┬── chitchat → END（闲聊 / 非规划意图）
                         ├── intent  ──┐
                         └── refiner ──┘
                                       ↓（并行 execute 阶段）
        search_pois_worker / search_restaurants_worker / get_user_profile_worker
                                       ↓ 汇聚
                                 execute_collect
                                       ↓
                                    planner（出权重 + 蓝图）
                                       ↓
                                   assemble（蓝图 → Itinerary）
                                       ↓
                                    critic（方案校验）
                            ┌── 通过 ──→ narrate → END
                            └── 硬违规 ──→ replan_router
                                         ├── llm_backprompt → planner（带 critic 反馈）
                                         ├── ils_fallback → ils_replan → narrate
                                         └── give_up → narrate
   （另有 execute_finalize 节点：确认下单路径）
```

- **Checkpointer**：编译时挂 `InMemorySaver`，`thread_id = session_id`，按 session 跨 turn 持久化对话状态（messages / blueprint 等）。serde 注册了业务 Pydantic 类型（Poi / Restaurant / IntentExtraction / Itinerary / PlanBlueprint 等）以消除反序列化警告。`get_compiled_graph()` 做模块级单例缓存。

### 分层

`agent/` 子目录按职责分层：

- `agent/intent/`：意图理解层——`router.py`（输入域分类）、`parser.py`（意图抽取）、`refiner.py`（反馈合并）、`narrator.py`（文案生成）。
- `agent/planning/`：规划算法层——`planners/`（rule / ILS / LLM-first 等多种规划器）、`critic/`（方案校验规则）、`blueprint/`（行程蓝图）、`commute/`（通勤可达性）、`weights_llm.py`（权重）、`memory_writer.py`。
- `agent/runtime/`：运行时层——`react_agent.py`（Pydantic AI ReAct Agent）、`orchestrator.py`（turn 路由与 ReAct 流式入口）、`conversation.py`（会话存储抽象，含 InMemory / Redis 两套实现）、`tool_provider.py`（数据源抽象）。
- `agent/graph/`：LangGraph 编排层——`build.py`（拓扑）、`nodes/`（各节点实现）、`sse_adapter.py`（graph 事件 → SSE）、`state.py`（AgentState）。
- `agent/core/`：基础设施——`llm_client.py`（OpenAI 兼容 LLM wrapper）、`llm_client_stub.py`（离线 stub）、`hedged_client.py`（主备双发治尾延迟）、`observability_init.py`（Logfire）、`injection_detector.py` / `prompt_guard.py`（防注入）。

### 三层 fallback（demo 永不翻车）

`/chat/turn` 按优先级尝试三条主路径，任一层不可用自动降级：

1. **LangGraph 主架构**（`USE_LANGGRAPH=1` 启用）：上述 StateGraph 全流程。
2. **ReAct 单一 Agent**（`USE_REACT_AGENT=1`，默认 ON；LangGraph 关闭或不可用时走）：基于 Pydantic AI，让 LLM 看到全部 9 个工具自主决策，critic 兜底用 `ModelRetry` 让 LLM 自纠错。
3. **rule planner**（最终兜底）：规则化 ReAct 主循环，纯算法零 LLM 调用，保证 demo 稳定。

`PLANNER_MODE` 控制规划范式（`rule` 规则化 / `llm` LLM Function Calling），可经 HTTP header `X-Planner-Mode` 覆盖。

---

## 5. Tool 层

9 个 Function Calling Tool，每个一个文件，统一签名 `(input: XxxInput) -> XxxOutput`（Pydantic 模型见 `schemas/tools.py`）。`tools/registry.py` 提供 `@register_tool` 装饰器，从 Pydantic v2 模型自动生成 **OpenAI Function Calling JSON Schema**（`{"type":"function","function":{"name","description","parameters"}}`），并维护全局 `TOOL_REGISTRY`。Agent 通过 `invoke_tool(name, raw_args)` 统一调用——不直接 import 单个 Tool；调用会做输入/输出双向 schema 校验，失败返回 `success=false + reason: FailureReason`，不抛业务异常。

| Tool | 作用 |
|------|------|
| `search_pois` | 按距离/标签查询活动地点（POI）候选 |
| `search_restaurants` | 按距离/标签/预算查询餐厅候选 |
| `check_restaurant_availability` | 查询餐厅在指定时段是否可订 |
| `estimate_route_time` | 估算两点间通勤时长 |
| `reserve_restaurant` | 预约餐厅（执行类） |
| `buy_ticket` | 购买 POI 门票（执行类） |
| `order_extra_service` | 下单附加服务，如蛋糕 / 鲜花（执行类） |
| `generate_share_message` | 生成行程转发文案（执行类） |
| `get_user_profile` | 读取用户画像（偏好 / 历史） |

---

## 6. 数据层

### Mock 数据（仓库根 `mock_data/`）

| 文件 | 规模 |
|------|------|
| `pois.json` | 51 个活动地点 |
| `restaurants.json` | 51 家餐厅 |
| `routes.json` | 288 条预算路线 |
| `personas.json` | 5 个 persona 画像 |
| `user_profiles.json` | 6 个用户画像 |
| `extra_services.json` | 5 种附加服务 |

加载入口在 `data/loader.py`（带缓存）；用户偏好（persona prior + 累积 memory）在 `data/memory_store.py`。可用 `SHANGWUJU_MOCK_DIR` 覆盖 mock 目录。

### Provider 抽象（部署演进位）

业务代码与具体数据源解耦，仅靠 env 切换 provider 即可接入真实业务接口：

| 抽象 | env | 取值 | 说明 |
|------|-----|------|------|
| 行程数据源（POI/餐厅/路线 Tool） | `DATA_PROVIDER` | `mock`（默认）/ `gaode` / `dianping` | `agent/runtime/tool_provider.py`；非 mock 当前为接入位 stub |
| 附近候选发现 | `NEARBY_PROVIDER` | `mock`（默认）/ `gaode` / `meituan` | `data/nearby_provider.py` |
| 会话持久化 | `SESSION_STORE` | `memory`（默认）/ `redis` | `agent/runtime/conversation.py`；`redis` 为真实现（`RedisRepository`），需 `--extra runtime` 装齐 redis 包 |
| 第三方登录 | `OAUTH_PROVIDER` | `wechat` / `google` / `dingtalk` | `auth/providers.py`；demo 阶段全部 stub |

---

## 7. 环境变量

复制 `.env.example` 为 `.env` 后填值。下表为常用变量（完整说明见 `.env.example`）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `LLM_API_KEY` | — | LLM 凭证（任意 OpenAI 兼容 endpoint）；不填则真 LLM 调用在网络层报错 |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | LLM endpoint |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `LLM_PROVIDER` | （自动推断） | 设 `stub` 走离线 fixture，不调真 LLM |
| `AMAP_REST_KEY` | — | 高德服务端 REST API key（地理编码 / 路线） |
| `AMAP_JS_CODE` | — | 高德 JS API 安全密钥，经 `/_AMapService` 代理注入 |
| `USE_LANGGRAPH` | `0` | `1` 时 `/chat/turn` 优先走 LangGraph 主架构 |
| `USE_REACT_AGENT` | `1` | LangGraph 关闭时是否走 ReAct 单一 Agent |
| `PLANNER_MODE` | `rule` | 规划范式：`rule` / `llm`（可被 `X-Planner-Mode` header 覆盖） |
| `DATA_PROVIDER` | `mock` | 行程数据源：`mock` / `gaode` / `dianping` |
| `NEARBY_PROVIDER` | `mock` | 附近候选源：`mock` / `gaode` / `meituan` |
| `SESSION_STORE` | `memory` | 会话存储：`memory` / `redis` |
| `REDIS_URL` | — | `SESSION_STORE=redis` 时的连接串 |
| `LOG_FORMAT` | `text` | 日志格式：`text`（开发）/ `json`（生产） |
| `LOGFIRE_TOKEN` | — | 配了走 Logfire 云端，不配降级本地控制台 |
| `OAUTH_PROVIDER` | `wechat` | OAuth 默认 provider |
| `LLM_TIMEOUT_S` / `LLM_MAX_RETRIES` | `30` / `2` | LLM 全局调用参数 |
| `SHANGWUJU_MOCK_DIR` | （仓库根 `mock_data/`） | mock 数据目录覆盖 |

`.env.example` 另含一批默认 OFF 的性能优化（HTTP/2 连接池、路由 fast path、任务级模型路由、主备双发）与算法可调常量（critic 反馈模式、replan 重试上限、grounding 候选过滤、ILS 参数等）。

### Redis 持久化（`SESSION_STORE=redis`）

默认 `memory`：单进程内存、零外部依赖、进程重启即清空——本地裸机开发 / 单实例 demo 足够。
设 `SESSION_STORE=redis`（配合 `REDIS_URL`）后，三类跨 turn 状态分别外置到 Redis：

| 状态 | 落地方式 | Redis 要求 |
|------|----------|-----------|
| 跨 turn 对话上下文（LangGraph 主路径，`USE_LANGGRAPH=1`） | `AsyncRedisSaver` checkpointer（`thread_id=session_id`，启动时 `warm_up_graph()` 建索引） | **需 Redis Stack（RediSearch ≥ 2.10）** |
| 会话快照（confirm / refine / 协作初始行程取用） | `api/_session_store.py` 写时镜像 + 启动 `warm_from_redis()` 预热 | 普通 Redis 即可 |
| 对话仓库（ReAct 旧路径 `USE_REACT_AGENT=1`） | `RedisRepository`（`SET`/`GET` JSON envelope，TTL 24h，confirm 续期 7d） | 普通 Redis 即可 |

- **checkpointer 需 Redis Stack**（含 RediSearch 模块）。普通 `redis:7-alpine` 上 `asetup()` 会失败，此时自动**优雅降级**回 `InMemorySaver`（打 warning、不影响功能，但对话上下文不落 Redis）；要完整持久化用 `redis/redis-stack-server` 镜像。
- **协作房间是单实例**：`collab/room.py` 的 `Room` 含 WebSocket 连接 / `asyncio.Task` 等进程内对象，不外置；多实例实时协作需额外 pub/sub 架构（不在当前范围）。
- redis 相关代码全部 **gated + 懒导入**：`memory` 模式不会 `import` / 连接 redis，行为与未引入 redis 完全一致——本地裸机跑不受任何影响。

docker compose 启用（叠加 `docker-compose.redis.yml`，换 Redis Stack 镜像 + 打开 redis 模式）：

```bash
docker compose -f docker-compose.yml -f docker-compose.redis.yml up --build
```

验证：起 redis 后发起一轮对话 → 确认下单 → **重启后端进程** → 用同 `session_id` 再访问，
确认会话快照与对话上下文仍在（checkpointer 的上下文恢复需 Redis Stack 环境）。

---

## 8. 测试

```bash
uv run pytest -q
```

`tests/` 当前约 **65 个测试文件、620+ 个 test 函数**，覆盖 schema 契约、各 Tool、规划算法、critic、SSE 序列、会话存储、协作房间、Agent 各路径等。

`scripts/` 下还有一批端到端 verify 脚本（如 `verify_schemas.py`、`verify_sse.py`、`verify_langgraph.py`、`verify_react_agent.py`、`verify_router.py`、`verify_planning.py`、`verify_tool_provider.py`、`verify_collab.py` 等），可单独运行验证某条链路：

```bash
uv run python -m scripts.verify_schemas
uv run python -m scripts.verify_sse
```

---

## 9. 目录结构

```
backend/
├── main.py                # FastAPI 入口：实例化 app + 接入 8 个 router + Logfire 探针
├── api_contract.md        # HTTP + SSE 接口契约（前后端共读权威）
├── pyproject.toml         # uv 包管理 + 依赖分组（core / runtime / dev）
├── .env.example           # 环境变量完整说明
│
├── api/                   # HTTP/SSE/WS 层：8 个 router 模块
│   ├── chat.py            # /chat/turn|confirm|refine|stream（SSE）
│   ├── collab.py          # /room/* + WS /ws/{room_id}
│   ├── scenarios.py       # /scenarios
│   ├── preferences.py     # /personas, /preferences/*
│   ├── amap.py            # /_AMapService/* 高德代理
│   ├── health.py          # /health, /ready
│   ├── legal.py           # /legal/*
│   ├── oauth.py           # /auth/*
│   ├── _streams/          # chat 端点共用的 SSE 流实现
│   └── _session_store.py  # 跨 router 共享的内存 session 快照
│
├── agent/                 # Agent 编排（核心）
│   ├── graph/             # LangGraph StateGraph：build.py + nodes/ + sse_adapter.py + state.py
│   ├── intent/            # router / parser / refiner / narrator
│   ├── planning/          # planners / critic / blueprint / commute / weights
│   ├── runtime/           # react_agent / orchestrator / conversation / tool_provider
│   └── core/              # llm_client / hedged_client / observability / 防注入
│
├── tools/                 # 9 个 Function Calling Tool + registry.py 注册中心
│
├── schemas/               # Pydantic v2 契约（intent / itinerary / tools / domain / sse / ...）
│
├── data/                  # loader.py（mock 加载）/ memory_store.py / nearby_provider.py
│
├── auth/                  # OAuth provider 接入位（wechat / google / dingtalk，stub）
│
├── collab/                # 协作房间业务逻辑（RoomManager / Room）
│
├── scripts/               # 端到端 verify 脚本 + mock 数据生成/迁移工具
│
└── tests/                 # pytest（约 65 文件 / 620+ 用例）
```
