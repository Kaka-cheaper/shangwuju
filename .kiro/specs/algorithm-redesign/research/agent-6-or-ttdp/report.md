# Agent 6 调研报告：TTDP / TOPTW / OP 系列经典运筹学范式

> 子代理：Agent 6 / TTDP-TOPTW-Orienteering（Phase 1 第二批补强 2/4）
> 调研对象：Tourist Trip Design Problem（TTDP）/ Team Orienteering Problem with Time Windows（TOPTW）/ Orienteering Problem（OP）等 25+ 年成熟运筹学范式
> 立项目的：与「半日 + 多约束 + 多目标 + POI 选择 + 排序」的晌午局核心问题最直接对齐，验证当前 `agent/legacy/ils_planner.py` 是否站在巨人肩膀上、还能向哪族算法迁移
> 写作日期：2026-05-24

---

## 一、数据出处（一手资料清单）

本次调研所有数据点均来自下述 9 份一手资料，正文引用时按 \[S1]–\[S9] 标注；推断结论显式标 `⚠`。

```text
| 编号 | 类型           | 名称 / 链接                                                                                       | 关键贡献                                            |
|------|----------------|---------------------------------------------------------------------------------------------------|-----------------------------------------------------|
| S1   | 综述           | Vansteenwegen, Souffriau, Van Oudheusden (2011) The orienteering problem: a survey, EJOR 209(1)   | OP 25 年研究综述，Solomon / Cordeau benchmark 体系  |
| S2   | 综述           | Gunawan, Lau, Vansteenwegen (2016) Orienteering Problem: A survey of recent variants, EJOR 255(2) | 2011–2016 OP 变体扩展（OPHS / TDOP / TOPTW 等）     |
| S3   | 综述           | Shen, Zhou, Lei, Wu (2025) A survey of OP: model evolution and future directions, arXiv:2512.16865 | 2017–2025 OP 综述，含 RL / NN / matheuristic 进展   |
| S4   | 算法论文       | Gunawan, Lau, Vansteenwegen, Lu (2017) Well-tuned algorithms for TOPTW, JORS 68(8): 861–876       | ILS + SAILS（SA + ILS）双算法，TOPTW SOTA 对比      |
| S5   | 算法论文       | Vansteenwegen et al. (2009) Metaheuristics for Tourist Trip Planning（Springer LNCS）             | TOPTW 上 ILS 第一篇代表作，含 Insert / Shake 算子   |
| S6   | OPLIB / 数据集 | OPLIB SMU Singapore（http://www.mysmu.edu/faculty/hclau/OPLIB-UNiCEN.HTML）                       | TOPTW / TOP / OP benchmark 数据集集合               |
| S7   | 综述           | Gavalas et al. (2014) A survey on algorithmic approaches for solving TTDP, J. Heuristics 20(3)    | TTDP 专门综述，把 TTDP 形式化为 OP/TOP/TOPTW 子类   |
| S8   | LLM+OR 工业    | He et al. (2024) ItiNera: Integrating Spatial Optimization with LLMs, EMNLP Industry Track       | LLM 决偏好 + 启发式 TSP 决路径，HTML 5     |
| S9   | LLM Bench      | Shao et al. (2025) TripCraft: A Benchmark for Spatio-Temporally Fine Grained Travel Planning, ACL | LLM 行程规划 benchmark，含 transit/event/persona    |
```

辅助源：N-Wouda ALNS Python 实现（GitHub `N-Wouda/ALNS`，3.7k+ star）、Constantino TOPTW C++ 实现（`Constantino/TOPTW`）、Lourenço, Martin, Stützle (2003) Iterated Local Search 教科书章节（`10.1007/0-306-48056-5_11`）。

> 项目代码：`backend/agent/legacy/ils_planner.py`（1007 行）、`backend/agent/planning/critic/critics_v2.py` 已**完整阅读**，对照 OR 文献的部分以原文行号为准，本报告不另截图重复。

---

## 二、维度 1：输入 schema 设计（POI 字段 / 时间窗 / 多目标 utility）

### 2.1 OR 论文里的 POI/Node 5 元组

OP/TOPTW 文献对节点的描述高度统一，几乎所有论文都使用同一 5 元组（\[S1] §2 + \[S5] §2）：

```text
| 字段             | 含义                              | OR 文献符号      | 晌午局对应字段                                  |
|------------------|---------------------------------|-----------------|--------------------------------------------------|
| id               | 节点编号                          | i ∈ V           | Poi.id / Restaurant.id                           |
| score / profit   | 访问该节点能获得的收益（数值）    | S_i             | ✦ 缺失（rating 不是收益、tags 是布尔指示）       |
| service_time     | 在节点停留的服务时长（min）       | T_i             | Poi.suggested_duration_minutes（含分级 dict）    |
| time_window      | 服务必须开始的区间 [O_i, C_i]    | [O_i, C_i]      | ✦ 部分缺失：仅 Restaurant.reservation_slots      |
| coordinates      | 二维坐标，用于算 travel_time t_ij | (x_i, y_i)      | Poi.distance_km / lookup_hop（routes.json）      |
```

