# Phase 4 联合审查报告（对抗审查）

> 由独立审查官对 8 份 agent 报告（A-H，~9 万字）+ Phase 3 dependency-graph.md 做对抗审查。
> 立场：**找漏洞、找重复、找误判、找漏点**——不是来鼓掌，是来追问"修了真能解决 5 岁娃博物馆 2.5h 吗"。
> 审查方法：交叉对照 8 份报告的 P0/P1 条目，跑业界对标抽检，验证 Phase 3 修复路径的端到端可行性。
> 输出原则：能合并的合并，能砍的砍，存疑就标存疑。

---

## 1. 重复 gap 合并清单（共 7 处合并机会）

8 个 agent 互相不读对方报告，导致同一根因被重复发现。Phase 3 dependency-graph 的"5 因联动"已合并了部分，但仍有较细粒度的重复未消化。下表是**比 Phase 3 更细**的合并方案：

```text
| 合并 ID | 涉及条目                              | 同根因依据                                                | 合并后唯一 owner | 工时去重 |
|---------|---------------------------------------|--------------------------------------------------------|----------------|---------|
| M1      | P0-B1 + P0-D2 + P0-E1（部分）         | 都指 _poi_preview 漏 suggested_duration_minutes        | Agent B（信息源透传） | 0.5h |
| M2      | P0-B2 + P0-G2                         | 都指 Restaurant 缺 typical_dining_min                   | Agent G（mock + schema） | 2h |
| M3      | P0-B3 + P0-G1 + P0-A1                 | 都指"按年龄分桶时长"——B 说字段差异化、G 说 dict 升级、A 说 pace_profile | Agent G + A 联合 | 4.5h |
| M4      | P0-D1 + P0-D3 + P0-E1                 | 都指 prompt/critic 对单段时长无业务感知（范例 165 + 上限 300 + critic 全无年龄）| Agent D + E 联合 | 1.5h |
| M5      | P0-A2 + P0-D2 + 部分 P1-D5            | 都指"LLM 看到的输入信号缺年龄/时长锚点"                  | Agent D（prompt 主防）| 1h |
| M6      | P0-F1 + P0-E1（ILS 镜像）             | 都指算法路径无 overload 维度——E 在 critic 拦、F 在 utility 罚 | 保留双重防御，不合并工时 | 0 |
| M7      | P0-H1 + P0-F3 + P1-E4                 | 都指"质疑信号缺失"——narrator 不质疑 + summary 强化 + warning 不可见 | Agent H 主导（narrator 是出口）| 1h |
```

**M1（合并依据）**：B 与 D 的报告里同一行 `_poi_preview` 字段集都列了。B 报告是从"信息源传递"看，D 报告是从"prompt 锚定"看，但**改动点完全相同**——`backend/agent/blueprint_llm.py:90-104` 的 `_poi_preview` dict 加 1 行 `"suggested_duration_minutes": p.suggested_duration_minutes`。E 在 P0-E1 第 5 因列也列了同一根因，但责任归 B/D。**合并后只留 B 当 owner**，D 报告的 P0-D2 标记"由 Agent B 负责，D 仅在 prompt 加消费规则"。

**M2（合并依据）**：B P0-2 与 G P0-G2 各自独立发现 Restaurant.typical_dining_min 缺失。B 给的是字段透传方案，G 给的是 cuisine 字典回填脚本——两者其实是同一改动的两侧（schema + mock）。**应当合并为单 spec 任务**，G 主导（mock + schema），B 只在 `_restaurant_preview` 透传字段。Phase 3 W1.2 + W2.2 拆成两个 task 是对的，但要明确依赖：W2.2 必须在 W1.2 之后做，否则 preview 暴露 None 比不暴露还坏。

**M3（合并依据）**：B P0-3、G P0-G1、A P0-A1 三方都触及"年龄分桶"。B 说"P003=90min 不分年龄是问题"，G 说"升级为 SuggestedDuration dict"，A 说"加 pace_profile"。**三方实际是两层**：mock 数据层（G 的 SuggestedDuration dict）+ 用户画像层（A 的 pace_profile）。B 的"年龄段差异化"是这两层的共同表现。**最严约束取 min(SuggestedDuration[age_tier], pace_profile.single_session_max_min)**——这个公式在三份报告里没写明，但是合并后必须写进 Phase 5 spec。

**M4（合并依据）**：D 的 165 → 75 范例修复 + D 的 300min 上限太宽 + E 的 critic 无年龄校验，是**同一防守纵深的三层**：prompt 范例层（D1）+ blueprint critic 兜底层（D3 + E1）。三者改动都集中在 `agent/blueprint.py` + `agent/prompts/blueprint_prompt.py`。Phase 3 W3.1 + W4.1 已合并，但 D3（critic 上限 300）的修复和 E1 的 _age_aware_duration_critic 是**同一函数**——不应该既改 MAX_NODE_DURATION_MIN 又加 _age_aware（两者会互相冲突）。**合并取舍**：保留 MAX_NODE_DURATION_MIN=300 作为"机械异常上限"不动，加 _age_aware 作为"业务合理性"独立 critic——E 的方案 A 已正确表达此意，D3 应当撤回。

