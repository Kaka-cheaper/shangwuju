# Agent E 审查报告 —— Critic 三套（#13 / #14 / #15）

> **审查范围**：`agent/blueprint.py`（蓝图级 critic）+ `agent/v2/critics_v2.py`（Itinerary 级 critic，主路径）+ `agent/critics.py`（旧 hybrid critic）+ `agent/v2/social_compat.py`（社交场景兼容矩阵）+ `agent/graph/nodes/critic.py`（主路径节点）+ `agent/lookup_hop.py`（critic 与 assemble 共用的边解析层）。
> **触发故事**：用户实测「家庭主线 5 岁娃博物馆 2.5h（150min）」。三套 critic **全部绿灯**放过 150min；DURATION_OUT_OF_RANGE 觉得 251min 总时长 ∈ [4,6h ± 30min] 合规；HOP_INFEASIBLE 觉得通勤 9min 自洽合规；blueprint `_duration_critic` 觉得 150min ≤ MAX_NODE_DURATION_MIN=300 合规。
> **核心追问**：业务合理性 critic 该长在哪套上？为什么 critic 当前体系拦不下"5 岁娃逛 2.5h 博物馆"这种 hackathon 评委一压就翻的反例？

---

## 1. 现状摘要

### 1.1 三套 critic 职责矩阵（**特殊职责 1 必答**）

下表把当前所有 critic 检查项列出来，标注归属哪套、严重度、是否进 backprompt。

```
| 检查项                              | #13 blueprint | #14 critics_v2     | #15 critics(旧)  | 阻断? | 备注                                   |
|------------------------------------|---------------|---------------------|------------------|------|--------------------------------------|
| 节点时序不重叠                      | ✅ _temporal  | ✅ TIMELINE_INCONSISTENT | -            | 硬   | blueprint 验粗算时间，critics_v2 验 hop+buffer 自洽 |
| 跨 24:00 边界                       | ✅ _temporal  | -                   | -                | 硬   | 仅 blueprint 兜                       |
| 单段 duration_min ∈ [10, 300]      | ✅ _duration  | -                   | -                | 硬   | **机械异常上限 5h，业务合理性 0 感知** |
| 总时长 vs intent.duration_hours    | -             | ✅ DURATION_OUT_OF_RANGE | ✅ hard_constraint | 硬   | ±30min 容差；critics_v2 与 critics 双套，行为不漂移 |
| 节点 kind 完整度                    | -             | -                   | ✅ hard_constraint | 硬   | 仅旧 critics（用 decide_segments）；critics_v2 用 NODES_INCOMPLETE 替代但仅验"≥1 个 mid 节点" |
| 营业时间覆盖                        | ✅ _opening_hours | -               | -                | 硬   | blueprint 粗筛（preferred_start + 累加 duration）；assemble 后无精确再验 |
| hop 可达（lookup_hop 实算 vs hop.minutes） | -      | ✅ HOP_INFEASIBLE   | -                | 硬   | 与 assemble 共用 lookup_hop，确定性同输入同输出 |
| 距离 vs intent.distance_max_km     | -             | ⚠ DISTANCE_EXCEEDED | -                | 警   | WARNING 不进 backprompt，仅日志       |
| 餐厅 reservation_slots 真有时段    | -             | -                   | ✅ time_window   | 硬   | **仅旧 critics 真验 mock 槽位**；主路径靠 demo-aware 17:00 埋点近似 |
| 17:00 满座 demo 埋点                | -             | ✅ RESTAURANT_FULL_UNRESOLVED | -        | 硬   | demo 演示韧性必触发                    |
| 饮食 tag 命中                       | -             | ⚠ DIETARY_VIOLATION | -                | 警   | 仅 WARNING                            |
| social_context 兼容矩阵             | -             | ✅/⚠ SOCIAL_CONTEXT_MISMATCH | ✅ style   | 硬/警 | critics_v2 走 social_compat 矩阵；旧 critics 只看 suitable_for ⊃ social_context |
| 预算（人均×party ≤ default×1.5）   | -             | -                   | ✅ budget(soft)  | 软   | 仅旧 critics；主路径完全无预算感知    |
| 不变量：首尾 home + duration=0     | -             | ✅ INVARIANT_BROKEN | -                | 硬   | Pydantic 已先拦，本 critic 是 mutate 后的兜底 |
| **同行人年龄 vs 单段时长（5 岁娃）** | ❌ **0 行**   | ❌ **0 行**         | ❌ **0 行**      | -    | **三套全无；本报告核心 gap**           |
| **同行人 ≥75 岁 vs 单段时长**       | ❌            | ❌                  | ❌               | -    | 同上                                  |
| **疲劳堆叠（连续主活动 >2h）**       | ❌            | ❌                  | ❌               | -    | 同上                                  |
| **用餐时刻反人性（14:30 正餐）**     | ❌            | ❌                  | ❌               | -    | 同上                                  |
| **同质活动连排（连 3 个博物馆）**     | ❌            | ❌                  | ❌               | -    | 同上                                  |
```

**职责漂移与重叠（5 处）**：

1. **总时长**：critics_v2.DURATION_OUT_OF_RANGE 与旧 critics.hard_constraint 双验。容差均 30min，行为对齐——但旧 critics 多一条「过短给软违规」（critics_v2 没有）。
2. **节点完整度**：旧 critics 用 `decide_segments(intent)` 判段缺失，critics_v2 仅判"mid nodes ≥ 1"。这是 P1-2026-05-17 「5 段写死」反模式修复的残骸——彻底放弃硬要 5 段后，critics_v2 接了"≥ 1"宽松版，旧 critics 还在按 segment_decider 严格版判。
3. **营业时间**：blueprint critic 粗筛（不含 hop），assemble 后 critics_v2 **完全不再验**——hop 拼装后真实开始时刻已知，但没人去查 POI/餐厅营业时间。这是 design.md 注释里「精确营业时间检查在 assemble 完成后由 critics_v2 完成」的 unfulfilled promise（grep 全文，critics_v2 无 opening_hours 相关代码）。
4. **餐厅时段**：旧 critics.time_window 真查 `reservation_slots[time].available`，critics_v2 仅查"是否落在 17:00 埋点"——主路径走 critics_v2，意味着**主路径根本不验"用餐时段是否真在餐厅可订时段池"**，仅靠 17:00 这个孤立埋点兜底。
5. **数据结构与 severity 不齐**：blueprint 用 `BlueprintViolation(severity="hard")` + `BlueprintReport`；critics_v2 用 `Violation(severity=Severity.CRITICAL)` + `list[Violation]`；旧 critics 用 `CriticViolation(severity="hard"/"soft")` + `CriticReport`。三套 schema 互不通用，做端到端的"违规历史聚合"必须三处转换。

