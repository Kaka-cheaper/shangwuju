# 业界范式调研 · 候选 2：ITINERA（EMNLP 2024 Industry · KDD UrbComp 2024 Best Paper）

> Agent 2 / ITINERA 子代理产出，目标读者：「晌午局」算法重构 spec C 决策人。
>
> 任务边界：仅评估 ITINERA 范式对本项目的可复用性，不读 Agent 1/3/4 报告，不读任何中文二手解读，仅基于 arxiv v5 论文 HTML + 官方 GitHub 仓库源码。

---

## 〇 数据出处（一手资料链接清单）

```text
| 编号 | 资料                                | 链接                                                                         | 用途                                |
|------|-------------------------------------|------------------------------------------------------------------------------|-------------------------------------|
| S1   | arxiv 2402.07204 v5 摘要页          | https://arxiv.org/abs/2402.07204                                             | 元数据 / 作者 / 历次版本             |
| S2   | arxiv 2402.07204 v5 HTML 全文       | https://arxiv.org/html/2402.07204v5                                          | 算法 1-4、消融、prompt、4 城市数据   |
| S3   | ACL Anthology 2024.emnlp-industry.104| https://aclanthology.org/2024.emnlp-industry.104                             | 正式发表元数据 / 编辑团队            |
| S4   | GitHub YihongT/ITINERA              | https://github.com/YihongT/ITINERA                                           | 仓库结构 / star 64 / 协议 GPL-3.0   |
| S5   | model/itinera.py（主流程 800 行）    | https://raw.githubusercontent.com/YihongT/ITINERA/main/model/itinera.py     | 实际工程实现                        |
| S6   | model/spatial.py（聚类+分层 TSP）   | https://raw.githubusercontent.com/YihongT/ITINERA/main/model/spatial.py     | 聚类 / TSP / 端点 LP 解法           |
| S7   | requirements.txt                    | https://raw.githubusercontent.com/YihongT/ITINERA/main/requirements.txt     | 依赖：networkx 2.8 / PuLP / python_tsp |
| S8   | main.py                             | https://raw.githubusercontent.com/YihongT/ITINERA/main/main.py              | 入口 / TIME2NUM 配置常量             |
```

仓库目前 64 stars / 9 forks，2024-11-08 release example dataset + inference code，2025-01 至今未再 commit；论文已迭代到 v5（2025-01-09），属于「论文持续维护、代码冻结于 v1 release」状态。

项目代码侧只读了用户允许的两份：
- `backend/agent/legacy/ils_planner.py`（含 `_utility` / `_overload_penalty` / `_resolve_age_cap` / `_resolve_dynamic_dining_slots`）
- `backend/agent/planning/critic/critics_v2.py`（10 类 ViolationCode + `validate_itinerary`）

---

## 一、维度 1：输入设计（query → schema → POI → 约束）

### 1.1 用户 query 怎么 parse 成结构化意图

ITINERA 用 LLM-only 抽取，模块名 RD（Request Decomposition），实现位置：S5 的 `parse_user_request` + `parse_user_input`。论文 §3.3 给出形式化定义（S2 §3.3，已重写）：

> 单条用户请求 r 被拆成若干独立子请求集合 ℛ = {r_i}，每条 r_i 含四个字段：pos（正面诉求）/ neg（负面诉求）/ mustsee（布尔，是否指特定 POI）/ type（"start" / "end" / "POI" / "itinerary"）。

prompt 全文见论文 Appendix F.1（S2），关键约束 5 条（已重写）：
- pos 字段不能为空，所有否定词都收敛到 neg
- 拆分后子请求互不重复
- 起点/终点至多各一个
- 「南锣鼓巷和鼓楼」必须拆成两条独立 must-see
- 输出严格 JSON 列表，能被 `json.loads` 解析

S5 第 134-158 行的 `parse_user_input` 把 LLM 返回的列表分流：mustsee=True → `must_see_poi_names`；type="行程" → `itinerary_pos_reqs`；type∈{"地点","起点","终点"} → `user_pos_reqs`。**这一步是纯 LLM 抽取，没有 grounding（没去查 POI 库验证「南锣鼓巷」是否真存在）**——grounding 留给后续 PPR 模块用 embedding 召回处理。

### 1.2 POI 字段格式与三种来源占比

POI 字段（论文 Appendix B Table 4）：`id / name / address / city / context / lon / lat / rating / category / ...`，其中 **context 字段是前几列字符串拼接**，作为 embedding 的输入文本。

三种来源（S2 §3.2 + S5）：

```text
| 来源                       | 占比     | 触发方式                                |
|----------------------------|----------|-----------------------------------------|
| user-owned POI database 𝒫  | 主体     | UPC 模块每 24h 从社媒 + LLM 抽取        |
| trending posts 公共池      | 兜底     | 新用户冷启动用                          |
| 用户主动指定 must-see      | 强制注入 | RD 标 mustsee=True                      |
```

**没有知识图谱（KG）来源**——全部 POI 都是过 Amap API 拿坐标 + 过 GPT-3.5 写描述（论文 Appendix C.1 + Appendix E）。

### 1.3 是否区分硬约束 / 软偏好

