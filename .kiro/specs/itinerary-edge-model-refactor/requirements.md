# Requirements Document

## Introduction

把行程数据模型从「Stage（含通勤过程段）」重构为「Activity Node（节点）+ Hop（边）」模型，对齐 Google Trips / Apple Maps / 携程的业内标准建模。

**为什么必须重构**（根因分析）：
现状 `ItineraryStage` 把「在某地停留」与「通勤过程」糅合在一个字段里，被 LLM、critic、前端三方各自解读：
- LLM 把「出发段 duration_min=15」当成 home→target 的通勤时间
- critic 把它当成「在 home 待 15 分钟」再额外要求段间通勤 buffer
- 前端把它和主活动段渲染成同样大小的时间块

三方语义漂移导致：用户输入「家庭半日游」→ LLM 反复 backprompt → critic 反复拒 → 触发 ILS fallback → ILS 也过不了同一个 critic → 死循环到 LangGraph 25 步硬限。无论 prompt 怎么改都救不了——根因在数据模型本身。

`mock_data/routes.json` 本来就是 edge 表（from_location → to_location → minutes），把它强行塞进 `stage.duration_min` 是反模式。重构后单一真理源：节点表达「在哪干啥、停留多久」，边表达「A→B 怎么过去、几分钟」，三方组件不再各算各的。

**Hackathon 时间盒约束**：保留 LangGraph 拓扑 + critics_v2 + LLM-Modulo 闭环；只换 schema + assemble + critic 的语义。前端 v1 通过 `schedule` 派生视图复用现有时间轴渲染，不强求拆 hop 卡片。

**当前阶段**：Phase 0.20 LangGraph 主架构已上线 / MVP-2 95% / 现 demo 死循环根因待修。

## Glossary

- **ActivityNode（节点）**：用户在某个地点做某件事的离散事件，含 `start_time + duration_min + target_kind + target_id + lat/lng`。`target_kind` ∈ {poi, restaurant, home}。home 节点 duration_min 固定为 0，仅作起终点锚定。
- **Hop（边）**：两个节点之间的位移过程，含 `start_time + minutes + mode + path_type + buffer_min`。`mode` ∈ {walking, taxi, bus, haversine_estimated, virtual}。`path_type` ∈ {real_route, estimated, in_place}。
- **BlueprintNode**：LLM 输出的中间节点契约（不含首尾 home，不含 hops）。LLM 只决定 `target_id + duration_min + kind`，不决定通勤时间、不决定起止时刻。
- **lookup_hop**：单一函数收口「from_id, to_id → (minutes, mode, path_type)」，三级降级（mock 路线 → haversine → 15 min 保守值），被 assemble 与 critic 共用。
- **ScheduleEntry（派生视图）**：把 nodes + hops 按时间序展平的只读条目，用于前端 v1 ItineraryCard 复用现有渲染逻辑。`hidden=true` 的条目（in_place hop）默认不渲染。
- **schema_version**：Itinerary 顶层字段，本次重构标记为 `"edge_v1"`。旧 stages 模型的固定文案标 `"legacy_v0"`。
- **In-place hop**：同地复用场景下的虚拟边（minutes=0 / mode=virtual / path_type=in_place），用于「连续两个节点共享同一 target_id」（如先看展再吃饭，都在同一商业体）。
- **不变量**：`len(hops) == len(nodes) - 1` 且 `nodes[0]` / `nodes[-1]` 必为 home。任一断言失败即 schema 损坏。
- **OrderRecord.target_kind**：本次新增字段，与 `ActivityNode.target_kind` 对齐，用于下单时按 `target_kind="restaurant"` 找节点而不是按旧 `restaurant_id` 字段名。
- **edge_v1**：本次重构后的 schema 版本号；`legacy_v0` 仅在解析旧 fixture 文本时出现，运行时一律 `edge_v1`。

## Requirements

### Requirement 1: 数据模型替换（Schema）

**User Story:** As a 后端开发者, I want Itinerary schema 用 nodes + hops 二元组取代 stages 单数组, so that LLM、critic、前端读到的「停留时长」与「通勤时长」语义不再漂移。

#### Acceptance Criteria