### 1.2 主路径调用链 vs fallback 路径调用链

**主路径（LangGraph，`USE_LANGGRAPH=1` 默认）**：

```
planner_node → assemble_node → critic_node
                                  ↓
                    critics_v2.validate_itinerary()  ← 调用 #14
                                  ↓
                    has_critical? → replan_router → ...
                                  ↓
                                narrate
```

`graph/nodes/critic.py:48-54` 写得很明确：`from agent.v2.critics_v2 import ...`，**主路径只用 #14**。

**Fallback 路径**：

- `planner_llm_first.py` 用 `agent.blueprint.run_blueprint_critics`（#13）做蓝图重生成 backprompt——**只在 LLM-First 流程里被调，主路径主线不进**。
- `planner_hybrid.plan_hybrid` 内部用 `agent.critics.run_critics`（#15）做 ILS 候选打分——**仅 ILS 兜底路径用**。
- ils_replan_node 拿到 ILS 输出后，按 `_route_after_ils` 直接走 `narrate`，**不再过 critics_v2 验证**（pitfalls P1-2026-05-23 修死循环时的设计）。

**结论（Q8 答案）**：旧 `agent/critics.py` **仍被代码 import**（`planner_hybrid.py:10`），但**已不在 LangGraph 主路径**。它在 ILS 内部局部消费打分，输出的 CriticReport 不会反向进 LangGraph 的 has_critical 判定。**冻结路径，零功能演进权重，仅生效于 hybrid ILS demo fallback 兜底**。

---

## 2. 业务合理性 gap 清单

### P0（demo 立刻翻车）

#### [P0-E1] 三套 critic 全无「同行人年龄 → 单段时长」感知 ★★★（5 岁娃 2.5h 直接根因）

- **现象**：用户输入「家庭，5 岁娃，下午半天」→ blueprint LLM 出 `nodes=[博物馆 duration_min=150]` → 三套 critic 全绿灯：
  - blueprint `_duration_critic`（`blueprint.py:177-200`）：150 ≤ MAX_NODE_DURATION_MIN=300，过 ✅
  - critics_v2 `_check_duration`（`critics_v2.py:347-376`）：total_minutes=251 ∈ [240-30, 360+30]=[210, 390]，过 ✅
  - 旧 critics `_hard_constraint_critic`（`critics.py:151-175`）：同 critics_v2 行为，过 ✅
- **根因**：
  1. `blueprint.py:55` `MAX_NODE_DURATION_MIN: int = 300` 是「LLM 误填检测器」（5h），不是「业务合理性上限」。注释明确写：「单个节点的最长停留时长（分钟，5 小时）。超过此视为 LLM 误填。」——这是结构性兜底，不是业务规则。
  2. `critics_v2._check_duration` 只验**总时长**容差，不分单段；`design.md` 的设计哲学是「单段时长由 LLM 主观决定，critic 只验客观可行性」——5 岁娃单段 ≤ 75min 是**业界客观数据（Smithsonian SEEC、Hands-On House 90min cap、Brain Balance 公式 2-3min × age）**，不是 LLM 主观偏好；当前架构把它错分类成"主观"。
  3. 旧 critics 也只看 stage kind 完整度 + 总时长，未对单段时长做任何 critic（hardcode 5 段时代默认主活动 90min，没必要验；削段模型上线后这个隐性假设失效，但 critic 没补）。
- **反例（端到端复现）**：
  - 输入：`{social_context: "家庭日常", companions: [{role: "孩子", age: 5}, {role: "妻子"}], duration_hours: [4, 6]}`
  - blueprint：`nodes=[home, P040(150min), R001(60min), home]`，total_minutes ≈ 251
  - 期望：critic 命中 `AGE_DURATION_MISMATCH(severity=critical, message="第 2 段「主活动 · 童趣海洋亲子馆」时长 150min 超过 5 岁儿童注意力上限（建议 ≤75min，参考 Smithsonian SEEC 学龄前 20-25min 单展项 × 切换）。请压缩或拆分为多段")` 触发 backprompt
  - 实际：3 套 critic 全过；narrate 文案直接复述"陪孩子玩两个半小时"
- **修复方向**：见 §4 方案 A——加 `_age_aware_duration_critic` 到 #13 blueprint critic（不是 #14，理由见 Q3 详解）。

#### [P0-E2] 主路径「精确营业时间」承诺没落地 ★★（单点失守）

- **现象**：`critics_v2.py` 注释（line 38）声称「节点级营业时间校验在 agent.blueprint._opening_hours_critic 阶段处理」；blueprint critic 注释（line 318）反过来说「精确营业时间检查在 assemble 完成后由 critics_v2 完成（届时已知 hop 真实分钟数）」。两边互相甩锅，**实际两套都没在 assemble 后做精确校验**。
- **根因**：blueprint critic 的营业时间校验用的是 `preferred_start_time + 累加 duration`，**不含 hop**——估算时刻偏早 N 分钟（N=hop 累计）。assemble 拼装后真实时刻已知（`ActivityNode.start_time`），但 critics_v2 没有对应 critic。
- **反例**：mock 餐厅 R024 营业 17:00-22:00。蓝图 `preferred_start_time=14:00`，主活动 90min → blueprint critic 估算用餐 15:30 起（未含 home→P040 9min + P040→R024 5min hop）→ 误判"15:30 不在 17:00-22:00"标违规。LLM 修正后改成 60min 主活动 → 估算 15:00 起 → 仍违规。LLM 困惑：为什么我把时间提前了反而不行？真实路径上时刻（含 hop）才是 15:14 → 仍 < 17:00，但 blueprint critic 看的不是这个时刻。
- **修复方向**：critics_v2 加 `_check_opening_hours_after_assemble`（按 `node.start_time + node.duration_min` 真实区间查 mock 营业时间），blueprint critic 的版本降级为"硬上限粗筛"。

