# Requirements Document

## Glossary

- **LLM-Modulo**：Kambhampati 团队提出的范式（arxiv 2402.01817 + NeurIPS 2024），核心是「LLM 出方案 + 外置 sound critic 兜底」。本项目当前 graph/build.py 已是同构形态——**spec C 的工作是显式承认这一点 + 工程化加固**。
- **ItiNera-style 分工**：「LLM 出语义打分 + 算法解空间组合」（EMNLP 2024 Industry Track）。本项目已部分落地（weights_llm.py 出 4 维权重），但 _utility 未把 LLM 输出当 single profit score。
- **TravelAgent 三层 schema**：`hard / soft / commonsense` 用户画像三层（arxiv 2409.08069 §3.1）。本项目 user_profile.json 当前仅含 hard 层（4 字段），soft + commonsense 缺失。
- **grounding-first**：Google AI Trip Ideas 的失败处理设计哲学（research.google blog 2025-06）——把硬约束（闭店 / 距离 / 年龄 cap）从事后 critic 上提到候选生成阶段强过滤，让 ILS 看不见不可行候选。
- **CRITIC_FEEDBACK_MODE**：本 spec 引入的 env flag，三档 `pinpoint-all`（默认）/ `first-only`（论文 first-only 与 full-feedback 性能等价但省 token）/ `reward`（dense scalar，给未来 RL 路径预留挂钩点）。
- **TOOL_RESPONSE_INCONSISTENCY**：本 spec 新增的第 11 个 ViolationCode——检查 itinerary.target_id 必须在 tool_results 候选池里（DeepTravel hallucination 50%→<20% 的核心防线）。
- **memory_writer 节点**：本 spec 新增的 LangGraph 节点，挂在 narrate 后，把当次 itinerary 摘要写回 user_profile.recent_trips（最多保留 5 条）。
- **double-fold ChatDock**：双层折叠 UX——ChatDock 默认收起（学携程 LUI 浮标）+ ToolTracePanel 默认收起、按需展开（保留评委决策可见性）。解决联合审查隐藏冲突 1。
- **三候选并列**：日本 NAVITIME 20+ 年招牌 UX——同一 query 出 3 个不同侧重的方案 + 三轴评分（时长合规度 / 距离合理度 / 偏好匹配度），让评委在 demo 现场切换。

## Introduction

把规划链路从「LLM-Modulo 同构但未显式承认 + ItiNera-style 分工部分落地 + TravelAgent 三层 schema 缺失」三个状态升级为「显式 + 完整 + 标注」的产品级算法骨架。

**根因故事**：Phase 1 全 8 范式调研 + Phase 2 联合审查得出三个铁律：

1. **「LLM-only 路径在 trip planning 上不可行」是行业共识**——TravelPlanner 0.6% / GPT-5 21.2% / DeepTravel hallucination 50% 三个独立数据点同源指向；本项目当前架构（LLM blueprint → critic → backprompt）方向正确。
2. **「LLM 出意图 + 算法/规则出可行性」是 8 范式最大公约数**——但本项目 _utility 4 维加权和未把 LLM 出 single profit score 当一等公民；这是 ItiNera 范式落地不到位的体现。
3. **「半日 + 一句话 + 决策可见」是商业产品全空缺的市场窗口**——Google Maps Ask Maps / 携程 TripGenie / 大众点评 5 商业产品全部缺至少 1 件；本项目 ToolTracePanel + DecisionTraceCard 是评委评分项 2（Agent 行为可见性 25%）的杀手锏，但 ChatDock 当前形态未学习 LUI 浮标，体验偏 chatbot。

**本 spec 不做**（与 spec A R1-R10 业务质量主线 + spec B 目录重组正交）：

- 不换主架构范式（LLM-Modulo 同构系统已落地，仅做工程化加固）
- 不做 RL 微调（30+ 人天 + GPU $500 不可承受；与决策可见性矛盾）
- 不做 vector RAG 替换 mock_data（42 POI + 45 餐厅规模过度工程；结构化 KG 检索是正确粒度）
- 不新增 agent 角色（当前 5 个真 agent 已达论文规模，详见 spec B 目录树）
- 不改 graph/build.py 拓扑（编排冻结纪律 §3.3.1；spec B 已锁）
- 不删 critics_v2 任何现有 ViolationCode（所有 10 类业务规则保留）

