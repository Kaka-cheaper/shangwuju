# Implementation Plan: Algorithm Redesign (spec C)

## Overview

把项目算法层从「事实上的 LLM-Modulo 同构系统」升级为「显式三联混合产品级骨架」。

**总工时预估**：~12.1h（≈ 1.5-2 人日，hackathon 时间盒内可分 1-2 天完成）。

**关键路径**：T1 baseline → T2-T3 critic 工程化（compute_reward + TOOL_RESPONSE_INCONSISTENCY）→ T4 grounding-first → T5 preference_scorer → T6 三层 schema + memory_writer → T7-T8 前端 UX → T9 联调 + 文档

**核心约束**：
- 保留 LangGraph 主路径拓扑（不动 graph/build.py edge）；spec B 已锁
- spec A 已落地的 9+1 类 ViolationCode 不删（仅加第 11 个）
- legacy/ 模块（spec B 冻结）只能 bug fix + 加新过滤函数，不改业务
- LLM 语义打分失败必须兜底全 0.5 分（不阻断 ILS 主路径）
- memory_writer 必须 idempotent + 隐私脱敏 + 跨平台兼容
- ChatDock + ToolTracePanel 双层折叠 + localStorage 持久化
- 联合审查的 8 项「绝对不要做」清单严格执行

## Tasks

- [ ] 1. [前置] baseline 验证 + spec A/B 完成度核查 + git tag（~0.3h）：
  - 跑 `pytest backend/tests/ -v --tb=short` 记录基线（必须全绿；含 spec A 新增的 ~30 项 + spec B 新增的 import_paths 测试）
  - 跑 `python backend/scripts/verify_planning_quality.py`（spec A R10）+ `verify_legacy_frozen.py`（spec B R3）+ `verify_planning.py` + `verify_edge_model.py` 必须全绿
  - 启动 `python -m backend.main &` + `curl http://localhost:8000/health` 必须 200
  - 跑 `cd frontend && pnpm verify:all`（lint + typecheck + 23 项 vitest + Next build）必须 0 红灯
  - 读 `.kiro/specs/planning-quality-deep-review/tasks.md` 确认 8 个 task 全 [x]
  - 读 `.kiro/specs/agent-directory-restructure/tasks.md` 确认 8 个 task 全 [x]
  - 读 `.kiro/specs/algorithm-redesign/research/joint-review/report.md` 确认存在（联合审查报告就位）
  - 如有任何不通过 → 立即停止 spec C，先报告状态等用户决定
  - 如全通过 → 打 git tag `v-spec-c-start`（用于 spec C 出问题时回滚锚点）

- [ ] 2. [R1] critics_v2 加 compute_reward + CRITIC_FEEDBACK_MODE 三档 mode（~1.0h）：在 `backend/agent/planning/critic/critics_v2.py` 加：
  - `SEVERITY_WEIGHTS: dict[Severity, float] = {CRITICAL: 1.0, WARNING: 0.2}`
  - `CODE_WEIGHTS: dict[ViolationCode, float]`：macro 级（INVARIANT_BROKEN / NODES_INCOMPLETE / TIMELINE_INCONSISTENT）取 1.5；细粒度（DIETARY_VIOLATION / DISTANCE_EXCEEDED）取 0.8；其余 1.0
  - `compute_reward(violations: list[Violation]) -> float`：公式 `-sum(SEVERITY_WEIGHTS[v.severity] * CODE_WEIGHTS.get(v.code, 1.0) for v in violations)`
  - `_get_feedback_mode() -> str`：从 env 读 `CRITIC_FEEDBACK_MODE`，不在 `{pinpoint-all, first-only, reward}` 范围 → fallback 到 pinpoint-all + stderr warn
  - 升级 `format_violations_for_llm(violations)`：保持原签名向后兼容；mode=pinpoint-all 时输出原行为；first-only 时仅列第一条 critical；reward 时返回空字符串
  - 在 `backend/.env.example` 加段 `CRITIC_FEEDBACK_MODE=pinpoint-all  # 三档：pinpoint-all（默认全量违规列表）/ first-only（仅第一条，节省 token 30-50%）/ reward（dense scalar，未来 RL 路径预留）`
  - 新增 `backend/tests/test_critic_feedback_mode.py`（≥ 8 项）：3 档模式各跑 5 岁娃 196min 案例 + compute_reward 数值验证（CRITICAL 单条 ≥ 1.5 / WARNING 单条 ≤ 0.4 / 多违规累加）+ env 不合法 fallback + 默认值 pinpoint-all 与 spec A diff 为空
  - 跑全套 pytest 必须 0 红灯（与 baseline 一致）

