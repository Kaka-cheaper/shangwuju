# Implementation Plan: Legacy Cleanup and Honest Naming (spec D)

## Overview

把 spec B 留下的误导性 `legacy/` 目录解构为「死代码（删）+ 主路径活代码（迁回 planning/planners/）+ 活 fallback（同上）+ ILS 专用 critic（迁回 planning/critic/）」四类，删除 `legacy/` 整个目录。

**总工时预估**：~2.7h（≈ 半人天，hackathon 时间盒内 1 个上午可以做完）

**关键路径**：T1 baseline → T2 删 3 个死代码 → T3 smartRelocate 4 处迁移 + docstring 改写 → T4 删 legacy/ 目录 + verify 脚本 → T5 改造 3 个测试 + 文档同步 + 一次性 commit

**核心约束**：
- 保留 spec B 5 子目录骨架（core/ + intent/ + planning/ + runtime/ + graph/）不动
- 保留 graph/build.py 拓扑不动（spec B 锁 + 编排冻结纪律）
- 业务行为零变化（仅动文件位置 + 删死代码 + 改 import 路径 + 改测试导入）
- 一次性原子 commit（与 spec B 策略一致）；前 4 task 失败直接 `git restore .`

## Tasks

- [ ] 1. [前置] baseline 验证 + spec A/B/C 完成度核查 + git tag（~0.2h）：
  - 跑 `pytest backend/tests/ -v --tb=short` 记录基线（必须全绿；含 spec A 30 项 + spec B 33 项 import_paths 测试）
  - 跑 `python backend/scripts/verify_planning.py` + `verify_planning_quality.py`（spec A R10）+ `verify_edge_model.py` + `verify_legacy_frozen.py`（spec B R3，本 spec 删除前最后一次跑）必须全绿
  - 启动 `python -m backend.main &` + `curl http://localhost:8000/health` 必须 200
  - 跑 `cd frontend && pnpm verify:all` 必须 0 红灯
  - 读 `.kiro/specs/planning-quality-deep-review/tasks.md` 确认 8 个 task 全 [x]
  - 读 `.kiro/specs/agent-directory-restructure/tasks.md` 确认 8 个 task 全 [x]
  - 读 `.kiro/specs/algorithm-redesign/tasks.md` 确认全部 [ ] 未启动（spec C 不能比 spec D 先启动）
  - 如有任何不通过 → 立即停止 spec D，先报告状态等用户决定
  - 如全通过 → 打 git tag `v-spec-d-start`（用于 spec D 出问题时回滚锚点）
  - 跑 `grep -rn "from backend\.agent\.legacy\|from agent\.legacy\." backend/ --include="*.py" | wc -l` 记录基线引用数（应 ≈ 25-30 处，包括测试 + main.py + collab + replan）

- [ ] 2. [R1] 删除 3 个真死代码模块（~0.6h）：
  - **删除前最后一次 grep 确认**：
    - `grep -rn "llm_first_planner" backend/ --include="*.py"` 应 0 命中
    - `grep -rn "from agent\.legacy\.llm_planner\|from agent\.legacy\.executor" backend/ --include="*.py"` 列出引用方（应仅 test_llm_planner.py / test_agent_flow.py / test_8_scenarios.py / legacy 内部）
  - 用 `delete_file` 删除：
    - `backend/agent/legacy/llm_first_planner.py`
    - `backend/agent/legacy/llm_planner.py`
    - `backend/agent/legacy/prompts/llm_planner_prompt.py`（孤儿，仅 llm_planner.py 引用）
    - `backend/agent/legacy/prompts/__init__.py`（空包，删 llm_planner_prompt 后变空）
    - `backend/agent/legacy/executor.py`
  - 跑 `pytest backend/tests/ -x --tb=short` 应有 3 个测试报错（test_llm_planner / test_agent_flow / test_8_scenarios）——这是预期，task 5 改造修复
  - 跑 `pytest backend/tests/ --ignore=backend/tests/test_llm_planner.py --ignore=backend/tests/test_agent_flow.py --ignore=backend/tests/test_8_scenarios.py -x --tb=short` 应 0 红灯（其他测试不受影响）；如失败：立即停 + 报告 + git restore .

