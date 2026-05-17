# 后端 HTTP/SSE 接口契约

> 本文件是后端（A/C）与前端（B）之间的**唯一权威接口定义**。
> Pydantic 模型已在 `backend/schemas/` 落，本文件描述 HTTP 路径、方法、状态码、SSE 事件序列。
>
> 修改纪律：任何字段改动先改 `backend/schemas/`，再同步本文件，再 grep 前端代码。

---

## 路径总览

```text
| 方法 | 路径                  | 用途                          | 实现 owner       |
|------|-----------------------|-------------------------------|------------------|
| GET  | /health               | 健康检查（前端启动探活）      | P3 W3            |
| POST | /chat/stream          | 主入口：一句话 → SSE 流式输出 | P2 A + P3 W3     |
| POST | /chat/confirm         | MVP-2：用户点确认后下发执行   | P2 A             |
| GET  | /scenarios            | 拉取 8 个演示场景的快捷输入   | P3 W3 静态文件   |
```

---

## 1. GET /health

**响应**（200）：

```json
{ "status": "ok", "version": "0.1.0", "llm_provider": "deepseek" }
```

---

## 2. POST /chat/stream（主入口）

### 请求体

```json
{
  "message": "今天下午想和老婆孩子出去玩几个小时...",
  "session_id": "sess_20260516_001",
  "scenario_id": "S1"
}
```

字段：

```text
| 字段        | 必填 | 说明                                                  |
|-------------|------|-------------------------------------------------------|
| message     | 是   | 用户一句话，长度 1-500                                |
| session_id  | 是   | 前端生成（uuid 或时间戳），用于幂等 + 关联 confirm    |
| scenario_id | 否   | 仅当点击演示快捷按钮时填，如 "S1"~"S8"；自由输入时不填|
```

### 响应

`Content-Type: text/event-stream`，遵循 SSE 协议。
**事件序列**（按时间顺序）：

```text
seq 0  intent_parsed
seq 1  tool_call_start (tool=get_user_profile)
seq 2  tool_call_end   (tool=get_user_profile)
seq 3  tool_call_start (tool=search_pois)
seq 4  tool_call_end   (tool=search_pois)
seq 5  agent_thought   (text="筛选 5 岁适配...")    # 可选
seq 6  tool_call_start (tool=search_restaurants)
seq 7  tool_call_end   (tool=search_restaurants)
seq 8  tool_call_start (tool=check_restaurant_availability, time=17:00)
seq 9  tool_call_end   (tool=check_restaurant_availability, available=false)
seq 10 replan_triggered (reason=restaurant_full, from_tool=check_restaurant_availability)
seq 11 tool_call_start (tool=check_restaurant_availability, time=17:30)
seq 12 tool_call_end   (tool=check_restaurant_availability, available=true)
seq 13 itinerary_ready (payload=Itinerary)
seq 14 done
```

**SSE 事件格式**（每条事件）：

```text
event: <SseEventType.value>
id: <seq>
data: <SseEvent.model_dump_json()>

```

具体 type 与 payload 形态见 `backend/schemas/sse.py` 顶部 docstring。

### 错误处理

- 输入校验失败 → HTTP 422 + JSON `{"detail": "..."}`，**不进入 SSE 流**
- 流中途异常 → 推一条 `stream_error` 事件后立刻 `done`，HTTP 状态仍 200
  ```json
  { "type": "stream_error", "seq": N, "payload": { "reason": "...", "detail": "..." } }
  ```

---

## 3. POST /chat/confirm（MVP-2）

> MVP-1 不实现；MVP-2 加入显式确认步骤后启用。

### 请求体

```json
{
  "session_id": "sess_20260516_001",
  "decision": "confirm",
  "modifications": null
}
```

字段：

```text
| 字段          | 必填 | 说明                                           |
|---------------|------|------------------------------------------------|
| session_id    | 是   | 与 /chat/stream 同一会话                       |
| decision      | 是   | "confirm" | "reject" | "modify"                |
| modifications | 否   | decision="modify" 时填用户改动                |
```