**M5（合并依据）**：A P0-A2（BlueprintLLM prompt 无年龄引导）与 D P0-D2（preview 漏字段）+ D P1-D5（review_excerpts 反向加压）都是"LLM 输入端信息不足/被污染"。A 是从意图层抱怨"我传了 age 但下游没用"，D 是从蓝图层抱怨"我看不到 age/duration 锚点"——**两个 agent 都对，根因是 prompt 加工层（D）**。合并归 D 主导，A 只负责保证 IntentExtraction.companions[].age 抽取率 ≥ 95%。

**M6（合并依据但取舍不同）**：E P0-E1 的 critic 与 F P0-F1 的 utility penalty 看起来重复，但**不应合并**——因为它们覆盖**不同路径**：E 拦 LangGraph 主路径（critic backprompt 让 LLM 重生成），F 拦 ILS 兜底路径（utility 罚分让算法换候选）。Phase 3 把这两个并列在 W4.1 + W5.1 是正确的，但需要把"对称防守"语义在 spec 里写明，避免后续维护把其中一处当冗余删掉。

**M7（合并依据）**：H P0-H1（narrator 不质疑）+ F P0-F3（_build_summary 取最长节点强化）+ E P1-E4（WARNING 对 LLM 不可见）三者都在"质疑信号缺失"这条线上。F 的 summary 强化是源头（在 itinerary_brief 阶段已经把 150min 标为"半日方案"），narrator 自然顺势复述；E 的 WARNING 不喂 LLM 是中间层；H 的 narrator prompt 不读 violations 是末端。**改动应集中在 H**：narrator 拿到的 itinerary_brief 加上 critic_summary（含 WARNING）+ 自我质疑指令；同时 F 的 _build_summary 加业务合理性 hint（这条 F P0-F3 价值小，可降级为 P1）。

**核心观察**：Phase 3 dependency-graph 的"5 因联动"已经做了最高层级合并，但**子任务级**仍有 7 处可合并，去重后总工时减少约 9-10h（Phase 3 估的 28h → 19-20h，与 hackathon 必修集吻合）。

---

## 2. 冲突方案与取舍建议（5 处冲突）

### 冲突 1：单段时长决策权——algo 决 vs critic 决 vs LLM 决（A 方案 B / E 方案 A / D 方案 B 三选一）

```text
| 方案                         | 提议 agent | 决策位置          | 时机            | 冲突点                  |
|-----------------------------|-----------|------------------|----------------|------------------------|
| A 方案 B：NodeDecider 升级    | A         | algo（node_decider.py）| BlueprintLLM 之前 | 把"主观时长"挪给 algo 决 |
| E 方案 A：critic 加 _age_aware | E         | critic 兜底       | BlueprintLLM 之后 | LLM 出错才修，正常路径无感 |
| D 方案 B：prompt 加分级表     | D         | LLM prompt        | BlueprintLLM 内 | LLM 主观决定 + prompt 软约束 |
```

**冲突表象**：三个方案职责重叠——同一个"5 岁娃 ≤ 75min"规则塞进三层会让维护痛苦（改 75 要改 3 处）。

**取舍建议（采纳）**：**D 主防 + E 兜底 + A 仅作可选 hint**。理由：

1. **prompt 主防（D）是首选**：LLM 一次过命中率最高，省 backprompt 一轮 ~2-3s 延迟（hackathon 演示对延迟敏感）。Google Research 2025-06 trip planning 与 Anthropic Constitutional AI 都支持 "prompt principle" 作为第一道防线（业界共识）。
2. **critic 兜底（E）必建**：ILS 路径不消费 prompt，唯一拦点；LLM 偶发不听话也需要拦。
3. **A 方案 B（NodeDecider 升 NodePlanHint）是过度设计**：把客观规则编码进 algo 层，与 D9 编排冻结纪律部分冲突（node_decider 不在 graph/ 但被 graph/nodes/planner.py 调用）。Hackathon 时间盒不值得做；可作为 P2 长期债。

**最终建议**：Phase 5 spec 采 D + E，A 方案 B **降级为 P2**。Phase 3 W6.5 应砍。

### 冲突 2：critic message 是否暴露 expected_range（E 方案 B vs D P1-D6 vs design.md "不暴露 dot-path"原则）

**冲突表象**：E 方案 B 提议 Violation schema 加 `expected_range: tuple[int, int]`，让 LLM 第二轮直接收敛；但 design.md 的设计哲学是"critic message 只能用自然语言，不暴露 dot-path/字段名"，避免 LLM 学到内部 schema 后绕过约束。

