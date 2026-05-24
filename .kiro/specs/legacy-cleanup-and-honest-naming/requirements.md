# Requirements Document

## Glossary

- **legacy/**：spec B `agent-directory-restructure` 落地的子目录，含 7 个 .py 文件 + 1 prompts 子目录。当时定为「冻结模块（仅 fallback / safety-net 用，不改业务）」。
- **冻结纪律**：AGENTS.md §3.3.1 锁定的规则——「在 `agent/legacy/` 加新功能、新 Agent、新输出类型、新 critic 规则；只允许 bug fix + schema 适配」。
- **PLANNER_LLM_STRATEGY 三档路由**：`backend/agent/legacy/planner_rule.py:plan_itinerary_with_mode` 在 `mode="llm"` 时按 env `PLANNER_LLM_STRATEGY` 分发——`llm_first`（默认）/ `hybrid` / `function_calling` 三档分别走不同 planner。**这意味着 legacy/ 下 4 个 planner 都是生产路径**。
- **executor.execute_plan vs graph/execute_finalize_node 的差异**：起草版 spec D 误判两者等价。实测发现 executor 用 `_extract_reserved_time(restaurant_node.note)` 解析「已为你预留 18:00」类预留时段；execute_finalize_node 直接用 `restaurant_node.start_time`（含通勤后的实际抵达时刻，如 18:02）。在 mock 严格匹配 HH:MM 整 30 分时段的环境下，两者**行为不等价**——executor 是活代码，不是死代码。
- **真死代码（dead code）**：全仓库 0 调用 / 仅自身测试引用 / 等价路径已存在的模块。本 spec 经**完整引用审计 + 实测行为等价性验证**确认：legacy/ 下 **0 个真死代码**。起草时误判 3 个，user 第一次审查指出 2 个误判，task 2 实测又发现 executor 也是活代码——3 → 1 → 0 三次诚实修正。
- **主路径活代码**：被生产主流程直接调用，不是兜底。本 spec 鉴定 1 个：`planner_rule.py`（main.py:1740 真链路 / collab/room.py / replan / 5 测试 + 三档子策略分发器）。
- **PLANNER_LLM_STRATEGY 子策略活代码**：被 plan_itinerary_with_mode 在三档 strategy 下调用的具体实现。本 spec 鉴定 3 个：`llm_first_planner.py`（默认路径）/ `ils_planner.py`（hybrid 子策略 + graph replan 兜底）/ `llm_planner.py`（function_calling 子策略）。
- **ILS 路径专用 critic**：与 `ils_planner` 配套的 critic 实现（`ils_score_critic.py`），与 `critics_v2` 维度不同——前者是 ILS 候选打分用、后者是 itinerary 全局校验用。
- **执行类活代码**：用户确认后下发执行类 Tool 的实现。本 spec 鉴定 1 个：`executor.py`（与 graph/execute_finalize 不等价，被 test_agent_flow / test_8_scenarios 验证 reserve + share 行为）。
- **诚实命名**：目录名应反映模块的真实定位（主路径 / 子策略 / 执行类 / 死代码），而不是用一个误导性大类（legacy/）打包。

## Introduction

spec B `agent-directory-restructure` 落地时把 8 个 .py 文件全甩进 `legacy/`，没盘点真实引用关系（含相对引用 + 内部链式调用 + 实测行为等价性）。**事实独立审查**（grep 全仓库 absolute + 相对引用 + 内部链式 import + task 2 实测）发现这个分类**严重名实不符**：

```text
| 类别                      | 模块                                          | 真实定位                                            |
|--------------------------|----------------------------------------------|--------------------------------------------------|
| 主路径活代码（不是 legacy）| planner_rule.py                              | main.py 真链路核心 + plan_itinerary_with_mode 三档分发器|
| PLANNER_LLM_STRATEGY 子策略 1 | llm_first_planner.py                     | env 默认值=llm_first 时的核心生产路径                 |
| PLANNER_LLM_STRATEGY 子策略 2 | ils_planner.py                           | hybrid 子策略 + graph replan 第 3 次 ILS 兜底         |
| PLANNER_LLM_STRATEGY 子策略 3 | llm_planner.py                           | function_calling 子策略（A/B 候选）                  |
| ils_planner 依赖          | segment_decider.py                          | ils_planner / replan / planner_rule 内部调          |
| llm_planner 依赖          | prompts/llm_planner_prompt.py                | llm_planner.py 内部调（不是孤儿）                    |
| ILS 路径专用 critic       | ils_score_critic.py                          | ils_planner 内部调 + verify_planning + 测试         |
| 执行类活代码（不是死代码） | executor.py                                  | 用 _extract_reserved_time(note) 解析预留时段；与 graph/execute_finalize 不等价；被 2 个测试 + agent/__init__.py 引用 |
```

核心问题（user 在审查中独立指出 + 编排者起草 spec D 时**犯了三层错误**——只 grep `from agent.legacy.X` 没看相对引用 + 假设 executor 与 graph 等价没实测 + 没考虑 mock 时段严格匹配）：

1. **`planner_rule.py` 是主路径核心 + 三档子策略分发器，不是 legacy**
2. **冻结纪律自相矛盾**——spec C R3+R4 必须改 `ils_planner.py` 加 grounding-first + LLM 语义打分
3. **「真死代码」实际有 0 个**——3 个 planner（含 llm_first 默认 + function_calling）都是 PLANNER_LLM_STRATEGY 子策略生产路径；executor 也活（与 graph/execute_finalize 行为不等价）；spec D v1 误判 3 个 / v2 误判 1 个 / v3 修正为 0
4. **审计教训三层**：
   - 第一层（v1 → v2）：grep 必须含相对引用 + 内部链式调用，不能只看 absolute import
   - 第二层（v2 → v3）：「等价路径已存在」不能只看函数名相似 / 文档说明，**必须实测**
   - 第三层（永久教训）：未来重组前必须独立 grep + 实测两个步骤都做完，再决定能不能删

**本 spec 的工作**（v3 修正版）：

1. **不删任何文件**（legacy/ 下 0 个真死代码）
2. 把 7 个非死代码模块（`planner_rule` / `ils_planner` / `segment_decider` / `ils_score_critic` / `llm_planner` / `llm_first_planner` / `executor`）+ 1 个 prompt 文件（`llm_planner_prompt.py`）解冻并迁回 `planning/` 下职责清晰的子目录
3. 删除 `legacy/` 整个目录（仅剩空 `__init__.py` + `__pycache__/`）+ `verify_legacy_frozen.py` 守门脚本
4. 同步 `AGENTS.md §3.3.1` 编排冻结纪律段——目录树更新 + 删除「legacy/ 不能加新功能」条款
5. 同步 `pitfalls.md` 加 [P0] 防再犯条款：未来不要按"我以为是冻结的"再造一个 legacy/，每次重组前必须 **grep 完整引用关系（含相对引用 + 内部链式）+ 实测行为等价性** 两步都做完

**前置硬约束**：本 spec 必须在 spec C 启动前完成。理由：spec C R3+R4 改动锚点（`ils_planner.py`）会因本 spec 而搬位置；如果 spec C 先做，spec D 再做要返工 2 次 import 路径迁移。

**与 spec B 的关系**：本 spec 是对 spec B 的**修正性重构**，不是推翻。spec B 的 5 子目录骨架（core/ + intent/ + planning/ + runtime/ + graph/）继续保留；本 spec 仅扩展 `planning/` 下的子目录 + 删除 `legacy/`。

**Hackathon 时间盒**：~3.0h（v3 修正版，比 v2 少 0.5h——不再有 1 个 deletion + 测试改造，仅多 1 处 smartRelocate）。分 5 wave 推进。

## Requirements

### Requirement 1: 解冻 7 个非死代码模块到 planning/ 子目录

**User Story:** As 后续接手项目的开发者 / AI Agent, I want 主路径活代码 + PLANNER_LLM_STRATEGY 三档子策略 + ILS 专用 critic + 执行类活代码都放在 `planning/` 下命名诚实的子目录, so that 看到目录树就能识别"这是规划主路径 + 三档子策略 + 执行 + ILS critic"，没有任何"legacy"误导分类。

#### Acceptance Criteria

1. WHEN 本 spec 完成 THEN 新建子目录 `backend/agent/planning/planners/` SHALL 含 5 个文件 + 1 个 prompts 子目录：
   - `rule_planner.py`（重命名自 `legacy/planner_rule.py`）—— main.py 真 LLM 链路 / collab fallback / 三档子策略分发器
   - `ils_planner.py`（搬自 `legacy/`）—— hybrid 子策略 + graph replan 第 3 次 ILS 兜底
   - `llm_first_planner.py`（搬自 `legacy/`）—— **PLANNER_LLM_STRATEGY=llm_first（默认）的核心生产路径**
   - `llm_planner.py`（搬自 `legacy/`）—— PLANNER_LLM_STRATEGY=function_calling 子策略（A/B）
   - `segment_decider.py`（搬自 `legacy/`）—— ils_planner 依赖（被 planner_rule / replan / 测试调）
   - `prompts/llm_planner_prompt.py`（搬自 `legacy/prompts/`）—— llm_planner 的 system prompt
   - `prompts/__init__.py`（空文件）+ `__init__.py`（空文件）
2. WHEN 本 spec 完成 THEN `backend/agent/planning/critic/` SHALL 加 `ils_score_critic.py`（搬自 `legacy/ils_score_critic.py`），明确文件 docstring 为「ILS 候选打分专用 critic（CriticReport / run_critics）；与 critics_v2 维度不同——前者是 ILS 路径产候选时用，后者是 itinerary 全局校验用」。
3. WHEN 本 spec 完成 THEN 新建子目录 `backend/agent/planning/execution/` SHALL 含：
   - `executor.py`（搬自 `legacy/executor.py`）—— 用户确认后下发执行类 Tool（reserve_restaurant + buy_ticket + generate_share_message），含 `_extract_reserved_time(note)` 解析预留时段的关键差异化逻辑（与 graph/execute_finalize 不等价）
   - `__init__.py`（空文件）
   - **建 execution/ 子目录的理由**：与 `planners/`（规划阶段）+ `critic/`（验证阶段）三足鼎立，符合「Plan → Critic → Execute」三段式语义；executor 不归 planners/ 因为它不是 planner 是 executor，归 critic/ 因为它不是 critic 是 executor。
4. WHEN 解冻完成 THEN 8 个文件**顶部 `# FROZEN` 注释 SHALL 全部删除**；docstring 改写为正确职责描述（参考 design.md §Components 4 提供的 8 段设计稿，含「主路径活代码」/「PLANNER_LLM_STRATEGY 子策略 X」/「ils_planner 依赖」/「ILS 候选打分专用 critic」/「执行类活代码」/「LLM Function Calling system prompt」六类标签）。
5. WHEN 解冻完成 THEN smartRelocate 工具 SHALL 自动更新所有 absolute import + 相对 import 引用为新路径；spec D 完成后全仓库 grep `from agent\.legacy\.` SHALL 0 命中，grep `from \.legacy\.` SHALL 0 命中。
6. WHEN `rule_planner.py` 重命名（仅此 1 处改名）THEN 函数 / 类名保持不变（`plan_itinerary` / `plan_itinerary_with_mode` 等仍叫原名，只改文件名）；smartRelocate 自动处理 import 改名。
7. WHEN 内部相对引用迁移 THEN 以下 4 处内部 `from .X import` 必须正确更新（smartRelocate 应自动处理，task 5 grep 复核）：
   - `legacy/planner_rule.py:1262 from .llm_planner import plan_itinerary_llm` → 在新 planners/ 内仍是 `from .llm_planner import`（同包）
   - `legacy/planner_rule.py:1290 from .ils_planner import plan_hybrid` → 同上
   - `legacy/planner_rule.py:1406 from .llm_first_planner import plan_llm_first` → 同上
   - `legacy/llm_planner.py:54 from .planner_rule import (...)` → **`from .rule_planner import (...)`**（**注意 rule_planner 是改名**）
   - `legacy/llm_planner.py:62 from .prompts.llm_planner_prompt import` → 同上（prompts/ 子目录跟随迁移）
   - `legacy/ils_planner.py:80 from .ils_score_critic import` → **`from ..critic.ils_score_critic import`**（**ils_score_critic 跨 critic/ 子目录**）
   - `legacy/ils_planner.py:634 from .planner_rule import _resolve_time_window` → **`from .rule_planner import _resolve_time_window`**（改名导致）
   - `legacy/executor.py` → 在新 `execution/` 子目录下；其内部 `from ..core.trace import Tracer`（跨包到 core/）由 smartRelocate 跨子目录处理

---

### Requirement 2: 删除 legacy/ 整个目录 + verify_legacy_frozen 脚本

**User Story:** As 项目目录树阅读者, I want `agent/legacy/` 整个目录从仓库消失, so that 再没有"legacy"这个误导性概念存在。

#### Acceptance Criteria

1. WHEN 本 spec 完成 THEN `backend/agent/legacy/` 整个目录（含 `prompts/` 子目录与 `__init__.py`、`__pycache__/`）SHALL 不存在。
2. WHEN 本 spec 完成 THEN `backend/scripts/verify_legacy_frozen.py` SHALL 删除（守的是已不存在的目录）；同步删除 CI / 文档中对它的引用（如有）。
3. WHEN 本 spec 完成 THEN `backend/agent/__init__.py` 中所有 `from .legacy.X import` 类的 re-export SHALL 全部更新到新路径（共 3 处：`from .legacy.planner_rule` → `from .planning.planners.rule_planner`；`from .legacy.llm_planner` → `from .planning.planners.llm_planner`；`from .legacy.executor` → `from .planning.execution.executor`），不删任何 re-export 行（executor 是活代码不是死代码）。
4. WHEN 全仓库 grep THEN 不再出现以下旧路径：
   - `from agent.legacy.X import` → 全部不存在
   - `from .legacy.X import` → 全部不存在
   - `from .X import`（在新 planners/ 目录内的相对引用）→ 与同目录其他模块兼容
5. WHEN `tests/test_import_paths.py` 升级 THEN `test_legacy_imports` SHALL 改名为 `test_planning_planners_imports`，断言新路径可 import（5 个 planners + 1 个 critic + 1 个 execution + 1 个 prompt 共 8 项）；`test_old_paths_no_longer_exist` SHALL 加新一批旧路径（`agent.legacy.*` 全部 7 个）的 ImportError 反向断言。

---

### Requirement 3: AGENTS.md §3.3.1 编排冻结纪律重写

**User Story:** As 后续 AI Agent / 开发者, I want AGENTS.md §3.3.1 的目录树与冻结纪律段同步本次重构, so that 不再被「legacy/ 不能加新功能」这条已无意义的规则困扰。

#### Acceptance Criteria

1. WHEN 本 spec 完成 THEN `AGENTS.md §3.3.1` 的目录树代码块 SHALL 替换为新结构：
   - 删除 `legacy/` 子目录段
   - `planning/` 段加 `planners/`（含 5 .py + prompts/llm_planner_prompt.py）+ `execution/`（executor.py）+ `critic/ils_score_critic.py`
2. WHEN 本 spec 完成 THEN `AGENTS.md §3.3.1` 的 MUST / MUST NOT 段 SHALL 删除以下条款：
   - MUST：「涉及 prompt 的改动按归属：……冻结 prompt 在 `agent/legacy/prompts/`」（legacy/prompts 已删）
   - MUST NOT：「在 `agent/legacy/` 加新功能、新 Agent、新输出类型、新 critic 规则；只允许 bug fix + schema 适配」（legacy/ 已不存在）
   - MUST NOT：「删除 `agent/legacy/` 模块的 `# FROZEN` 标记（受 `verify_legacy_frozen.py` 守护）」（验证脚本已删）
   - MUST NOT：「删除冻结路径里的导出符号——多个 fallback 链路依赖它们」（不再适用）
3. WHEN 本 spec 完成 THEN `AGENTS.md §3.3.1` 加一句新条款：「`planning/planners/` + `planning/execution/` 下的模块按真实定位区分——`rule_planner.py` 是主路径分发器，`ils_planner.py` 是 PLANNER_LLM_STRATEGY=hybrid + graph replan 兜底，`llm_first_planner.py` 是 PLANNER_LLM_STRATEGY=llm_first（默认）的核心，`llm_planner.py` 是 PLANNER_LLM_STRATEGY=function_calling 子策略，`segment_decider.py` 是 ils_planner 依赖，`execution/executor.py` 是用户确认后的执行类 Tool 派发（与 graph/execute_finalize 不等价——前者解析 note 中的预留时段，后者用 start_time）。改动这些文件不需要走「冻结口子」流程，但要遵守 graph/build.py 拓扑不动的纪律」。
4. WHEN 本 spec 完成 THEN `AGENTS.md §3.3.1` 的 MUST NOT 段保留「不动 graph/build.py 拓扑」条款（这条与 spec B 结论一致，不本 spec 改）。

---

### Requirement 4: spec C 改动锚点同步 + 防再犯 + 文档同步

**User Story:** As spec C 实施者, I want 本 spec 完成后 spec C requirements / design / tasks 三件套的所有 ils_planner 改动锚点同步到新路径, so that spec C task 4/5 启动时不会撞「legacy 已不存在」的报错。

#### Acceptance Criteria

1. WHEN 本 spec 完成 THEN `.kiro/specs/algorithm-redesign/requirements.md` SHALL grep 替换：
   - `backend/agent/legacy/ils_planner.py` → `backend/agent/planning/planners/ils_planner.py`
   - `agent/legacy/ils_planner.py` → `agent/planning/planners/ils_planner.py`（design.md 与 tasks.md 同步替换）
   - 删除 R3 / R4 / R5 / Out of Scope 中所有「FROZEN 模块」「legacy 路径」字样的描述（这些约束已不存在）
2. WHEN 本 spec 完成 THEN `.kiro/specs/algorithm-redesign/design.md` 的 §Components 2 段 SHALL 重写——把「FROZEN 模块允许加新过滤函数」措辞删除，直接说「在 `planning/planners/ils_planner.py` 加 _grounding_filter_poi / _grounding_filter_restaurant」。
3. WHEN 本 spec 完成 THEN `.kiro/specs/algorithm-redesign/tasks.md` task 4 + task 5 + task 8 SHALL 同步修改 ils_planner 路径锚点。
4. WHEN 本 spec 完成 THEN `docs/03-implementation/pitfalls.md` SHALL 追加 1 条 [P0] 防再犯条款：「目录重组前必须做**两步独立审计**：(1) grep 完整引用关系（含 absolute import + 相对引用 `from .X` / `from ..X` + 内部链式调用如函数体内 `from .X import Y`）；(2) **实测行为等价性**——任何「等价路径已存在」的删除假设都必须 pytest 实测，不能只看函数名相似 / docstring 说明。spec B 起草时漏掉相对引用 + 内部链式（4 个生产路径误归 legacy）；spec D 起草 v1 漏掉相对引用（误判 3 个死代码）；spec D 起草 v2 漏掉实测行为等价性（误判 1 个死代码——executor 用 `_extract_reserved_time(note)` 解析预留时段、graph/execute_finalize 直接用 start_time，mock 时段严格匹配下不等价）；spec D v3 实测后修正为 0 个死代码。**永久教训：未来重组前必须独立 grep + 实测两个步骤都做完，再决定能不能删**」
5. WHEN 本 spec 完成 THEN `docs/00-overview/progress.md` 决策记录段 SHALL 追加 `D-LEGACY-CLEANUP [日期]：删除误导的 legacy/ 目录——0 个真死代码（3 → 1 → 0 三次诚实修正），7 个非死代码迁回 planning/planners/ + planning/critic/ + planning/execution/。spec B 的 5 子目录骨架保留；冻结纪律改为按 graph/build.py 拓扑稳定（而非按文件位置）`
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
| 改业务逻辑 / prompt / schema                    | 本 spec **仅动文件位置 + import 路径**，不删任何文件，不改业务行为|
| 把 ils_score_critic 与 critics_v2 合并            | 维度不同（ILS 候选打分 vs itinerary 全局校验），不做|
| 改前端目录                                      | 本 spec 仅后端 agent 重组                      |
| spec C 实际算法改动                             | 留 spec C 主体；本 spec 仅同步 spec C 三件套的路径锚点 |
| 改 backend/api/ / backend/main.py 业务行为      | 本 spec 仅改 import 路径，不改业务行为          |
| 加 meta_critic_node                            | 留 spec D 之后评估                              |
| 删 ils_score_critic（双 critic 系统简化）       | 不删——主路径 critic_v2 + ILS 路径 critic 是两个维度|
| 简化 PLANNER_LLM_STRATEGY 三档路由              | 不简化——三档都是生产路径                       |
| 删除任何 legacy/ 下的 .py 文件                  | **legacy/ 下 0 个真死代码**——全部解冻迁回 |
| 把 executor 与 graph/execute_finalize 合并      | 行为不等价；超出 spec D 范围（不动业务行为）          |
| 修 graph/execute_finalize 添加 _extract_reserved_time | 同上，超出 spec D 范围                  |
```

---

## 前置条件 / 时序硬约束

**本 spec 必须在 spec C `algorithm-redesign` 实施启动前完成**。

**理由**：

- spec C R3+R4 改动锚点是 `ils_planner.py`——如果 spec C 先做，spec D 再做要返工 2 次 import 路径迁移
- spec C 当前三件套写的锚点是 `agent/legacy/ils_planner.py`，spec D 完成后要同步替换为 `agent/planning/planners/ils_planner.py`（本 spec R4）

**启动检查清单**（task 1 前必须满足）：

- [x] spec A `planning-quality-deep-review` 全部 8 task 完成（git tag `v-spec-a-done` 已打）
- [x] spec B `agent-directory-restructure` 全部 8 task 完成
- [x] spec C `algorithm-redesign` 三件套已落库（commit `9224284`）但 task 1 未启动
- [x] git tag v-spec-d-start 已打（task 1 已完成；本次修正是 spec D 文档而非业务代码）
- [x] 用户人工确认"可以启动 spec D v3"（用户选 a 路线 + 实测发现 executor 不可删后选 a 继续）