### 响应

`Content-Type: text/event-stream`，事件序列：

```text
seq 0 tool_call_start (tool=reserve_restaurant)
seq 1 tool_call_end   (tool=reserve_restaurant, order_id=...)
seq 2 tool_call_start (tool=buy_ticket)
seq 3 tool_call_end   (tool=buy_ticket, ...)
seq 4 tool_call_start (tool=generate_share_message)
seq 5 tool_call_end   (tool=generate_share_message)
seq 6 itinerary_ready (含 orders + share_message)
seq 7 done
```

---

## 4. GET /scenarios

### 响应（200）

```json
{
  "scenarios": [
    {
      "id": "S1",
      "title": "家庭主线",
      "input": "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
      "icon": "👨‍👩‍👧"
    },
    { "id": "S2", "title": "朋友 4 人", "input": "...", "icon": "👫" },
    "..."
  ]
}
```

字段约定来自 `docs/01-requirements/演示场景集.md` §二 8 个场景。

---

## 5. 跨字段约定

### session_id

- 前端生成，**全程透传**：`/chat/stream` 与 `/chat/confirm` 用同一 id
- 格式：`sess_<yyyymmdd>_<6位序号>` 或 uuid
- 后端用此 id 在内存中保存意图 + 最终方案，等 `/chat/confirm` 调用

### CORS

开发期 backend 默认开启 `Access-Control-Allow-Origin: http://localhost:3000`。
通过环境变量 `SHANGWUJU_CORS_ORIGINS=*` 可放开。

### 端口

- backend FastAPI：默认 `:8000`，通过 `SHANGWUJU_PORT` 覆盖
- frontend Next.js：默认 `:3000`
- 前端请求基址通过 `NEXT_PUBLIC_API_BASE` 环境变量配置（`http://localhost:8000`）

---

## 6. 前端联调 stub 模式（P3 必备）

后端启动时支持 `LLM_PROVIDER=stub`：

- `chat_with_tools` 返固定 fixture（家庭主场景）
- 仍走完整 SSE 流程（事件序列与真实一致）
- 让 B 同学不依赖 P2 完成度即可开发前端组件

启动方式：

```bash
SHANGWUJU_LLM_PROVIDER=stub uv run uvicorn main:app --reload
```

---

## 7. POST /chat/refine（Phase 0.6 新增 · 用户拒绝+反馈→重规划）

> 设计意图：当用户面对 Itinerary 卡片不满时，可以提供反馈让 Agent **基于反馈调整原 intent** 后重新规划，
> 而非完全推倒重来。这是「Agent」与「聊天机器人」的核心区分（评分项 1+5 加分点）。

### 请求体

```json
{
  "session_id": "sess_20260516_001",
  "feedback_text": "太远了，希望 3 公里以内"
}
```

字段：

```text
| 字段           | 必填 | 说明                                               |
|----------------|------|----------------------------------------------------|
| session_id     | 是   | 与 /chat/stream 同一会话；后端从内存取原 intent     |
| feedback_text  | 否   | 用户反馈文本（可空）；空时 refiner 走默认调整      |
```

字段精确定义见 `backend/schemas/refine.py` `RefinementInput`。

### 响应

`Content-Type: text/event-stream`，事件序列：

```text
seq 0  refinement_start    payload={"feedback_text": "..."}
seq 1  refinement_done     payload=RefinementOutput.model_dump()
                            含 refined_intent / changed_fields / refiner_note
seq 2-N  完整复用 /chat/stream 主路径事件序列
         （tool_call_start/end × N + replan_triggered? + itinerary_ready）
seq N+1 done
```

### 错误处理