1. WHEN 后端构造任何 Itinerary 对象, THE itinerary SHALL 含 `nodes: list[ActivityNode]`（最少 2 项）+ `hops: list[Hop]`（长度 = len(nodes) - 1）+ `schema_version: "edge_v1"` + `schedule: list[ScheduleEntry]` 派生视图。
2. WHEN 任意 Itinerary 实例化时不变量被破坏（hops 长度 ≠ nodes 长度 - 1, 或首尾不是 home, 或首尾 home 的 duration_min ≠ 0）, THE Pydantic 校验 SHALL 抛出 ValidationError 阻止该对象被生成。
3. THE ActivityNode SHALL 含字段 `node_id / kind / target_kind / target_id / start_time / duration_min / title / note / lat / lng / address`，其中 `target_kind ∈ {poi, restaurant, home}`，`target_id` 在 `target_kind="home"` 时固定为 `"home"`。
4. THE Hop SHALL 含字段 `hop_id / from_node_id / to_node_id / start_time / minutes / mode / path_type / buffer_min`，其中 `mode ∈ {walking, taxi, bus, haversine_estimated, virtual}`，`path_type ∈ {real_route, estimated, in_place}`，`buffer_min` 默认 5。
5. THE OrderRecord SHALL 新增字段 `target_kind: Literal["poi", "restaurant"]`，与对应 node 的 `target_kind` 对齐；下单逻辑 SHALL 通过 `target_kind="restaurant"` 找节点，而不是查 `stage.restaurant_id`。
6. THE Itinerary SHALL 不再包含 `stages` 字段（一刀切替换，旧字段彻底删除）；前端通过 `schedule` 派生视图访问扁平化的「时间块列表」。
7. WHEN 服务进程读到带 `schema_version: "legacy_v0"` 的 fixture（仅静态文本测试场景）, THE 加载逻辑 SHALL 抛出 RuntimeError 拒绝运行时使用，避免双 schema 并存的歧义。

### Requirement 2: LLM 蓝图契约简化（BlueprintNode）

**User Story:** As LLM 蓝图规划师（DeepSeek/Qwen）, I want 只输出节点序列和停留时长, so that 我不再需要自己算 home→A 通勤时间，也不再需要在 prompt 里跟「下一段.start = 上一段.end + commute_matrix + 5min」这种公式较劲。

#### Acceptance Criteria

1. THE BlueprintNode SHALL 只含字段 `kind / target_kind / target_id / duration_min / note`；不含 `start_time`、`end_time`、`hop_minutes` 任何与时间相关的字段。
2. THE PlanBlueprint SHALL 含 `nodes: list[BlueprintNode]`（≥1 项中间节点，不含首尾 home）+ `preferred_start_time: str`（默认 "14:00"）+ `rationale: str`。
3. WHEN LLM 输出的 JSON 含旧字段（`stages` / `start_time` / `end_time`）, THE blueprint_llm.parse_blueprint SHALL 视为无效输出并触发 BlueprintGenError 让 critic backprompt 走重试链路。
4. THE BLUEPRINT_SYSTEM_PROMPT SHALL 删除「commute_matrix 查表代入」「下一段开始时间公式」「buffer 5 分钟」这三段共 ~2300 字符的内容；新 prompt 长度 ≤ 1500 字符。
5. WHEN LLM 反序场景（先吃饭再看展）输出 `nodes = [restaurant, poi]`, THE assemble_from_blueprint SHALL 按节点顺序拼装，不强制 POI 先于 Restaurant。
6. WHEN LLM 单段方案（只想吃饭）输出 `nodes = [restaurant]`, THE assemble_from_blueprint SHALL 自动加首尾 home，最终 itinerary 含 3 个节点 + 2 条 hops。
7. WHEN LLM 同地复用场景输出连续两个相同 `target_id` 的节点, THE assemble_from_blueprint SHALL 在中间塞一条 `minutes=0 / mode=virtual / path_type=in_place` 的 hop，不破坏 hops 长度不变量。

### Requirement 3: Assemble 算法重写（自动加首尾 home + 自动算 hops）

**User Story:** As assemble_from_blueprint 函数, I want 接收 LLM 输出的 mid nodes，自动补首尾 home + 调 lookup_hop 计算所有边, so that LLM 不需要算时间，但最终 Itinerary 时间轴严格自洽。

#### Acceptance Criteria