**联合审查独立第二意见**（spec C 主架构）：「**LLM-Modulo（5+ 份合议）+ ItiNera-style 分工（2 份合议）+ TravelAgent 三层 schema（3+ 份合议）三联混合**」。每条 Requirement 来源标注调研报告 + ROI 评分。

**Hackathon 时间盒约束**：~5-10 人日工时，分 6 个 wave。所有改造保留 LangGraph 主路径不动，只改 critic 内部 / ils_planner 内部 / mock_data / 前端组件。

## Requirements

### Requirement 1: critics_v2 加 compute_reward + CRITIC_FEEDBACK_MODE 三档反馈策略

**User Story:** As 未来扩展 RL 路径或做反馈细化 A/B 实验的工程师, I want critics_v2 提供 `compute_reward(violations) -> float` 标量奖励函数 + 三档 env flag 控制反馈细化策略, so that 同一份 critic 既可做 LLM-Modulo backprompt（pinpoint-all），也可做 token 节省优化（first-only），也可作为未来 RL reward signal source（reward）零代价切换。

**ROI 来源**：Agent 5 报告 §六 Q4（7/10）+ Agent 3 报告 §三 §3.3 ablation（first-only ≈ full-feedback）。

#### Acceptance Criteria

1. WHEN `backend/agent/planning/critic/critics_v2.py` 升级 THEN 模块 SHALL 新增 `compute_reward(violations: list[Violation]) -> float` 函数；公式：`reward = -sum(SEVERITY_WEIGHTS[v.severity] * CODE_WEIGHTS[v.code] for v in violations)`；其中 `SEVERITY_WEIGHTS = {CRITICAL: 1.0, WARNING: 0.2}`；`CODE_WEIGHTS` 默认全 1.0，但对 macro 级（INVARIANT_BROKEN / NODES_INCOMPLETE / TIMELINE_INCONSISTENT）取 1.5，对细粒度（DIETARY_VIOLATION / DISTANCE_EXCEEDED）取 0.8（参考 STAR ablation MACRO 半稀疏 reward 在 OOD 表现最好的结论）。
2. WHEN 配置 `CRITIC_FEEDBACK_MODE=pinpoint-all` 或不设 THEN `format_violations_for_llm` SHALL 输出当前的 pinpoint-all 全量违规列表（保持向后兼容）。
3. WHEN 配置 `CRITIC_FEEDBACK_MODE=first-only` THEN `format_violations_for_llm` SHALL 仅输出第一条 critical violation；保持 message 内的「建议范围 lo-hi」自然语言不暴露字段名（design.md "不暴露 dot-path" 原则）。
4. WHEN 配置 `CRITIC_FEEDBACK_MODE=reward` THEN `format_violations_for_llm` SHALL 返回空字符串 + 同时记录 `compute_reward` 结果到 trace（用于未来 RL 路径预留 hook，本 spec 不消费）。
5. WHEN env flag 取值不在 `{pinpoint-all, first-only, reward}` 范围内 THEN 模块 SHALL 退化到 `pinpoint-all` 默认 + 在 stderr 输出一行 warning（不抛异常）。
6. WHEN 新增测试 `tests/test_critic_feedback_mode.py` THEN 应覆盖 ≥ 8 项断言：3 档模式各跑通 5 岁娃 196min 案例 + 反馈输出格式正确 + compute_reward 数值合理（CRITICAL 单条 ≥ 1.5 / WARNING 单条 ≤ 0.4）+ env 不合法时 fallback 验证。
7. WHEN spec C 全部完成 THEN `backend/.env.example` SHALL 含 `CRITIC_FEEDBACK_MODE=pinpoint-all` 段（带 3 档说明 + 默认值注释）。

---

### Requirement 2: 新增 TOOL_RESPONSE_INCONSISTENCY ViolationCode（防 LLM 幻觉 POI）

