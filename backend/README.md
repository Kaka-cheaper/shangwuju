# backend —— FastAPI + Pydantic v2 + SSE 网关

> 晌午局后端。Phase 0.8 完成（输入域路由 + 暖心气泡）。

## 启动方式

### Stub 模式（无需 LLM API key，最快）

```bash
cd backend
uv sync
$env:LLM_PROVIDER='stub'   # PowerShell；Bash 用 export LLM_PROVIDER=stub
uv run uvicorn main:app --port 8000
```

### 真 LLM 模式

`.env` 填一份 OpenAI 兼容凭证（任意服务都行）：

```env
LLM_API_KEY=<your-key>
LLM_BASE_URL=https://api.deepseek.com/v1   # 或通义/OpenAI/智谱/Ollama 等
LLM_MODEL=deepseek-chat
PLANNER_USE_REAL=1     # 显式启用真 planner（缺省也会按 base_url + key 自动启用）
```

`.env.example` 里有完整可选参数说明。

## 目录结构

```
backend/
├── agent/                    # Agent 编排层（A 同学 owner）
│   ├── router.py             # Phase 0.8 输入域 LLM 前置 6 类分类器
│   ├── intent_parser.py      # §5.7 IntentExtraction 抽取（含 persona prior 注入）
│   ├── planner.py            # rule mode ReAct 主循环 + plan_itinerary_with_mode 双范式入口
│   ├── llm_planner.py        # llm mode：LLM Function Calling 自主决策
│   ├── refiner.py            # 反馈合并（Phase 0.6）
│   ├── executor.py           # 用户确认后执行类 Tool 派发
│   ├── trace.py              # Tracer 内部事件流（→SSE）
│   ├── llm_client.py         # OpenAI 兼容 LLM wrapper（任意 base_url）
│   ├── llm_client_stub.py    # 单测 / 离线兜底 stub
│   └── prompts/              # system prompt + few-shot
├── data/                     # mock_data 加载与 memory 累积
│   ├── loader.py             # 静态 JSON 加载（缓存）
│   └── memory_store.py       # persona prior + memory 累积
├── schemas/                  # Pydantic v2 契约（D-SoT）
│   ├── intent.py             # §5.7 IntentExtraction
│   ├── itinerary.py          # 六段 Itinerary
│   ├── tools.py              # 8 Tool 输入输出
│   ├── domain.py             # POI / Restaurant / Route / UserProfile
│   ├── tags.py               # 三类 tag 词典
│   ├── errors.py             # FailureReason 枚举
│   ├── refine.py             # 反馈重规划（Phase 0.6）
│   ├── planner_mode.py       # rule / llm 双范式
│   ├── persona.py            # Persona / Memory（Phase 0.7）
│   ├── router.py             # InputKind / RouterDecision（Phase 0.8）
│   └── sse.py                # SseEventType + SseEvent
├── tools/                    # 8 个 Tool 实现（C 同学 owner）
│   ├── search_pois.py
│   ├── search_restaurants.py
│   ├── check_restaurant_availability.py
│   ├── estimate_route_time.py
│   ├── reserve_restaurant.py
│   ├── buy_ticket.py
│   ├── generate_share_message.py
│   ├── get_user_profile.py
│   └── registry.py           # Function Calling 注册中心
├── tests/                    # pytest（155 项）
├── scripts/                  # 端到端验证脚本
│   ├── verify_schemas.py     # schema 自检 6 项
│   ├── verify_phase0_5.py    # Phase 0.5 并行基座 8 项
│   ├── verify_sse.py         # SSE 网关序列
│   ├── verify_refine.py      # 反馈重规划 13 项
│   └── verify_router.py      # 输入域路由 7 项（Phase 0.8）
├── main.py                   # FastAPI 入口（4 端点 + SSE）
├── api_contract.md           # HTTP + SSE 契约（前后端共读）
├── pyproject.toml            # uv 包管理 + 入口配置
└── .env.example              # 环境变量完整说明
```

## HTTP 端点

```
| 方法 + 路径             | 用途                                        |
|------------------------|---------------------------------------------|
| GET  /health           | 健康检查 + planner_mode + llm_provider 展示  |
| GET  /scenarios        | 8 个演示场景配置                             |
| GET  /personas         | 5 个 mock persona 列表（Phase 0.7）          |
| GET  /preferences/{id} | 合并 persona + memory 偏好画像                |
| POST /preferences/{id}/reset | 清空某 user 的累积 memory             |
| POST /chat/stream      | 主入口：一句话 → SSE 流式输出                 |
| POST /chat/refine      | 反馈重规划（Phase 0.6）                      |
| POST /chat/confirm     | 用户确认后执行                               |
```

详细字段与 SSE 事件序列见 `api_contract.md`。

## 一键校验

```bash
uv run pytest -q                               # 155 项后端测试
uv run python -m scripts.verify_schemas        # schema 自检
uv run python -m scripts.verify_phase0_5       # 并行基座
uv run python -m scripts.verify_sse            # SSE 网关序列
uv run python -m scripts.verify_refine         # 反馈重规划（13）
uv run python -m scripts.verify_router         # 输入域路由（7，stub）
```

## SSE 事件类型（schemas/sse.py）

```
| 事件类型           | payload 形态                                  |
|--------------------|-----------------------------------------------|
| intent_parsed      | IntentExtraction.model_dump()                 |
| tool_call_start    | { tool, input }                               |
| tool_call_end      | { tool, output, duration_ms }                 |
| replan_triggered   | { reason: FailureReason, from_tool }          |
| agent_thought      | { text }                                      |
| itinerary_ready    | Itinerary.model_dump()                        |
| refinement_start   | { feedback_text }                             |
| refinement_done    | RefinementOutput.model_dump()                 |
| chitchat_reply     | RouterDecision.model_dump()  （Phase 0.8）    |
| stream_error       | { reason, detail }                            |
| done               | {}                                            |
```

## D9 硬条款（不得违反）

代码里**禁止**出现 `scene_type` / `relation_type` / `is_family` / `if scene == "family"` 这类场景枚举。所有约束通过参数（physical / dietary / experience tag + companions + distance）传递。

CI 风格的反向 grep gate（提交前自检）：

```bash
# 应当返 0 行
grep -rE "scene_type|relation_type|is_family" backend/agent/ backend/tools/ backend/schemas/
```

## 关键设计决策（详见 docs/01-requirements/架构选型.md）

```
| 决策 ID | 内容                                                       |
|---------|------------------------------------------------------------|
| D1      | LLM：任意 OpenAI 兼容 base_url（DeepSeek / 通义 / OpenAI / 智谱 / Ollama） |
| D2      | 前端：Next.js 14 App Router + TypeScript strict + Tailwind |
| D6      | 后端：FastAPI + Pydantic v2 + sse-starlette                |
| D7      | 目录：backend / frontend / mock_data / docs / tests        |
| D9      | 场景策略：开放语义底层 + 6-8 演示场景；Tool/Agent 对场景类型无感 |
| D-SoT   | 需求分析.md §5.7 IntentExtraction 锁定为唯一字段权威        |
```

## 当前完成度（progress.md 快照）

```
| 阶段                | 完成度  |
|---------------------|---------|
| Phase 0 契约基座    | 100% ✅ |
| Phase 0.5 并行基座  | 100% ✅ |
| Phase 0.6 反馈重规划| 100% ✅ |
| Phase 0.7 个性化    | 100% ✅ |
| Phase 0.8 输入域路由| 100% ✅ |
```

测试矩阵：155 后端 + 30 前端 + 13 verify_refine + 7 verify_router = 205 项全过。