1. WHEN assemble_from_blueprint 被调用, THE 函数 SHALL 在 nodes 数组首部插入 home 节点（`node_id="n0" / target_kind="home" / start_time=blueprint.preferred_start_time / duration_min=0`），尾部插入对应的 home 终点节点。
2. THE assemble_from_blueprint SHALL 对每对相邻节点调 `lookup_hop(from_id, to_id, transport_pref, user_profile)` 解析通勤分钟数，时间游标按「prev_node.end + hop.minutes + buffer_min → next_node.start」推进。
3. THE 首跳 hop（home → mid_nodes[0]）SHALL 设 `buffer_min=0`（不在家里等 buffer）；非首跳 hop SHALL 设 `buffer_min=5`。
4. WHEN 所有节点和边构造完成, THE assemble SHALL 在内部断言 `len(hops) == len(nodes) - 1` 与「首尾必为 home」两条不变量；任一失败立即 RuntimeError，不返回半成品 Itinerary。
5. THE assemble SHALL 在返回前生成 `schedule: list[ScheduleEntry]`，把 nodes + hops 按 start_time 排序展平；每条目带 `entry_kind / ref_id / start / end / title / minutes / mode / hidden`，`hidden=true` 仅当 `path_type="in_place"`。
6. THE assemble.total_minutes SHALL 等于 `parse_hhmm(nodes[-1].start_time) - parse_hhmm(hops[0].start_time)`，与 schedule 派生视图的总跨度一致。
7. WHEN user_profile 缺 transport_preference 字段, THE assemble SHALL 默认按 `taxi` 调 lookup_hop。

### Requirement 4: lookup_hop 三级降级

**User Story:** As 边解析层, I want 单一函数收口「from→to → 分钟数」的所有降级路径, so that assemble 与 critic 看到的通勤时间永远一致，不会一边查 mock 一边猜距离。

#### Acceptance Criteria

1. WHEN `from_id == to_id`（同地复用）, THE lookup_hop SHALL 返回 `(0, "virtual", "in_place")`，不查任何外部数据。
2. WHEN routes.json 含对应边且 `transport_pref` 对应字段非空, THE lookup_hop SHALL 返回 `(route.{transport_pref}_minutes, transport_pref, "real_route")`。
3. IF routes.json 不含对应边但两端节点都有 lat/lng, THEN THE lookup_hop SHALL 用 haversine 直线距离 × 路网折算系数 1.3 × 模式速度（walking 5km/h, taxi 25km/h, bus 18km/h），返 `(estimated_min, "haversine_estimated", "estimated")`。
4. IF 坐标缺失或所有降级失败, THEN THE lookup_hop SHALL 返回 `(15, transport_pref, "estimated")` 保守兜底，让流程能继续而不是抛异常。
5. THE lookup_hop SHALL 被 assemble 与 critics_v2._check_hop_feasibility 共同调用；双方对同一 (from, to) 输入 SHALL 永远返回相同的 (minutes, mode, path_type)。

### Requirement 5: critic 重写（hop_feasibility 取代 inter_stage_commute）

**User Story:** As critics_v2 验证层, I want 直接读 hop.minutes 与节点 start_time 验证可达性, so that 不再需要在「过程段」与「停留段」之间做特例判断（删除 _is_commute_stage hack）。

#### Acceptance Criteria

1. THE critics_v2.validate_itinerary SHALL 接受 `Itinerary`（含 nodes/hops）+ `IntentExtraction` + `user_id`，返回 `list[Violation]`。
2. WHEN critic 跑 `_check_hop_feasibility`, THE 函数 SHALL 遍历每条 hop，对每条非 `in_place` 的 hop 调 `lookup_hop(from_node.target_id, to_node.target_id, ...)` 取 `actual_min`，断言 `hop.minutes >= actual_min - 2`（容差 2 分钟）。
3. WHEN critic 跑 `_check_temporal_feasibility`, THE 函数 SHALL 验「from_node.end + hop.minutes + hop.buffer_min ≤ to_node.start + 容差 2min」对每条 hop 成立。
4. THE critics_v2 SHALL 删除 `_is_commute_stage` 函数与所有「过程段特例判断」逻辑；通勤过程是 hop 不是 node，不存在「这是否过程段」的判断需求。
5. WHEN critic 命中 critical 违规, THE format_violations_for_llm SHALL 把违规位置以人话描述（"第 2 段去 P040 的通勤时间不够"），不暴露 `nodes[1] / hops[0]` 等 dot-path 字符串给 LLM。
6. THE critics_v2 现有的 8 类 critic 中，`_check_duration` / `_check_distance` / `_check_dietary` / `_check_demo_restaurant_full` / `_check_social_context` SHALL 仅替换字段路径（stages → nodes），逻辑不变。
7. THE critics_v2 SHALL 新增结构 invariant 检查（`len(hops) == len(nodes)-1` / 首尾 home），违反即 critical。

### Requirement 6: LangGraph 节点字段路径同步

**User Story:** As LangGraph 拓扑, I want assemble / critic / replan / execute_finalize / refiner 节点全部读 nodes/hops 而不是 stages, so that 整条规划链路对新 schema 透明。