```text
| 场景                        | 处理                                                |
|-----------------------------|-----------------------------------------------------|
| session_id 不存在           | HTTP 422 + {"detail":"session not found"}（不进 SSE）|
| refiner LLM 调用失败        | SSE 推 stream_error → done；HTTP 200                |
| refined intent 校验失败     | 重试 1 次；仍失败 → stream_error                    |
| planner 失败                | 复用 /chat/stream 错误处理                          |
```

### 后端关键步骤（B 块实现，A 提供 refiner）

```python
1. 从内存 session 取 (original_intent, last_itinerary)
2. refined = refiner.refine_intent(original_intent, feedback_text)
3. emit RefinementOutput(refined_intent=refined, changed_fields=[...])
4. plan_result = plan_itinerary_with_mode(refined, mode=resolve_planner_mode(...))
5. emit 事件序列 + done
```

---

## 8. PLANNER_MODE 双范式切换（Phase 0.6 新增）

### 背景

两种规划范式并存：

- **rule**：规则化 ReAct（默认；MVP-1/2 主路径，Demo 安全网，Tool 调用顺序写死）
- **llm**：LLM Function Calling 自主决策（评分项 2 加分点；LLM 看 8 个 Tool spec 自己挑）

### 切换通道（优先级从高到低）

```text
1. HTTP 请求 header `X-Planner-Mode: rule|llm`
   - 前端在所有 /chat/stream / /chat/refine 请求带上
   - 通过前端 PlannerModeBadge 切换器写到 cookie，再透传到 header
2. 环境变量 `PLANNER_MODE=rule|llm`
   - 后端启动时从 .env 读
3. 默认 `rule`（任意通道非法值都回 default）
```

解析函数：`schemas.planner_mode.resolve_planner_mode(header_value, env_value)`

### /health 暴露当前模式

```json
{ "status": "ok", "version": "0.1.0", "llm_provider": "deepseek", "planner_mode": "rule" }
```

> 注：返回的是**环境变量级**的 mode；单次请求实际使用的 mode 受 header 覆盖。

### LLM 失败 fallback 策略

```text
mode=llm 时，llm_planner 内部如果：
- LLM 调用超时 / 抛错
- 总 Tool 调用次数超 MAX_TOTAL_TOOL_CALLS=12
- 输出 Itinerary 校验失败
→ 自动 fallback 回 rule planner 跑一次（防 LLM 死循环让 Demo 翻车）
→ trace 推一条 agent_thought {"text": "LLM ReAct 失败，fallback 到规则 planner"}
```


---

## 9. POST /chat/turn（Phase 0.11/0.12 新增 · 单一对话入口 + ReAct 单一 Agent）

> 设计意图：解决「dock 直接输入反馈无上下文」根因（详见 `pitfalls.md` P1-2026-05-17 / `problem.md` 问题 18-19）。
> 让 LLM 看到 message_history 后自主判断「这是新需求还是对上次方案的反馈」，无需前端区分对话框 vs 反馈按钮。
> 默认 ON：`USE_REACT_AGENT=1`（见 §10）。

### 请求体

```json
{
  "message": "太远了，3 公里以内",
  "session_id": "sess_20260517_001",
  "user_id": "u_dad",
  "scenario_id": null
}
```

字段：

```text
| 字段        | 必填 | 说明                                                  |
|-------------|------|-------------------------------------------------------|
| message     | 是   | 用户一句话，长度 1-500（可以是新需求或反馈）          |
| session_id  | 是   | 跨 turn 共享 message_history 的会话 id                |
| user_id     | 否   | persona prior 注入（见 §6 Phase 0.7 文档）；缺省=demo_user |
| scenario_id | 否   | 演示快捷按钮 id（S1-S8）；自由输入时不填              |
```

### 响应

`Content-Type: text/event-stream`，事件序列**取决于 LLM 的自主决策**：

#### 闲聊 / Q&A 类输入（LLM 选 ChatResponse 分支）

```text
seq 0  agent_thought  (text="正在理解你的需求……")     # 心跳
seq 1  chitchat_reply (payload={"text":"...", "tone":"warm"})
seq 2  done
```