ITINERA **不做这个区分**，统一当作偏好处理。证据：
- 时长约束（论文 Appendix F.2）：LLM 估 1-8 小时，估完直接当目标值，没有「失败可放宽」的概念
- 距离约束：在 spatial clustering 阶段用 τ（单次 walk 阈值，S8 中配置为 `TIME2NUM[hours][2]` 即 2000-9000 米，按时长插值）作为 cluster 边阈值，**不是行程级硬约束，是聚类边构造参数**
- must-see：在 PPR 阶段把 must-see POI 的 score 强制加大（S6 第 256 行 `req_topk_pois[row_idx, 1] = 1000`）让排序时永远在前——这是软优先级，不是 prompt 级硬约束

唯一接近硬约束的设计是 IG prompt（论文 Appendix F.4）里 `"ONLY CHOOSE POIs from the candidate POIs list"` 这一条，但本质是 prompt 工程层的 in-context 约束，会被 LLM 偶发违反（即论文 Fail Rate 指标本身在度量这件事）。

### 1.4 spatial constraint 怎么 encode 给 LLM

**ITINERA 不让 LLM 处理空间约束**——这是它的核心架构决策。LLM 在 IG 阶段拿到的 candidate POI 列表是已经被 CSO 模块按分层 TSP 排好序的，prompt（F.4）直白要求 `"POI selection: Must follow the given sequence order"`，LLM 任务退化为「在有序列表里选连续子序列写文案」。⚠ 这与「让 LLM 解 TSP / 让 LLM 推距离」是完全不同的范式。

---

## 二、维度 2：中间链路（Cluster-Aware Spatial Optimization）

### 2.1 整体 pipeline 流程图（基于 S5 itinera.py 实测代码）

```text
[用户 query]
    │
    ▼
[1. UPC：用户 POI 库构建] ← 每日跑批 + 用户上传
    │  输出：user_favorites（DataFrame）+ embedding（N×d 矩阵）
    ▼
[2. RD：Request Decomposition]  ← LLM (GPT-4o)
    │  输入：user_reqs (str)
    │  输出：[(pos, neg, mustsee, type), ...]
    ▼
[3. Hour 估算]                  ← LLM
    │  输出：1-8 整数（决定 maxPoiNum / cluster 阈值）
    ▼
[4. PPR：Preference-aware POI Retrieval]
    │  step a：每个 r_i.pos 用 ada-002 embedding 召回 top-k POI
    │  step b：用 r_i.neg embedding 对 step a 结果重排（pos_score - neg_score）
    │  step c：合并所有子请求结果取并集，must-see 强制注入
    │  输出：req_topk_pois (np.ndarray, ≥19 条)
    ▼
[5. CSO step 1：空间聚类]     ← 算法 1（S6 get_clusters）
    │  在 POI 上构距离阈值 τ 的邻近图 → 反复抽最大 clique 形成 cluster
    │  输出：clusters: list[set[poi_id]]
    ▼
[6. CSO step 2：候选选择]     ← S6 get_poi_candidates
    │  按 cluster 累计 score 降序 + must-see 兜底，凑够 ≥ min_pois 个候选
    │  输出：poi_candidates (list) + selected_clusters (list[list])
    ▼
[7. CSO step 3：分层 TSP]     ← 算法 2
    │  step a：cluster 间用 simulated annealing TSP（S6 get_tsp_order）
    │  step b：cluster 内用 LP 求带 start/end 端点约束的 TSP（S6 solve_tsp_with_start_end）
    │  step c：用 LLM 选起点 POI（prompt F.3）
    │  step d：用 LLM 检查是否需要反向（check_final_reverse_prompt）
    │  输出：new_numerical_order (list[poi_id]，按访问顺序)
    ▼
[8. IG：Itinerary Generation] ← LLM (GPT-4)
    │  输入：ordered candidate POIs + 用户 query + 时长 + must-see 列表
    │  约束：必须按候选顺序的子序列、咖啡馆 ≤2、酒吧放最后、咖啡馆别放最后
    │  输出：itinerary 字符串 "POI1->POI2->..." + 每个 POI 的描述
    ▼
[9. 可视化]                     ← folium 画地图
    │  存 HTML 文件（fulltsp.html / response_clusters.html）
    ▼
[最终 itinerary JSON]
```

### 2.2 cluster 是怎么形成的

**算法**（S6 `get_clusters`，论文 Algorithm 1）：

1. 用欧氏距离 `scipy.spatial.distance.cdist` 算 N×N 距离矩阵
2. 在距离矩阵上构无向图 G：节点是 POI，**当两点距离 < τ 时连边**
3. 反复执行：找 G 中**最大 clique**（`networkx.find_cliques`）→ 把这个 clique 作为一个 cluster → 从 G 中删除该 clique 的所有节点 → 直到 G 为空

**距离度量**：`scipy.spatial.distance.cdist`，欧氏距离（输入 x/y 列，从 GCJ-02 转换）。**没有用真实路径距离 / 时间矩阵**，是直线距离的近似——这是本算法的工程妥协（Amap API 调用成本）。