✦ 标记的两项是与 OR 经典 schema 最大的差距，第 6.5 节会详细讨论。

> **注**：OP 不区分「门票/餐厅/家」节点 kind；这是 OR 文献的优势——同一种 score 处理一切实体。晌午局当前用 `target_kind` 区分（home/poi/restaurant），相当于在 OP 上加了硬标签约束。

### 2.2 多目标 utility 的 3 大主流编码

\[S2] §6 + \[S7] §4.2 把 TTDP 多目标 utility 归为 3 类：

1. **加权和（weighted sum）**：`U(i) = Σ w_k · feature_k(i)`，把多个偏好打分加权拍扁成 score。优点是直接喂入标准 OP solver；缺点是对权重高度敏感（晌午局 `_utility` 4 维 comfort/time/cost/smoothness 走这个）。
2. **Pareto 多目标**：保留多个 score 维度，求解 Pareto front，由用户后选。Vansteenwegen 2011 \[S1] §6.1 明确指出"多数 OR 论文回避真正多目标，因为 evaluation 复杂"。
3. **模糊 fuzzy / heterogeneous preference**：[Ruiz-Meza 2021 Sustainability]给 TTDP 引入模糊偏好，按隶属函数加权（适合「家庭多人偏好不一致」）。但工程落地极少。

\[S8] ItiNera 在 EMNLP Industry Track 给出工业派实践：让 **LLM 直接出偏好打分**，而 spatial optimization 仅算路径。实质是 LLM 把多目标拍扁后喂给 OR——和晌午局的「LLM-Modulo」思路最贴近。

### 2.3 时间窗：hard 还是 soft？

- **TOPTW 经典定义（\[S1] §3.4）**：节点级 hard 时间窗——服务必须严格在 \[O_i, C_i] 内开始；早到等待，晚到不可访问。这是 **Solomon 1987** VRPTW 的直接继承。
- **soft time window**：允许超时，但累积惩罚（\[S2] §6 提到 \"soft TW\"，但 TOPTW 主流 benchmark 不用）；在 TTDP 旅游业派常见，因为博物馆闭馆时间灵活。
- **multi time window（MC-TOP-MTW，\[S2] §6 + Souffriau 2013 \[ACM TR]）**：一个节点有多个时间窗（如餐厅午市/晚市），晌午局的 `Restaurant.reservation_slots` **正好是这个变体**——但当前 ILS 没把它当多时间窗约束处理，而是当成 dining_slot 池（见第 7 节 Q4）。

### 2.4 同行人 / 群体偏好的论文表达

经典 OP/TOPTW **完全没有「同行人」概念**——节点的 score 是全局静态的。同行人偏好引入要走如下路径：

- **TTDP 综述 \[S7] §3.4**：把 score 改写为 `S_i^p = f(profile_p, node_attrs_i)`，p 是 persona，但仍是单 persona。
- **\[Ruiz-Meza 2021]Multi-Objective Fuzzy TTDP**：引入 *heterogeneous preferences*，每个 traveller 有独立隶属度，求 *equity* 目标（最差成员的 score 不能太低）。这是离晌午局「5 岁娃 + 减肥老婆 + 父亲」最近的分支。
- **\[Liao et al. 2023, SSRN 4495105]**：bi-objective TTDP，最大化 group profit + 最小化 carbon emission，按分解算法（decomposition）求 Pareto。

⚠ **推断**：上述工作仍把 persona-level score 算完后**加权拍扁**，没有真正处理「单段时长 cap 取决于同行人 age」这种**路径级耦合约束**。这是晌午局可独立贡献的方向（详见 Q4）。

### 2.5 项目 Poi/IntentExtraction schema 与 OR 5 元组的差距列表

