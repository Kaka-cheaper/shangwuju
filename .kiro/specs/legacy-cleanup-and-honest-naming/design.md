# Design Document: Legacy Cleanup and Honest Naming (spec D v3)

> **范围**：删除 spec B 留下的误导性 `legacy/` 目录 → 解构为「主路径活代码 + PLANNER_LLM_STRATEGY 三档子策略 + ILS 专用 critic + 执行类活代码」四类，**不删任何文件**
> **语言**：Python 3.11（后端，仅改 import 路径 + 文件位置 + smartRelocate 8 处）
> **项目模式**：hackathon + demo 阶段；本 spec 是 spec B 的修正性重构，spec C 实施前的前置 spec
> **规模**：~3.0h（v3 比 v2 少 0.5h——不再有 1 个 deletion + 2 个测试改造），分 4 个 task / 4 wave；不改业务行为，仅动位置 + 改路径
> **现状**：spec A + spec B 已落地；spec C 三件套已写但未实施；本 spec 在 spec B 6 子目录骨架上扩展 `planning/planners/` + `planning/execution/` 子目录 + 删 `legacy/`
> **绝对约束**：保留 spec B 的 5 子目录骨架（core/ + intent/ + planning/ + runtime/ + graph/）；保留 graph/build.py 拓扑（spec B 锁）；只动 legacy/ 内 8 文件 + 1 prompts 子目录 + 4 处 spec C 文档锚点
> **审计修正历程（三次）**：
>
> - v1：起草版只 grep absolute import → 把 llm_planner / llm_first_planner 误判为死代码
> - v2：user 独立审查指出后做完整审计（含相对 import + 内部链式调用）→ 修正为 1 死代码（仅 executor）
> - v3：task 2 实测发现 executor.execute_plan 与 graph/execute_finalize 行为不等价（前者 `_extract_reserved_time(note)`、后者 `start_time`，mock 严格匹配 HH:MM 不等价）→ 修正为 **0 死代码**

## Overview

把 spec B 落地的 `legacy/` 子目录解构为「**真定位明确**的四类」，**不删任何 .py 文件**：

```text
| 旧位置                              | 真实定位                                          | 新位置                                          | 操作        |
|------------------------------------|------------------------------------------------|----------------------------------------------|-------------|
| legacy/planner_rule.py              | 主路径活代码 + 三档子策略分发器                    | planning/planners/rule_planner.py            | smartRelocate + 改名 |
| legacy/ils_planner.py               | PLANNER_LLM_STRATEGY=hybrid + graph replan 兜底  | planning/planners/ils_planner.py             | smartRelocate |
| legacy/llm_first_planner.py         | PLANNER_LLM_STRATEGY=llm_first（默认）核心       | planning/planners/llm_first_planner.py       | smartRelocate |
| legacy/llm_planner.py               | PLANNER_LLM_STRATEGY=function_calling 子策略     | planning/planners/llm_planner.py             | smartRelocate |
| legacy/segment_decider.py           | ils_planner 依赖（被 planner_rule / replan 调）   | planning/planners/segment_decider.py         | smartRelocate |
| legacy/ils_score_critic.py          | ILS 路径专用 critic                              | planning/critic/ils_score_critic.py          | smartRelocate（跨子目录）|
| legacy/executor.py                  | 执行类活代码（与 graph/execute_finalize 不等价）   | planning/execution/executor.py（**新建子目录**）| smartRelocate |
| legacy/prompts/llm_planner_prompt.py| llm_planner.py 的 system prompt                | planning/planners/prompts/llm_planner_prompt.py | smartRelocate |
| legacy/prompts/__init__.py          | （空包）                                         | planning/planners/prompts/__init__.py        | 重建（空文件）|
| legacy/__init__.py                  | 冻结纪律 docstring                              | （删除）                                       | delete_file |
| legacy/ 整个目录                    | （误导的伞型分类）                              | （删除）                                       | delete_dir  |
| scripts/verify_legacy_frozen.py    | 守的是已不存在的目录                              | （删除）                                       | delete_file |
```