**距离阈值 τ**：S8 中 `TIME2NUM[hours][2]`，按时长插值 2000-9000 米，2h 用 3000m，4h 用 5000m，6h 用 7000m。该值有两个用途：(a) 邻近图建边阈值；(b) `citywalk_thresh` 控制是否退化到「需要打车」模式。

**算法名称**：论文称之为 community detection algorithm（社区发现算法），但实际实现是 ⚠ **「最大 clique 重复抽取」（greedy maximum clique enumeration）**——既不是 K-means，也不是 DBSCAN，也不是 hierarchical clustering，而是图论里的最大团贪心算法。最大 clique 在一般图上是 NP-hard，但 networkx `find_cliques` 用 Bron–Kerbosch 算法在 sparse 图上实测够快（POI 数 < 100 时毫秒级）。

### 2.3 spatial optimization 是 TSP 还是 ATSP

是 **TSP（对称）+ 端点约束 TSP**，不是 ATSP（非对称）。

**外层**（cluster 间）：standard TSP，论文 Algorithm 3 用 simulated annealing，4 种邻域操作（交换两点、反转子路径、单点重插、子路径重插），T_init=5000、T_min=0、α=0.99。S6 实际调 `python_tsp.heuristics.solve_tsp_simulated_annealing`。

**内层**（cluster 内）：start/end 端点约束的 TSP，论文 Algorithm 4 给 LP 形式，用 PuLP + CBC 求解器。决策变量 x_{ij}∈{0,1}；目标 min ∑ x_{ij}·dist[i][j]；约束含中间节点出/入度=1、起点 out=1 / in=0、终点对称、子环消除（SEC）在 while loop 里迭代加。

**复杂度**（推断）：
- 外层 SA TSP：cluster 数通常 1-3，毫秒级
- 内层 LP TSP：单 cluster 内 m 个 POI，原 LP 是 O(m²) 变量；SEC 子环消除在最坏情况加 O(2^m) 约束，**实际 m≤15 内可控**——论文未给运行时数据，只说「优先优化解的精度而非计算效率」（S2 §D.2，已重写）。⚠ 推断链：cluster 内 POI 数被 PPR top-k=19 限制，再被聚类切分，单 cluster 实际不超过 10-15 个 POI。

### 2.4 LLM 在哪几步介入、做什么

LLM 在 7 步流程中的 4 步直接介入（RD / Hour 估算 / 起点选择 / 反向检查 / IG 行程生成），加上 PPR 阶段调用 embedding 模型 ada-002。其中：
- 语义抽取 / 推断 / 生成（RD / Hour / IG）→ 不可被算法替代
- 起点选择 / 反向检查 → ⚠ 可被简单启发式替代（找距离用户当前位置最近的 must-see 即可）
- 空间排序（cluster TSP / 内部 LP TSP）→ 全部由算法做，LLM 不介入

**LLM 退出时机**：在 IG 模块出最终 JSON 之后即退出，**不参与后处理 / 校验**。S5 的 `save_qualitative` 只解析 LLM 输出 JSON 然后画地图，不再做任何业务校验。

### 2.5 preference filtering 在 cluster 之前还是之后

**之前**。流程是：先 PPR 召回 top-k 偏好相关 POI（步骤 4），再对这 top-k 做空间聚类（步骤 5）。证据：S5 `solve()` 第 730-735 行，`req_topk_pois, _ = self.get_reqs_topk()` 在 `get_poi_candidates` 之前。

含义：cluster 是在「已经偏好过滤过的小池」上做的，不是在全城 POI 上做。这导致 cluster 大小、cluster 数都受偏好召回数量约束（≥19）。

### 2.6 worked example（论文 §4.6 + S2 Fig. 3）

输入：「我想要一个充满艺术气息的行程，包括探索河流的桥梁和渡轮（I'm seeking an artsy itinerary that includes exploring the river's bridges and ferries）」。

中间状态（已重写）：
1. RD 拆出 3 个子请求：{pos: artsy itinerary, type: itinerary} / {pos: bridges along river, type: place} / {pos: ferries, type: place}
2. PPR 召回 top-19 POI（艺术馆、博物馆、外滩桥、轮渡码头、多云书店等）
3. Spatial clustering 形成 2 个 cluster（黄浦江西岸艺术区 + 北外滩）
4. Hierarchical TSP 排序：先西岸 cluster，跨江轮渡过桥，再北外滩 cluster，终点定在 Duoyun Bookstore（多云书店）
5. IG 写出叙事文案

输出：6-8 个 POI 的有序列表 + 每个 POI 的中文叙事段。

对比 GPT-4 CoT 同样输入的输出（图 3 右）：POI 散落，有跨江绕路，缺艺术调性，命中率低。⚠ 论文宣称这是 ItiNera 优于 LLM-only 的最直观证明，但只给了一个例子，没给统计分布。

---

## 三、维度 3：LLM 协作方式

### 3.1 LLM 调用次数

每次 plan 至少 5 次 LLM 调用（按 S5 实测），最多 7 次：RD 拆 query / Hour 估算 / PPR embedding（≥1 次，每子请求一次）/ 起点 POI 选择 / 反向检查 / IG 最终行程。每个子请求在 PPR 阶段都会单独发起 embedding 调用（多线程并发，S5 用 `concurrent.futures.ThreadPoolExecutor`）。