**User Story:** As 评委 / 用户, I want 项目能拦下「LLM 输出 itinerary 含 mock 池里不存在的 POI/餐厅 ID」这种幻觉, so that demo 现场不会出现「AI 编了一个不存在的咖啡馆」的灾难场景。

**ROI 来源**：Agent 5 报告 §六 Q4（8/10）+ DeepTravel hallucination 50%→<20% 数据点强烈支持。

#### Acceptance Criteria

1. WHEN `backend/agent/planning/critic/critics_v2.py:ViolationCode` 升级 THEN 枚举 SHALL 加 `TOOL_RESPONSE_INCONSISTENCY = "tool_response_inconsistency"`（第 11 个枚举值，紧跟 AGE_DURATION_MISMATCH 之后）。
2. WHEN `validate_itinerary` 升级 THEN 函数签名 SHALL 加可选参数 `tool_results: dict | None = None`（向后兼容，None 时跳过本项检查）；新增 `_check_tool_consistency(itinerary, tool_results) -> list[Violation]` 子函数。
3. WHEN `tool_results` 含 `search_pois.candidates: list[Poi]` + `search_restaurants.candidates: list[Restaurant]` THEN 子函数 SHALL 遍历 `itinerary.nodes`，对每个 `target_kind ∈ {poi, restaurant}` 节点检查 `target_id` 必须出现在对应候选池的 ID 集合里；不在则发 `TOOL_RESPONSE_INCONSISTENCY` violation（severity=CRITICAL，message 含「方案中 X 不在候选池中，可能是模型幻觉」）。
4. WHEN `backend/agent/graph/state.py:AgentState` 已含 `tool_results` 字段（execute_collect 节点写入） THEN `backend/agent/graph/nodes/critic.py:critic_node` SHALL 在调用 `validate_itinerary` 时透传 `state["tool_results"]` 参数。
5. WHEN 新增测试 `tests/test_tool_response_inconsistency.py` THEN 应覆盖 ≥ 6 项：编造 POI ID 触发违规 / 编造 Restaurant ID 触发 / 真实 ID 不触发 / tool_results=None 时跳过 / target_kind="home" 不检查 / 多个幻觉 ID 全部捕获。
6. WHEN `Violation.message` 在前端展示 THEN message SHALL 不含 dot-path（如 `tool_results.search_pois`）；只用自然语言描述（"方案中『XX 咖啡馆』可能是 AI 编造的，请重新规划"）。

---

### Requirement 3: ils_planner.py grounding-first 前置硬剔除

**User Story:** As ILS 算法, I want 在候选生成阶段把违反硬约束（年龄 cap / 闭店 / 距离超限）的 POI 直接剔除, so that ILS 看不见不可行候选，避免 spec A R5 已观察到的"P040 / P033 utility 第一名但违规"现象。

**ROI 来源**：Agent 1 报告 §五 Q5（grounding-first 8/10）+ Agent 6 报告 §六 Q1（业务规则 > 算法精度）+ 联合审查隐藏冲突 3 取舍。

#### Acceptance Criteria

1. WHEN `backend/agent/legacy/ils_planner.py:_query_pois` 升级 THEN 函数 SHALL 在 `SearchPoisOutput` 解析后 + 返回前加一层硬约束过滤 `_grounding_filter(candidates, intent) -> list[Poi]`，剔除以下情况：
   - 含 ≤6 岁同行人 + POI 的 suggested_duration_minutes 主导桶（kid_3_6 取 default 兜底）严格大于 90min
   - 含 ≥75 岁同行人 + 主导桶严格大于 75min
   - POI.distance_km > intent.distance_max_km + 1.0（与 _utility 物理可行性快检对齐）
   - POI.business_status ∈ {"closed", "permanent_closed"}（mock 当前未埋点，但 schema 已支持）
