# Design Document: Legacy Cleanup and Honest Naming (spec D)

> **范围**：删除 spec B 留下的误导性 `legacy/` 目录 → 解构为「死代码（删）+ 主路径活代码（迁回）+ 活 fallback（迁回）」三类
> **语言**：Python 3.11（后端，仅改 import 路径 + 文件位置 + 删 3 个文件）
> **项目模式**：hackathon + demo 阶段；本 spec 是 spec B 的修正性重构，spec C 实施前的前置 spec
> **规模**：~2-3h，分 5 个 task / 5 wave；不改业务行为，只动位置 + 删死代码 + 改路径
> **现状**：spec A + spec B 已落地；spec C 三件套已写但未实施；本 spec 在 spec B 6 子目录骨架上扩展 `planning/planners/` 子目录 + 删 `legacy/`
> **绝对约束**：保留 spec B 的 5 子目录骨架（core/ + intent/ + planning/ + runtime/ + graph/）；保留 graph/build.py 拓扑（spec B 锁）；只动 legacy/ 内 8 文件 + 1 prompts 子目录 + 4 处 spec C 文档锚点

## Overview

把 spec B 落地的 `legacy/` 子目录解构为「**真定位明确**的三类」：

```text
| 旧位置                              | 真实定位             | 新位置                                          | 操作        |
|------------------------------------|--------------------|----------------------------------------------|-------------|
| legacy/planner_rule.py              | 主路径活代码         | planning/planners/rule_planner.py            | smartRelocate + 改名 |
| legacy/ils_planner.py               | 活的 fallback        | planning/planners/ils_planner.py             | smartRelocate |
| legacy/segment_decider.py           | ils_planner 依赖     | planning/planners/segment_decider.py         | smartRelocate |
| legacy/ils_score_critic.py          | ILS 路径专用 critic  | planning/critic/ils_score_critic.py          | smartRelocate |
| legacy/llm_first_planner.py         | 真死代码（0 引用）   | （删除）                                       | delete_file |
| legacy/llm_planner.py               | 真死代码（仅自身测试）| （删除）+ test_llm_planner.py 改造测主路径    | delete_file + 改测试 |
| legacy/executor.py                  | 真死代码（与 graph/execute_finalize 等价）| （删除）+ 2 个测试改造调 execute_finalize_node | delete_file + 改测试 |
| legacy/prompts/llm_planner_prompt.py| llm_planner 的依赖（孤儿）| （删除）                                       | delete_file |
| legacy/__init__.py                  | 冻结纪律 docstring   | （删除）                                       | delete_file |
| legacy/ 整个目录                    | （误导的伞型分类）   | （删除）                                       | delete_dir  |
| scripts/verify_legacy_frozen.py    | 守的是已不存在的目录 | （删除）                                       | delete_file |
```

**对比 spec B**：spec B 是「重组到 6 子目录」（结构性整理）；spec D 是「修正 spec B 误把活代码当 legacy」（语义性纠错）。两者不冲突，spec D 仅扩展 spec B 的 `planning/` 子目录 + 删 `legacy/`。

**关键工具**：与 spec B 一样用 Kiro 的 `smartRelocate`——自动跟随式 import 更新（VS Code 内置 file move + reference rewrite），避免手工 `git mv` + `sed` 漏边角。

## Architecture

### 目录树对比（spec B 后 vs spec D 后）

```text
spec B 后（当前状态，误导性）：
backend/agent/
├── core/                    （6 子目录骨架，正确）
├── intent/                  （正确）
├── planning/
│   ├── blueprint/           （正确）
│   ├── critic/
│   │   ├── critics_v2.py    （正确）
│   │   └── social_compat.py
│   ├── commute/
│   └── weights_llm.py
├── runtime/                 （正确）
├── graph/                   （正确）
└── legacy/                  ← ⚠ 误导：含 3 类完全不同的代码
    ├── planner_rule.py        ← 实际是主路径活代码
    ├── ils_planner.py         ← 活的 fallback
    ├── segment_decider.py     ← ils_planner 依赖
    ├── ils_score_critic.py    ← ILS 专用 critic
    ├── llm_first_planner.py   ← 真死代码
    ├── llm_planner.py         ← 真死代码
    ├── executor.py            ← 真死代码（与 graph 等价）
    └── prompts/
        └── llm_planner_prompt.py  ← 孤儿
```