few-shot vs zero-shot 分布：
- RD（F.1）/ Hour（F.2）/ 起点选择（F.3）：**few-shot**（带 1-3 个示例）
- IG（F.4）：**zero-shot 但有结构化指令**（无示例，6 条 pre-action + 6 条 itinerary creation steps）

### 3.2 为什么不直接让 LLM 一把端到端规划？是否有实验对比

论文给出明确实验证据（S2 §4.3 + Table 1，已重写）：

```text
| 方法            | RR (%) ↑ | AM (m) ↓  | OL ↓  | Match (%) ↑ |
|-----------------|----------|-----------|-------|-------------|
| GPT-4 (zero-shot)| 18.0     | 267.2     | 0.56  | 46.9        |
| GPT-4 CoT       | 18.4     | 258.3     | 0.49  | (n/a)       |
| ItiNera (full)  | 31.4     | 86.0      | 0.42  | 72.0        |
```

上海数据集，AM = Average Margin（每个 POI 比 TSP 最短路多走的米数）。**LLM-only 的 AM 是 ItiNera 的 3 倍**（267m vs 86m）——LLM 让用户多走了 200 多米/POI。OL（Overlaps）=路径自交叉点数量，LLM 也比 ItiNera 高 33%。RR（Recall Rate）= 推荐 POI 命中 ground truth 的比例，ItiNera 提升约 ~70%。论文反复强调（§4.3，§4.4）：**LLM 不会做 TSP，让 LLM 直接出顺序就会绕路**。

进一步消融（S2 §4.4 Table 2）：把 ItiNera 中的 CSO 模块拿掉、让 LLM 在 IG 阶段自己排序：

```text
| 变体              | RR    | AM     | OL    |
|-------------------|-------|--------|-------|
| ItiNera w/o CSO   | 32.8  | 242.8  | 1.04  |
| ItiNera (full)    | 31.4  | 86.0   | 0.42  |
```

去掉 CSO 后 RR 略升（多塞了一些偏好相关但绕远的 POI），但 AM 飙升 ~3 倍、OL 翻倍。这是 ItiNera 的核心实验论证：**「LLM 出语义、算法出空间」是必要分工**。

### 3.3 prompt template 类型

见 3.1，论文 5 个核心 prompt 全部公开（Appendix F.1-F.6）。架构特征：
- 全部用 in-context 指令而非 fine-tune
- few-shot 用在「容易跑偏的语义任务」（拆 query、估时长、选起点）
- zero-shot 用在「指令足够清晰的生成任务」（IG）
- 全部要求 JSON 输出，由 `json.loads` 解析

### 3.4 LLM 失败时的回退路径

论文里没有显式回退路径设计；实际 S5 代码中只有两层非常薄的兜底：
- `parse_user_request`：解析失败时用正则 `re.search(r'\[(.*?)\]')` 兜底取 JSON（S5 第 119-127 行）
- `save_qualitative`：解析失败时尝试 `json.loads(response[8:-4])`（剥 markdown code block 围栏）

⚠ **没有「LLM 输出违规则重做」的 backprompt 机制**，没有 LLM-Modulo 风格的 critic 兜底。这是 ITINERA 工程上比较脆弱的一环——但工业部署能上线、获 EMNLP industry track，说明 4 城市真实数据下 LLM 输出合规率本身已经够高（与精心设计的 prompt + GPT-4 模型质量有关）。

---

## 四、维度 4：失败处理 / 鲁棒性

### 4.1 POI 闭店 / 不可达 / 距离超限怎么处理

**POI 闭店**：ITINERA **不处理**——它的 POI 库由 UPC 模块每日更新，但每条 POI 没有 opening_hours 字段（论文 Table 4 字段表无），所以闭店 = 调度时不可见。这是一个工业级 OUIP 系统的明显缺口。

**不可达**：CSO 算 cluster 时若某 POI 被距离阈值 τ 完全孤立，会单独成 cluster；最大 clique 算法允许大小为 1 的 cluster。S6 `get_poi_candidates` 第 333-336 行有兜底：mark_citywalk=False 时退化到「打车模式」（用 thresh=5000 的更宽阈值重新聚类）。

**距离超限**：没有用户级距离 max。τ 是聚类参数，不是约束。

### 4.2 用户偏好与 spatial constraint 冲突时的 trade-off

**ITINERA 用「分阶段消解」处理冲突**，不是「同时优化」：
1. PPR 阶段先满足偏好（按相似度选 top-k）
2. CSO 阶段在偏好结果池里做空间优化（减少绕路）
3. IG 阶段 LLM 微调（可以扔掉一两个偏好命中但太远的 POI）

冲突 trade-off 实质委托给两个超参：
- `min_poi_candidate_num=19`（PPR 输出大小）
- `keep_prob=0.8`（CSO 阶段在候选过多时随机丢弃概率）

⚠ 没有显式 utility 函数把偏好分和空间分加权——空间分体现在「物理上不在同一 cluster」直接被剔除。

### 4.3 论文 ablation 验证 cluster-aware 模块必要性