2. WHEN `_grounding_filter` 剔除任何候选 THEN 函数 SHALL emit `tracer.emit("grounding_filtered", {"poi_id": ..., "reason": ...})`，让 demo 时评委通过 trace 看到"AI 主动 filter 了 N 个不合适候选"——这是评分项 2 的可见性强化。
3. WHEN `_grounding_filter` 后候选池 < 3 THEN 函数 SHALL 自动放宽 +1 个最严约束（按优先级：distance > age cap > business_status）一次；再不够则返回空列表（让上游降级到 give_up 路径）。
4. WHEN `_query_restaurants` 升级 THEN 同步加 `_grounding_filter_restaurant`（仅过滤 distance + 满座，不做 age cap——餐厅 typical_dining_min 不区分客群桶）。
5. WHEN _utility 内的 `_overload_penalty(poi, intent)` 仍保留 THEN 该惩罚项作为「grounding 漏过的兜底」，spec C 不删（避免 critic 二次回灌路径漂移）。
6. WHEN `analyze_overload_coefficient.py` 复跑 THEN spec C R3 落地后，4 场景 × 5 系数下 P040 / P033 等违规候选 SHALL 不再出现在前 5 名 utility 排序里（grounding 阶段已被剔除）。
7. WHEN 新增测试 `tests/test_grounding_first.py` THEN 覆盖 ≥ 5 项：5 岁娃 P033 被过滤 / 70 岁外婆 P040 被过滤 / 候选池 < 3 时放宽距离 / restaurant 满座被过滤 / trace 记录正确。

---

### Requirement 4: _utility 末尾加 LLM 语义打分项（ItiNera-style 分工显式化）

**User Story:** As 工程师, I want _utility 公式末尾追加一项 LLM 出的语义契合分（不替换原 4 维 comfort/time/cost/smoothness）, so that LLM 对「亲子博物馆 vs 网红咖啡馆」这种语义差异的判断能直接进入 ILS 排序，而不是仅靠 tag 命中数死板加分。

**ROI 来源**：Agent 6 报告 §五 + §七（LLM-as-scorer 9/10）+ Agent 2 报告 §五 Q4（PPR 范式）+ 联合审查独立第二意见 7.2。

#### Acceptance Criteria

1. WHEN `backend/agent/planning/preference_scorer.py` 新建 THEN 模块 SHALL 提供：
   - `score_pois_with_llm(intent, pois, *, client) -> dict[str, float]`：批量调一次 LLM，返回 `{poi_id: 0-1 浮点}`
   - prompt 输入：intent 自然语言 + POI 列表（id/name/category/tags/rating/context），prompt 末尾要求 LLM 输出严格 JSON `{"scores": {"P001": 0.85, ...}}`
   - LLM 调用失败 / JSON 解析失败时返回所有 POI 默认 0.5 分（不阻断 ILS 主路径）
2. WHEN `backend/agent/legacy/ils_planner.py:_utility` 公式升级 THEN 函数 SHALL 接受新参数 `semantic_scores: dict[str, float] | None = None`（向后兼容）；公式末尾追加 `+ 0.3 * semantic_scores.get(poi.id, 0.5) if semantic_scores else 0`（保留原 4 维 + _overload_penalty 不变）。
3. WHEN `backend/agent/legacy/ils_planner.py:plan_hybrid` 升级 THEN 入口处 SHALL 在 `_query_pois` 后调用一次 `score_pois_with_llm`，缓存结果传给 `_utility` / `_local_search` / `_perturb`。
4. WHEN 模型未配置 LLM API key（`LLM_PROVIDER=stub` 模式）THEN `score_pois_with_llm` SHALL 返回所有 POI 默认 0.5 分（保持 stub 可跑通 demo）。
5. WHEN ILS 总耗时增加 THEN 单次 LLM 批量打分 SHALL 控制在 ≤ 3 秒（DeepSeek-V3 30 个 POI 批量调用经验值），不破 spec A 已锁的 latency 预算。
6. WHEN 新增测试 `tests/test_preference_scorer.py` + `tests/test_utility_with_semantic.py` THEN 覆盖 ≥ 6 项：5 岁娃场景 LLM 给亲子 POI 高分 / 商务场景给网红 POI 低分 / LLM 失败时全 0.5 / stub 模式跳过 / utility 加项数学正确性 / cache 命中。
7. WHEN `mock_data/pois.json` 检查 THEN 现有 `description` 字段已包含自然语言信息——本 spec **不改 mock 数据**（避免与 spec A R1 SuggestedDuration dict 升级再次冲突）。