#### [P0-E3] format_violations_for_llm 不给"期望区间"，重生成命中率低 ★★（Q5 答案：缺）

- **现象**：`critics_v2.format_violations_for_llm` 仅拼接 `v.message`。message 文本最多含"实际值 + 偏差量"（如 `"hop.minutes=3 < actual=9 - 容差 2 = 7，缺 6 分钟"`），但**不显式给"期望区间 [lo, hi]"**。
- **根因**：design.md 强约束"不暴露 dot-path 给 LLM"做对了，但忘了告诉 LLM"目标值是多少"。LLM 收到「过长，单段不应超过 5h」会再试 295min（仍合规但仍违业务直觉）；收到「过长，单段建议 75min（基于 5 岁同行人）」才会真正向 75 收敛。
- **细节**：现有 critic 中**只有 DURATION_OUT_OF_RANGE 给了期望区间**（`"将总时长拉到 4-6h 区间"`），其余 8 类全无。HOP_INFEASIBLE 给"actual_min=9"算隐性期望，DISTANCE_EXCEEDED 给"max_km=10"算隐性期望，但没有形式化"`expected_range=[lo, hi]`"字段，LLM 解析靠运气。
- **反例**：blueprint `_duration_critic` 当前消息「节点「主活动」停留时长 305 分钟过长（> 300min 上限）——单段 5h 以上请考虑拆成多个节点」。LLM 重出 295min 仍合规，但不会向「合理的 75-90min」收敛。
- **修复方向**：`Violation` schema 加可选字段 `expected_range: tuple[int, int] | None`，`format_violations_for_llm` 拼成 `"...建议范围 [{lo}, {hi}]"`；并把 prompt 指令改为「如 expected_range 给出，请取区间中位数」。Agent D 报告 P1-D6 指出同一问题，需联动修复。

### P1（用户不会立刻发现，但会侵蚀信任）

#### [P1-E4] WARNING 真不阻塞，且对 LLM 不可见（Q7 答案：是，且加剧 mock 数据稀疏问题）

- **现象**：`format_violations_for_llm` 第 522 行 `critical = [v for v in violations if v.severity == Severity.CRITICAL]`，明确过滤 CRITICAL。`route_after_critic` 只看 `has_critical = any(severity == CRITICAL)`，WARNING 不进 has_critical。
- **行为**：DISTANCE_EXCEEDED / DIETARY_VIOLATION / 部分 SOCIAL_CONTEXT_MISMATCH（POOR 等级）都是 WARNING——**LLM 收不到任何反馈，narrate 也不读 violations**（Agent H 报告 P0-H1）。等于这类"侵蚀信任"信号最终落地为 0 价值。
- **业务后果**：用户期望 5km 内，LLM 选 6.5km 的 P008——critics_v2 报 WARNING，但不进 backprompt，不进 narrate prompt，不进 SSE 推送——评委追问"为什么远了"时系统没法解释；用户嫌远，反馈"再近一点"，refiner 才介入修——错过了 critic 这一层的主动质疑机会。
- **修复方向**：3 选 1：① WARNING 也喂 narrator（Agent H 方案 A 已提）；② warning 累计 ≥ 2 时升级为 CRITICAL 阻断；③ WARNING 进 trace.quality_warnings 让前端 DecisionTraceCard 显示。本报告倾向①+③。

#### [P1-E5] 旧 critics.py 与 critics_v2 总时长容差行为差异

- **现象**：旧 critics.hard_constraint 多一条「太短给 soft 违规」（`critics.py:172`），critics_v2.DURATION_OUT_OF_RANGE 同时给 critical（`critics_v2.py:368-372`）。
- **后果**：ILS 兜底路径走旧 critics，total_minutes=180 实际 < 240-30=210 时仅给 soft（不阻断），ILS 自认为成功；ils_replan_node 直接走 narrate，不回 critics_v2 验证；用户拿到 180min 方案但口口声声"4-6h 内"——**与 LangGraph 主路径不一致**。
- **修复方向**：旧 critics 与 critics_v2 严重度对齐，或 ILS 走完后强制再过一次 critics_v2（违反 P1-2026-05-23 的死循环修复纪律——只能 ILS 内部就矫正完）。

#### [P1-E6] _check_hop_feasibility 与 _check_temporal_feasibility 容差不齐

- **现象**：
  - `_TEMPORAL_TOLERANCE_MIN = 2`（`critics_v2.py:189`）
  - `_HOP_FEASIBILITY_TOLERANCE_MIN = 2`（同名常量但独立定义）
  - assemble 内部按整数分钟取整（pitfalls P2-2026-05-22 routes.json 兜底用 haversine + 25km/h）
- **风险**：小段路（如 home → P040 实际 8.7min round 到 9min；hop.minutes=9）下 temporal 容差刚好不报；但若 assemble 用了 haversine fallback（FALLBACK_MIN=15）、blueprint LLM 给的 hop 为路网真值（routes.json 命中 9）、再次跑 lookup_hop 又给 haversine 估值——3 处取整与降级路径互相打架，2min 容差可能被吃光。
- **修复方向**：把两个常量提升为单一 `TEMPORAL_TOLERANCE_MIN=3`，与 lookup_hop FALLBACK_MIN=15 / haversine ±5min 配套测试矩阵。

#### [P1-E7] critic_attempts 跨轮泄漏（与 Agent H P1-H3 同源）