#### Acceptance Criteria

1. THE `agent/graph/nodes/assemble.py` SHALL 调新版 assemble_from_blueprint 并注入 DecisionTrace，trace.field_path 引用从 `stages[i]` 改为 `nodes[i]` / `hops[j]`。
2. THE `agent/graph/nodes/critic.py` SHALL 调新版 critics_v2.validate_itinerary，violations 累积逻辑不变。
3. THE `agent/graph/nodes/replan.py` SHALL 不变；replan 决策只读 retry_count + violations，不依赖 itinerary 字段细节。
4. THE `agent/graph/nodes/execute_finalize.py` SHALL 通过遍历 `itinerary.nodes` 找 `target_kind="restaurant"` 的节点构造 ReserveRestaurantInput，不再读 `stage.restaurant_id`。
5. THE `agent/graph/nodes/refiner.py` SHALL 不变；refiner 只改 intent，不改 itinerary。
6. THE `agent/graph/sse_adapter.py` SHALL 在 ITINERARY_READY 事件 payload 中含完整 nodes + hops + schedule + decision_trace；前端可任选其一渲染。

### Requirement 7: rule planner / hybrid ILS / blueprint critic 字段同步

**User Story:** As 规划兜底链路（rule planner / planner_hybrid / agent.critics 旧 hybrid critic）, I want 全部用新 schema 输出 Itinerary, so that 三种规划路径产物一致，前端不需要根据来源做兼容渲染。

#### Acceptance Criteria

1. THE `agent/planner.py` 的 `_assemble_itinerary` SHALL 输出新 schema 的 Itinerary（nodes + hops），不再返回带 stages 的对象。
2. THE `agent/planner_hybrid.py` 的 ILS 邻域操作（swap / shift）SHALL 改为操作节点而不是段；对应 helper 重命名为 `swap_node` / `shift_node`。
3. THE `agent/segment_decider.py` SHALL 重命名为 `node_decider.py`；`decide_segments` 函数重命名为 `decide_nodes`，返回中间节点的 kind 列表（不含首尾 home）。原文件保留为 `from .node_decider import *` 的兼容 alias，避免外部 import 损坏。
4. THE `agent/blueprint.py` 的旧 `_temporal_critic` / `_duration_critic` / `_opening_hours_critic` SHALL 改读 nodes/hops，删除 `BlueprintTargetKind.NONE`（new 模型不再有「过程段」概念）。
5. THE `agent/critics.py`（旧 hybrid critic）SHALL 把 `HardConstraintCritic._check_required_kinds` / `TimeWindowCritic` / `SocialContextStyleCritic` 的字段路径改为 nodes/hops。

### Requirement 8: 前端 types + ItineraryCard + MapOverlay 同步

**User Story:** As 前端 ItineraryCard / MapOverlay / DecisionTraceCard, I want 默认遍历 schedule 派生视图渲染（v1 兼容），并在地图上把节点画成 marker 而不是把过程段也画上, so that 视觉上把「在哪干啥」与「怎么过去」分清。

#### Acceptance Criteria

1. THE `frontend/lib/types.ts` SHALL 用 `ActivityNode + Hop + ScheduleEntry` 替换 `ItineraryStage` 类型；新增 `schema_version: "edge_v1"` 字段。
2. THE `frontend/components/ItineraryCard.tsx` SHALL 默认遍历 `itinerary.schedule` 渲染时间块；`hidden=true` 的条目不渲染。
3. WHEN 时间块的 `entry_kind="hop"` 且 `mode !== "virtual"`, THE ItineraryCard SHALL 渲染细长条「通勤 N 分钟（mode）」（视觉权重低于节点卡片）。
4. WHEN 用户点击 hop 行, THE ItineraryCard SHALL 展开通勤详情（v2 可选；v1 仅 hover 提示）。
5. THE `frontend/components/MapOverlay.tsx` SHALL 改读 `itinerary.nodes` 而不是 `itinerary.stages`；只对 `target_kind` ∈ {poi, restaurant} 的节点画 marker，home 节点不画。
6. THE `frontend/components/DecisionTraceCard.tsx` SHALL 把 violation field_path 引用从 `stages[i]` 改为 `nodes[i] / hops[j]`，但显示给用户的文案不变。
7. THE `frontend/lib/store.ts` SHALL 接收新 itinerary payload；refine 流的 previousItinerary 快照逻辑不变（只换字段名，不换结构）。

### Requirement 9: SSE 事件契约