**核心问题**：expected_range 算"内部 schema 暴露"吗？

**取舍建议（采纳 E 方案 B 的弱化版）**：

- **暴露区间值（45-75min）算业务知识，不算 schema**——LLM 即使学到这个区间，反而是好事（业界数据本就是公开的）。**采纳**。
- **不暴露字段名 `expected_range` / `nodes[i].duration_min`**——格式化为自然语言："建议范围 45-75min，可拆为多段"。**保留 design.md 原则**。

具体落地：`format_violations_for_llm` 拼成"...建议范围 45-75min（参考儿童注意力跨度）"——message 是字符串而非结构化字段。Phase 5 spec 应明确这条边界。

### 冲突 3：Restaurant 用餐时长决策——LLM 自由 vs Schema 锁死（B P0-2 vs G P0-G2 vs F 现状）

**冲突表象**：当前 LLM 在 BlueprintLLM 阶段自由给 60min；B 提议加 typical_dining_min 字段透传给 LLM；G 提议按 cuisine 在 mock 回填。但 F P2-F8（state.weights 写但下游不消费）这种"加字段没人用"的反模式已经踩过。

**风险**：单只暴露 typical_dining_min 给 LLM 而 prompt 不加消费规则——会出现"字段加了没用"的窘境。

**取舍建议（捆绑改动）**：B + G + D 必须同 spec 落地——
- G 主导 mock + schema 字段（cuisine→duration 字典回填）
- B 在 `_restaurant_preview` 透传
- D 在 prompt 加消费规则："餐厅 duration_min 应取 candidate.typical_dining_min ±20%；显著偏离须在 rationale 解释"

任一 agent 单独修都不够。Phase 3 W1.2 + W2.2 + W3 之间**必须强依赖**，spec 里要写明"三者必须同 PR 合入，不能分批"。

### 冲突 4：替换 fallback 路由策略——retry_count 算法决 vs 违规类型 LLM 决（F 方案 E vs Phase 3 现状）

**冲突表象**：F 方案 E 提议 `replan_router` 按违规类型（RESTAURANT_FULL → llm_backprompt / HOP_INFEASIBLE → ils_fallback）路由，而非现状的 retry_count 阶梯。但 pitfalls.md P1-2026-05-23"replan 死循环修复"刚把策略改成纯 retry_count——理由是"按违规类型路由会让 LLM 选错策略陷入死循环"。

**冲突核心**：F 方案是不是**回到死循环老路**？

**取舍建议**：F 方案 E **不采纳**。理由：

1. retry_count 硬上限 4 次的简洁性是 pitfalls 反复打磨的结果，违规类型路由会引入"判断哪个策略适合哪类违规"的二阶问题——LLM 可能选错。
2. F 方案的好处（如 RESTAURANT_FULL 不切 ILS）虽然成立，但 hackathon 阶段评委看的是"是否触发 fallback"而非"fallback 是否最优"。
3. **替代方案**：保留 retry_count 阶梯，只在 critic 层面加"违规类型→建议 fix 描述"的 message hint（不影响路由决策），让 LLM 自己看着调。

**结论**：F P1-F6 标记为"长期债"，Phase 5 spec 不收。

### 冲突 5：mock 重组——v2/ 子目录 vs 软迁移 vs 不动（G 目录建议 vs Phase 3 vs Hackathon 时间盒）

**冲突表象**：G 提议 `mock_data/v2/` 升级 schema 后挪过去，留 v1 软链接兼容期；Phase 3 dependency-graph 提议直接改 mock 不分版本；hackathon 距离截稿时间紧。

**取舍建议**：**不分 v1/v2，直接原地升级 + schema Union 双兼容期**。理由：

1. mock_data 不是公共 API，只 backend/data/loader.py 一个消费方——分版本无收益。
2. schema 用 `Union[int, SuggestedDuration]` 短期双兼容，2-3 周后删 int 分支。
3. 兜底：mock_data v1 在 git 历史里，需要回滚一行命令搞定。

Phase 5 spec 应明确：**直接修改 mock_data/pois.json（dict 升级），schema Union 兼容两轮 spec 后删除 int 分支**。

---

## 3. 漏点检查（4 个被低估的环节）

8 个 agent 各管一段，但仍有以下盲点未被任一报告充分覆盖：

### 漏点 1：narrator_prompt 词典污染——同一句"暖语"可能在 8 个场景里重复（H 报告未深挖）

H 报告侧重 narrator 不质疑方案的架构问题，但未审查 narrator_prompt 内的 SOCIAL_CONTEXTS 9 选 1 模板是否会让"家庭日常 / 闺蜜出游 / 朋友放松"在 narrator 输出里**句式高度同构**——demo 评委连看 4 个场景，会发现"陪宝贝玩 X 小时" / "陪闺蜜放松 X 小时" / "陪老婆减压 X 小时"句式雷同，文案多样性不足。

