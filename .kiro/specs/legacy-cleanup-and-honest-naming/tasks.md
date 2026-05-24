# Implementation Plan: Legacy Cleanup and Honest Naming (spec D)

## Overview

把 spec B 留下的误导性 `legacy/` 目录解构为「死代码（删 1）+ 主路径活代码（迁回 planning/planners/）+ PLANNER_LLM_STRATEGY 三档子策略（迁回 planning/planners/）+ ILS 专用 critic（迁回 planning/critic/）」四类，删除 `legacy/` 整个目录。

**总工时预估**：~3.5h（≈ 半人天偏长，hackathon 时间盒内 1 个上午 + 1 个早午餐可以做完）

**关键路径**：T1 baseline ✓ → T2 删 1 个死代码 + 改 agent/__init__.py + 2 测试 → T3 smartRelocate 7 处迁移（含 1 改名 + 1 跨子目录）+ docstring 改写 → T4 删 legacy/ 目录 + verify 脚本 → T5 升级 test_import_paths + AGENTS.md / spec C / 文档同步 + 一次性 commit

**核心约束**：
- 保留 spec B 5 子目录骨架（core/ + intent/ + planning/ + runtime/ + graph/）不动
- 保留 graph/build.py 拓扑不动（spec B 锁 + 编排冻结纪律）
- 业务行为零变化（仅动文件位置 + 删 1 个死代码 + 改 import 路径 + 改测试导入）
- 一次性原子 commit（与 spec B 策略一致）；前 4 task 失败直接 `git restore .`
- **本次起草版被 user 独立审查指出 2 个误判**（llm_planner / llm_first_planner 误判为死代码）；修正版只删 executor.py 一个

## Tasks

- [x] 1. [前置] baseline 验证 + spec A/B/C 完成度核查 + git tag（~0.2h）：**已完成**。
  - pytest baseline：599 passed + 1 skipped + 0 failed ✓
  - verify_planning ✓ / verify_edge_model ✓ 4/4 / verify_planning_quality ✓ 24/24 / verify_legacy_frozen ✓
  - FastAPI app load ✓
  - git tag `v-spec-d-start` 已打
  - baseline legacy import 引用数 = 33（task 5 grep 0 命中前的对照基线）

- [ ] 2. [R1] 删除 1 个真死代码 executor.py + 改 agent/__init__.py + 2 测试改造（~0.7h）：
  - **删除前最后一次 grep 确认**：`grep -rn "from agent\.legacy\.executor\|legacy\.executor import\|from \.legacy\.executor" backend/ --include="*.py"` 应仅 4 处命中（test_agent_flow / test_8_scenarios / test_import_paths / agent/__init__.py）
  - 用 `delete_file` 删除：`backend/agent/legacy/executor.py`
  - 改 `backend/agent/__init__.py`：
    - 删除 `from .legacy.executor import execute_plan, ExecutionResult` 行
    - 删除 `__all__` 中 `"execute_plan"` / `"ExecutionResult"` 两项
    - 在文件顶部 docstring 把 `executor.py 执行类 Tool 派发` 改为 `（已废弃；执行类 Tool 派发由 graph/nodes/execute_finalize.py 替代）`
  - 改 `backend/tests/test_agent_flow.py`：
    - 把 `from agent.legacy.executor import execute_plan` 改为 `from agent.graph.nodes.execute_finalize import execute_finalize_node`
    - 把 `exec_result = execute_plan(itinerary, party_size=..., ...)` 改为 `state = {"itinerary": itinerary, "intent": intent}; result_state = execute_finalize_node(state); exec_result = type("ExecResult", (), {"orders": result_state.get("orders", []), "share_message": result_state.get("share_message", ""), "narration": result_state.get("narration", ""), "itinerary": result_state.get("itinerary", itinerary), "success": True})()`（duck-typed namespace；保留断言不动）
  - 改 `backend/tests/test_8_scenarios.py`：同样改造（test_executor_reservation_filled_after_plan 用例的 execute_plan 调用）
  - 改 `backend/tests/test_import_paths.py:test_legacy_imports`：删除 `from agent.legacy.executor import execute_plan` 一行（保留其他 6 个 import 暂不动；task 5 整体改名 + 内容替换）
  - 跑 `pytest backend/tests/ -x --tb=short` 必须 0 红灯（test_agent_flow + test_8_scenarios + test_import_paths 等价行为已修复）
  - 如失败：立即停 + `git status` 列变更 + 报告失败测试 + traceback；用户决定回滚（git restore .）或继续修复

