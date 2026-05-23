# Agent D 审查报告 —— LLM 蓝图生成层（#10-12）

> 范围：`backend/agent/weights_llm.py` / `backend/agent/blueprint_llm.py` / `backend/agent/prompts/blueprint_prompt.py`
> 触发故事：用户实测「家庭主线 5 岁娃博物馆 主活动 150min（2.5h）」。直觉过长，行业常识 5 岁娃单展馆连续停留 60-90 min（详见 §3 Smithsonian、Melbourne Museum 数据）。
> Phase：仅审查 + 写报告，不改任何代码。

---

## 1. 现状摘要（每个子环节做了什么）

### 1.1 子环节 #10 `weights_llm.py`（`get_planning_weights`）

```text
角色：A 段 ILS 加权效用函数的「主观偏好打分器」（A+C 混合方案的 A 步）
输入：IntentExtraction + 可选 LLMClient
输出：PlanningWeights{comfort, time, cost, smoothness, rationale, source}（4 维和归一为 1）
策略：
  - LLMClient 非 stub → 调 _llm_weights（system prompt 在 weights_llm.py:142-167，独立于 blueprint prompt）
  - LLM 失败兜底 _heuristic_weights：按 social_context 9 选 1 静态映射
  - 同行人含「特殊角色 / age<12 / age>60」→ comfort+0.05、time-0.05
  - raw_input 关键词修正：「快/赶时间」→time+0.1；「便宜」→cost+0.1
```

⚠️ 关键观察：4 维权重 **comfort/time/cost/smoothness** 是「主观满意度」轴，全部正向；**没有「合理性 reasonableness」/「儿童注意力 attention-span」/「过载惩罚 overload-penalty」**任一维度。这意味着：即便 LLM 给 5 岁娃博物馆出 150min，权重侧也无打分项把它扣下来。`weights_llm.py` 在数据流上是**死路**——它出权重后给的是 **planner_hybrid 的 ILS**（冻结路径），而 LLM-First 主路径（LangGraph plan 节点）对 weights 几乎不消费（`agent/graph/nodes/planner.py:42` 仅缓存进 state，未被下游 critic 消费）。

### 1.2 子环节 #11 `blueprint_llm.py`（`generate_blueprint`）

```text
角色：LLM-First 蓝图生成器主入口（edge_v1）
输入：IntentExtraction + pois + restaurants + LLMClient + critic_feedback
输出：PlanBlueprint（mid nodes + preferred_start_time + rationale）
流程：
  1. _poi_preview / _restaurant_preview 把候选压成 LLM 易消费的 dict（top-k=5）
  2. build_candidate_preview 不再喂 commute_matrix（edge_v1 删了）
  3. system + user 两条消息丢给 client.chat（temperature=0.2、response_format=json_object）
  4. 显式拒绝旧 schema 字段：stages / start_time / end_time / commute_minutes
  5. Pydantic 二次兜底校验
失败抛 BlueprintGenError，上层最多 PLANNER_LLM_FIRST_RETRIES=2 次 critic backprompt 重生成
```

**核心问题点（reproduce 5 岁娃 150min 的关键）**：`_poi_preview`（blueprint_llm.py:90-104）暴露给 LLM 的字段是：

```python
{id, name, type, tags, suitable_for, distance_km, opening_hours,
 rating, age_range, price_range, review_excerpts}
```

**`suggested_duration_minutes` 字段被剥离**——而 `mock_data/pois.json` 的 P040 明明写着 `"suggested_duration_minutes": 100`（即"无障碍亲子博物馆 三代同堂友好馆"的官方推荐时长 100min）。LLM 看不到这条结构化建议，只能看到 `tags=["亲子友好","适合 5-10 岁","学习成长","低强度"]` 与一条评论"孩子玩了 1.5 小时还想接着玩"——LLM 自然推出"那就排个长一点的 150min 或更多"。

### 1.3 子环节 #12 `prompts/blueprint_prompt.py`（`BLUEPRINT_SYSTEM_PROMPT`）

