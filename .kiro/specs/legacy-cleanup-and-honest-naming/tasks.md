# Implementation Plan: Legacy Cleanup and Honest Naming (spec D v3)

## Overview

把 spec B 留下的误导性 `legacy/` 目录解构为「主路径活代码 + PLANNER_LLM_STRATEGY 三档子策略 + ILS 专用 critic + 执行类活代码」四类，**0 个真死代码删除**，全部解冻迁回 planning/ 子目录。

**总工时预估**：~3.0h（v3 比 v2 少 0.5h——不再有 deletion + 测试改造）

**关键路径**：T1 baseline ✓ → T2 smartRelocate 8 处迁移（含 1 改名 + 2 跨子目录）+ docstring 改写 → T3 删 legacy/ 目录 + verify 脚本 → T4 升级 test_import_paths + AGENTS.md / spec C / 文档同步 + 一次性 commit

**核心约束**：
- 保留 spec B 5 子目录骨架（core/ + intent/ + planning/ + runtime/ + graph/）不动
- 保留 graph/build.py 拓扑不动
- **业务行为零变化**（仅动文件位置 + 改 import 路径，不删任何 .py 业务文件）
- 一次性原子 commit；前 3 task 失败直接 `git restore .`
- v3 修正版起草版误判：v1 误判 3 个真死代码（漏看相对引用）→ v2 修正为 1 个（漏看实测行为等价性）→ v3 修正为 **0 个**（task 2 实测发现 executor 与 graph/execute_finalize 行为不等价）

## Tasks

- [x] 1. [前置] baseline 验证 + spec A/B/C 完成度核查 + git tag（~0.2h）：**已完成**（spec D v1 task 1 已跑过）。
  - pytest baseline：599 passed + 1 skipped + 0 failed ✓
  - verify_planning ✓ / verify_edge_model ✓ 4/4 / verify_planning_quality ✓ 24/24 / verify_legacy_frozen ✓
  - FastAPI app load ✓
  - git tag `v-spec-d-start` 已打
  - baseline legacy import 引用数 = 33（task 4 grep 0 命中前的对照基线）

- [ ] 2. [R1] smartRelocate 8 处迁移（含 1 改名 + 2 跨子目录）+ docstring 改写（~1.5h）：
  - 先创建空文件占位：
    - `backend/agent/planning/planners/__init__.py`（空）
    - `backend/agent/planning/planners/prompts/__init__.py`（空）
    - `backend/agent/planning/execution/__init__.py`（空）
  - 用 `smartRelocate` 工具按以下顺序移动 8 个文件（**严格按依赖顺序**，每次单独 smartRelocate；不要批量；每次后 grep 复核 + pytest -x）：
    1. `backend/agent/legacy/ils_score_critic.py` → `backend/agent/planning/critic/ils_score_critic.py`（**跨子目录** - 被 ils_planner 用 from .ils_score_critic 引用，搬走后该相对引用要改为 from ..critic.ils_score_critic）
    2. `backend/agent/legacy/segment_decider.py` → `backend/agent/planning/planners/segment_decider.py`
    3. `backend/agent/legacy/prompts/llm_planner_prompt.py` → `backend/agent/planning/planners/prompts/llm_planner_prompt.py`
    4. `backend/agent/legacy/llm_planner.py` → `backend/agent/planning/planners/llm_planner.py`（依赖 prompts/llm_planner_prompt.py 已搬过去）
    5. `backend/agent/legacy/llm_first_planner.py` → `backend/agent/planning/planners/llm_first_planner.py`
    6. `backend/agent/legacy/ils_planner.py` → `backend/agent/planning/planners/ils_planner.py`（依赖 ils_score_critic 已搬到 critic/，相对引用 from ..critic.ils_score_critic）
    7. `backend/agent/legacy/executor.py` → `backend/agent/planning/execution/executor.py`（**跨子目录** - 进新建 execution/）
    8. `backend/agent/legacy/planner_rule.py` → `backend/agent/planning/planners/rule_planner.py`（**改名**：planner_rule.py → rule_planner.py；其他 planner 内部 from .planner_rule 全改为 from .rule_planner）
  - 每次 smartRelocate 后跑 `pytest backend/tests/ -x --tb=short` 必须 exit 0；如失败：立即停 + git restore . 回滚整 task 2
  - 8 个迁移文件顶部注释清理（用 str_replace 单独处理每个文件）：
    - 删除 `# FROZEN: 详见 AGENTS.md §3.3.1，仅 fallback / safety-net，不改业务` 行（8 处，每个文件 1 处）
    - 改写 docstring 为正确职责（按 design.md §Component 3 提供的 8 段设计稿）
  - **关键改名注意**：smartRelocate 改名 planner_rule.py → rule_planner.py 时，类/函数名不变（`plan_itinerary` / `plan_itinerary_with_mode` 仍叫原名）；smartRelocate 自动改 import：
    - `from agent.legacy.planner_rule import plan_itinerary` → `from agent.planning.planners.rule_planner import plan_itinerary`
    - `from .planner_rule import _resolve_time_window`（在 ils_planner / llm_planner 内部）→ `from .rule_planner import _resolve_time_window`
  - 末尾 grep 复核：`grep -rn "from \.planner_rule\|from \.legacy\." backend/ --include="*.py"` 应 0 命中（除 test_import_paths.py 内的 ImportError 反向断言，task 4 处理）

