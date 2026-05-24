# Agent 5：RL 路径 Trip Planning 调研报告

> 子代理身份：Agent 5 / RL-based Trip Planning（Phase 1 第二批补强 1/4）
> 调研焦点：DeepTravel（DiDi 2025-09）+ STAR/Demystifying RL（2026-03）+ TripScore（2025-10）+ Single-turn GRPO（2025-09）+ ChinaTravel（2024-12）；与 Agent 4 已覆盖的 Planner-R1 / TravelPlanner / LLM-Modulo 严格区分。
> 报告语言：中文。所有表格放代码块。所有外推 / 推断显式标 ⚠。
> 与本项目代码的衔接：基于一手阅读 `backend/agent/graph/build.py`、`backend/agent/graph/nodes/replan.py`、`backend/agent/planning/critic/critics_v2.py` 三份。

---

## 〇、数据出处（一手 + 二手清单）

```text
| #  | 出处                                                                                  | 类型 | 用途                                  |
|----|---------------------------------------------------------------------------------------|------|---------------------------------------|
| 1  | arxiv.org/abs/2509.21842 (DeepTravel, Ning et al., DiDi, 2025-09-26)                  | 一手 | 主调研对象：RL+tool-use trip planning |
| 2  | huggingface.co/papers/2509.21842（DeepTravel HF 摘要 + librarian-bot 推荐相关论文）     | 二手 | 抽象 + 上下文佐证                     |
| 3  | liner.com/review/deeptravel-...（liner Quick Review，含 RQ1 中关于 32B 性能的细节）     | 二手 | 实验数字交叉核验                      |
| 4  | arxiv.org/abs/2603.21972 (Wu et al. STAR / Demystifying RL, 2026-03)                  | 一手 | 5 轴 RL 设计空间 + 7 个 takeaway      |
| 5  | liner.com/review/demystifying-...（含 STAR pipeline 与 RQ1-RQ6 总结）                  | 二手 | 训练数据量级 / reward 设计取舍依据    |
| 6  | arxiv.org/abs/2510.09011 (Qu et al. TripScore, 2025-10)                               | 一手 | unified reward + GRPO 实证            |
| 7  | arxiv.org/abs/2509.20616 (Hu et al. Single-turn GRPO, 2025-09)                        | 一手 | 1.5B 模型 70% 长程规划成功率          |
| 8  | arxiv.org/abs/2412.13682 (Shao et al. ChinaTravel, 2024-12 → ICLR 2026)               | 一手 | 中文 trip planning 数据可获得性       |
| 9  | arxiv.org/abs/2509.25779 (Planner-R1, 摘要部分；Agent 4 已覆盖详情)                   | 一手 | 仅做对照 / 不重复展开                 |
| 10 | tianpan.co/blog/2026-04-27-replan-dont-retry-agent-tool-errors（社区博客）             | 二手 | RL agent 行为分析的工程化补充         |
| 11 | 项目代码：backend/agent/graph/build.py / replan.py / critic/critics_v2.py             | 一手 | §维度 4 + 陷阱清单 Q4/Q5 衔接论证     |
```