#### 完整规划类输入（LLM 选 ItineraryResponse 分支）

```text
seq 0   agent_thought   (text="正在理解你的需求……")
seq 1   intent_parsed   (payload=IntentExtraction)
seq 2   tool_call_start (tool=search_pois)
seq 3   tool_call_end   (tool=search_pois)
seq 4-N tool_call_*     (LLM 自主决定调几个工具，含 search_restaurants / check_availability / estimate_route_time 等)
seq M   itinerary_ready (payload=Itinerary)
seq M+1 done
```

#### 反馈类输入（LLM 看 message_history 自主识别）

```text
seq 0   agent_thought   (text="收到反馈，正在调整……")
seq 1-N tool_call_*     (LLM 基于上次方案 + 新反馈自主决定调哪些工具)
seq M   itinerary_ready (payload=Itinerary，含 refined_intent 字段)
seq M+1 done
```

### Critic 兜底（LLM-Modulo）

LLM 输出 ItineraryResponse 后由 `agent/v2/critics_v2.py` 跑 7 类 ViolationCode 校验：

```text
| 类型                          | Severity     | 处理                            |
|-------------------------------|--------------|----------------------------------|
| DURATION_OUT_OF_RANGE         | CRITICAL     | ModelRetry → LLM 自纠错         |
| DISTANCE_EXCEEDED             | CRITICAL     | ModelRetry → LLM 自纠错         |
| STAGES_INCOMPLETE             | CRITICAL     | ModelRetry → LLM 自纠错         |
| RESTAURANT_FULL_UNRESOLVED    | CRITICAL     | ModelRetry → LLM 自纠错         |
| TIMELINE_INCONSISTENT         | CRITICAL     | ModelRetry → LLM 自纠错         |
| SOCIAL_CONTEXT_MISMATCH       | WARNING      | log + 上呈                       |
| DIETARY_VIOLATION             | WARNING      | log + 上呈                       |
```

`retries=3` 给 critic backprompt 留循环空间；超出抛 UnexpectedModelBehavior。

### Fallback 链（探活失败自动回旧路径）

```text
USE_REACT_AGENT=1（默认）
  ↓
探活：unified_agent / orchestrator.run_react_turn import 成功？
  成功 → ReAct 单一 Agent 路径
  失败 → 自动回旧 router → planner / refiner 双路径
       （/chat/turn 仍工作；不影响 demo）

USE_REACT_AGENT=0
  → 强制走旧路径（demo 安全兜底）
```

### 错误处理

```text
| 场景                        | 处理                                                |
|-----------------------------|-----------------------------------------------------|
| session_id 不存在           | 自动创建新 ConversationState（不是 422）            |
| message 空 / 超长           | HTTP 422 + {"detail":"..."}（不进 SSE）            |
| LLM 调用超时                | SSE 推 stream_error → done；HTTP 200               |
| critic critical 违规重试用尽 | SSE 推 stream_error → done；HTTP 200               |
| ReAct 探活失败              | 透明回 fallback 旧路径（见上）                      |
```

---

## 10. 环境变量速查（Phase 0.11/0.12 新增）

```text
| 变量名           | 默认值        | 说明                                          |
|------------------|---------------|-----------------------------------------------|
| USE_REACT_AGENT  | 1             | /chat/turn 走 ReAct 单一 Agent 路径（=0 走旧路径）|
| DATA_PROVIDER    | mock          | ToolProvider 实现：mock | gaode | dianping     |
| LOG_FORMAT       | text          | observability 日志格式：text | json            |
| SESSION_STORE    | memory        | ConversationRepository 后端：memory | redis    |
| LLM_PROVIDER     | mimo          | LLM 客户端类型，与 base_url / api_key 配套     |
| PLANNER_MODE     | rule          | 旧 /chat/stream 双范式：rule | llm             |
```

详见 `backend/.env.example` 注释段。