**反例**：用户连按 6 个演示场景按钮，narrator 输出 6 段开场白，前 5 个字总是"今天下午"，结尾总是"哪里不合适跟我说一声"——句式套路被评委看穿，"AI 文案能力"评分项掉分。

**修复方向**：narrator_prompt 加 `style_seed: int`（按 session_id hash 派生）随机化句式模板，或在 SOCIAL_CONTEXTS 模板里每场景写 3-5 个备选模板。Phase 5 spec 应作 P1。

### 漏点 2：演示场景集 §四"业务合理性反例" 只覆盖 8 处 mock 失败案例，**没覆盖"LLM 在合规约束下出反人性方案"的反例**

G P1-G6 提了一句"演示场景集 §四要求 ≥ 8 处 available=false"，但 8 处都是**机械失败**（餐厅满座、门票售罄）——评分项 3「Demo 闭环 + 异常韧性」要求"显式触发异常并恢复"，但**评委更想看"AI 主动质疑方案合理性"的反例**（如 5 岁娃 2.5h 博物馆，narrator 应该提"这个时长可能太长"而非默默执行）。

当前演示场景集没准备这类"反例"——所有 8 个 demo 都是"LLM 一次过 + 用户满意"的顺路。**缺一类"AI 主动建议改方案"的演示场景**——这是评分项 1「场景理解」与评分项 2「Tool 编排合理性可见性」的高分点。

**修复方向**：Phase 5 spec 应在演示场景集 §四 加一条 S9："5 岁娃下午全天去博物馆"——expected：AI 输出"主活动建议 ≤ 90min，建议拆为博物馆 90min + 公园 30min"，与原方案一起呈现。这是 H P0-H1 + E 方案 A 落地后的 demo 效果。

### 漏点 3：reviews 评论文本污染（G P1-G5）只标了 6 条，但 LLM 看到 review_excerpts 反向加压（D P1-D5）的"误导性"严重程度未被独立量化

G 与 D 都触及"reviews 文本污染"，但角度不同：
- G 角度：模板批量生成时把"5 岁亲子分龄"塞进 P018 西湖游船 / P028 电影院（**type 与评论主题不匹配**）
- D 角度：P040 评论"孩子玩了 1.5 小时还想接着玩"被 LLM 解读为"应该排 1.5h+"（**评论时长被反向锚定**）

**两位 agent 都没回答**：是否所有 41 个 POI 的 review 都做过类似量化扫描？现在已知 6 处明显污染，但是否还有 P019 / P033 这类高 helpful_count 评论里藏的"长时段暗示"？

**修复方向**：Phase 5 spec 加一个验收脚本 `scripts/audit_review_template.py`——按 type 扫描 review 关键词与 POI type 的匹配率，要求"≥ 95% 评论关键词与 POI type 主题相符"。这是 G + D 都提到但都没量化的漏点。

### 漏点 4：DecisionTrace 与 problem.md / pitfalls.md 的双向同步——agent 修复后会不会引入回归

H P2-H10 提了 DecisionTrace 信息不足，但**没提**"修了 5 岁娃 2.5h 后，pitfalls.md 是否要新增条款防再犯"。Phase 5 修完成后，pitfalls.md 应当**主动追加**：

- "[P0] 2026-05-XX：BlueprintPrompt 范例 JSON 的 in-context 锚定 → 防再犯：任何 prompt 范例值改动须 grep 范例 ID 与 mock 数据一致 + 单测断言"
- "[P0] 2026-05-XX：candidate_preview 漏字段 → 防再犯：preview 字段集变更须有"preview 字段单测"覆盖所有 mock schema 字段"

**修复方向**：Phase 5 spec 的"完成定义"加一条："修复完成时，向 pitfalls.md 追加防再犯条款 ≥ 3 条"。

---

## 4. 业界对标抽检（5 条 + 可信度）

8 份报告共引用约 30 条业界对标。本节抽 5 条用 web_fetch 真实核对，列可信度。

```text
| 抽检 | 引用条目                                | URL 命中 | 内容核对结果                                                | 可信度 |
|------|----------------------------------------|----------|------------------------------------------------------------|--------|
| 1    | Google Research Optimizing LLM trip    | ✅ 200    | LLM 出 suggested_duration + importance；DP 调度 + set packing；与 Agent D/F 引用一致 | ✅ 可信 |
| 2    | TravelPlanner ICML 2024（arxiv 2402.01622）| ✅ 200 | ICML 2024 Spotlight 真；GPT-4 0.6% 真。但 E 报告引用"two-stage commonsense 通过率 87%"摘要里查不到 | ⚠ 部分存疑 |
| 3    | ITINERA EMNLP 2024（arxiv 2402.07204） | ✅ 200    | EMNLP Industry Track 真；摘要无 "User-Owned POI Database" / "typical_visit_time" 字段名。G 报告"User-Owned POI Database 每个 POI 含 typical_visit_time"措辞**可能添油加醋** | ⚠ 字段名存疑 |
| 4    | LLM-Modulo Frameworks（arxiv 2402.01817）| ✅ 200  | Position paper，作者 Kambhampati 真；但 E 报告引用"每个 critic 输出 {satisfied, confidence, suggested_fix}"是高度具体的 schema，摘要中**找不到这种字段名定义** | ⚠ 字段格式存疑 |
| 5    | Pydantic AI output_validator           | ✅ 200    | @agent.output_validator 真；ModelRetry 真。但 E 引用"默认 retries 3 次"实际官方文档明示**默认 1 次**（"defaults to 1"） | ⚠ 数字小错 |
```

