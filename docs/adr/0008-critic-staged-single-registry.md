# ADR-0008 · critic 校验层重设计 — 分阶段 hard/soft 单注册表

- **状态**：Accepted（2026-06-30 · grilling 候选 #2 + 业务逻辑/顺序复审）
- **范围**：规划层 critic 子系统（`agent/planning/critic/`）+ 其在 LangGraph（`critic_node`）与 ILS（`plan_hybrid`）两路的接入。承接 ADR-0007。

## 背景（现状诊断，已代码核实）

校验层名义上 3 个 critic，实际 **2 活 + 1 死 + 漏检 + 漂移**：

- `critics_v2.validate_itinerary`（Itinerary 级，**LangGraph 主路径活**）；`ils_score_critic.run_critics`（Itinerary 级，**ILS 路径活**）；`blueprint.run_blueprint_critics`（PlanBlueprint 级，**生产死代码**——仅 test/script 调，`generate_blueprint` 不调）。
- **漏检**：营业时间校验生产**无任何实现**——critics_v2 docstring 称「在 blueprint `_opening_hours_critic` 阶段」，而那层是死的；critics_v2 里没有营业时间 check。
- **漂移**（2 个活 critic 同规则不同语义）：时长（双向 critical vs 超 hard/欠 soft）、年龄上限（`45/75/120/60` vs ILS grounding `90/75`）、社交（相容矩阵 vs 裸成员判定）、餐厅满座（查所有节点 vs 只查首个用餐节点）。
- **严重度→动作断裂**：仅 CRITICAL 进修复闭环，所有 WARNING（距离/饮食/餐时/POOR 社交）trace-only、永不驱动 repair（`check_meal_time` 自称「触发 LLM 自纠」实不可达）。
- **无原则顺序**：三 critic 皆 collect-all，执行顺序仅在 `first-only` 反馈模式下影响「给 LLM 看哪条」，其余装饰性。
- **bug**：`check_meal_time` 遇 `start_time=None` 抛未捕获 `TypeError`（违反「critic 不抛异常」）；`check_capacity` 的 `if≥6/else` 两支字节相同（死分支）。

## prior-art（重逻辑与框架）

- **LLM-Modulo**（Kambhampati, ICML'24 + TravelPlanner 案例 arXiv:2405.20625）：格式门先跑且短路 → **hard critic 决定接受**（回路可靠性=hard critic 可靠性）→ **soft critic 只建议**；反馈 collect-all 拼一条 backprompt；预算 ~10 轮。
- **VAL（PDDL 计划验证器）**：按执行时间线**分阶段**——结构 → 互斥/不变量 → 逐步前提 → 终态；后阶段预设前阶段成立；**定位 + 修复建议**而非只判 pass/fail。
- **Jakarta Bean Validation `@GroupSequence`**：**阶段间短路、阶段内 collect-all**——「fail-fast vs collect-all」之争的成熟折中。
- **JSON Schema**：产出诊断/注解时不可短路（必须遍历）——报告型 critic 应 collect-all 的原理依据。
- **Specification 模式（Evans/Fowler）+ 显式 metacontroller**；Fowler 警告**别用隐式规则引擎**（链式流不可维护）。

## 决策

1. **一个 `validate(plan, ctx) -> list[Violation]`**：单一入口、Check 注册表（Specification）、**LangGraph 与 ILS 两路共用**（干掉 `ils_score_critic.run_critics`）。
2. **分阶段**：Stage 0 结构门（不变量 / 节点完整 / 时间可解析 / 反幻觉 tool-consistency，命中即**短路**）→ Stage 1 **hard** 语义（gate 修复）→ Stage 2 **soft**（只建议）。阶段内 collect-all、阶段间短路；**接受只由 hard 层决定**。
3. **tier 分配**：
   - **hard（gate）**：时长、年龄上限、hop 可达、容量、**饮食**（升级）、**餐时**（升级）、**营业时间**（新增）、餐厅满座、社交 BLOCKING、**节点种类完整性**（吸收 ILS 的 `decide_nodes` 校验，替换孱弱的「count≥3」）。
   - **soft（建议）**：距离、社交 POOR、**预算**（吸收自 ILS）、多样性。
4. **单一 `Violation`**：`code / severity(hard|soft) / 节点定位 / fix-hint`；hard 违规 collect-all 拼一条 backprompt。消灭 CRITICAL/WARNING 与 hard/soft 双词汇。
5. **CriticContext**：pois/restaurants/profile/tool_results 一次性载入传各 Check。
6. **删死 blueprint critic 层**；营业时间**重新安家**到后置 Itinerary 层（用真实 hop 到达时间）。
7. **年龄上限单一真相源**：一张表，critic 与 **ILS grounding 共读**（消除 `45` vs `90` 分歧导致的 thrash）。
8. **永远 collect-all**：删 `first-only` / `reward` 反馈模式 + `compute_reward` + `SEVERITY_WEIGHTS/CODE_WEIGHTS` 权重表（随 reward 一起死）。
9. **可执行违规**（VAL）：每条 hard 违规带节点定位 + 修复 hint。
10. **顺手修**：`check_meal_time` 的 `None`→TypeError guard（O4）；`check_capacity` 死分支（O3）；删 social 遗留子串扫描（O10）；删孤儿常量 `DEMO_FULL_TIME`（O11）。

## 边界（不在本 ADR）

