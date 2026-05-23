# Requirements Document

## Introduction

把规划链路从「**形式合规但反业界常识**」升级为「**主防 + 兜底 + 主动质疑**三层防御的业务质量可信链路」。

**根因故事**：用户实测「家庭主线 5 岁娃博物馆主活动 2.5h（150min）」。行业常识 5 岁娃单 POI 段 ≤ 75min（Smithsonian SEEC 学龄前 25min 单展项 + 切换基线 / Hands-On House 90min cap / Brain Balance `attention_span ≈ 2-3min × age`）。当前 8 个规划环节、3 套 critic、2 类 mock 数据、5 个 LLM prompt **没有任何一处**对此有感知——150min 在所有 critic 路径全绿灯，narrator 顺势复述"陪孩子玩两个半小时"。

8 个并行子代理深度审查发现：

```
| 五因联动                                | 权重 | 直接修复点               |
|----------------------------------------|------|------------------------|
| [1] BLUEPRINT_SYSTEM_PROMPT 范例 165min  | 30%  | Agent D 改 prompt 范例   |
| [2] _poi_preview 漏 suggested_duration | 25%  | Agent B 透传字段         |
| [3] prompt 完全无年龄分级时长表          | 20%  | Agent D 加分级表         |
| [4] critic 三套全无单段年龄校验          | 15%  | Agent E 加 _age_aware    |
| [5] mock POI suggested_duration 不分年龄 | 10%  | Agent G 升级 dict        |
```

任一单点修复都不彻底。业界共识（TravelPlanner ICML 2024 + LLM-Modulo NeurIPS 2024 + Google AI Trip Planning 2025-06）是 **prompt 主防 + 数据信息源补 + critic 兜底三层联动**。

**Hackathon 时间盒约束**：~17-20h 工时落地，分 7 个 wave。**保留 LangGraph 主路径拓扑，不改 build.py edge**；只在节点内部加规则、prompt 加约束、schema 加字段、critic 加规则。

**与 itinerary-edge-model-refactor 关系**：上次重构解决「数据模型语义统一」（结构性），本次解决「业务合理性 + 工程严谨」（质量性）。两次正交，本次不会破坏上次成果，只在 edge_v1 模型基础上**填充业务约束层**。

## Glossary

- **Pace Profile**：节奏画像。`{single_session_max_min, total_active_min, break_every_min, preferred_dwell_min}`，从 IntentExtraction.companions 推导或从 Persona.default_pace_profile 注入。
- **Suggested Duration（升级版）**：POI 推荐时长按主导客群分桶。从 `Optional[int]` 升级为 `dict | int`：`{default, kid_3_6, kid_7_12, senior, multi_gen}`。Pydantic 用 `Union[int, SuggestedDuration]` 双兼容。
- **Typical Dining Min**：餐厅典型用餐时长。新增字段，按 cuisine 业界惯例回填（健康轻食 40min / 粤菜 90min / 商务接待 +15）。
- **Age-Aware Duration Critic**：单段时长按同行人年龄分级的 critic。在 #13 blueprint critic 实现（拦 LLM 主路径），#14 critics_v2 加 `AGE_DURATION_MISMATCH` 镜像（拦 ILS 兜底路径），形成对称防守。
- **Expected Range**：critic 给 LLM 的"建议区间"。Violation schema 加 `expected_range: tuple[int, int]` 字段，`format_violations_for_llm` 拼成自然语言「建议范围 45-75min」（**不暴露字段名**，遵守 design.md "不暴露 dot-path"原则）。
- **Overload Penalty**：ILS utility 函数的"过载惩罚"维度。新增项 `-0.5 × _overload_penalty(poi, intent)`，在算法兜底路径拦下 5 岁娃 180min 类候选。
- **5 因联动**：5 岁娃 2.5h 反例的根因聚类（详见 Introduction 表）。
- **17h 必修集**：Phase 4 联合审查修正后的 P0 任务集（Wave 1-7 共 17 任务，含联调测试 +3h ≈ 20h）。