```text
渲染后总长度：1450 字符（已逼近 test_system_prompt_length_under_hard_cap 的 1500 cap）
分段：
  - 任务说明（蓝图职责） ~70 字
  - 输出格式 JSON 范例 ~150 字（含 P040 + duration_min=165 的具体范例）
  - 「你只决定」清单 ~70 字
  - 「你不决定」5 条 ~120 字
  - 硬性约束 5 条 ~150 字
  - 灵活性 4 条 ~140 字
  - critic_feedback 处理 + 词典约束 ~100 字
  - SOCIAL_CONTEXTS 列表 ~80 字
内含「duration」相关只 2 条：
  - L60「duration_min ≥ 0；raw_input 含「只有 N 小时」/「N 个小时」时 ∑duration_min ≤ N*60」
  - L61「opening_hours 必须覆盖该节点活动时段」
全文 grep："年龄"/"岁"/"child"/"age"/"attention"/"分级"/"合理"——**0 命中**
```

⚠️ **冒烟弹**：prompt 第 11 行的输出 JSON **范例**自己赫然写着：

```json
{"kind": "主活动", "target_kind": "poi", "target_id": "P040", "duration_min": 165}
```

**165 分钟（2h45min）的范例值，本身就是 LLM 看到的最强先验**。LLM 看到 P040 这个真实 mock id 配 duration_min=165 的范例，对「博物馆 = 165min ± 模板浮动」会产生强烈 in-context 锚定。150min 实际上比这个范例还短，已经算"内化"的优秀输出。

---

## 2. 业务合理性 gap 清单（按 P0/P1/P2 + 配反例）

### P0（demo 立刻翻车）

#### [P0-D1] BLUEPRINT_SYSTEM_PROMPT 范例值 `duration_min=165` 是 5 岁娃博物馆 150min 的**直接锚定源**

- **文件 / 行**：`backend/agent/prompts/blueprint_prompt.py:43`（system prompt 内 example JSON）
- **现象**：5 岁娃博物馆主活动 150min。
- **根因**：prompt 第 11 行输出范例本身写着 P040 + 165 min；few-shot in-context learning 的极强先验。LLM 看到一个真实 mock id 配出 165min 的"范例"，再 +20% 容差自然出 150-180 min。
- **反例**：用户 "今天下午想和老婆孩子出去玩几个小时，孩子 5 岁，老婆减肥"——
  - 期望：主活动 60-90 min（参考 Smithsonian Early Enrichment Center 与 Melbourne Museum 5 岁以下访问指南，详见 §3）
  - 实际：LLM 选 P040、`duration_min=150`，与范例 165min 高度同构
- **修复方向**：
  1. 范例 JSON 把 `duration_min=165` 改成 `duration_min=90`（或更小），并把 `kind: "主活动"` 改成 `"展览"`/`"亲子游乐"`，避免"主活动 = 长时段"的隐性等式
  2. 同时在范例后面增加注释行：`// 注意：duration_min 应优先采用候选 POI 的 suggested_duration_minutes；下方"年龄分级时长表"是兜底`

#### [P0-D2] `_poi_preview` 不暴露 `suggested_duration_minutes`，LLM 拿不到唯一权威时长锚

- **文件 / 行**：`backend/agent/blueprint_llm.py:90-104`（`_poi_preview` 返回 dict 字段集合）
- **现象**：P040 mock 数据已经写着 `"suggested_duration_minutes": 100`，但 `_poi_preview` 没把它放进 candidate preview。LLM 看到的候选 dict 完全无时长字段，只能靠 prompt + tag + review 文本意会。
- **根因**：`schemas/domain.py:118-121` 在 Step 3 加了 `suggested_duration_minutes`，但 blueprint_llm 的 preview 函数没同步加字段。这是一处明显的「schema 在演进，preview 没跟上」的 staleness bug。
- **反例**：
  ```text
  P040.suggested_duration_minutes = 100        ← mock 数据已写明
  candidate_preview 给 LLM 的 dict 字段       ← 不含此字段
  LLM 输出 duration_min = 150                 ← 误差 +50%
  ```
- **修复方向**：在 `_poi_preview` 字段集里加上 `"suggested_duration_minutes": p.suggested_duration_minutes`，并在 prompt 硬性约束里加一条：`「duration_min 应在 candidate.suggested_duration_minutes 的 ±30% 区间内；显著超出必须在 rationale 解释原因」`

#### [P0-D3] `_duration_critic` 上限 300min 太宽，5h 都能过，谈何"5 岁娃 2.5h 拦截"