- [ ] 3. [R2] 删除 legacy/ 整个目录 + verify_legacy_frozen.py（~0.3h）：
  - 检查 `backend/agent/legacy/` 应仅剩 `__init__.py` + `__pycache__/`（prompts/ 子目录在 task 2 搬完 llm_planner_prompt + __init__.py 后已空，可删）
  - 用 `delete_file` 删除：
    - `backend/agent/legacy/__init__.py`
    - `backend/agent/legacy/prompts/__init__.py`（如未在 task 2 自动删除）
    - `backend/scripts/verify_legacy_frozen.py`
  - 用 `Remove-Item -Recurse -Force` 删除空目录（PowerShell）：
    - `backend/agent/legacy/__pycache__/`
    - `backend/agent/legacy/prompts/__pycache__/`
    - `backend/agent/legacy/prompts/`
    - `backend/agent/legacy/`（最后删）
  - 跑 `Test-Path backend\agent\legacy` 必须返回 `False`；`Test-Path backend\scripts\verify_legacy_frozen.py` 必须 `False`
  - 跑 `pytest backend/tests/ -x --tb=short` 必须 0 红灯

- [ ] 4. [R1.4+R2.5+R3+R4] 升级 test_import_paths + AGENTS.md + spec C + 文档同步 + 一次性原子 commit（~1.0h）：
  - **4.1 升级 test_import_paths.py**：
    - 把 `def test_legacy_imports()` 改名为 `def test_planning_planners_imports()`；内容替换为 design.md §Property 3 提供的 8 行 import：
      ```python
      from agent.planning.planners.rule_planner import plan_itinerary, plan_itinerary_with_mode
      from agent.planning.planners.ils_planner import plan_hybrid
      from agent.planning.planners.llm_first_planner import plan_llm_first
      from agent.planning.planners.llm_planner import plan_itinerary_llm
      from agent.planning.planners.segment_decider import decide_segments, FULL_SEGMENTS
      from agent.planning.planners.prompts.llm_planner_prompt import LLM_PLANNER_SYSTEM_PROMPT
      from agent.planning.critic.ils_score_critic import run_critics, CriticReport
      from agent.planning.execution.executor import execute_plan, ExecutionResult
      ```
    - 在 `def test_old_paths_no_longer_exist()` 加新一批旧路径反向断言（7 项 ImportError）：`agent.legacy.planner_rule` / `agent.legacy.ils_planner` / `agent.legacy.llm_first_planner` / `agent.legacy.llm_planner` / `agent.legacy.segment_decider` / `agent.legacy.ils_score_critic` / `agent.legacy.executor`
    - 跑 `pytest backend/tests/test_import_paths.py -v` 必须全过
  - **4.2 grep 验证旧路径不再被引用**：
    - 跑 `grep -rn "from backend\.agent\.legacy\|from agent\.legacy\." backend/ --include="*.py"` 必须 0 命中（除 test_import_paths.py 内反向断言）
    - 跑 `grep -rn "from \.legacy\." backend/ --include="*.py"` 必须 0 命中
    - 跑 `grep -rn "agent/legacy/" backend/ --include="*.py" --include="*.md"` 仅 `tests/test_import_paths.py` 内反向断言可命中（其他 0 命中）
  - **4.3 更新 AGENTS.md §3.3.1**：
    - 把目录树代码块的 `legacy/` 段删除
    - 加 `planning/planners/` 段（5 .py + prompts/llm_planner_prompt.py）+ `planning/execution/`（executor.py）
    - `planning/critic/` 段加 `ils_score_critic.py`
    - 删除 MUST NOT 段中 4 条：「在 `agent/legacy/` 加新功能」「verify_legacy_frozen 守护」「FROZEN 标记」「fallback 链路依赖导出符号」
    - 加新一句条款：「`planning/planners/` + `planning/execution/` 下的模块按真实定位区分——rule_planner.py 是主路径分发器，ils_planner.py 是 PLANNER_LLM_STRATEGY=hybrid + graph replan 兜底，llm_first_planner.py 是 PLANNER_LLM_STRATEGY=llm_first（默认）核心，llm_planner.py 是 function_calling 子策略，segment_decider.py 是 ils_planner 依赖，execution/executor.py 是用户确认后的执行类 Tool 派发（与 graph/execute_finalize 不等价）。改动这些文件不需要走「冻结口子」流程，但要遵守 graph/build.py 拓扑不动的纪律」
    - 保留「不动 graph/build.py 拓扑」条款（与 spec B 一致）
  - **4.4 同步 spec C 三件套（4 处）**：
    - `.kiro/specs/algorithm-redesign/requirements.md`：grep 替换 `backend/agent/legacy/ils_planner.py` → `backend/agent/planning/planners/ils_planner.py`；删除 R3+R4 中「FROZEN 模块」「legacy 路径」字样
    - `.kiro/specs/algorithm-redesign/design.md`：§Components 2 段重写——「在 `planning/planners/ils_planner.py` 加 _grounding_filter_poi / _grounding_filter_restaurant」（删除「FROZEN 模块允许加新过滤函数」措辞）
    - `.kiro/specs/algorithm-redesign/tasks.md`：task 4 + task 5 + task 8 同步 ils_planner 路径锚点（grep `agent/legacy/ils_planner.py` 替换为 `agent/planning/planners/ils_planner.py`）
    - 跑 `getDiagnostics` 验证 spec C 三件套无 Error 级别 diagnostic
  - **4.5 文档同步**：
    - 在 `docs/03-implementation/pitfalls.md` 追加 1 条 [P0] 防再犯条款：「目录重组前必须做**两步独立审计**：(1) **grep 完整引用关系**——含 absolute import + 相对引用 (from .X / from ..X) + 内部链式调用（函数体内 from .X import Y）；(2) **实测行为等价性**——任何「等价路径已存在」的删除假设都必须 pytest 实测，不能只看函数名相似 / docstring 说明。spec B 起草时漏掉相对引用（4 个生产路径误归 legacy）；spec D 起草 v1 漏掉相对引用（误判 3 个死代码）；spec D 起草 v2 漏掉实测行为等价性（误判 1 个死代码——executor 用 _extract_reserved_time(note) 解析预留时段、graph/execute_finalize 直接用 start_time，mock 时段严格匹配下不等价）；spec D v3 实测后修正为 0 个死代码。**永久教训：未来重组前必须独立 grep + 实测两个步骤都做完，再决定能不能删**」
    - 在 `docs/00-overview/progress.md` 决策记录段追加 `D-LEGACY-CLEANUP [日期]：删除误导的 legacy/ 目录——0 个真死代码（3 → 1 → 0 三次诚实修正），7 个非死代码 + 1 个 prompt 迁回 planning/planners/ + planning/critic/ + planning/execution/。spec B 的 5 子目录骨架保留；冻结纪律改为按 graph/build.py 拓扑稳定（而非按文件位置）`
    - 在 `problem.md` 追加本次记录（按全局 problem.md 格式）
  - **4.6 完整端到端验证**：
    - 跑 `pytest backend/tests/ -v --tb=short` 必须 0 红灯（与 task 1 baseline 完全一致 + 新增 8 项新路径 + 7 项反向断言全过）
    - 跑 `python backend/scripts/verify_planning.py` + `verify_planning_quality.py` + `verify_edge_model.py` 必须全绿
    - 启动 `python -c "import main; print(main.app)"` 必须输出 FastAPI app
    - `cd frontend && pnpm verify:all` 必须 0 红灯
  - **4.7 一次性原子 commit**：
    - `git status --short` 列出所有变更
    - `git diff --cached --stat` 复核 stage 范围
    - `git add -A`（包含新 planners/ + execution/ + 删除 legacy/ + AGENTS.md + spec C 三件套 + pitfalls/progress/problem 同步）但**不**带 untracked 杂物
    - `git commit -m "refactor(spec-d): 删除误导的 legacy/ 目录——0 个真死代码 + 7 个非死代码 + 1 prompt 全部迁回 planning/planners/ + planning/critic/ + planning/execution/（v3 修正起草版误判 executor 为死代码的错误：实测发现与 graph/execute_finalize 行为不等价）"`
    - `git tag v-spec-d-done`