**对比 spec D v2**：v2 删除 1 个真死代码（executor）+ 2 个测试改造为调 graph/execute_finalize；v3 不再删 executor（task 2 实测发现 executor 与 graph/execute_finalize 行为不等价），新建 `planning/execution/` 子目录给 executor 归位。

**对比 spec B**：spec B 是「重组到 6 子目录」（结构性整理）；spec D v3 是「修正 spec B 误把 8 个活代码当 legacy」（语义性纠错）。两者不冲突，spec D v3 仅扩展 spec B 的 `planning/` 子目录 + 删 `legacy/`。

**关键工具**：与 spec B 一样用 Kiro 的 `smartRelocate`——自动跟随式 import 更新（含 absolute + relative `from .X` + 内部链式 `from ..X`），避免手工 `git mv` + `sed` 漏边角。

## Architecture

### 目录树对比（spec B 后 vs spec D v3 后）

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
└── legacy/                  ← ⚠ 误导：含 4 类完全不同的代码，0 个真死代码
    ├── planner_rule.py        ← 主路径活代码 + 三档子策略分发器
    ├── ils_planner.py         ← PLANNER_LLM_STRATEGY=hybrid + replan 兜底
    ├── llm_first_planner.py   ← PLANNER_LLM_STRATEGY=llm_first（默认！）
    ├── llm_planner.py         ← PLANNER_LLM_STRATEGY=function_calling
    ├── segment_decider.py     ← ils_planner 依赖
    ├── ils_score_critic.py    ← ILS 专用 critic
    ├── executor.py            ← 执行类活代码（与 graph 不等价）
    └── prompts/
        └── llm_planner_prompt.py  ← llm_planner 依赖（不是孤儿）