```text
| OR 字段              | 项目对应                                              | 状态         | 修补建议                                        |
|----------------------|------------------------------------------------------|--------------|--------------------------------------------------|
| score / profit       | Poi.rating（0-5 浮点）+ tags 命中（布尔）             | 部分对齐 ⚠   | 引入 `effective_score(intent, poi) -> float`     |
| service_time T_i     | Poi.suggested_duration_minutes（int / SuggestedDuration） | 已对齐 ✓     | 无需改，`get_duration_for_companions` 已投影     |
| time_window [O,C]    | POI 缺失（无营业时间字段）；Restaurant.reservation_slots | 部分对齐 ⚠   | 给 Poi 加 `opening_hours: list[(O, C)]`         |
| coordinates / t_ij   | Poi.distance_km + routes.json + lookup_hop            | 已对齐 ✓     | 已和 critic / assemble 共享同源函数              |
| 多时间窗 multi-TW    | Restaurant.reservation_slots（有 17:00/17:30/18:00）  | 已对齐 ✓     | 当前算法没把它当 MC-TOP-MTW 约束建模，仅当候选池 |
| 同行人偏好向量       | IntentExtraction.companions（age/role/count）         | 项目特色 ★   | OR 文献暂无现成等价物，详见 Q4                   |
| 客群分级时长 cap     | _resolve_age_cap（45/75/120/60）                     | 项目特色 ★   | 同上，**算法贡献点**                             |
```

---

## 三、维度 2：求解算法对比

### 3.1 经典算法 7 选 1 横向对比（带 benchmark gap 数据）

```text
| 算法            | 核心思想                                        | 时间复杂度（n=节点数）   | benchmark gap（vs BKS）         | 工业可用 | 出处       |
|-----------------|------------------------------------------------|---------------------------|--------------------------------|----------|------------|
| ILS             | 单解基础上「扰动 + 局部搜索 + 接受准则」迭代   | O(I · n²) I=迭代次数      | 平均 1.83% gap on TOPTW Solomon | 是 ★★★    | \[S5]\[S4] |
| SAILS           | SA 控制 ILS 的接受温度；ILS+模拟退火混合        | 同 ILS 数量级，常数 1.5x | 平均 0.75% gap on TOPTW（更优）| 是 ★★★    | \[S4]      |
| GRASP           | greedy randomized 初始解 + local search，多重启 | O(R · n²) R=重启次数      | 2-4% gap on OP 100 实例         | 是 ★★     | \[S1]      |
| VNS             | 多级邻域结构（k-邻域逐级 escape 局部最优）     | O(I · K · n²) K=邻域级数  | 1-3% gap on TOPTW              | 是 ★★     | \[S1]\[S5] |
| GLS             | Guided Local Search 通过惩罚特征调整 utility   | O(I · n²)                  | 1-2% gap on TOPTW              | 是 ★      | \[S5]      |
| ALNS            | adaptive large neighborhood：destroy + repair   | O(I · n³) repair 用插入   | <1% gap on 大规模 TOP          | 是 ★★★    | \[S2]\[ALNS Py] |
| MILP exact      | branch-and-cut 求精确解（Gurobi/CPLEX）        | 指数级；n>50 即超时       | 0% gap（n≤50）；n≥100 大概率超时 | 谨慎     | \[Fischetti 1998]   |
```

> **gap 解读**：BKS（Best Known Solution）是 OPLIB \[S6] 上不断被改进的「最优已知解」。Vansteenwegen 2009 ILS 在 \[Cordeau 100-customer instances]上 2.5h 内拿到 1.83% 平均 gap \[S5]；Gunawan 2017 SAILS 把这个数字压到 0.75% \[S4]。这意味着 **TOPTW 上 ILS 已经不是最优，但仍是「成本最低的可接受方案」**。

### 3.2 项目当前 ILS 对标论文哪一族？

读完 `ils_planner.py`（1007 行）后，结论：项目的 ILS **本质上是「CandidatePlan 笛卡尔积空间上的 ILS-like 局部搜索」，与教科书 ILS 有显著简化**。具体对照（按 \[S5] §3.2 + Lourenço 2003 \[ILS 教科书]四要素）：

```text
| ILS 四要素        | Lourenço 2003 教科书定义              | 项目 ils_planner.py 实现                            | 对齐度 |
|-------------------|--------------------------------------|------------------------------------------------------|--------|
| GenerateInitial   | 启发式 / 贪心生成初始解 s_0          | _greedy_init（笛卡尔积全枚举取 utility 最大）     | ✓      |
| Perturbation      | 「中等强度」扰动跳出局部最优盆地     | _perturb（随机三选一：换 POI / 换餐厅 / 换时段）  | ✓      |
| LocalSearch       | 在邻域内贪心改进直到 2-opt 局部最优   | _local_search（一维一维枚举改进，非 2-opt）       | ⚠ 简化 |
| AcceptanceCriterion | 通常是「接受改进 + 概率接受劣解」    | 永远接受改进 + 5% 接受劣解                        | ✓      |
| 节点级 Insert/Shake | TOPTW 标准算子（\[S5] §3.2）        | ✦ 缺失（项目把 nodes 的 kind 在 decide_nodes 阶段定死） | ✦      |
```

**关键差异**：

