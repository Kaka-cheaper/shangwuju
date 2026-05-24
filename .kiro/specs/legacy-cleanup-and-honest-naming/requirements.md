# Requirements Document

## Glossary

- **legacy/**：spec B `agent-directory-restructure` 落地的子目录，含 7 个 .py 文件 + 1 prompts 子目录。当时定为「冻结模块（仅 fallback / safety-net 用，不改业务）」。
- **冻结纪律**：AGENTS.md §3.3.1 锁定的规则——「在 `agent/legacy/` 加新功能、新 Agent、新输出类型、新 critic 规则；只允许 bug fix + schema 适配」。
- **真死代码（dead code）**：全仓库 0 调用 / 仅自身测试引用、没有生产路径消费的模块。本 spec 鉴定 3 个：`llm_first_planner.py`（0 引用）/ `llm_planner.py`（仅 test_llm_planner 引用，且测试是测 plan_itinerary_with_mode 整体行为）/ `executor.py`（功能与 graph/nodes/execute_finalize 等价）。
- **活的 fallback**：有真实生产消费方但定位为兜底路径。本 spec 鉴定 2 个：`ils_planner.py`（graph/nodes/replan.py 的第 3 次 ILS 兜底）+ `segment_decider.py`（ils_planner 的依赖）。
- **主路径活代码**：被生产主流程直接调用，不是兜底而是真链路一等公民。本 spec 鉴定 1 个：`planner_rule.py`（main.py:1740 真 LLM 链路 / collab/room.py / 5 个测试主功能）。
- **ILS 路径专用 critic**：与 `ils_planner` 配套的 critic 实现（`ils_score_critic.py`），与 `critics_v2` 是不同维度——前者是 ILS 候选打分用、后者是 itinerary 全局校验用。
- **诚实命名**：目录名应当反映模块的真实定位（主路径 / fallback / 死代码），而不是用一个误导性大类（legacy/）打包。

## Introduction

spec B `agent-directory-restructure` 落地时把 8 个 .py 文件全甩进 `legacy/`，没盘点真实引用关系。**事实独立审查**（详见 spec D research/legacy_usage_audit.md）发现这个分类**严重名实不符**：

```text
| 类别                      | 模块                                          | 当前位置  | 真实定位             |
|--------------------------|----------------------------------------------|---------|--------------------|
| 主路径活代码（不是 legacy）| planner_rule.py                              | legacy/  | main.py 真链路核心实现|
| 活的 fallback             | ils_planner.py + segment_decider.py          | legacy/  | graph replan 第 3 次兜底|
| ILS 路径专用 critic       | ils_score_critic.py                          | legacy/  | ILS 候选打分（与 critics_v2 维度不同）|
| 真死代码（应删）          | llm_first_planner.py / llm_planner.py / executor.py | legacy/  | 0 引用 / 仅测试 / 与 execute_finalize 等价|
```

核心问题（user 在审查中独立指出）：

1. **`planner_rule.py` 是主路径核心实现，不是 legacy**——main.py:1740 直接调它跑真 LLM 链路；spec B 把它甩进 legacy 严重误导
2. **冻结纪律自相矛盾**——spec C R3+R4 必须改 `ils_planner.py` 加 grounding-first + LLM 语义打分，但冻结纪律说"不许加新功能"，导致需要单独开口子
3. **真死代码混在里面**——`llm_first_planner` / `llm_planner` / `executor` 占着「legacy」名号让真正的活 fallback 模糊

**本 spec 的工作**：

1. 删除 3 个真死代码（含一个测试文件 `test_llm_planner.py` 改造为测 `plan_itinerary_with_mode("llm")` 主路径行为，不删测试）
2. 把 4 个非死代码模块（planner_rule / ils_planner / segment_decider / ils_score_critic）解冻并迁回 `planning/` 下职责清晰的子目录
3. 删除 `legacy/` 整个目录 + `verify_legacy_frozen.py` 守门脚本
4. 同步 `AGENTS.md §3.3.1` 编排冻结纪律段——目录树更新 + 删除「legacy/ 不能加新功能」条款（不再有 legacy/）
5. 同步 `pitfalls.md` 加 [P0] 防再犯条款：未来不要按"我以为是冻结的"再造一个 legacy/，每次重组前必须 grep 真实引用

**前置硬约束**：本 spec 必须在 spec C 启动前完成。理由：spec C R3+R4 的改动锚点（`ils_planner.py`）会因本 spec 而搬位置；如果 spec C 先做，spec D 再做要返工 2 次 import 路径迁移。

**与 spec B 的关系**：本 spec 是对 spec B 的**修正性重构**，不是推翻。spec B 的 5 子目录骨架（core/ + intent/ + planning/ + runtime/ + graph/）继续保留；本 spec 仅扩展 `planning/` 下的子目录 + 删除 `legacy/`。

**Hackathon 时间盒**：~2-3h，1 人天可以做完。分 5 wave 推进。

## Requirements

### Requirement 1: 删除 3 个真死代码模块

**User Story:** As 项目维护者, I want 把 0 引用 / 等价路径已存在的真死代码删掉, so that legacy/ 不再混杂死代码 + 活 fallback + 主路径三种东西。

#### Acceptance Criteria

1. WHEN 本 spec 完成 THEN `backend/agent/legacy/llm_first_planner.py` SHALL 不存在；删除前 `grep -r "llm_first_planner" --include="*.py"` 必须 0 命中（已确认）。
2. WHEN 本 spec 完成 THEN `backend/agent/legacy/llm_planner.py` SHALL 不存在；唯一引用方 `backend/tests/test_llm_planner.py` SHALL 改造为只测 `plan_itinerary_with_mode("llm")` 整体行为（删除 `from agent.legacy.llm_planner import plan_itinerary_llm` 行；4 个测试用例全部走 `plan_itinerary_with_mode` 主路径）。
3. WHEN 本 spec 完成 THEN `backend/agent/legacy/executor.py` SHALL 不存在；唯一引用方 `tests/test_agent_flow.py` 与 `tests/test_8_scenarios.py` SHALL 改造为调 `from agent.graph.nodes.execute_finalize import execute_finalize_node`，传入构造好的 state dict 调用（验证 reserve + share + narration 等价行为）。
4. WHEN 本 spec 完成 THEN `backend/agent/legacy/__pycache__/` 也应一并清理。
5. WHEN 全套 pytest 跑 THEN 全部测试 SHALL 0 红灯（与本 spec 启动前基线一致），含改造后的 `test_llm_planner.py` / `test_agent_flow.py` / `test_8_scenarios.py`。

---

### Requirement 2: 解冻 4 个非死代码模块到 planning/ 子目录

**User Story:** As 后续接手项目的开发者 / AI Agent, I want 主路径活代码与活的 fallback 都放在 `planning/` 下命名诚实的子目录, so that 看到目录树就能识别"这是规划主路径，不是被冻结的死代码"。

#### Acceptance Criteria

1. WHEN 本 spec 完成 THEN 新建子目录 `backend/agent/planning/planners/` SHALL 含三个文件：
   - `rule_planner.py`（重命名自 `legacy/planner_rule.py`）—— main.py 真 LLM 链路 / collab fallback 主入口
   - `ils_planner.py`（搬自 `legacy/ils_planner.py`）—— graph replan 第 3 次 ILS 兜底
   - `segment_decider.py`（搬自 `legacy/segment_decider.py`）—— ils_planner 依赖
   - `__init__.py`（空文件）
2. WHEN 本 spec 完成 THEN `backend/agent/planning/critic/` SHALL 加 `ils_score_critic.py`（搬自 `legacy/ils_score_critic.py`），明确文件 docstring 为「ILS 候选打分专用 critic（CriticReport / run_critics）；与 critics_v2 维度不同——前者是 ILS 路径产候选时用，后者是 itinerary 全局校验用」。
3. WHEN 解冻完成 THEN 4 个文件**顶部 `# FROZEN` 注释 SHALL 全部删除**；docstring 改写为正确的职责描述（`rule_planner.py`：主路径活代码 / `ils_planner.py`：graph replan 第 3 次兜底 / `segment_decider.py`：ils_planner 依赖 / `ils_score_critic.py`：ILS 路径专用 critic）。
4. WHEN 解冻完成 THEN smartRelocate 工具 SHALL 自动更新所有 `from agent.legacy.<X>` 引用为新路径；spec D 完成后全仓库 grep `from agent\.legacy\.` SHALL 0 命中（与 R3.1 协同）。
5. WHEN `rule_planner.py` 重命名 THEN 函数 / 类名保持不变（`plan_itinerary` / `plan_itinerary_with_mode` 等仍叫原名，只改文件名）；smartRelocate 自动处理 import 改名。

---

### Requirement 3: 删除 legacy/ 整个目录 + verify_legacy_frozen 脚本

**User Story:** As 项目目录树阅读者, I want `agent/legacy/` 整个目录从仓库消失, so that 再没有"legacy"这个误导性概念存在。

#### Acceptance Criteria

1. WHEN 本 spec 完成 THEN `backend/agent/legacy/` 整个目录（含 `prompts/` 子目录与 `prompts/llm_planner_prompt.py`）SHALL 不存在；如果 `llm_planner_prompt.py` 在 `llm_planner.py` 删除后变成孤儿，一并删除。
2. WHEN 本 spec 完成 THEN `backend/scripts/verify_legacy_frozen.py` SHALL 删除（守的是已不存在的目录）；同步删除 CI / 文档中对它的引用（如有）。
3. WHEN 本 spec 完成 THEN `backend/agent/__init__.py` 如有 `from .legacy import ...` 类的 re-export SHALL 全部清理。
4. WHEN 全仓库 grep THEN 不再出现以下旧路径（与 R2.4 协同验证）：
   - `from agent.legacy.planner_rule import` → 应改为 `from agent.planning.planners.rule_planner import`
   - `from agent.legacy.ils_planner import` → `from agent.planning.planners.ils_planner import`
   - `from agent.legacy.segment_decider import` → `from agent.planning.planners.segment_decider import`
   - `from agent.legacy.ils_score_critic import` → `from agent.planning.critic.ils_score_critic import`
   - `from agent.legacy.llm_first_planner import` / `llm_planner import` / `executor import` → 全部不存在（模块已删）
5. WHEN `tests/test_import_paths.py` 升级 THEN `test_legacy_imports` SHALL 改名为 `test_planning_planners_imports`，断言新路径可 import；`test_old_paths_no_longer_exist` SHALL 加新一批旧路径（`agent.legacy.*` 全部）的 ImportError 反向断言。

---

### Requirement 4: AGENTS.md §3.3.1 编排冻结纪律重写

**User Story:** As 后续 AI Agent / 开发者, I want AGENTS.md §3.3.1 的目录树与冻结纪律段同步本次重构, so that 不再被「legacy/ 不能加新功能」这条已无意义的规则困扰。

#### Acceptance Criteria

1. WHEN 本 spec 完成 THEN `AGENTS.md §3.3.1` 的目录树代码块 SHALL 替换为新结构：
   - 删除 `legacy/` 子目录段
   - `planning/` 段加 `planners/`（rule_planner / ils_planner / segment_decider）
   - `planning/critic/` 段加 `ils_score_critic.py`
2. WHEN 本 spec 完成 THEN `AGENTS.md §3.3.1` 的 MUST / MUST NOT 段 SHALL 删除以下条款：
   - MUST：「涉及 prompt 的改动按归属：……冻结 prompt 在 `agent/legacy/prompts/`」（legacy/prompts 已删）
   - MUST NOT：「在 `agent/legacy/` 加新功能、新 Agent、新输出类型、新 critic 规则；只允许 bug fix + schema 适配」（legacy/ 已不存在）
   - MUST NOT：「删除 `agent/legacy/` 模块的 `# FROZEN` 标记（受 `verify_legacy_frozen.py` 守护）」（验证脚本已删）
   - MUST NOT：「删除冻结路径里的导出符号——多个 fallback 链路依赖它们」（不再适用）
3. WHEN 本 spec 完成 THEN `AGENTS.md §3.3.1` 加一句新条款：「`planning/planners/` 下的模块按真实定位区分——`rule_planner.py` 是主路径，`ils_planner.py` 是 graph replan 第 3 次 ILS 兜底，`segment_decider.py` 是 ils_planner 依赖。改动这些文件不需要走「冻结口子」流程，但要遵守 graph/build.py 拓扑不动的纪律」。
4. WHEN 本 spec 完成 THEN `AGENTS.md §3.3.1` 的 MUST NOT 段保留「不动 graph/build.py 拓扑」条款（这条与 spec B 结论一致，不本 spec 改）。

---

### Requirement 5: spec C 改动锚点同步 + 防再犯 + 文档同步

**User Story:** As spec C 实施者, I want 本 spec 完成后 spec C requirements / design / tasks 三件套的所有 ils_planner 改动锚点同步到新路径, so that spec C task 4/5 启动时不会撞「legacy 已不存在」的报错。

#### Acceptance Criteria

1. WHEN 本 spec 完成 THEN `.kiro/specs/algorithm-redesign/requirements.md` SHALL grep 替换：
   - `backend/agent/legacy/ils_planner.py` → `backend/agent/planning/planners/ils_planner.py`
   - `agent/legacy/ils_planner.py` → `agent/planning/planners/ils_planner.py`（design.md 与 tasks.md 同步替换）
   - 删除 R3 / R4 / R5 / Out of Scope 中所有「FROZEN 模块」「legacy 路径」字样的描述（这些约束已不存在）
2. WHEN 本 spec 完成 THEN `.kiro/specs/algorithm-redesign/design.md` 的 §Components 2 段 SHALL 重写——把「FROZEN 模块允许加新过滤函数」措辞删除，直接说「在 `planning/planners/ils_planner.py` 加 _grounding_filter_poi / _grounding_filter_restaurant」。
3. WHEN 本 spec 完成 THEN `.kiro/specs/algorithm-redesign/tasks.md` task 4 + task 5 + task 8 SHALL 同步修改 ils_planner 路径锚点。
4. WHEN 本 spec 完成 THEN `docs/03-implementation/pitfalls.md` SHALL 追加 1 条 [P0] 防再犯条款：「目录重组前必须 grep 真实引用——spec B 没盘点引用就把 8 个文件全甩进 legacy/，导致 4 个非死代码（含 main.py 真链路核心 planner_rule.py）被错误冻结。防再犯：任何重组 spec 启动前先用 grep 列每个待迁移模块的引用方，明确「主路径 / 活 fallback / 死代码」三类，每类去不同子目录」。
5. WHEN 本 spec 完成 THEN `docs/00-overview/progress.md` 决策记录段 SHALL 追加 `D-LEGACY-CLEANUP [日期]：删除误导的 legacy/ 目录——3 个真死代码删除，4 个非死代码迁回 planning/planners/ + planning/critic/。spec B 的 5 子目录骨架保留；冻结纪律改为按 graph/build.py 拓扑稳定（而非按文件位置）`。
6. WHEN 本 spec 完成 THEN `problem.md` SHALL 追加本次记录（按全局 problem.md 格式：问题 / 方案 / 修改文件 / 应当达成的效果 / 用户反馈如有）。

---

## Out of Scope（明确不做）

```text
| 不做的事                                       | 理由                                          |
|-----------------------------------------------|----------------------------------------------|
| 重组 graph/ 子目录                              | spec B 已锁，本 spec 范围严格限定在 planning/ + legacy/ 删除 |
| 重组 runtime/ 子目录                            | 同上                                          |
| 重组 intent/ 子目录                             | 同上                                          |
| 改 graph/build.py 拓扑                          | spec B + AGENTS.md §3.3.1 锁定，不本 spec 动 |
| 改业务逻辑 / prompt / schema                    | 本 spec 仅动文件位置 + import 路径 + 删 3 个死代码|
| 把 ils_score_critic 与 critics_v2 合并            | 维度不同（ILS 候选打分 vs itinerary 全局校验），不做|
| 改前端目录                                      | 本 spec 仅后端 agent 重组                      |
| spec C 实际算法改动                             | 留 spec C 主体；本 spec 仅同步 spec C 三件套的路径锚点 |
| 改 backend/api/ / backend/main.py 业务行为      | 本 spec 仅改 import 路径，不改业务行为          |
| 加 meta_critic_node                            | 留 spec D 之后评估                              |
| 删 ils_score_critic（双 critic 系统简化）       | 不删——主路径 critic_v2 + ILS 路径 critic 是两个维度|
```

---

## 前置条件 / 时序硬约束

**本 spec 必须在 spec C `algorithm-redesign` 实施启动前完成**。

**理由**：

- spec C R3+R4 改动锚点是 `ils_planner.py`——如果 spec C 先做，spec D 再做要返工 2 次 import 路径迁移
- spec C 当前三件套写的锚点是 `agent/legacy/ils_planner.py`，spec D 完成后要同步替换为 `agent/planning/planners/ils_planner.py`（本 spec R5）

**启动检查清单**（task 1 前必须满足）：

- [ ] spec A `planning-quality-deep-review` 全部 8 task 完成（git tag `v-spec-a-done` 已打）
- [ ] spec B `agent-directory-restructure` 全部 8 task 完成
- [ ] spec C `algorithm-redesign` 三件套已落库（commit `9224284`）但 task 1 未启动
- [ ] 用户人工确认"可以启动 spec D"
