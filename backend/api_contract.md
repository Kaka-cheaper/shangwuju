# 后端 HTTP / SSE / WS 接口契约

> 本文档描述**现状**；历史演进见 `docs/adr/`（尤其 ADR-0011「一脑三壳」输入路由 /
> ADR-0012 记忆与图状态回写 / ADR-0013「节点交互三元素」）。小团 App 接入的业务
> 说明另见 `docs/06-business/07-小团能力接入指南.md`（本文档只管协议形状）。
>
> 唯一权威定义永远是代码：端点在 `backend/api/*.py`，payload 形状在
> `backend/schemas/*.py`。本文档只做「地图 + 摘要」——具体字段全文回代码查，
> 不在本文档里抄第二份（抄了就必然和代码一起漂移，历史已经证明过一次）。
>
> 修改纪律：任何字段改动先改 `backend/schemas/`，再同步本文件，再 grep 前端代码。

---

## 0. 架构现状（防止按旧文认知代码）

当前对话的唯一路径是 **V3 LangGraph**：`agent/graph/build.py` 编译的图 +
`agent/graph/sse_adapter.py` 把 `graph.astream()` 输出转成本文档描述的 SSE 事件流。

以下概念已经**删除**，不再是系统的任何组成部分（如果你在别处的旧文档/旧代码注释
里看到，那是历史遗留，别再据此理解现在的行为）：

- `USE_REACT_AGENT` 开关 + 「探活失败→回退旧 router/planner/refiner 双路径」的
  fallback 链
- `ConversationState` / 旧 `ConversationRepository`（跨轮上下文现由 LangGraph
  `InMemorySaver` / `RedisSaver` 以 `thread_id=session_id` 承载）
- `POST /chat/stream`、`POST /chat/refine` 两个端点（V1 legacy，随旧双路径一并
  删除；「反馈→重规划」的语义已内化进 `/chat/turn` 内部分支，见 §2.3）

（见 `backend/api/chat.py` 模块 docstring 第一行：「V1 legacy /chat/stream +
/chat/refine 已退役删除，turn 唯一走 V3 LangGraph」。）

---

## 路径总览