**核对补充（OSM accessibility）**：G 报告引用 OSM "wheelchair=yes/limited/no" 三态——实际 web_fetch 验证是 **yes/limited/no/designated 四态**。三态归纳偏粗但方向对。

**总结**：8 份报告的业界对标 **5/5 URL 真实存在**（无幻觉链接），但**字段名与具体数字精度有 4 处小偏差**（27%）：

- TravelPlanner 87% 通过率（数字溯源不到）
- ITINERA "User-Owned POI Database / typical_visit_time"（字段名可能虚构）
- LLM-Modulo critic schema（{satisfied, confidence, suggested_fix} 字段格式可能虚构）
- Pydantic AI 默认 retries 次数（应为 1，引用为 3）
- OSM accessibility 状态值数（应为 4，引用为 3）

**对 Phase 5 的影响**：spec 里如果引用这些字段名，应当**重新核对原文或改为更宽泛的措辞**（"含访问时长字段"而非"`typical_visit_time` 字段"）。**整体可信度仍然高**（论文真实存在 + 大方向引用准确），但精确细节不可全信。

---

## 5. Phase 3 修复优先级挑战

### 5.1 W1-W8 共 8 wave / 28h（hackathon 砍到 19h）合理性评估

```text
| Wave | 工时 | 必要性 | 评估                                                |
|------|------|--------|----------------------------------------------------|
| W1   | 8h   | ★★★★★  | 信息源/schema，所有上层依赖。**合理**。但 W1.1+W1.2 可并行做（不同文件）       |
| W2   | 1.5h | ★★★★★  | preview 透传，**必修**。不能与 W1 并行（强依赖 schema） |
| W3   | 1.5h | ★★★★★  | prompt 主防，**必修**。可与 W2 并行（不同文件）       |
| W4   | 4h   | ★★★★   | critic 兜底。但 W4.4（精确营业时间）是 P0-E2 修复，**与 5 岁娃反例无关**——可砍 |
| W5   | 3.5h | ★★★    | ILS 兜底。W5.1（utility overload_penalty）必做；W5.2-5.4 可降为 P1     |
| W6   | 4.5h | ★★★    | 意图层。W6.1+W6.2 必做；W6.3-W6.5 可降为 P1（不影响 demo）  |
| W7   | 4h   | ★★★    | narrator + 输出。W7.1-W7.4 必做（评分项 2 直接相关）；W7.7 meta-critic 是新架构能力，hackathon 时间盒下**保留但作为加分项** |
| W8   | 2h   | ★★     | 通勤层。W8.1+W8.5 必做（修 docstring 漂移 + Tool 一致性）；W8.2-W8.4 可降为 P1（routes.json 重生 demo 没必要做） |
```

**Phase 3 给的 19h 必修集分析**：列了 19 个任务，加总 ~16h（含联调测试 +3h ≈ 19h）。**问题**：

- W6.5 NodeDecider 升级（0.5h）应砍——属本审查 §2 冲突 1 的过度设计
- W4.4 _check_opening_hours_after_assemble（1h）与 5 岁娃反例无关——可砍
- W7.7 meta_critic（2h）虽是加分项但风险高（新增 LLM 调用 +2-3s 延迟），应在最后做且配 ENV 开关

**优化后必修集（17h）**：去掉上述 3 项（合 3.5h），加上"W6.4 router Layer 3 启发式"（1h）和"演示场景集 S9 反例添加"（0.5h）。

### 5.2 端到端反例验证逻辑——19h 必修集真能解决"5 岁娃博物馆 2.5h"吗？

按 Phase 3 W1+W2+W3+W4+W7 修完后的 LLM 决策路径推演：