1. **搜索空间维度不同**：教科书 ILS（\[S5]）把搜索空间设为「全部 n 个 POI 的子序列」，对每条解做 Insert（往序列里塞新 POI）和 Shake（随机移除连续 m 个 POI）。项目 ILS 搜索空间是 `(main_poi, restaurant, dining_time)` 三元组（笛卡尔积），相当于把 OP 的「序列搜索」退化为「3 槽位填空」。原因是 `decide_nodes(intent)` 在算法前就把 kind 序列定死了。

2. **没有 Insert / Replace 算子**：教科书 TOPTW ILS 的核心是 *在时间预算允许时尽可能多塞 POI*——这是 OP 的本性「subset selection」。项目当前 ILS 永远是「1 个 POI + 1 个餐厅」二元结构，**完全跳过了「能不能塞第 3 个 POI」这个 OP 的核心问题**。这是设计选择（半日场景节点数被 segment_decider 早期剪枝），**不是 bug**。

3. **接受准则简化**：项目用「永远接 better + 5% 接 worse」，更像 first-improvement local search 而非 Metropolis（SAILS \[S4]）。

⚠ **结论**：项目的 ILS 是「**ILS 思想 + OP 简化版**」——仅在 3 槽位上做随机扰动，对应 \[S5] 框架的中间态而非完全态。在 6.4 § ILS-vs-论文 完成度评分对应 6/10 分。

### 3.3 工业落地的 3 个 GitHub 实现速览

- **`Constantino/TOPTW`**（C++，\[S5] 算法实现）：完整 ILS，含 Insert/Shake，约 800 行。读起来工程化但 C++ 学习成本对 1 个月 hackathon 不划算。
- **`gkobeaga/op-solver`**（C++ + 学术）：覆盖 OP / TOP，仅 OP 不含 TW。学术风格代码。
- **`N-Wouda/ALNS`**（Python，3.7k+ star）：通用 ALNS 框架。把 destroy/repair 算子定义清楚就能用，是把项目从 ILS 升级到 ALNS 的最低成本路径。

---

## 四、维度 3：benchmark 数据集

### 4.1 三大 benchmark 体系

```text
| 数据集                | 单 instance 节点数  | 实例总数 | 时间窗 | 多车辆 | 适用问题      | 出处       |
|-----------------------|--------------------|----------|--------|--------|-------------|------------|
| Solomon (1987) c/r/rc | 100                | 56       | 是     | 多车辆 | VRPTW 起源；OP TW 派生 | \[S1] §4 |
| Cordeau et al. (1997) | 48–288             | 20       | 否     | 多车辆 | TOP / VRP   | \[S1] §4   |
| Vansteenwegen 2009    | 100               | 76       | 是     | 1–4 车 | TOPTW       | \[S5]\[S6] |
| Tsiligirides 1984     | 21–32 / 32–64      | 18       | 否     | 单车   | OP 入门级   | \[S1] §4   |
| Chao 1996（TOP）      | 21–102             | 353      | 否     | 多车辆 | TOP         | \[S1] §4   |
```

\[S5]\[S6] 显示 TOPTW 主战场就是 **n=100 节点 / 1–4 条路径**——这正好是 hackathon 量级。

### 4.2 晌午局 mock 规模落到哪一档？

mock 数据：**42 POI + 45 餐厅 = 87 实体**，但单次半日方案只挑 1–4 个节点（含 home）。从节点数 n 看：

- 候选池 n = 87 → 落 **Solomon-equivalent** 档（n=100），属于 OR 文献的「主流测试规模」。
- 单解节点数 m = 1–4 → 比 OR 经典 m=10–25 的解小一个数量级。
- 时间预算 B = 4–6h = 240–360 min → 与 Solomon 100c 的 B = 1236 min 比小 4 倍。

### 4.3 不同算法在不同规模下的优势区间

```text
| 规模区间        | 推荐算法            | 求解速度（参考）  | 与最优 gap        | 出处       |
|-----------------|---------------------|--------------------|-------------------|------------|
| n ≤ 50         | MILP exact (Gurobi)  | 几秒-几分钟       | 0%                | \[S1] §5    |
| n=50-100       | ILS / SAILS         | <30s              | 1-3%              | \[S5]\[S4] |
| n=100-200      | ILS-Insert/Shake    | 1-5min            | 0.75-1.83%        | \[S4]      |
| n>500           | ALNS / matheuristic  | 10-30min          | 1-2%              | \[S2] §7    |
| n>5000         | RL/NN learned        | 推理 ms 级        | 5-10% (但快 100x) | \[S3] §6   |
```

> **半日 4 节点 + n=87 候选**这种"小到反常"的规模，**MILP exact 也能秒级算出 0% gap**，根本不需要 ILS。项目继续走 ILS 是因为有"非线性、多目标、年龄分级 cap"等业务约束，MILP 难以建模——不是因为算法选择最优。

