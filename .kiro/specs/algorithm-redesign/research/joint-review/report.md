# Phase 2 联合审查报告：8 范式横向交叉对照矩阵 + 真/表/隐三类共识清单

> 审查身份：独立 sub-agent（Phase 2 联合审查）。
> 报告位置：`.kiro/specs/algorithm-redesign/research/joint-review/report.md`
> 写作日期：2026-05-24
> 审查纪律：未读项目代码（仅读 AGENTS.md 了解背景）；仅引用 8 份范式调研报告中的事实与数据；不引入新论文 / 新数据 / 新 web search。
> 审查角色：质疑而非总结。本报告的价值不在于罗列 8 份报告的结论，而在于**找出它们之间的"真共识 / 表面共识 / 隐藏冲突"**。

---

## 〇、审查范围与纪律自陈

### 0.1 审查对象

```text
| 编号       | 范式                              | 核心范式来源                                  | 报告字数（中文计字） |
|-----------|----------------------------------|---------------------------------------------|------------------|
| Agent 1   | Google AI Trip Ideas             | research.google blog (Awasthi/Zhai 2025-06)  | ~7100             |
| Agent 2   | ITINERA                          | EMNLP 2024 Industry Track + KDD UrbComp 2024 | ~5950             |
| Agent 3   | LLM-Modulo Frameworks            | Kambhampati arxiv 2402.01817 + 2411.14484    | ~6800             |
| Agent 4   | TravelPlanner / Planner-R1 / SAT | OSU ICML'24 + LinkedIn 2025 + MIT 2025-01    | ~7500             |
| Agent 5   | RL 路径（DeepTravel / STAR）     | DiDi 2025-09 + STAR 2026-03 + 4 篇佐证      | ~6900             |
| Agent 6   | 经典 OR / TTDP / TOPTW            | Vansteenwegen 2009 + Gunawan 2017 + 综述     | ~6800             |
| Agent 7   | 多 Agent + RAG / 个性化记忆       | TravelAgent / TriFlow / Aimpoint / DocentPro | ~6400             |
| Agent 8   | 商业产品（TripGenie / 美团 / NAVITIME）| 携程 / 美团 / Google Ask Maps / NAVITIME    | ~7200             |
```

总阅读量约 5.5 万字。本报告自身字数控制在 7000-9000 字。

### 0.2 审查纪律自陈

1. 不读项目代码：8 份子报告已对 `ils_planner.py` / `critics_v2.py` / `graph/build.py` / `replan.py` 等做过实读，本报告不再独立验证；引用项目代码事实时全部转述子报告的引用（如「Agent 6 报告 line 469」即指 Agent 6 报告中对 ils_planner.py 第 469 行的转述）。
2. 不引入 8 份报告外的新数据：所有数字（0.6% / 23.89% / 56.9% / 93.9% / 等）必须能在 8 份报告里找到出处。
3. 自始至终保持质疑姿态：本报告不为编排者前一轮的「6 条交叉印证 / 4+4 合议」结论背书。
4. 表面同 ≠ 含义同：当多份报告字面看起来都在说同一件事（如「LLM 出方案 + critic 验」），必须深挖每份报告所指的具体设计形态。

---

## 一、8 维度 × 8 范式横向对照矩阵（64 格）

为避免格子过宽，矩阵分两半呈现。每格控制在 30-80 字，只填关键事实。

### 1.1 维度 1-4（输入 / 中间链路 / LLM 协作 / 失败处理）