- [ ] 3. [R2] smartRelocate 7 处迁移 + docstring 改写（~1.2h）：
  - 先创建空文件占位：
    - `backend/agent/planning/planners/__init__.py`（空）
    - `backend/agent/planning/planners/prompts/__init__.py`（空）
  - 用 `smartRelocate` 工具按以下顺序移动 7 个文件（**严格按顺序**，每次单独 smartRelocate；不要批量；每次后 grep 复核）：
    1. `backend/agent/legacy/ils_score_critic.py` → `backend/agent/planning/critic/ils_score_critic.py`（**跨子目录**，**先做**：被 ils_planner 用相对引用 from .ils_score_critic，搬走后 ils_planner 内部要改为 from ..critic.ils_score_critic；smartRelocate 应自动处理）
    2. `backend/agent/legacy/segment_decider.py` → `backend/agent/planning/planners/segment_decider.py`
    3. `backend/agent/legacy/prompts/llm_planner_prompt.py` → `backend/agent/planning/planners/prompts/llm_planner_prompt.py`
    4. `backend/agent/legacy/llm_planner.py` → `backend/agent/planning/planners/llm_planner.py`（依赖 prompts/llm_planner_prompt.py 已搬过去）
    5. `backend/agent/legacy/llm_first_planner.py` → `backend/agent/planning/planners/llm_first_planner.py`
    6. `backend/agent/legacy/ils_planner.py` → `backend/agent/planning/planners/ils_planner.py`（依赖 ils_score_critic 已搬到 critic/，相对引用 from ..critic.ils_score_critic）
    7. `backend/agent/legacy/planner_rule.py` → `backend/agent/planning/planners/rule_planner.py`（**改名**：planner_rule.py → rule_planner.py；其他 planner 内部 from .planner_rule 全改为 from .rule_planner）
  - 每次 smartRelocate 后跑 `pytest backend/tests/ -x --tb=short` 必须 exit 0；如失败：立即停 + git restore . 回滚整 task 3
  - 7 个迁移文件顶部注释清理（用 str_replace 单独处理每个文件）：
    - 删除 `# FROZEN: 详见 AGENTS.md §3.3.1，仅 fallback / safety-net，不改业务` 行（7 处，每个文件 1 处）
    - 改写 docstring 为正确职责（按 design.md §Component 4 提供的 7 段设计稿，分别标注「主路径分发器」/「PLANNER_LLM_STRATEGY=hybrid 子策略」/「PLANNER_LLM_STRATEGY=llm_first 默认核心」/「PLANNER_LLM_STRATEGY=function_calling 子策略」/「ils_planner 依赖」/「LLM Function Calling system prompt」/「ILS 候选打分专用 critic」7 类标签）
  - **关键改名注意**：smartRelocate 改名 planner_rule.py → rule_planner.py 时，类/函数名不变（`plan_itinerary` / `plan_itinerary_with_mode` 仍叫原名）；smartRelocate 自动改 import：
    - `from agent.legacy.planner_rule import plan_itinerary` → `from agent.planning.planners.rule_planner import plan_itinerary`
    - `from .planner_rule import _resolve_time_window`（在 ils_planner / llm_planner 内部）→ `from .rule_planner import _resolve_time_window`
  - 末尾 grep 复核：`grep -rn "from \.planner_rule\|from \.legacy\." backend/ --include="*.py"` 应 0 命中（除 test_import_paths.py 内的 ImportError 反向断言，task 5 处理）

- [ ] 4. [R3] 删除 legacy/ 整个目录 + verify_legacy_frozen.py（~0.3h）：
  - 检查 `backend/agent/legacy/` 应仅剩 `__init__.py` + `__pycache__/`（prompts/ 子目录在 task 3 搬完 llm_planner_prompt + __init__.py 后已空，可删）
  - 用 `delete_file` 删除：
    - `backend/agent/legacy/__init__.py`
    - `backend/agent/legacy/prompts/__init__.py`（如未在 task 3 自动删除）
    - `backend/scripts/verify_legacy_frozen.py`
  - 用 `Remove-Item -Recurse -Force` 删除空目录（PowerShell）：
    - `backend/agent/legacy/__pycache__/`
    - `backend/agent/legacy/prompts/__pycache__/`
    - `backend/agent/legacy/prompts/`
    - `backend/agent/legacy/`（最后删）
  - 跑 `Test-Path backend\agent\legacy` 必须返回 `False`；`Test-Path backend\scripts\verify_legacy_frozen.py` 必须 `False`
  - 跑 `pytest backend/tests/ -x --tb=short` 必须 0 红灯（确认 task 3 迁移无副作用 + task 4 删除无副作用）