```

```text
spec D v3 后（诚实命名）：
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
│   │   ├── rule_planner.py            ← 重命名自 legacy/planner_rule.py（主路径分发器）
│   │   ├── ils_planner.py             ← 搬自 legacy/（hybrid 子策略 + replan 兜底）
│   │   ├── llm_first_planner.py       ← 搬自 legacy/（**默认 llm_first 子策略**）
│   │   ├── llm_planner.py             ← 搬自 legacy/（function_calling 子策略）
│   │   ├── segment_decider.py         ← 搬自 legacy/（ils_planner 依赖）
│   │   └── prompts/
│   │       ├── __init__.py
│   │       └── llm_planner_prompt.py  ← 搬自 legacy/prompts/
│   ├── execution/           ← spec D 新建（与 planners/ + critic/ 三足鼎立）
│   │   ├── __init__.py
│   │   └── executor.py                ← 搬自 legacy/（执行类活代码）
│   └── weights_llm.py       （spec B 不动；# FROZEN 注释保留）
├── runtime/                 （spec B 不动）
└── graph/                   （spec B 不动）
（legacy/ 整个目录已删；verify_legacy_frozen.py 已删）
```

**execution/ 子目录的设计理由**：

```text
| 子目录            | 阶段                            | 含模块                                  |
|------------------|--------------------------------|----------------------------------------|
| planning/blueprint/ | 蓝图生成（LLM 出 candidate plan）| blueprint / blueprint_llm / assemble / node_decider |
| planning/planners/  | 规划主路径（按 mode 分发）        | rule / ils / llm_first / llm / segment_decider |
| planning/critic/    | 验证（critic 验 itinerary）       | critics_v2 / social_compat / ils_score_critic |
| planning/execution/ | 执行（用户确认后下发 Tool）       | executor                                |
| planning/commute/   | 通勤估算                          | lookup_hop                              |
| planning/weights_llm.py | ILS 权重（FROZEN）            | weights_llm                             |
```

「Plan → Critic → Execute」三段式语义；executor 不归 planners/ 因为它不是 planner，归 critic/ 因为它不是 critic。

### 关键决策（spec D v3 设计阶段拍板）

```text
| 决策点                                | 决定                              | 理由                                            |
|--------------------------------------|----------------------------------|------------------------------------------------|
| executor 归 planners/ 还是新建 execution/| 新建 execution/                  | executor 不是 planner；execution/ 与 planners/ + critic/ 三足鼎立 |
| 是否删 executor 用 graph/execute_finalize 替代| 否（v3 修正）                | 实测两者行为不等价：executor 解析 note 中预留时段；graph 用 start_time，mock 严格匹配下失败 |
| 是否修 graph/execute_finalize 加 _extract_reserved_time | 否              | 超出本 spec「只动位置不动业务」纪律            |
| planner_rule.py 是否改名为 rule_planner.py | 是                              | 命名一致性：planners/ 下文件全部 *_planner.py 形式|
| 5 个 planner 是否合并                  | 否                                | 三档 PLANNER_LLM_STRATEGY 都是生产路径；合并需评估业务影响 |
| ils_score_critic 是否合并进 critics_v2 | 否                              | 维度不同——critics_v2 是 itinerary 全局校验；ils_score_critic 是 ILS 候选打分；功能不重叠|
| 是否一次性 commit                     | 是                                | 与 spec B 一样的原子 commit 策略，便于回滚      |
| 解冻后是否清空 spec B 加的 # FROZEN 注释 | 是                                | 7 个迁移模块的 # FROZEN 注释全删 + docstring 改写为正确职责 |
| weights_llm.py 顶部的 # FROZEN 注释    | 保留（不在本 spec 范围）            | weights_llm 仍是 ILS 路径专用                  |
| 是否删除 verify_legacy_frozen.py     | 是                                | 守的目录已不存在；保留是垃圾代码                |
```

## Components and Interfaces

### Component 1: smartRelocate 8 处迁移（含 1 处改名 + 1 处跨子目录 + 1 处建新子目录）

```text
| 旧路径                                            | 新路径                                                          | 改名 | 跨子目录 |
|--------------------------------------------------|---------------------------------------------------------------|------|---------|
| backend/agent/legacy/planner_rule.py              | backend/agent/planning/planners/rule_planner.py               | 是   | 否      |
| backend/agent/legacy/ils_planner.py               | backend/agent/planning/planners/ils_planner.py                | 否   | 否      |
| backend/agent/legacy/llm_first_planner.py         | backend/agent/planning/planners/llm_first_planner.py          | 否   | 否      |
| backend/agent/legacy/llm_planner.py               | backend/agent/planning/planners/llm_planner.py                | 否   | 否      |
| backend/agent/legacy/segment_decider.py           | backend/agent/planning/planners/segment_decider.py            | 否   | 否      |
| backend/agent/legacy/prompts/llm_planner_prompt.py| backend/agent/planning/planners/prompts/llm_planner_prompt.py | 否   | 否      |
| backend/agent/legacy/ils_score_critic.py          | backend/agent/planning/critic/ils_score_critic.py             | 否   | **是**（到 critic/）|
| backend/agent/legacy/executor.py                  | backend/agent/planning/execution/executor.py                  | 否   | **是**（新建 execution/）|
```

**关键 smartRelocate 注意点**：

1. **改名**（仅 1 处）：planner_rule.py → rule_planner.py。类/函数名不变；smartRelocate 自动改 `from agent.legacy.planner_rule import plan_itinerary` → `from agent.planning.planners.rule_planner import plan_itinerary`。
2. **跨子目录**（2 处）：
   - ils_score_critic 从 `planners/` 区域跨到 `critic/`。其原内部相对引用 `from agent.legacy.ils_planner` 会变成 `from agent.planning.planners.ils_planner`（smartRelocate 跨包跟随）。
   - executor 进新建 `execution/` 子目录。其原内部 `from ..core.trace` / `from schemas.X` / `from tools.registry` 等跨包引用要保持正确——smartRelocate 自动处理。
3. **内部相对引用**（smartRelocate 应自动改）：
   - `legacy/planner_rule.py:1262 from .llm_planner import plan_itinerary_llm` → 同目录 `from .llm_planner import plan_itinerary_llm`（OK，仍同包）
   - `legacy/planner_rule.py:1290 from .ils_planner import plan_hybrid` → 同上
   - `legacy/planner_rule.py:1406 from .llm_first_planner import plan_llm_first` → 同上
   - `legacy/llm_planner.py:54 from .planner_rule import (...)` → **`from .rule_planner import (...)`**（改名导致）
   - `legacy/llm_planner.py:62 from .prompts.llm_planner_prompt import` → 同上（prompts/ 跟随迁移）
   - `legacy/ils_planner.py:80 from .ils_score_critic import` → **`from ..critic.ils_score_critic import`**（跨子目录到 critic/）
   - `legacy/ils_planner.py:634 from .planner_rule import _resolve_time_window` → **`from .rule_planner import _resolve_time_window`**（改名导致）

   smartRelocate 应能自动处理大部分；task 4 必须 grep 复核。

### Component 2: legacy/ 目录 + verify 脚本删除（不删任何 .py 业务模块）

```text
| 删除路径                                        | 删除时机                |
|------------------------------------------------|-----------------------|
| backend/agent/legacy/__init__.py               | task 3 末尾            |
| backend/agent/legacy/__pycache__/              | task 3 末尾            |
| backend/agent/legacy/prompts/__pycache__/      | task 2（搬完 llm_planner_prompt 后）|
| backend/agent/legacy/prompts/                  | task 2（搬完 llm_planner_prompt 后）|
| backend/agent/legacy/                          | task 3 末尾（确保空目录后删）|
| backend/scripts/verify_legacy_frozen.py        | task 4                |
```

**注意**：spec D v3 **不删任何 .py 业务模块**（含 executor.py）。仅删除：legacy/ 空目录 + __init__.py + __pycache__ + verify 守门脚本。

### Component 3: 解冻 + docstring 改写（8 个文件）

迁移到 `planners/` / `critic/` / `execution/` 的 8 个文件 SHALL 顶部清理：

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
- `plan_itinerary(intent, *, tracer)`：rule 模式（确定性安全网）
- `plan_itinerary_with_mode(intent, mode, *, tracer)`：按 mode 分发到 rule / llm；llm 模式按
  PLANNER_LLM_STRATEGY env 三档子策略（llm_first / hybrid / function_calling）路由
"""

# planning/planners/ils_planner.py（搬自 legacy/）
"""PLANNER_LLM_STRATEGY=hybrid 子策略 + graph replan 第 3 次 ILS 兜底。

被以下入口消费：
- `rule_planner._plan_with_hybrid`（PLANNER_LLM_STRATEGY=hybrid 时）
- `graph/nodes/replan.py:ils_replan`（LLM 重生成失败 N 次后第 3 次兜底）
- `tests/test_planner_hybrid.py` / `test_planner_hybrid_overload.py`

ILS 算法：搜索 (POI, restaurant, time) 三元组候选 + 4 维 utility 加权打分 +
local search + 5% 接受劣解。

含 spec A R5 加固：
- `_overload_penalty(poi, intent)` 单段过载强惩罚（年龄 cap 兜底）
- `_resolve_dynamic_dining_slots(intent, segments)` 动态用餐时段
- `_retry_with_critic_feedback` 4 类违规黑名单
"""

# planning/planners/llm_first_planner.py（搬自 legacy/）
"""PLANNER_LLM_STRATEGY=llm_first（**默认值**）核心生产路径。

被以下入口消费：
- `rule_planner._plan_with_llm_first`（PLANNER_LLM_STRATEGY=llm_first 时；env 默认）

LLM-First Planner（产品级架构，参考 problem.md 问题 14 + 15 的 LLM-First 重构）：
- 阶段 1：候选搜索（4 级降级 + 距离放宽）
- 阶段 2：LLM 蓝图生成（PlanBlueprint）
- 阶段 3：critic backprompt 重试（≤2 次）
- 阶段 4：拼装 Itinerary
- 阶段 5：失败 fallback 链 → hybrid → rule
"""

# planning/planners/llm_planner.py（搬自 legacy/）
"""PLANNER_LLM_STRATEGY=function_calling 子策略（旧实现，A/B 候选）。

被以下入口消费：
- `rule_planner.plan_itinerary_with_mode`（strategy="function_calling" 分支）
- `tests/test_llm_planner.py`（4 个用例验证 function_calling 整体行为）

LLM Function Calling 自主调 Tool 的旧路径，自 LangGraph 主架构上线后仍保留作为
A/B 候选，但默认走 llm_first。
"""

# planning/planners/segment_decider.py（搬自 legacy/）
"""ils_planner 的依赖——决定行程 segment 集合（亲子主路径 = 5 段、独处 = 3 段等）。

被以下入口消费：
- `ils_planner` 内部调（候选生成阶段）
- `rule_planner` 内部调（plan_itinerary 段集合决策）
- `graph/nodes/replan.py:ils_replan`（ILS 兜底前的段集合决策）
- `tests/test_node_decider.py`

新代码请优先用 `agent/planning/blueprint/node_decider.decide_nodes`（edge_v1 模型）。
"""

# planning/planners/prompts/llm_planner_prompt.py（搬自 legacy/prompts/）
"""LLM Function Calling 自主规划 system prompt（PLANNER_LLM_STRATEGY=function_calling 用）。

被以下入口消费：
- `llm_planner.py:62 from .prompts.llm_planner_prompt import LLM_PLANNER_SYSTEM_PROMPT`
"""

# planning/critic/ils_score_critic.py（搬自 legacy/）
"""ILS 候选打分专用 critic——CriticReport / run_critics。

与 critics_v2 维度不同：
- critics_v2.validate_itinerary：itinerary 全局校验（10 类 ViolationCode + 1 spec A R4）
- ils_score_critic.run_critics：ILS 候选打分（hard_constraint / time_window / budget / style 4 维评分）

被以下入口消费：
- `ils_planner` 内部调（候选打分阶段）
- `verify_planning.py` 验收脚本
- `tests/test_planner_hybrid.py` / `test_planner_hybrid_overload.py`
"""

# planning/execution/executor.py（搬自 legacy/）
"""执行类活代码——用户确认后下发 reserve_restaurant / buy_ticket / generate_share_message。

【与 graph/nodes/execute_finalize 的关键差异】（spec D v3 task 2 实测发现）：

- executor.execute_plan：用 `_extract_reserved_time(restaurant_node.note)` 解析「已为你预留 X」
  类预留时段，**与 mock 时段严格匹配（HH:MM 整 30 分）**
- graph/execute_finalize：直接用 `restaurant_node.start_time`（含通勤后的实际抵达时刻），
  在 mock 严格匹配下失败

两者行为**不等价**——本模块是 rule planner / ils_planner 路径下用户确认的执行入口；
graph 路径下用户确认的执行入口是 graph/nodes/execute_finalize。

被以下入口消费：
- `tests/test_agent_flow.py`（rule planner 路径主流程 → executor）
- `tests/test_8_scenarios.py`（8 场景 reserve + share）
- `agent/__init__.py` re-export `execute_plan` / `ExecutionResult`
"""
```