---

### Requirement 5: user_profile.json 扩 TravelAgent 三层 schema + memory_writer 节点

**User Story:** As 评委 / 用户, I want demo 时看到「AI 记得我上次的偏好」+「同行人画像三层注入主路径」, so that 「半日 + 个性化 + 主动召回历史」三件事在 demo 现场可见——这是评分项 1（场景理解 20%）的杀手锏。

**ROI 来源**：Agent 7 报告 §三 §3.1 + §7.3（8.5/10 最高 ROI 单点改造）+ Agent 2 报告 §五 Q4（RD 四元组吸纳）+ 联合审查独立第二意见 7.2。

#### Acceptance Criteria

1. WHEN `backend/schemas/persona.py`（或 `mock_data/user_profile.json` 对应的 Pydantic 模型）升级 THEN 模型 SHALL 加 3 段新字段（全部 Optional 向后兼容）：
   - `dietary_preference: Optional[str]`：自然语言段落（"喜欢健康轻食、避免油腻、对辣度敏感"等），不是 enum
   - `social_context_history: Optional[list[str]]`：历史社交场景列表（去过 ["family", "couple", "biz"] 等）
   - `recent_trips: Optional[list[RecentTrip]]`：最多保留 5 条；RecentTrip 含 `social_context / summary / success / timestamp` 4 字段
2. WHEN `mock_data/user_profile.json` 升级 THEN 文件 SHALL 加上述 3 段示例数据（demo 之前手动塞 1-2 条假 recent_trips，让"召回"在第 1 次对话就有效——参考 Agent 7 §五 Q5 末尾建议）。
3. WHEN 新增 `backend/agent/graph/nodes/memory_writer.py:memory_writer_node` THEN 节点 SHALL：
   - 接收 `state["itinerary"]` + `state["intent"]` + `state["user_id"]` 三参
   - 调 LLM（短 prompt < 200 token）生成 trip_summary（自然语言 1-2 句）
   - 把 `RecentTrip` 写回 user_profile.json 的 `recent_trips` 列表头部，超过 5 条时保留最新 5 条
   - 必须 idempotent（demo 反复跑同一句不会污染历史；用 `intent.session_id + timestamp` 做幂等键）
   - 必须做文件锁（多 session 并发时；用 `filelock` 或 `fcntl.flock`，跨平台兼容）
4. WHEN `backend/agent/graph/build.py` 升级 THEN 在 `narrate → END` 边之间插入 `narrate → memory_writer → END`（按编排冻结纪律 §3.3.1，**不删 narrate → END 边，加 memory_writer 节点**）；如违反「不动 build.py 拓扑」原则，本 task 做"只加节点不动 edge"——把 memory_writer 作为 narrate 内部副作用调用，不进 graph 节点拓扑。**最终选 B 路径**：在 `narrate_node` 末尾末尾加 `_persist_memory(state)` 副作用调用，不动 graph 拓扑。
5. WHEN `backend/agent/intent/parser.py` 升级 THEN `IntentParser._build_user_message` 前 SHALL 读 `user_profile.recent_trips`，把匹配 `intent.social_context` 的最新 1 条 trip_summary 注入 prompt（"用户上次「家庭」场景去过 P004，反馈 success=True"），让 LLM 在抽 intent 时有历史上下文。
6. WHEN 新增测试 `tests/test_memory_writer.py` + `tests/test_recent_trips_recall.py` THEN 覆盖 ≥ 8 项：写回 5 条上限 / 幂等键 / 文件锁 / 失败/cancel 不写回 / 召回匹配 social_context / 召回不匹配的不注入 prompt / dietary_preference 自然语言注入 / schema 向后兼容（旧 user_profile.json 仍可加载）。
7. WHEN 隐私敏感字段（孩子年龄）落盘 THEN `RecentTrip.summary` SHALL 经过 LLM 脱敏（"5 岁孩子" → "学龄前儿童"），不存原始数字（参考 Agent 7 §三 §3.4 隐私要求）。

---