- [ ] 5. [R1.4+R2.6+R3.5+R4+R5] 升级 test_import_paths + AGENTS.md + spec C + 文档同步 + 一次性原子 commit（~1.1h）：
  - **5.1 升级 test_import_paths.py**：
    - 把 `def test_legacy_imports()` 改名为 `def test_planning_planners_imports()`；内容替换为 design.md §Property 3 提供的 7 行 import：
      ```python
      from agent.planning.planners.rule_planner import plan_itinerary, plan_itinerary_with_mode
      from agent.planning.planners.ils_planner import plan_hybrid
      from agent.planning.planners.llm_first_planner import plan_llm_first
      from agent.planning.planners.llm_planner import plan_itinerary_llm
      from agent.planning.planners.segment_decider import decide_segments, FULL_SEGMENTS
      from agent.planning.planners.prompts.llm_planner_prompt import LLM_PLANNER_SYSTEM_PROMPT
      from agent.planning.critic.ils_score_critic import run_critics, CriticReport
      ```
    - 在 `def test_old_paths_no_longer_exist()` 加新一批旧路径反向断言（7 项 ImportError）：`agent.legacy.planner_rule` / `agent.legacy.ils_planner` / `agent.legacy.llm_first_planner` / `agent.legacy.llm_planner` / `agent.legacy.segment_decider` / `agent.legacy.ils_score_critic` / `agent.legacy.executor`
    - 跑 `pytest backend/tests/test_import_paths.py -v` 必须全过
  - **5.2 grep 验证旧路径不再被引用**：
    - 跑 `grep -rn "from backend\.agent\.legacy\|from agent\.legacy\." backend/ --include="*.py"` 必须 0 命中（除 test_import_paths.py 内反向断言）
    - 跑 `grep -rn "from \.legacy\." backend/ --include="*.py"` 必须 0 命中
    - 跑 `grep -rn "agent/legacy/" backend/ --include="*.py" --include="*.md"` 仅 `tests/test_import_paths.py` 内反向断言可命中（其他 0 命中）
  - **5.3 更新 AGENTS.md §3.3.1**：
    - 把目录树代码块的 `legacy/` 段删除
    - 加 `planning/planners/` 段（5 .py + prompts/llm_planner_prompt.py）
    - `planning/critic/` 段加 `ils_score_critic.py`
    - 删除 MUST NOT 段中 4 条：「在 `agent/legacy/` 加新功能」「verify_legacy_frozen 守护」「FROZEN 标记」「fallback 链路依赖导出符号」
    - 加新一句条款：「`planning/planners/` 下的模块按真实定位区分——rule_planner.py 是主路径分发器，ils_planner.py 是 PLANNER_LLM_STRATEGY=hybrid + graph replan 兜底，llm_first_planner.py 是 PLANNER_LLM_STRATEGY=llm_first（默认）核心，llm_planner.py 是 function_calling 子策略，segment_decider.py 是 ils_planner 依赖。改动这些文件不需要走「冻结口子」流程，但要遵守 graph/build.py 拓扑不动的纪律」
    - 保留「不动 graph/build.py 拓扑」条款（与 spec B 一致）
  - **5.4 同步 spec C 三件套（4 处）**：
    - `.kiro/specs/algorithm-redesign/requirements.md`：grep 替换 `backend/agent/legacy/ils_planner.py` → `backend/agent/planning/planners/ils_planner.py`；删除 R3+R4 中「FROZEN 模块」「legacy 路径」字样
    - `.kiro/specs/algorithm-redesign/design.md`：§Components 2 段重写——「在 `planning/planners/ils_planner.py` 加 _grounding_filter_poi / _grounding_filter_restaurant」（删除「FROZEN 模块允许加新过滤函数」措辞）
    - `.kiro/specs/algorithm-redesign/tasks.md`：task 4 + task 5 + task 8 同步 ils_planner 路径锚点（grep `agent/legacy/ils_planner.py` 替换为 `agent/planning/planners/ils_planner.py`）
    - 跑 `getDiagnostics` 验证 spec C 三件套无 Error 级别 diagnostic
  - **5.5 文档同步**：
    - 在 `docs/03-implementation/pitfalls.md` 追加 1 条 [P0] 防再犯条款（按 R5.4 模板）：「目录重组前必须 grep **完整引用关系**——含 absolute import (`from agent.legacy.X`) + 相对引用 (`from .X`, `from ..X`) + 内部链式调用 (函数体内 `from .X import Y`)。**spec B 起草时漏掉相对引用 + 内部链式**，把 4 个生产路径（含 PLANNER_LLM_STRATEGY 三档子策略 + ils_planner 依赖）误归为 legacy；**spec D 起草时编排者犯了同样错误**（grep 只看 absolute），被 user 独立审查指出后修正。防再犯：每次重组 spec 启动前用至少 4 个 grep 模板覆盖（absolute import / from \.X / from \.\.X / 函数体内链式），明确「主路径 / 子策略 / 死代码」三类，每类去不同子目录」
    - 在 `docs/00-overview/progress.md` 决策记录段追加 `D-LEGACY-CLEANUP [日期]：删除误导的 legacy/ 目录——1 个真死代码删除（executor.py），6 个非死代码迁回 planning/planners/ + planning/critic/。spec B 的 5 子目录骨架保留；冻结纪律改为按 graph/build.py 拓扑稳定（而非按文件位置）。同步纠正 spec D 起草时的引用审计错误（编排者首次 grep 只看 absolute import，被 user 独立审查指出后改为完整审计）`
    - 在 `problem.md` 追加本次记录（按全局 problem.md 格式）
  - **5.6 完整端到端验证**：
    - 跑 `pytest backend/tests/ -v --tb=short` 必须 0 红灯（与 task 1 baseline 一致 + 改造的 2 个测试全过 + 升级的 test_import_paths 7 项新路径 + 7 项反向断言全过）
    - 跑 `python backend/scripts/verify_planning.py` + `verify_planning_quality.py` + `verify_edge_model.py` 必须全绿
    - 启动 `python -c "import main; print(main.app)"` 必须输出 FastAPI app（验证 main.py:1740 改 import 后启动 OK）
    - `cd frontend && pnpm verify:all` 必须 0 红灯
  - **5.7 一次性原子 commit**：
    - `git status --short` 列出所有变更
    - `git diff --cached --stat` 复核 stage 范围
    - `git add -A`（包含新 planners/ + 删除 legacy/ + AGENTS.md + spec C 三件套 + pitfalls/progress/problem 同步）但**不**带 untracked 杂物（image.png / *.txt 等）
    - `git commit -m "refactor(spec-d): 删除误导的 legacy/ 目录——1 个真死代码删除 + 6 个非死代码迁回 planning/planners/ + planning/critic/ + AGENTS.md/spec C/pitfalls 同步（修正起草版误判 llm_planner/llm_first_planner 为死代码的错误）"`
    - `git tag v-spec-d-done`