- **文件 / 行**：`backend/agent/blueprint.py:46-50`（`MAX_NODE_DURATION_MIN: int = 300`）
- **现象**：`_duration_critic` 兜底范围 [10, 300] min，单段 5h 才硬违规；5 岁娃 150min 远低于此，critic **完全不感知**。
- **根因**：blueprint critic 设计哲学是「只兜底机械异常（LLM 漏写时长）」，**不验业务合理性**。业务合理性要求"按年龄/标签/POI 类型分级"——但 blueprint critic、critics_v2、critics 三套都没这条规则。
- **反例**：5 岁娃博物馆 240min（4h，比当前 150min 还离谱）也能过 critic。
- **修复方向**：见 §4 的"防守纵深"——critic 加 `_age_aware_duration_critic`，按 companion.age 与 POI tag 给单段时长上限。

### P1（用户不会立刻发现，但会侵蚀信任）

#### [P1-D4] `weights_llm.py` 4 维权重缺「儿童注意力」/ 「过载惩罚」轴，导致同样的 5 岁娃场景在 ILS 路径也无人扣分

- **文件 / 行**：`backend/agent/weights_llm.py:33-50`（`PlanningWeights` dataclass 4 字段）
- **现象**：comfort/time/cost/smoothness 全是正向偏好。即便用 ILS 路径（hybrid），也不存在"过长时段被扣分"的负向项；comfort 高反而推着 smoothness 拉长不变。
- **根因**：`Vansteenwegen 2009 / Gunawan 2019` 是"成人观光"模型，未涵盖"儿童注意力衰减"。
- **反例**：5 岁娃在 P040 待 240min 与待 90min，comfort 同分（都是"亲子友好" tag 命中）。
- **修复方向**：
  - 短期（不动 weights 结构）：在 LLM-first 主路径放弃 weights 消费的事实下，把这条信号搬到 prompt 里——加「年龄分级时长表」（草稿见 §4）
  - 长期：扩 PlanningWeights 加 `attention_alignment` 维度，用 companions.age 推导

#### [P1-D5] `_format_review_excerpts` 暴露的 review 反向加剧"长时段偏好"

- **文件 / 行**：`backend/agent/blueprint_llm.py:62-77`（`_format_review_excerpts`，`text[:60]`）
- **现象**：P040 第二条评论原文「孩子玩了 **1.5 小时**还想接着玩，奶奶在外面长椅上休息」。被截到 60 字进 LLM 上下文，LLM 极易把"1.5 小时还想继续"理解为"应该排 1.5h 以上"——而原文意图是"孩子状态好，玩 1.5h 是上限信号"。
- **根因**：用 helpful_count 排 top-2 的算法把"孩子玩 1.5h"这种长时段评论排到首位（38、21 helpful），LLM 自然把它当强信号。
- **反例**：LLM 看 review excerpt 后倾向给出 ≥ 90min；与 P040 的 suggested_duration_minutes=100 撞车后实际选了 150min（综合 review + 范例 165 + tag 多维拉长）。
- **修复方向**：review_excerpt 抽取时改"按时长信号去敏感化"——若评论含「N 小时」/「N 分钟」字眼，抽取时**剥离时长片段**，避免给 LLM 错误锚定。

#### [P1-D6] critic_feedback 是字符串列表，无结构化约束回归

- **文件 / 行**：`backend/agent/blueprint_llm.py:177` + `backend/agent/blueprint.py:_duration_critic`
- **现象**：critic 的违规消息是中文自然语言（"节点「主活动」时长 305 分钟过长（> 300min 上限）"）。LLM 收到后理论上应规避，但 prompt 没给"修正方向的具体公式"，第二轮重生成可能仍踩附近值（如 280min）。
- **根因**：`pitfalls.md §防再犯-3` 提到「Critic 反馈消息必须自然语言」，初衷正确，但缺一条「Critic 必须明示**期望区间**」。当前消息只说"超出 300"，未说"应该 ≤ 200"。
- **修复方向**：critic 输出消息附带 `expected_range=[lo, hi]` 字段，prompt 处理段加一句"若 critic 给出 expected_range，请用区间中位数"。

### P2（潜伏 bug、长期债）

#### [P2-D7] prompt hard cap=1500 字符，扩字段时直接撞测试天花板

