# 规划链路深度审查矩阵（Phase 1）

> **触发**：用户在 demo 中发现「家庭主线 5 岁娃博物馆主活动 2.5 小时」，直觉过长（行业常识 60-90min）。
> **目的**：用 8 个并行子代理逐子环节深度审查规划链路，找出业务合理性、工程严谨度、业界对标三方面的全部 gap。
> **产出**：8 份独立报告 + 1 份联合审查 → 2 份 spec（业务质量修复 + 目录重组）。
> **绝对约束**：本 Phase 只**审查 + 写报告**，不动任何代码。

---

## 一、规划链路 25 子环节清单

```
| 层级       | #  | 子环节                    | 文件                                          |
|-----------|----|--------------------------|----------------------------------------------|
| 意图理解   | 1  | Router                   | agent/router.py + prompts/router_prompt.py   |
|           | 2  | IntentParser              | agent/intent_parser.py + prompts/system_prompt.py |
|           | 3  | Refiner                   | agent/refiner.py + prompts/refiner_prompt.py |
|           | 4  | NodeDecider               | agent/node_decider.py（+ segment_decider alias） |
| 候选搜索   | 5  | GetUserProfile            | tools/get_user_profile.py                    |
|           | 6  | SearchPois                | tools/search_pois.py                         |
|           | 7  | SearchRestaurants         | tools/search_restaurants.py                  |
|           | 8  | CheckRestaurantAvail      | tools/check_restaurant_availability.py       |
|           | 9  | EstimateRouteTime         | tools/estimate_route_time.py                 |
| 蓝图生成   | 10 | WeightsLLM                | agent/weights_llm.py                         |
|           | 11 | BlueprintLLM              | agent/blueprint_llm.py                       |
|           | 12 | BlueprintPrompt           | agent/prompts/blueprint_prompt.py            |
| 客观验证   | 13 | BlueprintCritic           | agent/blueprint.py（_temporal/_duration/_opening_hours）|
|           | 14 | CriticsV2 (8 类)          | agent/v2/critics_v2.py                       |
|           | 15 | Critics (旧 hybrid)       | agent/critics.py                             |
| 拼装+边路  | 16 | AssembleBlueprint         | agent/assemble_blueprint.py                  |
|           | 17 | LookupHop                 | agent/lookup_hop.py                          |
|           | 18 | PlannerHybrid (ILS)       | agent/planner_hybrid.py                      |
|           | 19 | Planner (rule)            | agent/planner.py                             |
|           | 20 | PlannerLlmFirst           | agent/planner_llm_first.py                   |
| 执行+文案  | 21 | Narrator                  | agent/narrator.py + prompts/narrator_prompt.py |
|           | 22 | ExecuteFinalize           | agent/graph/nodes/execute_finalize.py + executor.py |
| 周边数据   | 23 | mock POI / Restaurant     | mock_data/{pois,restaurants}.json + schemas/domain.py |
|           | 24 | mock UserProfile / Persona | mock_data/{user_profile,personas}.json      |
|           | 25 | LangGraph 主路径 + SSE 适配 | agent/graph/{state,build,sse_adapter}.py + nodes/* |
```

## 二、8 agent 任务分配

```
| Agent | 范围 # | 主线焦点                                    | 估时 |
|-------|-------|--------------------------------------------|------|
| A     | 1-4   | 意图层（理解上限）                          | 25min |
| B     | 5-9   | 候选搜索（数据筛选合理性）                  | 30min |
| C     | 17,9  | 通勤计算（lookup_hop 一致性、估值偏差）     | 20min |
| D     | 10-12 | LLM 蓝图（提示词 / 决策核心）—— ★高杠杆     | 35min |
| E     | 13-15 | Critic 三套（业务约束执行层）—— ★高杠杆     | 35min |
| F     | 16,18-20 | 算法 / 拼装层（ILS / rule / fallback）   | 30min |
| G     | 23-24 | mock 数据 schema（信息源）—— ★高杠杆      | 30min |
| H     | 21-22,25 | 输出与图编排（narrator / SSE / state）   | 25min |
```