- [ ] 3. [R2] critics_v2 加 TOOL_RESPONSE_INCONSISTENCY ViolationCode（~0.8h）：在 `backend/agent/planning/critic/critics_v2.py` 加：
  - `ViolationCode.TOOL_RESPONSE_INCONSISTENCY = "tool_response_inconsistency"`（紧跟 AGE_DURATION_MISMATCH 之后，第 11 个枚举值）
  - `_check_tool_consistency(itinerary: Itinerary, tool_results: dict | None) -> list[Violation]`：tool_results=None 时返回 []；候选池为空时返回 []（避免 stub mode 误报）；遍历 itinerary.nodes，对 target_kind ∈ {poi, restaurant} 节点检查 target_id 是否在对应候选池 ID 集合里；不在则发 CRITICAL violation（message 含「方案中『XX』不在候选池中，可能是 AI 编造的，请重新规划」，不暴露 dot-path 字段名）
  - `validate_itinerary` 函数签名加 `tool_results: dict | None = None` 参数（向后兼容）；末尾追加 `violations.extend(_check_tool_consistency(itinerary, tool_results))`
  - 升级 `backend/agent/graph/nodes/critic.py:critic_node`：在调用 `validate_itinerary` 时透传 `state.get("tool_results")` 参数
  - 把 TOOL_RESPONSE_INCONSISTENCY 加进 task 2 的 `CODE_WEIGHTS`（取 1.5，hallucination 等同 macro 级）
  - 新增 `backend/tests/test_tool_response_inconsistency.py`（≥ 6 项）：编造 POI ID 触发违规 / 编造 Restaurant ID 触发 / 真实 ID 不触发 / tool_results=None 时跳过 / target_kind="home" 不检查 / 多个幻觉 ID 全部捕获
  - 跑全套 pytest 必须 0 红灯

- [ ] 4. [R3] ils_planner.py grounding-first 前置硬剔除（~1.5h）：在 `backend/agent/legacy/ils_planner.py`（FROZEN 模块允许加新过滤函数）加：
  - `_grounding_filter_poi(candidates, intent, tracer) -> list[Poi]`：剔除以下情况
    - 含 ≤6 岁同行人 + `get_duration_for_companions(poi.suggested_duration_minutes, intent.companions)` > 90min（用 spec A R2 helper）
    - 含 ≥75 岁同行人 + 主导桶 > 75min
    - `poi.distance_km > intent.distance_max_km + 1.0`
    - `getattr(poi, "business_status", "open") in {"closed", "permanent_closed"}`（mock 当前可能无此字段，getattr 兜底）
    - 每剔除一个候选必 emit `tracer.emit("grounding_filtered", {poi_id, reason})`
    - 候选池 < 3 时自动放宽：仅过滤距离 +2.0km 与 business_status，跳过 age cap
  - `_grounding_filter_restaurant(candidates, intent, tracer)`：仅过滤距离 + 营业状态（不做 age cap，餐厅 typical_dining_min 不区分客群桶）
  - 在 `_query_pois` 末尾的 return 前加 `candidates = _grounding_filter_poi(candidates, intent, tracer)`；同理 `_query_restaurants`
  - **保留** `_overload_penalty` 与 `_utility` 内的 `score -= 0.5 * _overload_penalty(poi, intent)`（兜底，不改）
  - 跑 `python backend/scripts/analyze_overload_coefficient.py` 复跑确认 P040 / P033 等违规候选不再出现在前 5 名 utility 排序里
  - 新增 `backend/tests/test_grounding_first.py`（≥ 5 项）：5 岁娃 P033 被过滤 / 70 岁外婆 P040 被过滤 / 候选池 < 3 时放宽距离 / restaurant 满座保留（由 critic 处理）/ tracer.emit("grounding_filtered") 记录正确
  - 跑 spec A R10 验证脚本 `verify_planning_quality.py` 必须 4 场景全绿（5 岁娃 + 老人 + 独处 + 商务）

