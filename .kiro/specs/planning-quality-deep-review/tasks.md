# Implementation Plan: Planning Quality Deep Review

## Overview

把规划链路从「形式合规但反业界常识」升级为「主防 + 兜底 + 主动质疑」三层防御。

**总工时预估**：~17h 必修集 + 3h 联调缓冲 = ~20h（hackathon 时间盒内可分 2-3 天完成）。

**关键路径**：W1 mock+schema → W2 preview → W3 prompt → W4 critic → W5 ILS → W6 intent+refiner → W7 narrator+输出 → W8 验收 + 文档 + 防再犯

**核心约束**：
- 保留 LangGraph 主路径拓扑（不动 graph/build.py edge）
- mock 数据用 Pydantic Union 双兼容期，旧测试断言不破
- LLM 主防 + critic 兜底 + ILS 兜底 + narrator 主动质疑 5 层联动
- 拒 NodeDecider 升级 / 拒 fallback 按违规类型路由 / 拒 mock_data/v2/ 子目录（联合审查冲突已取舍）
- 不在本 spec 加 meta_critic_node（留 spec C）

## Tasks

- [x] 1. [R1] mock 数据 + schema 升级（信息源 P0，~5h，可能膨胀）：在 `backend/schemas/domain.py` 新增 `SuggestedDuration` 模型（default 必填 + kid_3_6 / kid_7_12 / senior / multi_gen 可选）；`Poi.suggested_duration_minutes` 升级为 `Optional[Union[NonNegativeInt, SuggestedDuration]]` 双兼容；`Restaurant` 加 `typical_dining_min: Optional[NonNegativeInt]` 字段；用脚本 `scripts/migrate_mock_v2.py` 按 `_AGE_TIER_RULES` dict 批量回填 41 个 POI（type → 桶字典，亲子博物馆 / 主题乐园 / DIY 工坊 / 复合体验馆等 35+ 类型，参考 Agent G report §4 方案 A）+ 按 `_CUISINE_DINING_MIN` 回填 48 个餐厅（健康轻食 40 / 粤菜 90 / 火锅 120 / "高人均" tag +15）+ `mock_data/personas.json` 5 个 persona 加 `default_pace_profile`；先 grep 改动面（`grep -r "suggested_duration_minutes" backend/`）确认 21+ verify 脚本改动量；如果 ≥ 30 行则工时上浮到 7h；测试新增 `tests/test_schema_dict_compat.py` 验 Union 双兼容；不要在本 task 改 `_poi_preview`（那是 Task 2）

- [x] 2. [R2] candidate preview 字段透传 + helper（~1.5h）：新建 `backend/utils/duration_helpers.py:get_duration_for_companions(suggested, companions) -> Optional[int]`，dict 时按 companions 推主导桶（含 ≤6 岁 → kid_3_6 / 含 ≥75 岁 → senior / 多代 → multi_gen / default）取最严值；`backend/agent/blueprint_llm.py:_poi_preview` 加 `"suggested_duration_minutes"` 字段（投影为 int 单值，不给 LLM 看 dict 结构）；`_restaurant_preview` 加 `"typical_dining_min"` 字段；`SearchPoisOutput` 加 `effective_distance_max_km: Optional[float]`，`planner_llm_first._query_pois` 兜底放宽 +2km 时回写；新增 `tests/test_blueprint_llm.py` 5 项断言（dict 投影 / multi_gen 取最严 / restaurant 字段 / effective_distance / 5 岁娃场景 P040 投影 60 而非 100）