## Data Models

本 spec 不引入新数据模型——只动文件位置 + 改 import 路径。所有 Pydantic 模型保持现位置 / 现内容不变。

## Correctness Properties

### Property 1: 业务行为零变化

**Validates: Requirements 1.5, 2.4**

WHEN 本 spec 完成 THEN 跑全套 pytest + verify_planning.py + verify_planning_quality.py + verify_edge_model.py SHALL 与 baseline 完全一致（diff 应为空）。

### Property 2: 旧路径全部不可 import

**Validates: Requirements 2.4, 2.5**

WHEN 本 spec 完成 THEN 全仓库 grep `from agent\.legacy\.` SHALL 0 命中；`importlib.import_module("agent.legacy.X")` 对任何 X 都 ImportError。

### Property 3: 新路径全部可 import

**Validates: Requirements 1.4**

WHEN 本 spec 完成 THEN tests/test_import_paths.py:test_planning_planners_imports SHALL 全过：

```python
def test_planning_planners_imports() -> None:
    from agent.planning.planners.rule_planner import plan_itinerary, plan_itinerary_with_mode  # noqa: F401
    from agent.planning.planners.ils_planner import plan_hybrid  # noqa: F401
    from agent.planning.planners.llm_first_planner import plan_llm_first  # noqa: F401
    from agent.planning.planners.llm_planner import plan_itinerary_llm  # noqa: F401
    from agent.planning.planners.segment_decider import decide_segments, FULL_SEGMENTS  # noqa: F401
    from agent.planning.planners.prompts.llm_planner_prompt import LLM_PLANNER_SYSTEM_PROMPT  # noqa: F401
    from agent.planning.critic.ils_score_critic import run_critics, CriticReport  # noqa: F401
    from agent.planning.execution.executor import execute_plan, ExecutionResult  # noqa: F401
```