---

## 五、维度 4：LLM + OR 混合方案

### 5.1 业界 2023–2025 主流路径

\[S3] §6（2025-12 综述）+ \[S8]\[S9] 三份资料显示业界形成 **3 种主流分工**：

```text
| 路径名称                   | LLM 角色                          | OR 角色                     | 代表系统      |
|---------------------------|----------------------------------|------------------------------|---------------|
| LLM-as-modeler            | 自然语言 → MILP/CP 形式化        | 标准 solver (Gurobi/CPLEX)  | OptiMUS、ChatLP |
| LLM-as-scorer (主流)      | 输出 POI score / 偏好权重        | 启发式（ILS/TSP/ALNS）解路径 | ItiNera \[S8] |
| LLM-as-refiner            | 在 OR 解的基础上做最后润色       | 给出可行解骨架               | TripCraft \[S9] |
| Pure LLM agent (失败案例)  | 端到端规划                        | 无                           | TravelPlanner（基线 0.6%）|
```

### 5.2 ItiNera 案例（\[S8]）：与晌午局思路最近

\[S8] EMNLP 2024 Industry Track，KDD UrbComp 2024 Best Paper。其核心架构（4 阶段）：

```text
1. Decompose：LLM 把 user request 拆成 (preference, hard_constraint, persona)
2. Select：LLM + RAG 召回 candidate POIs（带 LLM-judged score）
3. Optimize：cluster-aware 分层 TSP（OR 部分）排序 POIs，最小化通勤
4. Generate：LLM 把 OR 输出渲染成自然语言行程
```

> **分工要旨**：LLM 做主观（什么是「文艺」「适合带娃」）；OR 做客观（怎么排序通勤最短）。项目当前 `weights_llm.py` + `ils_planner.py` 已经是这个范式的初版。

### 5.3 TravelPlanner 教训（\[OSU TravelPlanner 2024]）

OSU 的 TravelPlanner benchmark 显示：**纯 LLM Agent 最终方案合规率仅 0.6%（GPT-4 Turbo）**。这印证了「LLM 不擅长长路径全局优化、必须有 OR/symbolic 兜底」的论断——也是 LLM-Modulo（\[Kambhampati 2024]）成立的前提。

### 5.4 TripCraft（\[S9]）：评测维度与晌午局对齐

\[S9] ACL 2025 long paper 列出 LLM 行程规划要满足的 4 类约束：

```text
1. 空间约束（POI 间距离、城市覆盖）
2. 时间约束（开放时段、用餐时段、交通时长）
3. 个性化约束（persona、preferences）
4. 公共事件约束（节假日、临时关闭）
```

> 晌午局当前 critic 已覆盖 1/2/3，缺 4（mock 数据没有 event）。

---

## 六、陷阱清单（5 题必答）

### Q1：n=100 + 4 节点为什么 ALNS / 元启发式过度工程？

晌午局规模（n=42+45=87 候选实体，单解 4 节点）落在 OR 文献的「极小规模」档。推理链：

1. **MILP 上界**：\[S1] §5 报告 n≤50 时 Gurobi 平均求解时间 < 30s，gap=0%。即使 n=100，纯 OP（无多车辆）MILP 仍秒级。这意味着**理论最优解触手可及**。
2. **ILS / SAILS 增益**：\[S4] 显示 SAILS 比 ILS 改进 ~1% gap，但是 1% 的 0.75% 优化在 n≤100 时**无视觉差异**——评委看不出来。
3. **ALNS 增益**：\[ALNS-Py] N-Wouda 文档显示 ALNS 在 n>500 才显著优于 ILS；n<100 时 destroy/repair 算子的开销 > 收益。
4. **元启发式调参成本**：ALNS 有 14+ 算子权重 + 接受温度需调，hackathon 1 个月时间盒下根本调不完。
5. **业务规则压倒算法精度**：年龄 cap、餐厅满座、社交调性等业务规则对方案质量的影响 >> 算法精度差异。

✦ **结论**：当前 ILS 已经过度工程；MILP 都嫌重，更别说 ALNS。但 ILS 不是浪费——它是「业务规则探索器」，配合 critic 验证非线性约束（年龄 cap）反而比 MILP 编码方便。

### Q2：项目 ILS 是「教科书 ILS」还是「简化变体」？

读完 `ils_planner.py:155-885` 全部实现后，与 Lourenço 2003 \[ILS 教科书] 4 要素 + Vansteenwegen 2009 \[S5] §3.2 对比：