```text
输入：「今天下午想和老婆孩子出去玩，孩子 5 岁，老婆减肥」

Step 1: IntentParser（W6.1 加规则）
  → companions=[{role:孩子,age:5},{role:妻子}]
  → physical=["亲子友好","低强度"], dietary=["健康轻食"]
  → pace_profile={single_session_max_min: 75}（W1.4 + W6.1 规则触发）

Step 2: search_pois → top-5 候选（含 P040）

Step 3: BlueprintLLM 看到的 candidate_preview
  → P040 字段含 suggested_duration_minutes={default:90, kid_3_6:60, multi_gen:60}（W1.1 + W2.1）
  → restaurant.typical_dining_min=40（轻食，W1.2 + W2.2）

Step 4: BlueprintPrompt（W3）
  → 范例改 75（W3.1）+ 加分级表（W3.2）
  → LLM 看到"5 岁娃 + suggested.kid_3_6=60 + pace.single_session_max=75"
  → 输出 duration_min ∈ [60, 75]（高概率命中）

Step 5: critic（W4.1+W4.2）
  → 若 LLM 偶发出 150 → _age_aware_duration_critic 拦下，expected_range=[45, 75]
  → backprompt 重生成

Step 6: narrator（W7.1）
  → 看到 critic_summary=[{code:AGE_DURATION_MISMATCH, resolved:true, history}]
  → 文案："考虑到 5 岁宝贝的注意力，主活动安排 75 分钟，更不容易闹腾"
  → 用户感知"AI 主动考虑了孩子年龄"——评分项 1 直接加分
```

**结论**：19h 必修集（修正后 17h）**能彻底解决** 5 岁娃 2.5h 反例，且能在 demo 现场让评委看到"AI 主动质疑方案"的展示价值。但**有 3 个潜在断点**：

- **断点 1**：IntentParser 抽 age 失败率（A 报告 §6.2 估算 ≥ 95%）——若 LLM 漏抽 age，整条防御链退化为 default 桶。建议 verify_planning 增加 5-10 次"5 岁娃"压测，age 命中率 < 95% 视为回归。
- **断点 2**：mock 数据 dict 升级后旧测试 41 处单值断言全破——W1.1 工时低估（3h 不够），实际可能 5h+。Phase 5 spec 应预留缓冲。
- **断点 3**：narrator LLM 在收到 critic_summary 后是否真的输出"质疑话术"？这是 LLM 行为，不可 100% 控制。建议 narrator_prompt 加 few-shot 示例 + 温度 0.5（当前 0.7 可能让 narrator 偶尔忘记 critic_summary）。

---

## 6. 最终目录结构建议（综合 8 份报告）

8 份报告的目录建议在 dependency-graph 已合并，本审查二次梳理后给出**最终落地建议**——直接进 Phase 5 spec：

```text
backend/agent/
├── core/                       ── 全员共享底座
│   ├── llm_client.py / llm_client_stub.py
│   ├── observability_init.py
│   ├── feedback_detector.py
│   └── trace.py
│
├── intent/                     ── 意图理解层（Agent A 主导）
│   ├── parser.py               (← intent_parser.py)
│   ├── refiner.py
│   ├── router.py
│   ├── narrator.py             ← Agent H 建议放 runtime/，但 narrator 与 intent 共用 social_context 词典，归 intent 更内聚
│   └── prompts/                ← 拆 system_prompt.py
│       ├── intent_parser_prompt.py
│       ├── refiner_prompt.py
│       ├── router_prompt.py
│       └── narrator_prompt.py
│
├── planning/                   ── 规划主路径（Agent B/D/E/F/G 主导）
│   ├── blueprint/
│   │   ├── blueprint.py        ← 含 _age_aware_duration_critic（E 方案 A）
│   │   ├── blueprint_llm.py    ← 含改后的 _poi_preview / _restaurant_preview
│   │   ├── assemble_blueprint.py
│   │   ├── node_decider.py     ← 仅决 kind，不决时长（拒 A 方案 B 升级）
│   │   └── prompts/
│   │       └── blueprint_prompt.py
│   ├── critic/
│   │   ├── critics_v2.py       ← 含 AGE_DURATION_MISMATCH 镜像
│   │   └── social_compat.py
│   ├── commute/
│   │   └── lookup_hop.py
│   └── weights_llm.py          ← 文件顶部加 # FROZEN 注释（仅 ILS 路径）
│
├── runtime/                    ── 运行时框架（Agent H）
│   ├── react_agent.py          (← v2/react_agent.py)
│   ├── output_types.py
│   ├── orchestrator.py
│   ├── conversation.py
│   ├── tool_provider.py
│   ├── deps.py / model_factory.py
│   └── tools/                  ── (search_adapter.py)
│
├── graph/                      ── LangGraph 主路径（动 nodes/ 内部，不动 build.py 拓扑）
│   ├── nodes/
│   │   ├── (现有 11 个) +
│   │   └── meta_critic.py      ← 可选 W7.7（LLM-based business critic）
│   ├── state.py                ← 删 routes 字段（H P2-H8）
│   ├── build.py
│   └── sse_adapter.py          ← DONE payload 加 6 字段（H P0-H2）
│
└── legacy/                     ── 冻结模块（Agent F 主导）
    ├── planner_rule.py         (← planner.py)
    ├── ils_planner.py          (← planner_hybrid.py)
    ├── llm_first_planner.py    (← planner_llm_first.py)
    ├── llm_planner.py
    ├── ils_score_critic.py     (← critics.py)
    ├── executor.py             ← 与 graph/nodes/execute_finalize.py 双轨，docstring 标冻结
    └── segment_decider.py      ← 兼容 alias，下次 spec 删
```