```text
spec D 后（诚实命名）：
backend/agent/
├── core/                    （spec B 不动）
├── intent/                  （spec B 不动）
├── planning/
│   ├── blueprint/           （spec B 不动）
│   ├── critic/
│   │   ├── critics_v2.py    （spec B 不动）
│   │   ├── social_compat.py
│   │   └── ils_score_critic.py  ← 从 legacy/ 搬来；docstring 明确「ILS 候选打分专用」
│   ├── commute/             （spec B 不动）
│   ├── planners/            ← spec D 新建
│   │   ├── __init__.py
│   │   ├── rule_planner.py        ← 重命名自 legacy/planner_rule.py（主路径活代码）
│   │   ├── ils_planner.py         ← 搬自 legacy/（graph replan 第 3 次兜底）
│   │   └── segment_decider.py     ← 搬自 legacy/（ils_planner 依赖）
│   └── weights_llm.py       （spec B 不动；删 # FROZEN 注释也可暂留）
├── runtime/                 （spec B 不动）
└── graph/                   （spec B 不动）
（legacy/ 整个目录已删）
```

### 关键决策（spec D 设计阶段拍板）

```text
| 决策点                                | 决定                              | 理由                                            |
|--------------------------------------|----------------------------------|------------------------------------------------|
| planner_rule.py 是否改名为 rule_planner.py | 是                              | 命名一致性：planners/ 下文件全部 *_planner.py 形式|
| ils_planner.py 改名为 fallback_ils_planner.py？| 否                          | 已经是 ils_planner.py 命名足够清晰；ils 本身就是 fallback 算法 |
| ils_score_critic 是否合并进 critics_v2 | 否                              | 维度不同——critics_v2 是 itinerary 全局校验；ils_score_critic 是 ILS 候选打分；功能不重叠|
| 删除 3 个真死代码是否需要二次确认       | 不需要（grep 已确认 0 引用 / 仅测试引用）| 已经按 problem.md 教训独立审查；ils_planner 链路在 graph/replan.py 仍正常 |
| test_llm_planner.py 整体删还是改造     | 改造                              | 4 个测试用例是测 plan_itinerary_with_mode("llm") 整体行为，不是测 llm_planner 模块；删 import 行 + 用例不动即可 |
| executor.py 删除 + 2 个测试改造路径     | 改造为调 execute_finalize_node    | graph/nodes/execute_finalize 已落地等价行为（reserve + share + narration）|
| 是否一次性 commit                     | 是                                | 与 spec B 一样的原子 commit 策略，便于回滚      |
| 解冻后是否清空 spec B 加的 # FROZEN 注释 | 是                                | 4 个迁移模块的 # FROZEN 注释全删 + docstring 改写为正确职责 |
| weights_llm.py 顶部的 # FROZEN 注释    | 保留（不在本 spec 范围；spec C R4 之后再讨论）| weights_llm 仍是 ILS 路径专用；本 spec 不改它的状态 |
| 是否删除 verify_legacy_frozen.py     | 是                                | 守的目录已不存在；保留是垃圾代码                |
```

## Components and Interfaces

### Component 1: smartRelocate 4 处迁移

```text
| 旧路径                                       | 新路径                                                | 改名 | smartRelocate 自动改 |
|---------------------------------------------|------------------------------------------------------|------|---------------------|
| backend/agent/legacy/planner_rule.py        | backend/agent/planning/planners/rule_planner.py     | 是   | 全仓库 import 自动跟随 |
| backend/agent/legacy/ils_planner.py         | backend/agent/planning/planners/ils_planner.py      | 否   | 同上                |
| backend/agent/legacy/segment_decider.py     | backend/agent/planning/planners/segment_decider.py  | 否   | 同上                |
| backend/agent/legacy/ils_score_critic.py    | backend/agent/planning/critic/ils_score_critic.py   | 否   | 同上                |
```

**注意**：smartRelocate 改名（如 planner_rule.py → rule_planner.py）时，**类/函数名不变**——只动文件名。所有 `from agent.legacy.planner_rule import plan_itinerary` 自动变成 `from agent.planning.planners.rule_planner import plan_itinerary`。

### Component 2: 3 个真死代码删除 + 测试改造

#### Component 2.1: 删除 `legacy/llm_first_planner.py`