### Requirement 6: 前端 ChatDock + ToolTracePanel 双层折叠（隐藏冲突 1 取舍）

**User Story:** As 评委 / 用户, I want ChatDock 默认收起（学携程 LUI 浮标不打断主流程）+ ToolTracePanel 默认收起、按需展开（保留决策过程可见性）, so that demo 现场两种 UX 哲学不冲突——评委想看决策过程时点开 ToolTracePanel；不想看时主区域留给 itinerary。

**ROI 来源**：Agent 8 报告 §七 Q3 + §八 §8.4（LUI 9/10）+ 联合审查隐藏冲突 1 取舍。

#### Acceptance Criteria

1. WHEN `frontend/components/ChatDock.tsx` 升级 THEN 组件 SHALL 默认状态为 `collapsed`（仅显示底部浮标 56×56 圆形按钮 + 一个未读消息数 badge）；用户点击浮标 / 按 `Cmd+K` 展开成 chatbot 卡片（参考携程 TripGenie LUI）。
2. WHEN ChatDock 展开 THEN 用户输入 / 对话过程中 SHALL 不阻挡 itinerary 主区域（卡片宽度 ≤ 480px，定位 fixed bottom-right）；按 `Esc` 收起。
3. WHEN `frontend/components/ToolTracePanel.tsx` 升级 THEN 组件 SHALL 默认状态为 `collapsed`（显示一个"查看 Agent 决策过程（N 步）"折叠条 + 数字 badge）；用户点击展开成完整 trace 列表。
4. WHEN ToolTracePanel 展开 THEN 内容 SHALL 按 Epic 分组（Search / Plan / Critic / Execute / Memory），每组可独立折叠；当前已实现的 Epic 分组保留不动（spec C 不改组逻辑）。
5. WHEN 新增 `frontend/components/HomeView.tsx` props 升级 THEN 加 `chatDockDefaultOpen: boolean = false` + `toolTraceDefaultOpen: boolean = false`，用户偏好可写回 localStorage 跨 session 持久（演示场景前可一键切到全展开）。
6. WHEN `pnpm test` 运行 THEN `frontend/lib/store.test.ts` 或新建 `frontend/components/ChatDock.test.tsx` SHALL 覆盖 ≥ 5 项：默认收起 / Cmd+K 展开 / Esc 收起 / localStorage 持久化 / 浮标 badge 显示未读数。
7. WHEN 全量 `pnpm verify:all` 运行 THEN 现有 23 项 vitest + ESLint + TypeScript strict + Next build 全部 0 红灯（spec C 不破现有前端测试基线）。

---

### Requirement 7: ComparisonView 三候选 + 三轴评分强化（NAVITIME 借鉴）

**User Story:** As 评委, I want demo 现场看到「同一句话 → AI 给出 3 个不同侧重的半日方案 + 三轴评分对比」, so that 直接命中评分项 4（Tool 编排合理性 25%）+ 评分项 1（场景理解 20%）的"AI 真的在为我考虑多种可能"故事线。

**ROI 来源**：Agent 8 报告 §七 Q5（NAVITIME 20+ 年招牌 UX）+ 联合审查独立第二意见 7.2。

#### Acceptance Criteria