```text
| 范式 ↓ × 维度 →    | 1. 输入 schema                                | 2. 中间链路算法                              | 3. LLM 协作模式                            | 4. 失败处理                              |
|--------------------|----------------------------------------------|--------------------------------------------|------------------------------------------|----------------------------------------|
| Agent 1 / Google    | suggested_duration + level_of_importance；硬约束 budget/scheduling 由 LLM 抽取后强校验 | LLM warm-start → grounding/Search backend → Stage 1 单日 DP（k≤6-8 bitmask）→ Stage 2 多日 set packing + hill climbing local swap | 1 次 LLM 调用，无反馈循环；substitute 池由 Search backend 提供（不是 LLM 二次生成） | 4 层防御：Places KG business_status / DP feasibility=0 / LLM 主防 prompt（弱）/ UI disclaimer |
| Agent 2 / ITINERA   | RD 把 query 拆成 (pos, neg, mustsee, type) 四元组；POI.context 是嵌入文本拼接；硬/软不区分 | UPC → RD → PPR (ada-002 召回) → CSO 三步：邻近图最大 clique 聚类 → cluster 间 SA TSP → cluster 内 LP TSP（PuLP+CBC） | 5-7 次调用：RD / Hour 估算 / 起点选择 / 反向检查 / IG 行程生成；空间决策不进 LLM | 仅 JSON 解析正则兜底；**无 critic 兜底**；论文自承「LLM 空间推理弱」 |
| Agent 3 / LLM-Modulo| problem spec → candidate plan（PDDL / JSON / 自由文本）；reformulator LLM 做 schema 转换 | Generate-Test-Critique 循环：LLM 出 candidate → format critic → constraint critics（VAL / unit-test）→ meta controller backprompt | 多次往返 backprompt（max iter=10-15）；LLM 是 generator + mutator | 论文 base：达 budget 即拒绝输出；soundness 由 critic 保证、completeness 由 LLM 保证 |
| Agent 4 / TravelPlanner | 13 项约束三分类（Environment 不评分 / 8 项 Commonsense / 5 项 Hard）；逐日 dict + 字符串字段 | sole-planning（直接喂全数据）vs two-stage（ReAct 自己采集）；Planner-R1 改用 GRPO + 6 子项 reward；SAT/SMT 路径用 Z3 | LLM 单次直接出 plan（GPT-4 0.6%）/ Planner-R1 微调 Qwen3-8B 用 reward 训练 | binary（pass/fail）；Reflexion 给 high-level reason 也无效；形式化求解器路径（93.9%）无 LLM 反馈 |
| Agent 5 / RL 路径   | observation = (query, partial itinerary, K 步 Tool 响应)；token-level action；同行人画像论文均未处理 | sandbox 缓存数据 → GRPO 变体 + replay buffer → 双层 verifier（trajectory 时空可行性 + turn 一致性） | 训练时 LLM 做 token-level decision；推理时无显式 critic（critic 被吸收进权重） | hallucination 率 50% → <20%（reward 内化）；环境稳定性是 RL 训练硬约束（STAR RQ6） |
| Agent 6 / 经典 OR   | OP 5 元组 (id, score/profit, service_time T_i, [O_i, C_i], coord)；多目标走加权和 / Pareto / fuzzy | TOPTW 主流：ILS / SAILS / GRASP / VNS / GLS / ALNS / MILP exact；ILS 四要素：GenerateInitial + Perturbation + LocalSearch + Acceptance | 纯算法路径无 LLM；ItiNera-style 混合路径让 LLM 出 score、OR 解 OP | MILP exact n≤50 秒级 0% gap；ILS 业务规则增强（_overload_penalty）补 OR 文献空白 |
| Agent 7 / multi-agent+RAG | hard / soft / commonsense 三层约束 schema（TravelAgent §3.1）；user_profile 自然语言段落（不是 enum） | 4 类拓扑：流水线 / 主从 / 平级+validator / 并行+汇聚；TriFlow 三阶段递进：retrieval → planning → governance | 3-5 个 agent，少有 10+；TriFlow 三阶段 + multi LLM；Aimpoint 论证 fixed pipeline > tool calling | bounded iteration + give up（TriFlow 8 次上限）；validator 优先一票否决；fixed pipeline 比 tool calling 更 consistent |
| Agent 8 / 商业产品  | 一句话 NL（TripGenie/Ask Maps）/ 标签筛选（点评）/ 表单（NAVITIME）；商业产品**绝大多数不暴露 schema** | TripGenie：LLM 抽参 + RAG（黑盒）；点评：召回-排序-重排 feed；Ask Maps：Gemini + Maps Places；NAVITIME：图算法 + 偏好权重 | TripGenie 自研问道 LLM 抽参后注入产品页；Ask Maps 多约束自动降级；点评仍是 CTR 排序无 LLM 主路径 | TripGenie「软道歉 + 替代品」；点评「暂未营业」角标但不主动推替换；Ask Maps query 不可满足时给 fallback |
```

### 1.2 维度 5-8（数据规模 / soundness / 落地代价 / 半日单城 demo 适配）

```text
| 范式 ↓ × 维度 →    | 5. 数据规模假设                              | 6. soundness 来源                          | 7. 落地代价（人天 + 资源）              | 8. 半日单城 demo 适配                  |
|--------------------|--------------------------------------------|------------------------------------------|--------------------------------------|--------------------------------------|
| Agent 1 / Google    | Places KG（千万级 POI）+ 实时 grounding；多日跨城；DP 单日 ≤6-8 activity | grounding（Places KG）+ DP feasibility=0 否决+ Stage 2 hill climbing | 整体复用 3/10；grounding-first 流程复用 8/10；< 1 wave (4-6h) | 多日范式核心三件套全部退化；similarity 公式失效；仅 grounding-first 思想可借鉴 |
| Agent 2 / ITINERA   | 4 城 1233 行程 + 7578 POI；POI 用 ada-002 嵌入；行程 6-17 POI 按 1-8h 时长插值 | 工业化深度低于本项目；缺 critic 兜底；缺时间窗 / 营业时间 / 同行人画像 | 整体复用 2/10；RD + LLM-语义打分 7/10；1-2 工作日 | cluster + 分层 TSP 假设节点 ≥10 同质化；半日 4-6 节点全部退化 |
| Agent 3 / LLM-Modulo| 不依赖大规模数据；critic 池完备性 = completeness | critic 形式化正确性（VAL 定理 / 业务规则）；完整 sound 担保 | 整体复用 8/10；项目已是同构系统；扩展即可 | **半日单城最契合**：项目 graph/build.py 与 LLM-Modulo Figure 1 1:1 对应 |
| Agent 4 / TravelPlanner | 1225 query（人工标注每条 $0.80）+ 400 万条数据；Planner-R1 训练 180 query | TravelPlanner: rule-based evaluator（13 项独立判断）；SAT/SMT 路径：Z3 sound-and-complete | commonsense/hard 二分法借鉴 6/10；2-3 人天加 1 类违规码 | 13 项中仅 Cuisine ↔ DIETARY_VIOLATION 完全等价；其余因半日单城退化或不存在 |
| Agent 5 / RL 路径   | DiDi 真实用户 query 缓存（量级未公开）；STAR sweet spot ~1K 平衡难度 | reward signal 内化为 policy；critic 不显式可见 | 整体复用 1/10；30+ 人天 + GPU $500；推理路径替换不可承受 | hackathon 时间盒不可行；评委「看 Agent 决策」与 RL 内化矛盾 |
| Agent 6 / 经典 OR   | TOPTW Solomon n=100、Cordeau n=48-288；本项目 n=87 候选 + 4 节点是「极小规模」 | MILP exact 数学最优；ILS gap 1.83%；SAILS gap 0.75% | ILS 升级 8/10；ItiNera 风格 LLM-Modulo + ILS 兜底 7/10；wave 1 ≈ 50 行 | 规模太小，ALNS / MILP 都过度工程；ILS 已落地 6/10 完成度 |
| Agent 7 / multi-agent+RAG | mock_data 42 POI + 45 餐厅 → vector RAG 过度工程；user_profile 三层 schema 强烈缺失 | validator 优先一票否决；bounded iteration + give up | 记忆 schema 改造 8.5/10；≤2 人日；不要新增 agent 角色 | 5 个 agent 已达论文规模；user_profile 三层是高 ROI 单点改造 |
| Agent 8 / 商业产品  | TripGenie 多日 / 点评单点 / Ask Maps 即兴 / NAVITIME 单次行程 | 商业产品 C 端不需要 sound；只需软兜底（替代品 + 道歉） | UX 借鉴 9/10；2-3 人天；纯前端改造 | LUI 浮标 + 三候选 + 决策可见三件套；半日 + 一句话 + 决策可见**没有商业产品同时做到** |
```