## Requirements

### Requirement 1: Mock 数据信息源升级（schema + 数据）

**User Story**: As Agent G mock 数据维护者, I want 把 `Poi.suggested_duration_minutes` 升级为按年龄分桶 dict + Restaurant 加 `typical_dining_min`, so that 下游 LLM / critic 看到的字段密度对齐 Google Trips / TripAdvisor / Foursquare 业界基线。

#### Acceptance Criteria

1. WHEN `backend/schemas/domain.py` 重构, THE `Poi.suggested_duration_minutes` SHALL 从 `Optional[NonNegativeInt]` 升级为 `Union[NonNegativeInt, SuggestedDuration]`，其中 `SuggestedDuration` 含 `default: NonNegativeInt`（必填）+ `kid_3_6 / kid_7_12 / senior / multi_gen: Optional[NonNegativeInt]`（可选）。
2. WHEN `Restaurant` schema 升级, THE 模型 SHALL 加 `typical_dining_min: Optional[NonNegativeInt]` 字段。
3. WHEN `mock_data/pois.json` 全量回填, THE 41 个 POI SHALL 全部含 `suggested_duration_minutes` dict 形态；按 `_AGE_TIER_RULES`（参考 Smithsonian SEEC + Hands-On House）按 type 给 default + 至少 1 个 age 桶。
4. WHEN `mock_data/restaurants.json` 全量回填, THE 48 个餐厅 SHALL 全部含 `typical_dining_min`，按 `_CUISINE_DINING_MIN` dict 回填（健康轻食 40 / 咖啡 45 / 下午茶 75 / 粤菜 90 / 火锅 120 / "高人均"+15 / "私房菜"+15 等）。
5. WHEN 旧 verify 脚本 / 单测引用 `Poi.suggested_duration_minutes` 单值, THE Pydantic 双兼容 SHALL 让旧引用以 default 桶值返回；单测全部 pass。
6. WHEN `mock_data/personas.json` 升级, THE 5 个 persona SHALL 全部含 `default_pace_profile: PaceProfile`，按 notes 字段结构化（u_dad: `{single_session_max_min: 90, break_every_min: 45, preferred_dwell_min: 75}` 等）。
7. WHEN `mock_data/_samples/*.example.json` 与生产 mock schema 漂移检测, THE 自动化脚本 SHALL 跑 mock 加载 + Pydantic validate + 字段缺失 audit；任一断言失败即测试失败。
8. WHEN reviews 文本污染审计, THE `scripts/audit_review_template.py` SHALL 扫描 41 个 POI 的 review 关键词与 type 主题匹配率；要求 ≥ 95%。

### Requirement 2: 候选预览字段透传（让 LLM 看到锚点）

**User Story**: As BlueprintLLM, I want 在 `_poi_preview` / `_restaurant_preview` 看到 `suggested_duration_minutes` 与 `typical_dining_min` 字段, so that 我决定 `duration_min` 时有权威锚点而不是凭训练先验拍脑袋。

#### Acceptance Criteria

1. WHEN `backend/agent/blueprint_llm.py:_poi_preview` 升级, THE 函数 SHALL 在 dict 输出加 `"suggested_duration_minutes"` 字段；按 `intent.companions[].age` 主导桶投影（含 ≤6 岁 → kid_3_6 / 含 ≥75 岁 → senior / 多代 → multi_gen / 否则 default）。
2. WHEN `_restaurant_preview` 升级, THE 函数 SHALL 在 dict 输出加 `"typical_dining_min"` 字段。
3. WHEN POI 没有 candidates 满足距离, THE `SearchPoisOutput` SHALL 加 `effective_distance_max_km: float` 字段记录实际放宽到的距离（首次 5km → 兜底 +2km），让 LLM 在 rationale 解释。
4. WHEN candidate_preview 输出, THE 字段 `commute_matrix` SHALL 仍然不出现（已在 edge_v1 删除，本 spec 维持）。
5. WHEN preview 字段单测覆盖, THE `tests/test_blueprint_llm.py` SHALL 新增 ≥ 5 项断言（dict 投影正确 / 字段非 None / 多代场景取最严桶 / 等）。