## Task Dependency Graph

```json
{
  "waves": [
    [1],
    [2],
    [3],
    [4]
  ]
}
```

说明：
- 本 spec 是**严格串行**——每 task 必须等上一 task pytest 验证通过才能进入下一 task
- 不存在可并行的 task（与 spec B 同样性质）
- Task 1（baseline）已完成；Task 4（升级 test + 同步 + commit）必须最后做

## Notes

- **smartRelocate 用法**：与 spec B 完全一致；自动跟随 import 更新
- **smartRelocate 顺序**：task 2 内 8 个文件按依赖顺序逐个 smartRelocate（先迁被消费的：ils_score_critic 跨包 → segment_decider → llm_planner_prompt → llm_planner → llm_first_planner → ils_planner → executor → planner_rule 改名）
- **改名场景**：仅 1 处改名（planner_rule.py → rule_planner.py）；smartRelocate 自动处理位置改变 + 文件名改变，类/函数名不变
- **跨子目录场景**：2 处跨子目录（ils_score_critic 从 planners/ 迁到 critic/；executor 进新建 execution/）
- **不删任何 .py 业务文件**：v3 修正版核心约束——0 个真死代码
- **失败处理协议**：任何 task 末尾 pytest 红灯立即停 + 报告，**禁止跨 task 修复**
- **commit 策略**：4 个 task 中途绝不 commit，全部完成 + 验收通过后一次性原子 commit
- **AGENTS.md 改动边界**：仅改 §3.3.1 编排冻结纪律段；不改 MUST / MUST NOT 其他条款；保留「不动 graph/build.py 拓扑」