- [ ] 3. [R2] smartRelocate 4 处迁移 + docstring 改写（~0.8h）：
  - 先创建空文件占位：
    - `backend/agent/planning/planners/__init__.py`（空）
  - 用 `smartRelocate` 工具按以下顺序移动 4 个文件（逐个移动，每次确认 import 自动跟随；不要批量）：
    - `backend/agent/legacy/planner_rule.py` → `backend/agent/planning/planners/rule_planner.py`（**改名**：planner_rule.py → rule_planner.py）
    - `backend/agent/legacy/ils_planner.py` → `backend/agent/planning/planners/ils_planner.py`（不改名）
    - `backend/agent/legacy/segment_decider.py` → `backend/agent/planning/planners/segment_decider.py`（不改名）
    - `backend/agent/legacy/ils_score_critic.py` → `backend/agent/planning/critic/ils_score_critic.py`（不改名）
  - 每次 smartRelocate 后跑 `pytest backend/tests/ --ignore=backend/tests/test_llm_planner.py --ignore=backend/tests/test_agent_flow.py --ignore=backend/tests/test_8_scenarios.py -x --tb=short`（仍跳过 task 5 待修的 3 个测试）；如失败立即停 + git restore . 回滚整 task 3
  - 4 个迁移文件顶部注释清理（用 str_replace）：
    - 删除 `# FROZEN: 详见 AGENTS.md §3.3.1，仅 fallback / safety-net，不改业务` 行（4 处，每个文件 1 处）
    - 改写 docstring 为正确职责（按 design.md §Component 4 提供的 4 段设计稿）
  - **关键改名注意**：smartRelocate 改名 planner_rule.py → rule_planner.py 时，类/函数名不变（`plan_itinerary` / `plan_itinerary_with_mode` 仍叫原名）；smartRelocate 自动改 import：`from agent.legacy.planner_rule import plan_itinerary` → `from agent.planning.planners.rule_planner import plan_itinerary`

- [ ] 4. [R3] 删除 legacy/ 整个目录 + verify_legacy_frozen.py（~0.3h）：
  - 检查 `backend/agent/legacy/` 应仅剩 `__init__.py` + `__pycache__/` + `prompts/`（可能含 `__pycache__/`）
  - 用 `delete_file` 删除：
    - `backend/agent/legacy/__init__.py`
    - `backend/scripts/verify_legacy_frozen.py`
  - 用 `Remove-Item -Recurse -Force` 删除空目录：
    - `backend/agent/legacy/__pycache__/`
    - `backend/agent/legacy/prompts/`（如果还存在）
    - `backend/agent/legacy/`（最后删）
  - 跑 `Test-Path backend\agent\legacy` 必须返回 `False`（PowerShell）；`Test-Path backend\scripts\verify_legacy_frozen.py` 必须 `False`
  - 跑 `pytest backend/tests/ --ignore=backend/tests/test_llm_planner.py --ignore=backend/tests/test_agent_flow.py --ignore=backend/tests/test_8_scenarios.py -x --tb=short` 必须 0 红灯（确认 task 3 迁移无副作用 + task 4 删除无副作用）