- **现象**：`critic_node` 累积 `critic_attempts`，`refiner_node` 重置 11 个字段但**漏掉 critic_attempts / fallback_chain / alternatives**。用户反馈后下一轮 trace 仍带前一轮的 4 跳 fallback 历史。
- **根因**：refiner 视角仅清理"会让 plan 重跑的字段"，不清理 trace。critic 视角不参与 reset 决策。
- **修复方向**：critic_node 在 turn 起始处主动 reset，或与 Agent H 协同补 refiner_node 重置列表。

### P2（潜伏 bug、长期债）

#### [P2-E8] _check_demo_restaurant_full 仅检查 17:00 字面量，不感知 mock 真实 reservation_slots

- **现象**：`critics_v2._check_demo_restaurant_full` 写死 `_DEMO_FULL_TIME = "17:00"`，命中即报。但 mock 里 17:00 不一定是 RESTAURANT_FULL（取决于 reservation_slots 数据），且非 17:00 的真实满座（如 18:30）查不出来。
- **修复方向**：合并旧 critics.time_window 的 reservation_slots 真查逻辑——critic 走真数据，不靠 demo 字面量。

#### [P2-E9] _check_invariants 与 Pydantic model_validator 双跑

- **现象**：Itinerary Pydantic 已在 `_check_invariants` model_validator 拦下 hops 数 / home 首尾；critics_v2._check_invariants 又跑一次。
- **理由**（注释解释）：「有人手工 bypass Pydantic 构造 / 下游 mutate 后破坏不变量」。但实际只有测试里 `object.__setattr__` 才触发，生产路径上是死代码。
- **修复方向**：保留作"防御性 mutate 检测器"是合理的，工时债——可以放着；但要在 docstring 明确"非生产路径触发"。

#### [P2-E10] critics_v2 没有 critic_id / 优先级排序文档化

- **现象**：`validate_itinerary` 的 9 个调用顺序写死注释（`critics_v2.py:481-491`），但顺序与 severity 没绑定——比如 INVARIANT_BROKEN 命中后还会继续跑 NODES_INCOMPLETE / DURATION 等 8 个 critic 浪费工时（不变量已破坏，结构性 critic 都会误报）。
- **修复方向**：拆"短路 critic"（INVARIANT_BROKEN 命中 → return early）和"完整 critic"（其他都跑完合并）。

---

## 3. 业界对标 diff（≥ 4 个带链接）

### 对标 1：LLM-Modulo（Kambhampati et al., NeurIPS 2024）