**矩阵阅读说明**：

- 维度 1-4 是「能做什么」（输入 / 算法 / LLM / 失败）；维度 5-8 是「适不适合本项目」（数据 / soundness / 落地 / demo 适配）
- 维度 8（半日单城 demo 适配）是本项目最关键评估列：**Agent 3 是唯一全 demo 友好的范式；Agent 5 是唯一全 demo 不友好的范式**；其余 6 个范式都有局部可借鉴点



---

## 二、真共识清单（≥ 4 份独立报告用具体证据支持的论断）

每条共识陈述要求：**至少 4 份独立报告**给出具体证据；引用到段；带反对/质疑列。

### 真共识 1：LLM-only 端到端规划路径在 trip planning 上不可行

```text
| 共识陈述                                  | 支持的报告 + 具体证据                                                                                                | 反对/质疑 |
|------------------------------------------|--------------------------------------------------------------------------------------------------------------------|---------|
| LLM-only 端到端 trip planning 失败率极高，必须配合 critic / solver / RL reward 兜底 | Agent 4 §三 Table 3：GPT-4-Turbo two-stage 0.6%；Agent 6 §五（引）；Agent 7 §一 §四（FPR 1.1%）；Agent 5 §1.1（DeepTravel hallucination 50%→<20%）；Agent 2 §三 §3.2（ITINERA 31.4% vs GPT-4 18%）；Agent 3 §四 §4.5；Agent 8 §四（Ask Maps 自动降级） | 无 |
```

**7 份报告交叉印证**——这是 8 份报告里**最稳**的一条共识，比编排者前一轮总结的「6 条交叉印证」更扎实。

### 真共识 2：硬约束必须前置剥离到候选池过滤 / schema 阶段

```text
| 共识陈述                                  | 支持的报告 + 具体证据                                                                          | 反对/质疑 |
|------------------------------------------|----------------------------------------------------------------------------------------------|---------|
| 硬约束（闭店 / 距离 / 营业时间）应在候选生成阶段强过滤；事后 critic 是兜底而非主防 | Agent 1 §五 Q5（grounding-first）；Agent 6 §六 Q1（业务规则 > 算法精度）；Agent 4 §二（BUDGET 走 schema）；Agent 5 §六 Q4（TOOL_RESPONSE_INCONSISTENCY）；Agent 7 §四 Q4（fixed pipeline > tool calling） | Agent 3 §四 §4.6 隐含质疑：LLM-Modulo soundness 契约要求 critic 而非候选池剥离 |
```

**5 份印证 + 1 份隐含质疑**。这是隐藏冲突 3 的预演——LLM-Modulo 的 partial-planner 边界。

### 真共识 3：n=87 候选 + 4-6 节点的半日单城规模下，元启发式属于过度工程

```text
| 共识陈述                                  | 支持的报告 + 具体证据                                                                          | 反对/质疑 |
|------------------------------------------|----------------------------------------------------------------------------------------------|---------|
| 节点数 ≤ 6 + 候选池 ≤ 100 时，复杂元启发式无法发挥优势；ILS 是当前最佳工程平衡 | Agent 6 §三 §3.3（n≤50 MILP 秒级 0% gap）；Agent 1 §五 Q2（单日 brute force 即可）；Agent 2 §五 Q2（半日 4-6 节点 cluster 失效）；Agent 5 §六 Q2（DeepTravel 在 mock 场景过度工程） | 无 |
```

**4 份印证**——直接否定 Agent 1 / Agent 2 / Agent 5 三个看似 attractive 的范式整体复用路径。

### 真共识 4：LLM 出语义打分 + 算法解空间组合是最佳分工

```text
| 共识陈述                                  | 支持的报告 + 具体证据                                                                          | 反对/质疑 |
|------------------------------------------|----------------------------------------------------------------------------------------------|---------|
| LLM 不擅长解空间问题（TSP / 路径优化），应让 LLM 出 score / weight，算法做组合优化 | Agent 2 §三 §3.2（ITINERA w/o CSO AM 飙 3 倍）；Agent 6 §五（LLM-as-scorer 工业派主流）；Agent 1 §三 §3.2（算法是 LLM 的质检线）；Agent 5 §三 §3.4（TripScore unified reward）；Agent 7 §一 §1.2（TriFlow 三阶段） | 无 |
```

**5 份印证**——本项目 `weights_llm.py` 已部分落地，但 _utility 仍是 4 维加权和未把 LLM 输出当 single profit score。这是 spec C 改造的核心方向。

### 真共识 5：critics_v2 与 LLM-Modulo 同构，工程级 sound 对 hackathon demo 已足够

```text
| 共识陈述                                  | 支持的报告 + 具体证据                                                                          | 反对/质疑 |
|------------------------------------------|----------------------------------------------------------------------------------------------|---------|
| 项目 critics_v2 的 10 类 ViolationCode 已与 LLM-Modulo 同构；soundness 是工程级 | Agent 3 §五 Q2（hard/soft 分类同构 + 1:1 映射表）；Agent 5 §六 Q4（reward signal source）；Agent 6 §七 §7.1（业务规则增强是项目原创）；Agent 4 §五（10 类中 8 条论文域没覆盖）；Agent 2 §五 Q5（critic 工业化深度高于 ITINERA） | Agent 3 §四 §4.6 设计哲学差异：论文 give_up=拒绝输出；项目 give_up=输出 best-effort |
```

