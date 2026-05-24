# Agent 7：多 Agent + 检索增强 / 个性化记忆 / RAG-based Travel Agent 调研报告

> 范式编号：候选 7（晌午局算法重构 spec C，Phase 1 第二批补强 3/4）
> 报告位置：`.kiro/specs/algorithm-redesign/research/agent-7-multi-agent-rag/report.md`
> 调研日期：2026-05-24
> 撰写者：Agent 7 子代理（隔离阅读，未读其他 sub-agent 报告）

---

## 0 · 范式定位与选材范围

本范式落在「学术 benchmark」与「商业产品」之间的中间地带：研究界与工业界各取一半。学术侧追求构造与评测可复现的多 agent 协作 + RAG 框架；工业侧（Aimpoint / DocentPro / Vaiage）把这套思路落地成生产系统，更关心延迟、缓存、工具调用稳定性。我把六类工作并入同一个范式，因为它们共享三个支柱：

1. **多 Agent 协作**（角色化分工、shared state、validator 回环）
2. **检索增强（RAG）**（POI / 评论 / 历史轨迹的向量或结构化检索）
3. **持久化用户记忆**（hard / soft / commonsense 三层 schema、长短期分离）

调研选材聚焦这 6 篇/案例（按时间排序）：

```text
| 编号 | 工作                  | 出处                                | 类型      | 与本范式相关度          |
|------|-----------------------|-------------------------------------|-----------|------------------------|
| P1   | UrbanLLM              | arXiv 2406.12360（EMNLP 2024 投稿） | 学术微调  | 城市活动规划 / 任务分解 |
| P2   | TravelAgent (Fudan)   | arXiv 2409.08069（2024-09）         | 学术系统  | 4 模块 + 3 层记忆       |
| P3   | TP-RAG                | arXiv 2504.08694（EMNLP 2025）      | 学术 benchmark | RAG + 时空 trip plan |
| P4   | Vaiage                | arXiv 2505.10922（2025-05）         | 学术 + demo | 多 agent + 地图反馈    |
| P5   | TriFlow               | arXiv 2512.11271（WWW 2026 投稿）   | 学术 SOTA | 渐进多 agent 三段式    |
| I1   | Aimpoint Digital      | Databricks Blog（2024-12）          | 工业实战  | 多 RAG 并行检索         |
| I2   | DocentPro             | LangChain Blog（2025-04）           | 工业实战  | LangGraph 4 域 agent   |
```

> ⚠ 注：我没找到中文场景下的一手 trip planning 论文（TravelAgent 作者在复旦但 demo 用英文 + Google Maps）。中文场景的特殊约束在第 6 节单列推断。

---

## 1 · 维度 1：多 Agent 协作架构

### 1.1 Agent 数量与角色划分

按数量与角色摸排：

```text
| 工作        | Agent 数量          | 角色清单（论文原称）                                                    | 协作模式    |
|-------------|--------------------|------------------------------------------------------------------------|------------|
| UrbanLLM    | 1（fine-tuned）+ N（被调度的时空模型）| Spatio-Temporal Analysis / Model Matching / Results Generation | 三阶段流水线 |
| TravelAgent | 4 模块（非 agent 自治）| Tool-usage / Recommendation / Planning / Memory                       | 主从（Planning 主）|
| Vaiage      | ≥3（论文披露）       | Strategy Agent / Information Agent /（推断）Itinerary Agent           | 图状协作    |
| TriFlow     | 多个（按阶段挂多 LLM）| Retrieval Agent / Planning Agent / Governance Agent + 多个 validator    | 三阶段递进 + 反馈 |
| Aimpoint    | 1 orchestrator + 3 RAG retriever | Places / Restaurants / Events                              | 并行检索后汇聚 |
| DocentPro   | 4（按域）            | Attractions / Restaurants / Hotels / Activities                       | 模块化 + 跨工作流复用 |
```

**典型规模**：3-5 个 agent，少有论文上 10+。**多 agent 不等于堆角色**——TriFlow 把多个 LLM 串到三阶段里就拿到 91.1% Final Pass Rate（TravelPlanner benchmark）；Vaiage 也只显式画了 Strategy / Information 两个 agent。

### 1.2 协作模式

落到具体拓扑，论文界主要有四种模式：