- [x] 3. [R3] BlueprintPrompt 主防（~1.5h）：`backend/agent/prompts/blueprint_prompt.py` 改 BLUEPRINT_SYSTEM_PROMPT 范例 JSON 把 `duration_min: 165 / kind: "主活动"` 改成 `duration_min: 75 / kind: "看展"`（避免「主活动 = 长时段」隐性等式）；在「硬性约束」段加紧凑版「按 companion age 分级时长表」（≥6 条规则：婴幼儿 ≤45 / 学龄前 ≤75 / 学童 ≤120 / 长辈 ≤90 / 高龄 ≤60 / 多代取最严，目标 < 320 字符）；加候选预览消费规则「target.suggested_duration_minutes / typical_dining_min 是参考时长，duration_min 取该值 ±25% 区间，偏离须在 rationale 解释」；prompt 容量 cap 从 1500 提到 2200，同步更新 `tests/test_blueprint_prompt.py:test_system_prompt_length_under_hard_cap`；新增 ≥ 6 条 prompt 关键词断言（含 `["suggested_duration", "typical_dining", "5 岁", "75min", "学龄前", "建议范围"]`）

- [x] 4. [R4] BlueprintCritic + critics_v2 兜底（~3h）：`backend/agent/blueprint.py` 加 `_resolve_age_caps(intent) -> tuple[int, list[str]]`（按 companions 取最严：≤3 岁 → 45 / ≤6 岁 → 75 / 7-12 岁 → 120 / ≥75 岁 → 60）+ `_age_aware_duration_critic(blueprint, intent) -> list[BlueprintViolation]`（仅对 target_kind=POI 节点验，违规含 expected_range=(max(45, cap-15), cap)）；接入 `run_blueprint_critics`；`BlueprintViolation` schema 加 `expected_range: Optional[tuple[int, int]] = None`；`backend/agent/v2/critics_v2.py:ViolationCode` 加 `AGE_DURATION_MISMATCH`，`Violation` 加 `expected_range` 字段，加 `_check_age_aware_duration(itinerary, intent)` 镜像（防 ILS 路径绕过）；`format_violations_for_llm` 拼成「{message}（建议范围 {lo}-{hi}）」自然语言不暴露字段名；`_check_demo_restaurant_full` 改为查 mock `reservation_slots[time].available` 真值（不再写死 17:00）；`_check_opening_hours_after_assemble` **本 task 不加（Phase 4 砍）**；新增 `tests/test_age_aware_critic.py` 6 项（5 岁娃 90min 命中 / 70 岁老人 75min 命中 / 多代际取最严 / 无 age 时降级 / expected_range 自然语言不暴露字段 / blueprint 与 v2 镜像等价）

- [x] 5. [R5] ILS 算法兜底 utility 加 overload_penalty（~1.5h）：`backend/agent/planner_hybrid.py` 加 `_overload_penalty(poi, intent) -> float` 函数（按 _resolve_age_caps 同款公式：cap 推算 + suggested 取主导桶 + 超 cap 返 0.3 强惩罚）；`_utility` 公式末尾加项 `-0.5 * _overload_penalty(poi, intent)`（保留原 4 维 comfort/time/cost/smoothness 不变）；`DINING_SLOTS` 改用 `_resolve_time_window(intent, segments)`（来自 planner.py），不再硬编码 `("17:00","17:30","18:00")`；`_retry_with_critic_feedback` 黑名单覆盖扩到 ≥4 类违规（time_window / hard_constraint / dietary / social_context）；新增 `tests/test_planner_hybrid_overload.py` 4 项（5 岁娃 P019 180min 候选被 utility 罚分 / 同 POI 配成人无罚 / DINING_SLOTS 跟随 _resolve_time_window 推 / 黑名单 4 类全覆盖）