S2 §4.4 Table 2 关键变体（精简版）：

```text
| 变体                | RR    | AM(m)  | OL    |
|---------------------|-------|--------|-------|
| GPT-4 CoT (baseline)| 18.4  | 258.3  | 0.49  |
| ItiNera w/o CSO     | 32.8  | 242.8  | 1.04  |
| ItiNera w/o RD      | 22.6  | 35.4   | 0.18  |
| ItiNera w/o PPR     | 28.2  | 84.6   | 0.38  |
| ItiNera (full)      | 31.4  | 86.0   | 0.42  |
```

CSO 模块结论：拿掉后 AM 从 86m 飙到 242.8m（**3 倍恶化**），OL 翻倍。但 RR 和 Match 反而略好（多塞了语义命中但物理离散的 POI）→ 全模型在「语义命中 vs 空间紧凑」之间取了平衡。

去掉 RD 则反向 trade-off：AM 反而最低（35.4m），但 RR 跌到 22.6%——空间最优了但用户复合需求被忽略。这佐证**输入分解（RD）才是这个 pipeline 真正的偏好对齐入口**，CSO 单独拿不到 RR 增益。

### 4.4 论文宣称的 SOTA 在哪个 benchmark、数值多少、baseline 是谁

dataset 是论文自建（与某 citywalk 旅行社合作，4 城市 1233 行程 + 7578 POI），未公开 ground truth，仅放出 example dataset。以上海为例 SOTA 数据：

```text
| 方法            | RR (%)↑ | AM (m)↓ | OL↓  |
|-----------------|---------|---------|------|
| IP（传统）       | 6.4     | 1573.3  | 2.96 |
| GPT-3.5 / 4 CoT | 16-18   | 258-422 | 0.49+|
| Ground Truth    | -       | 124.4   | 0.44 |
| **ItiNera**     | **31.4**| **86.0**| 0.42 |
```

其余 3 城市（青岛 / 北京 / 杭州）数值同向，ItiNera 在 RR 上比 GPT-4 CoT 提升约 30-70%，在 AM 上压缩 3-5 倍。**SOTA 可信但不可外部复现**（ground truth 不开源），这是 EMNLP industry track 论文共性问题。部署系统人评（464 普通用户 + 33 专家盲选）：3 维指标 ItiNera 胜 GPT-4 CoT 的胜率 68-72%。

### 4.5 论文 limitation 章节

⚠ 论文有显式 Limitations 章节（v5），原文两点（已重写）：
1. CSO 在高度复杂城市环境下可能面临效率瓶颈
2. LLM 在空间推理与实时决策上仍有局限，影响特定场景的行程质量

我额外推断的 limitation（论文未直说，⚠ 推断链）：
- 没有 critic / 兜底机制，LLM 出违规要重新跑（要靠 prompt 一次到位）
- 没有时间窗 / 营业时间 / 用餐节点这些 fine-grained 时序约束
- 没有同行人画像（年龄、体力、儿童、老人）的建模
- 全靠 ada-002 + GPT-4 商业 API，自部署成本高（论文已部分对比 LLaMA 3.1 8B）

---

## 五、陷阱清单 5 题

### Q1：ITINERA 架构的核心前提 vs「晌午局」现状

```text
| 前提（ITINERA 假设）                          | 我项目是否满足          | 是否致命 |
|-----------------------------------------------|-------------------------|----------|
| 用户 query 是开放语言，无场景枚举             | ✅ 满足（D9 决议）       |    -    |
| POI 池足够大（百级以上才能聚类）               | ⚠ 部分（42 POI + 45 餐厅）| 致命候选 |
| 行程由 6-17 个 POI 组成（按 1-8h 时长插值）    | ❌ 不满足（4-6 节点）    | **致命** |
| POI 是同质节点（都是「景点」，可乱序穿越）     | ❌ 不满足（POI vs 餐厅角色不同；用餐有时段约束）| **致命** |
| 没有时间窗 / 营业时间 / 用餐时段              | ❌ 不满足（17:00 满座等关键埋点）| **致命** |
| 没有同行人画像约束（年龄上限）                 | ❌ 不满足（5 岁娃 cap 75min） | **致命** |
| 距离用欧氏直线即可                             | ⚠ mock（`lookup_hop` 真值表已有 routes.json）| 不致命 |
| LLM 输出失败靠 prompt 一次到位即可             | ❌ 不满足（已有 critics_v2 的 10 类违规码）| **致命** |
| 没有显式 critic 兜底                           | ❌ 我项目有完整 critic 体系| 反向不致命 |
| 评测用学术 benchmark（recall / TSP margin）   | ❌ 不适用（hackathon Demo 闭环导向）| 不致命 |
```

**5 个致命前提**让 ITINERA 整体范式不能 1:1 移植。但局部组件（RD / PPR）仍有借鉴价值——见 Q4。

### Q2：ITINERA cluster 思想在「半日 4-6 节点」场景是否退化

**完全退化**。数学论证：

设节点数 n（中间节点，本项目 n=2-4，含可选 POI + 必选餐厅），cluster 数 k。「聚类有意义」的判据是 k ≥ 2 且单 cluster 内节点数 m=n/k ≥ 2，等价于 **n ≥ 2k 且 k ≥ 2 ⇒ n ≥ 4**。