## Risk & Mitigation

```text
| 风险                                              | 概率 | 影响 | 缓解                                                |
|---------------------------------------------------|------|-----|---------------------------------------------------|
| baseline 阶段发现 spec A/B 红灯                    | 已过 | 高  | task 1 已跑 ✓                                      |
| smartRelocate 漏改某个相对引用（含 from .X / from ..X）| 中 | 中  | 分 task 推进 + 每次 smartRelocate 后 pytest -x         |
| 改名（planner_rule→rule_planner）破断字符串 import| 低   | 中  | grep 全仓库 importlib.import_module / __import__    |
| ils_score_critic 跨子目录搬时 import 路径漂移       | 低   | 中  | smartRelocate 自动 + task 4 grep 复核 from ..critic.ils_score_critic|
| executor 进新建 execution/ 子目录后跨包引用漂移     | 低   | 中  | smartRelocate 自动 + task 4 grep 复核                |
| backend/main.py:1740 改后真 LLM 链路启动失败       | 低   | 高  | task 4 必跑 `python -c "import main; print(main.app)"` |
| AGENTS.md §3.3.1 漏改某条 MUST NOT                | 中   | 低  | task 4 grep「legacy」「FROZEN」全检（5+ 处）         |
| spec C tasks.md 锚点同步遗漏                       | 中   | 中  | task 4 用 grep + str_replace 批量替换            |
| commit 中途出现导致回滚困难                       | 低   | 低  | task 4 末才 commit；前 3 task 失败 git restore .   |
| 起草版编排者犯重组审计错（已发现 + 修正 3 次）     | 0（已修正）| - | 永久教训写入 pitfalls.md：grep + 实测两步都做完     |
```