### Property 4: legacy/ 目录彻底清除

**Validates: Requirements 2.1, 2.2, 2.3**

WHEN 本 spec 完成 THEN：
- `backend/agent/legacy/` 目录不存在
- `backend/scripts/verify_legacy_frozen.py` 不存在
- `agent/__init__.py` 中 `from .legacy.X import` 行已全部更新为 `from .planning.X.Y import`（含 executor → execution.executor）

### Property 5: AGENTS.md 一致性

**Validates: Requirements 3.1, 3.2, 3.3**

WHEN 本 spec 完成 THEN AGENTS.md §3.3.1 内：
- `legacy/` 段已删
- `planning/planners/` + `planning/execution/` 段已加
- `planning/critic/ils_score_critic.py` 已加
- MUST NOT 段不再含「在 agent/legacy/ 加新功能」「verify_legacy_frozen 守护」「FROZEN 标记」相关条款

### Property 6: spec C 锚点同步

**Validates: Requirements 4.1, 4.2, 4.3**

WHEN 本 spec 完成 THEN `.kiro/specs/algorithm-redesign/{requirements,design,tasks}.md` SHALL grep `agent/legacy/` 0 命中；所有 ils_planner 引用都在 `planning/planners/ils_planner.py`。

### Property 7: 死代码 0 个

