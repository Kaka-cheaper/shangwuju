# Agent A 审查报告 —— 意图理解层（#1-4）

> **审查范围**：Router（#1）/ IntentParser（#2）/ Refiner（#3）/ NodeDecider（#4，含 segment_decider alias）
> **触发场景**：家庭主线「老婆孩子 5 岁，老婆减肥」→ blueprint 把博物馆 / 亲子 POI 主活动开成 165 min（业界常识 60-90 min），用户反馈「太久了 / 主活动太长」无法被 refiner 正确消费。
> **D-SoT**：`docs/01-requirements/需求分析.md` §5.7 + `backend/schemas/intent.py` + `backend/schemas/tags.py`。

---

## 1. 现状摘要

```
| #  | 子环节            | 输入 → 输出契约                                           | 关键 LLM 调用      | 兜底策略                                |
|----|-------------------|----------------------------------------------------------|---------------------|----------------------------------------|
| 1  | Router            | user_input → RouterDecision (input_kind 6 类 + chips)    | router_prompt 1 次  | fallback_decision 直接判 PLANNING       |
| 2  | IntentParser      | user_input → IntentExtraction (§5.7 schema)              | system_prompt 1+1 次| 校验失败回灌 LLM 1 次后抛 IntentParseError |
| 3  | Refiner           | (orig_intent, feedback) → RefinementOutput               | refiner_prompt 1+1  | _rule_fallback 关键字典（距离/预算/时长） |
| 4  | NodeDecider       | IntentExtraction → list[str]（中间节点 kind）              | 纯函数              | 极端兜底返 ["主活动"]                     |
```

**正向能力**（先肯定 4 点，避免后面找问题被误读为完全否定）：

- 词典出口防御严密：tag 通过 Pydantic `Literal` + prompt 内 `_format_set` 双锁，pitfalls P1-预埋已落地。
- D9 硬条款执行干净：`backend/schemas/intent.py:74` `extra="forbid"` + `system_prompt.py:69` 显式禁止 `scene_type/relation_type/is_family/is_friends`。
- 反馈链 raw_input 拼接（`refiner.py:184-191`）保证下游能从单一来源读到精确数字反馈，是 pitfalls P1-2026-05-17 多次复盘的结晶。
- Router Layer 3 弱信号兜底（`graph/nodes/router.py:71-83`）解决了「短反馈被误判为 chitchat」的回归 bug，工程上是合格的防御。

**核心反直觉发现**：当前意图层把**「我有几小时」**翻译得很准（精确数字识别 + 入口/出口双兜底，pitfalls P1-2026-05-17 五层防线），但把**「这一段我能扛多久」（attention span / pace / mobility budget）**完全没建模——schema、prompt、persona、refiner 字典 **四处皆缺**。这是 5 岁娃 2.5h 博物馆现象的根因之一。

---

## 2. 业务合理性 gap 清单

### P0（demo 立刻翻车）

#### [P0-1] IntentExtraction schema 没有「单段耐受时长 / 注意力跨度」字段
- **现象**：`backend/schemas/intent.py` 仅有总时长 `duration_hours: [min, max]`、`distance_max_km`、companions、4 类 tag。`attention_span` / `max_session_min` / `pace` / `breaks_required` 等节奏字段一概没有（grep `pace|节奏|attention|stamina|sleep_time` 返 0 命中）。
- **根因**：§5.7 schema 把行程视作"总时长 + tag 篮子"，没有"主活动单段耐受上限"的概念。下游 BlueprintLLM 看到 `duration_hours=[3,5]` + `companions=[孩子 5 岁]` + `physical=[亲子友好, 适合 5-10 岁]`，但没有"这个 5 岁娃单展项最多 25 min、单场馆最多 90 min"的约束信号。
- **反例**：
  - 输入：`今天下午想和老婆孩子出去玩几个小时，孩子 5 岁，老婆最近在减肥`
  - 期望抽取（业界基准，Smithsonian SEEC）：`companions[1].age=5` → `attention_span_min ≤ 25`、`single_venue_dwell_max_min ≤ 90`、`requires_break_every_min: 30`
  - 实际抽取（`backend/scripts/verify_planning.py:38-44`）：`companions[1]={role:孩子,age:5}` + `physical_constraints=["亲子友好","适合 5-10 岁"]`，**没有任何节奏字段**
  - 实际结果：`blueprint.py:60` `MAX_NODE_DURATION_MIN=300`（5 h），LLM 在没有节奏先验下拍 165 min 主活动 = **不违反任何 critic**
