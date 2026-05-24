# Design Document: Legacy Cleanup and Honest Naming (spec D)

> **范围**：删除 spec B 留下的误导性 `legacy/` 目录 → 解构为「死代码（删 1）+ 主路径活代码（迁回）+ PLANNER_LLM_STRATEGY 三档子策略（迁回）+ ILS 专用 critic（迁回）」四类
> **语言**：Python 3.11（后端，仅改 import 路径 + 文件位置 + 删 1 个文件）
> **项目模式**：hackathon + demo 阶段；本 spec 是 spec B 的修正性重构，spec C 实施前的前置 spec
> **规模**：~3.5h，分 5 个 task / 5 wave；不改业务行为，只动位置 + 删 1 个死代码 + 改路径
> **现状**：spec A + spec B 已落地；spec C 三件套已写但未实施；本 spec 在 spec B 6 子目录骨架上扩展 `planning/planners/` 子目录 + 删 `legacy/`
> **绝对约束**：保留 spec B 的 5 子目录骨架（core/ + intent/ + planning/ + runtime/ + graph/）；保留 graph/build.py 拓扑（spec B 锁）；只动 legacy/ 内 8 文件 + 1 prompts 子目录 + 4 处 spec C 文档锚点
> **审计修正**：起草版本只 grep absolute import 把 llm_planner / llm_first_planner 误判为死代码；user 独立审查指出后做完整审计（含相对 import + 内部链式调用），修正为「1 死代码 + 6 解冻」

## Overview

把 spec B 落地的 `legacy/` 子目录解构为「**真定位明确**的四类」：

```text
| 旧位置                              | 真实定位                                          | 新位置                                          | 操作        |
|------------------------------------|------------------------------------------------|----------------------------------------------|-------------|
| legacy/planner_rule.py              | 主路径活代码 + 三档子策略分发器                    | planning/planners/rule_planner.py            | smartRelocate + 改名 |
| legacy/ils_planner.py               | PLANNER_LLM_STRATEGY=hybrid + graph replan 兜底  | planning/planners/ils_planner.py             | smartRelocate |
| legacy/llm_first_planner.py         | PLANNER_LLM_STRATEGY=llm_first（默认）核心       | planning/planners/llm_first_planner.py       | smartRelocate |
| legacy/llm_planner.py               | PLANNER_LLM_STRATEGY=function_calling 子策略     | planning/planners/llm_planner.py             | smartRelocate |
| legacy/segment_decider.py           | ils_planner 依赖（被 planner_rule / replan 调）   | planning/planners/segment_decider.py         | smartRelocate |
| legacy/ils_score_critic.py          | ILS 路径专用 critic                              | planning/critic/ils_score_critic.py          | smartRelocate |
| legacy/prompts/llm_planner_prompt.py| llm_planner.py 的 system prompt                | planning/planners/prompts/llm_planner_prompt.py | smartRelocate |
| legacy/prompts/__init__.py          | （空包）                                         | planning/planners/prompts/__init__.py        | 重建（空文件）|
| legacy/__init__.py                  | 冻结纪律 docstring                              | （删除）                                       | delete_file |
| legacy/executor.py                  | 真死代码（与 graph/execute_finalize 等价）        | （删除）+ 2 测试改造调 execute_finalize_node    | delete + 改测试|
| legacy/ 整个目录                    | （误导的伞型分类）                              | （删除）                                       | delete_dir  |
| scripts/verify_legacy_frozen.py    | 守的是已不存在的目录                              | （删除）                                       | delete_file |
```

**对比 spec B**：spec B 是「重组到 6 子目录」（结构性整理）；spec D 是「修正 spec B 误把活代码当 legacy」（语义性纠错）。两者不冲突，spec D 仅扩展 spec B 的 `planning/` 子目录 + 删 `legacy/`。