- [ ] 5. [R1.5+R2.5+R3.5+R4+R5] 改造 3 个测试 + AGENTS.md + spec C + 文档同步 + 一次性原子 commit（~0.8h）：
  - **5.1 改造 test_llm_planner.py**：删除 line 20 的 `from agent.legacy.llm_planner import plan_itinerary_llm`；4 个测试用例本身不动（已经是测 `plan_itinerary_with_mode("llm")` 整体行为）；跑 `pytest backend/tests/test_llm_planner.py -v` 必须 4 项全过
  - **5.2 改造 test_agent_flow.py + test_8_scenarios.py**：
    - 把 `from agent.legacy.executor import execute_plan` 改为 `from agent.graph.nodes.execute_finalize import execute_finalize_node`
    - 改测试体内的调用：`result = execute_plan(itinerary=itinerary, intent=intent)` → `state = {"itinerary": itinerary, "intent": intent}; result_state = execute_finalize_node(state); result = {"orders": result_state.get("orders", []), "share_message": result_state.get("share_message", ""), "narration": result_state.get("narration", "")}`
    - 跑 `pytest backend/tests/test_agent_flow.py backend/tests/test_8_scenarios.py -v` 必须全过
  - **5.3 升级 test_import_paths.py**：
    - 把 `def test_legacy_imports()` 改名为 `def test_planning_planners_imports()`；内容替换为 design.md §Property 3 提供的 4 行 import
    - 在 `def test_old_paths_no_longer_exist()` 加新一批旧路径反向断言（`agent.legacy.planner_rule` / `agent.legacy.ils_planner` / `agent.legacy.segment_decider` / `agent.legacy.ils_score_critic` / `agent.legacy.llm_first_planner` / `agent.legacy.llm_planner` / `agent.legacy.executor` 全部 ImportError）
    - 跑 `pytest backend/tests/test_import_paths.py -v` 必须全过
  - **5.4 grep 验证旧路径不再被引用**：
    - 跑 `grep -rn "from backend\.agent\.legacy\|from agent\.legacy\." backend/ --include="*.py"` 必须 0 命中
    - 跑 `grep -rn "agent/legacy/" backend/ --include="*.py" --include="*.md"` 仅 `tests/test_import_paths.py` 内的反向断言可命中（其他 0 命中）
  - **5.5 更新 AGENTS.md §3.3.1**：
    - 把目录树代码块的 `legacy/` 段删除
    - 加 `planning/planners/`（rule_planner / ils_planner / segment_decider）段
    - `planning/critic/` 段加 `ils_score_critic.py`
    - 删除 MUST NOT 段中 4 条：「在 `agent/legacy/` 加新功能」「verify_legacy_frozen 守护」「FROZEN 标记」「fallback 链路依赖导出符号」
    - 加新一句条款：「`planning/planners/` 下的模块按真实定位区分——rule_planner.py 是主路径，ils_planner.py 是 graph replan 第 3 次 ILS 兜底，segment_decider.py 是 ils_planner 依赖。改动这些文件不需要走「冻结口子」流程，但要遵守 graph/build.py 拓扑不动的纪律」
    - 保留「不动 graph/build.py 拓扑」条款（与 spec B 一致）
  - **5.6 同步 spec C 三件套（4 处）**：
    - `.kiro/specs/algorithm-redesign/requirements.md`：grep 替换 `backend/agent/legacy/ils_planner.py` → `backend/agent/planning/planners/ils_planner.py`；删除 R3+R4 中「FROZEN 模块」「legacy 路径」字样
    - `.kiro/specs/algorithm-redesign/design.md`：§Components 2 段重写——「在 `planning/planners/ils_planner.py` 加 _grounding_filter_poi / _grounding_filter_restaurant」（删除「FROZEN 模块允许加新过滤函数」措辞）
    - `.kiro/specs/algorithm-redesign/tasks.md`：task 4 + task 5 + task 8 同步 ils_planner 路径锚点（grep `agent/legacy/ils_planner.py` 替换为 `agent/planning/planners/ils_planner.py`）
    - 跑 `getDiagnostics` 验证 spec C 三件套无 Error 级别 diagnostic
  - **5.7 文档同步**：
    - 在 `docs/03-implementation/pitfalls.md` 追加 1 条 [P0] 防再犯条款（按 R5.4 模板）："目录重组前必须 grep 真实引用——spec B 没盘点引用就把 8 个文件全甩进 legacy/，导致 4 个非死代码（含 main.py 真链路核心 planner_rule.py）被错误冻结。防再犯：任何重组 spec 启动前先用 grep 列每个待迁移模块的引用方，明确「主路径 / 活 fallback / 死代码」三类，每类去不同子目录"
    - 在 `docs/00-overview/progress.md` 追加 `D-LEGACY-CLEANUP [日期]：删除误导的 legacy/ 目录——3 个真死代码删除（llm_first_planner / llm_planner / executor），4 个非死代码迁回 planning/planners/ + planning/critic/。spec B 的 5 子目录骨架保留；冻结纪律改为按 graph/build.py 拓扑稳定（而非按文件位置）`
    - 在 `problem.md` 追加本次记录（按全局 problem.md 格式）
  - **5.8 完整端到端验证**：
    - 跑 `pytest backend/tests/ -v --tb=short` 必须 0 红灯（与 task 1 baseline 一致 + 改造的 3 个测试全过 + 新增 import_paths 反向断言全过）
    - 跑 `python backend/scripts/verify_planning.py` + `verify_planning_quality.py`（spec A R10）+ `verify_edge_model.py` 必须全绿
    - 启动 `python -m backend.main &` + `curl /health` 必须 200
    - `python -c "from backend.main import app; print('ok')"` 必须输出 ok（验证 main.py:1740 改 import 后启动 OK）
    - `cd frontend && pnpm verify:all` 必须 0 红灯
  - **5.9 一次性原子 commit**：
    - `git status --short` 列出所有变更
    - `git diff --cached --stat` 复核 stage 范围
    - `git add -A`（包含新 planners/ + 删除 legacy/ + AGENTS.md + spec C 三件套 + pitfalls/progress/problem 同步）但**不**带 untracked 杂物
    - `git commit -m "refactor(spec-d): 删除误导的 legacy/ 目录——3 个真死代码删除 + 4 个非死代码迁回 planning/planners/ + planning/critic/ + AGENTS.md/spec C/pitfalls 同步"`
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
- Task 1（baseline）必须最先做；Task 5（改造测试 + 同步 + commit）必须最后做

## Notes

