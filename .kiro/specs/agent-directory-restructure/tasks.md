# Implementation Plan: Agent Directory Restructure

## Overview

把 `backend/agent/` 重组为 5 子目录（`core/` / `intent/` / `planning/` / `runtime/` / `graph/`）+ 1 `legacy/`。**只动位置 + import 路径，不改业务**。

**总工时**：~4h（含 pytest 缓冲），分 8 个 task / 6 批次。

**前置硬约束**：spec A `planning-quality-deep-review` 全部 8 task 完成 + e2e 验收通过 + 用户人工确认后才能启动本 spec。

**关键工具**：Kiro `smartRelocate`（自动跟随式 import 更新）。

**核心原则**：
- 每批次结束跑 `pytest -x`，红灯立即停（禁止跨批次修复）
- 全部完成后一次性原子 commit（便于回滚）
- 重组前先建 baseline（验证当前是绿的）

## Tasks

- [x] 1. [R2/R4] baseline 验证 + spec A 完成度核查（~0.3h）：跑 `pytest backend/tests/ -v --tb=short` 记录基线（必须全绿，0 红灯，0 错误）；跑 `python backend/scripts/verify_planning.py` + `verify_edge_model.py` 必须全绿；启动 `python -m backend.main &` + `curl http://localhost:8000/health` 必须 200；`grep -r "5 岁" backend/scripts/verify_planning_quality.py` 确认 spec A R10 验收脚本存在；读 `.kiro/specs/planning-quality-deep-review/tasks.md` 确认 8 个 task 全 `[x]`；如有任何不通过 → **立即停止 spec B**，先报告状态，等待用户确认 spec A 完结再继续；如全通过 → 打 git tag `v-spec-a-done`（用于回滚锚点）。

- [x] 2. [R1.2/R2] 批 1：core/ 共享底座（~0.3h）：`smartRelocate` 移动 5 个文件到 `backend/agent/core/`：
  - `backend/agent/llm_client.py` → `backend/agent/core/llm_client.py`
  - `backend/agent/llm_client_stub.py` → `backend/agent/core/llm_client_stub.py`
  - `backend/agent/observability_init.py` → `backend/agent/core/observability_init.py`
  - `backend/agent/feedback_detector.py` → `backend/agent/core/feedback_detector.py`
  - `backend/agent/trace.py` → `backend/agent/core/trace.py`
  
  目标目录由 smartRelocate 自动创建；之后创建 `backend/agent/core/__init__.py`（空文件）；跑 `pytest backend/tests/ -x --tb=short` 必须 exit 0；如失败：立即停 + 跑 `git status` 列出所有变更 + 报告失败测试名 + traceback 命中的旧 import 路径，等待用户决定回滚或继续；如成功：进入下一 task。

- [x] 3. [R1.3/R2] 批 2：intent/ 意图层 + intent/prompts/（~0.6h）：先创建 `backend/agent/intent/__init__.py` 与 `backend/agent/intent/prompts/__init__.py`（空文件，确保 smartRelocate 不报"目标目录不存在"）；之后 `smartRelocate` 移动 8 个文件：
  - `backend/agent/intent_parser.py` → `backend/agent/intent/parser.py`（**改名**）
  - `backend/agent/refiner.py` → `backend/agent/intent/refiner.py`
  - `backend/agent/router.py` → `backend/agent/intent/router.py`
  - `backend/agent/narrator.py` → `backend/agent/intent/narrator.py`
  - `backend/agent/prompts/system_prompt.py` → `backend/agent/intent/prompts/intent_parser_prompt.py`（**改名**）
  - `backend/agent/prompts/refiner_prompt.py` → `backend/agent/intent/prompts/refiner_prompt.py`
  - `backend/agent/prompts/router_prompt.py` → `backend/agent/intent/prompts/router_prompt.py`
  - `backend/agent/prompts/narrator_prompt.py` → `backend/agent/intent/prompts/narrator_prompt.py`
  
  跑 `pytest backend/tests/ -x --tb=short` 必须 exit 0；如失败按 task 2 协议处理。