**关键工具**：与 spec B 一样用 Kiro 的 `smartRelocate`——自动跟随式 import 更新（含 absolute + relative `from .X` + 内部链式 `from ..X`），避免手工 `git mv` + `sed` 漏边角。

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
└── legacy/                  ← ⚠ 误导：含 4 类完全不同的代码
    ├── planner_rule.py        ← 主路径活代码 + 三档子策略分发器
    ├── ils_planner.py         ← PLANNER_LLM_STRATEGY=hybrid + replan 兜底
    ├── llm_first_planner.py   ← PLANNER_LLM_STRATEGY=llm_first（默认！）
    ├── llm_planner.py         ← PLANNER_LLM_STRATEGY=function_calling
    ├── segment_decider.py     ← ils_planner 依赖
    ├── ils_score_critic.py    ← ILS 专用 critic
    ├── executor.py            ← **唯一真死代码**（与 graph 等价）
    └── prompts/
        └── llm_planner_prompt.py  ← llm_planner 依赖（不是孤儿）
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
│   │   ├── rule_planner.py            ← 重命名自 legacy/planner_rule.py（主路径分发器）
│   │   ├── ils_planner.py             ← 搬自 legacy/（hybrid 子策略 + replan 兜底）
│   │   ├── llm_first_planner.py       ← 搬自 legacy/（**默认 llm_first 子策略**）
│   │   ├── llm_planner.py             ← 搬自 legacy/（function_calling 子策略）
│   │   ├── segment_decider.py         ← 搬自 legacy/（ils_planner 依赖）
│   │   └── prompts/
│   │       ├── __init__.py
│   │       └── llm_planner_prompt.py  ← 搬自 legacy/prompts/
│   └── weights_llm.py       （spec B 不动；# FROZEN 注释保留）
├── runtime/                 （spec B 不动）
└── graph/                   （spec B 不动）
（legacy/ 整个目录已删；executor.py 已删；verify_legacy_frozen.py 已删）
```

### 关键决策（spec D 设计阶段拍板）

```text
| 决策点                                | 决定                              | 理由                                            |
|--------------------------------------|----------------------------------|------------------------------------------------|
| planner_rule.py 是否改名为 rule_planner.py | 是                              | 命名一致性：planners/ 下文件全部 *_planner.py 形式|
| 5 个 planner 是否合并                  | 否                                | 三档 PLANNER_LLM_STRATEGY 都是生产路径；合并需评估业务影响，不在本 spec 范围 |
| ils_score_critic 是否合并进 critics_v2 | 否                              | 维度不同——critics_v2 是 itinerary 全局校验；ils_score_critic 是 ILS 候选打分；功能不重叠|
| 删除 1 个真死代码（executor）          | 是（grep 已确认仅 2 测试 + 1 re-export，与 graph/execute_finalize 等价行为）| spec A R6 已落地等价 graph 节点 |
| llm_planner / llm_first_planner 是否删 | **不删**（修正版）                  | grep 完整审计后发现是 PLANNER_LLM_STRATEGY 三档子策略生产路径|
| 是否一次性 commit                     | 是                                | 与 spec B 一样的原子 commit 策略，便于回滚      |
| 解冻后是否清空 spec B 加的 # FROZEN 注释 | 是                                | 6 个迁移模块的 # FROZEN 注释全删 + docstring 改写为正确职责 |
| weights_llm.py 顶部的 # FROZEN 注释    | 保留（不在本 spec 范围；spec C R4 之后再讨论）| weights_llm 仍是 ILS 路径专用；本 spec 不改它的状态 |
| 是否删除 verify_legacy_frozen.py     | 是                                | 守的目录已不存在；保留是垃圾代码                |
| 内部相对引用迁移                      | smartRelocate 自动 + task 5 grep 复核| 含 from .planner_rule → from .rule_planner（rule_planner 改名后） + from .ils_score_critic → from ..critic.ils_score_critic（跨子目录）|
```

## Components and Interfaces

### Component 1: smartRelocate 7 处迁移（含 1 处改名 + 1 处跨子目录）

```text
| 旧路径                                            | 新路径                                                          | 改名 | 跨子目录 | smartRelocate 自动改 |
|--------------------------------------------------|---------------------------------------------------------------|------|---------|---------------------|
| backend/agent/legacy/planner_rule.py              | backend/agent/planning/planners/rule_planner.py               | 是   | 否      | 全仓库 import 自动跟随 |
| backend/agent/legacy/ils_planner.py               | backend/agent/planning/planners/ils_planner.py                | 否   | 否      | 同上                |
| backend/agent/legacy/llm_first_planner.py         | backend/agent/planning/planners/llm_first_planner.py          | 否   | 否      | 同上                |
| backend/agent/legacy/llm_planner.py               | backend/agent/planning/planners/llm_planner.py                | 否   | 否      | 同上                |
| backend/agent/legacy/segment_decider.py           | backend/agent/planning/planners/segment_decider.py            | 否   | 否      | 同上                |
| backend/agent/legacy/prompts/llm_planner_prompt.py| backend/agent/planning/planners/prompts/llm_planner_prompt.py | 否   | 否      | 同上                |
| backend/agent/legacy/ils_score_critic.py          | backend/agent/planning/critic/ils_score_critic.py             | 否   | **是**  | 同上                |
```

**关键 smartRelocate 注意点**：

1. **改名**（仅 1 处）：planner_rule.py → rule_planner.py。类/函数名不变；smartRelocate 自动改 `from agent.legacy.planner_rule import plan_itinerary` → `from agent.planning.planners.rule_planner import plan_itinerary`。
2. **跨子目录**（仅 1 处）：ils_score_critic 从 `planners/` 移到 `critic/`。其原内部相对引用 `from agent.legacy.ils_planner` 仍正常（被解冻迁移；smartRelocate 跨包跟随）。
3. **内部相对引用**（4 处需 smartRelocate 自动改）：
   - `legacy/planner_rule.py:1262 from .llm_planner import plan_itinerary_llm` → 同目录 `from .llm_planner import plan_itinerary_llm`（OK，仍同包）
   - `legacy/planner_rule.py:1290 from .ils_planner import plan_hybrid` → 同上
   - `legacy/planner_rule.py:1406 from .llm_first_planner import plan_llm_first` → 同上
   - `legacy/llm_planner.py:54 from .planner_rule import (...)` → **`from .rule_planner import (...)`**（改名导致）
   - `legacy/llm_planner.py:62 from .prompts.llm_planner_prompt import` → 同上（prompts/ 子目录跟随迁移）
   - `legacy/ils_planner.py:80 from .ils_score_critic import` → **`from ..critic.ils_score_critic import`**（跨子目录到 critic/）
   - `legacy/ils_planner.py:634 from .planner_rule import _resolve_time_window` → **`from .rule_planner import _resolve_time_window`**（改名导致）

   smartRelocate 应能自动处理大部分；task 5 必须 grep 复核。

### Component 2: 1 个真死代码删除（executor.py）+ 测试改造

#### 删除前完整 grep 审计（已完成）

```text
| 引用方                                | 引用形态                                          |
|--------------------------------------|------------------------------------------------|
| backend/tests/test_agent_flow.py:14   | from agent.legacy.executor import execute_plan |
| backend/tests/test_8_scenarios.py:23  | from agent.legacy.executor import execute_plan |
| backend/tests/test_import_paths.py:86 | from agent.legacy.executor import execute_plan（在 test_legacy_imports 内）|
| backend/agent/__init__.py:29          | from .legacy.executor import execute_plan, ExecutionResult（顶层 re-export）|
| __all__ 中包含 "execute_plan" / "ExecutionResult" 两项|
```

#### 改造方案（task 2）

1. 删除 `backend/agent/legacy/executor.py`
2. 改造 `backend/agent/__init__.py`：
   - 删除 `from .legacy.executor import execute_plan, ExecutionResult` 行
   - 删除 `__all__` 中的 `"execute_plan"` / `"ExecutionResult"` 两项
   - 在文件头注释把 `executor.py 执行类 Tool 派发` 改为 `（已废弃；执行类 Tool 派发由 graph/nodes/execute_finalize.py 替代）`
3. 改造 `tests/test_agent_flow.py` + `tests/test_8_scenarios.py`：把 `from agent.legacy.executor import execute_plan` + `result = execute_plan(itinerary=..., intent=..., ...)` 替换为 graph 主路径调用：

```python
# 改造后
from agent.graph.nodes.execute_finalize import execute_finalize_node