- [ ] 5. [R4] preference_scorer.py + _utility 加 LLM 语义打分项（~2.0h）：
  - 新建 `backend/agent/planning/preference_scorer.py`，实现 `score_pois_with_llm(intent, pois, *, client=None) -> dict[str, float]`：
    - 失败兜底全 0.5 分（不阻断 ILS 主路径）
    - 检测 `client.provider == "stub"` 时直接返回全 0.5
    - prompt 用 `_SCORER_PROMPT`（见 design.md §Components 设计稿）；temperature=0.3；max_tokens=500
    - LLM 输出严格 JSON 解析（围栏剥离 + try/except）
    - 类型校验 + clip [0, 1]
  - 升级 `backend/agent/legacy/ils_planner.py:_utility` 函数签名加 `semantic_scores: dict[str, float] | None = None` 参数（向后兼容）；公式末尾加 `score += 0.3 * semantic_scores.get(poi.id, 0.5) if poi and semantic_scores else 0`（**保留** 原 4 维 + spec A R5 _overload_penalty 不变，仅末尾追加）
  - 升级 `plan_hybrid` 入口：调用 `score_pois_with_llm(intent, pois, client=client)` 缓存到局部变量；后续所有 _utility 调用透传 semantic_scores 参数
  - 升级 `_local_search` / `_perturb` 内部对 _utility 的调用，把 semantic_scores 一路传下去
  - 新增 `backend/tests/test_preference_scorer.py`（≥ 4 项）：5 岁娃场景 LLM 给亲子 POI 高分 / stub 模式全 0.5 / LLM 失败时全 0.5 / JSON 解析失败兜底
  - 新增 `backend/tests/test_utility_with_semantic.py`（≥ 2 项）：utility 加 LLM 项数学正确 + semantic_scores=None 时不加项（向后兼容）
  - 跑全套 pytest 必须 0 红灯；跑 spec A R10 验证脚本必须 4 场景全绿

- [ ] 6. [R5] user_profile.json 扩三层 schema + memory_writer 副作用（~2.5h）：
  - 在 `backend/schemas/persona.py`（或对应 user_profile schema 文件）加：
    - `RecentTrip(BaseModel)` 含 `timestamp / social_context / summary / success` 4 字段（参考 design.md §Component 4 设计稿）
    - `UserProfile` 加 3 段新字段（全 Optional 向后兼容）：`dietary_preference: Optional[str]` / `social_context_history: Optional[list[str]]` / `recent_trips: Optional[list[RecentTrip]]`
  - 升级 `mock_data/user_profile.json`：保留原 4 字段不动；加 `dietary_preference`（自然语言 50-100 字段落，如「喜欢健康轻食、避免油腻、对辣度敏感」）+ `social_context_history`（如 `["family", "couple", "solo"]`）+ `recent_trips`（手动塞 1-2 条假数据，让"召回"在第 1 次对话就有效，参考 Agent 7 §五 Q5 末尾建议）
  - 在 `backend/agent/graph/nodes/narrate.py:narrate_node` 加 `_persist_memory(state)` 副作用调用（在 narrate 主逻辑末尾，不动 graph 拓扑——按 design.md §Components 决策点 4 路径 B）：
    - `_summarize_trip(itinerary, intent, *, client) -> str`：LLM 短 prompt（< 200 token）生成脱敏摘要；隐私要求 prompt 显式约束「不出现具体年龄数字（5 岁 → 学龄前儿童）/ 不出现具体地址 / 经纬度」
    - `_persist_memory(state)`：用 `threading.Lock` 跨平台兼容（不依赖 fcntl）；幂等键用 social_context + 5 分钟 timestamp 窗口（同 session 重复不追加）；`recent_trips[:5]` 上限；失败 / cancel 不阻断主流程（try/except 包裹 + warning log）
  - 升级 `backend/agent/intent/parser.py:IntentParser._build_user_message`（或对应函数）：把匹配 social_context 的最新 1 条 recent_trip 注入 prompt（"用户上次「家庭」场景的行程：{summary}"）
  - 新增 `backend/tests/test_memory_writer.py`（≥ 5 项）：写回 5 条上限 / 幂等键 5 分钟窗口 / 失败/cancel 不写回 / 隐私脱敏（不含「5 岁」原始数字）/ 文件锁不冲突
  - 新增 `backend/tests/test_recent_trips_recall.py`（≥ 3 项）：召回匹配 social_context 注入 prompt / 召回不匹配的不注入 / dietary_preference 自然语言注入命中关键词
  - schema 向后兼容验证：旧 user_profile.json（仅 4 字段）仍可加载（用旧版做 fixture 测试）
  - 跑全套 pytest 必须 0 红灯

