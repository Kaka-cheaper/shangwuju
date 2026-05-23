# Implementation Plan: Itinerary Edge Model Refactor

## Overview

把行程数据模型从「Stage」重构为「ActivityNode + Hop」二元组。一刀切替换，不双 schema 并存。

**总工时预估**：~10-14 小时（一个工作日内可完成）。
**关键路径**：T1 schema → T2 lookup_hop → T3 assemble → T4 critic → T5 prompt → T6 LangGraph → T7 旁路（rule/hybrid）→ T8 前端 → T9 测试 → T10 端到端验证。

**核心约束**：
- LangGraph 拓扑保留（只换数据，不改图结构）
- mock_data 不动（routes.json 已是 edge 表，正好对齐）
- 8 个 Tool 不动（与 itinerary schema 解耦）
- 后端先跑通（T1-T7），再上前端（T8）；测试与 schema 同步推进（T9 与 T1-T7 并行验证）

## Tasks

- [x] 1. [R1] 重写 `backend/schemas/itinerary.py`：删除 `ItineraryStage`，新增 `ActivityNode + Hop + ScheduleEntry`；`Itinerary` 加 `schema_version: Literal["edge_v1"] = "edge_v1"` + `nodes: list[ActivityNode]` + `hops: list[Hop]` + `schedule: list[ScheduleEntry]`；`OrderRecord` 加 `target_kind: Literal["poi", "restaurant"]` 字段；用 Pydantic `model_validator(mode="after")` 实现 invariant 校验（hops 长度 = nodes-1 / 首尾必为 home / home duration_min=0）；保留 `total_minutes` / `summary` / `share_message` / `decision_trace` 字段不变

- [x] 2. [R2] 重写 `backend/agent/blueprint.py`：删除 `BlueprintStage` 与 `BlueprintTargetKind.NONE`，新增 `BlueprintNode`（仅含 `kind / target_kind / target_id / duration_min / note`）+ `PlanBlueprint`（`nodes: list[BlueprintNode]` + `preferred_start_time: str = "14:00"` + `rationale: str`）；删除旧 `_temporal_critic` / `_duration_critic` / `_opening_hours_critic` 中的 stage 概念，改读 nodes（注意：这些 critic 现在只验「nodes 不重叠 + 营业时间覆盖」，不验通勤）

- [x] 3. [R4] 新建 `backend/agent/lookup_hop.py`：实现 `lookup_hop(from_id: str, to_id: str, transport_pref: str, user_profile: UserProfile) -> tuple[int, HopMode, HopPathType]`，三级降级：(a) `from==to` 返 `(0, "virtual", "in_place")`；(b) 查 routes.json 返 `(min, transport_pref, "real_route")`；(c) haversine + 路网折算 1.3 + 模式速度（walking 5km/h, taxi 25km/h, bus 18km/h）返 `(est, "haversine_estimated", "estimated")`；(d) 全失败返 `(15, transport_pref, "estimated")` 保守兜底；带 unit test 4 项覆盖 4 级降级

- [x] 4. [R3] 重写 `backend/agent/assemble_blueprint.py`：`assemble_from_blueprint(intent, blueprint, user_profile) -> Itinerary` 整体替换；流程：(a) 从 user_profile 取 transport_preference；(b) 在 nodes 首部插 home 节点（n0）；(c) 遍历 blueprint.nodes 调 lookup_hop 算每条 hop，时间游标推进 prev.end + hop.minutes + buffer → next.start，首跳 buffer=0 非首跳 buffer=5；(d) 尾部插 home 终点节点；(e) 生成 schedule 派生视图（按 start_time 排序展平 nodes+hops，path_type=in_place 的 hop 标 hidden=true）；(f) 返回前 assert 不变量（hops 长度 = nodes-1 / 首尾 home），失败 RuntimeError；删除旧 `_resolve_coord_and_address` 等 stage 辅助函数