总时 ~3.5 小时（按串行；并行 ~2 小时）。

## 三、每个 agent 输出格式（强制 6 段）

```markdown
# Agent X 审查报告

## 1. 现状摘要（每个子环节做了什么）
- 子环节 N：....
- 子环节 N+1：....

## 2. 业务合理性 gap 清单（按 P0/P1/P2 + 配反例）

### P0（demo 立刻翻车）
- [P0-1] gap 标题
  - 现象：5 岁娃博物馆 2.5h
  - 根因：blueprint_prompt 没列年龄分级时长
  - 反例：....
  - 修复方向：....

### P1（用户不会立刻发现，但会侵蚀信任）
- ...

### P2（潜伏 bug、长期债）
- ...

## 3. 业界对标 diff（必须查 1-3 个开源项目/论文/产品）
- 对标项目 1：....（链接 / 论文）
  - 他们怎么做：....
  - 我们差在哪：....
  - 借鉴成本：....
- 对标项目 2：....
- 对标项目 3：....

## 4. 修复方案候选（每条带工时 + 跨环节依赖）
- 方案 A：....
  - 工时：~XX 分钟
  - 影响子环节：....
  - 风险：....
- 方案 B：....

## 5. 目录归属建议（A1 融合）
- 该子环节的文件应归属：
  - core / intent / planning / runtime / graph / legacy
- 是否应与其他文件合并 / 删除：
  - ....
- 是否冻结：....

## 6. 跨环节依赖警示（你看到但其他 agent 看不到的）
- 我修这里会影响：....
- 我依赖另一处先修：....
```

## 四、子代理通用约束（必读，违反扣报告分）

```
| 约束                                 | 理由                              |
|-------------------------------------|----------------------------------|
| ❌ 不动代码 / 不 commit / 不删文件   | Phase 2 仅审查                   |
| ✅ 必须 read 全文，不能瞎猜          | 防 hallucination                 |
| ✅ 业界对标必查 1-3 个真实项目       | 不闭门造车                        |
| ✅ 反例必带具体输入 + 期望 vs 实际   | 不接受 hand-wave                 |
| ✅ P0/P1/P2 分级必须有，不能堆一锅粥 | 让 Phase 3 排序有依据             |
| ✅ 引用代码必带文件:行号             | 让 Phase 4 联合审查可复核         |
| ✅ 报告写到 reports/agent-X/report.md | 集中产出，便于 Phase 3-5 聚合    |
| ✅ 报告语言：中文                    | 用户全局规则                      |
```

## 五、参考材料（每个 agent 都要读）

```
| 路径                                                 | 用途                            |
|-----------------------------------------------------|--------------------------------|
| docs/01-requirements/需求分析.md                    | 业务上下文 + §5.7 schema       |
| docs/01-requirements/演示场景集.md                  | 8 场景期望行为                  |
| docs/03-implementation/pitfalls.md                  | 历史踩过的坑（防再踩）           |
| problem.md                                          | 历史会话决策                     |
| .kiro/specs/itinerary-edge-model-refactor/design.md | 上次重构的 edge_v1 模型语义      |
| AGENTS.md                                            | 工程铁律 + §3.3.1 编排层冻结     |
```

## 六、产出收口（Phase 3-5 由编排者做）

- Phase 3：编排者读 8 份报告 → 画跨环节依赖图 → 排修复优先级（reports/synthesis/dependency-graph.md）
- Phase 4：派 1 个独立 agent 拿汇总做对抗审查（reports/synthesis/adversarial-review.md）
- Phase 5：编排者输出 2 份 spec：
  - `.kiro/specs/planning-quality-deep-review/`（requirements / design / tasks）
  - `.kiro/specs/agent-directory-restructure/`（同上）