- [x] 4. [R1.4/R2] 批 3：planning/ 规划主路径（~1.0h，最复杂）：先创建 `backend/agent/planning/__init__.py` / `planning/blueprint/__init__.py` / `planning/blueprint/prompts/__init__.py` / `planning/critic/__init__.py` / `planning/commute/__init__.py`（5 个空文件）；之后 `smartRelocate` 移动 9 个文件：
  - `backend/agent/blueprint.py` → `backend/agent/planning/blueprint/blueprint.py`
  - `backend/agent/blueprint_llm.py` → `backend/agent/planning/blueprint/blueprint_llm.py`
  - `backend/agent/assemble_blueprint.py` → `backend/agent/planning/blueprint/assemble_blueprint.py`
  - `backend/agent/node_decider.py` → `backend/agent/planning/blueprint/node_decider.py`
  - `backend/agent/prompts/blueprint_prompt.py` → `backend/agent/planning/blueprint/prompts/blueprint_prompt.py`
  - `backend/agent/v2/critics_v2.py` → `backend/agent/planning/critic/critics_v2.py`
  - `backend/agent/v2/social_compat.py` → `backend/agent/planning/critic/social_compat.py`
  - `backend/agent/lookup_hop.py` → `backend/agent/planning/commute/lookup_hop.py`
  - `backend/agent/weights_llm.py` → `backend/agent/planning/weights_llm.py`
  
  之后在 `backend/agent/planning/weights_llm.py` 文件顶部（紧跟 docstring 后）加注释 `# FROZEN: 仅 ILS 路径，不被 graph 路径消费`；跑 `pytest backend/tests/ -x --tb=short` 必须 exit 0；如失败按 task 2 协议处理。

- [x] 5. [R1.5/R2] 批 4：runtime/ Pydantic AI 框架 + runtime/tools/（~0.5h）：先创建 `backend/agent/runtime/__init__.py` 与 `backend/agent/runtime/tools/__init__.py`（空文件）；之后 `smartRelocate` 移动 9 个文件：
  - `backend/agent/v2/react_agent.py` → `backend/agent/runtime/react_agent.py`
  - `backend/agent/v2/output_types.py` → `backend/agent/runtime/output_types.py`
  - `backend/agent/v2/orchestrator.py` → `backend/agent/runtime/orchestrator.py`
  - `backend/agent/v2/conversation.py` → `backend/agent/runtime/conversation.py`
  - `backend/agent/v2/tool_provider.py` → `backend/agent/runtime/tool_provider.py`
  - `backend/agent/v2/deps.py` → `backend/agent/runtime/deps.py`
  - `backend/agent/v2/model_factory.py` → `backend/agent/runtime/model_factory.py`
  - `backend/agent/v2/observability.py` → `backend/agent/runtime/observability.py`
  - `backend/agent/tools/search_adapter.py` → `backend/agent/runtime/tools/search_adapter.py`
  
  跑 `pytest backend/tests/ -x --tb=short` 必须 exit 0；之后用 `list_directory` 确认 `backend/agent/v2/` 与 `backend/agent/tools/` 已空（只剩可选的 `__pycache__/`）；如未空：手动检查是否有遗漏文件，报告并等待用户决定。

- [x] 6. [R1.7/R2/R3] 批 5：legacy/ 冻结模块 + FROZEN 标记（~0.5h）：先创建 `backend/agent/legacy/__init__.py`，写入 docstring（按 design.md §Components 提供的设计稿）；之后 `smartRelocate` 移动 7 个文件：
  - `backend/agent/planner.py` → `backend/agent/legacy/planner_rule.py`（**改名**）
  - `backend/agent/planner_hybrid.py` → `backend/agent/legacy/ils_planner.py`（**改名**）
  - `backend/agent/planner_llm_first.py` → `backend/agent/legacy/llm_first_planner.py`（**改名**）
  - `backend/agent/llm_planner.py` → `backend/agent/legacy/llm_planner.py`
  - `backend/agent/critics.py` → `backend/agent/legacy/ils_score_critic.py`（**改名**）
  - `backend/agent/executor.py` → `backend/agent/legacy/executor.py`
  - `backend/agent/segment_decider.py` → `backend/agent/legacy/segment_decider.py`
  
  之后在每个 legacy/ 下 `.py` 文件顶部（紧跟 docstring 后）加注释 `# FROZEN: 详见 AGENTS.md §3.3.1，仅 fallback / safety-net，不改业务`（共 7 处加注释）；`executor.py` 额外在 module docstring 末尾加一行"已被 graph/nodes/execute_finalize.py 替代"；跑 `pytest backend/tests/ -x --tb=short` 必须 exit 0。