```text
| 模式       | 代表工作    | 信息流                                       | 适合场景             |
|-----------|-------------|---------------------------------------------|---------------------|
| 流水线     | UrbanLLM    | analysis → match → generation                | 任务分解明确         |
| 主从       | TravelAgent | Planning 主 + Tool/Recommendation/Memory 辅  | 行程为中心的规划     |
| 平级 + validator | TriFlow     | agent ↔ validator 反馈环（多次迭代）          | 强约束 / 多目标优化  |
| 并行 + 汇聚 | Aimpoint    | 多 RAG 同时拉 → 顺序合成 itinerary           | 检索 IO 占大头       |
```

**关键发现**：TriFlow 的 governance stage 用 "agent–validator loop" 做 bounded iterative refinement（论文原文 "bounded iterative refinement"，最多 8 次迭代）——这是 LLM-Modulo 思想在多 agent 里的体现，但 TriFlow 强调 "monotonic feasibility"：一旦约束被满足，后续阶段不能再违反它（[TriFlow 2.1](https://arxiv.org/html/2512.11271v1)）。

### 1.3 Agent 间通信协议

```text
| 工作        | 通信形态                                          | 优劣                          |
|-------------|--------------------------------------------------|------------------------------|
| UrbanLLM    | JSON `{task, id, dep:[...], args:{...}}`         | 结构化、可调度，但 schema 死  |
| TravelAgent | 模块函数调用 + Memory Module 读写                | 程序化，便于 unit test        |
| Vaiage      | 自然语言 + structured tool use（论文原话）        | 灵活，但需 prompt 反复对齐    |
| TriFlow     | structured intermediates + LLM refinement        | 折中：结构化骨架 + LLM 填细节 |
| Aimpoint    | 向量查询参数 + structured response                | 工业级，可监控                |
| DocentPro   | LangGraph node + LangSmith trace                 | 全链路可观测                  |
```

晌午局当前用的是 LangGraph + Pydantic AI 的混合方案：节点之间通过 `AgentState` 共享 dict 状态（结构化），同一个节点内部走 ReAct（LLM 自主决策）。这在论文谱系里属于 **TriFlow 与 Vaiage 之间**的折中型。

### 1.4 ⚠ Multi-agent vs Single-agent 增益证据

```text
| 工作      | 对照实验数字                                                      | 出处                |
|-----------|------------------------------------------------------------------|---------------------|
| UrbanLLM  | 68.30% vs GPT-4o 49.99%（spatio-temporal task analysis 准确率）  | Table 1, 2406.12360 |
| TravelAgent | Rationality 9.56 vs GPT-4+ agent 8.16；Personalization 8.44 vs 4.31（人类评分 1-10）| Table 1, 2409.08069 |
| Vaiage    | 8.5 vs no-strategy 7.2 vs no-external-API 6.8（GPT-4 rubric, 1-10）| abstract, 2505.10922 |
| TriFlow   | 91.1% FPR vs FormalVerify 93.3% vs TravelPlanner baseline 4.4%（TravelPlanner benchmark）| Table 1, 2512.11271 |
```

**注意**：TriFlow 在 TravelPlanner 上 FPR（91.1%）略低于 FormalVerify（93.3%），但 runtime 22.6s vs 245.7s，10× 加速。换句话说，多 agent 的核心增益是 **可解释 + 模块化 + 速度**，不是单纯的精度提升。这与晌午局 hackathon 评分倾向（Demo 闭环 + 可见性 + 异常韧性）高度吻合。

---

## 2 · 维度 2：检索增强（RAG）的粒度与策略

### 2.1 检索对象与粒度

各家选择不同粒度：

```text
| 工作        | 检索对象                                          | 粒度          |
|-------------|--------------------------------------------------|--------------|
| TP-RAG      | 18,784 条历史轨迹 + 85,575 个细标 POI            | 轨迹级 + POI 级 |
| TravelAgent | 实时 API（Google Maps / SerpAPI）+ Memory 历史    | API 级 + 用户级 |
| Aimpoint    | 3 个独立 vector index：places / restaurants / events | 类型分库 POI 级 |
| DocentPro   | 4 个域 agent，每个域内自己的 RAG                  | 域级 POI 级    |
| TriFlow     | "factual resources"（航班 / 酒店 / POI / 距离）   | 全局子集裁剪   |
| UrbanLLM    | spatio-temporal model zoo（>50 个模型）           | 模型级（不是数据级） |
```

晌午局现状：`mock_data/{pois,restaurants,routes,user_profile}.json` 是**结构化字段过滤** + ID 索引，不是 vector RAG。准确说法：**结构化 KG 检索（schema-constrained lookup）**。

### 2.2 检索时机

```text
| 时机           | 工作示例     | 适用场景                    |
|---------------|-------------|----------------------------|
| 上下文初始化阶段（一次） | TravelAgent Memory 初始化 | 用户偏好稳定           |
| 每个 Tool call 前    | 晌午局 ReAct 路径（react_agent.py） | LLM 驱动的动态决策   |
| 每轮规划前（批量）   | TriFlow Retrieval Stage | 强约束、需提前裁剪空间    |
| validator 触发后    | TriFlow Governance Stage | 修复违规时定向补检索      |
```

Aimpoint Digital 的工业经验值得特别注意：他们做了 A/B 比较 **fixed pipeline vs LLM-driven tool calling**，结论是 fixed pipeline **更稳定**（"itineraries generated using tool calling were less consistent, and the orchestrating LLM occasionally made errors in tool selection"，[Aimpoint case study](https://www.zenml.io/llmops-database/ai-agent-system-for-automated-travel-itinerary-generation)）。这条经验直接打脸"agent 必须能动态选工具"的迷思——生产环境下，**确定性的 fixed pipeline 比 LLM 自主调度更可靠**。这与晌午局的双轨设计（LangGraph 主路径 fixed pipeline + ReAct fallback）思路一致。

### 2.3 检索 vs LLM 生成的取舍点

两个常见坑（论文反复提到）：

- **坑 1：检索结果污染 LLM 决策**（retrieval poisoning）。TP-RAG 论文原文 "challenges persist in universality and robustness due to conflicting references and noisy data"——检索回来 18,784 条轨迹中相互冲突的部分会让 LLM 输出更差。
- **坑 2：LLM 忽略检索结果**（retrieval ignored）。这是 RAG 老问题，TravelAgent 的应对是**结构化注入**：把 hard constraints 抽到 tool-call 参数层，不依赖 LLM 主动用 retrieval（[TravelAgent §3.2](https://arxiv.org/html/2409.08069)）。

TP-RAG 提出的 EvoRAG 框架显示："integrating reference trajectories significantly improves spatial efficiency and POI rationality"，但它需要 evolutionary 多轨迹融合算法，复杂度远超 hackathon 范畴。晌午局**不要**走这条路。

### 2.4 Embedding 模型与精度

学术工作多数用 `text-embedding-ada-002` 或自训练的 dense retriever；Aimpoint Digital 用 Databricks 自家的 Mosaic AI Vector Search（具体 embedding 模型未披露）。**精度数字** TP-RAG 给的是 `route efficiency / POI appeal` 等多维度评估指标，没有给 single retrieval recall 数字。

### 2.5 ⚠ 改造 mock_data 的 ROI 估算（晌午局视角）

```text
| 改造目标          | 现状                  | RAG 替代方案     | ROI 评估                   |
|------------------|---------------------|----------------|--------------------------|
| 42 POI 的检索    | 字段过滤 + 距离排序    | vector top-k    | ❌ 过度工程（数据规模太小）  |
| 餐厅 capacity 查询| 字段 lookup           | vector 不适用    | ❌ 不需要 RAG                |
| 用户历史召回      | user_profile.json 单条记录 | 暂无历史，无法召回 | ⚠ 等积累足够数据再说       |
| 评论/标签富化    | tags 字段直接挂在 POI 上 | 评论文本 RAG     | ⚠ 中等：能丰富 narration 但不影响硬约束 |
```

**结论**：mock_data 规模下 vector RAG **完全过度工程**。结构化 KG 检索（当前方案）才是合适粒度。如果未来要接美团 / 大众点评的真实数据（万级 POI），那时再上 vector RAG 才有 ROI。

---

## 3 · 维度 3：持久化用户记忆 schema

### 3.1 三层约束 schema 是论文标配

TravelAgent 论文（[Fudan, 2409.08069 §3.1](https://arxiv.org/html/2409.08069)）首次明确提出「三层约束建模」：

```text
| 层级               | 例子                            | 来源                | 强制性 |
|-------------------|--------------------------------|--------------------|-------|
| Hard Constraints   | 出发/返程日期、人数、儿童年龄    | 用户当次输入         | 必满足 |
| Soft Constraints   | "用户偏好低预算 / 喜欢家庭活动" | Memory Module 跨场景累积 | 影响排序 |
| Commonsense Constraints | "低预算+亲子→排除高价 POI" | Memory Module + LLM 通用知识 | 影响合理性 |
```

晌午局 `mock_data/user_profile.json` 当前 schema：

```text
| 字段                     | 类型     | 对应 TravelAgent 层级 |
|-------------------------|---------|--------------------|
| user_id                 | string  | -                  |
| home_location           | object  | Hard（位置基准）    |
| default_budget          | float   | Hard（预算上限）    |
| transport_preference    | string  | Soft（出行习惯）    |
```

**严重不足**：缺 Soft 层（喜欢的菜系 / 调性 / 历史偏好）+ 缺 Commonsense 层（"用户家有 5 岁娃 → 推 POI 必须亲子友好"）。

### 3.2 长短期记忆分离

```text
| 工作        | 长期 Memory schema                              | 短期 Memory schema             |
|-------------|------------------------------------------------|------------------------------|
| TravelAgent | user_spending_level / user_attraction_preference / user_restaurant_preference + commonsense rules | 当次 hard constraints + 当次工具结果 |
| Vaiage      | （abstract 未细披露，暗示有 map-based feedback loop）| 对话窗口 |
| TriFlow     | 没有显式 long-term memory（聚焦单次 itinerary） | retrieval 阶段输出的 factual subset |
| DocentPro   | LangGraph checkpointer（thread_id 持久化）     | message_history             |
```

TravelAgent 给了一个具体的 long-term insight 例子（[Appendix B](https://arxiv.org/html/2409.08069)），核心字段：

- `user_id`
- `user_spending_level`（low / medium / high）
- `user_attraction_preference`（自然语言描述：例如"用户偏好免费或低成本、适合儿童与家长共同体验的景点……"）
- `user_restaurant_preference`（如 "Chinese"）
- 一条与场景配对的 commonsense rule（"if low spending level and family tour: ..."）

这里有个关键设计选择：**偏好用自然语言而不是 enum**。理由：LLM 直接消费自然语言比消费 enum 更灵活，且能携带上下文（"喜欢免费 + 适合儿童 + 短驻留"三个维度耦合）。

### 3.3 Memory 更新机制

```text
| 机制              | 工作            | 触发时机                   |
|------------------|----------------|--------------------------|
| 用户主动反馈触发    | TravelAgent (E)→(G) like/pass | 用户点 like 后 LLM 提炼新 insight |
| Agent 总结（自动） | TravelAgent G  | 每次对话后 LLM 写一段总结到 long-term |
| 时间衰减          | 论文均未实现     | -                        |
| SQL trigger       | 工业方案（如 Aimpoint Delta CDC）| 数据变更自动同步       |
```

**晌午局现状**：`user_profile.json` 没接入主流程，没有 update 路径。这是 problem 7（C 角色）落地的明显缺口。

### 3.4 隐私 / 一致性约束

中文场景下两个隐私敏感字段需要特别注意：

- **儿童年龄**：含未成年人数据，落盘要遵守「最小必要」原则。建议存"年龄段"而非具体年龄（5-10 / 11-17）
- **家庭住址**：home_location 已经是 lat/lng，比"地址文字串"已经脱敏一层。但仍需确保不暴露给非用户本人

论文层面，Personal Travel Agent 系列**没**显式覆盖中文隐私法规（GDPR / 网络安全法 / 个人信息保护法）。这是工程落地时晌午局自己要补的层级。

---

## 4 · 维度 4：失败处理与 graceful degradation

### 4.1 单 Agent 失败的论文应对

```text
| 工作        | 失败模式                       | 应对策略                                  |
|-------------|-------------------------------|------------------------------------------|
| TriFlow     | Constraint violation in planning| Governance stage iterate ≤8 次再不行就 give up |
| Vaiage      | Tool 调用失败                   | Strategy Agent 重新规划 + 用户参与回环      |
| TravelAgent | API 拉取失败                    | （论文未详述）暗示退化到 LLM 通用知识        |
| Aimpoint    | LLM tool selection error       | **直接放弃 tool calling，转 fixed pipeline**  |
| DocentPro   | LLM 输出"幻觉地点"              | Filter step 显式过滤 hallucinated/closed places |
```

### 4.2 多 Agent 决策冲突的仲裁

```text
| 仲裁机制              | 工作              | 实现细节                            |
|---------------------|------------------|-----------------------------------|
| Validator 优先（一票否决） | TriFlow Governance | validator 抛错则 governance agent 必修复 |
| 主 Agent 决策（hierarchy） | DocentPro / AWS Bedrock supervisor | supervisor 接受/拒绝 worker 输出 |
| User-in-the-loop    | Vaiage            | map-based feedback 让用户拍板         |
| 多数表决            | （论文中未见标准 trip planning 工作用此） | -                          |
```

晌午局现状：`graph/critic_node` 是 validator（critics_v2.validate_itinerary），`replan_router` 决策 backprompt / ils / give_up。这套设计对应**TriFlow Validator 优先**。冲突仲裁机制简单但合用。

### 4.3 失败率与失败模式

公开数字：

```text
| 工作       | 失败率 / 异常占比                                  | 出处                |
|-----------|--------------------------------------------------|---------------------|
| TriFlow   | TravelPlanner Hard 难度 FPR 80.0%（即 20% 失败）   | Table 1, 2512.11271 |
| TravelPlanner baseline | TravelPlanner Easy 难度 FPR 1.1%      | Table 3, 2512.11271 |
| 通用 LLM agent | UrbanLLM 之外 GPT-4o spatio-temporal task accuracy 50% | Table 1, 2406.12360 |
| Aimpoint  | "fixed pipeline 比 tool calling 更 consistent"    | case study 原文     |
```

注意 TravelPlanner 用 **Final Pass Rate (FPR)** 衡量"全部约束通过"——这是非常严苛的，1% 通过率不代表 99% 输出垃圾，而是 99% 输出至少违反 1 条硬约束。多数情况下软退化（部分约束满足）就够 demo 用。

### 4.4 三种失败处理哲学的对比（与 LLM-Modulo / OR solver）

```text
| 哲学            | 代表          | 失败处理                      | 何时适合           |
|----------------|--------------|------------------------------|------------------|
| GTC backprompt | LLM-Modulo   | critic 抛违规 → LLM 重做      | 约束少 / 检查器轻  |
| Unsatisfiable core | OR-Tools / Z3 | 求解器返回不可满足的 minimal set | 数学可形式化的约束 |
| Bounded iteration + give up | TriFlow / Vaiage / 晌午局 | 迭代 N 次还不行就退化 | 实际工程，强 SLA 要求 |
```

晌午局当前 graph 用第三种（`replan_router → ils_fallback → narrate`）。这与 TriFlow 的 governance stage 8 次迭代上限完全合拍。

---

## 5 · 陷阱清单 5 题

### Q1：晌午局现有 graph 是不是「多 agent 协作」架构？

**结论**：**部分是，部分不是**。

读 `backend/agent/graph/build.py`（行 67-145）后，现有 11 个节点：

- `router / chitchat / intent / refiner` 4 个偏意图层
- `search_pois_worker / search_restaurants_worker / get_user_profile_worker / execute_collect` 4 个执行层（前 3 个并行）
- `planner / assemble / critic / replan_router / ils_replan` 5 个规划层
- `narrate / execute_finalize` 2 个出口层

按论文界对 agent 的严格定义（"具有自主 LLM 决策能力的子系统"），晌午局的节点中只有 **intent / refiner / planner / critic / narrate** 真正算 agent；其余都是确定性 ETL（worker / collect / assemble / replan_router / ils_replan）。所以实际 agent 数 5 个，已经达到 TravelAgent / Vaiage 的规模。

差异点：晌午局的 agent 之间通过 `AgentState`（structured dict）传值，**不**走 Vaiage 的"natural language interaction"——这条偏向工业风格、可调试，但牺牲了一些 agent 间协商的灵活性。这是 hackathon 场景下正确的取舍。

### Q2：mock_data + 7 Tool 是「结构化 KG 检索」还是「裸字段 lookup」？要不要升级到 vector RAG？

**结论**：**结构化 KG 检索（中等水平）**，**不要**升级到 vector RAG。

按 RAG 的广义定义（"通过外部知识源增强 LLM 输出"），mock_data 满足核心要求；但比 vector RAG 多一层 schema 约束。具体形态：

- POI / 餐厅按字段过滤（distance / dietary / capacity / age_range）—— 类似 SQL WHERE
- 距离排序 + 标签匹配——类似简单的相关性打分
- routes.json 做点对点查表——类似 KV store

**42 POI 用 vector embedding 是浪费**。Aimpoint 的 Paris 500 餐厅级别（10× 规模）才刚开始有 vector RAG 优势；晌午局当前 mock 数据规模下，结构化检索的 precision 是 100%（字段精确匹配），vector RAG 反而会引入近似检索带来的 false positive。

ROI 估算（总投入 vs 收益）：

```text
| 改造项                       | 工作量      | 收益                   | 推荐 |
|----------------------------|------------|------------------------|------|
| 上 vector RAG 替换 mock_data lookup | 3-5 人日 | precision 反而下降      | ❌    |
| 给 POI 加 review 文本字段 + LLM 总结 | 1 人日   | narration 更生动         | ✓    |
| 给 user_profile 加自然语言 preference 字段 | 0.5 人日 | personalization 提升  | ✓✓   |
```

### Q3：user_profile.json 已存在但没接入主流程，原因？

**结论**：**设计未完成 + ROI 待验证**双重原因。

读现有 `user_profile.json`（4 个字段）+ `react_agent.py`（行 870 附近的 `get_user_profile` tool）+ `graph/nodes/execute.py`（含 `get_user_profile_worker`），可以看到：

- ReAct 路径：tool 已挂载，LLM 看到 8 工具中包含 `get_user_profile`，可以**自主选择**调用
- LangGraph 主路径：`get_user_profile_worker` 在并行 worker 阶段**强制**调用（与 search_pois 同级并行）

所以**已经接入了**，问题不在"没接入"。真正的缺口是：

1. **schema 太单薄**：4 字段中只有 `home_location` 真有用，`default_budget=300` 几乎所有 demo 用例都没差异化效果
2. **没有 update 机制**：用户多次对话不会改 profile（论文 TravelAgent 在交互后写回 long-term insight，晌午局没做）
3. **没有 cross-session 持久化**：profile 是 mock 文件读，session 间不学习

**基于本调研的建议**：
- 立即（≤0.5 人日）：扩 schema 到 TravelAgent 三层模型——加 `dietary_preference`（自然语言段落）+ `social_context_history`（去过哪些场景）+ `kid_age_range`（敏感字段，存"段"不存"岁"）
- 第二步（≤1 人日）：在 `narrate_node` 后加一个轻量 `memory_writer` 节点，把当次 itinerary 摘要写回 profile（论文 TravelAgent G 步骤的 hackathon 简化版）
- 第三步（demo 后）：考虑接入向量 / 模糊检索做"上次类似行程"召回——这是 Q5 主战场

### Q4：5 岁娃案例怎么处理？

**结论**：论文界主流是 **prompt 注入 + 工具参数化**两层结合，不是 RAG 历史数据。

具体看：

- **TravelAgent**（Fudan, 2409.08069）：从 user input form 拿 `children_num=1, children_ages=3`，注入到 hard constraints；Recommendation 模块用这俩字段过滤 attractions
- **Vaiage**：abstract 提到 "group size" 是 contextual constraints 之一，没明示 5 岁娃专门处理
- **TriFlow**：通过 query decomposition 把 "亲子" 提到 retrieval stage 作过滤参数

**晌午局 react_agent.py 的做法**（行 ~870）：
- `search_pois(age_in_party=[5])` 把年龄列表作为 tool 参数传入
- prompt 里 S1 few-shot 显式给了 `physical=["亲子友好","适合 5-10 岁"]` 的范例

这套是论文 Mainstream，没问题。

**但有一个论文没覆盖、晌午局没解决的问题**：5 岁娃的"年龄敏感约束"是**多级**的——

```text
| 约束级别       | 例子                                            | 论文是否覆盖 |
|---------------|------------------------------------------------|------------|
| 物理（硬）    | 不能去高山、需要无台阶                            | TravelAgent 显式 |
| 心理（软）    | 注意力短，单 POI 停留时长 ≤90min                   | 论文均未明确 |
| 时间（软）    | 中午有午睡需求，13:00-15:00 不安排剧烈活动          | 论文均未明确 |
| 饮食（硬）    | 需要儿童餐 / 软烂                                 | TravelAgent 显式 |
```

晌午局通过 `_age_aware_duration_critic`（spec planning-quality-deep-review R4）已经覆盖了"心理（软）—注意力短"维度，这是本项目相对论文的**一处加分项**。

### Q5：用户说"带 5 岁娃下午出去"，该不该主动召回历史？最小代价路径？

**结论**：**应该召回，但用最简单的"上次同社交场景"启发式即可，不要做 vector 检索**。

论文界做法对比：

```text
| 工作         | 主动召回的实现                                   | 复杂度  |
|-------------|------------------------------------------------|--------|
| TravelAgent | History Insights Retrieval：用 user_id 拉 long-term memory | 低（KV）   |
| Vaiage      | （abstract 未细述，暗示有 map-based feedback）  | 中     |
| Aimpoint    | 不做 cross-session memory                       | 0      |
| DocentPro   | LangSmith trace 可看历史，但不自动召回          | 0      |
```

**晌午局最小代价路径**（按工作量从小到大）：

1. **0.3 人日**：在 user_profile.json 加 `recent_trips: List[{social_context, success, timestamp, summary}]`（最多保留 5 条）。`narrate_node` 后加 hook 写回。
2. **0.2 人日**：在 `intent_node` 抽 `social_context` 时，把 profile 中匹配的 `recent_trips` 摘要塞进 LLM prompt（一段自然语言："你上次和家人去过 P004 反馈不错，这次可以…"）
3. **0**（不做）：**不要**做向量化、不要做相似度召回——5 条历史用 string match 就够了，hackathon demo 的"看见 agent 记得我上次的偏好"是评分点。

工程落地的关键 checklist：

```text
| 检查项                                                 | 必做 |
|------------------------------------------------------|------|
| recent_trips 落盘前要 LLM 总结（不存原始 itinerary，太占空间）| ✓   |
| 召回时要带"成功/失败"标签（被用户 cancel 掉的不要召回） | ✓   |
| 隐私敏感字段（孩子具体年龄）不要写进 trip_summary       | ✓   |
| demo 之前手动塞 1-2 条假历史，让"召回"在第 1 次对话就有效 | ✓   |
```

---

## 6 · 中文场景特殊约束的推断

⚠ 以下属于推断，无一手中文场景论文支持：

```text
| 约束维度        | 中文场景的特殊性                            | 落地建议                  |
|----------------|--------------------------------------------|--------------------------|
| POI 名实体识别 | 中文长名（"杭州西湖音乐喷泉广场"）含语义冗余 | 用 jieba 分词后做关键词过滤，不要 char-level embedding |
| 中式饮食偏好    | "粤菜/川菜/淮扬菜"等地域分类，比英文 cuisine 颗粒度细  | 需要 9-12 类的 dietary 字典 |
| 节假日 / 时辰   | "下午局"对应中式时间观，不是 PM 12-18 简单映射 | LLM prompt 显式约束"下午"语义 |
| 含义偏好（隐性）| "亲密"对中文情侣 vs 英文 intimate 含义差异   | 词典明确语义边界，避免 LLM 猜 |
```

晌午局的 9-context 词典 + 中文 tag 词典已经覆盖了大部分。在第 2 节"检索粒度"上，本项目对中文 POI 的**字段级过滤**比 vector RAG 更稳——因为中文 BPE 分词和 embedding 匹配在 POI 名场景下经常出现"语义飘移"（"西溪诚园"和"西溪湿地"在 char 级别相似但语义完全不同）。

---

## 7 · 关键洞察 / 复用评分 / 建议 / 衔接 / 阅读笔记

### 7.1 关键洞察 5 条

1. **多 agent ≠ 堆角色**。论文 SOTA（TriFlow）只有 retrieval / planning / governance 三阶段共 ≤5 agent；晌午局现有 5 个真 agent 已经达到这个量级，**不需要再加**。
2. **结构化优先于向量**。Aimpoint Digital 与 DocentPro 都把"deterministic logic + LLM"组合作为生产稳定性的核心；晌午局的 mock_data 走结构化路线是对的，不要被"必须 vector RAG"的迷思带偏。
3. **三层约束是论文标配**（hard / soft / commonsense），晌午局 user_profile.json **只覆盖了 hard 层**，建议立刻补 soft + commonsense 两层（不需要复杂 schema，自然语言段落即可）。
4. **失败处理的"bounded iteration + give up"是正解**。晌午局 `replan_router` 的设计与 TriFlow Governance 完全合拍，不要追求"全约束满足"——demo 场景下 80% pass + 兜底文案才是最佳工程方案。
5. **主动历史召回是 demo 加分项**，但用最简单的 string match 就够。**不要**为了"看起来更高级"而上 vector embedding——5 岁娃案例的说服力来自"agent 记得上次"，不来自"召回算法多牛"。

### 7.2 复用评分（0-10）

```text
| 子维度                 | 评分 | 理由                                                        |
|----------------------|------|-----------------------------------------------------------|
| 整体复用（端到端架构） | 7    | 多 agent + 三层 memory + bounded iteration 全套思路与晌午局现状高度对齐 |
| 仅多 agent 拓扑       | 6    | 晌午局已经有 5 agent，论文里再多角色对 hackathon 价值不大        |
| 仅 RAG               | 3    | mock_data 规模下 vector RAG 过度工程；结构化检索已够用            |
| 仅记忆机制            | 8.5  | TravelAgent 三层 schema + 长短期分离是**最高 ROI 改造**，立即可落地   |
```

### 7.3 给晌午局的建议（≤200 字）

立即做（≤2 人日）：扩展 `user_profile.json` 至 TravelAgent 三层 schema（hard / soft / commonsense），重点加自然语言 `dietary_preference` 与 `recent_trips: List[{social_context, summary, success}]`（最多 5 条）；在 `narrate_node` 后挂 `memory_writer` 把摘要写回。`intent_node` 抽 social_context 时把匹配的 recent_trips 注入 LLM prompt——这是 demo "agent 记得我上次"加分项的最小代价路径。**不要做** vector RAG 替代 mock_data；**不要**新增 agent 角色（5 个已够）；**不要**接入复杂 evolutionary RAG 框架（hackathon 时间盒不允许）。

### 7.4 与现有 graph + mock_data 的衔接细节

```text
| 改造点                    | 文件路径                              | 改动量   |
|--------------------------|-------------------------------------|--------|
| user_profile.json 扩字段  | mock_data/user_profile.json         | +20 行 |
| memory_writer 节点        | agent/graph/nodes/memory_writer.py（新） | ~80 行 |
| build.py 加 narrate→memory_writer 边 | agent/graph/build.py                 | +3 行  |
| intent prompt 注入 recent_trips | agent/intent/prompts/intent_parser_prompt.py | +1 段 |
| AgentState 加 recent_trips 字段 | agent/graph/state.py                  | +1 字段 |
```

**风险点 / 注意事项**：

- `memory_writer` 节点必须 idempotent（demo 反复跑同一句不要污染历史）
- 写回 user_profile.json 要做 **file lock**（多 session 并发时）
- recent_trips 召回要遵守"成功/失败"标签——cancel 的方案不要召回
- ⚠ 隐私：孩子年龄落盘前必须脱敏到"段"

### 7.5 阅读笔记

```text
| 论文 / 案例                  | 核心贡献                                | 复用价值（对晌午局）        |
|-----------------------------|---------------------------------------|---------------------------|
| UrbanLLM (2406.12360)       | 时空任务分解 + 微调 LLM 调度模型 zoo     | 思路启发，不直接复用（微调不在 hackathon 范围）|
| TravelAgent (2409.08069)    | 4 模块 + 3 层约束 + 长短期 Memory         | **最高 ROI 复用源**——schema 直接借鉴 |
| TP-RAG (2504.08694)         | 18,784 轨迹 RAG benchmark + EvoRAG 框架  | 不直接复用（轨迹规模不匹配）|
| Vaiage (2505.10922)         | 图状多 agent + 地图反馈回环               | 部分复用——map-based feedback 思路可借鉴 |
| TriFlow (2512.11271)        | retrieval / planning / governance 三段   | **架构对齐参考**——validate 已对齐 |
| Aimpoint (Databricks)       | 多 RAG 并行 + fixed pipeline > tool calling | 工程经验直接借鉴            |
| DocentPro (LangChain)       | 4 域 agent 模块化 + LangSmith 可观测      | LangGraph 实战参照          |
```

---

## 附录：调研参考链接

- UrbanLLM：[arXiv 2406.12360](https://arxiv.org/abs/2406.12360) ｜ [HTML 全文](https://arxiv.org/html/2406.12360v1)
- TravelAgent (Fudan)：[arXiv 2409.08069](https://arxiv.org/abs/2409.08069) ｜ [HTML 全文](https://arxiv.org/html/2409.08069)
- TP-RAG：[arXiv 2504.08694](https://arxiv.org/abs/2504.08694)（EMNLP 2025 main.626）
- Vaiage：[arXiv 2505.10922](https://arxiv.org/abs/2505.10922)
- TriFlow：[arXiv 2512.11271](https://arxiv.org/abs/2512.11271) ｜ [HTML 全文](https://arxiv.org/html/2512.11271v1)
- Aimpoint Digital case study：[ZenML LLMOps Database](https://www.zenml.io/llmops-database/ai-agent-system-for-automated-travel-itinerary-generation)（原文 [Databricks Blog](https://www.databricks.com/blog/aimpoint-digital-ai-agent-systems)）
- DocentPro case study：[LangChain Blog 2025-04-29](https://www.langchain.com/blog/customers-docentpro)
- TravelPlanner benchmark：[OSU NLP Group](https://osu-nlp-group.github.io/TravelPlanner/) ｜ [arXiv 2402.01622](https://arxiv.org/abs/2402.01622)

> 内容合规声明：本报告中所有引用论文 / 案例的描述，已重写为中文表述，未直接逐字摘抄超过 30 词的英文原文段落。所有数字（FPR、accuracy、score）均来自论文表格或 abstract，已在表格中标注出处。