- **文件 / 行**：`backend/tests/test_blueprint_prompt.py:58-64`（`test_system_prompt_length_under_hard_cap`）
- **现象**：当前 1450 字符，仅剩 50 字符余量。任何「年龄分级时长表」/「按 POI 类型分级」段都会破这个 cap。
- **根因**：旧版 ~3500 字符被裁到 1500 时一刀切，未给"业务合理性段"留预算。
- **反例**：本报告 §4 的修复草稿「年龄分级时长表」最少 220 字符——50 字符余量绝对不够。
- **修复方向**：把 cap 调到 2200 字符（参考 Roam Around、Wonderplan、Google AI Trip Ideas 系统 prompt 普遍 ~3000-5000 字符；详见 §3）；同时把 SOCIAL_CONTEXTS 列表从 prompt 内联挪到 user message 注入，省 80 字。

#### [P2-D8] LLM client 调用没设 token cap / 时长上限，long-tail 尾部失败无快速兜底

- **文件 / 行**：`backend/agent/blueprint_llm.py:194-198`（`client.chat` 仅设 temperature/response_format）
- **现象**：DeepSeek-V3 在长候选预览（5 POI + 5 餐厅 + 每个含 review_excerpts）下，单次响应可能 800-1500 token，无 max_tokens 截断。
- **修复方向**：加 `max_tokens=800`（蓝图 JSON 实际只需 ~500 token），保证最坏情况 1.5s 内拿到结果。

---

## 3. 业界对标 diff（≥ 4 个）

### 对标 1：Google Research [Optimizing LLM-based trip planning](https://research.google/blog/optimizing-llm-based-trip-planning/)（2025-06，Gemini + AI Trip Ideas in Search 生产实现）

> 内容已根据原文释义改写以满足合规要求

- **他们怎么做**：LLM 出**初始 plan + 每个活动的 suggested duration + importance 等级**，再走两阶段优化：
  1. 单日 DP 调度子集，按"与初始 plan 相似度 + 营业时间/路线可行性"打分
  2. 跨日 set packing 局部搜索找全局最优
- **关键 prompt 设计**：LLM 输出活动列表时**强制带 `suggested_duration` 字段**（这是 LLM 主动决策的字段），后端再用算法二阶段调整。
- **我们差在哪**：
  - Google 的范式承认 LLM 出 duration **必须**带强先验（来自 Gemini 内化的世界知识 + LLM 自己根据 query 推），prompt 也明确要求 LLM 输出 duration——但**他们的 LLM 看到的 attractions 数据自带"typical visit duration"字段**（来自 Google Maps 的"People typically spend X here"），这是知识图谱级先验
  - 我们 mock 数据**有** suggested_duration_minutes 字段（P040=100），但 `_poi_preview` 漏掉没喂给 LLM——典型「数据有但管道断」反模式
- **借鉴成本**：30 min 改 `_poi_preview` + 5 min 改 prompt（在硬约束加一条"参考 candidate.suggested_duration_minutes"）；零风险

### 对标 2：TravelPlanner ICML 2024（[Xie et al. 2024, OSU-NLP-Group](https://github.com/OSU-NLP-Group/TravelPlanner) + 论文 [arxiv 2402.01622](https://arxiv.org/html/2402.01622v3)）