## 启动检查清单（task 1 前必须满足）

```text
| #  | 检查项                                          | 验证方法                                              | 状态 |
|----|-----------------------------------------------|------------------------------------------------------|------|
| C1 | spec A 全部 8 task 完成                         | 读 .kiro/specs/planning-quality-deep-review/tasks.md，全 [x] | ✓ |
| C2 | spec B 全部 8 task 完成                         | 读 .kiro/specs/agent-directory-restructure/tasks.md，全 [x] | ✓ |
| C3 | spec C 三件套已落库（commit 9224284）但未启动    | 读 .kiro/specs/algorithm-redesign/tasks.md，全部 [ ] 未勾选 | ✓ |
| C4 | git tag v-spec-d-start 已打                     | git tag --list 应见 v-spec-d-start                  | ✓ |
| C5 | 用户人工确认"可以启动 spec D v3"                | 用户消息明确允许（已选 a 路线 + 修正 v3 后继续）       | ✓ |
```

C1-C5 已全过；进入 task 2 实施。

## Out of Scope（再次确认）

```text
| 不做的事                                       | 理由                                          |
|-----------------------------------------------|----------------------------------------------|
| 重组 graph/ / runtime/ / intent/ 子目录         | spec B 锁                                    |
| 改 graph/build.py 拓扑                          | spec B 锁；编排冻结纪律                       |
| 改业务行为 / prompt / schema                    | 本 spec 仅动文件位置 + 改测试 import + 删 legacy/ 空目录|
| 把 ils_score_critic 与 critics_v2 合并            | 维度不同（候选打分 vs 全局校验）              |
| 改前端目录                                      | 本 spec 仅后端                                |
| 改 backend/api/ 业务行为                        | 本 spec 仅改 import 路径                      |
| 加 meta_critic_node                            | 留 spec C 之后评估                            |
| 删 weights_llm.py 顶部 # FROZEN 注释            | 留 spec C R4 落地后评估                       |
| 重写 AGENTS.md 其他段落                         | 本 spec 仅改 §3.3.1                          |
| 重写 spec A / spec B 三件套                     | 它们已落地；本 spec 不动                       |
| 改前端 ChatDock / ToolTracePanel                | 留 spec C R6                                |
| 简化 PLANNER_LLM_STRATEGY 三档路由              | 不简化——三档都是生产路径                       |
| 删除任何 legacy/ 下的 .py 业务文件              | **0 个真死代码——v3 修正版核心约束**          |
| 修 graph/execute_finalize 加 _extract_reserved_time| 超出本 spec「只动位置不动业务」纪律        |
```