### Requirement 3: BlueprintLLM 主防（prompt 层）

**User Story**: As BlueprintLLM 蓝图规划师, I want prompt 里有「按 companion age 分级时长表」 + 范例值改成合理数（75 而非 165）, so that 我一次过命中"5 岁娃 ≤ 75min"业务约束。

#### Acceptance Criteria

1. WHEN `backend/agent/prompts/blueprint_prompt.py` 升级, THE `BLUEPRINT_SYSTEM_PROMPT` 范例 JSON SHALL 把 `duration_min: 165` 改成 `duration_min: 75`，`kind: "主活动"` 改成 `"看展"`（避免「主活动 = 长时段」隐性等式）。
2. WHEN prompt 加业务规则, THE BLUEPRINT_SYSTEM_PROMPT SHALL 在「硬性约束」段加紧凑版「按 companion age 分级时长表」（≥ 6 条规则：婴幼儿 ≤45 / 学龄前 ≤75 / 学童 ≤120 / 长辈 ≤90 / 高龄 ≤60 / 多代取最严）。
3. WHEN prompt 加规则后总字符数, THE prompt 容量 cap SHALL 从 1500 字符提到 2200 字符；`tests/test_blueprint_prompt.py` 同步更新。
4. WHEN prompt 加候选预览消费规则, THE BLUEPRINT_SYSTEM_PROMPT SHALL 加：「target 的 `suggested_duration_minutes` / `typical_dining_min` 是参考时长，duration_min 必须取该值或在 ±25% 区间内；显著偏离须在 rationale 解释」。
5. WHEN prompt 关键词检测, THE 单测 SHALL 验证 prompt 含 `["suggested_duration", "typical_dining", "5 岁", "75min", "ages ≤", "学龄前", "建议范围"]`（每条都不能省）。
6. WHEN 演示场景集 §四 自检, THE 8 个场景的 demo 跑通 SHALL 让 BlueprintLLM 对 5 岁娃 / 70 岁老人场景输出 `duration_min ∈ [60, 90]` 的命中率 ≥ 90%（首轮）。

### Requirement 4: BlueprintCritic + critics_v2 主防 + 兜底（critic 层）

**User Story**: As BlueprintCritic / critics_v2, I want 加 `_age_aware_duration_critic` + `AGE_DURATION_MISMATCH` 双层校验 + Violation 加 `expected_range`, so that LLM 偶发不听话时拦下并给出明确收敛区间。

#### Acceptance Criteria

1. WHEN `backend/agent/blueprint.py` 升级, THE 模块 SHALL 加 `_age_aware_duration_critic(blueprint, intent) -> list[BlueprintViolation]` 函数；逻辑参考 Agent E 报告 §4 方案 A 草稿（`_resolve_age_caps` + 主 critic 函数 + 接入点）。
2. WHEN `_age_aware_duration_critic` 命中, THE 违规 SHALL 满足 `severity="hard"` + `message` 含「建议范围 N-Mmin（基于 X 岁同行人）」；不暴露 `nodes[i].duration_min` 字段名（design.md "不暴露 dot-path"原则）。
3. WHEN `backend/agent/v2/critics_v2.py` 升级, THE `ViolationCode` SHALL 加 `AGE_DURATION_MISMATCH = "age_duration_mismatch"`；`validate_itinerary` 内部加 `_check_age_aware_duration` 镜像（防 ILS 兜底路径绕过 blueprint critic）。
4. WHEN `Violation` schema 升级, THE 模型 SHALL 加 `expected_range: tuple[int, int] | None` 字段；`format_violations_for_llm` 拼接成「...建议范围 {lo}-{hi}min」自然语言（不暴露字段名）。
5. WHEN 9 类 critic message 回填 expected_range, THE DURATION_OUT_OF_RANGE / HOP_INFEASIBLE / DISTANCE_EXCEEDED / AGE_DURATION_MISMATCH 四类 SHALL 全部填上 `expected_range`；其余可选。
6. WHEN `_check_demo_restaurant_full` 修复, THE 函数 SHALL 不再写死 `_DEMO_FULL_TIME = "17:00"`；改为查 mock `reservation_slots[time].available` 真值（与旧 critics.time_window 合并）。
7. WHEN `assemble` 后的精确营业时间校验, THE critics_v2 SHALL 加 `_check_opening_hours_after_assemble` 函数，按 `node.start_time + node.duration_min` 真实区间查 mock POI/Restaurant 的 `opening_hours`；blueprint critic 的版本降级为「硬上限粗筛」。
8. WHEN critic 重生成命中率统计, THE 端到端 e2e 测试（5 岁娃场景跑 5-10 次）SHALL 满足：首轮命中率 + backprompt 命中率 + ILS 兜底命中率累计 ≥ 95%。