- **删除前**：`grep -r "llm_first_planner" backend/ --include="*.py"` 必须 0 命中（已确认）
- **删除后**：直接 delete_file，不需要任何下游改造

#### Component 2.2: 删除 `legacy/llm_planner.py` + `legacy/prompts/llm_planner_prompt.py`

- **下游影响**：唯一引用方 `tests/test_llm_planner.py` 第 20 行 `from agent.legacy.llm_planner import plan_itinerary_llm`
- **改造**：删除该 import 行（4 个测试用例 `test_llm_planner_fallback_to_rule_with_stub` / `test_rule_vs_llm_mode_same_main_poi_and_restaurant` / `test_plan_itinerary_with_mode_param_robust` / `test_llm_mode_handles_all_scenes_via_fallback` 都是测 `plan_itinerary_with_mode("llm")` 整体行为，不直接调 `plan_itinerary_llm`）；测试用例本身不动
- **附加删除**：`legacy/prompts/llm_planner_prompt.py`（仅 llm_planner.py 引用它，删完成孤儿）+ `legacy/prompts/__init__.py`（空包）

#### Component 2.3: 删除 `legacy/executor.py`

- **下游影响**：`tests/test_agent_flow.py` + `tests/test_8_scenarios.py` 引用 `from agent.legacy.executor import execute_plan`
- **改造**：用 graph 主路径的 `execute_finalize_node` 等价行为替代

```python
# 改造前（test_agent_flow.py）：
from agent.legacy.executor import execute_plan
# ...
result = execute_plan(itinerary=itinerary, intent=intent, ...)

# 改造后：
from agent.graph.nodes.execute_finalize import execute_finalize_node
# ...
state = {"itinerary": itinerary, "intent": intent}
result_state = execute_finalize_node(state)
result = {
    "orders": result_state.get("orders", []),
    "share_message": result_state.get("share_message", ""),
    "narration": result_state.get("narration", ""),
}
```

**等价性验证**：execute_finalize_node 已经在 spec A R6+R7（Task 6）落地，含「reserve_restaurant 全量遍历 + generate_share_message + 调 generate_narration confirm 阶段」三件事——与 executor.execute_plan 行为完全等价（详见 graph/nodes/execute_finalize.py docstring）。

### Component 3: legacy/ 目录 + verify 脚本删除

```text
| 删除路径                                        | 删除时机              |
|------------------------------------------------|--------------------|
| backend/agent/legacy/__init__.py               | task 4 末尾         |
| backend/agent/legacy/__pycache__/              | task 4 末尾         |
| backend/agent/legacy/prompts/                  | task 3（含 llm_planner_prompt 删除）|
| backend/agent/legacy/                          | task 4 末尾（确保空目录后删）|
| backend/scripts/verify_legacy_frozen.py        | task 5             |
```

**注意**：`backend/agent/__init__.py` 如果有 `from .legacy import ...` 类的 re-export 必须先清理（实际本项目 spec B 落地时未加 re-export，但仍要 grep 确认）。

### Component 4: 解冻 + docstring 改写

迁移到 `planners/` / `critic/` 的 4 个文件 SHALL 顶部清理：

1. 删除 `# FROZEN: 详见 AGENTS.md §3.3.1，仅 fallback / safety-net，不改业务` 注释
2. 改写文件 docstring 为正确职责描述：