state = {"itinerary": itinerary, "intent": intent}
result_state = execute_finalize_node(state)
result = type("ExecResult", (), {
    "orders": result_state.get("orders", []),
    "share_message": result_state.get("share_message", ""),
    "narration": result_state.get("narration", ""),
    "itinerary": result_state.get("itinerary", itinerary),
    "success": True,  # graph 节点失败已被 try/except 兜底，到这里都是 success
})()
```

4. 改造 `tests/test_import_paths.py:test_legacy_imports`：删除 `from agent.legacy.executor import execute_plan` 一行（不放进 test_planning_planners_imports，因为模块已删）

**等价性保证**：execute_finalize_node 已经在 spec A R6+R7（Task 6）落地，含「reserve_restaurant 全量遍历 + generate_share_message + 调 generate_narration confirm 阶段」三件事——与 executor.execute_plan 行为完全等价（详见 graph/nodes/execute_finalize.py docstring）。

### Component 3: legacy/ 目录 + verify 脚本删除

```text
| 删除路径                                        | 删除时机                |
|------------------------------------------------|-----------------------|
| backend/agent/legacy/__init__.py               | task 4 末尾            |
| backend/agent/legacy/__pycache__/              | task 4 末尾            |
| backend/agent/legacy/prompts/__pycache__/      | task 3（搬完 llm_planner_prompt 后）|
| backend/agent/legacy/prompts/                  | task 3（搬完 llm_planner_prompt 后）|
| backend/agent/legacy/                          | task 4 末尾（确保空目录后删）|
| backend/scripts/verify_legacy_frozen.py        | task 5                |
| backend/scripts/verify_legacy_frozen.py 在 spec B 测试或 CI 中的引用| task 5（grep 后清理）|
```

### Component 4: 解冻 + docstring 改写（7 个文件）

迁移到 `planners/` / `critic/` 的 7 个文件 SHALL 顶部清理：

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
- 阶段 4：拼装 Itinerary（用 rule_planner 已有的 _resolve_time_window / _estimate / _assemble_itinerary helper）
- 阶段 5：失败 fallback 链 → hybrid → rule
"""

# planning/planners/llm_planner.py（搬自 legacy/）
"""PLANNER_LLM_STRATEGY=function_calling 子策略（旧实现，A/B 候选）。

被以下入口消费：
- `rule_planner.plan_itinerary_with_mode`（strategy="function_calling" 分支，line 1262）
- `tests/test_llm_planner.py`（4 个用例验证 function_calling 整体行为）

LLM Function Calling 自主调 Tool 的旧路径，自 LangGraph 主架构上线后仍保留作为
A/B 候选，但默认走 llm_first。所有新功能改动应在 graph/ 下完成。
"""

# planning/planners/segment_decider.py（搬自 legacy/）
"""ils_planner 的依赖——决定行程 segment 集合（亲子主路径 = 5 段、独处 = 3 段等）。

被以下入口消费：
- `ils_planner` 内部调（候选生成阶段）
- `rule_planner` 内部调（plan_itinerary 段集合决策）
- `graph/nodes/replan.py:ils_replan`（ILS 兜底前的段集合决策）
- `tests/test_node_decider.py`

新代码请优先用 `agent/planning/blueprint/node_decider.decide_nodes`（edge_v1 模型）；
本模块用于 stages 模型路径的兼容兜底。
"""

# planning/planners/prompts/llm_planner_prompt.py（搬自 legacy/prompts/）
"""LLM Function Calling 自主规划 system prompt（PLANNER_LLM_STRATEGY=function_calling 用）。

被以下入口消费：
- `llm_planner.py:62 from .prompts.llm_planner_prompt import LLM_PLANNER_SYSTEM_PROMPT`

与 intent/prompts/intent_parser_prompt 区别：本 prompt 给 planner LLM 用，
让它自主决定调哪个 Tool；intent_parser_prompt 是给意图解析 LLM 用。
"""

# planning/critic/ils_score_critic.py（搬自 legacy/）
"""ILS 候选打分专用 critic——CriticReport / run_critics。

与 critics_v2 维度不同：
- critics_v2.validate_itinerary：itinerary 全局校验（10 类 ViolationCode + 1 spec A R4 + 1 spec C R2）
- ils_score_critic.run_critics：ILS 候选打分（hard_constraint / time_window / budget / style 4 维评分）

被以下入口消费：
- `ils_planner` 内部调（候选打分阶段）
- `verify_planning.py` 验收脚本
- `tests/test_planner_hybrid.py` / `test_planner_hybrid_overload.py`
"""
```