### Requirement 5: ILS 算法兜底路径（utility 加 overload_penalty）

**User Story**: As planner_hybrid ILS 算法兜底, I want utility 函数加「过载惩罚」维度, so that LLM 主路径失败 fallback 到 ILS 时也不会输出 5 岁娃 180min 陶艺工坊。

#### Acceptance Criteria

1. WHEN `backend/agent/planner_hybrid.py:_utility` 升级, THE 函数 SHALL 加 `_overload_penalty(poi, intent) -> float` 子函数（参考 Agent F §4 方案 A 草稿），公式：
   ```
   cap = MAX_NODE_DURATION_MIN
   for c in intent.companions:
       if c.age <= 6: cap = min(cap, 75)
       elif c.age >= 75: cap = min(cap, 60)
   suggested = poi.suggested_duration_minutes or 90
   actual = min(suggested, cap)
   return 0.3 if actual < suggested else 0
   ```
2. WHEN utility 重新加权, THE 公式 SHALL 加项 `-0.5 × _overload_penalty(poi, intent)`；保留原有 4 维（comfort/time/cost/smoothness）。
3. WHEN `DINING_SLOTS` 修复, THE `planner_hybrid.py` SHALL 改用 `_resolve_time_window(intent, segments)`（来自 planner.py），不再硬编码 `("17:00","17:30","18:00")`。
4. WHEN `_retry_with_critic_feedback` 升级, THE 黑名单 SHALL 覆盖 ≥ 4 类违规（time_window / hard_constraint / dietary / social_context），不再仅 2 类。
5. WHEN ILS 兜底端到端测试, THE 5 岁娃场景在 LLM 主路径假装失败时切 ILS, ILS 输出 SHALL 不含「5 岁娃单 POI > 75min」的候选。

### Requirement 6: Narrator 主动质疑（输出层）

**User Story**: As Narrator, I want 收到 critic_summary + critic 历史信号后, 主动质疑方案（如"主活动 75min 给 5 岁娃刚好"），so that 用户感知"AI 真的在为我考虑"，提升评分项 1。

#### Acceptance Criteria