- [x] 7. [R2.5/R3.4/R5] 批 6：清理 + AGENTS.md + verify 脚本 + 文档（~0.8h，含文档同步）：
  - 删除空目录 `backend/agent/v2/` 和 `backend/agent/tools/` 和 `backend/agent/prompts/`（用 `Remove-Item` 含 `__pycache__`），但保留 `backend/agent/__init__.py`
  - `list_directory backend/agent/` 应仅含 `__init__.py` + 6 个子目录（`core/` / `intent/` / `planning/` / `runtime/` / `graph/` / `legacy/`），无任何顶层 `.py` 文件
  - 新建 `backend/scripts/verify_legacy_frozen.py`（按 design.md §Components 提供的设计稿，grep `legacy/` 下所有 .py 是否含 `# FROZEN` 注释）；跑 `python backend/scripts/verify_legacy_frozen.py` 必须 exit 0
  - 新建 `backend/tests/test_import_paths.py`（按 design.md §Testing Strategy §4 提供的设计稿，含 6 个测试函数：test_core_imports / test_intent_imports / test_planning_imports / test_runtime_imports / test_legacy_imports / test_old_paths_no_longer_exist）；跑 `pytest backend/tests/test_import_paths.py -v` 必须全绿
  - 跑 grep 验证旧路径不再被引用：`grep -rn "from backend\.agent\.intent_parser\|from backend\.agent\.blueprint\b\|from backend\.agent\.planner\b\|from backend\.agent\.v2\.\|from backend\.agent\.tools\." backend/ --include="*.py"` 必须 0 命中（注意 `\b` 边界避免误伤 `from backend.agent.planning.blueprint`）
  - 更新 `AGENTS.md §3.3.1` 编排冻结纪律段：把目录树代码块替换为新结构（含 `agent/legacy/planner_rule.py` / `agent/legacy/ils_planner.py` / `agent/legacy/llm_first_planner.py` / `agent/legacy/llm_planner.py` / `agent/legacy/ils_score_critic.py` / `agent/legacy/executor.py` / `agent/legacy/segment_decider.py` 全列出）；保留"编排冻结纪律"含义不变；保留 MUST/MUST NOT 段不动

- [x] 8. [R2.4/R4.4/R5] 全套验收 + 一次性 commit（~0.5h）：
  - **完整后端测试**：`pytest backend/tests/ -v --tb=short` 必须 exit 0（与 baseline 一致）；如有失败立即停 + 报告
  - **verify 脚本套**：`python backend/scripts/verify_planning.py` + `verify_edge_model.py` + `verify_legacy_frozen.py` 三个脚本必须全绿
  - **FastAPI 启动**：`python -m backend.main` 后台启动 + `curl -s http://localhost:8000/health` 必须返回 200；启动 `python -c "from backend.main import app; print('ok')"` 直接验 import
  - **前端构建**：`cd frontend && npm run build` 必须 exit 0（理论上前端不依赖后端，但作为最后一道防线）
  - **文档同步**：在 `docs/00-overview/progress.md` 决策记录段追加 `D-AGENT-RESTRUCTURE [2026-05-XX]：agent/ 目录重组——5 子目录 + legacy/，业务行为零变化`，含一句话理由；在 `docs/03-implementation/pitfalls.md` 追加 `[P1] 2026-05-XX：agent/ 目录重组——防再犯：新加 .py 前先看 AGENTS.md §3.3.1 目录树明确归属；不允许在 agent/ 顶层加 .py（除 __init__.py）；不允许在 legacy/ 加新功能；新加节点须在 graph/nodes/ 下`；在 `problem.md` 追加本次 spec 的记录（按全局 steering 格式）
  - **一次性原子 commit**：`git add -A && git commit -m "refactor(agent): restructure into 5 subdirs + legacy/ for clarity"`（**不**在中途 commit）；之后用 `git log -1 --stat` 确认 commit 包含全部本次变更
  - **触发 codesee sync**（可选）：完成后告诉用户 codesee features.json 的 refs 路径已被 codesee sync 流程自动更新，无需手工

## Task Dependency Graph