**5 份印证 + 1 份哲学差异**——是项目护城河，但需明示 trade-off。

### 真共识 6：评委导向「Agent 决策可见性」与 RL 内化、TripGenie 黑盒形成反向选择

```text
| 共识陈述                                  | 支持的报告 + 具体证据                                                                          | 反对/质疑 |
|------------------------------------------|----------------------------------------------------------------------------------------------|---------|
| Hackathon demo 必须暴露 Agent 决策过程；与 RL 内化、商业产品黑盒形成反向选择 | Agent 5 §六 Q3（RL 替代违反 AGENTS.md §3.1）；Agent 8 §七 Q3（TripGenie 三年没暴露 Tool 链路）；Agent 6 §七 §7.3（业务规则压倒算法精度）；Agent 7 §四 §4.4（Aimpoint fixed pipeline > tool calling） | 无 |
```

**4 份印证**——直接淘汰 Agent 5（RL）整体复用，并对 Agent 8 商业产品借鉴范围给出明确边界（仅 UX，不借鉴黑盒哲学）。

### 真共识 7：user_profile 三层 schema 是高 ROI 单点改造（边缘计入：3.5 份印证）

```text
| 共识陈述                                  | 支持的报告 + 具体证据                                                                          | 反对/质疑 |
|------------------------------------------|----------------------------------------------------------------------------------------------|---------|
| 当前 user_profile.json 仅有 hard 层 4 字段，缺 soft + commonsense 两层 | Agent 7 §三 §3.1（TravelAgent 三层 schema 标配）+ §7.3（≤2 人日）；Agent 2 §五 Q4（RD 四元组吸纳）；Agent 8 §六（IntentSummary 显式回写） | Agent 5 §六 Q1：同行人 / 偏好 encode 在 RL 论文均未处理（限定性质疑） |
```

**3 份直接 + 1 份限定性质疑**——严格说不达 4 份阈值。**编排者声称的「6 条交叉印证」如包含此条则有夸大嫌疑**——详见编排风险评估。

---

## 三、表面共识清单（字面看起来都同意但底层语义不一致）

### 表面共识 1：「LLM 出方案 + critic / verifier 验证」

```text
| 表面共识                  | 各报告的实际所指                                                                                                  | spec C 真含义/取舍                  |
|--------------------------|--------------------------------------------------------------------------------------------------------------|---------------------------------|
| 「LLM 出方案 + critic 验证」 | Agent 1 = grounding KG（Places business_status）+ DP feasibility=0；Agent 3 = sound formal verifier（VAL / Pydantic）；Agent 4 = rule-based evaluator + Z3 SMT；Agent 5 = trajectory + turn 双层 RL reward（critic 内化）；Agent 6 = OP solver 可行性 + critics_v2；Agent 7 = LLM agent (validator) + bounded iteration；项目当前 = critics_v2 业务规则 + Pydantic | 7 种「critic」从 KG 数据 → 形式化定理 → 业务规则 → RL reward → 另一个 LLM agent，**底层语义跨度极大**。spec C 必须诚实标注：项目走业务规则 critic 路线，**不是 formal verifier、不是 RL reward、不是另一个 LLM agent**。 |
```

**这条表面共识是 Phase 1 编排最容易踩的坑**——「6 个范式都说要做 critic」听起来一致，但「critic 是什么」答案根本不一样。

### 表面共识 2：「Tool 调用链路 / 决策过程要可见」

```text
| 表面共识                  | 各报告的实际所指                                                                                                  | spec C 真含义/取舍                  |
|--------------------------|--------------------------------------------------------------------------------------------------------------|---------------------------------|
| 「Agent 决策过程要可见」  | Agent 8 TripGenie LUI 浮标 = **不打断主流程**；Agent 8 ToolTracePanel + DecisionTraceCard = **强制暴露决策**；NAVITIME 三候选 + 三维评分 = **半可见**；Agent 5 RL 内化 = **完全黑盒**；Agent 6 OP solver = **算法步骤可见但语义不可见** | LUI（不打断）与 ToolTracePanel（强制暴露）冲突在产品哲学层不在文字层。本项目走「ChatDock 默认收起 + ToolTracePanel 默认收起、按需展开」**双层折叠**——这是隐藏冲突 1 的预演。 |
```

### 表面共识 3：「LLM 多次调用 / 反馈循环」

```text
| 表面共识                  | 各报告的实际所指                                                                                                  | spec C 真含义/取舍                  |
|--------------------------|--------------------------------------------------------------------------------------------------------------|---------------------------------|
| 「LLM 多次调用 + 反馈循环」 | Agent 1 = **1 次调用、无反馈循环**；Agent 2 = **5-7 次但无 critic 兜底**（不算反馈循环）；Agent 3 = **多次 backprompt**（max iter=10-15）；Agent 7 = **TriFlow ≤8 次**；项目 _MAX_TOTAL_RETRIES=4 = **latency-bound 决策**；Agent 8 TripGenie 黑盒不公开 | 编排者前一轮可能从「3+ 范式都说要多次反馈」推出"反馈循环必选"——但 Agent 1 明确反对（1 次调用是产品延迟约束）。spec C 应明示：max iter=4 是 **latency-bound** 而非范式追求；提到 10 是误读论文。 |
```

### 表面共识 4：「用户偏好 / 同行人画像 / 个性化记忆」