- **修复方向**：§5.7 schema 加 `pace_profile: {single_session_max_min, total_active_min, breaks_required}`，意图层 system_prompt 加规则「companions 含 ≤6 岁孩 → single_session_max_min ≤ 60；含老人 → ≤ 90」。

#### [P0-2] BlueprintLLM 看不到 companions[].age（**已传但 prompt 无引导**）
- **现象**：`blueprint_llm.py:233` 把 `intent.model_dump_json()` 整体喂 LLM，age 字段技术上**在 JSON 里**。但 `blueprint_prompt.py:52-79` 全段不提"年龄 / 亲子 / 节奏"任何先验——LLM 没有理由把 165 min 调成 90 min。
- **根因**：blueprint_prompt 只列了「节点字段、target 选择、营业时间、duration_min ≥ 0」等结构性约束，对**业务合理性**（小娃博物馆 90 min 上限）零提示。
- **反例**：
  - 同上 P0-1 输入
  - 期望：blueprint_prompt 内有 "companions 含 ≤6 岁孩 → 单 POI duration_min ≤ 90"
  - 实际：prompt 仅有 `4. duration_min ≥ 0；raw_input 含「只有 N 小时」/「N 个小时」时 ∑duration_min ≤ N*60`（`blueprint_prompt.py:55`），即"只看总和不看分布"
- **修复方向**：blueprint_prompt 加 6-8 条业务约束（亲子单段 ≤ 90、老人单段 ≤ 60、独处 ≥ 60、商务接待用餐 ≥ 90）；并把 weights_llm 的「同行老人/儿童」检测扩到 5 类业务约束 dict 一并传 LLM。

#### [P0-3] Refiner 关键字典识别不到「太久 / 太长 / 主活动累」类反馈
- **现象**：`refiner.py:201-209` 字典覆盖 4 类反馈：距离近/远、便宜、时间紧/松（且时间紧的关键词限定为「时间紧/快一点/短一点/时间不多」）。**「太久了 / 这一段太长 / 孩子盯不住」一个都没命中**。
- **根因**：字典是 P1-2026-05-17 修复"5 段写死"时按 3 类常见反馈做的，没考虑「单段时长反馈」与「总时长反馈」是两回事——前者要削减某节点的 duration_min，后者要削减总池。
- **反例**（用户截图复现路径）：
  - 输入：用户已收到 165 min 博物馆方案，前端反馈「主活动太久了，孩子盯不住」（10 字 < 15 字）
  - 期望：refiner 把 nodes[主活动].duration_min 从 165 → 60-90，**不动总时长上限**
  - 实际链路：
    1. `graph/nodes/router.py:75` Layer 3 弱信号兜底：`has_itinerary + len<15 + route ∈ (ambiguous, chitchat)` → 强制 feedback
    2. refiner.py LLM 路径：feedback「太久」无具体小时数 → `_extract_duration_from_feedback` 返 None → `_enforce_duration_consistency` 不触发（`refiner.py:267-271`）
    3. LLM 异常 → `_rule_fallback`：4 类关键字典全 miss → 走末尾 `distance -1km 兜底`（`refiner.py:344-349`）
    4. **最终结果**：用户嫌「主活动 165 min 太久」，系统反应是「把 distance_max_km 从 5km 减到 4km」+ 「distance 上限：5km → 4km（轻量调整）」 toast → **完全错误的反应**
- **修复方向**：
  - 字典加 `_KEYWORDS_SESSION_TOO_LONG = ("太久", "太长", "盯不住", "无聊", "扛不住", "腻了")`，命中后**不动 duration_hours，也不动 distance**，而是产出 `{"main_activity_session_max_min": 90}` 类的细粒度约束。
  - blueprint_prompt 增 `single_session_max_min` 字段消费规则。

