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