| 方法 | 路径 | 用途 | 实现文件 |
|---|---|---|---|
| POST | /chat/turn | 对话主入口（唯一入口；自动识别新需求 vs 反馈） | `api/chat.py` |
| POST | /chat/confirm | 确认下单：预约/购票/加购/转发文案 | `api/chat.py` |
| POST | /chat/adjust | 单人节点定向调整 / 具名备选（ADR-0013 F-4） | `api/adjust.py` |
| GET | /scenarios | 8 个演示场景快捷输入 | `api/scenarios.py` |
| GET | /personas | mock persona 列表 | `api/preferences.py` |
| GET | /preferences/{user_id} | 读取某用户合并偏好 | `api/preferences.py` |
| POST | /preferences/{user_id}/reset | 清除某用户累积偏好 | `api/preferences.py` |
| GET | /health | liveness 探针 | `api/health.py` |
| GET | /ready | readiness 探针（依赖探活） | `api/health.py` |
| POST | /room/create | 创建协作房间 | `api/collab.py` |
| GET | /room/{room_id}/state | 拉房间状态快照（HTTP，供 SSR / WS 连接前预加载） | `api/collab.py` |
| WS | /ws/{room_id} | 协作房间实时通信 | `api/collab.py` |
| GET | /legal/terms, /legal/privacy | 法务文案占位（真上线前需法务审核） | `api/legal.py`（运营辅助） |
| GET | /auth/* | OAuth 接入位占位（demo 阶段全 stub） | `api/oauth.py`（运营辅助） |
| * | /_AMapService/{path} | 高德 JS API 安全代理 | `api/amap.py`（运营辅助，`include_in_schema=False`） |

小团 App 集成核心是前 3 行（`/chat/turn` + `/chat/confirm` + `/chat/adjust`）加
`/scenarios` + `/health`；协作房间是加分项；法务/OAuth/高德代理是运营辅助端点，
不在本文档后续详细展开范围内（各自的模块 docstring 已经说明清楚）。

### SSE 传输信封（/chat/turn、/chat/confirm、/chat/adjust 共用）

`Content-Type: text/event-stream`；每条事件是 `sse-starlette` 的三键格式
（`api/_sse_helpers.py::to_sse`）：

```
event: <SseEventType.value>
id: <seq>              # 字符串化的 seq，不是事件类型
data: <SseEvent.model_dump_json()>

```

流中途未捕获异常 → 兜底推一条 `stream_error` + `done`（`safe_stream` 装饰器，
见 `api/_sse_helpers.py`），HTTP 状态码仍是 200（错误已经在流里，不是 HTTP 层错误）。

---

## 1. POST /chat/turn（对话主入口，小团 App 唯一入口）

Owner：`api/chat.py::chat_turn`。**唯一走 V3 LangGraph**——graph build/import
失败直接同步 `HTTP 500 langgraph_unavailable`，不再有任何 fallback 到旧路径
（那条 fallback 链已随 `USE_REACT_AGENT` 一并删除）。

### 1.1 请求体（`api/_streams/models.py::ChatStreamRequest`）

| 字段 | 必填 | 说明 |
|---|---|---|
| message | 是 | 1-500 字；新需求或反馈皆可，由图内 `router` 节点自主判断，前端不需要区分 |
| session_id | 是 | 1-128 字；同一值 = 同一 LangGraph `thread_id`（跨 turn 上下文持久化的唯一真相源） |
| scenario_id | 否 | 演示场景 id（S1-S8，见 §4） |
| user_id | 否 | persona prior 注入；缺省按 `X-User-Id` header > `"demo_user"`（`api/_session_store.py::resolve_user_id`） |

### 1.2 响应 header

`X-Planner-Mode`（解析结果，见 §8）、`X-User-Id`（解析结果）、`X-Turn-Kind: langgraph`。

### 1.3 SSE 事件序列

**开场**：不论走哪条分支，第一条事件恒为心跳（防 8s 首字节超时）：

```
seq 0  agent_thought  {"text": "正在理解你的需求……"}
```

之后按图内 `router` 节点的判定分三条分支（`agent/graph/_emit_handlers.py::emit_router`）：

**分支 A：陪聊 / 确认引导 / 澄清引导 / 安全婉拒**（`route_kind` 既非 planning 也非 feedback；ADR-0011 E-2-c 六标签闭集）

```
chitchat_reply  payload = RouterDecision.model_dump()   # schemas/router.py
done            payload = 见 §1.4「DONE 恒 6 字段」
```

不出现 `itinerary_ready`。`RouterDecision` 核心字段：`input_kind`（`planning`/
`chitchat`/`confirm`/`clarify`/`defense` 五值之一，`schemas/router.py::InputKind`；
E-2-c 起 meta/emotional 併入 chitchat 由 tone 承载语气、off_topic→defense、
ambiguous→clarify、confirm 独立成类）/ `reply_text` / `tone` / `cta_chips`
（`CtaChip` 列表；`send` 字段是白名单字面文案，前端点击原样发回即可重入
`/chat/turn` 主链路；`action="confirm"` 时表示点击应直接触发 `/chat/confirm`
而非发文本消息）。

**分支 B：反馈**（`route_kind == "feedback"`，图内自主识别"这是对上一版方案的反馈"）

```
agent_thought      {"text": "收到反馈，正在调整……"}
refinement_start   {"feedback_text": <本轮 message>}       # 兼容旧前端事件名
refinement_done    payload = RefinementOutput.model_dump()  # refined_intent/changed_fields/refiner_note
intent_parsed      payload = IntentExtraction.model_dump()  # 用新 intent 重推一次，前端 IntentSummary 刷新
```

随后与分支 C「候选召回」往下共享同一套图节点（同一张图，`route_kind` 只是
决定走哪条条件边，节点本身不区分调用者是新需求还是反馈）。

**分支 C：完整规划请求**（`route_kind == "planning"`）

```
agent_thought       {"text": "好的，让我帮你规划一下。"}
intent_parsed       payload = IntentExtraction.model_dump()   # schemas/intent.py
tool_call_start × 3 / tool_call_end × 3
  # 并行 fan-out：search_pois / search_restaurants / get_user_profile
  # payload 带 group_id="fanout-execute", parallel=true（前端可横向并列渲染同组）
  # 注意：这 3 个是当前唯一会在 /chat/turn 出现的 tool_call_* 事件；
  # check_restaurant_availability / estimate_route_time 等其余工具在规划算法
  # 内部调用，不个别转成 SSE 事件（与 V1 旧文档描述的"逐工具重试"模型不同）
[agent_thought]     # 权重摘要 / 蓝图节点数摘要，可能 0-2 条
[critic_fix_attempt]                         # 仅 plan_attempt > 1（critic backprompt 重试）时
[critic_violations + replan_triggered]       # 仅命中 hard 违规时（两者成对出现）
[plan_fallback + agent_thought]              # 仅触发降级链（llm_first→ils→rule 等跳变）时，可多跳
agent_thought       {"text": "蓝图已拼成行程草稿，正在验证可行性……"}   # 缺坐标时前面多一条警示
itinerary_ready     payload = Itinerary.model_dump()      # finalize_plan 节点推送，见 §1.4
agent_narration     payload = 见 §1.4「agent_narration 兄弟字段」    # narrate 节点推送
done                payload = 见 §1.4「DONE 恒 6 字段」
```

### 1.4 关键契约细节

**`itinerary_ready` 先于 `agent_narration`（易错点）**：`itinerary_ready` 在
`finalize_plan` 节点推送，**早于** `narrate` 节点推送的 `agent_narration`——
critic 通过 / 降级链给出结果的那一刻方案就已定稿，不必等叙事 LLM（数秒到数十秒）
跑完；narrate 只在文案确实更精彩时通过 `agent_narration.title` 兄弟字段原地
更新前端已展示的标题，**不重推**整份 `Itinerary`（见 `agent/graph/_emit_handlers.py`
的 `emit_finalize_plan` / `emit_narrate` 两个函数 docstring）。

**`itinerary_ready` payload 是纯 `Itinerary.model_dump()`**，不夹带任何兄弟
字段——`api/chat.py` 会把它整体镜像进 `SESSION_STORE`，`/chat/confirm` /
协作房间快照拿 `Itinerary.model_validate()`（`extra="forbid"`）反序列化，混入
陌生字段会直接把确认流程校验炸掉（`emit_narrate` docstring 记录的一次深审
教训）。所有"附加通道"都走 `agent_narration` 的兄弟字段：

| 字段 | 何时出现 | 出现在哪些端点 |
|---|---|---|
| messages | 规划器产出 `Advisory` 告知时（`schemas/advisory.py`，ADR-0010 D-7） | `/chat/turn`、`/chat/adjust`（§2） |
| node_actions | 至少一个节点有按钮/备选时；`{node_id: {chips: [NodeChip], alternatives: [AlternativeOption]}}`（`schemas/node_chip.py`） | `/chat/turn`、`/chat/adjust` |
| title | narrate 用 LLM 换出比规则标题更精彩的版本时（字符串，前端原地替换已展示标题） | 仅 `/chat/turn` |
| demand_ledger | 台账非空时；`schemas/demand_ledger.py::ledger_for_display` 投影 | 仅 `/chat/adjust`（§2）和协作房间换菜（§8）；**`/chat/turn` 的 narrate 不带这个字段** |

`/chat/confirm` 的 `agent_narration` 不带任何兄弟字段（见 §3）。

**`done` 在 `/chat/turn` 恒携带 6 字段总结**（`agent/graph/sse_adapter.py`
`run_graph_stream` 末尾拼装，闲聊分支也不例外，只是数值退化为默认值）：

```json
{"final_strategy": "llm_first", "plan_attempts": 0, "critic_attempt_count": 0,
 "fallback_hops_count": 0, "total_ms": 1234, "has_itinerary": false}
```

`final_strategy` ∈ `llm_first / llm_backprompt / ils / rule / give_up`。这是
`/chat/turn` 专属的约定——`/chat/confirm` 和 `/chat/adjust` 的 `done` payload
就是空 `{}`（各自拼流的实现里从未传参数给它，见 §3/§2）。

全部 SSE 事件类型枚举 + 各类型 payload 约定的权威定义：
`schemas/sse.py::SseEventType` / `SseEvent`（docstring 逐类型列了 payload
形状，是本节表格的真源）。节点 → 事件的精确 dispatch 逻辑（哪个 LangGraph
节点触发哪个 `emit_xxx`）：`agent/graph/sse_adapter.py` +
`agent/graph/_emit_handlers.py`——本文档只保证「事件目录 + 关键顺序不变量」，
不逐节点复述内部图拓扑（拓扑随规划质量迭代常变，为它维护一份逐节点镜像
文档必然很快漂移，属于概念完整性上不该由契约文档承担的细节）。

### 1.5 关于 /chat/refine 的语义去向

V1 `POST /chat/refine` 端点已删除。"用户反馈→重规划"现在是 `/chat/turn` 图内
`router` 节点判定的 `feedback` 分支（本节分支 B）——前端不需要区分「对话框」
还是「反馈按钮」，同一个端点，LLM 看跨轮上下文自主判断。`refinement_start` /
`refinement_done` 两个事件类型和 `RefinementOutput` schema（`schemas/refine.py`）
原样保留，只是触发路径从独立端点变成了 `/chat/turn` 内部分支。

### 1.6 错误处理

| 场景 | 处理 |
|---|---|
| message 空/超长/session_id 缺失 | HTTP 422 + `{"detail": "..."}`（pydantic 校验，不进 SSE） |
| LangGraph build/import 失败 | HTTP 500 `{"detail": "langgraph_unavailable: ..."}`（同步失败，不进 SSE） |
| 图执行中途异常 | SSE 推 `stream_error {"reason": "graph_execution_failed", "detail": "..."}` → `done`；HTTP 状态仍 200 |

---

## 2. POST /chat/adjust（ADR-0013 F-4，单人节点调整）

Owner：`api/adjust.py::chat_adjust` + `api/_streams/graph_adjust.py::_graph_adjust`。
**不经过 LLM 路由**——三种 action 是结构化指令，点击即生效、无预览（ADR-0013 决策 2）。
响应**不带**任何额外 header（不同于 `/chat/turn`/`/chat/confirm` 会回显
`X-Planner-Mode`）；请求体也不含 `user_id`（`session_id` 已绑定图 checkpoint，
无需二次给身份）。

### 2.1 前置校验（同步 HTTP 4xx，SSE 流开始之前）

- LangGraph build 失败 → 500 `langgraph_unavailable`
- session 无图 checkpoint，或图状态里 `itinerary`/`intent` 任一为 `None`
  （还没跑过一次 `/chat/turn`）→ **404**，人话 `detail`

### 2.2 请求体（`api/_streams/models.py::ChatAdjustRequest`）

| 字段 | 必填 | 说明 |
|---|---|---|
| session_id | 是 | 需已有 `/chat/turn` 产出的图 checkpoint |
| node_id | 是 | `ActivityNode.target_id`（POI/Restaurant 实体 id，**不是** `ActivityNode.node_id` 那个"n_0"结构化定位 id） |
| action | 是 | 判别式 Union（按 `type` 字段区分），三选一，见下 |

`action` 三种判别式形状（`AdjustAction = Union[AdjustActionAdjust, AdjustActionAlternative, AdjustActionDislike]`）：

| type | 字段 | 语义 |
|---|---|---|
| `adjust` | `adjustment: NodeAdjustment`（`schemas/node_adjustment.py`，6 维之一：price/distance/cuisine_or_type/dietary/ambience/crowd_fit）+ `label?`（≤8 字，缺省时后端按维度合成诉求台账 source_text） | 点「定向调整按钮」——按维度换 |
| `alternative` | `target_id: str` | 点「具名备选」——直接换成这一个实体（候选池收窄手法，保证不会被同池更高分候选顶替） |
| `dislike` | （无载荷） | 点踩——无方向局部重解；单人 UI 暂不发出这个 action，协议先立好给房间侧（§8）复用 |

### 2.3 SSE 事件序列

```
agent_thought    {"text": "收到，这就帮你调整一下这一站……"}
```

分两种结局：

**成功**：

```
itinerary_ready  payload = Itinerary.model_dump()   # 新方案，纯 dump，同 §1.4 契约
agent_narration  payload = {"text": ..., "stage": "stream",
                             "messages"?: [...], "node_actions"?: {...}, "demand_ledger"?: [...]}
done             payload = {}
```

**业务性失败**（无可换候选 / 保留节点排不到一块儿——`SwapResult.success=False`，
不是异常）：

```
agent_narration  payload = {"text": <告知文案，方案不动>, "stage": "stream"}
done             payload = {}
```

`resolve_node_swap` 对"node_id 不存在"等调用方**契约违反**抛 `ValueError`——
这类走 `safe_stream` 兜底转 `stream_error` + `done`，与上面"业务性失败"刻意
区分（见 `api/_streams/graph_adjust.py` 模块 docstring「业务性失败 vs 契约
违反」）。

诉求台账副作用（无论换菜是否成功都可能发生）：`adjust` 类型的诉求恒记账
（换不成不代表用户不再想要）；换菜成功且 `degrade_tier ∈ {1, 2}`（谓词确实
被满足，非"近似"）才把该条标记 `SATISFIED`。

---

## 3. POST /chat/confirm

Owner：`api/chat.py::chat_confirm`，实现在 `api/_streams/graph_confirm.py::_graph_confirm`。
**恒走这一条流**——`USE_LANGGRAPH` 开关与专用 `_stub_confirm` 已退役（ADR-0012
决策 5），协作房间确认（§8）也复用同一实现。

响应 header 带 `X-Planner-Mode`（解析结果回显），但**这个 mode 值不实际影响
confirm 行为**——`execute_finalize_node` 硬编码 `defer_post_confirm_effects=True`
→ `use_llm=False`，confirm 阶段 narration 恒为规则文案，不像 `/chat/turn` 那样
真正把 mode 送进图状态给 critic/narrate/planner 消费（见 `collab/room.py::confirm`
docstring 的明确说明）。这是当前行为，不是本次文档批引入的降级。

### 3.1 请求体（`ChatConfirmRequest`）

| 字段 | 必填 | 说明 |
|---|---|---|
| session_id | 是 | 需与 `/chat/turn` 同一 session；后端读 `SESSION_STORE` 投影（**不读**图状态） |
| decision | 是 | `"confirm"` \| `"reject"` \| `"modify"`（正则约束）；非 `confirm` 时只推一条 `agent_thought` 告知 + `done`，不派发任何工具 |
| user_id | 否 | 缺省用 `SESSION_STORE` 缓存里的值 |
| allowed_restaurant_ids / allowed_poi_ids | 否 | 执行类工具白名单（防 hallucination）；前端从 `ITINERARY_READY` 收到的合法 id 集合回传；缺省不做校验（demo 短路径不破） |

### 3.2 SSE 事件序列

```
agent_thought    {"text": "正在确认预约与加购服务……"}
[agent_thought × N]      # FINALIZE_HEARTBEAT_S=1.5s 心跳；execute_finalize 是同步阻塞函数，跑在 asyncio.to_thread
tool_call_start/end × N  # 按方案 pending_actions 实际派发，视方案而定：
                         # reserve_restaurant → buy_ticket → order_extra_service → generate_share_message
itinerary_ready  payload = Itinerary.model_dump()   # 含 orders + share_message，同步回写 SESSION_STORE
agent_narration  payload = {"text": ..., "stage": "confirm"}   # 无兄弟字段（不同于 §1.4 表格）
memory_persisted payload = {...}    # 仅当 memory_status 非 None 时出现
done             payload = {}       # 空 payload，不同于 /chat/turn 的 6 字段总结（见 §1.4）
```

确认成功后三件事**不阻塞**上述事件推送（并行后台任务，见 `_graph_confirm`
docstring）：

1. memory_writer 副作用（写 `user_profile.json` 的 `recent_trips`）
2. memory_store 标签/访问累积（`_accumulate_memory_after_confirm`）
3. 终版方案 + `user_decision="confirm"` **同步**回写进 LangGraph 图 checkpoint
   （`_writeback_graph_state`，在推 `DONE` 之前 `await` 完成，供下一轮
   `/chat/turn` 感知"已下单"；协作房间会话无 checkpoint 时优雅跳过，不影响
   确认结果本身）

### 3.3 错误处理

| 场景 | 处理 |
|---|---|
| session 无 itinerary 快照 | SSE `stream_error {"reason": "session_not_found"}` → `done` |
| 快照反序列化失败 | SSE `stream_error {"reason": "invalid_session_snapshot"}` → `done` |
| finalize 执行异常 | SSE `stream_error {"reason": "finalize_failed"}` → `done` |

---

## 4. GET /scenarios

`api/scenarios.py`——纯 adapter，数据源已迁至
`agent/routing/canonical_shortcut.py::DEMO_SCENARIOS`（单一真相源；这 8 条
`input` 文案同时是壳2「canonical 字面短路」的匹配表，是断网/stub 演示下
"任意输入→引导气泡→点场景 chip→正常规划"的规划可达通道，见该模块 docstring）。

```json
{"scenarios": [{"id": "S1", "title": "学生党 KTV 局", "input": "...", "icon": "🎤"}, ...]}
```

8 条，青年向场景置首位（小团主力用户群）；具体文案以 `canonical_shortcut.py`
为准，不在本文档复述（复述了就是第二个会漂移的真相源）。

---

## 5. persona / preferences（`api/preferences.py`）

| 方法 + 路径 | 返回 | 备注 |
|---|---|---|
| GET /personas | `{"personas": [Persona.model_dump(), ...]}` | `schemas/persona.py::Persona`；5 个 mock 身份档案 |
| GET /preferences/{user_id} | 合并偏好视图（`data.memory_store.compute_priors(user_id)` 的 dump） | persona 默认值 + 累积 memory 的合并结果，给前端偏好面板 |
| POST /preferences/{user_id}/reset | `{"status": "ok", "memory": {...}}` | 清空该用户累积 memory（演示清场用） |

`Persona` 核心字段：`user_id` / `label` / `icon` / `notes` / `home_location` /
`default_distance_max_km` / `default_budget` / `default_tags`
（`PersonaDefaultTags`：physical/dietary/experience/suitable_for_priority，
仅作 prior 注入，不强制、用户输入永远优先）。

---

## 6. GET /health、GET /ready

### GET /health（liveness）

```json
{"status": "ok", "version": "0.1.0", "llm_provider": "deepseek", "planner_mode": "rule", "planner_real": "1"}
```

- `llm_provider`：解耦后由 base_url 自动推断（`_resolve_creds` 成功即真实
  provider 名；`LLM_PROVIDER=stub` 时固定 `"stub"`）
- `planner_mode`：**环境变量级**的 mode（`current_env_mode()`）；单次请求
  实际用的 mode 受 `X-Planner-Mode` header 覆盖，见 §7
- `planner_real`：字符串 `"1"`/`"0"`（非 bool）——是否会真调 LLM
  （`api/health.py::_use_real_planner` 优先级：`PLANNER_USE_REAL` 显式开关 >
  `LLM_PROVIDER=stub` > 是否有 credential > 默认假）

### GET /ready（readiness）

200 `{"status": "ready", "version": "...", "checks": {...}}`；任一子项失败
→ **503** `{"status": "not_ready", ...}`。探活清单（`api/health.py::ready`）：

1. `checks.llm`：`LLM_PROVIDER=stub` 视为可用；否则调 `_resolve_creds`
2. `checks.redis`：仅当 `SESSION_STORE=redis` 或显式配了 `REDIS_URL` 才探；
   InMemory 模式恒 `{"ok": true, "skipped": "session_store=memory"}`
3. `checks.mock_data`：`data.loader.load_pois()`/`load_restaurants()` 至少各有 1 条

---

## 7. 跨字段 / 跨端点约定

### session_id / thread_id

前端生成并全程透传；`/chat/turn` 用它做 LangGraph `thread_id`（跨轮上下文
持久化的唯一真相源），`/chat/confirm`/`/chat/adjust` 用它读同一份
`SESSION_STORE` 投影（`api/_session_store.py`）。`SESSION_STORE` 是
dict-like 存储，`SESSION_STORE=redis` 时额外镜像/预热（详见该文件
docstring），行为对调用方透明。

### X-Planner-Mode / PLANNER_MODE

`rule`（规则化，默认，Demo 安全网）/ `llm`（LLM 自主决策）。解析优先级：
请求 header `X-Planner-Mode` > 环境变量 `PLANNER_MODE` > 默认 `rule`
（`schemas/planner_mode.py::resolve_planner_mode`）。仍是图内 `planner` /
`critic` / `narrate` 三个节点实际读取的活字段（`agent/graph/nodes/*.py`
里 `state.get("planner_mode")`），不是死配置。`/chat/turn` 与 `/chat/confirm`
响应都带 `X-Planner-Mode` header 回显解析结果，但 `/chat/confirm` 的这个值
**不实际改变确认行为**（见 §3 开头说明）；`/chat/adjust` 走的换菜引擎
（`resolve_node_swap`）完全不读这个 header。

### X-User-Id

`resolve_user_id(body.user_id, header)`：body 显式传的 `user_id` >
`X-User-Id` header > `"demo_user"`。`/chat/adjust` 不接受也不解析
`user_id`/`X-User-Id`（`session_id` 已绑定图 checkpoint，动作不需要二次给身份）。

### CORS

`main.py` 写死 `allow_origins=["*"]`（demo 模式，含 WebSocket）；生产收紧
直接改 `main.py`，不走 env。

### 端口

backend 默认 `:8000`（FC 注入 `PORT=9000`），frontend 默认 `:3000`；前端
请求基址走 `NEXT_PUBLIC_API_BASE`。

### stub 模式

`LLM_PROVIDER=stub` 时全链路走固定 fixture，不调任何真实 LLM，事件序列
形状与真实一致（联调/CI 用）。

---

## 8. 协作房间：HTTP + WS（`api/collab.py` + `collab/room.py`）

### 8.1 HTTP

| 方法 + 路径 | 用途 |
|---|---|
| POST /room/create | 创建房间；可选带入 `session_id`（继承已规划方案）/ `chat_messages` / `planning_events` / `chat_state` |
| GET /room/{room_id}/state | 拉当前状态快照（同 WS 首次推的 `room_state`），供 SSR / WS 连接前预加载；房间不存在 → 404 |

`POST /room/create` 请求体 `CreateRoomRequest`：`user_id`（必填）/
`nickname`（默认"发起人"）/ `session_id?` / `chat_messages?` /
`planning_events?` / `chat_state?`。响应 `CreateRoomResponse`：`room_id` /
`share_url` / `owner_id`。

### 8.2 WS /ws/{room_id}

连接参数（query string）：`user_id`（必填）/ `nickname`（可选，默认 =
user_id）。房间不存在 → accept 后发一条 `error` + `close(code=4004)`。

#### 上行（客户端 → 服务端）

| type | 字段 | 语义 |
|---|---|---|
| constraint | text | 自由打字——先过统一路由脑子 `route_turn` 判义务（ADR-0013 决策 7），按 feedback/planning/其余 三分支处理，见下 |
| vote | stage_index, action(`"like"`\|`"dislike"`) | 赞 = 锁定该段（`locked_stages`，纯展示态）；踩 = 收编进节点级局部重解（`RoomManager.adjust`，`action=dislike`），**不再触发全量重排** |
| adjust | node_id, action | `action` 判别式协议同 `/chat/adjust`（§2.2 三选一）——节点行按钮/具名备选/点踩三个入口在协议层已殊途同归 |
| confirm | （无） | 仅 `owner_id` 可发；复用 `_graph_confirm`（§3），确认阶段 narration 恒规则文案 |
| ping | （无） | → 回 `pong` |

`constraint` 消息的义务分发（`RoomManager.add_constraint`，ADR-0013 决策 7）：

- `route_turn` 判 `feedback` → 进约束池 + 中断在跑规划 + 合并约束重新规划
- `route_turn` 判 `planning`（如完整场景文本 / "重新规划一个"）→ **不进
  约束池**，直接全新规划
- 其余（chitchat/confirm/clarify/defense）→ 原样广播
  `chitchat_reply`（`RouterDecision.model_dump()`），不动方案、不动约束池、
  不中断在跑任务

无论分到哪支，原始发言都无条件走"归名"广播（`constraint_added` +
追加进 `chat_messages`）——展示是纯展示语义，与"算不算可执行约束"是两回事。

#### 下行（服务端 → 客户端）

| type | 载荷要点 | 触发时机 |
|---|---|---|
| room_state | 见下方全字段表 | 每次 WS 连接建立时（首次加入/重连都推一次全量快照） |
| member_joined | user_id, nickname, role | 首次加入（前端追加一行） |
| member_reconnected | user_id, nickname, role | 断线重连（前端更新既有行的 online/nickname，不追加新行——区分 joined/reconnected 是为了防止"重连刷屏" bug，见 `RoomManager.join` docstring） |
| member_left | user_id | WS 断开 |
| constraint_added | user_id, nickname, text, source, timestamp | 任何人发 constraint（归名展示，见上） |
| planning_started | trigger, trigger_user, constraints_count? | 重规划/全新规划开始 |
| planning_aborted | reason, by_user | 在跑规划被新约束/新规划请求中断 |
| vote_updated | stage_index, user_id, action, votes, locked_stages | 投票后 |
| node_locked | node_id, by_user, nickname | F-5：换菜处理期开始广播（全员可见该节点 Shimmer 处理中） |
| node_unlocked | node_id | 换菜处理结束（成功/失败/异常都会到达，`finally` 保证不会卡死锁定） |
| planning_event | `{"event": {...}}` 信封，内层是 `SseEvent` 同形状 dict（`type`/`seq`/`payload`/`timestamp_ms`） | 规划过程中的每个事件（`intent_parsed`/`itinerary_ready`/`agent_narration`/`chitchat_reply`/`refinement_done`/`stream_error`/`done` 等，`RoomManager._broadcast_planning_event`）；新成员加入时可从 `room_state.planning_events` 回放 |
| error | message | 房间不存在 / adjust 消息缺 node_id 或 action 格式不对 / 非 owner 尝试确认 |
| pong | （无） | 响应 ping |

`room_state` 全字段（`collab/room.py::Room.get_state_snapshot`）：
`type="room_state"` / `room_id` / `owner_id` / `members`（含 online 态）/
`constraints` / `votes` / `itinerary` / `previous_itinerary` / `intent` /
`locked_stages` / `planning_events`（历史回放用）/ `chat_messages` /
`chat_state` / `planning_active`（bool）/ `demand_ledger`（F-5 新增，
`ledger_for_display` 投影，同 §2.3 单人版同一口径）。

### 8.3 房间版换菜与单人版的差异（`RoomManager.adjust`）

复用 `/chat/adjust` 同一引擎 `resolve_node_swap`，三处必然差异：

1. **候选池现场重查**：房间没有图 checkpoint 缓存的 `pois`/`restaurants`，
   复用 `ils_planner._query_pois`/`_query_restaurants` 同款真实召回
2. **全程串行**：`room.lock` 保证同一房间的调整请求排队处理
3. **归名 + 处理期锁定广播**：诉求台账记 `member_id`/`nickname`；处理期
   先广播 `node_locked`，成功/失败都以 `node_unlocked` 收尾

业务性失败与契约违反（`ValueError`）在房间版**都**降级为告知气泡（不像
`/chat/adjust` 的 SSE 那样把契约违反转 `stream_error`）——房间是长连接
会话，未捕获异常会被外层 `except Exception` 当断线处理触发 `manager.leave()`，
代价远大于多做一层防御性收窄（见 `RoomManager.adjust` docstring）。

---

## 9. schemas/ 分层导航

`backend/schemas/__init__.py` 顶部 docstring 是权威的分层地图（tags/errors
基础层 → intent/domain/itinerary 核心层 → persona/decision_trace 扩展层 →
tools/sse/router/refine/planner_mode API 契约层），本文档不重复摘抄，改字段
前先去那里定位文件。

本文档引用到的 schema 速查：

| 概念 | 文件 |
|---|---|
| SSE 事件类型 + payload 约定（真源） | `schemas/sse.py` |
| 意图抽取 | `schemas/intent.py` |
| POI / Restaurant / UserProfile | `schemas/domain.py` |
| 行程（edge_v1：nodes + hops 二元组） | `schemas/itinerary.py` |
| 决策可解释性（挂在 Itinerary 上） | `schemas/decision_trace.py` |
| 输入域路由（6 类 + 引导 chip） | `schemas/router.py` |
| 反馈合并输出 | `schemas/refine.py` |
| 定向调整（6 维受控维度表） | `schemas/node_adjustment.py` |
| 调整按钮下发 / 回传形状 | `schemas/node_chip.py` |
| 诉求台账（状态机 + 顶替规则） | `schemas/demand_ledger.py` |
| advisory「绝不默默忽略」告知通道 | `schemas/advisory.py` |
| persona / memory | `schemas/persona.py` |
| 9 个 Tool 的 Input/Output | `schemas/tools.py` |
| 3 类受控 tag 词典 | `schemas/tags.py` |
| 失败原因枚举 | `schemas/errors.py` |

---

## 10. 发现的文档-代码新出入（三条均已收口，2026-07-03）

- ~~`ChatConfirmRequest.modifications` 死字段~~ **已删**：`decision="modify"`
  分支与 `reject` 同路只告知不消费改动，前端调用点（`lib/store.ts` 的
  `/chat/confirm` 请求体）也从未发送过它——字段从后端 schema 与前端
  `ChatRefineRequest`/`ChatConfirmRequest` 类型镜像一并移除。
- ~~`schemas/refine.py` docstring 漂移~~ **已修**：模块 docstring 改述当前
  真实链路（反馈经 /chat/stream 统一路由 → refiner_node）；随删除的
  /chat/refine 端点一起死掉的请求体类 `RefinementInput`（全仓零消费方）
  连壳移除，`RefinementOutput` 保留（refiner 输出 + `REFINEMENT_DONE`
  payload，活契约）。
- ~~`CtaChip.action="confirm"` 无消费方~~ **记录修正，链路成立非死字段**：
  生产方在后端 `agent/core/dialogue_acts.py`（预约指令 → 一键「确认预约」
  chip），消费方在前端 `components/ChitchatBubble.tsx` + `lib/store/
  event-handlers.ts`（识别 `action === "confirm"` 的 chip 点击直调
  `/chat/confirm`）。后端不需要自己消费——chip 的语义就是"让前端替用户按
  下确认按钮"，原记录"未深挖前端"的存疑现已核实为设计如此。