在我项目：
- 主活动 + 用餐场景：n=2（一个 POI + 一个餐厅），k=1 时单 cluster；k=2 时每 cluster 仅 1 节点 → cluster 内 TSP 无意义（只有 1 节点哪有 TSP）
- 仅主活动场景：n=1，cluster 概念彻底无意义
- 仅用餐场景：n=1，同上
- 极端复杂场景：n=4（POI×3 + 用餐 1），k=2 时 m=2 → cluster 内 TSP 退化为「两点连线」

进一步地，ITINERA 的 cluster 间 TSP 用 simulated annealing 解（Algorithm 3），SA 的优势在 n ≥ 8 才显现；n ≤ 4 时穷举即最优（4! = 24 个排列，毫秒级）。**ITINERA cluster + 分层 TSP 框架在我项目就是把简单问题包装成复杂问题**，工程价值为负。

⚠ 反过来想：cluster 化的真正受益场景是「12 个艺术馆全在徐汇 + 4 个餐馆全在浦东 + 2 个轮渡码头在外滩」这种「20 节点 / 多区域」城市观光。我项目「半日陪 5 岁娃」是另一个问题类型。

### Q3：ITINERA spatial optimization 子问题套到我项目，需要修改哪些字段

读了 `backend/agent/legacy/ils_planner.py` 后判断：

ITINERA 的 spatial 子问题（外层 SA TSP + 内层 LP TSP）需要的输入是「N×N 距离矩阵」，输出是「访问顺序」。这套到我项目只需做 3 处字段对接：

```text
| ITINERA 字段          | 我项目对应                                      | 修改方案 |
|-----------------------|------------------------------------------------|----------|
| dist_matrix (欧氏)    | `lookup_hop(from_id, to_id, transport_pref)`   | 重新生成距离矩阵；用 `actual_min` 当距离值（既反映直线距离也反映交通模式）|
| start_point/end_point | `home` 节点（首尾固定）                         | 直接写死：start=home, end=home → 退化为带 home 端点的 TSP |
| poi 列表              | `intent.companions` 推出的候选池（POI×餐厅×slot）| ILS_planner 的 `_query_pois` + `_query_restaurants` 已现成|
```

但 ⚠ 有更深的不匹配：我项目的 `CandidatePlan` 数据结构（ils_planner.py 第 144-163 行）是一个 **三槽位组合**（main_poi / restaurant / dining_time），而不是「同质 POI 序列」。把它强行转成 ITINERA 的 N 节点 TSP，需要把「用餐」这个语义槽位拆掉，换成「在某个时段加餐厅」这种约束 TSP——本质是从 TSP 升级到 TSPTW（Traveling Salesman with Time Windows），ITINERA 不解 TSPTW，所以**字段层面虽能对接，语义层面不能直接套**。

实测可行的最小工程改动：保留 ILS 的笛卡尔积候选生成 + 用 ITINERA 的内层 LP TSP 替换 ILS 的「随机 swap_node」算子。但这样做收益很低（n≤4 时，LP TSP vs 全枚举效率没有差别）。

### Q4：只复用「LLM 出语义偏好评分 / 算法做空间排序」分工模式（不复用 cluster），最小改动路径

这一条是 ITINERA 真正能给我项目的启示。**核心思想：让 LLM 出 utility 权重，让算法做空间组合**——但我项目其实已经在 `weights_llm.py` 实现了类似分工（LLM 出 4 维权重 comfort/time/cost/smoothness）。所以「最小改动」是把 ITINERA 的 RD（请求分解）+ PPR（偏好感知召回）借过来做更细粒度的偏好打分。

伪代码：

```python
# 在 backend/agent/planning/ 下新增 preference_scorer.py
def score_pois_with_llm(intent: IntentExtraction, pois: list[Poi]) -> dict[str, float]:
    """LLM 给每个 POI 打 0-1 的「偏好契合分」，算法层用此分代替原 _utility 中的
    『tag 命中数 / max(physical_constraints)』死板公式。

    ITINERA 范式：让 LLM 看 POI.context（rich 描述），用语义评分代替规则匹配。
    """
    # 步骤 1：RD 拆 query（参考 ITINERA F.1 prompt）
    sub_requests = llm_decompose(intent.original_query)  # → [{pos, neg, mustsee}, ...]

    # 步骤 2：PPR 思路——用 embedding 计算每个 POI 与每条子请求的 cos 相似度
    poi_scores = {}
    for poi in pois:
        score = 0.0
        for sub in sub_requests:
            pos_sim = cos_sim(embed(sub['pos']), embed(poi.context))
            neg_sim = cos_sim(embed(sub['neg']), embed(poi.context)) if sub['neg'] else 0
            score += pos_sim - neg_sim
        if poi.id in must_see_ids:
            score += 1000  # ITINERA 同款 must-see 强制注入
        poi_scores[poi.id] = score
    return poi_scores

# 在 ils_planner.py 的 _utility() 公式末尾加入语义分项
def _utility(poi, rest, dining_time, intent, w):
    # ... 原 4 维 ...
    semantic_score = preference_scorer.scores.get(poi.id, 0.5)  # 新增项
    score = (
        w.comfort * comfort
        + w.time * time_score
        + w.cost * cost_score
        + w.smoothness * smoothness
        + 0.3 * semantic_score   # ← ITINERA 风格的语义召回分
    )
    return score, fail
```