- [x] 6. [R6+R7] Narrator 主动质疑 + state 一致性修复（~2h）：`backend/agent/narrator.py:build_narrator_user_message` 签名加 `critic_summary: str = ""` + `quality_warnings: list[str] = None` 两形参；`backend/agent/prompts/narrator_prompt.py:NARRATOR_SYSTEM_PROMPT` 加「主动质疑规则」段（≥ 2 条规则 + 2 条 few-shot 示例）+ 把 LLM 温度从 0.7 降到 0.5；`_template_narration` 兜底加质疑：含 ≤ 6 岁孩 + 任 node.duration_min > 90 时强制追加质疑短语（"宝贝可能会累" / "可以中途休息" 等）；`backend/agent/graph/nodes/narrate.py:narrate_node` 用 `itinerary.model_copy(update={"decision_trace": ...})` 替代原地 mutate（Agent H P1-H6）+ 把 critic_summary 从 state.critic_attempts 拼接喂给 narrator；`backend/agent/graph/nodes/refiner.py:refiner_node` return dict 加 `critic_attempts=[] / fallback_chain=[] / alternatives=[] / quality_issues=[]` 重置（Agent H P1-H3）；`backend/agent/graph/state.py` 删 `routes: list[Any]` 死字段（Agent H P2-H8）+ make_initial_state / refiner 同步删；`backend/agent/graph/nodes/execute_finalize.py:execute_finalize_node` 用 `[n for n in nodes if n.target_kind=="restaurant"]` 全量遍历 + 加 confirm 阶段 narrator 调用（`generate_narration(stage="confirm")`）；`backend/agent/graph/sse_adapter.py` 末尾 DONE event payload 加 `{final_strategy, plan_attempts, critic_attempt_count, fallback_hops_count, total_ms, has_itinerary}` 6 字段总结（Agent H P0-H2）；新增 `tests/test_narrator_active_query.py` 5 项（critic_summary 触发质疑 / template 兜底质疑 / 5 岁娃场景文案多样性 / DONE payload 字段 / refiner 重置 trace）

- [x] 7. [R8] 意图层 + Refiner 升级（~3h）：`backend/schemas/intent.py` 加 `PaceProfile` 模型（single_session_max_min / total_active_min / break_every_min / preferred_dwell_min 全 Optional）；`IntentExtraction` 加 `pace_profile: Optional[PaceProfile] = None` 字段；`backend/agent/prompts/system_prompt.py:INTENT_PARSER_SYSTEM_PROMPT` 加 4 条隐含规则段（「ages ≤ 6 → single_session_max_min ≤ 90」「老人 / `适合老人` → ≤ 90 + break_every_min ≤ 60」「独处放空 → ≥ 60」「商务接待用餐 → ≥ 90」）；`build_intent_parser_system_prompt_with_priors` 新增消费 `personas[user_id].default_pace_profile` 字段，注入 prompt addendum；`backend/agent/refiner.py:_rule_fallback` 字典加 `_KEYWORDS_SESSION_TOO_LONG = ("太久", "太长", "盯不住", "无聊", "扛不住", "腻了")`，命中后**不动 duration_hours / distance_max_km**，而产出 `pace_profile.single_session_max_min` 缩 30%（如原 90 → 65）；`_extract_duration_from_feedback` 扩支持「半小时」/「30 分钟」/「一个半小时」三类正则；`backend/agent/feedback_detector.py:looks_like_feedback` 同步加 SESSION_TOO_LONG 关键词；新增 `tests/test_refiner_session_too_long.py` 6 项（半小时识别 / 一个半识别 / "太久"映射 pace_profile / "太久" 不动 distance / 跨持久化反馈合并 / persona pace_profile 注入命中）

- [x] 8. [R9+R10] 演示场景集 S9 + 端到端验证 + 防再犯（~3h，含 +3h 联调缓冲）：`docs/01-requirements/演示场景集.md` §四 自检表加 S9：「输入 `5 岁娃下午全天去博物馆`」→ 期望 AI 输出「主活动建议 ≤ 90min，建议拆为博物馆 90min + 公园 30min」+ 与原方案一起呈现」；新增 `backend/scripts/verify_planning_quality.py` 端到端脚本（5 岁娃 + 老人 + 独处 + 商务 4 种场景跑 5-10 次，统计 `duration_min ∈ [60, 90]` 首轮命中率 + backprompt 命中率 + ILS 兜底命中率，要求累计 ≥ 95%）；`scripts/audit_review_template.py` 扫 41 个 POI 评论关键词与 type 主题匹配率，要求 ≥ 95%；跑全套 backend tests + verify_schemas + verify_phase0_5 + verify_edge_model 0 红灯（如有失败修到 0）；`docs/03-implementation/pitfalls.md` 追加 ≥ 3 条防再犯条款（[P0] BlueprintPrompt 范例 in-context 锚定 / [P0] candidate_preview 字段集变更 / [P0] critic 三套职责漂移）；`problem.md` 追加本次 spec 的 4 段记录（问题/方案/修改文件/达成效果）