- [ ] 7. [R6] 前端 ChatDock + ToolTracePanel 双层折叠（~1.0h）：
  - 升级 `frontend/components/ChatDock.tsx`：
    - 默认 `expanded=false`（收起态：底部浮标 56×56 圆形按钮 + badge）
    - 用 `useEffect` 读 `localStorage.getItem("shangwuju.chatdock.expanded")` 初始化（SSR 默认 collapsed 避免 hydration mismatch）
    - 监听 `Cmd+K` / `Ctrl+K` 展开 + `Esc` 收起
    - 展开态：480px 卡片 + `bottom-right` 定位 + 关闭按钮
    - props 加 `defaultOpen: boolean = false`
  - 升级 `frontend/components/ToolTracePanel.tsx`：
    - 默认 `expanded=false`（收起态：「查看 Agent 决策过程（N 步）」折叠条 + badge）
    - 展开态：保留现有按 Epic 分组逻辑不动
    - props 加 `defaultOpen: boolean = false`
  - 升级 `frontend/components/HomeView.tsx`：把 ChatDock + ToolTracePanel 默认 props 设为 collapsed
  - 新增 `frontend/components/ChatDock.test.tsx`（≥ 5 项）：默认收起 / Cmd+K 展开 / Esc 收起 / localStorage 持久化跨 session / SSR hydration 不报警告
  - 跑 `cd frontend && pnpm verify:all`（lint + typecheck + 23 + 5 项 vitest + Next build）全 0 红灯

- [ ] 8. [R7] ComparisonView 三候选 + 三轴评分（~2.0h）：
  - 后端：
    - 新建 `backend/agent/planning/comparison_axes.py`，实现 `compute_axes(itinerary, intent) -> dict[str, int]`（三轴公式见 design.md §Component 5）：
      - 时长合规度 = `1 - 违规节点数 / 总节点数`（0-100 整数）
      - 距离合理度 = `exp(-(总通勤时间 - target_min)^2 / 800)`（target_min = duration_hours × 60 × 0.2）
      - 偏好匹配度 = `mean(semantic_scores)` × 100（从 task 5 的 preference_scorer 拿）
    - 升级 `backend/agent/legacy/ils_planner.py:plan_hybrid`：返回 top_k=3 候选 + 每个候选的 `comparison_axes`
    - 升级 `backend/agent/graph/sse_adapter.py:_emit_itinerary_ready`：payload 加 `candidates: list[Itinerary]` + `comparison_axes: list[dict]`（保留原 itinerary 字段为主行程，向后兼容）
  - 前端：
    - 升级 `frontend/components/ComparisonView.tsx`：3 列并排卡片（mobile 改竖向滑动）+ 每张卡片底部 3 条横向 AxisBar
    - 用户点击卡片切换主行程：仅前端 store 状态切换，不发新 SSE，延迟 < 100ms
    - 切换后 IntentSummary / ToolTracePanel / ItineraryCard / MapOverlay 同步更新主行程
    - candidates 长度 < 2 时不显示 ComparisonView（保持单行程兼容）
  - 新增 `backend/tests/test_comparison_axes.py`（≥ 4 项）：5 岁娃 196min 反例 duration_compliance ≤ 50 / 合规候选 = 100 / 距离公式数学正确 / 偏好匹配从 semantic_scores 拿
  - 新增 `frontend/components/ComparisonView.test.tsx`（≥ 2 项）：3 候选渲染正确 / 切换主行程不发新 SSE
  - 跑全套 pytest + pnpm verify:all 必须 0 红灯