```python
# planning/planners/rule_planner.py（重命名自 legacy/planner_rule.py）
"""规划主路径活代码——rule + LLM-first + ILS 三模式分发器。

被以下入口直接消费：
- `backend/main.py:1740`（真 LLM 链路 SSE 主流程）
- `backend/collab/room.py`（房间协作 fallback 兜底）
- `backend/agent/graph/nodes/replan.py:_route_after_ils`（ILS 失败后的 rule planner 兜底）
- `backend/scripts/verify_planning.py`（验收脚本）
- 5 个 pytest（test_refiner_duration_consistency / test_planner_hybrid / 等）

提供两个入口函数：
- `plan_itinerary(intent, *, tracer)`：默认 mode（按 PLANNER_MODE env 路由）
- `plan_itinerary_with_mode(intent, mode, *, tracer)`：显式选 rule / llm / hack mode
"""

# planning/planners/ils_planner.py（搬自 legacy/）
"""ILS 算法兜底 planner——Iterated Local Search 搜索候选 (POI, restaurant, time) 三元组。

graph 主路径在 LLM 重生成失败 N 次后由 graph/nodes/replan.py 调它做第 3 次兜底。
非主路径，但是评分项 4（Tool 编排合理性）的兜底链路一等公民。

含 spec A R5 加固：
- `_overload_penalty(poi, intent)` 单段过载强惩罚（年龄 cap 兜底）
- `_resolve_dynamic_dining_slots(intent, segments)` 动态用餐时段
- `_retry_with_critic_feedback` 4 类违规黑名单
"""

# planning/planners/segment_decider.py（搬自 legacy/）
"""ils_planner 的依赖——决定行程 segment 集合（亲子主路径 = 5 段、独处 = 3 段等）。

仅被 ils_planner / replan 节点 / test 引用；不进 graph 主路径。
"""

# planning/critic/ils_score_critic.py（搬自 legacy/）
"""ILS 候选打分专用 critic——CriticReport / run_critics。

与 critics_v2 维度不同：
- critics_v2.validate_itinerary：itinerary 全局校验（10 类 ViolationCode + 1 spec A R4 + 1 spec C R2）
- ils_score_critic.run_critics：ILS 候选打分（hard_constraint / time_window / budget / style 4 维评分）

被 ILS 路径在产候选时调用；被 verify_planning.py 验收脚本调用。
"""
```

## Data Models

本 spec 不引入新数据模型——只动文件位置 + 删 3 个文件 + 改测试导入。所有 Pydantic 模型保持现位置 / 现内容不变。

## Correctness Properties

### Property 1: 业务行为零变化

**Validates: Requirements 1.5, 2.5**

WHEN 本 spec 完成 THEN 跑全套 pytest + verify_planning.py + verify_planning_quality.py + verify_edge_model.py SHALL 与 baseline 完全一致（diff 应为空）。

### Property 2: 旧路径全部不可 import

**Validates: Requirements 3.4, 3.5**

WHEN 本 spec 完成 THEN 全仓库 grep `from agent\.legacy\.` SHALL 0 命中；`importlib.import_module("agent.legacy.X")` 对任何 X 都 ImportError。

### Property 3: 新路径全部可 import

**Validates: Requirements 2.4**

WHEN 本 spec 完成 THEN tests/test_import_paths.py:test_planning_planners_imports SHALL 全过：

```python
def test_planning_planners_imports() -> None:
    from agent.planning.planners.rule_planner import plan_itinerary, plan_itinerary_with_mode  # noqa: F401
    from agent.planning.planners.ils_planner import plan_hybrid  # noqa: F401
    from agent.planning.planners.segment_decider import decide_segments, FULL_SEGMENTS  # noqa: F401
    from agent.planning.critic.ils_score_critic import run_critics, CriticReport  # noqa: F401
```

### Property 4: 死代码彻底清除

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 3.1, 3.2, 3.3**

WHEN 本 spec 完成 THEN：
- `backend/agent/legacy/` 目录不存在
- `backend/scripts/verify_legacy_frozen.py` 不存在
- `grep -r "llm_first_planner\|legacy.executor\|legacy.llm_planner" backend/ --include="*.py"` 0 命中

### Property 5: AGENTS.md 一致性

**Validates: Requirements 4.1, 4.2, 4.3**

WHEN 本 spec 完成 THEN AGENTS.md §3.3.1 内：
- `legacy/` 段已删
- `planning/planners/` 与 `planning/critic/ils_score_critic.py` 段已加
- MUST NOT 段不再含「在 agent/legacy/ 加新功能」「verify_legacy_frozen 守护」「FROZEN 标记」相关条款

### Property 6: spec C 锚点同步

**Validates: Requirements 5.1, 5.2, 5.3**

WHEN 本 spec 完成 THEN `.kiro/specs/algorithm-redesign/{requirements,design,tasks}.md` SHALL grep `agent/legacy/` 0 命中；所有 ils_planner 引用都在 `planning/planners/ils_planner.py`。

## Testing Strategy

### 1. baseline（task 1 前）