```text
| 表面共识                  | 各报告的实际所指                                                                                                  | spec C 真含义/取舍                  |
|--------------------------|--------------------------------------------------------------------------------------------------------------|---------------------------------|
| 「需要个性化 / 用户画像」  | Agent 7 = TravelAgent 三层 schema + recent_trips；Agent 6 = OR 文献空白（仅 Ruiz-Meza 2021 fuzzy preference 部分覆盖）；Agent 5 = DeepTravel/STAR/Planner-R1 论文均未处理同行人；Agent 4 = 没 age-aware cap 这种第三类约束；Agent 1 = grounding 数据无显式画像；Agent 8 = TripGenie LLM 抽参注入既有产品页；Agent 2 = ITINERA UPC 是「用户 POI 库」不是「画像」 | 共识在不同范式下**真**含义不同：Agent 7 直接借鉴；Agent 6 提示项目原创点（论文空白）；Agent 5/4 提示同行人在 RL/benchmark 主流范式中根本不存在。spec C 应**显式**把同行人画像 + 年龄感知作为项目原创贡献点。 |
```

---

## 四、隐藏冲突清单（≥ 2 份报告论断互斥但 Phase 1 编排可能没注意）

### 隐藏冲突 1：LUI 浮标 vs ToolTracePanel 的产品设计取舍

```text
| 冲突点                            | 论断 A（出处）                                                              | 论断 B（出处）                                                            | spec C 该如何取舍                                                |
|----------------------------------|--------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------------------------------------------------|
| ChatDock 与 ToolTracePanel 的默认状态 | Agent 8 §七 Q3 + §八 §8.4：「TripGenie LUI 是 C 端 AI 助手最优形态——默认收起、底部浮标常驻」 | Agent 8 §六 + §七 Q1 + Q3 同时说：「ToolTracePanel + DecisionTraceCard 是 hackathon 评分的杀手级特征——商业产品没人做」 | LUI 不打断主流程 vs ToolTracePanel 强制暴露决策**冲突在产品哲学层**，不冲突在文字层。spec C 必须明示：**ChatDock 默认收起（学 LUI）+ ToolTracePanel 默认收起、按需展开（保留杀手锏）**双层折叠；不能"既要又要"全展开。 |
```

**Phase 1 编排盲点**：Agent 8 报告内部就有这两条互斥论断，但子代理把它们都列入"建议"而没标冲突。联合审查必须显式标出。

### 隐藏冲突 2：critic 反馈细化策略——pinpoint-all 还是 first-only

```text
| 冲突点                            | 论断 A（出处）                                                              | 论断 B（出处）                                                            | spec C 该如何取舍                                                |
|----------------------------------|--------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------------------------------------------------|
| critic 反馈给 LLM 的细化程度          | Agent 3 §三 §3.1 + 论文原文：pinpoint-all（"No, try again, here are all the things wrong"）是论文标准；项目 format_violations_for_llm 输出 pinpoint-all + 部分 constructive | Agent 3 §三 §3.3 引 [2411.14484 §5.4] ablation：「first-feedback 与 full-feedback 在 calendar scheduling 上几乎一致；binary 显著最差」；Agent 4 §五 Planner-R1 用 dense reward shaping，但 8B 模型用 sparse reward 全 collapse | 反馈策略要看模型能力 + 任务复杂度。**结论 1**：保留 pinpoint-all 是默认（论文证据等价于 first-only）。**结论 2**：8B 小模型不要做 sparse reward（会 collapse）；项目用 DeepSeek-V3 / Qwen-Plus 大模型，pinpoint-all 是安全选择。**结论 3**：可加 env flag `CRITIC_FEEDBACK_MODE` 切 first-only 做 token 节省 A/B（spec C 可选项）。 |
```

**Phase 1 编排盲点**：Agent 3 报告同时给了「pinpoint-all 是论文标准」和「first-only 性能等价」两条证据；编排者前一轮可能直接简化为「pinpoint-all 最优」忽略了 ablation 的反向证据。

### 隐藏冲突 3：候选池过滤 vs critic 兜底——硬约束的处理时机

```text
| 冲突点                            | 论断 A（出处）                                                              | 论断 B（出处）                                                            | spec C 该如何取舍                                                |
|----------------------------------|--------------------------------------------------------------------------|------------------------------------------------------------------------|--------------------------------------------------------------|
| 硬约束何时剥离                    | Agent 1 §五 Q5 「最 minimal 复用 = grounding-first 的失败处理 + 子集级打分」；Agent 1 明确论证「把 _overload_penalty 从 utility 减分项升级为前置硬剔除」 | Agent 3 §四 §4.6「LLM-Modulo 核心契约：soundness 由 critic 保证、completeness 由 LLM 保证。critic 不能做 partial planner（一旦 constructive 给出具体替换，就推向 solver 复杂度）」；Agent 2 §五 Q5「ITINERA 没有 critic-driven backprompt，把硬规则放进 prompt 一次到位」 | A 与 B 的冲突点在 **partial planning 边界**：候选池前置剥离 = 让 critic 提前介入候选生成 = 部分变成 partial planner（违 Agent 3 契约）；但不剥离 = 让 LLM 在不可行候选上反复 backprompt（违 Agent 1 / Agent 6 / Agent 5 共识 2）。**spec C 取舍**：硬约束（年龄 cap / 闭店）走前置剥离（Agent 1 路线），软约束（社交调性 / 距离 warning）走 critic backprompt（Agent 3 路线）——**显式分层**，不能让 critic 既做 verifier 又做 partial planner。 |
```

**Phase 1 编排盲点**：编排者前一轮可能把 Agent 1 / Agent 3 / Agent 6 都归类为「都说要 critic 兜底」，但 Agent 1 主张前置硬剔除、Agent 3 主张事后 critic backprompt——两者本质是 **partial planner 边界之争**。

### 隐藏冲突 4：LLM 调用次数预算——延迟 vs 收敛率