- [ ] 9. [R8 + 联调] 防再犯条款 + 文档同步 + 一次性原子 commit（~1.0h）：
  - 端到端验证：
    - 跑 `pytest backend/tests/ -v --tb=short` 必须 0 红灯（与 baseline 一致 + 新增 ~30 项 spec C 测试全绿）
    - 跑 `python backend/scripts/verify_planning_quality.py` 4 场景必须全绿（5 岁娃 + 老人 + 独处 + 商务）
    - 跑 `python backend/scripts/verify_legacy_frozen.py` + `verify_planning.py` + `verify_edge_model.py` 全绿
    - 启动 `python -m backend.main` + `curl /health` = 200
    - `cd frontend && pnpm verify:all` 全绿
    - 浏览器 demo（手动）：5 岁娃场景跑 1 次，确认（a）grounding_filtered trace 出现 ≥ 1 条；（b）ChatDock 默认收起；（c）ToolTracePanel 默认收起；（d）ComparisonView 显示 3 候选 + 三轴评分；（e）narrate 后 user_profile.json 多了 1 条 recent_trip
  - 文档同步：
    - 在 `docs/03-implementation/pitfalls.md` 追加 4 条 [P0] 防再犯条款（按 R8.1 模板）：不要做 RL / 不要做 vector RAG / 不要新增 agent 角色（10+）/ CRITIC_FEEDBACK_MODE 默认保持 pinpoint-all，每条带「依据：联合审查 §X / Agent N 报告 §Y」出处
    - 在 `docs/00-overview/progress.md` 追加 `D-ALGO-REDESIGN [日期]：算法重构 spec C 落地——LLM-Modulo（5+ 合议）+ ItiNera-style 分工（2 份合议）+ TravelAgent 三层 schema（3+ 份合议）三联混合主架构；7 项必做 + 8 项绝对不做清单已固化为 pitfalls.md`
    - 在 `problem.md` 追加本次 spec 的「问题 / 方案 / 修改文件 / 应当达成的效果」记录（按全局 problem.md 格式）
    - 在 `AGENTS.md §3.3.1` 编排冻结纪律加一句话：「critics_v2.py 加 CRITIC_FEEDBACK_MODE / TOOL_RESPONSE_INCONSISTENCY 不破冻结纪律——这是同一个 critic 文件内的扩展，不是新增 critic 文件」
  - 一次性原子 commit：
    - `git status --short` 列出所有变更
    - `git diff --cached --stat` 复核 stage 范围（按 pitfalls P2 越界教训）
    - `git add -A`（包含新增的 schema / 测试 / 前端组件 / 文档）但**不**带 untracked 杂物（image.png / *.txt 等）
    - `git commit -m "feat(spec-c): 算法重构 LLM-Modulo + ItiNera + TravelAgent 三联混合落地（compute_reward + grounding-first + LLM 语义打分 + 三层 schema + 双层折叠 + 三候选 + 防再犯条款）"`
    - `git tag v-spec-c-done`（用于后续 spec D 时回滚锚点）

## Task Dependency Graph

```json
{
  "waves": [
    [1],
    [2, 3],
    [4],
    [5],
    [6],
    [7, 8],
    [9]
  ]
}
```

