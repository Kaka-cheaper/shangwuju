# Requirements Document

## Glossary

- **smartRelocate**：Kiro 工具，移动文件 + 自动更新所有 import 引用
- **legacy/**：本 spec 新建的子目录，存放冻结模块（FROZEN：仅 fallback / safety-net 用）
- **FROZEN 标记**：文件顶部 `# FROZEN: ...` 注释，标识冻结模块
- **spec A**：`planning-quality-deep-review` spec（业务质量主线，本 spec 的前置）
- **spec B**：本 spec（`agent-directory-restructure`，目录重组）
- **批次**：6 批 smartRelocate + pytest 验证组合
- **D9 编排冻结纪律**：AGENTS.md §3.3.1 规定的「graph/ 主路径 / 其余 fallback / 不动 build.py 拓扑」纪律

## Introduction

`backend/agent/` 目录在三个版本的演进中已经混乱失控：根目录（25 个 `.py`，含 `planner.py` / `planner_hybrid.py` / `planner_llm_first.py` / `llm_planner.py` 四套 planner、`critics.py` / `v2/critics_v2.py` 两套 critic、`executor.py` / `graph/nodes/execute_finalize.py` 双轨执行）、`v2/`（Pydantic AI fallback）、`graph/`（LangGraph 主路径）三套并存，新人/AI 接入项目时很难识别"新代码该写在哪、哪些是冻结模块"。

本 spec 把 `backend/agent/` 重组为 5 个职责清晰的子目录（`core/` / `intent/` / `planning/` / `runtime/` / `graph/`）+ 1 个 `legacy/` 显式冻结目录，并通过 Pydantic-AI 风格的 import 路径迁移把所有引用更新到新位置。**重组只动文件位置 + import 路径，不改业务行为**——所有现有测试在重组后必须 0 红灯。

**前置约束**：本 spec 必须在 `planning-quality-deep-review` spec **全部 task 完成 + 联调通过 + demo 验收后**启动（详见 `reports/synthesis/adversarial-review.md` §8.4）。原因：业务质量修复期间会在 `agent/blueprint.py` / `critics_v2.py` / `narrator.py` 等大量改动，重组在中途做会导致 import 路径冲突 + git merge 冲突 + demo 现场 import 错误。**spec A 在前，spec B 在后，时序硬约束**。

**不在本 spec 范围**（重组只是把文件挪位置 + 改路径，**不**做以下任何事）：

- 删除任何 legacy 模块（`planner.py` / `planner_hybrid.py` / `planner_llm_first.py` / `executor.py` 都保留并标 `# FROZEN`，下个 sprint 再删）
- 改业务逻辑、prompt、schema、function 行为
- 改 `graph/build.py` 拓扑（编排冻结纪律 §3.3.1）
- 加新 critic / 新 node / 新 prompt
- 把 `narrator.py` 的"主动质疑"功能搬挪（这属于 spec A 范围）
- 把 `mock_data/` 重组（adversarial-review §2 冲突 5 已拒 v1/v2 子目录）
- 把 `tests/` 目录重组（保持现状，因为 import 路径已被工具自动迁移）

---

## Requirements

### Requirement 1: 5 子目录 + 1 legacy 的最终目录树

**User Story**: As 后续接手项目的开发者/AI Agent, I want 打开 `backend/agent/` 时一眼看出"业务在哪、运行时框架在哪、冻结模块在哪", so that 不需要面对 25 个扁平的 `.py` 文件猜哪个是新代码该改的。

#### Acceptance Criteria

1. WHEN 用户进入 `backend/agent/` THEN 该目录下 SHALL 只包含 `__init__.py` + 6 个子目录（`core/` / `intent/` / `planning/` / `runtime/` / `graph/` / `legacy/`），无任何业务 `.py` 文件直接停在 `agent/` 顶层。

2. WHEN 用户读 `backend/agent/core/` THEN 该目录 SHALL 含且仅含全员共享底座模块：
   - `llm_client.py`（含原 `agent/llm_client.py`）
   - `llm_client_stub.py`（含原 `agent/llm_client_stub.py`）
   - `observability_init.py`（含原 `agent/observability_init.py`）
   - `feedback_detector.py`（含原 `agent/feedback_detector.py`）
   - `trace.py`（含原 `agent/trace.py`）

3. WHEN 用户读 `backend/agent/intent/` THEN 该目录 SHALL 含意图理解 + 反馈刷新 + 文案输出三类模块：
   - `parser.py`（重命名自原 `agent/intent_parser.py`）
   - `refiner.py`（含原 `agent/refiner.py`）
   - `router.py`（含原 `agent/router.py`）
   - `narrator.py`（含原 `agent/narrator.py`，归 intent 因为与 SOCIAL_CONTEXTS 9 选 1 词典强耦合）
   - `prompts/intent_parser_prompt.py`（从原 `agent/prompts/system_prompt.py` 拆出）
   - `prompts/refiner_prompt.py`（含原 `agent/prompts/refiner_prompt.py`）
   - `prompts/router_prompt.py`（含原 `agent/prompts/router_prompt.py`）
   - `prompts/narrator_prompt.py`（含原 `agent/prompts/narrator_prompt.py`）

4. WHEN 用户读 `backend/agent/planning/` THEN 该目录 SHALL 含规划主路径四类模块：
   - `blueprint/blueprint.py`（含原 `agent/blueprint.py`，本目录承载 `_age_aware_duration_critic`——spec A R4 落地后的产物）
   - `blueprint/blueprint_llm.py`（含原 `agent/blueprint_llm.py`，含改后的 `_poi_preview` / `_restaurant_preview`——spec A R2 落地后的产物）
   - `blueprint/assemble_blueprint.py`（含原 `agent/assemble_blueprint.py`）
   - `blueprint/node_decider.py`（含原 `agent/node_decider.py`，**仅决 kind 不决时长**——拒 A 方案 B 升级）
   - `blueprint/prompts/blueprint_prompt.py`（含原 `agent/prompts/blueprint_prompt.py`，含 spec A R3 改后的范例 + 分级表）
   - `critic/critics_v2.py`（含原 `agent/v2/critics_v2.py`，含 `AGE_DURATION_MISMATCH` 镜像——spec A R4 落地后的产物）
   - `critic/social_compat.py`（含原 `agent/v2/social_compat.py`）
   - `commute/lookup_hop.py`（含原 `agent/lookup_hop.py`）
   - `weights_llm.py`（含原 `agent/weights_llm.py`，文件顶部加 `# FROZEN: 仅 ILS 路径`）

5. WHEN 用户读 `backend/agent/runtime/` THEN 该目录 SHALL 含 Pydantic AI ReAct 运行时框架（不是业务）：
   - `react_agent.py`（含原 `agent/v2/react_agent.py`）
   - `output_types.py`（含原 `agent/v2/output_types.py`）
   - `orchestrator.py`（含原 `agent/v2/orchestrator.py`）
   - `conversation.py`（含原 `agent/v2/conversation.py`）
   - `tool_provider.py`（含原 `agent/v2/tool_provider.py`）
   - `deps.py`（含原 `agent/v2/deps.py`）
   - `model_factory.py`（含原 `agent/v2/model_factory.py`）
   - `observability.py`（含原 `agent/v2/observability.py`，与 `core/observability_init.py` 不同——`v2/observability.py` 是 Pydantic AI 专用）
   - `tools/search_adapter.py`（含原 `agent/tools/search_adapter.py`）

6. WHEN 用户读 `backend/agent/graph/` THEN 该目录拓扑 SHALL 与重组前完全一致（不动 `build.py` / `nodes/` / `state.py` / `sse_adapter.py`），仅在 `nodes/` 内部按 spec A 已落地的内容（如 spec A R6 的 `narrate_node` 改动）保持现状。

7. WHEN 用户读 `backend/agent/legacy/` THEN 该目录 SHALL 含 7 个被冻结的模块（每个文件顶部 SHALL 加 `# FROZEN: 详见 AGENTS.md §3.3.1，仅 fallback / safety-net，不改业务`）：
   - `planner_rule.py`（重命名自原 `agent/planner.py`）
   - `ils_planner.py`（重命名自原 `agent/planner_hybrid.py`）
   - `llm_first_planner.py`（重命名自原 `agent/planner_llm_first.py`）
   - `llm_planner.py`（含原 `agent/llm_planner.py`）
   - `ils_score_critic.py`（重命名自原 `agent/critics.py`）
   - `executor.py`（含原 `agent/executor.py`，与 `graph/nodes/execute_finalize.py` 双轨，docstring 标"已被 graph 路径替代"）
   - `segment_decider.py`（含原 `agent/segment_decider.py`，作为兼容 alias，下次 spec 删）

8. WHEN 用户对比重组前后 THEN `backend/agent/v2/` / `backend/agent/tools/` / `backend/agent/prompts/` 目录 SHALL 不再存在（其内容已被分拆到 `runtime/` / `runtime/tools/` / `intent/prompts/` + `planning/blueprint/prompts/`）。

---

### Requirement 2: import 路径迁移 0 错误

**User Story**: As 开发者, I want 重组完成后所有 `from backend.agent.xxx import yyy` 风格的引用都自动更新到新路径, so that 跑全套 pytest 0 红灯，不需要手工修复边角 import。

#### Acceptance Criteria

1. WHEN 重组的每一步文件移动 THEN SHALL 使用 Kiro 的 `smartRelocate` 工具（自动更新所有 import 引用），不使用手工 `git mv` + `sed`（手工易漏边角 import）。

2. WHEN 重组完成 THEN 全仓库下列 import 模式 SHALL 不再出现（`grep -r 'from backend\.agent\.<旧路径>'` 必须 0 命中）：
   - `from backend.agent.intent_parser import` → 应改为 `from backend.agent.intent.parser import`
   - `from backend.agent.refiner import` → `from backend.agent.intent.refiner import`
   - `from backend.agent.router import` → `from backend.agent.intent.router import`
   - `from backend.agent.narrator import` → `from backend.agent.intent.narrator import`
   - `from backend.agent.blueprint import` → `from backend.agent.planning.blueprint.blueprint import`
   - `from backend.agent.blueprint_llm import` → `from backend.agent.planning.blueprint.blueprint_llm import`
   - `from backend.agent.assemble_blueprint import` → `from backend.agent.planning.blueprint.assemble_blueprint import`
   - `from backend.agent.node_decider import` → `from backend.agent.planning.blueprint.node_decider import`
   - `from backend.agent.lookup_hop import` → `from backend.agent.planning.commute.lookup_hop import`
   - `from backend.agent.weights_llm import` → `from backend.agent.planning.weights_llm import`
   - `from backend.agent.llm_client import` → `from backend.agent.core.llm_client import`
   - `from backend.agent.llm_client_stub import` → `from backend.agent.core.llm_client_stub import`
   - `from backend.agent.observability_init import` → `from backend.agent.core.observability_init import`
   - `from backend.agent.feedback_detector import` → `from backend.agent.core.feedback_detector import`
   - `from backend.agent.trace import` → `from backend.agent.core.trace import`
   - `from backend.agent.v2.react_agent import` → `from backend.agent.runtime.react_agent import`
   - `from backend.agent.v2.critics_v2 import` → `from backend.agent.planning.critic.critics_v2 import`
   - `from backend.agent.v2.social_compat import` → `from backend.agent.planning.critic.social_compat import`
   - `from backend.agent.tools.search_adapter import` → `from backend.agent.runtime.tools.search_adapter import`
   - `from backend.agent.prompts.system_prompt import` → `from backend.agent.intent.prompts.intent_parser_prompt import`
   - `from backend.agent.prompts.refiner_prompt import` → `from backend.agent.intent.prompts.refiner_prompt import`
   - `from backend.agent.prompts.router_prompt import` → `from backend.agent.intent.prompts.router_prompt import`
   - `from backend.agent.prompts.narrator_prompt import` → `from backend.agent.intent.prompts.narrator_prompt import`
   - `from backend.agent.prompts.blueprint_prompt import` → `from backend.agent.planning.blueprint.prompts.blueprint_prompt import`
   - `from backend.agent.planner import` → `from backend.agent.legacy.planner_rule import`
   - `from backend.agent.planner_hybrid import` → `from backend.agent.legacy.ils_planner import`
   - `from backend.agent.planner_llm_first import` → `from backend.agent.legacy.llm_first_planner import`
   - `from backend.agent.llm_planner import` → `from backend.agent.legacy.llm_planner import`
   - `from backend.agent.critics import` → `from backend.agent.legacy.ils_score_critic import`
   - `from backend.agent.executor import` → `from backend.agent.legacy.executor import`
   - `from backend.agent.segment_decider import` → `from backend.agent.legacy.segment_decider import`

3. WHEN 重组完成 THEN `backend/agent/__init__.py` SHALL 保持解锁状态（不重新加 eager-import），但若有少数公开 API 需要稳定（如 `from backend.agent import LLMClient`）则在 `__init__.py` 内显式列出 ≤ 5 个 re-export。

4. WHEN 重组完成 THEN 完整 pytest 套（含 `tests/` + `backend/scripts/verify_*.py`）SHALL 0 红灯，与重组前完全一致；新增的 import 路径迁移测试 SHALL 在 `tests/test_import_paths.py` 中显式断言 ≥ 10 条新路径可 import。

5. WHEN 重组完成 THEN `backend/main.py` 与 `backend/api/` 下的 router/endpoint 模块 SHALL import 路径完全更新，FastAPI 启动 0 错误（跑 `python -m backend.main` 启动后 `/health` 返回 200）。

---

### Requirement 3: legacy/ 模块的冻结纪律可机器验证

**User Story**: As 后续 AI Agent, I want 修代码前能立即识别"哪些文件是被冻结的、不能加新功能", so that 避免误改 legacy 模块违反 §3.3.1 编排冻结纪律。

#### Acceptance Criteria

1. WHEN 重组完成 THEN `backend/agent/legacy/` 下的每个 `.py` 文件第一行（紧跟 docstring 后）SHALL 含 `# FROZEN: 详见 AGENTS.md §3.3.1，仅 fallback / safety-net，不改业务` 注释。

2. WHEN 重组完成 THEN `backend/agent/legacy/__init__.py` SHALL 含一段 docstring 说明本目录的冻结纪律（含 `# FROZEN` 标记说明 + "新功能只在 graph/ 加" 的引用）。

3. WHEN 重组完成 THEN `AGENTS.md §3.3.1` SHALL 同步更新——把原"agent/v2/" / "agent/planner*.py" / "agent/llm_planner.py"路径替换为新路径："agent/legacy/ils_planner.py" / "agent/legacy/planner_rule.py" / "agent/legacy/llm_first_planner.py" / "agent/legacy/llm_planner.py" / "agent/legacy/ils_score_critic.py" / "agent/legacy/executor.py" / "agent/legacy/segment_decider.py" 全列出。

4. WHEN 重组完成 THEN `backend/scripts/verify_legacy_frozen.py` SHALL 存在且可运行（grep `legacy/` 下所有 `.py` 是否含 `# FROZEN` 注释，0 漏即 exit 0），用于 CI / 防再犯。

5. WHEN 重组完成 THEN `weights_llm.py` 文件顶部 SHALL 加 `# FROZEN: 仅 ILS 路径，不被 graph 路径消费` 注释（adversarial-review §6 要求）。

---

### Requirement 4: 重组分批 + 可回滚

**User Story**: As 执行重组的 AI Agent, I want 分多个小批次（每批 1-3 个文件）做 smartRelocate + 跑测试, so that 避免一次大批量 mv 后某个 import 漏掉、定位变难，并具备每批次回滚能力。

#### Acceptance Criteria

1. WHEN 执行重组 THEN SHALL 按 6 批次推进（每批结束跑一次完整 pytest 验证）：
   - **批 1：core/**（5 个文件移动）→ 跑 pytest 必须 0 红灯
   - **批 2：intent/ + intent/prompts/**（4 个 .py + 4 个 prompts/.py 移动）→ 跑 pytest 必须 0 红灯
   - **批 3：planning/blueprint/ + planning/blueprint/prompts/ + planning/critic/ + planning/commute/ + planning/weights_llm.py**（4 + 1 + 2 + 1 + 1 = 9 个文件移动）→ 跑 pytest 必须 0 红灯
   - **批 4：runtime/ + runtime/tools/**（8 个 .py + 1 个 tools/.py 移动）→ 跑 pytest 必须 0 红灯
   - **批 5：legacy/**（7 个 .py 移动 + 4 个改名）→ 跑 pytest 必须 0 红灯
   - **批 6：清理空目录 + AGENTS.md 更新 + verify_legacy_frozen.py 验收脚本**→ 跑全套 verify_*.py 必须 0 红灯

2. WHEN 任意批次结束 pytest 出现红灯 THEN AI Agent SHALL 立即停止后续批次、输出失败的测试名 + 命中的旧 import 路径，由用户决定回滚或继续修复。**禁止跨批次修复**（一批修不完不跨批）。

3. WHEN 任意批次执行后 THEN SHALL 立即用 `git status` 列出本批次改动的文件清单（用户用于人工核对）。

4. WHEN 全部 6 批次完成 THEN SHALL 跑一次完整端到端验证：
   - `pytest backend/tests/ -v --tb=short`（断言全部通过）
   - `python backend/scripts/verify_planning.py`（断言 0 红灯）
   - `python backend/scripts/verify_edge_model.py`（断言 4/4 场景过）
   - `python -m backend.main` 启动 + `curl http://localhost:8000/health` 返回 200
   - `npm run build`（前端构建不依赖后端 import 路径，但确认 0 红灯）

5. WHEN 全部 6 批次完成 THEN SHALL 自动 git commit，message 为 `refactor(agent): restructure into 5 subdirs + legacy/ for clarity`，**不**在重组中途 commit（一次原子提交便于回滚）。

---

### Requirement 5: 文档同步

**User Story**: As 读 AGENTS.md / progress.md 的人类/AI, I want 重组后文档里的目录树/路径示例都同步更新, so that 避免"文档说在 v2 下，代码已经挪到 runtime/ 下"的精神分裂。

#### Acceptance Criteria

1. WHEN 重组完成 THEN `AGENTS.md §3.3.1` 的目录树代码块 SHALL 替换为新结构（5 子目录 + legacy/），并保留"编排冻结纪律"含义不变。

2. WHEN 重组完成 THEN `docs/00-overview/progress.md` SHALL 在「决策记录」段追加一条 `D-AGENT-RESTRUCTURE [2026-05-XX]：agent/ 目录从 v2/+graph/+ 根目录三套并存重组为 core/ + intent/ + planning/ + runtime/ + graph/ + legacy/`，含一句话理由（"新人/AI 接入项目时无法识别新代码该写在哪"）。

3. WHEN 重组完成 THEN `docs/03-implementation/pitfalls.md` SHALL 追加一条 `[P1] 2026-05-XX：agent/ 目录重组——防再犯：新加 .py 文件前先看 AGENTS.md §3.3.1 目录树，明确归属；不允许在 agent/ 顶层直接加 .py（含 except `__init__.py`）；不允许在 legacy/ 加新功能；新加节点须在 graph/nodes/ 下`。

4. WHEN 重组完成 THEN `problem.md` SHALL 追加一条记录（按全局 steering 要求的格式）。

5. WHEN 重组完成 THEN `.codesee/features.json` 受影响的 feature 的 `refs[].file` 路径 SHALL 自动更新（通过 codesee sync 流程，本 spec 不手动改）。

---

## Out of Scope（明确不做）

```text
| 不做的事                                       | 理由                                          | 何时再做                  |
|-----------------------------------------------|----------------------------------------------|---------------------------|
| 删除任何 legacy 模块                           | 仍是 fallback 链路依赖项                      | spec C 或下个 sprint      |
| 改业务行为（含 prompt / schema / function）    | 重组只动位置，本 spec 范围严格限定            | 已在 spec A 中处理        |
| 改 graph/build.py 拓扑                         | 编排冻结纪律 §3.3.1 + adversarial §6.3       | 不改                      |
| 加 meta_critic_node                           | 留 spec C，本 spec 不收（adversarial §8.3）  | spec C                    |
| mock_data/v2/ 子目录                          | adversarial §2 冲突 5 已拒                    | 不做                      |
| 把 narrator 主动质疑搬挪                       | spec A R6 已处理，重组只是把文件移到 intent/ | 不做                      |
| tests/ 目录重组                                | 保留扁平结构（import 路径已自动迁移）         | 不做                      |
| weights_llm.py 拆解                            | 当前文件仍单一职责（ILS 权重）                | 下次 spec                 |
```

---

## 前置条件 / 时序硬约束

**本 spec 必须在 `planning-quality-deep-review` spec 全部 8 task 完成 + e2e 验收通过 + demo 现场跑通 5 岁娃场景后启动**。

**理由**（来自 `adversarial-review.md` §8.4 + §6 末尾段）：

- spec A 期间会大量改动 `agent/blueprint.py` / `critics_v2.py` / `narrator.py` / `prompts/*.py`——重组在中途做会导致 spec A 的 PR 全部 import 路径冲突
- 重组后所有 import 路径变化，demo 现场任何遗漏都会导致评委演示翻车——必须在业务质量稳定后做
- adversarial §6 末尾："hackathon 时间盒下，质量修复后再做重组——不要在评委面前因为大量 import 路径变化而 demo 翻车"
- adversarial §8.4 spec 拆分建议："spec A（业务质量）在前，spec B（目录重组）在 spec A 全部落地 + 联调通过后启动"

**启动检查清单**（以下条件全满足才能启动 spec B 的实施）：

- [ ] spec A 的 8 个 task 全部 `[x]` 完成
- [ ] `backend/scripts/verify_planning_quality.py` 跑过 5-10 次，前三档命中率 ≥ 95%
- [ ] demo 现场（或本地完整 e2e）跑通 5 岁娃 + 老人 + 独处 + 商务 4 个场景
- [ ] git tag `v-spec-a-done` 打上（用于 spec B 出问题时回滚）
- [ ] 用户人工确认 "可以启动 spec B"