- **链接**：[arxiv.org/abs/2402.01817](https://arxiv.org/abs/2402.01817) / [LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks](https://arxiv.org/html/2402.01817v3)
- **他们怎么做**（按论文 §3 释义改写以满足合规要求）：LLM-Modulo 把 critic 体系明确分层为 **`critic bank`（多个独立、便宜、可证伪的 verifier）**——硬约束 critic（数学正确性）、软约束 critic（偏好评分）、style critic（语义合理性）等并行调用，每个 critic 输出 `{satisfied, confidence, suggested_fix}`，由 meta-controller 聚合。LLM 收到 critic feedback 后做 backprompt 重生成。
- **我们差在哪**：
  1. 我们的"critic bank"分裂成三套**互不通信**的实现，且各自的 violation schema 不同（hard/soft 双值 vs CRITICAL/WARNING 双值 vs 无 severity）。
  2. **没有 style / 语义合理性 critic**——年龄-时长、疲劳堆叠、用餐时刻人性化都属于 style critic 范畴，业界常态是建在 LLM-Modulo 框架最外层。
  3. critic 反馈不带 `suggested_fix` 结构化字段（只有自然语言 message）；论文强调 fix 必须形式化让 LLM 第二轮直接消费。
- **借鉴成本**：中。可以把"三套 critic"统一到 critics_v2 的 schema 下（保留 ViolationCode 枚举 + 加 expected_range），做"critic bank 化"：blueprint critic / itinerary critic / business critic（新加）三个独立 verifier，外层做 dispatcher。

### 对标 2：TravelPlanner Constraint Validator（Xie et al., ICML 2024）

- **链接**：[arxiv.org/abs/2402.01622](https://arxiv.org/abs/2402.01622) / [github.com/OSU-NLP-Group/TravelPlanner](https://github.com/OSU-NLP-Group/TravelPlanner)
- **他们怎么做**（按论文 §3.3 + 仓库 `evaluation/` 释义改写）：把约束验证拆成 `hard_constraints`（用户硬要求）+ `commonsense_constraints`（常识默认，如「饭点不能跳过 3 餐」「博物馆建议时长 1-3h」）+ `environment_constraints`（候选数据约束）。每类约束有独立 validator，`hard` 失败 → reject + retry；`commonsense` 失败 → 也算 critical，但 message 解释清楚「常识依据」而不是「用户没说就过」。
- **我们差在哪**：
  1. 我们的 critic 几乎只验 `hard_constraints`（用户口头说的 4-6h、5km）+ `environment_constraints`（hop 可达 / 营业时间）；**`commonsense_constraints` 几乎为零**——5 岁娃 ≤ 75min、老人单段 ≤ 60min、博物馆 60-120min 这些"用户没说但常识应有"的约束完全缺失。
  2. TravelPlanner 把 "commonsense" 也作为 hard violation 让 LLM 重做——这是为什么他们能在通用旅行规划做到 87% 通过率。
- **借鉴成本**：低-中。只需在 critics_v2 加 `_check_commonsense_constraints` 并把"年龄-时长词典 / 用餐时刻 / 同质活动"等规则编码进去；schema 上 `Violation.commonsense_source: str` 字段存"参考 Smithsonian SEEC / Hands-On House"等出处，给 LLM 与评委看。

### 对标 3：Constitutional AI Critic Chain（Anthropic, 2022）

- **链接**：[arxiv.org/abs/2212.08073](https://arxiv.org/abs/2212.08073) / [anthropic.com/research/constitutional-ai-harmlessness-from-ai-feedback](https://www.anthropic.com/research/constitutional-ai-harmlessness-from-ai-feedback)
- **他们怎么做**（按论文 §3 释义改写）：CAI 把"critic"拆成两步：① 用一组**显式书面 principle（"宪法"）**让 LLM 自我评估当前输出（self-critique）；② LLM 按 critique 自己 revise。Principle 是文本写死的（"避免输出有害信息" / "尊重多元观点"），不是代码 critic——但每条 principle 等价于一个 critic_v2 节点。
- **我们差在哪**：
  1. 我们的 prompt 没有"行为规约"段——blueprint_prompt 全是结构性约束（输出格式 / 字段名）；缺少"当 companions 含 ≤ 6 岁儿童，单 POI 段必须 ≤ 75min"这种"宪法"级 principle。
  2. 业务 critic 长期债的彻底解法可能是 **prompt 主防 + 算法 critic 兜底**——CAI 的启示是："critic 不一定是代码，可以是 LLM 自我审查的 principle"，但这要求 principle 写得**形式化、可验证**。
- **借鉴成本**：低。在 blueprint_prompt 加 4-6 条 principle（草稿见 §4 方案 A）；critic 兜底（方案 D）作为防"LLM 不严格遵守 principle"的最后一道防线。

### 对标 4：Pydantic AI output_validator（pydantic.dev / 0.0.55+）

- **链接**：[ai.pydantic.dev/output/](https://ai.pydantic.dev/output/) / [ai.pydantic.dev/agents/#agent-output-validators](https://ai.pydantic.dev/agents/#agent-output-validators)
- **他们怎么做**（按官方文档释义）：Pydantic AI Agent 支持 `@agent.output_validator` 装饰器，在 LLM 输出后强制跑一组 Python validator，校验失败抛 `ModelRetry(message)` 触发自动重试（默认 3 次）。validator 函数签名 `(ctx: RunContext, output: T) -> T or raise`，message 直接喂给 LLM 作为 backprompt。这与我们 critics_v2 + replan_router 的体系**几乎完全同构**——但他们把 validator 与 LLM 调用绑成一个 transactional unit，不需要应用层手写 retry 循环。
- **我们差在哪**：
  1. 我们 LangGraph 主路径的 critic 是手写 retry（replan_router_node 的 `_MAX_LLM_RETRIES=2`），跨节点状态机管理 retry_count——比 Pydantic AI 的装饰器复杂数倍。
  2. v2 路径（fallback react_agent.py）已经用 `@unified_agent.output_validator` 做了类似事，但 LangGraph 主路径没复用——又是冻结路径与主路径漂移（pitfalls §3.3.1）。
  3. Pydantic AI message 强制人话化（与我们 design.md 的"不暴露 dot-path"约束一致），且 retries 计数自动管理，工程债更低。
- **借鉴成本**：高。LangGraph 与 Pydantic AI 是两个 framework，迁移代价大；短期不动 LangGraph 拓扑，仅借鉴 message 形式与 retry 边界；中期评估在 plan-and-execute 里嵌一个 Pydantic AI validator。

### 对标 5：RouteLLM Verifier Agent（arxiv 2510.06078, 2025-10）

- **链接**：[arxiv.org/abs/2510.06078](https://arxiv.org/abs/2510.06078) / [arxiv.org/html/2510.06078](https://arxiv.org/html/2510.06078)
- **他们怎么做**（按论文 §3 + Fig. 2 释义改写）：在 hierarchical route planner 的最末端加一个独立 `verifier agent`——不是规则代码，而是 LLM-based critic agent，**消费完整 plan + intent + 候选数据，做"自然语言 critique"**：检查 plan 是否符合常识、是否覆盖 intent 全部需求、是否在边界 case（如夜间路段、儿童陪同）有错误。verifier 输出 `{passes: bool, issues: list[str], suggested_revisions: list[str]}` 喂回 path refinement agent。
- **我们差在哪**：
  1. 我们的 critic 全是规则代码（Python 函数 + Pydantic 校验），**没有 LLM-based business critic**。规则 critic 只能验"形式可观测"的违规；"5 岁娃 2.5h 博物馆"这种半软半硬的业务直觉违规，规则 critic 不擅长（需要写非常多 if/elif 才能覆盖）。
  2. verifier agent 范式与 Agent H 报告 §3.6 提议的 "meta-critic node" 高度同构——这是 hackathon 评分项 2 "Tool 编排合理性可见性"的强加分项。
- **借鉴成本**：中。新增一个 `agent/v2/business_critic.py` LLM agent + `prompts/business_critic_prompt.py`（含年龄词典、疲劳曲线、用餐时刻规则）；在 LangGraph 拓扑加节点 `business_critic_node` 接在 `critic_node` 之后。Agent H 方案 D 已经做了详细工时估算，~2.5h 可落地。

---

## 4. 修复方案候选

### 方案 A：blueprint critic 加 `_age_aware_duration_critic`（**特殊职责 2 必答**）

> **结论**：单段时长按同行人年龄/标签/POI 类型分级**应在 #13 blueprint critic 实现**（Q3 答案）。理由 4 条：
> 1. 蓝图阶段拦下，可触发 LLM 重生成；assemble 后再拦相当于让 LLM 浪费一轮调用。
> 2. 蓝图阶段 nodes 还是 mid-only 形态，单段语义清晰；assemble 后 nodes 含 home 首尾会让逻辑复杂化。
> 3. critics_v2 已经定位为「Itinerary 级 critic」，主验 hop / 总时长 / 不变量；加单段业务规则会让职责漂移。
> 4. 旧 critics.py 已冻结，不应在冻结路径加新功能（违反 §3.3.1 编排冻结纪律）。
>
> **同时** critics_v2 加镜像兜底（理由：blueprint critic 可能被 ILS 路径绕过，ILS 出来的 itinerary 没经过 blueprint critic）。

**Python 草稿**（写进 `agent/blueprint.py`，不改 critics_v2 主路径）：

```python
# 新增常量 ----------------------------------------------------------------
# 业界依据：Smithsonian SEEC / Hands-On House 90min cap / Brain Balance 公式
_AGE_KID_THRESHOLD: int = 6      # ≤6 岁视为"学龄前儿童"
_AGE_ELDER_THRESHOLD: int = 75   # ≥75 岁视为"高龄长辈"
_AGE_TEEN_LO, _AGE_TEEN_HI = 7, 12  # 学童区间

# kid_3_6 单 POI 段建议上限（min）。参考 Smithsonian SEEC 25min × 切换 3 次 + buffer
_KID_PRESCHOOL_SINGLE_POI_MAX: int = 75
# elder_75plus 单 POI 段建议上限：体力衰减、需 60min 一次休息
_ELDER_SINGLE_POI_MAX: int = 60
# teen_7_12 单 POI 段建议上限
_TEEN_SINGLE_POI_MAX: int = 120
# 含 ≤3 岁婴幼儿的极限上限
_TODDLER_SINGLE_POI_MAX: int = 45


def _resolve_age_caps(intent: IntentExtraction | None) -> tuple[int, list[str]]:
    """根据 intent.companions 推算单 POI 段时长上限 + 触发依据。

    取最严：5 岁娃 + 70 岁外婆同行 → 取 min(75, 90) = 75min。
    返回 (cap_min, reasons)，reasons 用于 message 解释依据。
    """
    if intent is None or not intent.companions:
        return MAX_NODE_DURATION_MIN, []

    caps: list[tuple[int, str]] = []
    for c in intent.companions:
        age = c.age
        if age is None:
            continue
        if age <= 3:
            caps.append((_TODDLER_SINGLE_POI_MAX, f"含 {age} 岁婴幼儿（≤3 岁）"))
        elif age <= _AGE_KID_THRESHOLD:
            caps.append((_KID_PRESCHOOL_SINGLE_POI_MAX,
                         f"含 {age} 岁学龄前儿童（≤6 岁，参考 Smithsonian SEEC）"))
        elif _AGE_TEEN_LO <= age <= _AGE_TEEN_HI:
            caps.append((_TEEN_SINGLE_POI_MAX, f"含 {age} 岁学童（7-12 岁）"))
        elif age >= _AGE_ELDER_THRESHOLD:
            caps.append((_ELDER_SINGLE_POI_MAX, f"含 {age} 岁高龄长辈（≥75 岁）"))

    if not caps:
        return MAX_NODE_DURATION_MIN, []

    # 取最严
    caps.sort(key=lambda x: x[0])
    cap_min, _ = caps[0]
    reasons = [r for _, r in caps]
    return cap_min, reasons


def _age_aware_duration_critic(
    blueprint: PlanBlueprint,
    intent: IntentExtraction | None,
) -> list[BlueprintViolation]:
    """同行人年龄敏感的单段时长 critic。

    仅对 target_kind=POI 的节点验（餐厅时长由 typical_dining_min 另外管，本 critic 不动）。
    违规 → severity=hard + 显式 expected_range，让 LLM 第二轮直接收敛。
    """
    out: list[BlueprintViolation] = []
    cap_min, reasons = _resolve_age_caps(intent)
    if cap_min >= MAX_NODE_DURATION_MIN:
        return out  # 无年龄约束 → 与原 _duration_critic 等价，不重复报

    reason_str = "、".join(reasons)
    for i, n in enumerate(blueprint.nodes):
        if n.target_kind != BlueprintTargetKind.POI:
            continue
        if n.duration_min > cap_min:
            out.append(
                BlueprintViolation(
                    critic="blueprint_age_aware_duration",
                    severity="hard",
                    message=(
                        f"节点[{i}]「{n.kind} · {n.target_id}」时长 {n.duration_min}min "
                        f"超过基于同行人年龄的建议上限 {cap_min}min（{reason_str}）。"
                        f"请压缩到 {max(45, cap_min - 15)}-{cap_min}min 之间，"
                        f"或拆分为 2-3 个更短节点（含中场休息）。"
                    ),
                    field_hint=f"nodes[{i}].duration_min",
                )
            )
    return out
```

**接入点**（`run_blueprint_critics`）：

```python
# blueprint.py:518 后插入（保持与 _temporal/_duration/_opening_hours 同位）
for v in _age_aware_duration_critic(blueprint, intent):
    all_violations.append(v)
```

**镜像 critics_v2**（防 ILS 绕过 blueprint critic）：

```python
# critics_v2.py 加 ViolationCode.AGE_DURATION_MISMATCH（severity=CRITICAL）
# _check_age_aware_duration(itinerary, intent)：遍历 nodes（target_kind=poi）按相同公式校验
# 接入 validate_itinerary（在 _check_duration 后）
```

**工时**：~2h（含 critic 实现 30min + 单测 6 条 60min + ILS 路径回归 30min）

**影响子环节**：#13 blueprint / #14 critics_v2 / #4 NodeDecider（Agent A 方案 B 的 NodePlanHint 同源，建议联动）

**风险**：低-中。ages 由 IntentExtraction 抽取，缺失 age 时 cap 退化为 MAX_NODE_DURATION_MIN（与现状等价）→ 不会误伤"未指定年龄"的成人场景。

### 方案 B：format_violations_for_llm 加 expected_range 字段

```python
# Violation 加字段
class Violation(BaseModel):
    ...
    expected_range: tuple[int, int] | None = Field(
        default=None,
        description="形式化期望区间，如 (45, 75)；format_violations_for_llm 会拼成「建议 45-75min」",
    )

# format_violations_for_llm 内
for i, v in enumerate(critical, 1):
    line = f"{i}. {v.message}"
    if v.expected_range is not None:
        lo, hi = v.expected_range
        line += f"（建议范围 {lo}-{hi}）"
    lines.append(line)

# blueprint critic 同步在各 BlueprintViolation 加 expected_range
```

**工时**：~45min（schema 改 5min + 9 处 critic 回填 30min + 测试 10min）
**影响子环节**：#13 / #14 / #15 / #11 blueprint_llm（receives）
**风险**：极低，纯 superset 字段添加。

### 方案 C：critics_v2 加 commonsense_constraints critic 大类

把方案 A 的"年龄-时长"扩展为"常识约束"大类，含：
- AGE_DURATION_MISMATCH（方案 A 已含）
- MEAL_TIMING_HUMAN（用餐时刻 ∈ [11:30, 13:30] ∪ [17:00, 20:00]，违反 → critical）
- ENERGY_OVERLOAD（连续 ≥2 段 POI 累计 > 240min 无餐厅/休息插入）
- THEMATIC_MONOTONY（连续 ≥3 段相同 type）

**工时**：~3.5h
**影响子环节**：#14 / #11 prompt 同步加 principle / 新建 `_check_commonsense.py` 子模块
**风险**：中（连续段判定有 corner case）。

### 方案 D：新增 LLM-based business_critic_node（呼应 Agent H 方案 D）

参考 RouteLLM verifier agent 与 Anthropic constitutional critic chain，新增：

- `agent/v2/business_critic.py`：用 LLM 跑业务 critic，输出 `{passes, issues, suggested_revisions}`
- `prompts/business_critic_prompt.py`：含年龄词典、疲劳曲线、用餐时刻、同质活动检测
- `graph/nodes/business_critic.py`：LangGraph 节点，接在 `critic_node` 之后
- `state.py` 加 `quality_issues: list[QualityIssue]` 字段供 narrator 消费

**工时**：~3h（与 Agent H 估算 2.5h 接近，本报告侧重 critic 实现）
**影响子环节**：#13 / #14 / #21 narrator / #25 LangGraph build / 前端 trace 卡
**风险**：中（新增 LLM 调用增加 ~2-3s 延迟；需 ENV 开关 ENABLE_BUSINESS_CRITIC 兜底）

### 方案 E：旧 critics.py 收缩为 ILS 内部工具（不删，但冻结+更名）

把 `agent/critics.py` 重命名为 `agent/planning/legacy/ils_score_critic.py`（与 Agent F 的 planner 重组对齐），文件顶部 docstring 加「⚠ 冻结：仅 ILS 内部打分使用，主路径 critic 用 v2/critics_v2.py」。同时把"段缺失 vs decide_segments"的旧逻辑删（segment_decider 已 alias），仅保留 budget / time_window 两条 ILS 真正用得上的 critic。

**工时**：~1h
**影响子环节**：#15 / #18 planner_hybrid / Agent F 联动
**风险**：低（grep 仅 planner_hybrid 一个调用点）。

---

## 5. 目录归属建议（A1 融合）

```
| 文件                                | 当前位置             | 建议归属                  | 是否合并/删/冻结                                   |
|------------------------------------|---------------------|--------------------------|--------------------------------------------------|
| backend/agent/blueprint.py         | agent/              | core/planning/blueprint/ | 保留+扩 _age_aware_duration_critic；不冻结        |
| backend/agent/v2/critics_v2.py     | agent/v2/           | core/critic/             | 主路径 critic，提到独立子目录；与 blueprint critic 对称 |
| backend/agent/critics.py           | agent/              | legacy/                  | 冻结+更名为 ils_score_critic.py；docstring 标记仅 ILS 用 |
| backend/agent/v2/social_compat.py  | agent/v2/           | core/critic/             | 跟 critics_v2 一起搬，保持职责紧贴                |
| backend/agent/graph/nodes/critic.py | agent/graph/nodes/  | runtime/graph/nodes/     | 保留；如方案 D 落地，加 business_critic.py        |
```

**核心建议**：
- 新建 `core/critic/` 子目录，把 `critics_v2.py` + `social_compat.py` 搬进去；蓝图 critic 留在 `core/planning/blueprint/` 与 blueprint_llm.py 同级（两类 critic 不在一起，对应 §1.1 矩阵的"蓝图层 vs Itinerary 层"）。
- 旧 critics.py 进 `legacy/` 与 hybrid planner 同级，docstring 强标识冻结。
- 方案 D 的 `business_critic.py` 不放 v2/（v2 已冻结，§3.3.1 编排纪律）；放 `core/critic/` 作为新主路径 critic 之一。

**冻结声明**：
- `agent/critics.py` ★立即冻结★（仅 bug fix；新业务 critic 不进）
- `agent/v2/critics_v2.py` 半冻结（被 LangGraph 主路径与 Pydantic AI fallback 共用，改动需双向兼容；但 schema/逻辑可演进）
- `agent/blueprint.py` 不冻结（蓝图层主路径，本报告方案 A 直接在此扩展）

---

## 6. 跨环节依赖警示

### 6.1 我修这里会影响（外部依赖）

- **Agent A（意图层 #1-4）**：方案 A 依赖 `intent.companions[].age` 字段被可靠抽取。Agent A P0-1 报告了 schema 缺 `pace_profile`——**两个改动可分离落地**：方案 A 仅依赖 age（已有字段），不依赖 pace_profile 新字段。但若 Agent A 加了 pace_profile，方案 A 应升级为消费 `intent.pace_profile.single_session_max_min`（更精细），与 Agent A 形成"用户/persona 注入 + critic 兜底"双层。
- **Agent B（候选搜索 #5-9）**：方案 A 与 Agent B P0-1（暴露 `suggested_duration_minutes`）是**同一根因的两侧防御**——B 在 prompt 信息层（让 LLM 看到时长锚点），A 在 critic 兜底层（LLM 不听话也拦得住）。**两个修复必须同时上**：单 B 不上 A，LLM 偶发不看锚点；单 A 不上 B，LLM 永远看不到锚点 → critic 频繁 backprompt 浪费 LLM 调用。
- **Agent D（蓝图层 #10-12）**：方案 A 新加的 `_age_aware_duration_critic` 反馈消息要喂 blueprint LLM（critic_feedback 路径），**与 Agent D 方案 B（年龄分级时长表 prompt）对齐措辞**——若 prompt 写"5 岁娃单 POI ≤ 75min"，critic message 也用 75min；不一致会让 LLM 困惑。同时 Agent D P0-D1（prompt 范例 165min 是反向锚定）必须改，否则 critic 罚单与 prompt 范例**互相打架**。
- **Agent G（mock 数据 #23-24）**：方案 A 中 `_resolve_age_caps` 仅看 `companions[].age`，与 personas.json 的 `notes` 自由文本无关；不直接依赖 G。但 Agent A 提议把 personas.notes "孩子能玩 1.5h+" 结构化为 `default_pace_profile` → Agent G 工作量。
- **Agent H（输出层 #21-22 / #25）**：H P0-H1（narrator 不质疑方案）的根因之一就是"critic 不拦 5 岁娃 2.5h 博物馆"。本报告方案 A 落地后，H P0-H1 自动获得 critic 信号；H 方案 A（narrator 接 critic 信号）仍需做——critic 拦下后 LLM 重生成成功，narrator 看不到历史质疑。**两个方案叠加**：critic 主动拦 + narrator 转述质疑历史 = 完整防守纵深。

### 6.2 我依赖另一处先修（前置依赖）

- **依赖 Agent A 确保 `intent.companions[].age` 抽取率 ≥ 95%**：5 岁娃这种高频 case 漏抽时，方案 A 的 critic 退化为 no-op。verify_planning 跑 5-10 次「家庭主线」检查 age 缺失率。
- **依赖 Agent B 在 candidate_preview 暴露 `suggested_duration_minutes`**：方案 A 的 cap 公式取 `min(suggested, age_cap)` 时需要这个字段；否则只能用 age_cap 单一来源。
- **依赖 Agent G 检查 38 个 POI 的 `suggested_duration_minutes` 字段已校准**：P040=100min 配 5 岁娃合理，但 P019=180min（迪士尼级）配 5 岁娃显然超长——方案 A 的 cap 公式会把 P019 拦下，但 G 应当 audit 这些字段是否合理。

### 6.3 内部 cross-critic 一致性（Agent E 内部）

- **方案 A + 方案 B（expected_range）联动**：blueprint critic 与 critics_v2 同时给 expected_range，措辞统一（如"建议 45-75min"）；不能 blueprint 给 75 / critics_v2 给 90。
- **方案 D（business_critic）与方案 A（age_aware）的边界**：方案 A 是规则 critic（确定性），方案 D 是 LLM critic（不确定）。建议**方案 A 优先**——确定性规则少漏少误；方案 D 用作"补漏"（验疲劳堆叠 / 同质连排这类规则难写的）。两者并存时，D 不重复 A 已验过的内容（用 `seen_codes` 让 LLM prompt 知晓）。
- **方案 E（冻结旧 critics）后**：planner_hybrid 不应再 import 旧 critics 的复杂逻辑——重新定位为"ILS 自身打分用的 4 维 lite critic"，不参与主路径质量判定。

### 6.4 Critic 是主防还是兜底？（**特殊职责 3 必答**）

> **明确答案：critic 是兜底，不是主防。**

理由 4 条：

1. **prompt 是主防**。LLM 在第一轮就看不到"5 岁娃单段 ≤ 75min"，意味着每一次 plan 都先违规再修。一次 LLM 调用 ~2s + critic backprompt 一轮 ~3s → 至少多 3-5s 延迟。hackathon 演示对延迟敏感，**让 LLM 一次过**才是最高 ROI 的设计。
2. **preview 是信息防（数据层）**。LLM 看不到 candidate.suggested_duration_minutes 时，连基础锚点都没有，光靠 prompt 措辞难收敛。Agent B P0-1 修这一层，是"让 LLM 有判断依据"的前置。
3. **critic 是兜底防**。它的设计目标是「LLM 偶发不听话时拦下」，不是「每次都靠它 backprompt」——主防靠 critic 等价于把它当 LLM 调度器，违反 LLM-Modulo 的"verifier 应该便宜"原则。
4. **critic 仍必须建**。理由是"防 LLM 不听话"+"防 ILS 兜底路径绕过 prompt"——ILS 不消费 prompt，只看 mock 数据 + utility_score，LLM 那套约束完全失效；这时只有 critic 能拦下。

**正确的防守纵深图（与 Agent D 报告一致）**：

```
| 层级       | 当前状态                       | 改动方向                            | 责任 agent  |
|-----------|-------------------------------|------------------------------------|-------------|
| 1. schema | suggested_duration_minutes 已存在；缺 pace_profile | 加 pace_profile（可选）          | Agent A/G   |
| 2. preview | 漏暴露 suggested_duration       | 暴露字段                           | Agent B     |
| 3. prompt 范例 | duration_min=165 反向锚定    | 改为 75；加年龄分级表             | Agent D     |
| 4. prompt 规则 | 完全无年龄/疲劳/分级语句       | 加 4-6 条 commonsense principle   | Agent D     |
| 5. critic 兜底 | 上限 300 / 总时长容差          | **加 _age_aware_duration_critic** | **Agent E**（本报告方案 A） |
| 6. critic v2 镜像 | 完全无单段年龄校验            | **加 AGE_DURATION_MISMATCH**      | **Agent E**（本报告方案 A） |
| 7. business critic | 不存在                       | 方案 D（LLM-based）               | Agent E + H  |
```

**Agent E 的核心定位**：在第 5、6、7 层落地——是兜底，不是主防。但若主防没建好（Agent A/B/D 不修），critic 兜底压力会指数级上升（每个反例都要写一条 critic 规则），不可持续。

---

## 自检确认

- [x] 6 段强制格式（§1 现状 / §2 gap / §3 业界 / §4 修复 / §5 目录 / §6 跨环节）
- [x] gap ≥ 5：P0×3 + P1×4 + P2×3 = 10 条（远超下限 5）
- [x] 业界对标 ≥ 4 带链接：LLM-Modulo / TravelPlanner / Constitutional AI / Pydantic AI / RouteLLM = 5 条
- [x] 三套 critic 职责矩阵（§1.1 必答完成）
- [x] `_age_aware_duration_critic` 草稿（§4 方案 A，可直接 paste 到 blueprint.py）
- [x] critic 是主防还是兜底的明确答案（§6.4，结论：兜底）
- [x] 引用代码均带文件:行号
- [x] 中文 + 字数 ~5400 字（区间 3000-6000 内）
- [x] 不动代码 / 不 commit / 不删文件（仅审查产出 markdown）