- **修复策略路由**（失败后走 backprompt/ILS/rule/give_up 哪条）归 replan 策略（报告 #3），critic 只**产违规**。
- **ILS 自身的扰动重试**保留（只把它调的 critic 换成统一 `validate`）。
- 前端 `ViolationCode` 副本同步（陈旧 8 vs 13）为跟进项。

## 备选与拒因

- 保留 3 套 critic 各自演化——拒：持续漂移 + 漏检 + 三套 Violation 词汇。
- 通用规则引擎——拒：Fowler「隐式控制流不可维护」；单一 bounded 域用显式 metacontroller 更清晰。
- 全 collect-all 不分阶段——拒：结构破损/幻觉方案上做语义校验是噪声；分阶段短路才省。
- 距离升 hard——拒：与 grounding「候选稀疏时放宽距离」对冲（放宽→reject→放宽→give_up thrash）；留 soft。

## 待办（实现期 · 本 ADR 未动代码）

- TDD 分阶段实现（A 建结构 + 行为保持迁移 → B 行为改变：tier 调整/营业时间/年龄表统一/弃 reward → C 接 ILS + 删死代码）。
- 迁移 ~6 个 critic 测试文件，区分**行为保持**（characterization）vs **故意改变**（dietary/meal_time 升 hard、营业时间新增、reward/first-only 删）。
- 前端 `ViolationCode` 同步（跟进）。

## 红队审查修订（2026-06-30 · 实现前并入，必须照此编码）

红队对着真实代码挑出的问题已判定有效并并入：

- **节点完整性按 `target_kind` 判，非自由文本 `kind`**（B1，最关键）：`decide_nodes` 的 `主活动/用餐` 映射为「需 ≥1 个 `target_kind=poi` / ≥1 个 `target_kind=restaurant` 节点」。**不得**比对 `node.kind`——它是 LLM 自由选的展示标签（prompt 鼓励 夜宵/早茶/自由），否则 Stage 0 短路会误杀合法夜场方案、并压掉其余全部诊断。ILS 今天侥幸无事仅因 rule assembler 写死 kind。
- **SoT 延伸到 grounding**（X-1 / R1+R2）：年龄上限单一表用 critic 分桶（≤3→45 / 4-6→75 / 7-12→120 / ≥75→60，量**实际 `duration_min`**）；**ILS grounding 对 age + 营业时间按同表/同逻辑预过滤，且不在候选稀疏(<3)时放宽这两维**（距离仍可放宽，因其 soft）→ grounding 放行的候选不会再被 critic 因 age/营业时间拒，从根上消除 thrash（兑现 ADR 自身的 SoT 原则）。
- **ILS 接入需 adapter + 路由重映射**（X-2 / B2+B3）：`plan_hybrid` 的重试死耦合在旧 `CriticReport` 形状 + critic 名上。Phase C 加「`validate` 列表 → `plan_hybrid` 所需 report 形状」适配，并把 `_classify_violation` 黑名单路由从「critic 名」改映射到 `ViolationCode`（保住 ILS 既有补救）；**新 hard 码 ILS 无法拉黑补救者 → fail 落 rule 地板**（D2 安全，不过度投入教 ILS 修每种）。
- **保留「该时段无 slot 配置 = hard」语义**（R3）：并入统一餐厅可用性检查——旧 `check_demo_restaurant_full` 只判 `available==False`、会漏掉「无 slot 配置」，迁移时**不得丢**此 hard 保证。
- **相位修正**（G4）：**Phase A = 纯结构迁移（仍 collect-all、不短路、行为保持，CRITICAL→hard/WARNING→soft 1:1）**；**分阶段短路移到 Phase B** 作为有意行为改变。否则 A 的「行为保持 + 全套绿」站不住。
- **时间检查拆位**（G2）：时间**可解析** → Stage 0 结构门；hop/buffer **对齐**（`TIMELINE_INCONSISTENT`）→ Stage 1 hard。
- **营业时间检查移植 + None-guard**（G3）：从死的 blueprint `_opening_hours_critic` **移植**逻辑（非单纯删）到后置 Itinerary 层，加 None-guard（防重蹈 O4 的 TypeError）；知悉其单区间正则仅覆盖当前 mock 数据。
- **CriticContext 两数据源分开**（G5）：全量 mock（距离/饮食/容量）vs `tool_results` 快照（仅反幻觉）；ILS 路径无快照 → 反幻觉在该路径为 no-op（写明）。
- **小项**：删 roster 里的「多样性」（无实现/无数据源，赘文，G1）；budget 检查改读 `CriticContext.profile`（对齐 user_id）；删 reward 机制连带删 `tests/test_critic_feedback_mode.py`（全套基线相应下调）；blueprint 死层删除时其营业时间逻辑须先移植。

---
**落地状态**：🔁 部分落地（决策 2026-06-30 · 红队修订已并入）。Phase A（结构迁移，2535d94）/ B-1（hard·soft 严重度 + 分阶段短路，f7f7ad2）/ B-2a（dietary·meal_time 升 hard + 节点完整性改 target_kind + temporal 拆分，7977097）/ B-2b（营业时间检查 + age_caps 单表 + O3/O10/O11 + 消词漂移，963b39a）已落地（936 passed）。**Phase C（接 ILS + grounding 对齐 + 删死 blueprint 层）转由 [ADR-0009](0009-ils-real-rung-and-critic-repair-loop.md) 承接并深化**。