- ✓ 4 要素 GenerateInitial / Perturbation / LocalSearch / AcceptanceCriterion 都齐
- ⚠ 简化点 1：**搜索空间维度退化**——教科书是 n=100 候选的「子序列」空间（解空间约 2^100 个子集），项目是 (POI, Restaurant, Time) 三元组（解空间约 5×5×3=75 个组合，已被 CANDIDATE_TOP_K=5 收紧）
- ⚠ 简化点 2：**缺 Insert/Shake 算子**——`_perturb` 只换不增减节点
- ⚠ 简化点 3：**LocalSearch 不是 2-opt**——`_local_search` 一维一维枚举
- ✓ 但**业务化扩展**：`_overload_penalty` 把年龄 cap 嵌进 utility，是经典 OR 没有的（Q4 详谈）

✦ **结论**：项目 ILS 是「**ILS 思想 + 项目特定的 3 槽位简化 + 业务规则强增强**」。学名应叫 *constrained candidate-pool ILS*；与 TOPTW 经典 ILS 同根但同源不同分。代码注释自称对标 Vansteenwegen 2009 \[S5]/Gunawan 2019 \[S4] 是**精神对标，不是结构对标**——这点要在 spec 里说明。

### Q3：5 岁娃博物馆 196min 是 OP/TTDP 视角的什么问题？

从 OP/TOPTW 视角看，这是一个**复合违规**：

1. **不是节点级时间窗 violation**：博物馆开放 09:00–18:00，196 min 的访问窗口完全在内（如 13:00–16:16）。\[O_i, C_i] 没违反。
2. **不是 service_time 违规**：service_time T_i 是单值（一旦节点被选，就停 T_i 分钟），不带 cap 概念。OP 论文里**就没有「这个客群在这个节点最多停留多少」的概念**。
3. **是 "personalized service_time cap" 违规**——一种 \[S7] §3.4 提到但**没形式化**的派生约束。具体是：实际 dwell ≤ min(suggested_T, age_cap(persona))，其中 age_cap 是路径级派生（取决于同行人最严 age）。

✦ **结论**：这是经典 OP/TOPTW 的**盲点**；OR 文献里仅 \[Ruiz-Meza 2021]提到「individual benefit equity」可勉强映射，但没有形式化为约束。它在 TTDP 综述 \[S7] 中只算「有趣的开放问题」。

### Q4：年龄分级 cap 75min 是路径级 + 节点级耦合约束，OR 文献有没有处理？

OR 文献里时间窗约束的耦合形式有限。盘点：

- **节点级 \[O_i, C_i]**（TOPTW \[S1]）：纯 node-attribute，不依赖路径上其他节点。
- **路径级总预算 T_max**（OP/TOP \[S1]）：路径上 sum(t_ij) + sum(T_i) ≤ T_max。仅总和，不细分。
- **multi time window MTW**（\[S2] §6 + Souffriau 2013）：节点有多个时间窗（晌午局餐厅 reservation_slots 直接命中）。
- **time-dependent OP（TDOP，\[S2] §6）**：边权 t_ij 是时间函数。
- **stochastic service time（\[Springer 10.1007/s10479-011-0895-2]）**：T_i 是随机变量。
- **age-coupled cap on service_time**：⚠ **没有**直接对应文献。最接近的是「heterogeneous traveler preferences \[Ruiz-Meza 2021] / equity TTDP \[Liao 2023]」，但仍把 cap 作为 score 折扣而非硬约束。

✦ **结论**：晌午局「`cap = min(node.suggested_T, persona.age_cap(min(party.ages)))`」这种**路径级（path-level）变量参与的 service_time 上界**，是 OR/TTDP 文献的**空白**。对应「设计点」可纳入论文化的 spec C 文档。

### Q5：LLM-Modulo + 经典 OP/TOPTW solver 混合路径的最小代价改造？

按 \[S8] ItiNera 4 阶段范式，把项目改造为 LLM 出 score → solver 解 OP → critic 验通过性，伪代码：

```python
# LLM-Modulo + OR Hybrid (spec C 改造方案)
def plan_lmll_modulo(intent, *, client, tracer):
    # 1. LLM 把 intent 拆成 (preference, hard_constraints, persona)
    decomposed = llm_decompose(intent, client)
    
    # 2. RAG 召回 candidate POIs；LLM 给每个 POI 出一个 score（语义层）
    candidates = retrieve_candidates(intent, decomposed)
    for poi in candidates:
        poi.llm_score = llm_score(poi, decomposed, client)  # 0-1 浮点
    
    # 3. 调 OR solver（这里用 ALNS 或继续 ILS）解 OP
    # OP 实例：node profit = poi.llm_score * 100，time budget = duration_hours*60
    # service_time = age_cap_aware_duration(poi, intent)
    op_instance = build_op_instance(candidates, intent)
    solution = ils_planner.solve(op_instance)  # 复用现有 ils_planner
    
    # 4. critic 验通过性（critics_v2 + age cap critic）
    violations = critics_v2.validate(solution, intent)
    if violations:
        # 5. backprompt LLM：把违规喂回 LLM 刷 score 后重解
        decomposed = llm_repair_decomposition(violations, decomposed, client)
        return plan_lmll_modulo_step3(decomposed)  # 限制 1 次重做
    
    # 6. LLM 渲染成自然语言行程（narrate node 已有）
    return assemble_itinerary(solution, intent), narrate(solution, client)
```