- [x] 5. [R5] 重写 `backend/agent/v2/critics_v2.py`：把 `_check_inter_stage_commute` 替换为 `_check_hop_feasibility`（遍历 hops，非 in_place 调 lookup_hop 取 actual_min，断言 hop.minutes >= actual_min - 2）；新增 `_check_temporal_feasibility`（验 from_node.end + hop.minutes + buffer ≤ to_node.start + 容差 2min）；新增 `_check_invariants`（hops 长度 / 首尾 home / home duration=0 三条结构断言，违反即 critical）；删除 `_is_commute_stage` 与 `_resolve_stage_location` 中的「过程段」分支；其它 critic（duration / distance / dietary / demo_restaurant_full / social_context）字段路径替换 stages → nodes，逻辑不变；`format_violations_for_llm` 改为人话表达不暴露 dot-path

- [x] 6. [R2] 重写 `backend/agent/prompts/blueprint_prompt.py`：`BLUEPRINT_SYSTEM_PROMPT` 删除「commute_matrix 查表代入」「下一段 start_time 公式」「buffer 5 分钟」三段（共 ~2300 字符）；新 prompt 仅说明：(a) 你只输出 mid nodes，不输出 home 起终点；(b) 每个 node 含 `kind / target_kind / target_id / duration_min`；(c) 不要输出 start_time / end_time / hop 时间，系统会自动算；(d) 选 target_id 必须在候选预览里存在 + opening_hours 覆盖；(e) 反序 / 单段 / 同地复用都允许；新长度目标 ≤ 1500 字符；`build_user_message` 改候选预览结构（删除 commute_matrix）

- [x] 7. [R2] 重写 `backend/agent/blueprint_llm.py`：`generate_blueprint` 改为只解析 `nodes` 数组（拒绝旧 `stages` 字段触发 BlueprintGenError）+ `preferred_start_time` + `rationale`；`build_candidate_preview` 删除 `commute_matrix` 字段（assemble 自己算 hop，不需要喂给 LLM）；保留 review_excerpts UGC 引用逻辑

- [x] 8. [R6] 修改 LangGraph 节点字段路径：`agent/graph/nodes/assemble.py` 调新 assemble_from_blueprint，DecisionTrace.field_path 改 nodes[i] / hops[j]；`agent/graph/nodes/critic.py` 调新 critics_v2，violations 累积逻辑不变；`agent/graph/nodes/execute_finalize.py` 改用 `next(n for n in itinerary.nodes if n.target_kind == "restaurant")` 找用餐节点构造 ReserveRestaurantInput；`agent/graph/nodes/replan.py` 不动；`agent/graph/sse_adapter.py` ITINERARY_READY payload 自然是 `itinerary.model_dump()`，含 nodes+hops+schedule

- [x] 9. [R7] 同步 rule planner / hybrid ILS / segment_decider：`agent/planner.py:_assemble_itinerary` 输出新 schema（nodes+hops），删除 5 段写死逻辑；`agent/planner_hybrid.py` 的 ILS 邻域操作 `_swap_poi / _swap_rest / _shift_time` 重命名为 `_swap_node / _shift_node` 且操作目标改为 nodes；`agent/segment_decider.py` 重命名为 `node_decider.py`，函数 `decide_segments` 重命名为 `decide_nodes`（返回中间节点 kind 列表），原文件保留 `from .node_decider import *` 兼容 alias；`agent/critics.py`（旧 hybrid critic）字段路径替换

- [x] 10. [R5+R7] 同步 refiner / 旁路：`agent/refiner.py` 不改逻辑（只改 intent，不读 itinerary 字段）；`backend/main.py` 中 confirm 流的 `_collect_itinerary_tags` 与 `_accumulate_memory_after_confirm` 改读 `itinerary.nodes` 而不是 `stages`；`backend/collab/room.py` 中 `current_itinerary_dict` 读取逻辑保持 dict 形式（pydantic dump 后字段自动是 nodes/hops），无需改动；`agent/v2/social_compat.py:evaluate_poi/evaluate_restaurant` 函数签名加 `node: ActivityNode` 形参（取代旧 stage 参数）