```bash
pytest backend/tests/ -v --tb=short > /tmp/baseline_pre_d.txt 2>&1
python backend/scripts/verify_planning.py
python backend/scripts/verify_planning_quality.py
python backend/scripts/verify_edge_model.py
python backend/scripts/verify_legacy_frozen.py    # 仍存在；本 spec 删它前要先验它过
python -m backend.main & sleep 3 && curl -s http://localhost:8000/health
```

### 2. 每 task 末尾的 smoke test

每 task 末尾跑 `pytest backend/tests/ -x --tb=short`（`-x` 模式遇到第一个失败立即停）。

### 3. task 5 末尾完整验证

```bash
pytest backend/tests/ -v --tb=short
python backend/scripts/verify_planning.py
python backend/scripts/verify_planning_quality.py
python backend/scripts/verify_edge_model.py
# verify_legacy_frozen.py 已删——不再跑
python -m backend.main & sleep 3 && curl -s http://localhost:8000/health
cd frontend && pnpm verify:all  # 前端不依赖后端 import 路径但仍验证
grep -rn "from backend\.agent\.legacy\|from agent\.legacy" backend/ --include="*.py"  # 必须 0 命中
```

### 4. 改造后的测试覆盖矩阵

```text
| 测试文件                              | 改动类型                                  | 验证                          |
|--------------------------------------|------------------------------------------|------------------------------|
| tests/test_llm_planner.py             | 删除 from agent.legacy.llm_planner 行      | 4 个用例继续过（plan_itinerary_with_mode 主路径行为）|
| tests/test_agent_flow.py              | from agent.legacy.executor → execute_finalize_node 改造 | reserve + share + narration 行为等价|
| tests/test_8_scenarios.py             | 同上                                       | 8 场景端到端跑通 + E1/E2 异常分支 |
| tests/test_import_paths.py            | test_legacy_imports 改名 + 内容替换         | 4 个迁移模块 + 旧路径 ImportError 反向断言 |
| tests/test_node_decider.py            | from agent.legacy.segment_decider 自动改   | smartRelocate 自动处理         |
| tests/test_planner_hybrid_overload.py | from agent.legacy.ils_score_critic / ils_planner 自动改| smartRelocate 自动处理        |
| tests/test_planner_hybrid.py          | from agent.legacy.ils_score_critic / planner_rule 自动改 | smartRelocate 自动处理        |
| tests/test_refiner_duration_consistency.py | from agent.legacy.planner_rule 自动改 | smartRelocate 自动处理         |
| backend/main.py:1740                  | from agent.legacy.planner_rule 自动改     | smartRelocate 自动处理         |
| backend/collab/room.py 2 处            | 同上                                       | 同上                          |
| backend/agent/graph/nodes/replan.py 3 处| 同上                                      | 同上                          |
| backend/scripts/verify_planning.py    | 同上                                       | 同上                          |
| backend/scripts/verify_llm_first.py   | 同上                                       | 同上                          |
| backend/scripts/analyze_overload_coefficient.py | 同上                              | 同上                          |
```

### 5. 不做的测试

- 不做大规模性能测试（hackathon 时间盒不允许）
- 不做 LLM 真链路 e2e（DEEPSEEK_API_KEY 未必配置；stub 模式覆盖即可）
- 不重新跑 spec A R10 验证脚本（业务行为零变化，spec A 已通过）

## Error Handling

```text
| 风险                                              | 概率 | 影响 | 缓解                                                 |
|---------------------------------------------------|------|-----|----------------------------------------------------|
| smartRelocate 漏改某个相对引用                    | 中   | 中  | 分 task 推进 + 每 task pytest -x 立即定位             |
| executor.py 删除后测试改造行为不等价              | 中   | 高  | task 3 改造时跑 test_agent_flow + test_8_scenarios 详细验证；不等价立刻停 |
| llm_planner.py 删除后测试漏掉某个用例              | 低   | 低  | 4 个用例都是测 plan_itinerary_with_mode 整体行为；删 import 行不影响|
| AGENTS.md §3.3.1 漏改某条 MUST NOT                | 中   | 低  | task 5 grep 「legacy」「FROZEN」全检                |
| spec C 三件套同步遗漏                             | 中   | 中  | task 5 用 grep + str_replace 批量替换           |
| backend/main.py:1740 改后真 LLM 链路启动失败       | 低   | 高  | task 5 跑 `python -c "from backend.main import app"` + curl /health|
| collab/room.py 改后协作功能崩                     | 低   | 中  | task 5 跑 collab 相关测试（如有）                    |
| ils_score_critic 搬到 planning/critic/ 与 critics_v2 命名混淆 | 低   | 低  | docstring 写明维度差异；保持函数名 `run_critics` 不变 |
| Wave 5 末尾 verify_planning_quality 红灯           | 低   | 高  | 立即回滚（git restore .）；hackathon 时间盒不允许返工 |
```