**User Story:** As 前端 SSE 消费层, I want itinerary_ready 事件 payload 直接是新 schema, so that 不需要额外做兼容转换。

#### Acceptance Criteria

1. THE SseEventType.ITINERARY_READY 事件 payload SHALL 等于 `Itinerary.model_dump()`（含 schema_version / nodes / hops / schedule / orders / share_message / total_minutes / decision_trace）。
2. THE 现有事件类型（INTENT_PARSED / TOOL_CALL_START/END / REPLAN_TRIGGERED / AGENT_THOUGHT / AGENT_NARRATION / DONE / STREAM_ERROR）SHALL 不变；payload 不受 schema 重构影响。
3. WHEN 前端收到 schema_version ≠ "edge_v1" 的 itinerary_ready, THE 客户端 SHALL 在 console 报警并回退降级渲染（仅显示 summary + total_minutes 文本），保证 demo 不挂。

### Requirement 10: 测试与回归门禁

**User Story:** As 回归门禁, I want 所有单测全过 + 4 场景端到端 + 8 条不变量在 fuzz 测试下成立, so that 重构不破坏现有 267 个测试。

#### Acceptance Criteria

1. WHEN pytest 全量运行, THE 现有 267 项 + 新增 ~25 项 SHALL 全部 pass，无 xfail 转 xpass 的悬空状态。
2. THE `tests/test_assemble_blueprint.py` SHALL 覆盖 4 种场景（标准 / 单段 / 同地复用 / 反序）+ 8 条不变量断言。
3. THE `tests/test_critics_v2_commute.py` SHALL 重命名为 `test_critics_v2_hop.py`；覆盖 hop 合法 / hop.minutes 偏小 / in_place 跳过 / 数据缺失保守兜底 4 项。
4. THE `tests/test_edge_model_invariants.py` SHALL 用随机 fuzz 10 个 blueprint 跑 assemble，每次断言全部 8 条不变量。
5. THE `backend/scripts/verify_edge_model.py` SHALL 端到端跑 4 场景（家庭半日 / 只想吃饭 / 同地复用 / 反序），断言核心不变量 + critic 不命中 critical。
6. THE `backend/scripts/verify_langgraph.py` SHALL 字段路径全量替换后仍 pass；浏览器 demo「家庭主线」场景不再触发 ILS 死循环（核心症状）。
7. THE 8 场景端到端测试 (`test_8_scenarios.py`) SHALL 字段路径替换后全部 pass，断言改为 nodes 数量 + 关键 kind 命中。

### Requirement 11: 一刀切迁移与降级策略

**User Story:** As 项目维护者, I want 一次性替换 schema 而不是双 schema 并存, so that 不需要在每个组件里写 isinstance(stage, OldStage) ? old_handler : new_handler。

#### Acceptance Criteria

1. THE 重构 SHALL 一次性删除 `ItineraryStage` 类型与 `BlueprintStage` 类型；不保留任何「双 schema 兼容字段」。
2. WHEN 服务进程在迁移过程中重启, THE InMemoryRepository 进程内的 itinerary_snapshot SHALL 自动失效（无跨进程持久化），不需要数据迁移脚本。
3. THE Phase 0.20 的 ConversationRepositoryStub（Redis）SHALL 仍保持 NotImplementedError 状态；本次重构不接 Redis。
4. WHEN LLM 偶发输出旧字段（仍按 stages 输出）, THE blueprint_llm.parse_blueprint SHALL 抛 BlueprintGenError，触发 LangGraph 既有重试链；不在解析层做兼容转换。
5. WHEN 任何上游模块（如 collab/room.py）拿到旧 schema fixture, THE 系统 SHALL 在 Pydantic 校验层报错，让问题暴露在最早期，不让旧数据透传到 critic / 前端。

## Out-of-Scope（v1 不做，留 v2）

```
| 范围                              | v2 留坑                                              |
|-----------------------------------|------------------------------------------------------|
| Hop 单独 emit SSE 事件            | hop 不是用户感知粒度的事件，本次不拆                 |
| 跨日时段（夜宵 23:00-01:00）的 UI | start_time 数据层兼容（24+），UI 不强求改造          |
| Hop 详情卡（点击展开通勤详情）    | v1 仅 hover；v2 可做 Modal                          |
| ConversationRepository Redis 接入 | 与 schema 重构正交                                   |
| 真实美团 / 高德 API 切换          | 与 schema 重构正交（mock_data 层不变）               |
| Itinerary 历史版本号兼容          | edge_v1 是首版；之后版本演进时再做 schema migration  |
```