### P1（用户不会立刻发现，但会侵蚀信任）

#### [P1-1] persona prior 仅注入 2 个字段，attention/pace 维度完全不消费
- **现象**：`system_prompt.py:212-281` `build_intent_parser_system_prompt_with_priors` 把 persona 注入 prompt，但只影响：
  - **social_context**（用户没明示场景时取 `suitable_for_priority[0]`）
  - **distance_max_km**（取 `default_distance_max_km`）
  - tag 复用「保守补全」原则不主动加
- **根因**：`mock_data/personas.json` 的 `u_dad`（新手爸爸）notes 字段已写「孩子能玩 1.5h+」，但这是**自由文本**没结构化，prior 不消费 → 等于白写。
- **反例**：
  - user_id=u_dad、`输入="今天下午想和老婆孩子出去玩"`
  - persona notes 写了「孩子能玩 1.5h+」（暗示主活动 ≤ 90 min）
  - 期望：persona 注入 `attention_span_min: 25, single_session_max_min: 90`
  - 实际：persona 仅注入 `social_context=家庭日常 + distance_max_km=5.0`，attention 信号完全损失
- **修复方向**：persona schema 加结构化 `pace_profile`，`compute_priors` 把它合并到 prompt addendum；且不与 D9 冲突——这是 user 维度而非 scene_type 枚举。

#### [P1-2] NodeDecider 仅决 kind 不决时长，业务合理性单点失守
- **现象**：`node_decider.py:131-185` `decide_nodes` 输出 `list[str]`（如 `["主活动", "用餐"]`），**完全不输出每段建议时长**。时长决策权 100% 在 BlueprintLLM。
- **根因**：edge_v1 的设计取舍（problem.md 问题 14 / pitfalls P1-2026-05-17：「LLM 决主观、algo 决客观」）。但「亲子单段 ≤ 90 min」是**业界客观常识**（SEEC 数据），不是 LLM 主观偏好——不应让 LLM 自由发挥。
- **反例**：
  - 同 P0-1 输入（5 岁娃 + 4-6h）
  - decide_nodes 返 `["主活动", "用餐"]`
  - BlueprintLLM 在没有 nodes 建议时长的情况下，按总池 4-6h 拍 165/45 切分（合理但孩子撑不住）
  - 期望：node_decider 同时返建议时长 `[(kind="主活动", suggested_min=80, max_min=90), (kind="用餐", suggested_min=60)]`
- **修复方向**：把 `decide_nodes` 升级为 `decide_node_plan(intent) -> list[NodePlanHint]`，每个 hint 含 `kind / suggested_min / max_min / rationale`；BlueprintLLM 把 hint 作为软约束消费，仍允许偏离但要在 rationale 里说明。

#### [P1-3] Router Layer 3 阈值 `<15` 字符过粗
- **现象**：`graph/nodes/router.py:79-83` `len(user_input.strip()) < 15 and route_kind in ("ambiguous", "chitchat")` → 强制 feedback。15 字含中文标点能塞下「主活动太久了！孩子盯不住」（13 字）这类**应进 refiner 的有效反馈**，但也能塞下「再帮我看看周日呢」（9 字）这类**应进 planner 的新需求**。
- **根因**：用「字数」近似「反馈意图」，量纲错误。
- **反例**：
  - 已有 itinerary，输入「再帮我看看周日呢」（9 字）→ Layer 3 误判为 feedback
  - refiner 收到「再帮我看看周日呢」 → 既无小时数也无距离/预算关键词 → distance -1km 兜底 → **用户想换日期，系统改了距离**
- **修复方向**：把启发式从「短输入」改成「`feedback_detector.looks_like_feedback` 二级判定」（已存在但 Layer 3 没复用）；或单独训一个轻量分类器（input_len < 30 且 LLM 判 ambiguous → 二次喂 LLM 问「是反馈还是新需求」）。

### P2（潜伏 bug、长期债）