- [x] 11. [R8] 前端 types + ItineraryCard：`frontend/lib/types.ts` 用 `ActivityNode + Hop + ScheduleEntry` 替换 `ItineraryStage`，`Itinerary` 加 `nodes / hops / schedule / schema_version` 字段并删除 `stages` 字段；`OrderRecord` 加 `target_kind`；`frontend/components/ItineraryCard.tsx` 默认遍历 `itinerary.schedule` 渲染（hidden=true 不渲染），`entry_kind="hop"` 且 `mode!=="virtual"` 时渲染细长条「通勤 N 分钟（mode）」（视觉权重低于 node 卡片）；intent_chips / orders / share_message 渲染逻辑不变

- [x] 12. [R8] 前端 MapOverlay + DecisionTraceCard + store：`frontend/components/MapOverlay.tsx` 改读 `itinerary.nodes`，只对 `target_kind ∈ {poi, restaurant}` 的节点画 marker，home 节点不画；marker 间路径用 nodes 顺序连，不画 stage；`frontend/components/DecisionTraceCard.tsx` violation field_path 引用从 stages[i] 改 nodes[i]/hops[j]，显示文案不变；`frontend/lib/store.ts` 中 `previousItinerary` 快照逻辑不变（structuredClone 整个 itinerary）；`frontend/lib/sse.ts` 不改（解析器与字段无关）

- [x] 13. [R9] SSE schema 兼容降级：`frontend/lib/store.ts` 在 itinerary_ready handler 加 `if (payload.schema_version !== "edge_v1") { console.warn(...); 仅渲染 summary + total_minutes 文本 }` 降级路径；后端 `agent/graph/sse_adapter.py` 不需改（payload 自动带新 schema_version）

- [x] 14. [R10] 单测全量替换：`tests/test_assemble_blueprint.py` 重写覆盖 4 场景（标准 / 单段 / 同地复用 / 反序）+ 8 条不变量断言；`tests/test_critics_v2_commute.py` 重命名为 `test_critics_v2_hop.py` 覆盖 4 项（hop 合法 / hop.minutes 偏小 / in_place 跳过 / 数据缺失保守兜底）；`tests/test_blueprint.py` / `test_blueprint_llm.py` / `test_decision_trace_integration.py` 字段路径替换；`tests/test_segment_decider.py` 重命名 `test_node_decider.py` 断言 `decide_nodes` 输出；`tests/test_8_scenarios.py` 字段路径替换并改用 nodes 数量 + kind 命中断言

- [x] 15. [R10] 新建 `tests/test_edge_model_invariants.py`：随机 fuzz 10 个 blueprint（mid nodes 数 1~5、target_kind 随机、target_id 从 mock 候选随机选）跑 assemble，每次断言 8 条不变量（hops 长度 / 首尾 home / home duration=0 / hop start 与 from_node end 对齐 / to_node start ≥ hop end + buffer / total_minutes 自洽 / in_place hop minutes=0 且 from-to 同 target_id / home target_id="home"）；任一断言失败立即 failure

- [x] 16. [R10] 新建 `backend/scripts/verify_edge_model.py`：4 场景端到端断言（S1 家庭半日 → 3 mid nodes / 4 hops / total ≈ 270min / 首尾 home / no critical；S2 只想吃饭 → 1 mid node / 2 hops / total ≈ 90min；S3 同地复用 → 中间 hop minutes=0 mode=virtual；S4 反序场景 → mid nodes 顺序 [restaurant, poi]）；与现有 `verify_langgraph.py` / `verify_phase0_5.py` 一起跑，全过即门禁通过

- [x] 17. [R10+R11] 浏览器端到端验证：跑 `pnpm dev` 后用「家庭主线」场景输入「今天下午想和老婆孩子出去玩几个小时，别离家太远」，确认：(a) ITINERARY_READY 事件 payload 含 `schema_version: "edge_v1"` + nodes + hops + schedule；(b) ItineraryCard 时间轴正常渲染含 hop 行（细长条）；(c) MapOverlay 只画节点 marker；(d) DecisionTraceCard 不再显示「LLM 修正后通过」误导 chip；(e) 不再触发 ILS 死循环（核心症状回归）；(f) 浏览器 console 无 schema_version 兼容警告

## Task Dependency Graph

```json
{
  "waves": [
    [1],
    [2, 3],
    [4],
    [5, 6, 7],
    [8, 9, 10],
    [11, 12, 13],
    [14, 15],
    [16],
    [17]
  ]
}
```