## Decisions Log

```text
| 决策                                | 决定                              | 来源                              |
|------------------------------------|----------------------------------|----------------------------------|
| spec D 是否在 spec C 启动前做         | 是（前置硬约束）                    | spec C R3+R4 锚点要改             |
| 解冻后保留 # FROZEN 注释还是删除     | 删除（4 个迁移模块全部）            | 解冻语义即不冻结，留注释名实不符  |
| weights_llm.py 顶部的 # FROZEN 注释   | 保留不动                          | 不在本 spec 范围；它仍是 ILS 路径专用|
| ils_score_critic 是否合并进 critics_v2 | 否                                | 维度不同（候选打分 vs 全局校验）    |
| 一次性 commit 还是分批 commit         | 一次性原子 commit                  | 与 spec B 一致；便于回滚          |
| 改名策略（planner_rule → rule_planner）| 是                                | planners/ 下统一 *_planner.py 命名|
| ils_planner.py 是否改名                | 否                                | 命名已足够清晰                    |
| segment_decider.py 是否改名            | 否                                | 命名已足够清晰                    |
| executor.py 测试改造路径               | 调 graph/nodes/execute_finalize_node | 已落地等价行为                  |
| llm_planner.py 测试改造路径            | 仅删 import 行                    | 测试用例本身已是测 mode 整体行为   |
| graph/build.py 拓扑                    | 不动（spec B 锁）                  | 编排冻结纪律                      |
```

## Risk Assessment

```text
| 风险                                              | 概率 | 影响 | 缓解                                                |
|---------------------------------------------------|------|-----|----------------------------------------------------|
| baseline 阶段发现 spec A/B 已有红灯                 | 低   | 高  | 立即停止 spec D；先修 spec A/B 红灯再启动              |
| smartRelocate 在改名（planner_rule→rule_planner）时挂掉| 低 | 中  | 该名字仅 1 处改名；smartRelocate 已在 spec B 用过 6 次  |
| 测试改造（executor → execute_finalize）行为不等价   | 中   | 高  | task 3 单独验证；不等价立刻 git restore           |
| backend/main.py 改后 FastAPI 启动失败              | 低   | 高  | task 5 必跑 `python -m backend.main` + curl /health |
| AGENTS.md §3.3.1 重写漏改某条 MUST NOT             | 中   | 低  | task 5 grep 「legacy」全文（4 处）                  |
| spec C tasks.md 锚点同步遗漏                       | 中   | 中  | task 5 grep + 人工核对 4 处                       |
| 一次性 commit 中途出现导致回滚困难                  | 低   | 低  | task 5 末才 commit；前 4 task 失败直接 git restore .|
| 改 main.py:1740 时漏改其他位置                     | 低   | 中  | smartRelocate 自动改全仓库 + task 5 grep         |
```

## Estimated Effort

```text
| 任务                                               | 工时   | 备注                                          |
|---------------------------------------------------|-------|-----------------------------------------------|
| Task 1: baseline + git tag v-spec-d-start        | 0.2h  | 跑 spec A/B 验收 + verify_legacy_frozen 最后一跑 |
| Task 2: 删除 3 个真死代码（含 prompts/llm_planner_prompt） | 0.6h  | delete_file 3 次 + grep 确认                  |
| Task 3: smartRelocate 4 处迁移 + docstring 改写    | 0.8h  | 4 次 smartRelocate + 顶部注释清理 + docstring 改写|
| Task 4: 删除 legacy/ 目录 + verify_legacy_frozen.py| 0.3h  | rmdir 空目录 + delete_file                    |
| Task 5: 改造 3 个测试 + AGENTS.md / spec C / 文档同步 + commit| 0.8h | 改测试 + grep 旧路径 + 改 4 处文档锚点 + 原子 commit |
| **总计**                                          | **2.7h**| ≈ 半人天（hackathon 时间盒可承受）            |
```

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
```