#### [P2-1] segment_decider.py 兼容 alias 与新 node_decider 双轨在 critic 链残留
- **现象**：`segment_decider.py:1-44` 整文件是 `from .node_decider import *` re-export。`backend/agent/critics.py` / `planner_hybrid.py` / `planner_llm_first.py` / `replan.py` 仍 import `decide_segments`（旧 frozenset 语义）。Wave 5 完结后应删，但当前**两套表述并存**——任何加新 kind 的人都要同步两边。
- **修复方向**：grep 各调用点 → 统一迁移到 `decide_nodes` → 删除 `segment_decider.py`（与 Agent F 的 planner 重组对齐）。

#### [P2-2] 词典出口设计无法表达「细粒度生理约束」
- **现象**：`schemas/tags.py:16-25` PhysicalTag 仅 9 个值（亲子友好/适合 5-10 岁/适合青少年/适合老人/无台阶/可休息/无障碍/高强度/低强度）。「需 30 min 一次中场休息」「不能站超过 60 min」这类**节奏型生理约束**无表达位。
- **修复方向**：要么 PhysicalTag 加 `"需中场休息"`/`"久站易累"` 等节奏 tag；要么新加独立 schema 字段 `pace_profile: PaceProfile`（推荐后者，避免 tag 篮子膨胀）。

#### [P2-3] `_extract_duration_from_feedback` 仅识 1-12 整数小时，不识「半小时 / 30 分钟 / 一个半」
- **现象**：`refiner.py:227-260` 正则只匹配 `(\d+)\s*(?:个)?\s*小时`，`半小时 / 30 分钟 / 一个半小时` 全不命中。pitfalls 没记录这个 case，但 Demo 现场风险高。
- **反例**：用户反馈「主活动半小时就够」→ 不命中 → fallback distance -1km。
- **修复方向**：扩正则 + 加分钟模式 `(\d+)\s*分钟`，半小时映射 30、一个半小时映射 90。

#### [P2-4] Refiner 把反馈拼到 raw_input 末尾会逐轮膨胀
- **现象**：`refiner.py:184-191` `raw_input = f"{original.raw_input}（反馈：{feedback}）"`。三轮反馈后 raw_input 形如「`原句（反馈：A）（反馈：B）（反馈：C）`」。`weights_llm._heuristic_weights` 用 `if any(kw in raw for kw in ("快","赶时间","急"))` 累加 time 权重——历史反馈关键词会**永久污染**当前轮权重。
- **修复方向**：用 `intent.feedback_history: list[str]` 独立字段携带，`raw_input` 保留原句不拼；或拼接时只保留**最近一次**反馈（覆盖式而非累加式）。

---

## 3. 业界对标 diff（4 项）