## Data Models

本 spec 不引入新数据模型——只动文件位置 + 删 1 个文件 + 改测试导入。所有 Pydantic 模型保持现位置 / 现内容不变。

## Correctness Properties

### Property 1: 业务行为零变化

**Validates: Requirements 1.6, 2.5**

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
    from agent.planning.planners.llm_first_planner import plan_llm_first  # noqa: F401
    from agent.planning.planners.llm_planner import plan_itinerary_llm  # noqa: F401
    from agent.planning.planners.segment_decider import decide_segments, FULL_SEGMENTS  # noqa: F401
    from agent.planning.planners.prompts.llm_planner_prompt import LLM_PLANNER_SYSTEM_PROMPT  # noqa: F401
    from agent.planning.critic.ils_score_critic import run_critics, CriticReport  # noqa: F401
```

### Property 4: 死代码彻底清除

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 3.1, 3.2, 3.3**

WHEN 本 spec 完成 THEN：
- `backend/agent/legacy/` 目录不存在
- `backend/scripts/verify_legacy_frozen.py` 不存在
- `grep -r "legacy.executor\|legacy/executor" backend/ --include="*.py"` 0 命中
- `agent/__init__.py` 中 `from .legacy.executor` 行已删 + `__all__` 中 `execute_plan / ExecutionResult` 已删

### Property 5: AGENTS.md 一致性

**Validates: Requirements 4.1, 4.2, 4.3**

WHEN 本 spec 完成 THEN AGENTS.md §3.3.1 内：
- `legacy/` 段已删
- `planning/planners/` 段（5 文件 + prompts/llm_planner_prompt.py）已加
- `planning/critic/ils_score_critic.py` 已加
- MUST NOT 段不再含「在 agent/legacy/ 加新功能」「verify_legacy_frozen 守护」「FROZEN 标记」相关条款

### Property 6: spec C 锚点同步

**Validates: Requirements 5.1, 5.2, 5.3**

WHEN 本 spec 完成 THEN `.kiro/specs/algorithm-redesign/{requirements,design,tasks}.md` SHALL grep `agent/legacy/` 0 命中；所有 ils_planner 引用都在 `planning/planners/ils_planner.py`。

## Testing Strategy

### 1. baseline（task 1 前；已在起草版 task 1 完成）

```bash
pytest backend/tests/ -v --tb=short > /tmp/baseline_pre_d.txt 2>&1   # 599 passed + 1 skipped
python backend/scripts/verify_planning.py                              # ✓
python backend/scripts/verify_planning_quality.py                      # ✓ 24/24
python backend/scripts/verify_edge_model.py                            # ✓ 4/4
python backend/scripts/verify_legacy_frozen.py                         # ✓（删前最后一次）
python -c "import main; print(main.app)"                                # ✓ FastAPI app loaded
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
python -c "import main; print(main.app)"
cd frontend && pnpm verify:all  # 前端不依赖后端 import 路径但仍验证
grep -rn "from backend\.agent\.legacy\|from agent\.legacy" backend/ --include="*.py"  # 必须 0 命中
grep -rn "from \.legacy\." backend/ --include="*.py"  # 必须 0 命中
```

### 4. 改造后的测试覆盖矩阵

```text
| 测试文件                              | 改动类型                                              | 验证                                |
|--------------------------------------|------------------------------------------------------|-----------------------------------|
| tests/test_agent_flow.py              | from agent.legacy.executor → execute_finalize_node 改造| reserve + share + narration 行为等价|
| tests/test_8_scenarios.py             | 同上                                                  | 8 场景端到端跑通 + E1/E2 异常分支     |
| tests/test_import_paths.py            | test_legacy_imports → test_planning_planners_imports（含 5 + 1 prompt + 1 critic）；旧路径 ImportError 反向断言加 7 项 | 7 个迁移模块新路径可 import + 7 个旧路径全 ImportError |
| tests/test_llm_planner.py             | 仅 from agent.legacy.llm_planner → from agent.planning.planners.llm_planner（smartRelocate 自动）| 4 个用例继续过 |
| tests/test_node_decider.py            | from agent.legacy.segment_decider 自动改               | smartRelocate 自动处理              |
| tests/test_planner_hybrid_overload.py | from agent.legacy.ils_score_critic / ils_planner 自动改| smartRelocate 自动处理              |
| tests/test_planner_hybrid.py          | from agent.legacy.ils_score_critic / planner_rule（→ rule_planner 改名） 自动改 | smartRelocate 自动处理 |
| tests/test_refiner_duration_consistency.py | from agent.legacy.planner_rule（→ rule_planner 改名）自动改 | smartRelocate 自动处理         |
| backend/main.py:1740                  | from agent.legacy.planner_rule（→ rule_planner 改名）自动改 | smartRelocate 自动处理         |
| backend/collab/room.py 2 处            | 同上                                                  | 同上                              |
| backend/agent/graph/nodes/replan.py 5 处| 同上（含 from agent.legacy.segment_decider / ils_planner / planner_rule）| smartRelocate 自动处理 |
| backend/scripts/verify_planning.py    | 同上                                                  | 同上                              |
| backend/scripts/verify_llm_first.py   | 同上                                                  | 同上                              |
| backend/scripts/analyze_overload_coefficient.py | 同上                                          | 同上                              |
| backend/agent/__init__.py             | 删除 from .legacy.executor + __all__ 两项 + 改 from .legacy.X → from .planning.planners.X 等 | 顶层 re-export 重写 |
```

### 5. 不做的测试

- 不做大规模性能测试（hackathon 时间盒不允许）
- 不做 LLM 真链路 e2e（DEEPSEEK_API_KEY 未必配置；stub 模式覆盖即可）
- 不重新跑 spec A R10 验证脚本（业务行为零变化，spec A 已通过）

## Error Handling

```text
| 风险                                              | 概率 | 影响 | 缓解                                                 |
|---------------------------------------------------|------|-----|----------------------------------------------------|
| smartRelocate 漏改某个相对引用（含 from .X / from ..X 跨包）| 中 | 中 | 分 task 推进 + 每 task pytest -x 立即定位；task 5 grep 复核|
| executor.py 删除后测试改造行为不等价              | 中   | 高  | task 2 改造时跑 test_agent_flow + test_8_scenarios 详细验证；不等价立刻停 |
| ils_score_critic 跨子目录搬到 critic/ 后 import 漏改| 低 | 中  | smartRelocate 自动改 + task 5 grep 复核 from ..critic.ils_score_critic|
| AGENTS.md §3.3.1 漏改某条 MUST NOT                | 中   | 低  | task 5 grep「legacy」「FROZEN」全检（5+ 处）        |
| spec C 三件套同步遗漏                             | 中   | 中  | task 5 用 grep + str_replace 批量替换           |
| backend/main.py:1740 改后真 LLM 链路启动失败       | 低   | 高  | task 5 跑 `python -c "import main; print(main.app)"` |
| collab/room.py 改后协作功能崩                     | 低   | 中  | task 5 跑 collab 相关测试（如有）                    |
| ils_score_critic 搬到 planning/critic/ 与 critics_v2 命名混淆 | 低 | 低  | docstring 写明维度差异；保持函数名 `run_critics` 不变 |
| Wave 5 末尾 verify_planning_quality 红灯           | 低   | 高  | 立即回滚（git restore .）；hackathon 时间盒不允许返工 |
| 解冻后某模块顶部 docstring 改写漏掉              | 低   | 低  | task 5 grep `# FROZEN` 整仓库（应仅剩 weights_llm.py 一处保留）|
| 起草版本误判（llm_planner / llm_first_planner 误删）| 0（已修正） | -    | 起草版的「3 个真死代码」已修正为「1 个真死代码」    |
```

## Decisions Log

```text
| 决策                                | 决定                              | 来源                              |
|------------------------------------|----------------------------------|----------------------------------|
| spec D 是否在 spec C 启动前做         | 是（前置硬约束）                    | spec C R3+R4 锚点要改             |
| 解冻后保留 # FROZEN 注释还是删除     | 删除（6 个迁移模块全部）            | 解冻语义即不冻结，留注释名实不符  |
| weights_llm.py 顶部的 # FROZEN 注释   | 保留不动                          | 不在本 spec 范围；它仍是 ILS 路径专用|
| ils_score_critic 是否合并进 critics_v2 | 否                                | 维度不同（候选打分 vs 全局校验）    |
| llm_planner / llm_first_planner 是否删 | **否（修正版）**                   | grep 完整审计后确认是 PLANNER_LLM_STRATEGY 三档子策略生产路径|
| 一次性 commit 还是分批 commit         | 一次性原子 commit                  | 与 spec B 一致；便于回滚          |
| 改名策略（planner_rule → rule_planner）| 是                                | planners/ 下统一 *_planner.py 命名|
| ils_planner.py 是否改名                | 否                                | 命名已足够清晰                    |
| llm_planner / llm_first_planner / segment_decider 是否改名| 否            | 命名已足够清晰                    |
| executor.py 测试改造路径               | 调 graph/nodes/execute_finalize_node | 已落地等价行为                  |
| graph/build.py 拓扑                    | 不动（spec B 锁）                  | 编排冻结纪律                      |
```

## Risk Assessment

```text
| 风险                                              | 概率 | 影响 | 缓解                                                |
|---------------------------------------------------|------|-----|----------------------------------------------------|
| baseline 阶段发现 spec A/B 已有红灯                 | 已过 | 高  | task 1 已跑 ✓                                      |
| smartRelocate 在改名（planner_rule→rule_planner）时挂掉| 低 | 中  | 该名字仅 1 处改名；smartRelocate 已在 spec B 用过 6 次  |
| 测试改造（executor → execute_finalize）行为不等价   | 中   | 高  | task 2 单独验证；不等价立刻 git restore           |
| backend/main.py 改后 FastAPI 启动失败              | 低   | 高  | task 5 必跑 `python -c "import main; print(main.app)"` |
| AGENTS.md §3.3.1 重写漏改某条 MUST NOT             | 中   | 低  | task 5 grep 「legacy」全文（5+ 处）                  |
| spec C tasks.md 锚点同步遗漏                       | 中   | 中  | task 5 grep + 人工核对 4 处                       |
| 一次性 commit 中途出现导致回滚困难                  | 低   | 低  | task 5 末才 commit；前 4 task 失败直接 git restore .|
| 改 main.py:1740 时漏改其他位置                     | 低   | 中  | smartRelocate 自动改全仓库 + task 5 grep         |
| ils_score_critic 跨子目录搬时 import 路径漂移       | 低   | 中  | smartRelocate 自动改 + task 5 grep 复核 ..critic.ils_score_critic|
| 起草版编排者犯重组审计错（已发现）                  | 0（已修正）| - | spec D requirements / design / tasks 已重写        |
```

## Estimated Effort

```text
| 任务                                               | 工时   | 备注                                          |
|---------------------------------------------------|-------|-----------------------------------------------|
| Task 1: baseline + git tag v-spec-d-start        | 0.2h  | 已完成（task 1 已跑通 ✓）                       |
| Task 2: 删除 1 个真死代码（executor）+ 测试改造     | 0.7h  | delete_file + agent/__init__.py 清理 + 2 测试改造|
| Task 3: smartRelocate 7 处迁移（含 1 改名 + 1 跨子目录）+ docstring 改写| 1.2h  | 7 次 smartRelocate + 顶部注释清理 + 7 段 docstring 改写|
| Task 4: 删除 legacy/ 目录 + verify_legacy_frozen.py| 0.3h  | rmdir 空目录 + delete_file                    |
| Task 5: 升级 test_import_paths + AGENTS.md / spec C / 文档同步 + commit| 1.1h | 改测试 + grep 旧路径 + 改 4 处文档锚点 + 原子 commit |
| **总计**                                          | **3.5h**| ≈ 半人天偏长（hackathon 时间盒可承受）         |
```

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