**Validates: Requirements 1.3**

WHEN 本 spec 完成 THEN 所有原 legacy/ 下的 8 个 .py 业务文件（含 prompts/llm_planner_prompt.py）+ 1 个 __init__.py 中：
- 8 个业务文件全部解冻迁回 planning/ 子目录
- 0 个文件被删除
- legacy/__init__.py 删除（不是业务文件）

## Testing Strategy

### 1. baseline（task 1 已完成）

```bash
pytest backend/tests/ -v --tb=short          # 599 passed + 1 skipped + 0 failed
python backend/scripts/verify_planning.py     # ✓
python backend/scripts/verify_planning_quality.py  # ✓ 24/24
python backend/scripts/verify_edge_model.py   # ✓ 4/4
python backend/scripts/verify_legacy_frozen.py  # ✓（删前最后一次）
python -c "import main; print(main.app)"      # ✓ FastAPI app loaded
```

### 2. 每 task 末尾的 smoke test

每 task 末尾跑 `pytest backend/tests/ -x --tb=short`（`-x` 模式遇到第一个失败立即停）。

### 3. task 4 末尾完整验证

```bash
pytest backend/tests/ -v --tb=short
python backend/scripts/verify_planning.py
python backend/scripts/verify_planning_quality.py
python backend/scripts/verify_edge_model.py
# verify_legacy_frozen.py 已删——不再跑
python -c "import main; print(main.app)"
cd frontend && pnpm verify:all
grep -rn "from backend\.agent\.legacy\|from agent\.legacy" backend/ --include="*.py"  # 必须 0 命中（除 test_import_paths 反向断言）
grep -rn "from \.legacy\." backend/ --include="*.py"  # 必须 0 命中
```

### 4. 改造后的测试覆盖矩阵