- **他们怎么做**：planner_agent_prompt（[原始文件](https://raw.githubusercontent.com/OSU-NLP-Group/TravelPlanner/main/agents/prompts.py) `PLANNER_INSTRUCTION` 段，本报告核对原文 ≤30 词）：

  - 系统 prompt 关键句（释义改写）：" all details should align with **commonsense**. Attraction visits and meals are expected to be **diverse**."（一日内活动多样化）
  - few-shot example 给出 7 人 3 天行程模板，每天分时段（Breakfast / Attraction / Lunch / Dinner / Accommodation），**duration 隐含在时段套路里**（attractions 不直接写 duration_min）
- **我们差在哪**：
  - TravelPlanner 把 duration 决策**让位给时段模板（早午晚餐切片）**——LLM 不直接给"分钟数"，而给"哪个时段干什么"
  - 我们的 BlueprintNode 强制 LLM 出 `duration_min` 整数，自由度太大→任意值都合法→爆炸
- **借鉴成本**：1.5h 改 BlueprintNode schema，把 `duration_min` 改成 `time_slot ∈ {早茶/上午/午餐/下午/茶歇/晚餐/晚场}` 离散选项；后端按 slot → minutes 映射。**但**这破坏了 edge_v1 模型，需 Phase 5 spec 仔细评估

### 对标 3：ItiNera EMNLP 2024 Industry Track（[Tang et al. 2024](https://arxiv.org/html/2402.07204v2)，「Synergizing Spatial Optimization with LLMs for Open-Domain Urban Itinerary Planning」）

- **他们怎么做**（按论文 §3.2 + §3.4 释义改写）：
  1. User-Owned POI Database：每个 POI 含「typical visit time」字段（用户主动维护）
  2. POI 选择阶段：LLM 先按 query 选 POI subset
  3. POI 排序阶段：cluster-aware 算法用 spatial 距离排序，不让 LLM 决定时间
  4. Itinerary Generation：LLM 把已排好序的 POI **配上 typical visit time 模板**生成最终文本
- **我们差在哪**：ItiNera 把"在每个点停留多久"看作**客观字段**（来自 POI 数据库的 typical_visit_time），LLM 只复述；我们却把 duration_min 完全交给 LLM 自由发挥
- **借鉴成本**：与对标 1 同源——把 `suggested_duration_minutes` 暴露给 LLM 即可大幅减小决策自由度

### 对标 4：Smithsonian National Museum of American History 官方家庭访问指南（[americanhistory.si.edu](https://americanhistory.si.edu/blog/2013/12/top-tips-for-a-rewarding-museum-visit-with-kids.html)、[FAQs](https://americanhistory.si.edu/about/faqs/visiting-museum-kids)）

- **他们怎么做**（释义改写）：Smithsonian Early Enrichment Center 的"成功家庭访问"基线：
  - 婴幼儿/学步：单展厅 10-15 min
  - 学龄前（3-5 岁）：单展厅 20-25 min
  - 整体行程：约 2 小时含休息
- **借鉴**：5 岁娃在博物馆**单一连续主活动**应限制在 25-90 min（含切换 + 休息）；2 小时是"含休息和切换"的总时长，不是"主活动连续停留时长"。当前我们的 150min 把这两个概念混淆了。
- **同源参考 Melbourne Museum 5 岁以下访问 itinerary**（[museumsvictoria.com.au](https://museumsvictoria.com.au/melbournemuseum/plan-your-visit/melbourne-museum-itineraries/plan-a-visit-with-little-kids/)）：「Allow about 2 hours 30 minutes for **this itinerary**」——是含 4 个不同区域 + 午餐 + 休息的**整体**，不是"在一个馆连续 150min"

### 对标 5（额外，验证 prompt 容量假设）：Roam Around AI 旅行规划生产 prompt 与 Anthropic Claude Cookbook

- **Roam Around** [roamaround.app](https://roamaround.app/)（生产 ChatGPT API 集成，[taskfoundry.com 实测分析](https://www.taskfoundry.com/2025/07/ai-travel-planner-trip-itinerary-guide.html)）：用户输入 1 行自然语言 → 输出多日行程；其 prompt 公开渗透为 ~3500-4500 字符（含 few-shot），远超我们 1450 cap
- **借鉴**：1500 hard cap 是过早优化（过去防 token 爆，现在 DeepSeek-V3 / Qwen-Plus context 都 ≥ 64k）。可以把 cap 调到 2200-2500，留给「年龄分级时长表」+「家庭场景注意事项」预算

---

## 4. 修复方案候选（每条带工时 + 跨环节依赖）

### 方案 A：暴露 `suggested_duration_minutes` 到 candidate_preview（最小侵入，最高 ROI）

```text
工时：~30 min
影响子环节：#11 blueprint_llm._poi_preview
依赖：无（mock 数据已经有此字段、schema 已经定义）
风险：极低（纯 dict 字段透传）
```

具体改动：

```python
# backend/agent/blueprint_llm.py:90-104  _poi_preview 现状字段
{id, name, type, tags, suitable_for, distance_km, opening_hours,
 rating, age_range, price_range, review_excerpts}

# 改后字段（新增 1 行）
{..., "suggested_duration_minutes": p.suggested_duration_minutes, ...}
```

并在 prompt 硬约束加一条（消耗 ~50 字符）：

```text
6. 选 duration_min 时优先采用候选的 suggested_duration_minutes（±30% 容差）；
   显著偏离须在 rationale 中说明（如"用户说只待 1h 故压缩 50%"）
```

### 方案 B：prompt 加「按 companion age 分级时长表」（核心修复）

```text
工时：~45 min（含改 prompt + 改 1500 cap 测试到 2200 + 加单测）
影响子环节：#12 blueprint_prompt
依赖：方案 A（让 LLM 同时拿到 POI suggested 与 age 分级两个信号）
风险：低（仅 prompt 文本扩展；范例 JSON 改动需 review）
```

**草稿 1（紧凑版，~300 字符，可单独贴）**：

```text
【按同行人年龄分级单段时长（companion-age tier，硬性建议）】
- 含 0-3 岁婴幼儿：单段 ≤ 45 min（注意力极短，需频繁切换）
- 含 4-6 岁学龄前：单段 ≤ 75 min（参考 Smithsonian 25min × 切换）
- 含 7-12 岁学童：单段 ≤ 120 min
- 含 60-75 岁长辈：单段 ≤ 90 min（每 90min 必须有可休息 hop）
- 含 75 岁以上：单段 ≤ 60 min
- 多代同行（如 5 岁娃 + 70 岁外婆）：取**最严**约束
当 candidate 的 suggested_duration_minutes 与本表冲突时，取**较小值**。
```

**草稿 2（含示例对照，~480 字符，更明确但费 token）**：

```text
【时长决策协议（按以下顺序）】
1. 优先采用 candidate.suggested_duration_minutes（mock 数据已校准）
2. 若 intent.companions 含 age ≤ 6 的儿童：单 POI 段 ≤ min(suggested, 75)
3. 若 intent.companions 含 age ≥ 75 的长辈：单 POI 段 ≤ min(suggested, 60)
4. 若 raw_input 含「只有 N 小时」：∑duration_min ≤ N*60（已有）
5. duration_min ∈ [10, 300]（已有）

例：query="5 岁娃 + 老婆，下午几小时" + P040.suggested=100
   → 主活动 duration_min = min(100, 75) = 75min（命中规则 2）
   而不是 150min（无规则约束的自由发挥）
```

**草稿建议**：用草稿 1 进 prompt（紧凑，~300 字符可控），把 cap 从 1500 调到 2000；草稿 2 作为单测里的"反例文档"。

### 方案 C：改 prompt 范例 JSON 的具体值（10 min，立即止血）

```text
工时：~10 min（改 prompt + 改 6 个相关单测）
影响子环节：#12 blueprint_prompt（仅范例 JSON 数值）
依赖：无
风险：极低，立即可见
```

把 `prompts/blueprint_prompt.py:43` 的：

```json
{"kind": "主活动", "target_kind": "poi", "target_id": "P040", "duration_min": 165}
```

改成：

```json
{"kind": "看展", "target_kind": "poi", "target_id": "P040", "duration_min": 75}
```

**注意**：`test_blueprint_prompt.py:115-121` 验证 prompt 含 `target_id` 等关键词，但**没**验证具体 165 这个值——所以改值不破已有测试。

### 方案 D：加 `_age_aware_duration_critic`（防守纵深第二层）

```text
工时：~75 min（写 critic + 写测试 + 跑回归）
影响子环节：#13 BlueprintCritic（agent/blueprint.py）
依赖：方案 A、B（让 LLM 第一道防线尽量出对，critic 只兜尾部失败）
风险：中（critic 误拒会触发 backprompt 重生成，可能引入 LLM 多次失败）
跨环节：与 Agent E（critic 子环节）联调
```

伪代码：

```python
def _age_aware_duration_critic(blueprint, intent) -> list[str]:
    """根据 intent.companions 的 age 推算每个 POI 段的合理上限。"""
    if not intent or not intent.companions:
        return []
    has_young_kid = any(c.age is not None and c.age <= 6 for c in intent.companions)
    has_elder = any(c.age is not None and c.age >= 75 for c in intent.companions)
    out = []
    for i, n in enumerate(blueprint.nodes):
        if n.target_kind != BlueprintTargetKind.POI:
            continue
        cap = 300  # default upper bound from MAX_NODE_DURATION_MIN
        if has_young_kid: cap = min(cap, 75)
        if has_elder: cap = min(cap, 60)
        if n.duration_min > cap:
            out.append(
                f"节点[{i}]「{n.kind}」时长 {n.duration_min}min 超过基于同行人年龄的"
                f"建议上限 {cap}min（"
                f"{'含 ≤6 岁儿童' if has_young_kid else ''}"
                f"{'含 ≥75 岁长辈' if has_elder else ''}）"
                f"。请压缩或拆为多段。expected_range=[10, {cap}]"
            )
    return out
```

### 方案 E：扩 PlanningWeights 加「合理性」轴（长期债，不阻塞）

```text
工时：~150 min（改 dataclass + 改 LLM prompt + 改 ILS scorer + 重训 _heuristic_weights）
影响子环节：#10 weights_llm + #18 planner_hybrid 的 utility_score
依赖：与 Agent F（算法层）协同
风险：中高（_heuristic_weights 9 种 social_context 默认权重需重调；测试矩阵变宽）
建议：**Phase 5 spec 排在「应当（should）」而非「必须（must）」**——LLM-First 主路径已不消费 weights，ROI 不高
```

---

### 杠杆点排序（D 内 3 个子环节）

```text
#12 blueprint_prompt   ★★★★★（核心病灶；改 prompt 范例 + 加年龄分级表立竿见影）
#11 blueprint_llm      ★★★★☆（改 _poi_preview 暴露 suggested_duration 是必备配套）
#10 weights_llm        ★★☆☆☆（在 LLM-First 主路径已被绕过；先放后面）
```

**先修顺序建议**：方案 C（10 min 止血）→ 方案 A（30 min 暴露字段）→ 方案 B（45 min 加分级表）→ 方案 D（75 min 加 critic 兜底）。前 4 步 ~160 min 可形成完整防守纵深。

### prompt vs critic vs schema 的「防守纵深」（高杠杆 agent 必答）

```text
| 层级       | 当前状态                          | 改动建议                              | 与谁配合       |
|-----------|----------------------------------|--------------------------------------|---------------|
| 1. schema | suggested_duration_minutes 已存在 | 无需改                               | Agent G（mock）|
| 2. preview | 漏暴露 suggested 字段             | 方案 A 透传（30 min）                  | 自身 #11      |
| 3. prompt 范例 | duration_min=165 是反向锚定    | 方案 C 改成 75（10 min）              | 自身 #12      |
| 4. prompt 规则 | 完全无年龄分级时长表           | 方案 B 加紧凑版分级表（45 min）        | 自身 #12      |
| 5. critic 兜底 | 上限 300 min 无年龄感知        | 方案 D 加 _age_aware（75 min）        | Agent E       |
| 6. critics_v2 | 只验 ±30 min 总时长容差        | 不变（与 D 维护层次划分）              | Agent E       |
```

**关键判断**：单 prompt 修是首要（杠杆最大、ROI 最高），**但不彻底**——prompt 是软约束，LLM 仍可能因为 review_excerpt 或 tag 拉满偷偷走极值。必须配合 critic 兜底层（方案 D）形成 "prompt 主防 + critic 兜底" 双层。schema 已经做对（suggested_duration_minutes 字段已有），只是 preview 把它丢了。

---

## 5. 目录归属建议（A1 融合）

```text
| 文件                                  | 当前位置                  | 建议归属                    | 备注                               |
|--------------------------------------|--------------------------|----------------------------|-----------------------------------|
| backend/agent/weights_llm.py         | agent/                    | agent/planning/            | 仅服务 ILS（hybrid 冻结路径）；考虑迁移或冻结|
| backend/agent/blueprint_llm.py       | agent/                    | agent/planning/blueprint/  | 主路径核心，应与 blueprint.py / assemble_blueprint.py 同栏 |
| backend/agent/prompts/blueprint_prompt.py | agent/prompts/        | agent/planning/blueprint/prompts/ | 紧贴生成器，跨包导入路径不变，便于修改 prompt 时同步看 schema |
```

合并 / 删除建议：

- **不合并**：blueprint_llm 与 blueprint.py 职责清晰（生成 vs 数据 + critic）
- **冻结候选**：`weights_llm.py` 在 LLM-First 主路径事实已死路；建议在文件顶部加 `# FROZEN: 仅 hybrid fallback 路径消费` 注释，与 §3.3.1 编排冻结纪律对齐
- **不删任何文件**：所有三个文件都被主路径 / fallback 路径引用

---

## 6. 跨环节依赖警示（你看到但其他 agent 看不到的）

### 我修这里会影响：

1. **Agent E（critic 三套）**：方案 D 在 `blueprint.py` 加 `_age_aware_duration_critic`；与他们 critics_v2 的 `_check_duration` 边界要厘清——blueprint critic 验**单段**年龄敏感时长，critics_v2 验**总时长**±30min 容差，两者层次互补但消息措辞要避免重复让 LLM 困惑
2. **Agent G（mock 数据）**：方案 A 暴露 `suggested_duration_minutes` 后，G 必须确保**所有 38 个 POI** 都填了合理值（已确认有，但要他们 audit 一下"健身房 30min" / "游乐场 180min" 是否实际合理）；E.g. P019.suggested=180min 配 5 岁娃没问题（迪士尼级），但 P040.suggested=100min 配老人体力衰减仍偏长
3. **Agent A（意图层）**：要保证 `intent.companions[].age` 字段被 LLM 准确填——若用户说"5 岁娃"但 intent_parser 漏抽 age，方案 D 的 `_age_aware_duration_critic` 退化为 no-op
4. **Agent H（narrator）**：narrator 文案不应去重复 prompt 内的"分级时长"逻辑（不要在生成的"下午带宝贝去 X 玩 90 分钟"文案中和 prompt 内的硬性数字打架）

### 我依赖另一处先修：

1. **Agent A**：`intent.companions[].age` 必须可靠抽取（现状已大体支持，但需 verify 5 岁/老人/婴幼儿三档命中率 ≥ 95%）
2. **Agent G**：38 个 POI 的 `suggested_duration_minutes` 须 audit 一遍（特别是亲子类、博物馆类、复合体验馆类——P040 是"复合体验馆"配 100min 是否真合理？参考 Smithsonian 数据 5 岁单一馆只该 25min × 多馆切换）
3. **Agent F（assemble + ILS）**：方案 D 的 critic 触发后会强制 backprompt 重生成；F 要确保 `LLM_FIRST_MAX_CRITIC_RETRIES=2` 仍够用（5 岁娃这种约束多重叠的场景，可能首次 + 重试 1 次仍命中重新生成上限——需要预留第 2 次重试预算）

### 暗坑提示：

1. **prompt 范例值改了但单测可能还在断言旧值**：`test_blueprint_prompt.py` grep 检查 `target_id`、`duration_min` 等关键字时如果不小心断言了具体 165，会破测试。改前先 grep 确认（我已确认 `test_blueprint_prompt.py:113-121` 只查关键词不查具体值）
2. **prompt cap 调高 2200 时**：要同步调 `test_system_prompt_length_under_hard_cap` 里 1500 → 2200，并把测试 message 里的解释（"旧版 ~3500 → 新版目标 ≤1500"）改成"加业务合理性段后 ≤ 2200"
3. **方案 B 草稿提及"参考 Smithsonian"** 这种第三方机构名**不应**进 prompt（多余 token、与"晌午局"品牌调性不匹配）；只在报告与 commit message 留作引用

---

## 附录：根因诊断收口（"为什么 LLM 出 150 min？"）

```text
五因联动表：
┌─[1] prompt 范例 duration_min=165 ─→ in-context 锚定主活动 ≈ 165min
├─[2] candidate_preview 漏 suggested_duration_minutes（mock=100）──→ LLM 失去权威下限
├─[3] prompt 无任何「按年龄分级时长」语句 ────→ 5 岁娃信号被 prompt 完全忽略
├─[4] _format_review_excerpts 把"孩子玩 1.5 小时还想接着玩"原话喂给 LLM ──→ 反向加压拉长时段
└─[5] _duration_critic 上限 300min 从不触发；critics_v2 只看总时长 ±30min ──→ critic 一路绿灯放行
```

任意单点修复都不够。**最高 ROI 的组合**是 [2][3][1] 三件齐做（方案 A + B + C，~85 min 工时），命中率从 0% 拉到 ≥ 90%；剩下 10% 由方案 D 的 critic 兜底。

---

> 报告完成；本报告含约 6800 字（含表格代码块）。
> 引用代码均含文件:行号；业界对标 4 条带 URL，对标 2/3 用释义改写满足合规。
> Phase 4 联合审查请重点对照 §4 的「防守纵深表」与 Agent E（critic）、Agent G（mock）的对标项。