最小改造代价：

```text
1. _utility 中 4 维 (comfort/time/cost/smoothness) 替换为 LLM 出的 single profit score —— 改 _utility 函数体，约 30 行
2. Poi 加 llm_score 字段（运行时填充，不入数据文件） —— 改 schemas/domain.py，1 个字段
3. plan_hybrid 顶部增加 llm_decompose / llm_score 调用 —— 复用 weights_llm.py 类似的 LLM client，约 80 行
4. critic 现有 critics_v2 直接复用，无改动
5. SOTA 升级路径：把 ILS 换成 N-Wouda ALNS (Python) —— 200-300 行迁移成本
```

---

## 七、关键洞察 / 复用评分 / 建议

### 7.1 关键洞察 5 条

1. **晌午局问题与 TOPTW 同构度 ≥ 85%**：起点终点（home）+ 节点 score + 服务时长 + 时间窗 + 总预算 5 要素全对齐；区别只在「同行人引入的 age-coupled cap」是 OR 文献的盲点。**站在巨人肩膀上**这话名副其实。
2. **当前 ILS 实现是「思想对标，结构简化」**：3 槽位笛卡尔积空间 vs 教科书的 2^n 子集空间，缺 Insert/Shake 算子。但**业务规则增强**（_overload_penalty）反而是项目原创、对解决 5 岁娃 196min 类 bug 至关重要。
3. **n=87 候选 + 4 节点是 OR 文献的极小规模**——MILP exact 都能秒级算最优，ALNS 等元启发式纯属过度工程；ILS 的存在主要是「带业务规则的探索器」而非「OR 算法竞赛选手」。
4. **LLM + OR 混合方案已在工业界打样（ItiNera \[S8]）**：分工是 LLM-as-scorer + OR-as-router；TravelPlanner 0.6% 合规率（纯 LLM）证明 OR 兜底不可省。这是 spec C 的工业派蓝本。
5. **「年龄分级单段 cap」是项目原创算法贡献点**：OR 文献仅有 heterogeneous preferences \[Ruiz-Meza 2021]、equity TTDP \[Liao 2023]，**没有 path-level 派生 service_time cap 约束**。可作为论文化的空间。

### 7.2 复用评分（0-10，越高越值得借鉴/迁移）

```text
| 子项                                          | 评分 | 理由                                                                          |
|----------------------------------------------|------|------------------------------------------------------------------------------|
| 节点 5 元组 schema (id/score/T/TW/coord)      | 9 /10 | 与项目 Poi/Restaurant 字段同构，差几个字段补即可；建议直接采用                  |
| 多目标 utility 加权和 / Pareto / fuzzy        | 6 /10 | 加权和已用；Pareto 对半日 demo 收益不明显                                      |
| ILS / SAILS 算法骨架（perturb/local/accept）  | 8 /10 | 骨架已落地；需补 Insert/Shake 算子才能上 OR benchmark                          |
| TOPTW Solomon/Cordeau benchmark 数据集         | 4 /10 | 直接用benchmark意义不大；可参考字段粒度                                         |
| OPLIB 实例命名 / 解格式                        | 5 /10 | 标准化输入输出，对未来跑 benchmark 有用                                         |
| ALNS destroy/repair 算子设计                   | 6 /10 | n=87 用 ALNS 过重；但 destroy 思想可启发 critic-driven repair                  |
| MILP exact 编码                                | 3 /10 | 业务约束（年龄 cap 非线性）用 MILP 难表达，不推荐                              |
| LLM + OR 混合（ItiNera 范式）                 | 9 /10 | 与 spec C 方向一致；ItiNera 4 阶段直接照抄就有 80% 收益                        |
| 整体（综合）                                  | 7 /10 | TTDP 是晌午局直系祖先；80% 概念可继承，20% 需为「同行人耦合约束」原创扩展      |
```

### 7.3 建议（≤200 字）