说明：
- **Wave 1（baseline）**：Task 1 必须最先做；spec A/B 完成度核查 + 5 个 verify 脚本全绿才能启动后续。
- **Wave 2（critic 工程化）**：Task 2（compute_reward）+ Task 3（TOOL_RESPONSE_INCONSISTENCY）可并行——都改 critics_v2 同一个文件但不同函数；建议串行做（Task 2 → Task 3）避免 git 冲突。
- **Wave 3（grounding-first）**：Task 4 依赖 spec A R2 已落地的 `get_duration_for_companions` helper（spec B 已迁移到 `backend/agent/utils/duration_helpers.py` 或保留原位置）。
- **Wave 4（LLM 语义打分）**：Task 5 依赖 Task 4 的 grounding-first 已剔除明显违规候选（避免 LLM 给 P040 / P033 出高分浪费）。
- **Wave 5（三层 schema）**：Task 6 独立，但建议在 Task 5 后做（让 user_profile 召回的 recent_trips 与 semantic_scores 配合验证）。
- **Wave 6（前端 UX）**：Task 7（ChatDock + ToolTracePanel 折叠）+ Task 8（ComparisonView 三候选）可并行——不同组件文件，不冲突。但 Task 8 后端依赖 Task 5（preference_scorer）的 semantic_scores 输出，建议 Task 8 在 Task 5 之后。
- **Wave 7（联调 + commit）**：Task 9 必须最后做——所有改动一次性原子 commit。

## Notes

- **向后兼容是硬约束**：每个 task 的 schema / 函数签名升级都加 Optional 默认值，让 spec A/B 已通过的 470+ 项 pytest 不破。
- **grounding-first 与 _overload_penalty 双重防御不删旧**：spec A R5 已锁的 utility 减分机制保留——避免破坏现有测试基线 + 提供两层防线。
- **memory_writer 副作用接入方式**：选 design.md §Component 4 路径 B（narrate_node 内副作用调用），不动 graph 拓扑——这是编排冻结纪律 §3.3.1 + spec B 锁的强制约束。
- **CRITIC_FEEDBACK_MODE 默认 pinpoint-all**：reward 模式仅占位，本 spec 不消费——为未来 spec D（如要做 RL 路径实验）预留挂钩点。
- **三候选 SSE payload 大小**：仅返回 itinerary 摘要 + axes 数字，不返回全 trace，避免 SSE 过大影响 latency。
- **ChatDock 默认收起对 demo 的影响**：demo 前可手动设 `localStorage.setItem("shangwuju.chatdock.expanded", "true")` 强制展开 + Cmd+K 教学评委一句"按 Cmd+K 召唤 AI 助手"。
- **联合审查 8 项「绝对不要做」严格执行**：本 spec 的 R8 防再犯条款落库，未来任何 PR 涉及 RL / vector RAG / 新增 agent 角色都会触发 pitfalls.md 提醒。
- **commit 策略**：9 个 task 中途绝不 commit，全部完成 + 验收通过后一次性原子 commit；如某 task 失败决定回滚，跑 `git restore .` + `git clean -fd` 撤销本批次（前面已通过的 task 也一起回滚——本 spec 无中间 commit）。
- **失败处理协议**：任何 task 末尾 pytest 红灯立即停 + 报告，**禁止跨 task 修复**（task 4 失败不允许在 task 5 里"顺手"修）。

## Risk & Mitigation