```text
| 测试文件                              | 改动类型                                              | 验证                                |
|--------------------------------------|------------------------------------------------------|-----------------------------------|
| tests/test_import_paths.py            | test_legacy_imports → test_planning_planners_imports（含 5 + 1 prompt + 1 critic + 1 execution = 8 项）；旧路径 ImportError 反向断言加 7 项 | 8 项新路径可 import + 7 项旧路径全 ImportError |
| tests/test_agent_flow.py              | from agent.legacy.executor → from agent.planning.execution.executor（smartRelocate 自动）| reserve + share + narration 行为零变|
| tests/test_8_scenarios.py             | 同上                                                  | 8 场景端到端跑通                     |
| tests/test_llm_planner.py             | from agent.legacy.llm_planner → from agent.planning.planners.llm_planner（smartRelocate 自动）| 4 个用例继续过 |
| tests/test_node_decider.py            | from agent.legacy.segment_decider 自动改               | smartRelocate 自动处理              |
| tests/test_planner_hybrid_overload.py | from agent.legacy.ils_score_critic / ils_planner 自动改| smartRelocate 自动处理              |
| tests/test_planner_hybrid.py          | from agent.legacy.ils_score_critic / planner_rule（→ rule_planner 改名） 自动改 | smartRelocate 自动处理 |
| tests/test_refiner_duration_consistency.py | from agent.legacy.planner_rule（→ rule_planner 改名）自动改 | smartRelocate 自动处理         |
| backend/main.py:1740                  | from agent.legacy.planner_rule（→ rule_planner 改名）自动改 | smartRelocate 自动处理         |
| backend/collab/room.py 2 处            | 同上                                                  | 同上                              |
| backend/agent/graph/nodes/replan.py 5 处| 同上                                                | smartRelocate 自动处理               |
| backend/scripts/verify_planning.py    | 同上                                                  | 同上                              |
| backend/scripts/verify_llm_first.py   | 同上                                                  | 同上                              |
| backend/scripts/analyze_overload_coefficient.py | 同上                                          | 同上                              |
| backend/agent/__init__.py             | 改 from .legacy.X → from .planning.X.Y（3 处：planner_rule / llm_planner / executor）| 顶层 re-export 重写 |
```

### 5. 不做的测试

- 不做大规模性能测试（hackathon 时间盒不允许）
- 不做 LLM 真链路 e2e
- 不重新跑 spec A R10 验证脚本（业务行为零变化，spec A 已通过）

## Error Handling

```text
| 风险                                              | 概率 | 影响 | 缓解                                                 |
|---------------------------------------------------|------|-----|----------------------------------------------------|
| smartRelocate 漏改某个相对引用（含 from .X / from ..X 跨包）| 中 | 中 | 分 task 推进 + 每 task pytest -x 立即定位；task 4 grep 复核|
| ils_score_critic 跨子目录搬到 critic/ 后 import 漏改| 低 | 中  | smartRelocate 自动 + task 4 grep 复核 from ..critic.ils_score_critic|
| executor 搬到新建 execution/ 子目录后跨包引用漏改 | 低   | 中  | smartRelocate 自动 + task 4 grep 复核                 |
| AGENTS.md §3.3.1 漏改某条 MUST NOT                | 中   | 低  | task 4 grep「legacy」「FROZEN」全检                  |
| spec C 三件套同步遗漏                             | 中   | 中  | task 4 用 grep + str_replace 批量替换            |
| backend/main.py:1740 改后真 LLM 链路启动失败       | 低   | 高  | task 4 跑 `python -c "import main; print(main.app)"` |
| 起草版本误判（v1 / v2 删错文件）                   | 0（v3 已修正）| - | 已发现并修正：v1 误判 3 个 / v2 误判 1 个 / v3 = 0 个|
```

## Decisions Log