1. WHEN `backend/agent/narrator.py:build_narrator_user_message` 升级, THE 函数 SHALL 在 `intent_brief / itinerary_brief` 之外加 `critic_summary: str`（最多 3 条 critical 历史，含 resolved 标记）+ `quality_warnings: list[str]`（meta-critic 可选输出）。
2. WHEN `narrator_prompt.py:NARRATOR_SYSTEM_PROMPT` 升级, THE prompt SHALL 加规则：「如收到 critic_summary 或 quality_warnings，必须在文案中提一句质疑性建议（例：'考虑到 5 岁宝贝注意力，主活动安排 75min 不会让宝贝累'）」。
3. WHEN narrator LLM 温度调整, THE temperature SHALL 从 0.7 降到 0.5（牺牲多样性换稳定性，让 critic_summary 指令不被"暖语气"覆盖）。
4. WHEN narrator template fallback 加质疑兜底, THE `_template_narration` SHALL 在 intent.companions 含 ≤ 6 岁孩 + 任 node.duration_min > 90 时强制追加质疑文案，避免 LLM 失败时模板也失败。
5. WHEN narrator prompt 加 few-shot 示例, THE 示例 SHALL 含 ≥ 2 条「输入 X → 输出 Y 含质疑句」（5 岁娃 + 老人场景各 1）。
6. WHEN demo 8 场景跑通后, THE narrator 输出 SHALL 让评委连看 6 个场景时句式多样性提升（不再千篇一律「今天下午...哪里不合适跟我说一声」）。
7. WHEN `agent/graph/sse_adapter.py` 末尾改, THE DONE event payload SHALL 含 `{final_strategy, plan_attempts, critic_attempt_count, fallback_hops_count, total_ms, has_itinerary}` 6 个字段。

### Requirement 7: state 一致性 + 跨轮泄漏修复

**User Story**: As LangGraph state, I want refiner 重置 trace 累积字段 / narrate 用 model_copy / state.routes 死字段删除, so that 跨轮反馈不污染 trace、不变 mutate 不破 LangGraph 不可变范式。

#### Acceptance Criteria

1. WHEN `backend/agent/graph/nodes/refiner.py:refiner_node` 升级, THE return dict SHALL 加 `critic_attempts=[] / fallback_chain=[] / alternatives=[] / quality_issues=[]` 重置字段，避免 Agent H P1-H3 跨轮泄漏。
2. WHEN `backend/agent/graph/nodes/narrate.py:narrate_node` 升级, THE 节点 SHALL 用 `itinerary.model_copy(update={"decision_trace": ...})` 返回新对象，不再原地 mutate（Agent H P1-H6）。
3. WHEN `backend/agent/graph/state.py` 升级, THE AgentState SHALL 删 `routes: list[Any]` 字段（Agent H P2-H8 死字段）；`make_initial_state` 与 refiner 同步。
4. WHEN `backend/agent/graph/nodes/execute_finalize.py:execute_finalize_node` 升级, THE 函数 SHALL 用 `[n for n in nodes if target_kind=="restaurant"]` 全量遍历下单，不再仅 `next(...)` 取首个；并加 confirm 阶段 narrator 调用（`generate_narration(stage="confirm")`）。

### Requirement 8: 意图层 + Refiner（识别"主活动太久"反馈）

**User Story**: As Refiner, I want 识别「太久 / 太长 / 盯不住」类反馈并映射到 `single_session_max_min` 而不是 `distance_max_km`, so that 用户嫌主活动太久时系统不会错改距离。

#### Acceptance Criteria

1. WHEN `backend/schemas/intent.py` 升级, THE `IntentExtraction` SHALL 加 `pace_profile: Optional[PaceProfile]`，PaceProfile 含 `single_session_max_min / total_active_min / break_every_min / preferred_dwell_min`。
2. WHEN `backend/agent/prompts/system_prompt.py` 升级, THE INTENT_PARSER 隐含规则 SHALL 加 4 条：「ages ≤ 6 → single_session_max_min ≤ 90」「老人 / `适合老人` → ≤ 90 + break_every_min ≤ 60」「独处放空 → ≥ 60」「商务接待用餐 → ≥ 90」。
3. WHEN `backend/agent/refiner.py:_rule_fallback` 升级, THE 字典 SHALL 加 `_KEYWORDS_SESSION_TOO_LONG = ("太久", "太长", "盯不住", "无聊", "扛不住", "腻了")`；命中后**不动 duration_hours / distance_max_km**，而产出 `pace_profile.single_session_max_min` 缩 30%。
4. WHEN `_extract_duration_from_feedback` 升级, THE 函数 SHALL 扩支持「半小时」/「30 分钟」/「一个半小时」三类正则（除现有 1-12 整数小时）。
5. WHEN `feedback_detector.looks_like_feedback` 同步升级, THE 启发式 SHALL 命中 `_KEYWORDS_SESSION_TOO_LONG`，避免 Layer 1 强信号路径漏判反馈意图为 chitchat。