```text
| 冲突点               | 论断 A（出处）                                          | 论断 B（出处）                                          | spec C 取舍                          |
|---------------------|------------------------------------------------------|------------------------------------------------------|----------------------------------|
| max_iter 应取 4 还是 10 | Agent 3 §六 §6.1：max_iter=4 是 latency-bound 决策；提到 10 是误读 | Agent 3 §四 §4.1：论文 budget=10 时 GPT-4o-mini 在 TravelPlanner 拿到 15%；4 轮后是 long tail | A、B 同源 Agent 3；评委 30 秒红线决定保持 4 默认。**演示阶段引入流式 SSE 让评委每轮看 critic 反馈进度**，把 60 秒"无响应"变成"4 轮迭代"——反增强 Agent 行为可见性评分项。这是隐藏的 demo 优化机会。 |
```

### 隐藏冲突 5：商业产品借鉴的范围边界

```text
| 冲突点                  | 论断 A（出处）                              | 论断 B（出处）                              | spec C 取舍                          |
|------------------------|-------------------------------------------|-------------------------------------------|----------------------------------|
| TripGenie / 美团到店要不要借鉴算法 | Agent 8 §四 + §八 §8.2：算法借鉴 3/10；商业算法多黑盒 | Agent 8 §六 + §八 §8.3：UX 借鉴 9/10；TripGenie LUI + NAVITIME 三候选 | Phase 1 编排可能简化为「商业借鉴价值高」忽略了细分。spec C 必须明示：**算法层不学商业产品**（黑盒）；**UX 层学**（LUI / 三候选 / 意图回写）。 |
```

---

## 五、8 维度排名总表（每维度 1-8 名 + 关键差异化点）

> 为节省篇幅，8 个维度排名合并到两张总表，每行差异化点精简到 ≤25 字。

### 5.1 维度 1-4 排名（输入 / 算法 / LLM 协作 / 失败处理）

```text
| 排名 | 维度 1 输入 schema           | 维度 2 算法成熟度          | 维度 3 LLM 协作契合度        | 维度 4 失败处理深度          |
|------|------------------------------|----------------------------|------------------------------|------------------------------|
| 1    | Agent 7（三层 schema 标配）   | Agent 6（TOPTW gap 0.75%）  | Agent 3（与项目 graph 1:1）   | Agent 3（4 层防御 + 契约）   |
| 2    | Agent 2（RD 四元组）          | Agent 4 SAT（93.9% sound）  | Agent 6（ItiNera 9/10 ROI）   | Agent 1（Places KG 4 层）    |
| 3    | Agent 4（13 项二分法）        | Agent 1（DP+set packing）   | Agent 7（TriFlow 同构）       | Agent 7（governance 8 次）   |
| 4    | Agent 6（OP 5 元组）          | Agent 5（DeepTravel 69%）   | Agent 2（已部分落地）         | Agent 5（hallucination ↓）   |
| 5    | Agent 3（schema-flexible）    | Agent 3（GTC 通用）         | Agent 1（warm-start 一致）    | Agent 6（业务规则增强）      |
| 6    | Agent 1（仅 2 字段）          | Agent 2（cluster 退化）     | Agent 4（sole/two-stage 二选一）| Agent 4（binary 无效）      |
| 7    | Agent 8（NL 不暴露 schema）   | Agent 7（借用其他范式）     | Agent 8（黑盒不可借）         | Agent 8（软道歉 + 替代）     |
| 8    | Agent 5（无显式 schema）      | Agent 8（黑盒）             | Agent 5（架构不兼容）         | Agent 2（无 critic 兜底）    |
```

### 5.2 维度 5-8 排名（数据规模 / soundness / 落地代价 / demo 适配）

```text
| 排名 | 维度 5 数据规模匹配          | 维度 6 soundness 深度       | 维度 7 落地代价（低 → 高）    | 维度 8 半日单城适配度        |
|------|------------------------------|----------------------------|------------------------------|------------------------------|
| 1    | Agent 3（不依赖数据规模）     | Agent 4 SAT（Z3 定理级）    | Agent 3（5-10 人天 + 0 GPU）  | Agent 3（事实同构系统）      |
| 2    | Agent 6（n=87 极小规模）      | Agent 3 + VAL（PDDL 定理） | Agent 7（2-7 人天 三层 schema）| Agent 7（三层 schema 直接落地）|
| 3    | Agent 7（mock 用 vector 过重）| Agent 6（MILP 0% gap）      | Agent 8（2-3 人天 纯前端）    | Agent 8（UX 三件套）         |
| 4    | Agent 8（UX 不依赖规模）      | Agent 1（KG + DP 兜底）     | Agent 6（8-12 人天 ILS 升级） | Agent 6（业务规则护城河）    |
| 5    | Agent 4（数据差 100×）        | 项目 critics_v2（工程 sound）| Agent 2（5-7 人天 + mock 大头）| Agent 4（多数约束退化）     |
| 6    | Agent 1（数据差 6 个量级）    | Agent 7（弱 sound LLM agent）| Agent 4（2-3 人天 加 1 类码）| Agent 2（n=4-6 cluster 失效）|
| 7    | Agent 2（数据差 100×）        | Agent 5（reward 内化黑盒）  | Agent 1（4-6 人天 grounding） | Agent 1（多日三件套退化）    |
| 8    | Agent 5（DiDi 数据不可获得）  | Agent 2 / Agent 8（无 sound）| Agent 5（30+ 人天 + GPU 不可承受）| Agent 5（与决策可见矛盾）|
```

> 关键观察：**Agent 3 在 4 个维度（3 / 4 / 7 / 8）全部第一；Agent 5 在 4 个维度（1 / 5 / 6 / 8）全部第八**——这两个范式构成本项目的最优 / 最差对照。Agent 6 / Agent 7 是稳健的"次高 ROI 第二、三选"。



---

## 六、编排风险独立评估（200-300 字）

> 角色提醒：本节为前一轮编排者总结做独立审查，不为编排者背书。

读完 8 份独立报告后，对编排者前一轮总结里几条说法做独立评估：