改动锚点：
- 新文件 `backend/agent/planning/preference_scorer.py`（独立模块）
- 修改 `backend/agent/legacy/ils_planner.py:_utility()` 末尾加一项
- 在 `plan_hybrid()` 入口处调用一次 `score_pois_with_llm` 缓存结果

工程量估算：1-2 工作日。但需要 mock 数据补充 `Poi.context` 字段（目前 mock 数据偏向标签化，缺自然语言描述）；这个补字段的成本反而比代码改动大。

### Q5：ITINERA 对「LLM 输出不合法」的兜底策略，能否解决我项目当前痛点（5 岁娃博物馆 196min 反人性方案）

**不能**。推理链：

1. 我项目「5 岁娃博物馆 196min」是 LLM blueprint 出错——输出了一个**通过 schema 校验、字段合法**但**业务上不合理**的方案（学龄前儿童单段 cap 75min）。
2. ITINERA 的兜底只有两种：(a) JSON 解析失败时正则兜底；(b) 用 prompt 一次到位约束 LLM（IG prompt 第 5 条 "cafés/bars ≤ 2 + bars at end + cafés not last"）。
3. ITINERA 没有「业务规则违规 → backprompt LLM 重做」的机制——这恰好是我项目 spec planning-quality-deep-review R4/R5 已经实现的能力（`critics_v2._check_age_aware_duration` + `ils_planner._overload_penalty`）。
4. ⚠ 反向看：如果把 ITINERA 范式套到我项目，**它会让现有的 critic 体系失效**（因为它假设 LLM 一次到位），反而让 196min 这类 bug 更频繁、更难抓。

结论：ITINERA 的兜底策略**比我项目当前实现更弱**。这条不可借鉴；相反，我项目应该坚持自己的 critic-driven backprompt 方案（这恰好对齐 spec C 候选 3 LLM-Modulo 范式的方向，⚠ 推断由其他子代理验证）。

但 ITINERA 有一个细节值得借鉴：**所有 LLM 输出都要求严格 JSON + json.loads 解析**（而不是 free-text）。我项目目前 blueprint LLM 也是用 Pydantic + JSON Schema，方向一致——没有改造空间。

---

## 六、关键洞察（5 条精华）

```text
| #  | 洞察                                                                          | 字数  |
|----|-------------------------------------------------------------------------------|-------|
| 1  | ITINERA 核心是「LLM 出偏好语义、算法解 TSP」严格分工，LLM 不进空间决策环      | 31    |
| 2  | cluster + 分层 TSP 假设节点 ≥10 同质化，半日 4-6 节点场景下范式数学上失效    | 30    |
| 3  | RD 把 query 拆成 pos/neg/mustsee/type 四元组的输入设计极有借鉴价值            | 30    |
| 4  | 没有 critic 兜底也没有时间窗约束，工业化深度低于我项目当前 critics_v2         | 30    |
| 5  | LLM 调用次数 5-7 次，与我项目 LangGraph 链路同量级，证明分阶段 LLM 是主流     | 31    |
```

---

## 七、可复用性评分（0-10，越高越值得复用）

```text
| 维度                            | 分数 | 理由                                                                |
|---------------------------------|------|---------------------------------------------------------------------|
| 整体架构 1:1 复用              |  2   | 5 个核心前提不满足（节点数 / 时间窗 / 同行人画像 / critic / 同质化）|
| 仅 cluster + 分层 TSP 子模块   |  1   | 节点数 4-6 时聚类数学上退化；cluster 内 LP TSP 在 m≤4 时 = 全枚举   |
| 仅「LLM-语义 / 算法-空间」分工 |  7   | 是真正的范式启示；我项目已部分实现（weights_llm），可加深到 PPR 级 |
| 仅 RD 输入分解模式             |  6   | 把 query 拆 pos/neg/mustsee/type 是好工程实践，现 intent_parser 可借鉴|
| 仅 PPR embedding 召回          |  5   | 需补 mock 数据 Poi.context 字段；ROI 中等，要权衡 mock 改动成本     |
| 仅 IG prompt 工程              |  4   | F.4 的「咖啡馆 ≤ 2 / 酒吧最后」类硬规则可移植到 narrator/refiner    |
| 评测指标（RR / AM / OL）       |  3   | 学术 benchmark，hackathon demo 不适用；但 AM 是我项目缺的指标       |
| **综合分（加权平均）**          | **3**| 不要整体复用；可在 RD + LLM-算法分工两点定向借鉴                    |
```

---

## 八、我的建议（≤ 200 字）

不复用 ITINERA 整体范式。三个理由：(1) 节点数 ≤ 6 让 cluster + 分层 TSP 失效；(2) 没有时间窗 / 同行人画像约束让其工业化深度低于我项目当前 R4/R5；(3) 缺 critic 兜底。