- **smartRelocate 用法**：与 spec B 完全一致，sourcePath / destinationPath 都用绝对路径（带 `d:\桌面\美团AI Hackathon\` 前缀）；工具自动跟随 import 更新
- **改名场景**：仅 1 处改名（planner_rule.py → rule_planner.py）；smartRelocate 自动处理位置改变 + 文件名改变，类/函数名不变
- **测试改造的等价性验证**：execute_finalize_node 与 executor.execute_plan 行为等价（都做 reserve + share + narration），spec A R6 已验证；本 spec 只动测试 import 不动业务逻辑
- **失败处理协议**：任何 task 末尾 pytest 红灯立即停 + 报告，**禁止跨 task 修复**
- **commit 策略**：5 个 task 中途绝不 commit，全部完成 + 验收通过后一次性原子 commit；如某 task 失败决定回滚，跑 `git restore .` 撤销本批次
- **AGENTS.md 改动边界**：仅改 §3.3.1 编排冻结纪律段；不改 MUST / MUST NOT 其他条款；保留「不动 graph/build.py 拓扑」（与 spec B 一致）
- **codesee features.json 不手动改**：触发条件是代码改动后 codesee sync 流程自动跑

## Risk & Mitigation

```text
| 风险                                              | 概率 | 影响 | 缓解                                                |
|---------------------------------------------------|------|-----|---------------------------------------------------|
| baseline 阶段发现 spec A/B 红灯                    | 低   | 高  | 立即停止 spec D；先修红灯                            |
| smartRelocate 漏改某个相对引用                    | 中   | 中  | 分 task 推进 + 每 task pytest -x 立即定位             |
| 改名（planner_rule→rule_planner）破断字符串 import| 低   | 中  | grep 全仓库 importlib.import_module / __import__    |
| executor.py 删除后测试改造行为不等价              | 中   | 高  | task 5 单独验证 test_agent_flow + test_8_scenarios  |
| backend/main.py:1740 改后真 LLM 链路启动失败       | 低   | 高  | task 5 必跑 `python -m backend.main` + curl /health |
| AGENTS.md §3.3.1 漏改某条 MUST NOT                | 中   | 低  | task 5 grep「legacy」「FROZEN」全检（4 处）         |
| spec C tasks.md 锚点同步遗漏                       | 中   | 中  | task 5 用 grep + str_replace 批量替换            |
| commit 中途出现导致回滚困难                       | 低   | 低  | task 5 末才 commit；前 4 task 失败 git restore .   |
| 改 main.py:1740 时漏改其他位置                     | 低   | 中  | smartRelocate 自动改全仓库 + task 5 grep            |
| 5 个 collab/replan 引用 smartRelocate 跟不上       | 低   | 高  | task 3 每次迁移后跑全部测试；如失败立即停           |
```

## 启动检查清单（task 1 前必须满足）

```text
| #  | 检查项                                          | 验证方法                                              |
|----|-----------------------------------------------|------------------------------------------------------|
| C1 | spec A 全部 8 task 完成                         | 读 .kiro/specs/planning-quality-deep-review/tasks.md，全 [x] |
| C2 | spec B 全部 8 task 完成                         | 读 .kiro/specs/agent-directory-restructure/tasks.md，全 [x] |
| C3 | spec C 三件套已落库（commit 9224284）但未启动    | 读 .kiro/specs/algorithm-redesign/tasks.md，全部 [ ] 未勾选 |
| C4 | git tag v-spec-c-start 暂未打                   | 不允许 spec C 比 spec D 先启动                       |
| C5 | 用户人工确认"可以启动 spec D"                   | 用户消息明确允许                                      |
```

只有 C1-C5 全满足才能开始 task 1。否则立即停止，等待用户。

## Out of Scope（再次确认）

```text
| 不做的事                                       | 理由                                          |
|-----------------------------------------------|----------------------------------------------|
| 重组 graph/ / runtime/ / intent/ 子目录         | spec B 锁                                    |
| 改 graph/build.py 拓扑                          | spec B 锁；编排冻结纪律                       |
| 改业务行为 / prompt / schema                    | 本 spec 仅动文件位置 + 删死代码 + 改测试 import|
| 把 ils_score_critic 与 critics_v2 合并            | 维度不同（候选打分 vs 全局校验）              |
| 改前端目录                                      | 本 spec 仅后端                                |
| 改 backend/api/ 业务行为                        | 本 spec 仅改 import 路径                      |
| 加 meta_critic_node                            | 留 spec C 之后评估                            |
| 删 weights_llm.py 顶部 # FROZEN 注释            | 留 spec C R4 落地后评估                       |
| 重写 AGENTS.md 其他段落                         | 本 spec 仅改 §3.3.1                          |
| 重写 spec A / spec B 三件套                     | 它们已落地；本 spec 不动                       |
| 改前端 ChatDock / ToolTracePanel                | 留 spec C R6                                |
```