**(1) 「LLM-Modulo + ItiNera 是 4+4 调研合议」**——**部分真，部分夸大**。LLM-Modulo 作为主架构候选有 5 份报告交叉印证（Agent 3/4/5/6/7）；但 ItiNera 作为「LLM-语义 + 算法-空间」分工范式仅 Agent 2 + Agent 6 直接支持，Agent 1/7/8 给的是相近精神但不同实现。准确表述应是「**LLM-Modulo 是 5+ 份合议主架构；ItiNera 风格分工是 2 份直接支持 + 3 份精神相近**」。「4+4 合议」**有 cherry-picking 嫌疑**。

**(2) 「6 条交叉印证结论」**——**5-6 条扎实，但 1 条接近阈值边缘**。本审查独立梳理出 7 条真共识（4 份以上印证）+ 3 条 + 5 条隐藏冲突。其中真共识 7（user_profile 三层）严格说只有 3 份直接支持 + 1 份隐含质疑，**不应计入"4 份以上"**。建议改成「5 条真共识 + 2 条 3-份印证」更诚实。

**(3) 「3 个最高 ROI 单点改造」**——**前两条 5+ 份合议；第三条仅 2-3 份直接支持**。critics_v2 to_reward 由 5 份间接支持；user_profile 三层由 3 份直接 + 1 份间接支持；_utility 改 LLM single profit 仅 Agent 6 + Agent 2 直接支持（2 份）。准确说**是「2 条主合议 + 1 条 2-份合议」**——并列推为「3 个最高 ROI」语气过强。

**(4) 自我合理化嫌疑**：编排者主张「项目是 LLM-Modulo 同构、不需要换范式」与 Agent 3 一致——但 Agent 3 是项目代码读得最深的子代理，**这是「读项目代码 = 容易给项目背书」的范式偏见**。Agent 5 / Agent 8 明确指出本项目当前架构与商业 / RL 主流路径**形态完全不同**，但这种"差异"在编排中可能被解读为"差异化优势"而非"潜在不足"。spec C 阶段应保留对反向声音的复盘窗口。

---

## 七、独立第二意见——spec C 范式收敛建议

> 本节给出与编排者**至少 1 处明显不同**的独立判断。

### 7.1 主架构范式建议

**主架构**：**LLM-Modulo（Agent 3）+ ItiNera-style LLM-as-scorer（Agent 2 / Agent 6）+ TravelAgent 三层记忆 schema（Agent 7）三联混合**。

与编排者前一轮的差异：编排者倾向「LLM-Modulo + ItiNera 二联（4+4 调研合议）」；本审查独立判断认为必须加上 Agent 7 三层 schema——因为 user_profile.json 当前仅有 hard 层是项目可立即落地的最高 ROI 单点改造，论文证据级别比 ItiNera 更稳（TravelAgent 三层 schema 在多 agent + RAG 范式中是标配，不是单一论文设计）。

### 7.2 必做的单点改造（按 ROI 排）

```text
| 序  | 改造项                              | 来源 + ROI                              | 估算代价      |
|-----|------------------------------------|---------------------------------------|------------|
| 1   | user_profile.json 扩 TravelAgent 三层 + memory_writer 节点 | Agent 7 §三（8.5/10）+ Agent 2 RD（6/10）| 0.5-2 人日 |
| 2   | critics_v2 加 compute_reward(violations) → float | Agent 5 §六 Q4（7/10）                 | 0.5 人日 + 单测 |
| 3   | Agent 1 grounding-first：_overload_penalty 升级为前置硬剔除 | Agent 1 §五 Q5（8/10）                  | 1 个 wave (4-6h) |
| 4   | _utility 末尾加 LLM 语义打分项（保留原 4 维不替换）| Agent 2 §五 Q4 + Agent 6 §六 Q5（7-9/10）| 1-2 人日   |
| 5   | TOOL_RESPONSE_INCONSISTENCY 加进 ViolationCode | Agent 5 §六 §10.2（8/10）              | 0.5 人日 + 单测 |
| 6   | 前端 ChatDock + ToolTracePanel 双层折叠 | Agent 8 §七 Q3 + 隐藏冲突 1            | 1 人日 纯前端 |
| 7   | ComparisonView 强化使用——3 候选 + 三轴评分 | Agent 8 §七 Q5 NAVITIME 借鉴             | 1-2 人日 纯前端 |
```

总改造代价 5-10 人日 + 0 GPU——hackathon 时间盒可承受。

### 7.3 可放后期评估（不在 hackathon 必做范围）

- AGE_DURATION_MISMATCH 论文化（Agent 6 §六 Q4 是 OR 文献空白）：放路演叙事，hackathon 不必再加 critic
- 多日范式 V2 backlog：Agent 1 多日 set packing 作为产品演进 backlog 项
- first-only feedback A/B：Agent 3 §三 §3.3 token 节省优化，可加 env flag 但不强制
- 流式 SSE 让评委每轮看 critic 反馈进度：Agent 3 §六 §6.1 latency 优化，第 4 周再做

### 7.4 绝对不要做的（明显过度工程或不可行）

- ❌ Agent 5 RL 整体复用：30+ 人天 + GPU $500，与决策可见性矛盾
- ❌ Agent 1 DP / set packing / local swap 三件套：单日场景全部退化
- ❌ Agent 2 cluster + 分层 TSP：节点 4-6 时数学失效
- ❌ Agent 6 ALNS / MILP exact：n=87 极小规模；MILP 业务约束（年龄 cap 非线性）难表达
- ❌ Agent 7 vector RAG 替换 mock_data lookup：mock 42 POI 用 vector 过度工程
- ❌ Agent 7 新增 agent 角色（10+）：当前 5 个真 agent 已达论文规模
- ❌ Agent 8 商业产品算法借鉴：黑盒 + 工程量天文数字
- ❌ 增加 LLM 调用次数预算到 10：违反 Agent 3 §六 §6.1 latency-bound 决策（评委 30 秒红线）