```text
| 决策                                | 决定                              | 来源                              |
|------------------------------------|----------------------------------|----------------------------------|
| spec D 是否在 spec C 启动前做         | 是（前置硬约束）                    | spec C R3+R4 锚点要改             |
| 解冻后保留 # FROZEN 注释还是删除     | 删除（7 个迁移模块全部）            | 解冻语义即不冻结，留注释名实不符  |
| weights_llm.py 顶部的 # FROZEN 注释   | 保留不动                          | 不在本 spec 范围                  |
| ils_score_critic 是否合并进 critics_v2 | 否                                | 维度不同（候选打分 vs 全局校验）    |
| llm_planner / llm_first_planner 是否删 | **否（v2 修正）**                  | grep 完整审计后确认是 PLANNER_LLM_STRATEGY 三档子策略生产路径|
| executor 是否删（用 graph/execute_finalize 替代）| **否（v3 修正）**       | task 2 实测发现行为不等价（_extract_reserved_time(note) vs start_time）|
| executor 归 planners/ 还是新建 execution/| 新建 execution/                  | executor 不是 planner；与 planners/ + critic/ 三足鼎立 |
| 一次性 commit 还是分批 commit         | 一次性原子 commit                  | 与 spec B 一致；便于回滚          |
| 改名策略（planner_rule → rule_planner）| 是                                | planners/ 下统一 *_planner.py 命名|
| ils_planner.py / 其他 planner 是否改名 | 否                                | 命名已足够清晰                    |
| graph/build.py 拓扑                    | 不动（spec B 锁）                  | 编排冻结纪律                      |
```

## Risk Assessment

```text
| 风险                                              | 概率 | 影响 | 缓解                                                |
|---------------------------------------------------|------|-----|----------------------------------------------------|
| baseline 阶段发现 spec A/B 已有红灯                 | 已过 | 高  | task 1 已跑 ✓                                      |
| smartRelocate 在改名（planner_rule→rule_planner）时挂掉| 低 | 中  | 该名字仅 1 处改名；smartRelocate 已在 spec B 用过 6 次  |
| backend/main.py 改后 FastAPI 启动失败              | 低   | 高  | task 4 必跑 `python -c "import main; print(main.app)"` |
| AGENTS.md §3.3.1 重写漏改某条 MUST NOT             | 中   | 低  | task 4 grep 「legacy」全文（5+ 处）                  |
| spec C tasks.md 锚点同步遗漏                       | 中   | 中  | task 4 grep + 人工核对 4 处                       |
| 一次性 commit 中途出现导致回滚困难                  | 低   | 低  | task 4 末才 commit；前 3 task 失败直接 git restore .|
| ils_score_critic / executor 跨子目录搬时 import 路径漂移| 低 | 中  | smartRelocate 自动 + task 4 grep 复核              |
| 起草版编排者犯重组审计错（已发现 + 修正 3 次）     | 0（已修正）| - | 永久教训写入 pitfalls.md：grep + 实测两步都做完     |
```

## Estimated Effort

```text
| 任务                                               | 工时   | 备注                                          |
|---------------------------------------------------|-------|-----------------------------------------------|
| Task 1: baseline + git tag v-spec-d-start        | 0.2h  | 已完成（task 1 已跑通 ✓）                       |
| Task 2: smartRelocate 8 处迁移（含 1 改名 + 2 跨子目录）+ docstring 改写| 1.5h  | 8 次 smartRelocate + 顶部注释清理 + 8 段 docstring 改写|
| Task 3: 删除 legacy/ 目录 + verify_legacy_frozen.py| 0.3h  | rmdir 空目录 + delete_file                    |
| Task 4: 升级 test_import_paths + AGENTS.md / spec C / 文档同步 + commit| 1.0h | 改测试 + grep 旧路径 + 改 4 处文档锚点 + 原子 commit |
| **总计**                                          | **3.0h**| ≈ 半人天（v3 比 v2 少 0.5h）                  |
```

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
| 删除 llm_planner / llm_first_planner / executor  | **不删——v3 修正后 0 个真死代码**           |
| 合并 5 个 planner 模块                           | 业务影响评估超出本 spec 范围                   |
| 修 graph/execute_finalize 加 _extract_reserved_time| 超出本 spec「只动位置不动业务」纪律        |
```