说明：
- **Wave 1（schema 基座）**：Task 1（itinerary schema）必须最先完成，下游全部依赖。
- **Wave 2（独立基础设施）**：Task 2（blueprint schema）+ Task 3（lookup_hop）可并行，互不依赖。
- **Wave 3（assemble 串联）**：Task 4 依赖 Task 1+2+3。
- **Wave 4（消费方与上游）**：Task 5（critic）+ Task 6（prompt）+ Task 7（blueprint_llm）可并行，分别依赖 Task 4 / 2 / 2。
- **Wave 5（LangGraph + 旁路同步）**：Task 8（LangGraph nodes）+ Task 9（rule/hybrid）+ Task 10（refiner/main/collab/social_compat）可并行，分别依赖各自组件。
- **Wave 6（前端）**：Task 11（types + ItineraryCard）+ Task 12（MapOverlay + DecisionTraceCard）+ Task 13（schema_version 降级）可并行，依赖 Wave 5 后 SSE payload 已是新 schema。
- **Wave 7（测试）**：Task 14（单测全量）+ Task 15（fuzz invariants）可并行，依赖 Wave 5 后端就绪。
- **Wave 8（端到端门禁）**：Task 16 跑 verify_edge_model 脚本，依赖 Wave 7 单测全过。
- **Wave 9（浏览器验证）**：Task 17 跑 dev server 实地确认，是终极门禁。

## Notes

- **一刀切原则**：删除 `ItineraryStage` 与 `BlueprintStage` 类型，不留兼容字段。任何旧字段透传立即报 Pydantic ValidationError，让问题暴露在最早期。
- **Hackathon 时间盒**：v1 不做 hop 单独 SSE / 跨日 UI / hop 详情卡（留 v2）。schedule 派生视图保证前端 v1 复用现有时间轴渲染逻辑。
- **InMemoryRepository 失效**：服务进程重启后 itinerary_snapshot 自动失效，不需要数据迁移脚本。Redis stub 仍保持 NotImplementedError 状态。
- **测试基线**：现有 ~267 项 pytest 全部 pass + 新增 ~25 项（assemble 4 + critic_hop 4 + invariants 10 + 节点决策 ~7）。无 xfail 转 xpass。
- **死循环回归确认**（Task 17 核心目标）：旧版「家庭主线」复现死循环（11+ 次 ILS 兜底）；新版应一次 plan 直出过 critic，至多 1 次 backprompt。
- **commit 节奏**：每个 Wave 一个 commit，commit message 包含 task 编号便于回溯。Wave 1-3 完成后即可独立验证 schema + assemble 不变量；Wave 5 完成后 LangGraph 端到端跑通；Wave 9 是发布门禁。
- **回滚策略**：每个 Wave 一个 git tag（如 `edge-refactor-w3-assemble`），任何 Wave 出问题可 `git reset --hard` 回到上一个 tag。
- **后端先行原则**：T1-T10 后端必须全过 + 单测全过，再开始 T11-T13 前端改动。前后端 schema 不一致会让浏览器渲染崩。

## Risk & Mitigation

```
| 风险                                    | 概率 | 影响 | 缓解                                             |
|----------------------------------------|------|-----|--------------------------------------------------|
| LLM 偶发输出旧 stages 字段             | 中   | 低  | blueprint_llm 解析层抛 BlueprintGenError，走重试 |
| invariant 在边界场景被破坏              | 中   | 中  | T15 fuzz 10 case，T16 端到端 4 场景双重保险      |
| 前端 ItineraryCard 渲染 hop 行视觉过重  | 中   | 低  | v1 用细长条（视觉权重低），v2 再做 hop 详情卡    |
| collab/room.py 跨进程 itinerary_dict   | 低   | 低  | dict 形式天然兼容字段名变化；无需改逻辑          |
| 测试基线漂移（隐性 import 链路）         | 低   | 中  | T14 全量替换，跑 pytest -v 看每条测试明细        |
| 重构期间 demo 跑不通                    | 高   | 低  | Wave 1-9 严格按依赖顺序；每 Wave commit 一次     |
```