**与 Phase 3 dependency-graph §五"目录重组建议综合"差异**：

1. **narrator.py 归 intent/ 而非 runtime/**：H 建议放 runtime/，但 narrator 输出与 intent 的 social_context 9 选 1 强耦合，更内聚的归 intent/。
2. **node_decider 留在 planning/blueprint/**：与 Phase 3 一致，但本审查明确"拒升级为 NodePlanHint"。
3. **保留 graph/ 不动 build.py 拓扑**：与 §3.3.1 编排冻结纪律一致，**业务 critic 加在 critic_node 内部**而非新加 meta_critic_node（除非 W7.7 落地）。
4. **mock_data/profiles/ 不分 v1/v2**：与 G 建议 v1+v2 双版本不同，本审查 §2 冲突 5 已说明直接原地升级。

**重组工时**：Phase 3 估 6h，本审查认为**应砍到 3h**（hackathon 时间盒下，质量修复后再做重组——不要在评委面前因为大量 import 路径变化而 demo 翻车）。**建议**：spec A（业务质量）在前，spec B（目录重组）在 spec A 全部落地 + 联调通过后再做。

---

## 7. 风险红旗（5 个）

### 红旗 1：mock 数据 dict 升级后，21 个 verify 脚本全部断言失效（高风险）

**风险**：`backend/scripts/verify_planning.py` + `backend/tests/` 共 ~21 个测试文件断言"P003.suggested_duration_minutes == 90"这类**单值**。G 方案 A 把它升为 dict 后，**所有这些断言一夜之间报错**——要么改测试断言为 dict 路径，要么写 schema Union 兼容。

**爆炸点**：W1.1 工时估 3h，但若包含 21 处断言迁移，可能膨胀到 6-8h。Phase 5 spec 应**先 grep 确认改动面**：

```bash
grep -r "suggested_duration_minutes" backend/tests/ backend/scripts/ | wc -l
```

若结果 > 30 行，工时翻倍。

**缓解**：schema 用 `Union[NonNegativeInt, SuggestedDuration]`（双兼容期）+ helper 函数 `get_duration_for_companions(poi, companions)` 统一调用——测试只 mock helper 不直接读字段。

### 红旗 2：critic backprompt 重生成命中率假设过高（中风险）

**假设**：E 方案 A + Phase 3 W4 落地后，5 岁娃 165 min 场景"critic 拦 → backprompt → LLM 重出 75 min" 命中率 ≥ 90%。

**质疑**：

1. LLM 一次过率多少？无端到端测试数据。pitfalls.md 没记 backprompt 命中率指标。
2. 第二轮 LLM 收到"建议 45-75min"后会不会出 80min（仍不合规但接近）？这是 design.md "expected_range" 设计的核心目的，但**LLM 收到自然语言"45-75"是否真的取区间值**？没人验证过。
3. 若两轮 LLM 仍不合规，会切 ILS——ILS overload_penalty（W5.1）能否拦？取决于 weights 配比，可能仍漏。

**缓解**：Phase 5 spec 必须包含端到端 e2e 测试 5-10 次，统计：
- 首轮命中率（LLM 一次过 5 岁娃 ≤ 75min 的比例）
- backprompt 命中率（首轮违规后重生成命中的比例）
- ILS 兜底命中率
- give_up 率

期望前三档累计 ≥ 95%。若 < 95%，方案 D（meta-critic）必须落地。

### 红旗 3：Phase 5 改完 prompt 后，原 21 个 prompt 测试（含字符数 cap、关键词断言）全破（中风险）

**风险**：D 方案 B 把 prompt cap 从 1500 提到 2200（W3.3）；prompt 范例 165→75（W3.1）；加分级表（W3.2）。`tests/test_blueprint_prompt.py` 有 ~6-10 条断言可能破：

- `test_system_prompt_length_under_hard_cap` 断言 ≤ 1500
- `test_blueprint_prompt_contains_target_id` 可能断言具体 165
- `test_socials_listed` 断言 SOCIAL_CONTEXTS 内联（D 提议挪到 user message 省 80 字）

**缓解**：W3 完成时同步改测试断言；e2e 跑全套 backend tests，确认 0 红灯。

### 红旗 4：narrator LLM 行为不可控——critic_summary 喂进去后真的会"质疑方案"吗（中风险）

**风险**：H 方案 A 在 narrator_prompt 加"如收到 critic_summary，必须在文案中提质疑句"，但**LLM 不一定听**——温度 0.7 + 上下文长 + 已有"暖语气"指令，新加指令可能被覆盖。

**反例风险**：narrator 收到 critic_summary 仍输出"陪宝贝玩 75 分钟"（不质疑），评委以为修复无效——比"修了但不见效"更糟的是"看起来没修"。

**缓解**：

1. narrator_prompt 加 2-3 条 few-shot 示例（"输入 X → 输出 Y 含质疑句"）
2. 温度从 0.7 降到 0.5（牺牲一点多样性换稳定性）
3. **fallback 兜底**：narrator 模板（template 路径）加一段"if intent.companions has young kid AND any node.duration > 90: 强制追加 '主活动安排 N 分钟，刚好不会让宝贝累'"——LLM 失败时模板兜底也能给评委看到"质疑感"

### 红旗 5：编排冻结纪律破窗——meta_critic_node（W7.7）会不会引发后续"加节点风潮"（低-中风险）

**风险**：W7.7 在 graph/nodes/ 加 meta_critic.py + 改 build.py 拓扑（critic → meta_critic → narrate）。AGENTS.md §3.3.1 明确"新功能改动只在 graph/ 加"——这条没破规则。但**破窗效应**：以后任何 agent 看到"原来可以加节点啊"，会陆续提"我也要加 commute_critic / safety_critic / cost_critic"——graph 拓扑膨胀到 15+ 节点，重新进入 v2/ 时代的混乱。

**缓解**：

1. Phase 5 spec 明确："此次只允许加 1 个新节点 meta_critic_node，下次再加新节点须跑过 brainstorm + 4 层架构边界检查"
2. meta_critic_node 内部用 LLM 实现，避免成为多个规则 critic 的容器（规则 critic 留在 critics_v2 内部加方法）
3. 把 meta_critic 设为"可选"——配 ENV `ENABLE_META_CRITIC` 开关，hackathon 演示打开，回归测试关闭，避免 LLM 调用拖慢 CI

---

## 8. 总结：可以进 Phase 5 spec 吗？

**结论：可以，但有保留。**

### 8.1 可以进 spec 的依据

- 8 份报告深度足够（~9 万字 + 25 子环节全覆盖）
- Phase 3 dependency-graph 把 5 因联动梳清楚了
- 业界对标 5/5 URL 真实（虽有 4 处字段名细节存疑，不影响方向）
- 端到端反例验证逻辑（§5.2）证明 19h 必修集**能解决** 5 岁娃 2.5h 反例
- 5 个风险红旗有具体缓解方案

### 8.2 进 Phase 5 前必须修正的 6 处

1. **§1 M3 合并必须明确**：mock dict + pace_profile + age 取最严是单一公式，不能三个 agent 各写一份
2. **§2 冲突 1 取舍写明**：拒 NodeDecider 升级（W6.5 砍）
3. **§2 冲突 5 取舍写明**：mock 不分 v1/v2，直接升级 + Union 双兼容
4. **§3 漏点 2 落地**：演示场景集 §四 加 S9"5 岁娃博物馆"反例
5. **§5.1 优化必修集**：W4.4 / W6.5 / W7.7 重新评估必要性，工时砍到 17h（含 +3h 联调缓冲到 20h）
6. **§7 红旗 1 缓解**：W1.1 加 grep 改动面前置任务，工时上浮到 5h

### 8.3 不进 Phase 5 的事

- F 方案 E 按违规类型路由（与 pitfalls.md 死循环修复冲突）
- A 方案 B NodeDecider 升级为 NodePlanHint
- G 目录建议 mock_data/v2/ 子目录
- H 方案 G 加 NodeDecision 字段（评分边际收益不高）
- 业界对标存疑细节（TravelPlanner 87%、ITINERA typical_visit_time、LLM-Modulo schema）不进 spec 文案

### 8.4 spec 拆分建议

- **spec A：planning-quality-deep-review**（业务质量主线）—— W1+W2+W3+W4+W5.1+W6.1-W6.4+W7.1-W7.5+W8.1+W8.5（17-20h）
- **spec B：agent-directory-restructure**（目录重组）—— spec A 全部落地 + 联调通过 + demo 验收后启动（3-6h）
- **spec C（可选 P2）**：meta_critic 节点 + advanced quality（W7.7 + W7.8 + 演示场景 S9）—— 仅作为评分加分项，hackathon 时间允许时做

---

> 报告完结。共 ~6800 字（不含 7 张表格代码块）。

**对主代理的一句话总结**：Phase 4 完成，发现 7 处重复合并 / 5 处冲突 / 4 个漏点 / 4 条对标存疑（其中精度小错） / 5 个风险红旗，建议**有保留地进 Phase 5**——必须先按 §8.2 修正 6 处后再启动 spec A，spec B 应延后到 spec A 联调通过后启动。