```text
| 风险                                                    | 概率 | 影响 | 缓解                                                |
|--------------------------------------------------------|------|-----|---------------------------------------------------|
| LLM 语义打分增加 ~3s latency 破 spec A 锁的预算          | 中   | 中  | 批量调一次（30 POI 一起）；每次单独 sub-agent 不重复  |
| spec A R5 _overload_penalty 与 grounding-first 双重过滤  | 中   | 中  | grounding 加放宽机制（候选 < 3 时 +1km）              |
| ChatDock 默认收起导致 demo 评委不会展开                  | 中   | 高  | demo 前手动 localStorage 设展开 + Cmd+K 教学          |
| memory_writer 文件锁在 Windows 下不兼容                  | 低   | 中  | threading.Lock 跨平台；不依赖 fcntl                 |
| TOOL_RESPONSE_INCONSISTENCY 在 stub 模式误报             | 中   | 中  | 候选池为空时跳过；stub 模式也不报                    |
| 三候选并列让 ItineraryCard / MapOverlay 渲染慢           | 低   | 中  | 仅 ComparisonView active 时渲染；切换主行程才更新    |
| spec C 落地破 spec A / B 已通过测试                      | 中   | 高  | 每个 task 末尾跑全套 pytest；T1 baseline 必须先过    |
| CRITIC_FEEDBACK_MODE=reward 不消费导致评委不知道有这功能 | 低   | 低  | progress.md + pitfalls.md 写明「未来 RL 预留挂钩」  |
| recent_trips 隐私脱敏不到位（存了「5 岁」）              | 低   | 中  | LLM prompt 显式约束 + tests/test_memory_writer 验证 |
| LLM 语义打分对 5 岁娃 P004（亲子博物馆）出低分           | 低   | 低  | prompt 显式描述 social_context；temperature=0.3 收敛 |
| ComparisonView 三候选时数据冗余                          | 低   | 低  | candidates 与 itinerary 字段共用同一 Itinerary 模型 |
| 全套 pytest 在 task 5 / task 8 后膨胀到 600+ 项          | 中   | 低  | 每 task 末尾跑 -x 模式立即停；CI 跑全套             |
| memory_writer 在并发 SSE 时写文件竞争                    | 低   | 中  | threading.Lock 进程内 + 异常捕获不阻断              |
```

## 启动检查清单（task 1 前必须满足）

```text
| #  | 检查项                                          | 验证方法                                              |
|----|-----------------------------------------------|------------------------------------------------------|
| C1 | spec A 全部 8 task 完成                         | 读 .kiro/specs/planning-quality-deep-review/tasks.md，全 [x] |
| C2 | spec B 全部 8 task 完成                         | 读 .kiro/specs/agent-directory-restructure/tasks.md，全 [x] |
| C3 | 联合审查报告就位                                | .kiro/specs/algorithm-redesign/research/joint-review/report.md 存在 |
| C4 | git tag v-spec-b-done 已打                      | git tag --list 应见 v-spec-b-done（或本次直接打 v-spec-c-start）|
| C5 | 用户人工确认"可以启动 spec C"                   | 用户消息明确允许                                      |
```

只有 C1-C5 全满足才能开始 task 1。否则立即停止，等待用户。

## Out of Scope（再次确认）

```text
| 不做的事                              | 理由                                | 时机                  |
|--------------------------------------|------------------------------------|----------------------|
| RL 微调（DeepTravel / Planner-R1）   | 30+ 人天 + GPU $500；与可见性矛盾   | 永不做                |
| Google 多日 DP/set packing            | 半日单城退化                        | 永不做                |
| ITINERA cluster + 分层 TSP             | 节点 4-6 时数学失效                  | 永不做                |
| ALNS / MILP exact                     | n=87 极小规模过度工程                | 永不做                |
| vector RAG 替代 mock_data              | 42 POI 用 vector 过度工程            | 永不做                |
| 新增 agent 角色（10+）                 | 当前 5 个已达论文规模                | 永不做                |
| 商业产品算法借鉴（黑盒）               | 工程量天文数字                      | 永不做                |
| 增加 LLM 调用次数预算到 10             | latency 30 秒红线                    | 永不做                |
| meta_critic_node                      | 引入 +2-3s 延迟                      | spec D 评估           |
| AGE_DURATION_MISMATCH 论文化          | 路演叙事素材                        | 路演大纲              |
| 流式 SSE 让评委每轮看 critic 进度      | 后期优化                            | 第 4 周或 demo 后     |
| 多日范式 V2                           | 产品演进 backlog                    | 后期                  |
| graph/build.py 拓扑改动               | 编排冻结纪律 + spec B 锁            | 永不做                |
| _check_opening_hours_after_assemble  | spec A 已砍                         | 永不做                |
| 前端 ItineraryCard / MapOverlay 大改  | 只做 ComparisonView 增量            | 后期                  |
```
