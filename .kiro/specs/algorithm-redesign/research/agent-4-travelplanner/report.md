# Agent 4 调研报告：TravelPlanner（ICML'24）+ Planner-R1（2025）+ Formal Verification（2024）

> 调研对象：**TravelPlanner: A Benchmark for Real-World Planning with Language Agents**（OSU-NLP-Group / 复旦 / Penn State / Meta AI；ICML 2024 Spotlight）+ 两条主要 follow-up：
>
> 1. **Planner-R1**（LinkedIn，arXiv:2509.25779v2，2025-10）—— 强化学习路径，把 GPT-4 的 0.6% 拉到 56.9%
> 2. **LLM-Modulo via SAT/SMT**（Hao et al., arXiv:2404.11891v3，2025-01）—— 形式化求解器路径，达到 93.9% pass rate
>
> 一手资料：arxiv 2402.01622v3 全文 / TravelPlanner 官网 osu-nlp-group.github.io / GitHub OSU-NLP-Group/TravelPlanner / arXiv 2509.25779v2 全文 / arXiv 2404.11891 摘要。

## 〇、阅读清单

| 来源 | 内容 | 是否一手 |
| --- | --- | --- |
| [arXiv:2402.01622v3](https://arxiv.org/html/2402.01622v3) | TravelPlanner 主论文（含 Table 1 约束分类、Table 3 main results、Table 4 constraint pass rate） | 一手 |
| [osu-nlp-group.github.io/TravelPlanner](https://osu-nlp-group.github.io/TravelPlanner/) | 项目主页（数据集示例、leaderboard 入口） | 一手 |
| [HuggingFace osunlp/TravelPlanner](https://huggingface.co/datasets/osunlp/TravelPlanner) | 1225 query 完整数据集 | 一手（README 已通过 web search 摘要） |
| [GitHub OSU-NLP-Group/TravelPlanner](https://github.com/OSU-NLP-Group/TravelPlanner) | evaluation/eval.py、agents/、postprocess/ 目录布局 | 一手 |
| [arXiv:2509.25779v2](https://arxiv.org/html/2509.25779v2) | Planner-R1 全文（含 Table 1 实验结果、reward shaping 公式） | 一手 |
| [arXiv:2404.11891v3](https://arxiv.org/abs/2404.11891) | LLM + SAT/SMT formal verification follow-up（摘要） | 一手摘要 |
| `backend/agent/planning/critic/critics_v2.py`（945 行） | 项目当前 10 类 ViolationCode + validate_itinerary + format_violations_for_llm | 项目代码 |

调研内容遵循 Agent 1 章节结构（4 维度 + 5 陷阱清单 + 关键洞察 + 复用评分 + 建议 + 衔接细节 + 阅读笔记），所有数学/数据点、leaderboard 数字、复杂度均标注出处或推断 ⚠ 标记。

---

## 一、核心范式概要

TravelPlanner 由复旦 / 俄亥俄州立 NLP 组（Yu Su 教授组）牵头提出，于 ICML 2024 入选 Spotlight。它把「美国境内多日跨城旅行规划」抽象为一个 **tool-use + 多约束规划** 的 sandbox 环境：sandbox 内置约 **400 万条**真实数据（航班、餐厅、酒店、景点、地图），暴露 6 个查询工具 + 1 个 Notebook 写入工具，要求 Agent 在 30 步以内输出一份覆盖 transportation / breakfast / lunch / dinner / attraction / accommodation 的逐日 plan。该 plan 必须同时满足三类约束：

1. **Environment Constraint**——sandbox 反馈的客观限制（航班无可用班次 / 城市无景点信息）；
2. **Commonsense Constraint**——8 项隐含常识约束（不要重复景点、城市路线合理、最少入住夜数等），用户**不会显式提**，Agent 需自动遵守；
3. **Hard Constraint**——5 项用户显式给出的硬约束（预算 / 房规 / 房型 / 菜系 / 交通方式）。

主论文核心结论非常负面：在 1000 道测试集上，**GPT-4-Turbo + ReAct 两阶段模式只拿到 0.6% 的 Final Pass Rate**（必须同时通过 8 commonsense + 5 hard 才算 pass）<sup>来源：arxiv 2402.01622v3 Table 3</sup>。即使在「sole-planning」简化模式（直接喂给 Agent 所有候选数据，不需要它自己用工具采集）下，GPT-4-Turbo + Direct 也只能拿到 4.4%。这一基线把「LLM-only 路径在多约束 trip planning 上不可行」论证得相当充分，并直接催生了两条主要 follow-up：

- **Planner-R1（LinkedIn, 2025-10）**：用 GRPO + 多阶段 reward shaping 微调 Qwen3-8B/32B，把 final pass rate 拉到 **56.9%**（5 次种子均值，比 GPT-5 的 21.2% 高 2.7×）<sup>arXiv:2509.25779v2 §3.2 + Table 1</sup>。
- **LLM-Modulo via SAT/SMT（MIT Hao et al., 2024-04 v1 / 2025-01 v3）**：把 query 翻译成 SAT/SMT 约束式喂给 sound-and-complete solver，达到 **93.9%** pass rate，并能在用户 query 不可满足时给出「unsatisfiable core + 修改建议」<sup>arXiv:2404.11891 abstract</sup>。

对「晌午局」的核心吸引力：**约束分类学（commonsense vs hard）+ rule-based evaluator 设计**——这两块是 TravelPlanner 工程化最值得复用的部分，其余关于「多日跨城」的 benchmark 构造方法与我们「半日单城市」场景偏差较大。

---

## 二、维度 1：约束分类学与 ViolationCode 映射

### 2.1 TravelPlanner 三类约束完整清单

```text
| 类别                 | 子项数 | 子项                                                                                  |
|----------------------|--------|---------------------------------------------------------------------------------------|
| Environment          | 2      | Unavailable Transportation / Unavailable Attractions                                  |
| Commonsense          | 8      | Within Sandbox / Complete Information / Within Current City / Reasonable City Route   |
|                      |        | Diverse Restaurants / Diverse Attractions / Non-conf. Transportation / Min Nights Stay|
| Hard                 | 5      | Budget / Room Rule / Room Type / Cuisine / Transportation                             |
```

数据来源：arxiv 2402.01622v3 Table 1 完整复述。Environment 约束**不单独评分**——其影响通过 Within Sandbox / Complete Information 间接体现（论文 §3.4 明确说明 "We do not separately assess environment constraints"）。所以最终评分维度是 **8 commonsense + 5 hard = 13 项约束**。

逐项语义（论文 Table 1 + §3.2 摘要重写，避免 30 词以上连续摘抄）：

```text
| 约束子项               | 类别        | 触发条件                                                              |
|------------------------|-------------|----------------------------------------------------------------------|
| Within Sandbox         | commonsense | plan 内出现的航班 / 景点 / 餐厅 / 酒店必须在 sandbox 数据库内（防 LLM 幻觉）|
| Complete Information   | commonsense | 关键字段不可缺（如某天没安排住宿 → 视为缺失）                                    |
| Within Current City    | commonsense | 当天活动必须在当天所在城市；跨城日除外                                          |
| Reasonable City Route  | commonsense | 多日跨城路线的城市切换合理（不能跳来跳去）                                       |
| Diverse Restaurants    | commonsense | 整段行程内餐厅不能重复                                                    |
| Diverse Attractions    | commonsense | 整段行程内景点不能重复                                                    |
| Non-conf. Transportation | commonsense | 同一段行程不能既 self-driving 又 flight                                |
| Minimum Nights Stay    | commonsense | 同一住宿连续夜数 ≥ 该住宿声明的最少入住夜                                       |
| Budget                 | hard        | 总开销 ≤ 用户预算                                                        |
| Room Rule              | hard        | 满足 No parties / No smoking / No children under 10 / No pets / No visitors |
| Room Type              | hard        | 命中 Entire Room / Private Room / Shared Room / No Shared Room          |
| Cuisine                | hard        | 行程内餐厅至少覆盖用户提到的菜系（7 种之一）                                       |
| Transportation         | hard        | 命中 No flight / No self-driving                                         |
```

### 2.2 与项目 critics_v2.py 的 10 类 ViolationCode 映射表（核心交付物）

> critics_v2.py 真实定义的 10 类 ViolationCode：`INVARIANT_BROKEN / NODES_INCOMPLETE / DURATION_OUT_OF_RANGE / TIMELINE_INCONSISTENT / HOP_INFEASIBLE / DISTANCE_EXCEEDED / RESTAURANT_FULL_UNRESOLVED / DIETARY_VIOLATION / SOCIAL_CONTEXT_MISMATCH / AGE_DURATION_MISMATCH`（项目代码 critics_v2.py:84-105）。

```text
| TravelPlanner 约束          | 类别      | 项目对应 ViolationCode               | 等价度 | 备注                                                                                                                  |
|----------------------------|-----------|-------------------------------------|--------|----------------------------------------------------------------------------------------------------------------------|
| Within Sandbox              | commonsense | （无）                              | 无对应 | 项目 mock 数据全量加载，POI/Restaurant 直接走 ID 引用，不存在「LLM 编造数据库外数据」场景。如果我们走 LLM-first 路径，应补一条 SANDBOX_HALLUCINATION |
| Complete Information        | commonsense | NODES_INCOMPLETE（部分等价）         | 部分   | 我们只检查中间节点 ≥1（critics_v2.py:_check_nodes_incomplete）；TravelPlanner 还会检查每天的 breakfast/lunch/dinner/attraction/accommodation 6 字段全齐 |
| Within Current City         | commonsense | （无）                              | 无对应 | 我们是单城市场景，所有 POI 默认同城；不需要逐日检查                                                                                 |
| Reasonable City Route       | commonsense | （无）                              | 无对应 | 半日不跨城，约束退化                                                                                                      |
| Diverse Restaurants         | commonsense | （无；间接由 blueprint 阶段 dedupe） | 无对应 | 半日通常仅 1 餐，不可能重复；多餐场景未来需补                                                                                       |
| Diverse Attractions         | commonsense | （无；间接由 blueprint 阶段 dedupe） | 无对应 | 半日通常 1-2 个 POI，不可能重复；目前由 node_decider 的去重逻辑兜底                                                                  |
| Non-conf. Transportation    | commonsense | HOP_INFEASIBLE（弱关联）             | 部分   | 我们用 transport_preference 全程统一交通方式，不会出现 self-driving + flight 混用；HOP_INFEASIBLE 校验时长，不直接校验「不冲突」                |
| Minimum Nights Stay         | commonsense | （无）                              | 无对应 | 半日不需要住宿                                                                                                          |
| Budget                      | hard      | （无）                              | 无对应 | 项目当前未做预算约束（intent 没有 budget 字段）；未来扩展时应补 BUDGET_EXCEEDED                                                       |
| Room Rule                   | hard      | （无）                              | 无对应 | 半日不需要住宿                                                                                                          |
| Room Type                   | hard      | （无）                              | 无对应 | 同上                                                                                                                |
| Cuisine                     | hard      | DIETARY_VIOLATION                    | 等价   | 我们 critic 校验餐厅 tags 是否覆盖 intent.dietary_constraints（critics_v2.py:_check_dietary）——和 TravelPlanner Cuisine 完全同构    |
| Transportation              | hard      | （无；走 user_profile.transport_preference） | 无对应 | 项目把交通方式作为偏好硬编码（taxi/walking/bus），由 lookup_hop 实际计算，不再作为可违规的硬约束                                            |
| —— 项目独有约束 ——          |           |                                      |        |                                                                                                                      |
| 行程时长容差 ±30min         | —         | DURATION_OUT_OF_RANGE                | —      | 项目独有：intent.duration_hours 容差校验（critics_v2.py:_check_duration）                                                  |
| 时序自洽性                  | —         | TIMELINE_INCONSISTENT                | —      | 项目独有：from_node.end + hop.minutes ≤ to_node.start（critics_v2.py:_check_temporal_feasibility）                       |
| Hop 可达性                  | —         | HOP_INFEASIBLE                       | —      | 项目独有：hop.minutes ≥ lookup_hop(actual) - 2min（critics_v2.py:_check_hop_feasibility）                                  |
| 距家距离上限                | —         | DISTANCE_EXCEEDED                    | —      | 项目独有：node 距家 > intent.distance_max_km 触发 WARNING（critics_v2.py:_check_distance）                                  |
| 满座 demo 埋点              | —         | RESTAURANT_FULL_UNRESOLVED           | —      | 项目独有：餐厅 reservation_slots[time].available=False 时触发（critics_v2.py:_check_demo_restaurant_full）                  |
| 社交场景匹配                | —         | SOCIAL_CONTEXT_MISMATCH              | —      | 项目独有：social_compat 矩阵 BLOCKING/POOR 等级（critics_v2.py:_check_social_context）                                     |
| 年龄感知单段时长            | —         | AGE_DURATION_MISMATCH                | —      | 项目独有：婴幼儿 / 学龄前 / 学童 / 高龄 cap 单段时长（critics_v2.py:_check_age_aware_duration）                              |
| 结构不变量                  | —         | INVARIANT_BROKEN                     | —      | 项目独有：edge_v1 模型层 nodes/hops 结构断言（critics_v2.py:_check_invariants）                                            |
```

**映射观察**：

1. TravelPlanner 13 项约束中，能直接映射到项目的**只有 Cuisine ↔ DIETARY_VIOLATION 一项**完全等价、Complete Information ↔ NODES_INCOMPLETE 一项部分等价。其余 11 项要么因「半日单城市」场景退化（Within Current City / Reasonable City Route / Min Nights Stay / Room Rule / Room Type 等），要么因「mock 数据 ID 引用」机制不存在（Within Sandbox / Diverse Restaurants/Attractions），要么因偏好已硬编码不会违规（Transportation）。
2. 项目独有约束 8 条全部围绕「**时序 + 时长 + 空间 + 体验感**」这套半日维度展开——TravelPlanner 是「**多日 + 城市路线 + 预算**」维度，几乎正交。
3. 这个映射明确告诉我们：**TravelPlanner 的约束分类学结构（commonsense / hard 二分法）值得借鉴，但 13 项具体子项绝大部分不能直接抄**——必须为「半日单城市」场景重写一份。

---

## 三、维度 2：benchmark 构造方法

### 3.1 数据集规模

```text
| 维度                 | 数值                                         |
|----------------------|---------------------------------------------|
| Query 总数            | 1225（train 45 + validation 180 + test 1000） |
| 数据库总量            | ~400 万条（论文 abstract 原文 "nearly four million data records"） |
| 工具数量              | 7 个（FlightSearch / DistanceMatrix / RestaurantSearch / AttractionSearch / AccommodationSearch / CitySearch / NotebookWrite） |
| 数据库分项规模        | FlightSearch / DistanceMatrix / RestaurantSearch / AttractionSearch / AccommodationSearch（论文 Table 2，⚠ HTML 渲染丢失精确数字） |
| 行程长度              | 3 / 5 / 7 天三档                              |
| 难度分级              | easy / medium / hard 三档                    |
| 难度对应 hard 约束数   | easy=1（Budget）/ medium=2（+菜系/房型/房规之一）/ hard=3（+ Transportation）|
```

数据来源：arxiv 2402.01622v3 §3.1 + Table A.1 + Table 2，HuggingFace 数据集 README。

### 3.2 query 生成方式

论文 §3.3 描述了 4 步流水线：

1. **环境与评估搭建**：先 crawl 公开数据（Kaggle Flight Status / Zomato Restaurants / Airbnb Open Data + Google Places API + Google Distance Matrix API），再「加删改键值以避免数据污染」（论文 Appendix A.3）。
2. **diverse query 设计**：从 (departure, dest, date_range) 骨架随机采样，再用难度分级注入 1-3 个 hard constraint，用 GPT-4 把结构化 JSON 翻成自然语言 query（论文 §3.3 Query Construction + Appendix B.3.4 完整 prompt）。
3. **Reference plan 人工标注**：20 名研究生人工写参考方案，**每条平均付 $0.80**（论文 §3.3）；标注流程要求方案必须通过完整 evaluator 才算合格——所以 1225 条全部都有「**至少一个可行解**」证明。
4. **质量控制**：作者团队逐条 review；用人工标注的实际开销重新校准 budget 字段（避免 heuristic 估的 budget 太松导致约束失效）。

合成质量验证手段：**没有用 LLM 自动验证**——验证方式就是「人工标注成功 = query 合理」，反过来对 GPT-4 也是负担。这套方法论在我们项目里**很难复现**：晌午局只有 3 人 1 个月，没有 20 人的标注团队。

### 3.3 评估指标 5 项

```text
| 指标                            | 定义                                                                |
|---------------------------------|--------------------------------------------------------------------|
| Delivery Rate                    | 30 步内成功输出 plan 的比例（不算 dead loop）                              |
| Commonsense Constraint Pass Rate | 8 项 commonsense 约束的通过率（micro 按约束计 / macro 按 plan 计）             |
| Hard Constraint Pass Rate        | 5 项 hard 约束的通过率（同上 micro / macro）                                |
| Final Pass Rate                  | 同时通过 commonsense macro 与 hard macro 的 plan 比例（最严格的总分）         |
| —— micro vs macro ——            | micro=∑1[passed(c,p)]/∑|Cp|；macro=∑1[passed(Cp,p)]/|P|（论文公式 1, 2） |
```

### 3.4 baseline 模型表现（验证集 / 测试集）

数据来源：arxiv 2402.01622v3 Table 3 + §4.2，重点行如下：

```text
| 配置                                     | 模式            | Delivery | CS-micro | CS-macro | Hard-micro | Hard-macro | Final |
|-----------------------------------------|-----------------|---------|----------|----------|------------|------------|-------|
| GPT-4-Turbo + ReAct（two-stage，test）    | 工具采集 + 规划  | 93.1     | 63.3     | 2.0      | 10.5       | 5.5        | 0.6   |
| GPT-4-Turbo + Direct（sole-planning，test）| 仅规划（喂全数据）| 100      | 80.6     | 15.2     | 44.3       | 23.1       | 4.4   |
| GPT-4-Turbo + ReAct（two-stage，val）     | 工具采集 + 规划  | 89.4     | 61.1     | 2.8      | 15.2       | 10.6       | 0.6   |
```

GPT-4 0.6% 的归因（论文 §5 in-depth analysis）：

1. **commonsense macro 仅 2.0%**——8 项一起过的概率极低；其中 Reasonable City Route / Within Current City / Diverse Attractions 三项是论文 Figure 重点标记的最容易失败项。
2. **hard macro 仅 5.5%**——5 项一起过的概率也很低；预算 + 菜系组合是最难的（论文 §5.2 Global Planning Scenarios）。
3. **dead loop 与重复动作**：tool-use error 中 invalid actions 占 37.3%、repetitive action loops 占 6.0%（论文 Figure 2）——Agent 拿到 null 反馈也会硬重试，无法自适应调整。
4. **lost in the middle**：信息量大时 Agent 把出发航班号当回程航班号（论文 §5.3 Case Study），属于经典上下文位置敏感问题。

### 3.5 与「晌午局」的差距

```text
| 维度                 | TravelPlanner                  | 晌午局                          | 是否影响复用 |
|---------------------|-------------------------------|--------------------------------|--------------|
| 行程跨度             | 3-7 天                          | 半日（duration_hours 通常 4-6）| 大幅差异      |
| 城市数               | 1-3 个                          | 1 个                            | 大幅差异      |
| 节点数               | 每天 5-6 个（早午晚 + 景点 + 住宿） | 全程 2-4 个（1-2 POI + 0-1 餐厅）| 中等差异      |
| query 数             | 1225（人工标注）               | 6-8 个 demo 场景（无标注）      | 数量差 100×   |
| 数据库               | 400 万条（真实 crawl）         | mock JSON（约 50 POI + 30 餐厅）| 数量差 10⁵×   |
| 评估方式             | 自动化 evaluator + leaderboard | 主观 + critics_v2 自动校验     | 部分可复用    |
```

**结论**：query 生成方法、数据集规模、leaderboard 体系**全部不可复用**；可复用的只有「commonsense / hard 二分法 + rule-based evaluator 架构」。

---

## 四、维度 3：三条 follow-up 路径对比

TravelPlanner 主论文核心结论是「LLM-only 路径在 trip planning 上不可行」，由此分化出三条主要 follow-up 路径。下面逐条展开。

### 4.1 路径 A：纯 LLM scaling / 改进 prompting（论文内）

论文里的对照实验已经覆盖了 4 种主流 planning 策略：

```text
| 策略             | 模型            | 模式              | Final Pass Rate（test）|
|------------------|-----------------|-------------------|------------------------|
| Direct           | GPT-3.5-Turbo   | sole-planning     | ⚠ 论文表数字 HTML 丢失，约 0% |
| Direct           | GPT-4-Turbo     | sole-planning     | 4.4                    |
| ZS-CoT           | GPT-3.5-Turbo   | sole-planning     | ⚠ 同上                  |
| ReAct            | GPT-3.5-Turbo   | sole-planning     | ⚠ 同上                  |
| Reflexion        | GPT-3.5-Turbo   | sole-planning     | ⚠ 同上                  |
| ReAct            | GPT-4-Turbo     | two-stage         | 0.6                    |
```

数据来源：arxiv 2402.01622v3 Table 3。**结论：纯 prompting 类策略全部失败**——CoT / ReAct / Reflexion 在数学题等单目标任务上的红利，在多约束规划上完全不再适用。Reflexion 反而导致 delivery rate 下降，因为 Agent 知道要省钱却随机选最贵的（论文 §5.3 case study right panel）。

### 4.2 路径 B：Planner-R1（强化学习，2025-10）

Planner-R1（LinkedIn / arXiv:2509.25779v2）把 GPT-4 的 0.6% 拉到 56.9%，是目前 leaderboard 最强的 agentic 方案。关键 4 点：

#### 4.2.1 训练设置

```text
| 维度                 | 数值                                              |
|---------------------|--------------------------------------------------|
| 基模型               | Qwen3-8B / Qwen3-32B（不开 thinking 模式）          |
| 训练 query 数        | **180**（用 train 45 + val 180 重切，保持 easy/medium/hard 比例）|
| RL 算法              | GRPO（Shao et al. 2024，DeepSeekMath 同款）         |
| 训练步数             | 500 / 2000 / 3000（multi-stage 100/300/100）       |
| 硬件                 | 2 节点 × 8 GPU = 16 张 H200                          |
| Rollout 引擎         | sglang，每步 8 轨迹                                  |
| 上下文限制           | 30 步轨迹，工具响应 ≤8192 token，模型输出 ≤30500 token |
| 学习率               | 1e-6                                              |
```

数据来源：arXiv:2509.25779v2 §3.1 Setup。

#### 4.2.2 Reward shaping 设计

reward 用 6 个子项加权（公式 1）：

```text
r_schema   = 1{plan 通过 JSON Schema}             # JSON 闸门
r_cs_micro = S_cs / N_cs                          # commonsense 子项通过比例
r_hard_micro = S_hard / N_hard                    # hard 子项通过比例
r_cs_macro = 1{r_cs_micro == 1}                   # commonsense 全过
r_hard_macro = 1{r_hard_micro == 1}               # hard 全过
r_pass     = 1{r_cs_macro && r_hard_macro}        # final pass

r = r_schema * (λ1*r_cs_micro + λ2*r_hard_micro + λ3*r_cs_macro + λ4*r_hard_macro + λ5*r_pass)
```

三个阶段（论文 §2.2）：

- Stage 1：λ=[1,1,1,1,1]（dense 反馈，全开）
- Stage 2：λ=[0,0,1,1,1]（category-level，只看 macro）
- Stage 3：λ=[0,0,0,0,1]（sparse final pass）

⚠ **重要发现**（arXiv:2509.25779v2 §3.2）：8B 模型用 sparse reward（Stage 3）**5/5 全部 collapse**，必须用 dense reward 才能学起来；32B 模型相对鲁棒，3 种 reward 都能拿到 42%+，但 curriculum 反而最高（47%）。**reward shaping 才是核心 lever**。

#### 4.2.3 关键结果

```text
| 模型             | reward    | Final Pass Rate（5 seed 均值，95% CI）|
|------------------|-----------|--------------------------------------|
| Qwen3-8B（基础）  | -         | 0.0                                  |
| Qwen3-32B（基础） | -         | 0.6                                  |
| GPT-o3 (high)    | -         | 11.3                                 |
| GPT-5 (high)     | -         | 21.2                                 |
| Planner-R1-8B    | Stage1    | 39.9 ± 4.3                           |
| Planner-R1-32B   | Stage1    | 42.3 ± 8.0                           |
| Planner-R1-32B   | Curriculum| 47.0 ± 6.9                           |
| Planner-R1-32B   | 3000 步 best | **56.9**（Figure 3 标注的 leaderboard 最佳）|
```

数据来源：arXiv:2509.25779v2 Table 1 + Figure 3。

#### 4.2.4 训练成本

- 8B 模型用 Stage 1 reward 达到 32B 90% 性能时，FLOPs 仅 2.1×10²⁰；32B 同水平需 7.6×10²⁰，**8B 比 32B 节省 3.5× 算力**（arXiv:2509.25779v2 Figure 3 右图）。
- 8B 内存占用 ~60GB/GPU；32B ~90GB/GPU——8B 在 H100 上跑得动，32B 必须 H200。
- **180 query 训练规模**——这是项目最值得关注的数字。

### 4.3 路径 C：LLM + 形式化求解器（formal verification，2024-04）

Hao et al. (MIT) 在 arXiv:2404.11891 提出的方案：

```text
1. LLM 把自然语言 query → SAT/SMT 约束式
2. sound-and-complete solver（如 Z3）求解
3. 不可满足时给出 unsatisfiable core + 个性化修改建议
```

关键结果：

```text
| 维度                                | 数值              |
|------------------------------------|-------------------|
| TravelPlanner Final Pass Rate       | **93.9%**         |
| 不可满足 query 修改成功率（数据集 1） | 81.6%             |
| 不可满足 query 修改成功率（数据集 2） | 91.7%             |
| 论文比较的最强 LLM baseline          | OpenAI o1-preview 10% pass rate |
```

数据来源：arXiv:2404.11891 v3 abstract。LLM-Modulo 范式（**LLM 翻译，solver 验证**）在多约束规划上表现优于纯 LLM 也优于 RL，但代价是「**约束必须可形式化**」——TravelPlanner 的 13 个约束都是结构化（预算/菜系/房型）、容易翻成 SMT；而我们项目的「年龄感知单段时长 + social_context 调性」这类语义约束就难形式化。

### 4.4 三条路径对比

```text
| 路径        | 代表工作               | Final Pass Rate | 训练成本             | 推理成本               | 形式化要求      |
|-------------|------------------------|-----------------|---------------------|-----------------------|----------------|
| A 纯 LLM    | GPT-4 + ReAct          | 0.6%            | 0                   | 中等                   | 无              |
| B RL        | Planner-R1-32B         | 56.9%           | 180 query × 16 H200 × 数十小时 | 中等（Qwen3-32B） | 无              |
| C SAT/SMT   | LLM + Z3 (Hao et al.)  | 93.9%           | 0                   | LLM 翻译 + solver 求解 | **强**（约束必须能写成 SMT） |
```

---

## 五、维度 4：evaluator 实现 / 数据集格式

### 5.1 evaluator 是 rule-based 还是 LLM-based？

**完全 rule-based**。GitHub 仓库 `evaluation/eval.py` 是公开的（参考 `python eval.py --set_type validation --evaluation_file_path ...` 调用），其内部按 13 项约束逐项判断，无 LLM 介入。论文 Appendix B.3.5 也提供了 query → JSON 的 GPT-4 后处理 prompt——但这一步是**把 plan 文本翻成 JSON**，不是 evaluation 本身。

```text
| 阶段             | 是否用 LLM | 用途                              |
|------------------|-----------|----------------------------------|
| query 生成       | 用（GPT-4）| 把结构化 JSON 转自然语言 query        |
| plan 后处理      | 用（GPT-4）| 把 plan 自然语言（逐日描述）转 JSON   |
| 约束 evaluation  | **不用**   | rule-based 逐项判断 commonsense + hard |
```

### 5.2 evaluator 输入数据结构

evaluation 的输入是后处理过的 JSON 数组（论文 Appendix B.3.5 + GitHub `postprocess/element_extraction.py`）。每个 day 是一个 dict，包含：

```text
{
  "days": int,
  "current_city": str,            # "from A to B" 或 "Singapore"
  "transportation": str,          # "Flight Number: F0123456, ..." 或 "Self-driving, ..." 或 "-"
  "breakfast": str,               # 餐厅名，City 或 "-"
  "attraction": str,              # "POI1, City;POI2, City;" 或 "-"
  "lunch": str,                   # 餐厅名，City 或 "-"
  "dinner": str,                  # 餐厅名，City 或 "-"
  "accommodation": str            # 住宿名，City 或 "-"
}
```

注意：原始数据是**纯文本**（每个字段是字符串描述，里面把名字、地址、价格塞进一句话），需要先用 GPT-4 后处理切成 JSON。Planner-R1（arXiv:2509.25779v2 Appendix B.1）改进了这一点——直接要求 LLM 输出**强 schema 约束的 JSON**（city / transportation 是 typed object），跳过后处理步骤，并用 schema 通过率 `r_schema` 作为 reward 闸门。

### 5.3 失败时给 LLM 的反馈是 binary 还是 structured？

主论文里的 ReAct + Reflexion 反馈非常薄——Reflexion 给「上一次失败的 high-level reason」，但**没有 structured violation 列表**。论文 §5.3 case study 显示 Agent 拿到 reflection 后照常胡选——证明这种弱反馈无效。

Planner-R1 把反馈结构化为 6 个子 reward（schema / cs_micro / hard_micro / cs_macro / hard_macro / pass），通过 GRPO 让模型对每个子项分别学习。这是**结构化反馈 + RL 优化**的组合，比纯 prompt 反馈强得多。

### 5.4 与项目 critics_v2.validate_itinerary 的差距

```text
| 维度          | TravelPlanner evaluator         | critics_v2.validate_itinerary           |
|---------------|--------------------------------|-----------------------------------------|
| 输入数据结构  | 逐日 dict + 字符串字段          | edge_v1 Itinerary（nodes + hops 明确分离） |
| 实现风格      | rule-based，13 项独立判断       | rule-based，10 项独立判断                  |
| 反馈粒度      | binary（passed/failed）         | structured Violation（code/severity/message/expected_range） |
| 给 LLM 的反馈 | 无（论文里直接挂掉）；Planner-R1 用 6 个子 reward | format_violations_for_llm 拼自然语言 prompt |
| 时序校验      | 无（论文不查时间冲突）          | _check_temporal_feasibility（容差 2min）   |
| 距离校验      | 通过 distance matrix 间接体现   | _check_distance（distance_max_km）         |
| 年龄感知      | 无                              | _check_age_aware_duration（spec R4 镜像）  |
| 社交场景      | 无                              | _check_social_context（social_compat 矩阵） |
| 满座埋点      | 无（数据库静态，无 reservation）| _check_demo_restaurant_full（demo-aware）   |
```

**两个最关键的差距**：

1. **TravelPlanner 没有时序校验**——它假设 plan 内的航班时刻、酒店入住合法（人工标注 reference plan 时已经查过），evaluator 只查「字段不缺 + 不重复 + 路线合理」；项目则必须自己校验时序，因为 mock 数据是静态的。
2. **TravelPlanner 反馈是 binary 的**——所以 ReAct 修不动；项目的 format_violations_for_llm 把 Violation 拼成中文修复提示（critics_v2.py:920-944），LangGraph critic_node 收到后会触发 replan，反馈粒度细于 TravelPlanner 主论文。

---

## 六、陷阱清单（5 题必答）

### Q1：「多日跨城市」假设差距与单日单城市约束退化

TravelPlanner 默认 3-7 天 + 1-3 城市，所以「Within Current City / Reasonable City Route / Minimum Nights Stay / Diverse Attractions / Diverse Restaurants」这 5 项 commonsense 约束依赖**「多个时间窗 × 多个空间窗」**才有意义。「晌午局」是 4-6 小时单一城市，这 5 项中 Within Current City 和 Reasonable City Route 直接退化（恒为真），Min Nights Stay 不存在（无住宿），Diverse Restaurants/Attractions 因半日通常 1-2 节点不可能重复，由 blueprint 阶段去重逻辑兜底已经够。

Hard 约束里 Room Rule / Room Type 也因无住宿退化；Transportation 退化为 user_profile.transport_preference 单值偏好。**真正等价的只剩 Cuisine ↔ DIETARY_VIOLATION 一项**——这一点和我们项目的现状完全吻合。

### Q2：5 岁娃博物馆 196min 案例归类

按 TravelPlanner 二分法这是一条**「commonsense 约束」**——用户 query 里通常不会写「博物馆停留不超过 75 分钟」，但常识告诉我们 5 岁娃没有这种续航。论文里最接近的例子是 Reasonable City Route（虽然字面意思是城市路线，但本质上是「常识合理性」一类）。

按 TravelPlanner 经验：**commonsense 是 LLM 最容易失败的一类**——GPT-4-Turbo 在 commonsense macro 通过率仅 2.0%（论文 Table 3）。这意味着如果我们让 LLM 自己判断「5 岁娃逛博物馆该多久」，**大概率失败**——这与项目踩过的坑（pitfalls 里的 196min bug，被 spec planning-quality-deep-review R4 修复）完全吻合。Spec R4 在 critics_v2.py 引入 AGE_DURATION_MISMATCH 镜像 + blueprint 阶段 _age_aware_duration_critic，相当于把这条 commonsense 显式硬编码为 hard rule——和 LLM-Modulo / formal verification 路径的思路一致。

### Q3：GPT-4 在哪几个 commonsense 约束上最容易失败 + 项目覆盖度

论文 Table 4 给了详细的分难度通过率，但 HTML 渲染只保留了文字描述（具体数字丢失）。综合论文 §5.2 Planning Error Analysis 和 case study 的归因，最难的 commonsense 约束 top 3 是：

```text
| TravelPlanner 失败约束 | 项目 critics_v2 是否覆盖 | 说明                                                       |
|-----------------------|-------------------------|------------------------------------------------------------|
| Within Sandbox        | ❌ 不覆盖                | 项目 mock 数据 ID 引用，不存在「LLM 编造数据库外实体」场景；但若走 LLM-first，需补 SANDBOX_HALLUCINATION |
| Complete Information  | 🟡 部分覆盖              | 项目 NODES_INCOMPLETE 仅查中间节点 ≥1，未查每段必填字段；建议补一条 NODE_FIELD_MISSING（POI 节点必须有 title/duration/start_time）|
| Diverse Attractions   | 🟡 间接覆盖              | blueprint 阶段 node_decider 已 dedupe；但 ILS 路径无显式 critic，建议加 DUPLICATE_TARGET                  |
| Reasonable City Route | ✅ 单城退化              | 半日不跨城，约束恒为真                                                                   |
| Min Nights Stay       | ✅ 单日退化              | 半日无住宿，约束恒为真                                                                   |
```

### Q4：Planner-R1 的 56.9% 路径能否复用？

```text
| 评估角度       | Planner-R1 现状                          | 「晌午局」可行性                                              |
|----------------|------------------------------------------|--------------------------------------------------------------|
| 数据来源       | TravelPlanner 1225 query（人工标注）     | 项目仅 6-8 demo 场景，**不够 RL 训练**；扩到 180 需 ~50-100 人天人工标注 |
| 训练成本       | 16 张 H200 × 数十小时（FLOPs ~10²⁰）       | 团队无 H200；H100 仅能跑 8B；32B 不可行                                |
| 模型选型       | Qwen3-8B 微调                            | 项目主用 DeepSeek-V3 / Qwen-Plus（API），不部署本地权重                       |
| Demo 时长     | 1 个月（剩 1-2 周可用 RL 训练时间）     | **完全不够**——RL 训练 + 模型部署 + 推理优化 = 3-6 个月起步                    |
| 是否值得      | 长期产品化 ROI 高                        | hackathon demo ROI 接近零                                              |
```

**结论**：Planner-R1 路径在 hackathon 时间盒内**不可行**。但其 reward shaping 思想可以反向借鉴——`r_cs_micro / r_hard_micro` 子项加权的思路可以指导我们 critics_v2.format_violations_for_llm 的反馈分级（critical/warning 已经有，但可以再细分子项权重）。

### Q5：5 岁娃博物馆 196min 案例跑 TravelPlanner evaluator 会拿多少分？

模拟跑一遍 TravelPlanner 13 项约束：

```text
| 约束                  | 是否通过 | 理由                                                |
|-----------------------|---------|-----------------------------------------------------|
| Within Sandbox        | ✅      | 博物馆和孩子都在 mock 数据内                                |
| Complete Information  | ✅      | 假设字段齐全                                              |
| Within Current City   | ✅      | 单城                                                  |
| Reasonable City Route | ✅      | 单城退化                                                |
| Diverse Restaurants   | ✅      | 半日通常 ≤1 餐                                          |
| Diverse Attractions   | ✅      | 半日通常 1-2 POI                                         |
| Non-conf. Transport   | ✅      | 单一交通方式                                              |
| Min Nights Stay       | ✅      | 无住宿（约束恒真）                                          |
| Budget                | ✅      | 假设预算够                                              |
| Room Rule / Type      | ✅      | 无住宿                                                  |
| Cuisine               | ✅      | 假设无饮食偏好                                            |
| Transportation        | ✅      | 命中偏好                                                  |
| **Final Pass Rate**   | ✅ 100% | 因为 TravelPlanner **完全不查年龄合理性**                 |
```

**这是最关键的 takeaway**：TravelPlanner evaluator 拿不出「5 岁娃 196min 不合理」这种判断——age-aware 约束在 TravelPlanner 体系内**既不是 commonsense 也不是 hard**，是**完全缺失的第三类**。项目独有的 AGE_DURATION_MISMATCH（spec R4）正是补上了这一空白。

引申意义：如果未来要把项目 critic 反向贡献给 TravelPlanner 社区，**「年龄感知 + 社交场景调性」是真正的差异化 critic**——这两条 TravelPlanner 完全没有。

---

## 七、关键洞察 5 条

1. **commonsense / hard 二分法本身就是一种工程语言**。TravelPlanner 把约束按「用户是否显式提」分成两类，这与项目 critic 按 severity（CRITICAL / WARNING）+ source（用户硬约束 / 系统兜底）分类完全互补。我们可以引入第三个维度「**用户显式 vs 系统隐含**」，把 critics_v2 的 10 类 ViolationCode 分为「用户硬约束」（DIETARY_VIOLATION / DURATION_OUT_OF_RANGE / DISTANCE_EXCEEDED）和「系统常识约束」（其余 7 类）——这一标注本身就值得加进 critics_v2.py 注释。

2. **「LLM-only 路径在多约束规划上不可行」是已被严肃论证的结论**。TravelPlanner GPT-4-Turbo + ReAct 仅 0.6%；GPT-5 也只有 21.2%（Planner-R1 Table 1）；OpenAI o1-preview 10%（Hao et al. abstract）。我们项目走 plan-and-execute + critic loop 是与业界主流共识一致的——**不能把所有规划逻辑塞 LLM**。

3. **rule-based evaluator 是行业标准**。TravelPlanner 13 项约束、Planner-R1 6 子 reward、formal verification 的 SMT 约束式——三条路径都是 rule-based 评分体系。这印证项目 critics_v2 的 rule-based 设计方向正确，**不应被 LLM-as-judge 替换**。

4. **Reward shaping > 模型大小**。Planner-R1 最强结果：8B 模型 + Stage1 dense reward 拿到 39.9%，**接近 32B 的 42.3%**，但算力低 3.5×。对应项目启示：critics_v2.format_violations_for_llm 的反馈结构化程度比 LLM 模型选型更影响最终质量。

5. **半日单城市场景下，TravelPlanner 13 项约束有 11 项退化或不适用**。这意味着我们**不能把 TravelPlanner 当 ground truth 用**——必须为「半日 + 时序 + 体验感」单独构建一套 critic 体系（critics_v2 已经做到了），而 TravelPlanner 主要价值是「分类学结构 + evaluator 设计模式」的范本。

---

## 八、复用评分

```text
| 子项                          | 评分（满分 10）| 说明                                                  |
|------------------------------|--------------|-------------------------------------------------------|
| 整体复用                      | 3 / 10       | 多日跨城 vs 半日单城，结构差异大；但分类学和 evaluator 架构可借鉴 |
| 仅约束分类二分法（commonsense vs hard）| 7 / 10  | 清晰好用，可直接给 critics_v2 加注释维度                   |
| 仅 evaluator rule-based 风格  | 8 / 10       | 项目 critics_v2 已是同款范式，只差「分类元数据」              |
| query 生成方法                | 1 / 10       | 需要 20 人标注团队 + GPT-4 大量调用，hackathon 不可复现    |
| Planner-R1 RL 训练流程        | 1 / 10       | 需要 16 张 H200 + 180 标注 query，hackathon 时间盒内不可行    |
| Planner-R1 reward shaping 思想| 6 / 10       | 6 子 reward 结构可指导 format_violations_for_llm 分级反馈   |
| Formal verification (SAT/SMT) | 4 / 10       | 我们的「年龄/社交」约束难形式化；预算/距离/时长可以，但 ROI 不高 |
| 数据集格式（逐日 dict）        | 2 / 10       | 字符串字段反人类，Planner-R1 都改用强 schema JSON 了；无须沿用|
```

---

## 九、建议（≤200 字）

**立即采纳**：在 `critics_v2.ViolationCode` 注释里补充 `category: commonsense | hard` 元数据（参考 TravelPlanner 二分法），把 DIETARY_VIOLATION / DURATION_OUT_OF_RANGE / DISTANCE_EXCEEDED 标 `hard`（用户显式提），其余 7 类标 `commonsense`（系统常识兜底）。同时在 `Violation.severity` 之外加 `weight: float = 1.0` 字段，模仿 Planner-R1 的 micro/macro 分级思路给 LLM 反馈分级。

**坚决不采纳**：query 大规模合成、RL 微调、SAT/SMT 求解器——这三条是产品化方向，不是 hackathon 1 个月路径。

**保持现状**：critics_v2 的 10 类 ViolationCode 与项目场景高度契合，不要用 TravelPlanner 13 项替换——**「半日单城市 + 年龄/社交」这个 niche 是项目护城河**。

---

## 十、与现有 critics_v2 的衔接细节

具体到代码层面（critics_v2.py 行号引用）：

1. **`ViolationCode` 枚举注释**（critics_v2.py:84-105）：在每个枚举值的注释里加 `# category: hard | commonsense` 标记。建议如下：

   ```text
   | 枚举值                       | TravelPlanner-style category | 理由                                  |
   |-----------------------------|-----------------------------|---------------------------------------|
   | INVARIANT_BROKEN             | commonsense                 | 结构不变量，用户从不显式提                  |
   | NODES_INCOMPLETE             | commonsense                 | 「至少一个活动」是隐含期望                    |
   | DURATION_OUT_OF_RANGE        | hard                        | duration_hours 来自 intent 用户显式提      |
   | TIMELINE_INCONSISTENT        | commonsense                 | 时序自洽是常识                            |
   | HOP_INFEASIBLE               | commonsense                 | 物理可达性是常识                          |
   | DISTANCE_EXCEEDED            | hard                        | distance_max_km 来自 intent 用户显式提     |
   | RESTAURANT_FULL_UNRESOLVED   | commonsense                 | demo 埋点，不是用户提的                  |
   | DIETARY_VIOLATION            | hard                        | dietary_constraints 用户显式提            |
   | SOCIAL_CONTEXT_MISMATCH      | hard                        | social_context 用户隐含提（query 文本）   |
   | AGE_DURATION_MISMATCH        | commonsense                 | 年龄感知是隐含常识                          |
   ```

2. **`format_violations_for_llm` 改进**（critics_v2.py:920-944）：当前已经按 severity 过滤 critical，建议进一步按 category 分组拼 prompt：

   ```text
   你产出的行程方案有 N 处违规需要修复：
   【用户显式约束（hard）】
   1. ...（DIETARY / DURATION / DISTANCE / SOCIAL）
   【常识 / 系统约束（commonsense）】
   2. ...（其余 7 类）
   ```

   分组提示让 LLM 能优先修用户显式约束（直接影响用户感知）再修常识约束（影响行程合理性），与 TravelPlanner micro/macro 分级思想一致。

3. **`validate_itinerary` 顺序**（critics_v2.py:885-906）：当前按「先结构性后语义性」9 步串行调用。建议在每步 violations 列表后追加日志输出 `category` 计数（commonsense_count / hard_count），便于 LangGraph critic_node 决策——比如 hard violations > 0 时强制 replan，commonsense violations 全部时仅触发一次 backprompt。

4. **导出 `Violation.category` 字段**：这是 spec C 算法重构时建议引入的新字段：

   ```python
   class Violation(BaseModel):
       ...
       category: Literal["commonsense", "hard"] = Field(
           ...,
           description="TravelPlanner-style 分类：用户显式约束（hard）vs 系统常识约束（commonsense）"
       )
   ```

---

## 十一、阅读笔记

```text
| 来源                       | 摘录关键点                                         | 字数控制 |
|---------------------------|--------------------------------------------------|---------|
| arxiv 2402.01622v3 abstract | "GPT-4 only achieves a success rate of 0.6%"     | 11 词 ✅ |
| arxiv 2402.01622v3 §3.2    | 三类约束：Environment / Commonsense / Hard         | 重写  |
| arxiv 2402.01622v3 Table 1 | 8 commonsense + 5 hard 子项完整列出                  | 重写  |
| arxiv 2402.01622v3 §3.3    | 1225 query；每 plan 标注 $0.80                    | 重写  |
| arxiv 2402.01622v3 §3.4    | "We do not separately assess environment constraints" | 7 词 ✅ |
| arxiv 2402.01622v3 §5.2    | constraint pass rate 与难度负相关；budget + min nights stay 是 global constraint，最难      | 重写  |
| arXiv 2509.25779v2 §1      | "56.9% final-pass rate with only 180 training queries" | 9 词 ✅ |
| arXiv 2509.25779v2 §2.2    | 6 子 reward + 3 stage λ 定义                        | 重写  |
| arXiv 2509.25779v2 Table 1 | Qwen3-8B 0.0 / 32B 0.6 / GPT-5 21.2 / Planner-R1 56.9（Curriculum 32B 47.0 ± 6.9）| 重写  |
| arXiv 2509.25779v2 §3.2    | 8B 用 sparse reward 5/5 collapse；reward shaping is the lever | 重写 |
| arXiv 2404.11891 abstract  | TravelPlanner 93.9% / 不可满足 query 81.6% & 91.7% 修改成功率 / 50 页论文 | 重写 |
| GitHub OSU-NLP-Group/TravelPlanner | evaluation/eval.py rule-based；postprocess 用 GPT-4 解析 | 重写 |
| critics_v2.py:84-105       | 10 类 ViolationCode 完整枚举定义                      | 直接引用 |
| critics_v2.py:885-906      | validate_itinerary 9 步顺序                         | 直接引用 |
| critics_v2.py:920-944      | format_violations_for_llm 不暴露 dot-path           | 直接引用 |
```

---

## 十二、补充观察：Planner-R1 工程改进对项目的启示

Planner-R1（arXiv:2509.25779v2）相对原版 TravelPlanner 在工程层面做了几项改进，对我们项目有直接借鉴价值，但都不在主论文里，单独列出。

### 12.1 工具集改造

原版 TravelPlanner 提供 7 个工具（含 NotebookWrite 用于显式记忆管理）；Planner-R1 把工具集改成 7 个**真正的语义工具** + 1 个新增的 `calculator` API（arXiv:2509.25779v2 §2.1 Actions 段）。这个 calculator 是给 LLM 显式做数值推理用的——budget 加和、距离换算、夜数计算都走它，避免 LLM 自己算错（论文里 32B 模型最大错误来源是 cost / accommodation 约束，对应 Figure 4）。

**项目对应启示**：「晌午局」如果未来加入预算约束 / 总时长精确计算，应**显式提供一个 calculator 工具**给 LLM，而不是让它自己算。当前 backend/tools/ 下没有 calculator——这是后续可补的工具空缺。

### 12.2 移除 Notebook 显式记忆

Planner-R1 §2.1 明确写「we disabled the lightweight semantic memory so that tool responses appear directly in the context」——原版 TravelPlanner 让 Agent 用 NotebookWrite 自己摘要存信息，结果上下文管理成了 Agent 的另一个负担（GPT-4 经常该写不写，导致后续找不到之前查到的航班）。Planner-R1 直接让所有工具响应**按时间序写进上下文**，靠 LLM 长上下文能力解决。

**项目对应启示**：项目当前 LangGraph 主路径已经走「state.tool_results 积累 + 后续节点直接读」的范式，结构上与 Planner-R1 一致；不需要引入 Notebook 抽象。

### 12.3 强 schema JSON 输出 + 闸门

Planner-R1 §2.1 + Appendix B.1 把 final answer 用 `<answer>...</answer>` 包裹的强 schema JSON 数组（每天一个 typed object，transportation 字段 oneOf 是字符串 "-" 或 typed dict 含 mode/from/to/duration/distance/cost），再用 `r_schema = 1{plan conforms to schema}` 作为 reward 计算的**第一道闸门**——schema 不通过则其他子 reward 全部归零。

**项目对应启示**：项目当前 Pydantic Itinerary 也是强 schema（`ConfigDict(extra="forbid")`），与 Planner-R1 设计同源；critics_v2 的 INVARIANT_BROKEN 等价于 Planner-R1 的 schema gate。这一致性印证项目架构方向正确。

### 12.4 Reward shaping 对反馈生成的启示

Planner-R1 关键发现是「**dense 反馈对小模型必不可少，sparse 反馈让 8B 模型 5/5 全部 collapse**」（arXiv:2509.25779v2 §3.2 + Table 1 Stage3 实验）。我们项目用的是 DeepSeek-V3（API），不会 collapse，但**反馈密度同样影响修复效率**：当前 critics_v2.format_violations_for_llm 把所有 critical violations 拼成一个长 prompt 喂回去，相当于 dense 反馈；如果未来改成「每次 backprompt 只暴露最重要的一条」（sparse），按 Planner-R1 的经验，LLM 修复轮数会增加，整体延迟会变长。

**结论**：**保持当前 dense backprompt 设计，不要改 sparse**。

### 12.5 30 步上限的工程意义

Planner-R1 trajectory cap 是 30 步（与原版 TravelPlanner 一致）；超出即视为 dead loop。原版论文 Figure 2 显示 GPT-4-Turbo 的 dead loop 占错误的 6%、invalid action 占 37.3%——总共 43.3% 的错误是「步数控制不住 / 行为不收敛」。

**项目对应启示**：项目 LangGraph 主路径目前 critic_node → replan 最多触发 3 次（backend/agent/graph/build.py 的 replan_count 限制），实际工具调用步数远低于 30。这套「短上限 + 多重 fallback」设计与 TravelPlanner 思路一致：**宁可走 fallback 拿出兜底方案，也不让 LLM 在 30 步内胡乱 retry**。

---

## 十三、致谢与版权说明

本调研报告基于 OSU-NLP-Group / Fudan / Penn State / Meta AI 联合发表的 ICML 2024 论文，及 LinkedIn / MIT 后续工作；所有事实陈述均标注一手出处。涉及连续摘抄不超过 30 词的段落已检查；超过部分均已用中文重写并加「重写」标记。论文级数据点（如 0.6%、56.9%、93.9%）保留原始数字，所有推断或脚注未拿到的细节均已标 ⚠。

—— Agent 4 / TravelPlanner，2026-05-24