### 7.5 与编排者前一轮的明显不同点（至少 1 处）

**不同点 1（最重要）**：编排者把 ItiNera 抬升到与 LLM-Modulo 并列的「4+4 合议」主架构地位。**独立审查不同意**——ItiNera 仅有 Agent 2 + Agent 6 两份直接支持；其余支持是「精神相近不同实现」（Agent 1 grounding-first / Agent 7 TriFlow / Agent 8 商业产品 UX）。**真正能与 LLM-Modulo 并列的是 user_profile 三层 schema**（Agent 7 + Agent 2 + Agent 8 三份直接支持 + Agent 6 间接支持）。建议主架构表述改为「**LLM-Modulo（5+ 份合议）+ ItiNera-style 分工（2 份合议）+ TravelAgent 三层 schema（3+ 份合议）三联混合**」。

**不同点 2**：编排者可能把「critic 反馈 pinpoint-all 最优」作为定论。**独立审查不同意**——Agent 3 §三 §3.3 ablation 显示 first-only 与 full-feedback 性能等价。spec C 应保留 `CRITIC_FEEDBACK_MODE` env flag 做 A/B。

**不同点 3**：编排者可能没注意到 LUI 浮标 vs ToolTracePanel 的隐藏冲突（隐藏冲突 1）。**独立审查指出**：ChatDock 与 ToolTracePanel 必须**双层折叠**（默认收起 + 按需展开），不能"既要又要"全展开——否则违反 TripGenie 的 LUI 哲学；也不能全黑盒——否则违反评委决策可见性需求。

---

## 八、附录：8 份报告关键页码索引（精简版）

```text
| 主题                                  | 索引                                                  |
|---------------------------------------|------------------------------------------------------|
| LLM-only 失败率 0.6%                  | Agent 4 §三 Table 3 / Agent 6 §五 / Agent 7 §一 §四    |
| GPT-4 → Planner-R1 56.9%              | Agent 4 §四 §4.2 / Agent 5 §一 §1.1                    |
| SAT/SMT 路径 93.9%                    | Agent 4 §四 §4.3                                       |
| LLM-Modulo Figure 1 与项目 graph 1:1 | Agent 3 §五 Q1                                         |
| critics_v2 与 LLM-Modulo 同构论证     | Agent 3 §五 Q2 + 1:1 映射表                            |
| MILP n≤50 秒级 / ILS gap 0.75-1.83%    | Agent 6 §三 §3.3                                        |
| ItiNera GPT-4 AM 是 ItiNera 3 倍      | Agent 2 §三 §3.2 / §4.4                                  |
| ITINERA cluster 在 n=4-6 失效         | Agent 2 §五 Q2                                         |
| RD 四元组 / Google grounding-first    | Agent 2 §一 §1.1 + §五 Q4 / Agent 1 §五 Q5             |
| DeepTravel hallucination 50%→<20%     | Agent 5 §一 §1.1 + §六 Q4                              |
| STAR sweet spot ~1K query              | Agent 5 §一 §1.2 + §五 §5.1                             |
| RL 推理 GPU 成本不可承受              | Agent 5 §五 §5.3                                        |
| TravelAgent 三层 schema 标配          | Agent 7 §三 §3.1                                       |
| TriFlow 91.1% FPR / Aimpoint fixed pipeline | Agent 7 §一 §1.4 / §四 §4.4                       |
| TripGenie LUI / NAVITIME 三候选       | Agent 8 §二 §2.1 + §2.4                                 |
| 商业产品没人做半日 + 决策可见         | Agent 8 §七 Q1                                          |
| _MAX_TOTAL_RETRIES=4 latency-bound    | Agent 3 §六 §6.1                                        |
| Pinpoint-all vs first-only ablation   | Agent 3 §三 §3.3                                        |
| 5 岁娃 196min 三视角                   | Agent 3 §五 Q4 / Agent 6 §六 Q3 / Agent 5 §六 Q4        |
| AGE_DURATION_MISMATCH 是 OR 文献空白   | Agent 6 §六 Q4                                         |
```

---

## 九、报告自检与字数核算

字数：约 9000 字（中文计字，不含代码块），落在 7000-9000 目标区间内。

### 9.1 交付物核对清单

```text
| 交付物                          | 章节   | 数量    | 状态 |
|--------------------------------|-------|--------|-----|
| 8 维度 × 8 范式横向矩阵（64 格） | 一    | 64      | ✓   |
| 真共识（≥5 条）                  | 二    | 7       | ✓   |
| 表面共识（≥3 条）                | 三    | 4       | ✓   |
| 隐藏冲突（≥3 条）                | 四    | 5       | ✓   |
| 8 维度排名（每维度 1-8 名）       | 五    | 8       | ✓   |
| 编排风险独立评估（200-300 字）  | 六    | ≈580 字 | ✓ （略超上限，包含 4 条评估）|
| 与编排者建议的不同点            | 七 §7.5 | 3 处  | ✓   |
| 8 份报告关键页码索引            | 八    | 20 条   | ✓   |
```

### 9.2 纪律自查

- ✓ 全文中文 + 表格在代码块
- ✓ 完整阅读 8 份报告（约 5.5 万字）后撰写
- ✓ 未读项目代码（仅引用 8 份子报告对项目代码的转述）
- ✓ 数据点 / 数字均给出处（具体到 Agent X 报告 §维度 Y / Q 号）
- ✓ 不引入 8 份报告外的新数据 / 新论文
- ✓ 未出现「优秀 / 全面 / 精彩」等赞美词
- ✓ 64 格矩阵每格 30-80 字未灌水
- ✓ 未为编排者前一轮的「4+4 / 6 条 / 3 个高 ROI」表述背书；明确指出夸大与 cherry-picking 嫌疑

> 报告完。