1. WHEN `frontend/components/ComparisonView.tsx` 升级 THEN 组件 SHALL 接受 `candidates: list[Itinerary]`（默认 3 个）+ 显式 emit `ComparisonAxes` 评分（轴 1：时长合规度 / 轴 2：距离合理度 / 轴 3：偏好匹配度，每轴 0-100 分整数）。
2. WHEN ILS 主路径升级 THEN `backend/agent/legacy/ils_planner.py:plan_hybrid` SHALL 返回前 3 名 utility 排名候选（不只 1 名最优），格式 `list[Itinerary]` 而非单 `Itinerary`；spec A 已经部分实现（保留 `top_k` 参数），spec C 把默认 `top_k=3` 锁死。
3. WHEN 主路径返回 3 候选 THEN `graph/sse_adapter.py:_to_sse` 的 `itinerary_ready` event SHALL payload 含 `candidates: list[Itinerary]` + `comparison_axes: list[ComparisonAxes]`（每候选一组评分）。
4. WHEN `frontend/components/ComparisonView.tsx` 渲染 THEN 评委 SHALL 看到 3 列并排卡片 + 每张卡片底部 3 条横向评分条；用户点击一张卡片切换为「主行程」，其他 2 张缩回 thumbnail。
5. WHEN 用户在 ComparisonView 切换主行程 THEN 不发起新的 LLM 调用（只是前端状态切换），延迟 < 100ms；切换后 IntentSummary / ToolTracePanel / ItineraryCard 同步更新。
6. WHEN 评分计算 THEN 三轴分数 SHALL 由后端在 plan_hybrid 末尾算出（避免前端做 critic 计算）：
   - 时长合规度 = 1 - sum(超过 age_cap 的节点 / 总节点) × 100
   - 距离合理度 = exp(-(总通勤时间 - intent.duration_hours × 60 × 0.2)² / 800) × 100（通勤占比超过 20% 衰减）
   - 偏好匹配度 = mean(语义打分 R4 输出) × 100
7. WHEN 新增测试 `tests/test_comparison_axes.py` + `frontend/components/ComparisonView.test.tsx` THEN 覆盖 ≥ 6 项：3 候选格式正确 / 三轴评分数学正确 / 切换主行程不发新 SSE / NAVITIME 风格视觉对齐 / 用户可以拒绝所有 3 候选触发 refine / mobile 端 3 候选改为竖向滑动。

---

### Requirement 8: 防再犯条款 + 文档同步

**User Story:** As 维护者, I want spec C 完成时主动追加 pitfalls.md 防再犯条款 + progress.md 决策记录 + problem.md 流水账, so that 下次有人想做 RL / 想做 vector RAG / 想新增 agent 角色时，立刻看到 8 范式调研的"绝对不要做"清单。

#### Acceptance Criteria

1. WHEN spec C 全部 task 完成 THEN `docs/03-implementation/pitfalls.md` SHALL 追加 ≥ 4 条防再犯条款：
   - [P0] **不要做 RL 整体复用**：30+ 人天 + GPU $500 + 与决策可见性矛盾。Agent 5 报告 §五 §5.3 数据点为依据
   - [P0] **不要做 vector RAG 替代 mock_data**：42 POI + 45 餐厅规模过度工程，结构化 KG 检索 precision=100% 反而比 vector 高。Agent 7 报告 §二 §2.5 ROI 估算为依据
   - [P0] **不要新增 agent 角色（10+）**：当前 5 个真 agent 已达 TriFlow / Vaiage 论文规模，再加冗余。Agent 7 报告 §一 §1.1 为依据
   - [P0] **CRITIC_FEEDBACK_MODE 默认保持 pinpoint-all**：first-only 性能等价但 token 节省 30-50%，是可选优化不是默认；reward 模式仅为未来 RL 路径预留挂钩点，本项目不消费
2. WHEN `docs/00-overview/progress.md` 升级 THEN 决策记录段 SHALL 追加 `D-ALGO-REDESIGN [2026-05-XX]：算法重构 spec C 落地——LLM-Modulo（5+ 合议）+ ItiNera-style 分工（2 份合议）+ TravelAgent 三层 schema（3+ 份合议）三联混合主架构；7 项必做 + 8 项绝对不做清单已固化为 pitfalls.md 防再犯条款`。
3. WHEN `problem.md` 追加 THEN 文件 SHALL 含本次 spec 的「问题 / 方案 / 修改文件 / 应当达成的效果」记录（按全局 problem.md 格式）。
4. WHEN `AGENTS.md §3.3.1` 编排冻结纪律 THEN 文件 SHALL 加一句话补充：「critics_v2.py 加 CRITIC_FEEDBACK_MODE / TOOL_RESPONSE_INCONSISTENCY 不破冻结纪律——这是同一个 critic 文件内的扩展，不是新增 critic 文件」。
5. WHEN `.codesee/features.json` 受影响的 feature 的 `refs[].file` 路径 THEN SHALL 通过 codesee sync 流程自动更新（spec C 不手动改 features.json）。