## Task Dependency Graph

```json
{
  "waves": [
    [1],
    [2, 3],
    [4],
    [5, 6, 7],
    [8]
  ]
}
```

说明：
- **Wave 1（mock + schema 基座）**：Task 1 必须最先做，下游 4 个 task 全部依赖 schema 升级 + mock 数据回填。改动面 grep 后可能膨胀到 7h。
- **Wave 2（preview + prompt 主防）**：Task 2（preview 字段透传）与 Task 3（BlueprintPrompt）可并行，互不依赖（不同文件）；都依赖 Task 1 的 schema 字段。
- **Wave 3（critic 兜底）**：Task 4 依赖 Task 1（schema）+ Task 2（字段透传）+ Task 3（prompt 范例改）；critic 是兜底，主防应已就位。
- **Wave 4（外围三轨并行）**：Task 5（ILS）+ Task 6（narrator）+ Task 7（intent + refiner）可并行，分别依赖：T5→T1（utility 用 SuggestedDuration）；T6→T4（narrator 接 critic_summary）；T7→T1（IntentExtraction 加 pace_profile）。
- **Wave 5（验收 + 防再犯）**：Task 8 必须最后做，依赖前 7 个 task 全部就位才能跑 e2e。

## Notes

- **Pydantic Union 双兼容**：W1 的 schema 升级用 `Optional[Union[NonNegativeInt, SuggestedDuration]]`，旧 mock + 旧测试 不破。Spec 完全合并 + 1 个 sprint 后删除 int 分支（写入 pitfalls.md 提醒）。
- **Hackathon 时间盒**：v1 不做 meta_critic_node / NodeDecider 升级 / fallback 按违规类型路由（详见 design.md Out-of-Scope）。
- **风险红旗 1（mock dict 升级让 21+ verify 脚本断言失效）**：W1 必须先 grep 改动面，工时可能上浮 5-7h。
- **风险红旗 4（narrator LLM 不可控）**：W6 同时改 prompt + 温度 + few-shot + 模板兜底四层，最坏情况 LLM 失败时模板路径也质疑。
- **测试基线**：现有 ~485 项 pytest 全部 pass + 新增 ~30 项；无 xfail 转 xpass 的悬空状态。
- **Demo 现场反例验证**：S9 场景必须能演示 「5 岁娃 + 全天博物馆」→ AI 输出"建议拆短"——这是评分项 1 + 2 的高分点。

## Risk & Mitigation

```text
| 风险                                               | 概率 | 影响 | 缓解                                                  |
|---------------------------------------------------|------|-----|------------------------------------------------------|
| mock dict 升级让 21+ verify/test 断言失效           | 高   | 中  | W1 先 grep 改动面 + 工时上浮 + Pydantic Union 双兼容 |
| narrator LLM 不听 critic_summary 指令              | 中   | 中  | 温度 0.7→0.5 + few-shot + template 强制兜底         |
| critic 重生成命中率 < 95%                          | 中   | 中  | ILS overload_penalty 兜底 + give_up 兜底 + W8 e2e 验证|
| prompt cap 1500→2200 破 6-10 个测试                 | 中   | 低  | W3 同时改测试断言；e2e 全套跑 0 红灯               |
| 业界对标精度（TravelPlanner 87% 等）进 spec 文案    | 低   | 低  | spec 引用宽泛措辞（"含访问时长字段"而非具体字段名）|
| meta_critic_node 引发"加节点风潮"                  | 低   | 低  | 本 spec 不加；spec C 单独 + ENV 开关                  |
| W1 工时膨胀阻塞后续 wave                            | 中   | 中  | 必要时拆 W1 为 W1.1（schema）+ W1.2（mock）+ W1.3（迁移测试）三个并行 sub-task |
```