## Task Dependency Graph

```json
{
  "waves": [
    [1],
    [2],
    [3],
    [4],
    [5]
  ]
}
```

说明：
- 本 spec 是**严格串行**——每 task 必须等上一 task pytest 验证通过才能进入下一 task（避免 import 路径冲突累积）
- 不存在可并行的 task（与 spec B 同样性质，动文件位置串行更安全）
- Task 1（baseline）已完成；Task 5（改造测试 + 同步 + commit）必须最后做

## Notes

- **smartRelocate 用法**：与 spec B 完全一致，sourcePath / destinationPath 都用绝对路径（带 `d:\桌面\美团AI Hackathon\` 前缀）；工具自动跟随 import 更新
- **smartRelocate 顺序**：task 3 内 7 个文件按依赖顺序逐个 smartRelocate（先迁依赖被消费的：ils_score_critic 跨包 → segment_decider → llm_planner_prompt → llm_planner → llm_first_planner → ils_planner → planner_rule 改名）；批量同迁可能漏跨包相对引用
- **改名场景**：仅 1 处改名（planner_rule.py → rule_planner.py）；smartRelocate 自动处理位置改变 + 文件名改变，类/函数名不变
- **跨子目录场景**：仅 1 处跨子目录（ils_score_critic 从 planners/ 迁到 critic/）；其原相对引用方（ils_planner.py:80 from .ils_score_critic）会变成 from ..critic.ils_score_critic
- **测试改造的等价性验证**：execute_finalize_node 与 executor.execute_plan 行为等价（都做 reserve + share + narration），spec A R6 已验证；本 spec 只动测试 import 不动业务逻辑
- **失败处理协议**：任何 task 末尾 pytest 红灯立即停 + 报告，**禁止跨 task 修复**
- **commit 策略**：5 个 task 中途绝不 commit，全部完成 + 验收通过后一次性原子 commit；如某 task 失败决定回滚，跑 `git restore .` 撤销本批次
- **AGENTS.md 改动边界**：仅改 §3.3.1 编排冻结纪律段；不改 MUST / MUST NOT 其他条款；保留「不动 graph/build.py 拓扑」（与 spec B 一致）
- **codesee features.json 不手动改**：触发条件是代码改动后 codesee sync 流程自动跑

## Risk & Mitigation

```text
| 风险                                              | 概率 | 影响 | 缓解                                                |
|---------------------------------------------------|------|-----|---------------------------------------------------|
| baseline 阶段发现 spec A/B 红灯                    | 已过 | 高  | task 1 已跑 ✓                                      |
| smartRelocate 漏改某个相对引用（含 from .X / from ..X）| 中 | 中  | 分 task 推进 + 每 task pytest -x 立即定位             |
| 改名（planner_rule→rule_planner）破断字符串 import| 低   | 中  | grep 全仓库 importlib.import_module / __import__    |
| executor.py 删除后测试改造行为不等价              | 中   | 高  | task 2 单独验证 test_agent_flow + test_8_scenarios  |
| backend/main.py:1740 改后真 LLM 链路启动失败       | 低   | 高  | task 5 必跑 `python -c "import main; print(main.app)"` |
| AGENTS.md §3.3.1 漏改某条 MUST NOT                | 中   | 低  | task 5 grep「legacy」「FROZEN」全检（5+ 处）         |
| spec C tasks.md 锚点同步遗漏                       | 中   | 中  | task 5 用 grep + str_replace 批量替换            |
| commit 中途出现导致回滚困难                       | 低   | 低  | task 5 末才 commit；前 4 task 失败 git restore .   |
| 改 main.py:1740 时漏改其他位置                     | 低   | 中  | smartRelocate 自动改全仓库 + task 5 grep            |
| ils_score_critic 跨子目录搬时 import 路径漂移       | 低   | 中  | smartRelocate 自动 + task 5 grep 复核 from ..critic.ils_score_critic|
| 起草版编排者犯重组审计错（已发现）                  | 0（已修正）| - | spec D requirements / design / tasks 已重写        |
| 5 个 collab/replan 引用 smartRelocate 跟不上       | 低   | 高  | task 3 每次迁移后跑全部测试；如失败立即停           |
```

## 启动检查清单（task 1 前必须满足）

```text
| #  | 检查项                                          | 验证方法                                              | 状态 |
|----|-----------------------------------------------|------------------------------------------------------|------|
| C1 | spec A 全部 8 task 完成                         | 读 .kiro/specs/planning-quality-deep-review/tasks.md，全 [x] | ✓ |
| C2 | spec B 全部 8 task 完成                         | 读 .kiro/specs/agent-directory-restructure/tasks.md，全 [x] | ✓ |
| C3 | spec C 三件套已落库（commit 9224284）但未启动    | 读 .kiro/specs/algorithm-redesign/tasks.md，全部 [ ] 未勾选 | ✓ |
| C4 | git tag v-spec-d-start 已打                     | git tag --list 应见 v-spec-d-start                  | ✓ |
| C5 | 用户人工确认"可以启动 spec D"                   | 用户消息明确允许（已选 a 路线 + 修正版后继续）        | ✓ |
```

C1-C5 已全过；进入 task 2 实施。

## Out of Scope（再次确认）

```text
| 不做的事                                       | 理由                                          |
|-----------------------------------------------|----------------------------------------------|
| 重组 graph/ / runtime/ / intent/ 子目录         | spec B 锁                                    |
| 改 graph/build.py 拓扑                          | spec B 锁；编排冻结纪律                       |
| 改业务行为 / prompt / schema                    | 本 spec 仅动文件位置 + 删 1 个死代码 + 改测试 import|
| 把 ils_score_critic 与 critics_v2 合并            | 维度不同（候选打分 vs 全局校验）              |
| 改前端目录                                      | 本 spec 仅后端                                |
| 改 backend/api/ 业务行为                        | 本 spec 仅改 import 路径                      |
| 加 meta_critic_node                            | 留 spec C 之后评估                            |
| 删 weights_llm.py 顶部 # FROZEN 注释            | 留 spec C R4 落地后评估                       |
| 重写 AGENTS.md 其他段落                         | 本 spec 仅改 §3.3.1                          |
| 重写 spec A / spec B 三件套                     | 它们已落地；本 spec 不动                       |
| 改前端 ChatDock / ToolTracePanel                | 留 spec C R6                                |
| 简化 PLANNER_LLM_STRATEGY 三档路由              | 不简化——三档都是生产路径                       |
| 删除 llm_planner / llm_first_planner            | **不删——它们是 PLANNER_LLM_STRATEGY 子策略生产路径**|
| 合并 5 个 planner 模块                           | 业务影响评估超出本 spec 范围                   |
```