### Requirement 9: 演示场景集 + 评分项加分

**User Story**: As 演示场景集 §四, I want 加 S9「AI 主动质疑方案」反例 + 8 场景跑通后 narrator 输出多样性达标, so that 评委看到"AI 不只是规划工具，还会主动质疑方案"，直接命中评分项 1（场景理解 20%）+ 评分项 2（Tool 编排合理性 25%）。

#### Acceptance Criteria

1. WHEN `docs/01-requirements/演示场景集.md` 升级, THE 自检表 SHALL 加 S9：「输入「5 岁娃下午全天去博物馆」→ 期望 AI 输出「主活动建议 ≤ 90min，建议拆为博物馆 90min + 公园 30min」+ 与原方案一起呈现」。
2. WHEN 8 场景全跑通验证, THE narrator 输出 SHALL 满足：句式多样性（开场白 / 结尾不出现 ≥ 4 次重复套路）+ 至少 2 个场景命中"AI 主动质疑"文案。
3. WHEN 端到端跑 5-10 次「家庭主线 5 岁娃」场景, THE 总时长 SHALL 命中「5 岁娃单 POI ≤ 75min」率 ≥ 90%；critic 拦截率（首轮 + backprompt 累计）≥ 95%。

### Requirement 10: 防再犯条款 + pitfalls.md 同步

**User Story**: As 工程债维护者, I want 修复完成时主动追加 pitfalls.md 防再犯条款, so that 下次有人改 prompt / preview / mock schema 时立刻看到历史教训。

#### Acceptance Criteria

1. WHEN spec 全部 task 完成, THE `docs/03-implementation/pitfalls.md` SHALL 追加 ≥ 3 条防再犯条款：
   - [P0] BlueprintPrompt 范例 JSON 的 in-context 锚定 → 防再犯：任何 prompt 范例值改动须 grep 范例 ID 与 mock 数据一致 + 单测断言。
   - [P0] candidate_preview 字段集 → 防再犯：preview 字段集变更须有"preview 字段单测"覆盖所有 mock schema 字段。
   - [P0] critic 三套职责漂移 → 防再犯：新增 critic 必须在「主防 / 兜底」分层中明确归属，message 必须含 expected_range 自然语言。
2. WHEN `problem.md` 追加, THE 文件 SHALL 含本次 spec 的「问题/方案/修改文件/达成效果」记录（按全局 problem.md 格式）。

## Out-of-Scope（v1 不做，留 v2 / spec C）

```text
| 范围                                  | v2 留坑                                         |
|--------------------------------------|----------------------------------------------|
| meta_critic_node（LLM-based business critic） | 加分项；hackathon 时间允许时做（spec C） |
| NodeDecider 升级为 NodePlanHint        | 拒（联合审查冲突 1：D 主防 + E 兜底已足够）    |
| replan_router 按违规类型路由           | 拒（pitfalls.md 死循环修复冲突）              |
| mock_data/v2/ 子目录双版本             | 拒（直接原地升级 + Union 双兼容）              |
| DecisionTrace 加 NodeDecision 字段     | 评分边际收益不高，留 v2                       |
| ILS 自适应迭代 + 退火温度衰减          | F P1-F4，工时 2h，留 v2                        |
| Buffer 按 companions 浮动              | F P1-F5，工时 1h，留 v2                        |
| narrator 句式 style_seed 多样化        | 漏点 1，留 v2                                 |
| 业界对标精度复核（TravelPlanner 87% 等）| 文案细节，留 v2                               |
| spec B：agent-directory-restructure   | 单独 spec，本 spec A 联调通过 + demo 验收后启动|
```