### 对标 1：TravelPlanner（NeurIPS / ICML 2024，OSU NLP Group）
- **链接**：[arxiv.org/abs/2402.01622](https://arxiv.org/abs/2402.01622) / [github.com/OSU-NLP-Group/TravelPlanner](https://github.com/OSU-NLP-Group/TravelPlanner) / [osu-nlp-group.github.io/TravelPlanner](https://osu-nlp-group.github.io/TravelPlanner/)
- **他们怎么做**：把 user query 解析为三类约束并行约束树——`hard_constraints`（用户硬要求）+ `commonsense_constraints`（常识默认，如「饭点不能跳过 3 餐」）+ `environment_constraints`（候选数据约束）。LLM 必须同时满足三类。
- **我们差在哪**：意图层只抽 `hard_constraints`（用户口头说的 4-6h、5km）。`commonsense_constraints`（5 岁娃 = 单展项 25 min；亲子博物馆 ≤ 90 min）**完全没建模** —— 这正是 P0-1 的根因。
- **借鉴成本**：~90 min。在 IntentExtraction 加 `commonsense_constraints: list[CommonsenseRule]`，由 system_prompt 按 companions 自动填；blueprint_prompt 把它作为硬约束消费。

### 对标 2：ITINERA（EMNLP 2024 Industry Track）
- **链接**：[arxiv.org/abs/2402.07204](https://arxiv.org/abs/2402.07204) / [acl.ldc.upenn.edu/2024.emnlp-industry.104](https://acl.ldc.upenn.edu/2024.emnlp-industry.104/)
- **他们怎么做**：query → request decomposition（拆出 mood / theme / pace / dwell-time-preference 四维度）→ preference-aware POI retrieval → cluster-aware spatial optimization。pace 与 dwell-time 是**首类抽取维度**而非附属 tag。
- **我们差在哪**：我们的 IntentExtraction 把 mood 翻译成 `experience_tags`（独处舒缓/拍照友好），把 theme 翻译成 `social_context` —— 这两个都对了。但 **pace / dwell-time 完全不抽**。这是 EMNLP 2024 工业级实践已经定型的字段，我们没跟上。
- **借鉴成本**：~60 min。schema 加 `dwell_time_preference: Literal["快速打卡","深度沉浸","随性"]`；intent_parser system_prompt 加抽取规则；BlueprintLLM 用它调 duration_min 上下界。

### 对标 3：RouteLLM（arxiv 2510.06078，Hierarchical LLM Agents for Route Planning）
- **链接**：[arxiv.org/html/2510.06078](https://arxiv.org/html/2510.06078)
- **他们怎么做**：parser agent → constraint agent（**专门一个 agent 做 constraint resolution and formal check**）→ POI agent → path refinement → verifier agent。Constraint agent 显式区分「natural language constraint」与「formal constraint」并做翻译 + 一致性检查。
- **我们差在哪**：我们的 refiner 是「自然语言反馈 → IntentExtraction 字段直接改」单跳。中间没有 constraint resolution 层做「『太久』是落 duration_hours / single_session_max / pace 哪个字段」的形式化决策。这是 P0-3 的架构根因——refiner 字典在做 constraint resolution 的工作，但用 if/elif 关键字典实现，无法 scale。
- **借鉴成本**：~3 h（要新增 constraint_resolver_agent 节点）。短期可在 refiner_prompt 内加「反馈字段映射表」由 LLM 内部完成 resolution；中期再单独拆 agent。

### 对标 4：Smithsonian SEEC（Smithsonian Early Enrichment Center）
- **链接**：[americanhistory.si.edu/blog/2013/12/top-tips-for-a-rewarding-museum-visit-with-kids](https://americanhistory.si.edu/blog/2013/12/top-tips-for-a-rewarding-museum-visit-with-kids.html) / [americanhistory.si.edu/about/faqs/visiting-museum-kids](https://americanhistory.si.edu/about/faqs/visiting-museum-kids)
- **他们怎么做**（注意力跨度业界基准，rephrased for compliance）：婴幼儿 10-15 min、学龄前儿童 20-25 min 算成功的单展项；带 5 岁娃的家庭整馆建议约 2 h 含休息；老儿童亦不应连续 hours 不停看。Brain Balance Centers 给出 child attention span ≈ 2-3 min × age 的工程公式（5 岁 ≈ 10-15 min 单任务专注）。
- **我们差在哪**：blueprint critic `MAX_NODE_DURATION_MIN=300`（5 h）—— 这是「LLM 误填检测」级别的上限，不是「业务合理性」。5 岁娃单 POI 165 min **完全合规**。整个意图链路 + critic 链路都没把 SEEC 这种业界已有数据消费进来。
- **借鉴成本**：~30 min。把上面公式编码进 system_prompt + blueprint_prompt（由 IntentParser 写入 commonsense_constraints，由 BlueprintLLM 在 duration_min 上消费）。

---

## 4. 修复方案候选

### 方案 A：**最小 schema 扩展 + prompt 升级**（推荐）
- **内容**：
  1. `schemas/intent.py` 加 `pace_profile: Optional[PaceProfile]`，PaceProfile 含 `single_session_max_min: int | None`、`total_active_min: int | None`、`break_every_min: int | None`。
  2. `system_prompt.py` INTENT_PARSER 隐含规则段加 4 条：「ages ≤ 6 → single_session_max_min ≤ 90」「老人 / `适合老人` → ≤ 90 + break_every_min ≤ 60」「独处放空 → ≥ 60」「商务接待用餐 → ≥ 90」。
  3. `blueprint_prompt.py` 加 `pace_profile` 消费段：每个 node duration_min 必须 ≤ pace_profile.single_session_max_min。
  4. `refiner_prompt.py` 字典加 `_KEYWORDS_SESSION_TOO_LONG` + 把它映射到 `pace_profile.single_session_max_min` 缩 30%（不动 duration_hours、不动 distance）。
  5. `blueprint.py` `_duration_critic` 加按 intent.pace_profile 校验单段时长，CRITICAL 级。
- **工时**：~3.5 h（schema 0.5h + prompt 1h + critic 0.5h + 测试 1.5h）
- **影响子环节**：#2 IntentParser、#3 Refiner、#4 NodeDecider、#11 BlueprintLLM、#13 BlueprintCritic
- **风险**：schema 扩字段需同步 §5.7 D-SoT 文档；新 LLM 字段抽取稳定性需要 verify_planning 多跑 5-10 次。

### 方案 B：**NodeDecider 升级为 NodePlanHint 输出**
- **内容**：`decide_nodes` → `decide_node_plan(intent) -> list[NodePlanHint]`，hint 含 `kind / suggested_duration_min / max_duration_min / rationale`。BlueprintLLM 把 hint 作为软约束消费。
- **工时**：~2 h
- **影响子环节**：#4 NodeDecider、#11 BlueprintLLM、#16 AssembleBlueprint（边路对齐）
- **风险**：node_decider 引入业务知识 → 与 P1-2026-05-17 「algo 决客观、LLM 决主观」原则部分冲突。**取舍**：节奏（亲子 ≤ 90、老人 ≤ 60）是**业界客观数据不是 LLM 主观偏好**，应进 algo 层不进 LLM。
- **依赖**：与方案 A 互补；A 处理"用户表达"，B 处理"算法兜底"。

### 方案 C：**Refiner 加 Constraint Resolver 中间层**
- **内容**：refiner_prompt 加「反馈字段映射决策表」让 LLM 内部完成 natural→formal constraint resolution。映射表举例：「太久/太长 → pace_profile.single_session_max_min - 30」「换个地方 → 把当前 nodes[main].target_id 加 ambiguous_fields」。
- **工时**：~2 h（仅 prompt + few-shot 升级，不动代码）
- **影响子环节**：#3 Refiner
- **风险**：LLM 字段路由稳定性需要回归测试矩阵覆盖（已有 21 个 refiner 测试，扩到 35-40 个）。

### 方案 D（不推荐）：**短期 hard-code 5 岁规则**
- **内容**：blueprint_prompt 加一行「companions 含 age ≤ 6 → 主活动 duration_min ≤ 90」。
- **工时**：~10 min
- **风险**：违反 D9（"对场景类型无感"），且只 cover 一个边界。
- **结论**：仅作 demo 兜底，不应作为长期方案。

---

## 5. 目录归属建议（A1 融合）

```
| 文件                                      | 当前位置       | 建议归属        | 理由                                                |
|-------------------------------------------|---------------|----------------|----------------------------------------------------|
| backend/agent/router.py                   | agent/        | agent/intent/  | 与 intent_parser/refiner 同属意图理解层             |
| backend/agent/intent_parser.py            | agent/        | agent/intent/  | 同上                                               |
| backend/agent/refiner.py                  | agent/        | agent/intent/  | 反馈合并是意图层职责（用户期望转结构化约束）          |
| backend/agent/node_decider.py             | agent/        | agent/planning/| 节点结构决策，属 planning 而非 intent              |
| backend/agent/segment_decider.py          | agent/        | agent/legacy/  | 兼容 alias，待 Wave 5 删除（已在文件 docstring 注明）|
| backend/agent/prompts/router_prompt.py    | agent/prompts/| agent/intent/prompts/ | 跟随 router.py 一起搬                       |
| backend/agent/prompts/system_prompt.py    | agent/prompts/| agent/intent/prompts/ + agent/planning/prompts/ | 一文件含 INTENT/PLANNER 两 prompt，应拆 2 文件 |
| backend/agent/prompts/refiner_prompt.py   | agent/prompts/| agent/intent/prompts/ | 跟随 refiner.py 一起搬                      |
```

**合并/删除建议**：
- `system_prompt.py` 当前混装 IntentParser 和 ReAct Planner 两套提示词 + 1 个 prior 注入函数，**职责漂移**——拆为 `intent/prompts/intent_parser_prompt.py` + `planning/prompts/react_planner_prompt.py` 两个文件。
- `segment_decider.py` 计入 legacy/，加 deprecation warning，下个 spec（itinerary-edge-model-refactor 完结后）一次性删除。
- 4 个 prompt 文件单独抽到对应子目录的 `prompts/` 子目录，与代码同级 —— 与 D 系（蓝图层）prompts 在 `planning/` 下保持镜像一致。

**冻结建议**：
- `node_decider.py` 主体逻辑（`decide_nodes`）冻结字段语义；只允许加新 KIND 常量（如 `KIND_NIGHTSNACK`）或新 social_context 阈值。
- `segment_decider.py` 立即冻结，仅 alias re-export。

---

## 6. 跨环节依赖警示

### 我修这里会影响：
- **Agent D（#10-12 蓝图层）**：方案 A 引入 `pace_profile` → blueprint_prompt 必须改；blueprint_llm 的 `intent.model_dump_json()` 喂入 pace_profile 字段 → LLM 输出契约不变但合理性提升。**需与 Agent D 协商 prompt 改动顺序**（prompt 互锁，谁先改谁负责对齐 schema）。
- **Agent E（#13-15 Critic）**：blueprint critic `_duration_critic` 要新增 pace_profile 单段校验；critics_v2 要在 Itinerary 级别 cross-check pace_profile 是否被 hop 蚕食。
- **Agent G（#23-24 mock 数据）**：personas.json 的 `notes` 自由文本「孩子能玩 1.5h+」要结构化为 `default_pace_profile: {single_session_max_min: 90}`；persona prior 注入路径（`build_intent_parser_system_prompt_with_priors`）才能消费。

### 我依赖另一处先修：
- **Agent G 先建 `pace_profile` 在 personas.json 的字段**，否则 prior 注入无新字段可读。
- **Agent D 先在 blueprint_prompt 增加 `pace_profile` 消费规则**，否则 schema 加了字段也是死信。
- **Agent F（#16,18-20 拼装层）确认 ILS / rule planner fallback 不会把 pace_profile 丢失**：`planner_hybrid.py` / `planner.py` 走 fallback 时 intent 字段透传是否完整？需要验证 hybrid critic 走 ILS 时仍会校验单段时长。

### 同 layer 内警示：
- 方案 A + B 同时改 NodeDecider 和 BlueprintLLM 时，谁是 single source of truth 要明确：**hint 是 algo 推荐，pace_profile 是用户/persona 注入；冲突时 pace_profile 胜**（用户/persona 优先于算法启发式）。
- Refiner 加 SESSION_TOO_LONG 关键字典后，必须更新 `feedback_detector.looks_like_feedback`——否则 Layer 1 强信号启发式仍按旧关键词识别，「主活动太久」会被路由到 chitchat 而非 refiner。

### 与 D9 / §3.3.1 编排冻结纪律的兼容性：
- 方案 A 在 IntentExtraction 加 `pace_profile` 字段——**不**违反 D9（pace_profile 是 user 维度数值，不是场景枚举）。
- 方案 B 升级 NodeDecider 输出契约——属 `agent/` 主路径改造，与 §3.3.1「编排层冻结只允许 graph/ 加节点」**部分相关**：node_decider 不在 graph/ 但被 graph/nodes/planner.py 调用 → 只要不动 graph/build.py 的 edge 拓扑就 ok。
- 方案 C 仅改 prompt，不动代码结构——零工程风险。

---

> **报告完结**。Phase 3 编排者请把方案 A + C 列入 P0 修复（demo 直接受益），方案 B 列入 P1（架构清理与 algo 兜底强化），方案 D 仅作紧急 demo 兜底备案。