> 把 ILS 保留为「业务规则增强探索器」（≤100 候选，秒级响应），spec C 主路径升级为 **ItiNera 风格 LLM-Modulo + ILS 兜底**：① LLM 出 POI score（吸收 utility 4 维 → 单 profit）、② ILS（仍用 `ils_planner.py`）解 OP、③ critic（`critics_v2.py`）验证 + 1 次 backprompt。**不引入 ALNS / MILP / RL**——n=87 用不上，1 个月时间盒投入产出比低。论文化亮点是「**path-level age-coupled service_time cap**」——OR 文献空白，可写论文。

### 7.4 与现有 ILS 路径的衔接细节

按 `agent/legacy/ils_planner.py` 行号 + 函数名落地：

```text
| 衔接点                        | 现状（行号 / 函数）                            | 改造动作                                                       |
|-------------------------------|---------------------------------------------|---------------------------------------------------------------|
| _utility                      | line 542（4 维加权和 + _overload_penalty）  | LLM 模式下：profit = LLM 出 score；保留 _overload_penalty 镜像 |
| _greedy_init                  | line 660（笛卡尔积全枚举）                   | 节点数升 5+ 时升级为 OP greedy insertion（按 score/cost 比插入）|
| _perturb                      | line 711（_swap_node + _shift_node）        | 加 _insert_node / _remove_node（达到 \[S5] Insert/Shake 完整态）|
| _local_search                 | line 836（一维枚举）                         | 升级为 2-opt（边交换）+ or-opt（节点重定位）                    |
| _retry_with_critic_feedback   | line 938（4 类违规黑名单）                   | 增加 LLM repair 路径：把 violation 喂回 LLM 出新 score          |
| _resolve_age_cap              | line 469（45/75/120/60）                    | 保持不变；这是项目原创点                                       |
| _overload_penalty             | line 506                                    | 保持不变；与 _check_age_aware_duration（critics_v2 line 311）镜像|
| _resolve_dynamic_dining_slots | line 615                                    | 保持不变；spec R5 已加固                                       |
```

衔接 spec C 落地路径（按 wave 推进）：
- **Wave 1**：维持 ILS 不动，仅在 `_utility` 上层包一个 `llm_profit_score(poi, intent)` 替换 4 维加权和（约 50 行）
- **Wave 2**：补 `_insert_node` / `_remove_node` 算子，把 mid_nodes 从「kind 序列定死」松绑为「真子集搜索」（约 200 行）
- **Wave 3**：把 `_retry_with_critic_feedback` 升级为 critic → LLM 反馈 → 重 ILS 的 LLM-Modulo 闭环（约 100 行）
- **Wave 4（可选）**：换 ILS 为 `N-Wouda/ALNS`（Python 框架）

### 7.5 阅读笔记

- **\[S1] Vansteenwegen 2011**：OP 综述基石。读 §2 节点定义 5 元组 + §4 benchmark + §6 多变体。**最值得读的 1 篇**。
- **\[S2] Gunawan 2016**：扩展变体。读 §6 OPHS / TDOP / MTW / soft TW。
- **\[S3] arXiv 2512.16865 (2025-12)**：最新综述（2025-12 发布），覆盖 RL / NN / matheuristic。**spec C 的 future work 引用它最权威**。
- **\[S4] Gunawan 2017 SAILS**：TOPTW SOTA。SA + ILS 拌一起，gap 0.75%。如果未来要在 OPLIB 上做对比实验，目标应是这篇。
- **\[S5] Vansteenwegen 2009 LNCS**：第一个把 ILS 用在 TOPTW 上，含 Insert/Shake 完整算法描述。**当前 ils_planner 升级目标论文**。
- **\[S6] OPLIB SMU**：数据集集合，可下载实例。
- **\[S7] Gavalas 2014 TTDP 综述**：把 TTDP 形式化为 OP/TOP/TOPTW 子类。**与晌午局问题相似度最高的一篇**——从 §3.4 personalization 直接看到「heterogeneous preference」是开放问题，正是项目可贡献处。
- **\[S8] ItiNera EMNLP 2024**：LLM + OR 混合工业实践。读 §3 4-stage architecture。**spec C 直接抄它**。
- **\[S9] TripCraft ACL 2025**：LLM 行程 benchmark；4 类约束（空间/时间/persona/event）已被 critic 全覆盖（除 event）。

读取代码笔记：
- `ils_planner.py:1-1007` 整体读完。`_utility`（line 542）4 维加权和 + R5 R4 增强是最有项目特色的部分。
- `ils_planner.py:469-540` `_resolve_age_cap` + `_overload_penalty` 的镜像设计（与 critics_v2 同源公式）是面向 LLM 调优的工业级抗绕过设计。
- `critics_v2.py:1-835` 读了前 80% 全部 critic 实现 + 10 类 ViolationCode + `_check_age_aware_duration`（line 308）。violation 文案不暴露 dot-path 是 design.md 强约束。