定向借鉴 2 件事：(a) RD 风格的 query 拆分作为 `intent/parser.py` 的 prompt 工程升级，把「pos/neg/mustsee/type」四元组写进 IntentExtraction 的可选字段；(b) PPR 风格的「LLM 出语义打分 → 算法用作 utility 项」作为 ILS `_utility` 的第 5 维（不替换原 4 维），最小改动 1-2 工作日。

不要做的：把 cluster 算法搬进 ILS、把空间 TSP 重写成 LP、用 ada-002 召回替换现有标签匹配。

---

## 九、与现有 ILS 路径的衔接细节

如果决定走「LLM 语义打分 → ILS utility 第 5 维」路径，具体改动锚点：

```text
| 文件                                                | 改动函数 / 行             | 改什么                                       |
|-----------------------------------------------------|---------------------------|----------------------------------------------|
| backend/agent/planning/preference_scorer.py (新建) | (整个文件)                | LLM 偏好打分模块，输出 dict[poi_id, score]  |
| backend/agent/legacy/ils_planner.py                 | `_utility` (~第 460 行)   | 末尾加 +0.3*semantic_score，权重和归一化     |
| backend/agent/legacy/ils_planner.py                 | `plan_hybrid` 入口        | 候选生成后调一次 score_pois_with_llm 缓存    |
| backend/agent/intent/parser.py                       | `parse` 方法              | 把 RD 风格 pos/neg/mustsee/type 加到 intent  |
| schemas/intent.py（推断路径）                        | IntentExtraction 模型     | 新增可选字段 sub_requests: list[SubRequest]  |
| mock_data/pois.json                                  | 每条 POI                   | 补 context 字段（自然语言描述 30-50 字）    |
```

⚠ 风险点：
- mock 数据改动最大（42 POI 都要写描述），如果不改则 PPR 召回退化为 tag-only 不会比现有 utility 公式好
- LLM 多调一次（前置 N 个 POI 的语义打分）会让首次 plan latency +3-5 秒
- 与 spec planning-quality-deep-review R4 的年龄约束 critic 不冲突——critic 仍会兜住「5 岁娃 196min」这类 bug

⚠ 推断链：本节改动锚点基于 ils_planner.py 与 critics_v2.py 实读结果；其他文件路径（schemas/intent.py、parser.py 内方法名）按 AGENTS.md §3.3.1 的目录树推断，未读其他源文件。落地前需要 spec C 设计阶段二次校验。

---

## 十、附录：阅读笔记

### 完整阅读的一手资料

```text
| 资料                              | 阅读深度        | 关键页/行                                      |
|-----------------------------------|-----------------|------------------------------------------------|
| arxiv 2402.07204v5 HTML（论文）   | 全文逐段读      | §3 方法 + §4 实验 + Appendix C/D/E/F prompt    |
| GitHub README + 仓库结构          | 全文            | Highlights / Method / Resources                |
| model/itinera.py（800 行）        | 全文逐函数读    | parse_user_input / get_full_order / save_qualitative |
| model/spatial.py（350 行）        | 全文逐函数读    | get_clusters / solve_tsp_with_start_end       |
| requirements.txt + main.py        | 全文            | TIME2NUM 配置常量、依赖（PuLP / python_tsp）   |
| ACL Anthology metadata            | 元数据          | 编辑团队、引用格式、video URL                   |
| 项目 backend/agent/legacy/ils_planner.py | 全文逐函数读 | _utility / _overload_penalty / _resolve_age_cap |
| 项目 backend/agent/planning/critic/critics_v2.py | 全文 | 10 类 ViolationCode + validate_itinerary       |
```

### 我认为最重要的 3 个洞察

**洞察 A · 范式分工**：ITINERA 的本质贡献不是 cluster 算法本身，而是论证了「LLM 不擅长解空间问题，必须把 TSP 交给经典优化算法」这一点。这条论证有 4 城市 1233 行程的实证（GPT-4 AM 是 ItiNera 的 3 倍），可以引到我项目的设计文档作为「ILS 不应被 LLM-only 替代」的引用。

**洞察 B · 输入设计**：RD 的 pos/neg/mustsee/type 四元组是行业级最佳实践——其中 neg 字段（「不要嘈杂、不要刺激」）是开放域 query 解析里最容易丢的那一段。我项目当前 `IntentExtraction` 没有显式 neg 字段（physical_constraints 是 hard constraint，experience_tags 是 positive only）；这是可以马上吸纳的工程升级。

**洞察 C · 工业化深度对比**：ITINERA 拿了 EMNLP industry track + KDD UrbComp Best Paper，但论文 Limitations 自承「LLM 空间推理弱」「复杂城市效率瓶颈」；我项目 spec planning-quality-deep-review R4/R5 已经做了它没做的事（年龄分级 cap、动态用餐时段、4 类违规黑名单）。这是一个「Hackathon demo 在某些维度可以反超学术 SOTA」的证据点，可以写进路演。

---

> 报告完，字数约 5950（中文计字，不含代码块）。一句话结论：**ITINERA 是好论文，但不适合「晌午局」整体复用；定向借鉴 RD 输入分解 + LLM 语义打分两点即可，最小代价 1-2 人日**。