```json
{
  "waves": [
    [1],
    [2],
    [3],
    [4],
    [5],
    [6],
    [7],
    [8]
  ]
}
```

说明：
- 本 spec 是**严格串行**——每批次必须等上一批次 pytest 全绿才能进入下一批次（避免 import 路径冲突累积）
- 不存在可并行的 task（与 spec A 不同，spec A 是同一架构内修业务，spec B 是动文件位置，串行更安全）
- Task 1（baseline）必须最先做；Task 8（验收 + commit）必须最后做

## Notes

- **smartRelocate 用法**：调用时 `sourcePath` 与 `destinationPath` 都用绝对路径（带 `d:\桌面\美团AI Hackathon\` 前缀），工具会自动更新所有 `from backend.agent.<旧>` 引用为 `from backend.agent.<新>`，包括相对引用 `from . import xxx`
- **改名场景**：5 个文件改名（intent_parser.py → parser.py / planner.py → planner_rule.py / planner_hybrid.py → ils_planner.py / planner_llm_first.py → llm_first_planner.py / critics.py → ils_score_critic.py / system_prompt.py → intent_parser_prompt.py）。smartRelocate 同时处理位置改变 + 文件名改变，所有 `from backend.agent.intent_parser import IntentParser` 自动变成 `from backend.agent.intent.parser import IntentParser`（**类名不变**）
- **__init__.py 在 smartRelocate 前先建空文件**：避免 smartRelocate 报"目标目录不是包"
- **失败处理协议**：任何批次 pytest 红灯立即停 + 报告，**禁止跨批次修复**——批 2 红灯不允许在批 3 里"顺手"修
- **commit 策略**：6 批次中途绝不 commit，全部完成 + 验收通过后一次性 commit。如果某批次失败决定回滚，跑 `git restore .` 撤销本批次（前面已通过的批次也一起回滚——本 spec 无中间 commit）
- **AGENTS.md §3.3.1 更新边界**：只更新「目录树代码块 + 路径列表」，**不**改 MUST / MUST NOT 段语义（编排冻结纪律含义不变）
- **codesee features.json 不手动改**：触发条件是代码改动后 codesee sync 流程自动跑（adversarial-review 后续 codesee sync 触发）

## Risk & Mitigation

```text
| 风险                                              | 概率 | 影响 | 缓解                                                   |
|---------------------------------------------------|------|-----|--------------------------------------------------------|
| smartRelocate 漏改某个相对引用                    | 中   | 中  | 分 6 批次 + 每批次 pytest -x 立即定位                 |
| FastAPI 启动 import 错误（pytest 不发现）          | 中   | 中  | task 8 含 curl /health 验证                           |
| 改名（intent_parser→parser）破断字符串 import     | 低   | 中  | task 7 grep importlib.import_module / __import__       |
| spec A 未完成就启动 spec B                        | 低   | 高  | task 1 含完成度核查 + git tag 锚点                    |
| commit 中途出现，回滚困难                         | 低   | 低  | task 8 末才 commit，禁止中途提交                       |
| AGENTS.md §3.3.1 路径漂移（spec B 改完没同步）    | 中   | 低  | task 7 同步更新 + task 8 验收清单含 grep              |
| 前端 .env / 配置硬编码后端路径                    | 极低 | 低  | task 8 含 npm run build 验证                          |
| backend/api/ 下 router import 路径漏改             | 中   | 中  | smartRelocate 自动更新 + task 8 启动 FastAPI 验证      |
```

## 启动检查清单（task 1 前必须满足）

```text
| #  | 检查项                                          | 验证方法                                              |
|----|-----------------------------------------------|------------------------------------------------------|
| C1 | spec A 全部 8 task 完成                         | 读 .kiro/specs/planning-quality-deep-review/tasks.md，全 [x] |
| C2 | spec A e2e 验收脚本通过                         | 跑 backend/scripts/verify_planning_quality.py，前三档 ≥ 95% |
| C3 | spec A demo 现场跑通 5 岁娃 + 老人 + 独处 + 商务 | 用户人工演示确认                                      |
| C4 | git tag v-spec-a-done 已打                      | git tag --list 应见 v-spec-a-done                    |
| C5 | 用户人工确认 "可以启动 spec B"                   | 用户消息明确允许                                      |
```

只有 C1-C5 全满足才能开始 task 1。否则立即停止，等待用户。