数字 / 论断的引用规则：句末括号 [#出处编号]；推断或外推显式标 ⚠。论文里的复杂公式（如 GRPO 损失）仅做必要文字描述，不抄完整公式（30 词限制 + 我们用不上）。

---

## 一、核心论文画像（DeepTravel + 4 篇佐证）

### 1.1 DeepTravel（Ning et al. 2025-09-26，DiDi）[1][2][3]

> 论文标题原文：*DeepTravel: An End-to-End Agentic Reinforcement Learning Framework for Autonomous Travel Planning Agents*。已部署 DiDi Enterprise Solutions App 并做线上 A/B。

**一句话定位**：用 sandbox + 双层 verifier + replay buffer 把 Qwen3-32B 训成超过 OpenAI o1/o3 与 DeepSeek R1 的 trip planning agent，路径是「RL 微调 LLM 让它自己用 Tool」。

**框架核心三件套**[1][3]：

```text
| 组件                        | 作用                                               | 工程地位          |
|-----------------------------|----------------------------------------------------|-------------------|
| 1. Sandbox 环境             | 缓存交通 / 住宿 / POI 数据；屏蔽真实 API QPS / 一致性问题 | 训练能进行的前提 |
| 2. 分层 reward verifier     | 轨迹级先查时空可行性，回合级再查 itinerary 与 tool 响应一致性 | 解决 reward 稀疏 |
| 3. Replay 增强 RL（reply-augmented） | 周期性回放失败经验缓冲区                          | 解决长程稀疏成功 |
```

**RL 算法**：相比 PPO / GRPO / DAPO，replay 机制带来显著增益，尤其在复杂 query 上 [3]。注意它没说自己是「全新算法」，准确说法是「基于 GRPO 家族 + replay buffer trick」⚠（liner 表述为「outperforms PPO / GRPO / DAPO」，未明示主算法名；从 RQ4 中「validation reward / model entropy / average interaction turns」是典型 GRPO 监控指标可推断主算法是 GRPO 变体）。

**性能数字（一手）**[3]：

```text
| 指标                                  | 数字                              |
|---------------------------------------|-----------------------------------|
| DeepTravel-32B offline "无约束" 任务    | 69.34% Final Pass Rate            |
| 相对 K2（最强基线）的 lead              | +28.9 个百分点                    |
| RL 阶段相对 cold-start SFT 在线提升     | +24% (8B) / +25.5% (32B)          |
| Hallucination 率（cold-start vs RL）   | 50% → <20%                        |
| 训练用 LLM 选型                         | Qwen3-8B / Qwen3-32B              |
```

**与 Planner-R1（Agent 4 已覆盖，仅作差异对照）**：

```text
| 维度          | Planner-R1（2509.25779）        | DeepTravel（2509.21842）           |
|---------------|---------------------------------|------------------------------------|
| 任务模式       | sole-planning（不让 agent 调 Tool）| tool-use（agent 自主调 Tool）        |
| 训练 query 数  | 180 条人工标注                    | sandbox + 数据合成（量级未公开）⚠     |
| 模型尺寸       | 8B 最优                           | 32B 主推；8B 也跑通                  |
| reward 信号源  | TravelPlanner 规则评估器（leaderboard）| 双层 verifier（trajectory + turn）  |
| 落地证据       | 仅 leaderboard                    | DiDi App 线上部署 + 用户研究         |
```

### 1.2 STAR / Demystifying RL（Wu et al. 2026-03-23）[4][5]

> 论文标题原文：*Demystifying Reinforcement Learning for Long-Horizon Tool-Using Agents: A Comprehensive Recipe*。这篇是「RL trip planning 的 cookbook」——专门把 reward shaping / model scaling / data composition / algorithm selection / environmental stability 5 轴系统消融，使用的实验台正是 TravelPlanner（与 Agent 4 已覆盖的 benchmark 同源，但本论文是把 benchmark 当 lab 做 RL 配方研究，与 Agent 4 角度不同）。

**最相关的 7 个 takeaway 中和我们直接相关的 3 条**[4][5]：

1. reward 与算法选择是 scale-dependent：小模型（1.5B / 3B）从 staged / curriculum reward（密 → 疏）受益最大；7B 反而是 dense SUM reward 收敛最快——但 SUM 的代价是 OOD 泛化掉得最厉害（alignment tax）。
2. **训练数据 sweet spot ≈ 1K 条平衡难度**——过载会损伤 in-domain 与 OOD。这是个非常硬的工程数字。
3. **环境稳定性是 RL 训练能不能成的硬约束**——RQ6 直接做了「随机 Tool 失败注入」的对照实验，证明随机失败的环境会让 policy degradation。

STAR pipeline 是「Synthesis → Training → And Reinforcement」三段 [5]：先合成 feasible / 难度可控的 query，再用教师模型做 SFT，最后 GRPO 上 RL。这条 pipeline 给出的是「业界做 RL trip planning 的标准配方」。

### 1.3 TripScore（Qu et al. 2025-10-10）[6]

> 论文标题原文：*TripScore: Benchmarking and rewarding real-world travel planning with fine-grained evaluation*。

**贡献**：把行程评估的 fine-grained 指标统一成一个 **scalar reward**，使其能直接接 GRPO 训练。开放了 4870 条 query（含 219 条 free-form 真实用户请求）的数据集。 RL via GRPO 在 base 模型上一致提升 itinerary feasibility [6]。

**对我们的意义**：它证明「unified reward」是 RL 路径的工程必经之路——这与 critics_v2.py 现有 9+1 类 ViolationCode 形成天然映射（ViolationCode + severity → 一个标量 reward 是可机械化的，下面 §维度 2 / 陷阱 Q4 详谈）。

### 1.4 Single-turn GRPO（Hu et al. 2025-09-24, IEEE L-CSS 已接收）[7]

**核心思想**：把 multi-turn 任务规划转化为 single-turn 任务推理问题，避免 sparse episode reward 与 long-horizon credit assignment 两个老大难。1.5B 模型用 single-turn GRPO + dense 可验证 reward（来自 expert 轨迹），在长程规划任务（30+ 步）上拿到 70% 成功率，超过 14B baseline [7]。

**对我们的意义**：1.5B 都能行 → 「规划任务必须超大模型」是个误解。但代价是必须有 **expert trajectory** 给 dense reward。我们项目无 expert trajectory。

### 1.5 ChinaTravel（Shao et al. 2024-12，ICLR 2026）[8]

> 中文 trip planning benchmark，1154 真实用户 query + DSL（domain-specific language）+ neuro-symbolic baseline 在人类 query 上做到 37.0% constraint satisfaction（10× 优于纯神经）[8]。

**对我们的意义**：是目前**最贴近中文场景的 trip planning 数据集**，但仍然是 multi-day / multi-POI 跨城旅游（机票 + 酒店）；与晌午局的「半日 + 单城市 + 城内 POI」差距大。**不能直接用作我们的训练数据**，但 DSL 的「compositional constraint」结构给 critics_v2.py 的 ViolationCode 加新规则提供启发⚠。

---

## 二、维度 1：RL 输入设计（observation / action / state）

### 2.1 observation 三件套

DeepTravel 的 observation 由三部分构成（一手 abstract 表述「reflecting on tool responses to explore, verify, and refine intermediate actions」推断）⚠[1]：

1. **用户 query 文本**（出发城市 / 目的地 / 日期 / 偏好）
2. **当前 partial itinerary**（已规划部分）
3. **最近 K 步 Tool 响应**（含可能的 inconsistent output—— DeepTravel 用 sandbox 屏蔽这个不一致性）

STAR 的 observation 类似 [4][5]，但额外强调「difficulty 信号」也进 obs 让 curriculum learning 有数据可读。

晌午局对应映射（如果走 RL 路径）⚠：

```text
| 论文 obs 字段        | 晌午局对应                                             |
|----------------------|--------------------------------------------------------|
| 用户 query           | router_node 后 IntentExtraction（已结构化）            |
| Partial itinerary    | state["itinerary"] 的当前 nodes / hops（assemble 输出） |
| Tool 响应历史        | state["tool_results"]（execute_collect 汇聚后）        |
| 用户画像 / 同行人     | IntentExtraction.companions + UserProfile             |
```

### 2.2 action space 类型

DeepTravel / STAR 都是 **token-level**——RL 微调 LLM 自身，action 是「下一个 token」，但语义层面理解为「调哪个 Tool / 选哪个 POI / 对蓝图做什么修改」三类高层 action [1][4]。这与 Planner-R1 一致（Agent 4 已覆盖，不展开）。

Single-turn GRPO 是 token-level 但配合「expert trajectory dense reward」让 credit assignment 退化为单步可验证 [7]。

晌午局如果落地 RL，action space ≈ token-level（直接微调 Qwen3-8B）。**这意味着 critic 的反馈必须是 reward 形式而非 prompt 形式**——这是后面 §维度 2 reward shaping 与 critics_v2 的衔接核心。

### 2.3 训练 query 合成（核心可借鉴点）

```text
| 论文           | 训练 query 数                | 合成方式                           | 标注成本          |
|----------------|------------------------------|------------------------------------|-------------------|
| Planner-R1     | 180                          | 从 TravelPlanner train 集筛选       | 人工标注（小规模）|
| STAR/Demystify | ~1K（balanced difficulty）   | LLM 数据合成 + 教师轨迹 SFT          | 可大规模合成      |
| DeepTravel     | 未公开⚠（线上 query 缓存）   | 从 DiDi 真实用户 query 缓存 + sandbox 回放 | 真实数据闭环     |
| TripScore      | 4870 query（219 真实 free-form）| LLM 合成 + 真实用户 query 混合      | 中等              |
| ChinaTravel    | 1154 人类 query              | 真实人类 participant 输入            | 高人工成本        |
```

**takeaway**：`STAR 的 1K 平衡难度 + LLM 合成`是当前业界共识的 sweet spot[4]。**DeepTravel 不靠人工标注，靠真实用户 query 缓存**——这是 DiDi 才有的特权。我们项目没有这个特权（Hackathon 没真实用户）。

### 2.4 同行人 / 偏好 encode

3 篇论文里都没专门处理「同行人画像」⚠（DeepTravel / STAR / Planner-R1 的任务都是单人或抽象 group）。我们项目的 `companions[]` + `social_context` 是中文本地生活规划独特的软约束维度，需要在 observation 里以**自然语言文本**形式 prepend——与 critics_v2.py 的 _check_age_aware_duration / _check_social_context 保持同源知识库（防止 RL 与 critic 知识漂移）。

---

## 三、维度 2：reward shaping 与 GRPO/PPO 算法选型

### 3.1 算法选型谱系

```text
| 论文           | 主算法                           | reward 类型                        |
|----------------|----------------------------------|------------------------------------|
| Planner-R1     | GRPO + reward shaping            | dense process-level（8B 最优）     |
| DeepTravel     | GRPO 变体 + replay buffer⚠      | trajectory + turn 双层 verifier    |
| STAR/Demystify | GRPO 主推                        | CURRICULUM/MACRO/SUM/SUCCESS 4 档对比 |
| TripScore      | GRPO                             | 单一 unified scalar reward          |
| Single-turn    | GRPO（single-turn 版本）         | dense + verifiable                 |
```

**业界已经收敛到 GRPO**[2][3][4][6][7]——PPO 因为需要 critic network、DPO 因为只能做偏好对比，都被 GRPO 在 trip planning 这种「多约束、可验证」场景边缘化。

### 3.2 reward shaping 的层次设计（核心借鉴点）

DeepTravel 的双层 verifier[1][3]：

```text
| 层级               | 检查内容                              | 触发结果              |
|--------------------|---------------------------------------|-----------------------|
| Trajectory level   | 时空可行性（spatiotemporal feasibility）| 轨迹直接被过滤掉      |
| Turn level         | itinerary 与 Tool 响应的细节一致性     | 单 turn reward 加权    |
```

STAR 的 4 种 reward 设计[5]：

```text
| reward 设计 | 性质                  | 适用                       |
|-------------|-----------------------|----------------------------|
| SUCCESS     | sparse（只看 final pass）| 7B+ 大模型勉强能用          |
| MACRO       | semi-sparse（macro 约束）| 平衡 in-domain + OOD（推荐）|
| SUM         | dense（细粒度全加）      | 7B in-domain 最强但 OOD 掉  |
| CURRICULUM  | progressive 密 → 疏    | 1.5B / 3B 小模型最佳        |
```

**结论 1**：dense reward 高 in-domain 但 alignment tax 大；MACRO（半稀疏 macro 约束）是最稳的工程选择 [5]。

**结论 2**：reward 与模型尺寸耦合——小模型（≤3B）非 staged reward 不可，大模型（7B+）可以靠 dense SUM 快速收敛但泛化掉。

### 3.3 与 Planner-R1 reward 设计的差异（一句话总结，不展开）

Planner-R1 的 reward 是「直接接 TravelPlanner 评估器输出」（Agent 4 已覆盖），DeepTravel 的 reward 是「自训双层 verifier」——DeepTravel 多了「Tool 响应一致性」这个维度（检查 agent 是否凭空捏造 Tool 没返回的 POI）[3]。这是 trip planning RL 落地必须解决的「hallucination 防御」问题，也是 DeepTravel hallucination 率从 50% 降到 <20% 的关键 [3]。

### 3.4 reward hacking 案例

DeepTravel 论文未直接报告 reward hacking 案例（abstract / liner review 中均未提及）⚠[1][3]。但从 STAR 的发现「SUM reward 让 7B 在 in-domain 最强、OOD 大幅退化」[5]——可推断这是一种**软性 reward hacking**：模型学会了 game in-domain 评估器的细粒度规则，但对其他 query 失去泛化。这与「LLM-as-judge 被反向 game」是同一类问题。

`TripScore` 也明确点了「LLM-as-judge baseline 不如 unified scalar reward」[6]——侧面说明「reward signal 越透明、越规则化，越难被 hacking」。

---

## 四、维度 3：tool-use 路径下的 RL agent 行为

### 4.1 Tool 失败时 agent 行为

DeepTravel **靠 sandbox 把这个问题绕过了**[1][3]——sandbox 是缓存数据，不存在真实 API 失败。这个设计选择本身就是一个重要信号：**RL agent 在「环境稳定」假设下才能稳定训练**。

STAR/Demystify 的 RQ6 专门做了「随机 Tool 失败注入」对照实验[5]：得出「环境稳定性是 RL 训练能不能成的硬约束」结论。也就是说，**真实 API（不稳定）训不出 stable policy；必须先建 sandbox**。

我们项目的 mock_data 层天然就是 sandbox（不接真实美团 API）✅——这是个利好。但晌午局当前 mock 数据的「失败案例」（餐厅满座 / 门票售罄）是 demo 评分项埋点[项目 AGENTS.md §3.4]，**与训练时的「环境稳定性」假设是冲突的**——训练阶段需要 stable success/fail signal，演示阶段需要可控失败。如果走 RL 路径，需要分离两套数据。

### 4.2 上下文管理（30 步 trajectory cap）

⚠ 论文没直接给 trajectory cap 数字。从 STAR 引文「complex multi-turn environments」+ DeepTravel「multi-step reasoning」可以推断是 10-30 turn 量级 [1][4]。RQ4 中提到「average interaction turns」是训练监控指标 [3]，说明确实存在 turn 上限。

Tool response 喂回 context 的压缩策略：DeepTravel 没明说，但「sandbox 缓存」意味着 Tool response 是格式可控的小 JSON，不会爆 context；STAR 也没直接讨论。社区博客 [10] 给出「retry storm」反模式——每次 retry 都把全 context 喂回 LLM 烧 token。这与「replan don't retry」哲学一致。

### 4.3 emergent behavior 报告（论文未深挖⚠）

DeepTravel 论文没专门报告「agent 第 1 步通常调什么 Tool / 怎么收尾」这类细粒度 emergent behavior⚠——它的 user study 维度集中在 user intention understanding / itinerary completeness / feasibility / clarity / hallucination reduction[3]。这是一个比较遗憾的盲点。

但 hallucination 率从 50% → <20%[3] 强烈暗示一个 emergent behavior：**RL 训练后的 agent 学会「调 Tool 验证再回答」而不是「凭印象编」**——这与我们项目 critic 的核心使命（防 LLM 在 5 段段集合上幻觉）方向一致。

### 4.3.5 emergent behavior 推断（论文未深挖部分的工程外推）⚠

虽然 DeepTravel 论文没显式列出 agent 学到的策略层面行为，但从「hallucination 率从 50% 降到 <20%」+「user study 维度提及 itinerary completeness 显著上升」可以反推出 3 条 emergent 行为⚠：

1. **Tool 选择从「prompt 教什么调什么」转为「先广撒网再聚焦」**——RL agent 在初期 turn 倾向于多调几个查询类 Tool 把候选池扩大，再在中后期 turn 用反思 turn 收敛。这与社区博客 [10] 提到的「探索轮 + 利用轮」分离模式吻合。
2. **失败恢复从 retry 变 replan**——sandbox 缓存避免了真实 API 失败，但 turn-level verifier 会惩罚「调出来的 Tool 结果与 itinerary 不符」，这隐式训练 agent 在「Tool 返回与已规划方案冲突时」选择**改方案**而不是**改 Tool 调用**。
3. **收尾验证 turn 形成稳定模式**——RQ4 的「average interaction turns」指标会受 turn-level verifier 引导收敛到「最少必要 turn 数」，避免 agent 在已 satisfy 所有约束后还继续调用 Tool 浪费 token。这是 RL 微调相比朴素 ReAct 的关键效率优势。

### 4.4 RL agent vs ReAct 的优势具象

```text
| 行为             | 传统 ReAct            | RL 微调 agent（DeepTravel）        |
|------------------|-----------------------|------------------------------------|
| Tool 选择         | prompt-engineering 决 | 自学（学到 latent 选择策略）         |
| 失败恢复          | 程序化 retry / replan  | 内化（无显式 retry loop）           |
| Hallucination 防御 | LLM-as-critic 二轮     | RL reward 直接惩罚（hallucination 50%→<20%）|
| 跨场景泛化        | 取决于 prompt 设计     | 取决于训练数据多样性（OOD 风险大）  |
```

**关键认知**：RL agent 的优势核心是「**hallucination 内化为低概率事件**」（reward 直接惩罚），但代价是**整个 graph 的可解释性下降**——critic 不再是显式节点，而是被吸收进了模型权重。对我们这种「评委要看决策链路」的 hackathon 项目，这个 trade-off 严重不利（详见 §维度 4 + 陷阱 Q3）。

---

## 五、维度 4：产品级落地可行性（核心交付）

### 5.1 训练数据：从 0 到能用要多少

```text
| 路径                                    | query 数         | 标注成本估算                |
|-----------------------------------------|------------------|----------------------------|
| Planner-R1 模式（小数据 + reward shaping）| 180             | 3 人 × 2 天 = 6 人天⚠       |
| STAR sweet spot                         | ~1K（合成）      | LLM 合成（GPT-4o 调用 ~$200⚠）|
| TripScore 中等数据集                     | 4870            | 真实用户混合（不可获得）    |
| DeepTravel（DiDi 线上 query）            | 大规模未公开⚠   | 真实业务闭环（不可获得）    |
```

**结论**：纯人工标注 180 条 ~ 6 人天可承受；STAR 1K 合成 ~ 1 人天 + 200 美刀 GPU 资源⚠。但这只是「有数据」，不是「数据质量过关」——difficulty 平衡 + feasibility 校验本身就是 STAR pipeline 的硬骨头[4][5]。

### 5.2 训练算力 / 时长 / 模型选型

```text
| 路径               | 模型       | GPU 估算                       | 训练时长           |
|--------------------|------------|--------------------------------|--------------------|
| Single-turn GRPO   | 1.5B       | 单 H100 / A100 80G 单机          | 数小时～1 天⚠       |
| Planner-R1         | 8B         | 「3.5× 更省算力」 vs 32B [9]      | 论文未公开具体小时数⚠|
| DeepTravel         | 32B        | 多机多卡（DiDi 工业级）           | 未公开⚠            |
| STAR cookbook      | 1.5B-7B    | 1K samples × multi-epoch GRPO    | 数十小时～数百 GPU·hour⚠|
```

参考 Open Deep Research 教程（同 GRPO 量级训练）[10]：~30 小时训练 + ~$350 总成本（8B 模型，单 H100）。trip planning RL 训练的 ROM ⚠：**1 张 H100 + 30-50 小时 + ~$500，是单人级可承受的最低门槛**。

### 5.3 推理成本：本地部署 vs API

RL 微调过的 8B 模型不能继续走 DeepSeek API，必须**自部署**[项目当前主选 D1 = DeepSeek-V3 API]——这意味着推理基础设施重做：

```text
| 选项               | 延迟             | 成本               | Hackathon 适配 |
|--------------------|------------------|--------------------|----------------|
| 自部署 8B Qwen3     | 200-800ms / step  | ~$0.5-1/小时 GPU  | 需运维投入      |
| 自部署 32B Qwen3    | 500ms-2s / step   | ~$2-4/小时 GPU    | 重，几乎不可行  |
| 现 DeepSeek API    | ~1s / step（含网络）| 按 token 计费     | 当前主路径，省事|
```

**结论**：走 RL 路径意味着完全替换主线 LLM 推理路径——不仅训练成本高，**推理路径切换是更大的工程改造**。

### 5.4 Sim-to-real gap

DeepTravel 的处理是「sandbox 缓存真实 API 数据」[1][3]——sandbox 与真实 API 的差异主要在 QPS limit 和 inconsistent output。论文显式指出「inconsistent output」是真实 API 的痛点[1]。

**多脏算够脏？** ⚠论文没给具体污染率。从「real-world inconsistent output」+「user study hallucination 50%」可外推：**真实 trip planning API 的字段完整率 < 80%、跨调用一致性 < 90%** ⚠——这正是我们 mock_data 应该模拟的污染密度（提醒 spec C 设计 pitfalls.md）。

晌午局 mock_data 的字段密度（详见 `docs/01-requirements/演示场景集.md` 与 `mock_data/`）已经接近这个污染密度，**但 sandbox 与 demo 双重身份会冲突**：训练阶段要 stable，演示阶段要埋彩蛋（满座 / 售罄）。这是后面陷阱 Q2 / Q4 的核心论证。

### 5.5 中文 trip planning 训练数据可获得性

```text
| 数据集               | 域                        | 中文 / 跨城 / 半日适配度         |
|----------------------|---------------------------|----------------------------------|
| ChinaTravel [8]       | 中文 + 多日 + 多城         | 中文✓ + 半日✗ + 单城内 POI✗      |
| TripScore real-form [6]| 4870 query（含 219 free-form）| 中文性未明示⚠ + 半日适配性未明示⚠|
| TravelPlanner [Agent 4 已覆盖]| 英文 + 多日跨城         | 中文✗                          |
```

**结论**：**没有现成的「中文 + 半日 + 单城」trip planning 数据集**。如果走 RL 路径，必须自己合成——这又是个 6+ 人天的活，且我们没有真实业务数据闭环（DiDi 优势）。

---

## 六、陷阱清单（5 题必答）

### Q1：Hackathon 1 个月时间盒下 RL 路径能否落地？

**结论：不能落地 end-to-end RL，但可吸收 RL 思想做 reward shaping 借鉴**。

```text
| 阶段           | 最小代价                   | 实际可承受？        |
|----------------|----------------------------|---------------------|
| 数据合成        | 1K query × LLM 合成 ≈ 1-2 人天 + ~$200 GPT-4o 费 | 可⚠              |
| 模型训练        | 8B Qwen3 + GRPO + 1K query：1 H100 × 30-50h ≈ $500 | 可（GPU 是瓶颈）  |
| reward 设计 + 调试| critic 改 dense reward + 联调：2-3 人天 | 可                |
| 评估对齐        | 与现有 critic 比对、A/B 测：2-3 人天 | 可                |
| 自部署推理       | 部署 8B + Streaming API：3-5 人天 + 长期 GPU 成本 | **不可承受**       |
| 总计            | 9-13 人天 + GPU 成本 + 长期推理 GPU | 时间够、推理路径替换不够|
```

时间盒下的关键瓶颈不是「训练能不能跑」，而是「**训出来的模型怎么部署到现有 SSE 流式后端**」——把 DeepSeek API 替换为自部署 8B vLLM，意味着 backend 改造、运维、稳定性测试全部要重做。3 人团队 1 个月覆盖不动。

### Q2：DeepTravel 在「半日单城市 + mock 数据」场景下是否过度工程？

**结论：是，严重过度工程**。

RL 优势成立的 3 个前提：

```text
| RL 优势前提            | 晌午局符合度                                      |
|------------------------|---------------------------------------------------|
| 数据多（>1K 训练 query）| ✗ 我们 demo 仅 6-8 个场景，连 100 query 都没有      |
| 状态空间大              | ✗ 半日 5 段（home + POI + 餐 + POI + home），状态空间 < TravelPlanner 的 1/100 |
| 可在 sim 反复 rollout    | ✗ Hackathon 没训练流程，1 个月里没人有时间 maintain sandbox + 训练 pipeline |
```

DeepTravel 的所有重型组件——sandbox / 双层 verifier / replay buffer / 32B 模型——都是为「跨城多日 + DiDi 真实业务量」设计的。我们半日单城 5 段 + mock 数据 + 6-8 演示场景**用 LLM + 显式 critic 可以直接把 90% 收益拿到手**，剩下 10% 是 RL 的边际收益但代价是整个工程改造。

### Q3：spec C 是否应该走 RL 路径？

**结论：不走 RL 主路径，应走 LLM-Modulo（LLM 出方案 + sound critic 验）路径**。

trade-off 清单（按优先级）：

```text
| 维度          | RL 路径（DeepTravel 范式）       | LLM-Modulo（Agent 3 路径）    |
|---------------|----------------------------------|------------------------------|
| ROI 投入      | 高（数据+训练+推理重做）         | 低（critic 已存在，扩展即可） |
| 开发周期      | 30+ 人天（团队不可承受）          | 5-10 人天（可承受）           |
| 可维护性      | 模型权重黑盒，迭代靠重训          | 显式规则代码，单测可覆盖      |
| 演示可解释性   | 决策链路被吸收进权重，评委看不见  | 决策链路 = critic 反馈链，显式 |
| Hallucination 防御 | reward 内化（50%→<20%）       | critic 显式拦下，0 hallucination ✓|
| 可控的 emergent behavior | 弱（policy 是 black-box）   | 强（每条违规可追溯 ViolationCode）|
| 长期生产价值   | 高（真业务闭环时回报巨大）        | 中（达到一定水平后边际收益减）|
```

**关键判断**：评委评分项（AGENTS.md §3.1）「评委能否看到 Agent 决策过程」是 LLM-Modulo 强、RL 弱的维度——RL 主路径会**降低 demo 可见性**。Hackathon 应该坚定 LLM-Modulo，把 RL 思想中可借鉴的小点（reward shaping → critic severity 加权）吸收即可。

### Q4：现有 critics_v2 在 RL 范式下扮演什么角色？

读 critics_v2.py 后明确答案：**critics_v2 在 RL 范式下天然就是 reward signal source，但需要做两步改造**。

**现状**[critics_v2.py 一手阅读]：

```text
| 当前接口                               | RL 改造方向                          |
|----------------------------------------|--------------------------------------|
| validate_itinerary() → list[Violation] | 已天然是 「scorer」结构              |
| Violation.severity (CRITICAL/WARNING)  | 直接映射 reward 权重（CRITICAL = -1.0, WARNING = -0.2）⚠ |
| ViolationCode（10 类）                  | 每个 code 是一个 dense reward 维度    |
| format_violations_for_llm() → str       | RL 范式下不再需要——LLM 不读 prompt 而是吃 reward |
```

**第 1 步改造**：Violation 列表 → scalar reward。最简单的转换：

```text
reward = Σ_i  weight[code_i] × (-1 if severity_i == CRITICAL else -0.2)
```

权重 weight[code] 可参考 STAR 的 MACRO reward 思路[5]——对「macro 维度」（INVARIANT_BROKEN / NODES_INCOMPLETE / TIMELINE_INCONSISTENT）给大权重，对「细粒度」（DIETARY_VIOLATION / DISTANCE_EXCEEDED warning）给小权重，避免 dense SUM 导致的 OOD alignment tax。

**第 2 步改造**：critics_v2 当前在 graph 里是「pass / fail 二分」+「format prompt 喂 LLM」（critics_v2.py:875-891 format_violations_for_llm）。RL 范式下需要：

- 保留 validate_itinerary 拿 violation 列表
- 新增 `compute_reward(violations) → float` 函数（替代 format_violations_for_llm）
- 训练时把 reward 喂 GRPO；推理时 critic 仍可作 trace（双轨）

**关键认知**：RL 与 LLM-Modulo **共享 critics_v2 这一层 single source of truth**。如果将来有时间做 RL 追加路径，**不需要重写 critic，只是给现有 critic 加一个 `to_reward()` 方法**。这是 RL 思想中**唯一对我们项目有立竿见影价值的借鉴点**。

### Q5：现有 graph 拓扑能否被 RL agent 替代？replan loop 是否被内化？

读 `build.py` + `replan.py` 后明确答案：**不能完全替代；可被部分内化但要付惨痛代价**。

**现有拓扑**[build.py 一手阅读]：

```text
START → router (chitchat/refiner/intent)
     → execute (3 个 worker 并行 → execute_collect)
     → planner → assemble → critic
        → narrate → END
        → replan_router → planner / ils_replan
                                → narrate → END
```

**RL agent 替代后的拓扑（DeepTravel 范式外推）**⚠：

```text
START → router (chitchat → END / 其余 → rl_agent_node)
     → rl_agent_node (内部 ReAct loop：调 Tool / 反思 / 修正)
     → narrate → END
```

**replan loop 被「内化」的代价**：

1. **可见性消失**：replan.py 中 `FallbackHop` chain（llm_first → llm_backprompt → ils → rule → give_up，共 5 跳）全部丢失——这是评委看「Agent 决策过程」的核心证据。
2. **死循环防御消失**：build.py:_route_after_ils 注释明确写「ILS 自身不解决 commute_infeasible … 防 ILS 死循环 P1」。RL agent 没有 _MAX_TOTAL_RETRIES 硬上限——policy 自己学到「最多重试 N 次」，但「N 是多少」是不可控的。replan.py:_MAX_TOTAL_RETRIES=4 是项目踩坑后才加的硬上限（pitfalls P1-2026-05-23）；RL 内化意味着失去这个保险。
3. **fallback 链兼容性**：replan.py 当前的 ILS / rule / give_up 兜底是「零成本插入」（仅 conditional edge）；RL agent 范式下要做兜底就要在 rl_agent_node 外再裹一层 graph，反而比当前更复杂。

**结论**：RL 替代是「**用模型权重黑盒换掉 graph 显式拓扑**」——这对生产系统迭代是好事（一次训练长期受益），对 hackathon demo 是坏事（评委看不见 Agent 行为）。**spec C 重构如果走 RL，要从 build.py 顶层重写 70%+ 代码**，不只是加节点。

---

## 七、关键洞察 5 条

1. **DeepTravel 的核心创新是 sandbox + 双层 verifier 不是 RL 算法本身**——RL 算法层（GRPO）已收敛为业界共识，差异化全在「环境怎么搭、reward 怎么设」。
2. **STAR 的 ~1K query 平衡难度** 是当前业界 RL trip planning 训练数据的 sweet spot，不需要 100K 量级。
3. **reward 越 dense 越 in-domain 强、越 OOD 弱**（alignment tax），MACRO 半稀疏 reward 是 OOD 鲁棒性最佳选择。
4. **critics_v2 与 RL reward 是同构的**——9+1 类 ViolationCode + Severity 加权和就是 dense reward 函数，迁移成本极低。
5. **RL agent 内化 replan loop 的代价是「评委看不见决策链路」**——在 demo 导向 hackathon 是致命的（违反 AGENTS.md §3.1）。

## 八、对本项目的可复用性评分（0-10）

```text
| 子项                                            | 评分 | 理由                                        |
|-------------------------------------------------|------|---------------------------------------------|
| 整体复用（直接走 DeepTravel 路径）                 | 1/10 | 训练 + 推理改造代价远超 1 个月时间盒          |
| 仅 reward 设计层借鉴（critic.severity → reward 权重）| 7/10 | 单点改造、立竿见影；但严格说是 LLM-Modulo 的扩展，不是 RL 路径|
| 仅训练数据合成方法借鉴                            | 5/10 | STAR 的 LLM 数据合成可在 spec C 后期评估，但 hackathon 阶段优先级低 |
| 仅 sandbox 思想借鉴（mock_data 双轨：训练 stable + demo 埋点）| 6/10 | 我们 mock_data 已天然是 sandbox，只需把「埋点失败」与「演示成功路径」分离即可，工程量小 |
| 仅 hallucination 防御认知借鉴                     | 8/10 | DeepTravel 50%→<20% 数据强烈说明「Tool 响应一致性」是 trip planning hallucination 的核心来源，验证我们 critics_v2 的 RESTAURANT_FULL_UNRESOLVED 思路对路 |
| 综合判断                                          | **3/10** | RL 路径整体不可行，但思想层面有 1-2 个高 ROI 借鉴点 |
```

## 九、我的建议（≤200 字）

spec C 不走 RL 主路径。坚定 LLM-Modulo（Agent 3 路径）作为主架构，把 RL 思想中以下 3 个点吸收：(1) `critics_v2.py` 加 `to_reward()` 方法做单点改造，让未来扩展为 RL 路径时不需要重写 critic；(2) STAR 的 MACRO reward 思路启发 critics_v2 的 severity 权重设计——把 INVARIANT_BROKEN / NODES_INCOMPLETE / TIMELINE_INCONSISTENT 设为 hard-block，DIETARY_VIOLATION / DISTANCE_EXCEEDED 仅作 soft-warning；(3) DeepTravel hallucination 50%→<20% 数据强化我们对「Tool 响应一致性」是 trip planning 核心防线的判断，应在 critic 中加一类 `TOOL_RESPONSE_INCONSISTENCY`（如 LLM 选了 mock 中不存在的 POI ID）。**不动 graph 拓扑、不动模型选型**。

## 十、与现有 graph 的衔接细节

### 10.1 critics_v2 加 `to_reward()`（单文件改动）

```text
| 文件                                         | 改动锚点                                      | 量级        |
|----------------------------------------------|------------------------------------------------|-------------|
| backend/agent/planning/critic/critics_v2.py | 新增 compute_reward(violations) -> float      | +50 行      |
| backend/agent/planning/critic/critics_v2.py | __all__ 加 "compute_reward"                    | +1 行       |
| backend/agent/planning/critic/critics_v2.py | 严重度权重表（CRITICAL=1.0, WARNING=0.2 默认）  | 单测覆盖    |
| 不动 critic_node / replan / build.py        | RL 路径未启用前 reward 函数仅做副作用 trace      | 0 风险      |
```

### 10.2 ViolationCode 加 TOOL_RESPONSE_INCONSISTENCY（防 hallucination）

```text
| 文件                                                  | 改动锚点                                              |
|-------------------------------------------------------|-------------------------------------------------------|
| backend/agent/planning/critic/critics_v2.py:ViolationCode | 加枚举值 TOOL_RESPONSE_INCONSISTENCY = "tool_response_inconsistency" |
| backend/agent/planning/critic/critics_v2.py        | 加 _check_tool_consistency(itinerary, tool_results) → 检查 itinerary 中所有 target_id 必须出现在 tool_results 的 candidate 列表里 |
| backend/agent/graph/state.py                       | AgentState 已有 tool_results 字段（execute_collect 写入），无需新增 |
| backend/agent/graph/nodes/critic.py                | 在 validate_itinerary 调用处新增 tool_results 参数透传    |
```

这是从 DeepTravel hallucination 防御的核心借鉴点——**RL 是用 reward 内化的，我们用显式 critic 做同样的事**。预计改动 +80 行 + 单测 6-8 个 case。

### 10.3 mock_data 双轨分离（演示与训练沙盒解耦）

如果将来真要做 RL 实验：

```text
| 文件                       | 改动                                          |
|----------------------------|-----------------------------------------------|
| mock_data/restaurants.json | 演示数据（含 17:00 满座等彩蛋）保留           |
| mock_data/restaurants_train.json | 训练沙盒（去掉所有彩蛋，纯 stable success） |
| backend/data/loader.py     | load_restaurants(mode="demo"/"train") 双模式  |
```

这是 STAR / DeepTravel 的「环境稳定性」结论 [4][5] 在我们项目的对应工程化。**hackathon 阶段不做，仅记入 spec C design.md 的「未来 RL 实验」段**。

---

## 十一、附录：阅读笔记

### 11.1 完整阅读的一手资料

```text
| 来源                                                      | 阅读深度        |
|-----------------------------------------------------------|-----------------|
| arxiv.org/abs/2509.21842 (DeepTravel)                     | 完整 abstract + 实验结论 |
| huggingface.co/papers/2509.21842 (HF 摘要 + librarian-bot) | 完整            |
| liner.com/review/deeptravel-...                           | 完整 RQ1-RQ6     |
| arxiv.org/abs/2603.21972 (STAR/Demystifying RL)           | 完整 abstract + 7 takeaway |
| liner.com/review/demystifying-...                          | 完整 RQ1-RQ6 + STAR pipeline |
| arxiv.org/abs/2510.09011 (TripScore)                      | 完整 abstract    |
| arxiv.org/abs/2509.20616 (Single-turn GRPO)               | 完整 abstract + 1.5B/70% 数字 |
| arxiv.org/abs/2412.13682 (ChinaTravel ICLR 2026)          | 完整 abstract + DSL 描述 |
| arxiv.org/abs/2509.25779 (Planner-R1，仅作 Agent 4 对照)   | abstract + 56.9% 数字 |
| 项目 backend/agent/graph/build.py                         | 完整阅读        |
| 项目 backend/agent/graph/nodes/replan.py                  | 完整阅读        |
| 项目 backend/agent/planning/critic/critics_v2.py          | 完整阅读（含 format_violations_for_llm） |
```

二手 / 工程化补充：tianpan.co/blog（retry storm + replan don't retry）、towardsdatascience.com/agentic-rag-failure-modes、Open Deep Research 教程（GRPO 训练成本数字）。

### 11.2 我认为的 3 个最重要洞察

1. **DeepTravel 的双层 verifier 启发我们：critic 不只是 pass/fail 验证器，它本质是 reward signal source**——critics_v2.py 加 to_reward 方法是任何未来 RL 实验的零成本前提。这是本次调研唯一对项目立竿见影的借鉴。

2. **STAR 的 reward 4 档对比 + alignment tax 现象**告诉我们：「越细粒度的反馈越好」是个伪命题，**MACRO 半稀疏 reward 才是 OOD 鲁棒性最佳**。这与 critics_v2 的 severity 二元（CRITICAL/WARNING）天然契合——不要为了「细粒度」把 9+1 类 ViolationCode 拆成 30+ 类。

3. **DeepTravel 的 hallucination 50%→<20% 数据**强烈验证：trip planning 的核心 hallucination 模式不是「胡编 POI 描述」而是「Tool 没返回的 POI/餐厅被 LLM 在 itinerary 里使用」。这是我们 critics_v2 当前缺的一类 ViolationCode（TOOL_RESPONSE_INCONSISTENCY），强烈建议在 spec C design 中补上——这是 RL 路径以外、用显式 critic 也能拿到 hallucination 防御红利的关键点。

---

## 十一·五、与本批次其他调研路径的横向对照（可选阅读）

⚠ 本节由 Agent 5 自身基于公开范式做横向定位，不读其他 sub-agent 报告（防偏见纪律）；具体路径标签如有出入以正式报告为准。

```text
| 维度                | RL 路径（本报告）            | LLM-Modulo（Agent 3）         | Itinera/经典规划（Agent 2）  |
|---------------------|------------------------------|--------------------------------|------------------------------|
| 主算法              | GRPO + reward shaping        | LLM 出方案 + sound critic 验   | OR-tools / heuristic         |
| 现有 critic 能否复用 | 改 to_reward()，1 单元改造    | 直接复用，0 改造               | 部分复用（约束转线性）        |
| Hackathon 时间盒    | 不可承受（30+ 人天）          | 可承受（5-10 人天）             | 中等（10-15 人天）            |
| Demo 可见性         | 弱（决策链路黑盒）            | 强（critic 链路显式）          | 中（OR-tools 解释性弱）       |
| 中文场景适配        | 训练数据稀缺（仅 ChinaTravel） | 与语言无关                     | 与语言无关                   |
| 长期生产价值        | 高（业务闭环时）              | 中（critic 演进有边界）         | 低（约束变化难维护）          |
| 推荐用途            | spec C 仅做思想借鉴           | spec C 主路径                   | spec C 兜底（已落地为 ILS）  |
```

读者在阅读完 Agent 1-5 五份报告后应得出一致结论：**spec C 主路径走 LLM-Modulo + critic 增强；RL 与经典规划仅作思想借鉴 / 兜底**。本报告与该论点一致。

## 十二、调研结论汇总

```text
| 问题                                  | 回答                              |
|---------------------------------------|-----------------------------------|
| DeepTravel 等 RL 路径是否复用？        | 不复用整体路径                    |
| reward 设计是否借鉴？                  | 借鉴：severity 加权 → reward      |
| sandbox 思想是否借鉴？                 | 部分借鉴：mock_data 双轨提案      |
| hallucination 防御认知是否借鉴？       | **强烈借鉴**：加 TOOL_RESPONSE_INCONSISTENCY |
| 是否应在 spec C 主路径走 RL？          | **否**——LLM-Modulo（Agent 3）+ critic 增强即可 |
| Hackathon 1 个月 + 3 人时间盒下可行性？ | **不可行**——推理路径替换不可承受 |
| 中长期价值（项目转生产时）？           | RL 思想可作为 v2 演进路线，spec C 仅留挂钩点 |
```