---

## Out of Scope（明确不做）

```text
| 不做的事                                         | 理由                                          | 出处 / 时机                  |
|------------------------------------------------|----------------------------------------------|----------------------------|
| RL 微调（DeepTravel / Planner-R1 整体复用）       | 推理路径替换不可承受；与决策可见性矛盾           | Agent 5 §六 Q3；本 spec 永不做 |
| Google 多日 DP/set packing/local search 三件套   | 半日单城场景全部退化；过度工程                    | Agent 1 §五 Q1+Q2；本 spec 永不做 |
| ITINERA cluster + 分层 TSP                       | 节点 4-6 时数学失效（cluster 数 ≥ 节点 / 2）       | Agent 2 §五 Q2；本 spec 永不做 |
| ALNS / MILP exact 求解器                         | n=87 极小规模过度工程；MILP 业务约束（年龄 cap）非线性难表达 | Agent 6 §三 §3.1；本 spec 永不做 |
| vector RAG 替换 mock_data                        | 42 POI 用 vector 过度工程；结构化检索 precision 100% | Agent 7 §二 §2.5；本 spec 永不做 |
| 新增 agent 角色（10+）                           | 当前 5 个真 agent 已达论文规模；再加冗余          | Agent 7 §一 §1.1；本 spec 永不做 |
| 商业产品算法借鉴（TripGenie 内部 / 美团 LongCat）| 黑盒；工程量天文数字                              | Agent 8 §八 §8.2；本 spec 永不做 |
| 增加 LLM 调用次数预算到 10                       | 违反 latency-bound 决策；评委 30 秒红线            | Agent 3 §六 §6.1；本 spec 永不做 |
| meta_critic_node（LLM-as-judge）                | 与项目当前 rule-based critic 哲学冲突；引入 +2-3s 延迟 | spec A Out-of-Scope；spec D 评估 |
| AGE_DURATION_MISMATCH 论文化                     | 路演叙事素材，hackathon 不必再加 critic            | Agent 6 §六 Q4；放路演大纲   |
| 流式 SSE 让评委每轮看 critic 反馈进度             | latency 优化挂钩点；第 4 周再做                    | Agent 3 §六 §6.1；后期      |
| 多日范式 V2（产品演进 backlog）                   | hackathon 不做；Agent 1 多日 set packing 留 V2   | Agent 1 §五 Q5；后期         |
| `_check_opening_hours_after_assemble`            | 已被 spec A R4 砍                                  | spec A Out-of-Scope；不做  |
| AGENTS.md §3.3.1 graph 拓扑改动                  | 编排冻结纪律 + spec B 已锁                       | AGENTS.md §3.3.1；不做     |
```

---

## 前置条件 / 时序硬约束

**本 spec 必须在 spec A `planning-quality-deep-review` 全部 8 task 完成 + spec B `agent-directory-restructure` 全部 8 task 完成 + 联合审查报告产出后启动**。

**理由**：

- spec A 期间会修业务规则 critic / blueprint prompt / mock 数据——critics_v2 / preference_scorer 改动如果与 spec A 时序冲突会导致 import 路径不一致 + critic 双层防御不全
- spec B 重组完了 critics_v2 在 `backend/agent/planning/critic/critics_v2.py`（spec C 改动锚点都假设这个新路径）
- 联合审查报告（`.kiro/specs/algorithm-redesign/research/joint-review/report.md`）的 7 项必做 + 5 条隐藏冲突取舍是 spec C requirements 的直接来源

**启动检查清单**：

- [ ] spec A 的 8 个 task 全部 `[x]` 完成
- [ ] spec B 的 8 个 task 全部 `[x]` 完成
- [ ] 联合审查报告 `.kiro/specs/algorithm-redesign/research/joint-review/report.md` 已产出
- [ ] git tag `v-spec-b-done`（用于 spec C 出问题时回滚）已打
- [ ] 用户人工确认"可以启动 spec C"

**注**：当前会话已确认 spec A + spec B 完成 + 联合审查报告已 commit（hash 52e3f61）；缺第 4 项 git tag 与第 5 项用户确认即可启动。
