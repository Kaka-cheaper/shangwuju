# Design: Agent Directory Restructure

## Overview

把 `backend/agent/` 从「25 个扁平 .py + v2/ + graph/ 三套并存」重组为「5 子目录 + legacy/」分层结构。本 spec **只动文件位置 + import 路径**，不改业务逻辑、不改 schema、不改 prompt 内容。

**核心工具**：Kiro 的 `smartRelocate`——自动跟随式 import 更新（VS Code 内置 file move + reference rewrite），避免手工 `git mv` + `sed` 漏边角。

**预估工时**：3h（adversarial-review §6 末尾建议「砍到 3h」）+ 1h pytest 验证缓冲 = 4h。比 Phase 3 估的 6h 砍一半，因为：

- spec A 已落地业务质量修复——文件内容稳定，重组只是搬位置
- 不重组 `tests/` / `mock_data/` / `graph/build.py`，改动面收窄
- smartRelocate 一次性多文件移动 + 自动 import 更新，不需要手工

## Architecture

### 重组前 vs 重组后目录树对比

```text
重组前（混乱）：
backend/agent/
├── __init__.py
├── llm_client.py         ←┐
├── llm_client_stub.py    │ 5 个底座文件散在顶层
├── observability_init.py │
├── feedback_detector.py  │
├── trace.py              ←┘
├── intent_parser.py      ←┐
├── refiner.py            │ 4 个意图层散在顶层
├── router.py             │
├── narrator.py           ←┘
├── blueprint.py          ←┐
├── blueprint_llm.py      │ 4 个蓝图层散在顶层（与 v2/ 并存）
├── assemble_blueprint.py │
├── node_decider.py       ←┘
├── lookup_hop.py         ←  通勤层散在顶层
├── weights_llm.py        ←  ILS 权重散在顶层
├── critics.py            ←┐
├── planner.py            │
├── planner_hybrid.py     │ 4 套 planner + 旧 critic 散在顶层
├── planner_llm_first.py  │ ↑ 已 FROZEN 但目录结构未体现
├── llm_planner.py        │
├── executor.py           │
├── segment_decider.py    ←┘
├── prompts/              ← 5 个 prompts 混放
│   ├── system_prompt.py        ← 实际是 intent parser 的
│   ├── refiner_prompt.py
│   ├── router_prompt.py
│   ├── narrator_prompt.py
│   └── blueprint_prompt.py
├── tools/
│   └── search_adapter.py
├── v2/                   ← Pydantic AI fallback（含 9 个 .py）
│   ├── react_agent.py
│   ├── output_types.py
│   ├── orchestrator.py
│   ├── conversation.py
│   ├── tool_provider.py
│   ├── deps.py
│   ├── model_factory.py
│   ├── observability.py
│   ├── critics_v2.py     ← 业务 critic（被 graph/ 共用）
│   └── social_compat.py
└── graph/                ← LangGraph 主路径（不动）
    ├── build.py
    ├── state.py
    ├── sse_adapter.py
    └── nodes/ (11 个 nodes)
```

```text
重组后（5 子目录 + legacy/）：
backend/agent/
├── __init__.py            ← 仍然解锁，可选 ≤5 个 re-export
│
├── core/                  ── 全员共享底座（5 文件）
│   ├── __init__.py
│   ├── llm_client.py
│   ├── llm_client_stub.py
│   ├── observability_init.py
│   ├── feedback_detector.py
│   └── trace.py
│
├── intent/                ── 意图理解 + 反馈刷新 + 文案输出（4 文件 + prompts/）
│   ├── __init__.py
│   ├── parser.py          ← 重命名自 intent_parser.py
│   ├── refiner.py
│   ├── router.py
│   ├── narrator.py        ← 与 SOCIAL_CONTEXTS 强耦合，归 intent/
│   └── prompts/
│       ├── __init__.py
│       ├── intent_parser_prompt.py  ← 重命名自 system_prompt.py
│       ├── refiner_prompt.py
│       ├── router_prompt.py
│       └── narrator_prompt.py
│
├── planning/              ── 规划主路径（蓝图 + critic + 通勤）
│   ├── __init__.py
│   ├── blueprint/
│   │   ├── __init__.py
│   │   ├── blueprint.py             ← 含 _age_aware_duration_critic（spec A R4 产物）
│   │   ├── blueprint_llm.py         ← 含改后的 _poi_preview / _restaurant_preview（spec A R2 产物）
│   │   ├── assemble_blueprint.py
│   │   ├── node_decider.py          ← 仅决 kind，不决时长（拒升级）
│   │   └── prompts/
│   │       ├── __init__.py
│   │       └── blueprint_prompt.py  ← 含 spec A R3 改后的范例 + 分级表
│   ├── critic/
│   │   ├── __init__.py
│   │   ├── critics_v2.py            ← 含 AGE_DURATION_MISMATCH 镜像（spec A R4 产物）
│   │   └── social_compat.py
│   ├── commute/
│   │   ├── __init__.py
│   │   └── lookup_hop.py
│   └── weights_llm.py               ← 文件顶部加 # FROZEN: 仅 ILS 路径
│
├── runtime/               ── Pydantic AI ReAct 运行时框架（不是业务）
│   ├── __init__.py
│   ├── react_agent.py
│   ├── output_types.py
│   ├── orchestrator.py
│   ├── conversation.py
│   ├── tool_provider.py
│   ├── deps.py
│   ├── model_factory.py
│   ├── observability.py             ← Pydantic AI 专用（与 core/observability_init.py 不同）
│   └── tools/
│       ├── __init__.py
│       └── search_adapter.py
│
├── graph/                 ── LangGraph 主路径（不动）
│   ├── __init__.py
│   ├── build.py                     ← 拓扑不动
│   ├── state.py                     ← 已含 spec A R6 的 routes 字段删除
│   ├── sse_adapter.py               ← 已含 spec A R6 的 DONE payload 6 字段
│   └── nodes/ (11 个 nodes)         ← 已含 spec A R6 的 narrate_node / refiner_node 改动
│
└── legacy/                ── 冻结模块（每个文件顶部加 # FROZEN 注释）
    ├── __init__.py        ← docstring 说明冻结纪律
    ├── planner_rule.py    ← 重命名自 planner.py
    ├── ils_planner.py     ← 重命名自 planner_hybrid.py
    ├── llm_first_planner.py ← 重命名自 planner_llm_first.py
    ├── llm_planner.py
    ├── ils_score_critic.py ← 重命名自 critics.py
    ├── executor.py        ← 与 graph/nodes/execute_finalize.py 双轨
    └── segment_decider.py ← 兼容 alias
```

### 决策日志（design 阶段拍板的 5 个细节）

**决策 D-RES-1：narrator 归 intent/ 而非 runtime/**

- 候选方案：
  - A. narrator 归 `intent/`（与 SOCIAL_CONTEXTS 词典共用，本 spec 采纳）
  - B. narrator 归 `runtime/`（Agent H 报告 §5 建议）
- 理由：narrator 与 intent 层 SOCIAL_CONTEXTS / role 词典强耦合，归 intent/ 更内聚。`runtime/` 应当只包含 Pydantic AI ReAct **框架**模块（react_agent / orchestrator / conversation 等），而非任何业务输出。
- 放弃 B 的代价：runtime/ 模块更纯，但 narrator 与 intent_parser 的 SOCIAL_CONTEXTS 引用要跨子目录——可接受。

**决策 D-RES-2：node_decider 留在 planning/blueprint/ 而非升级到 NodePlanHint**

- 候选方案：
  - A. node_decider 文件移到 `planning/blueprint/node_decider.py`（仅决 kind，本 spec 采纳）
  - B. 升级为 NodePlanHint 决时长 + kind 双职责（Agent A 报告方案 B）
- 理由：adversarial-review §2 冲突 1 已拒升级，理由是过度设计 + 与 D9 编排冻结部分冲突。本 spec 只重组**位置**，不变更业务行为。
- 放弃 B 的代价：略，已在 adversarial-review §8.3 明确"不进 spec"。

**决策 D-RES-3：legacy/ 而非 frozen/ / deprecated/ / archive/**

- 候选方案：
  - A. `legacy/`（本 spec 采纳）
  - B. `frozen/`（语义最强，但与 § AGENTS.md 已用 "FROZEN" 注释重复）
  - C. `deprecated/`（暗示"将删除"，但实际我们不删）
  - D. `archive/`（暗示"已退役"，但 fallback 链路仍消费）
- 理由：legacy 同时表达"老代码 + 仍在用 + 不动它"三层语义，与 fallback 链路实际行为吻合。FROZEN 注释保留在文件内部作为 grep-able 标记。
- 放弃 BCD 的代价：均无显著差异，命名偏好。

**决策 D-RES-4：分 6 批次推进 + 每批跑 pytest，不一次性大批量 mv**

- 候选方案：
  - A. 6 批次（本 spec 采纳）
  - B. 一次性 smartRelocate 全 25 个文件
  - C. 不分批，按文件类型一次过
- 理由：smartRelocate 虽然能跟随 import 更新，但跨子目录时偶发 edge case（比如 `from . import xxx` 相对引用）；分批让 pytest 在每个安全点验证一次，便于定位 import 漏。
- 放弃 BC 的代价：一次性 mv 风险更高，定位变难——在 hackathon 后期不可接受。

**决策 D-RES-5：保留 `weights_llm.py` 单文件，不拆 weights/ 子目录**

- 候选方案：
  - A. `planning/weights_llm.py` 单文件（本 spec 采纳）
  - B. `planning/weights/llm_weights.py` + `planning/weights/rule_weights.py`
- 理由：weights_llm.py 当前仅 ILS 路径消费，单一职责，文件不大；拆子目录是过度设计。
- 放弃 B 的代价：未来若需要拆 ILS / DP / set packing 多套权重，再开 spec 拆。

## Components and Interfaces

### 关键迁移路径表

```text
| 旧路径                                            | 新路径                                                       | 操作        | 改名? |
|---------------------------------------------------|------------------------------------------------------------|-------------|-------|
| backend/agent/llm_client.py                       | backend/agent/core/llm_client.py                           | smartRelocate | 否    |
| backend/agent/llm_client_stub.py                  | backend/agent/core/llm_client_stub.py                      | smartRelocate | 否    |
| backend/agent/observability_init.py               | backend/agent/core/observability_init.py                   | smartRelocate | 否    |
| backend/agent/feedback_detector.py                | backend/agent/core/feedback_detector.py                    | smartRelocate | 否    |
| backend/agent/trace.py                            | backend/agent/core/trace.py                                | smartRelocate | 否    |
| backend/agent/intent_parser.py                    | backend/agent/intent/parser.py                             | smartRelocate | 是    |
| backend/agent/refiner.py                          | backend/agent/intent/refiner.py                            | smartRelocate | 否    |
| backend/agent/router.py                           | backend/agent/intent/router.py                             | smartRelocate | 否    |
| backend/agent/narrator.py                         | backend/agent/intent/narrator.py                           | smartRelocate | 否    |
| backend/agent/prompts/system_prompt.py            | backend/agent/intent/prompts/intent_parser_prompt.py       | smartRelocate | 是    |
| backend/agent/prompts/refiner_prompt.py           | backend/agent/intent/prompts/refiner_prompt.py             | smartRelocate | 否    |
| backend/agent/prompts/router_prompt.py            | backend/agent/intent/prompts/router_prompt.py              | smartRelocate | 否    |
| backend/agent/prompts/narrator_prompt.py          | backend/agent/intent/prompts/narrator_prompt.py            | smartRelocate | 否    |
| backend/agent/blueprint.py                        | backend/agent/planning/blueprint/blueprint.py              | smartRelocate | 否    |
| backend/agent/blueprint_llm.py                    | backend/agent/planning/blueprint/blueprint_llm.py          | smartRelocate | 否    |
| backend/agent/assemble_blueprint.py               | backend/agent/planning/blueprint/assemble_blueprint.py     | smartRelocate | 否    |
| backend/agent/node_decider.py                     | backend/agent/planning/blueprint/node_decider.py           | smartRelocate | 否    |
| backend/agent/prompts/blueprint_prompt.py         | backend/agent/planning/blueprint/prompts/blueprint_prompt.py | smartRelocate | 否    |
| backend/agent/v2/critics_v2.py                    | backend/agent/planning/critic/critics_v2.py                | smartRelocate | 否    |
| backend/agent/v2/social_compat.py                 | backend/agent/planning/critic/social_compat.py             | smartRelocate | 否    |
| backend/agent/lookup_hop.py                       | backend/agent/planning/commute/lookup_hop.py               | smartRelocate | 否    |
| backend/agent/weights_llm.py                      | backend/agent/planning/weights_llm.py                      | smartRelocate | 否    |
| backend/agent/v2/react_agent.py                   | backend/agent/runtime/react_agent.py                       | smartRelocate | 否    |
| backend/agent/v2/output_types.py                  | backend/agent/runtime/output_types.py                      | smartRelocate | 否    |
| backend/agent/v2/orchestrator.py                  | backend/agent/runtime/orchestrator.py                      | smartRelocate | 否    |
| backend/agent/v2/conversation.py                  | backend/agent/runtime/conversation.py                      | smartRelocate | 否    |
| backend/agent/v2/tool_provider.py                 | backend/agent/runtime/tool_provider.py                     | smartRelocate | 否    |
| backend/agent/v2/deps.py                          | backend/agent/runtime/deps.py                              | smartRelocate | 否    |
| backend/agent/v2/model_factory.py                 | backend/agent/runtime/model_factory.py                     | smartRelocate | 否    |
| backend/agent/v2/observability.py                 | backend/agent/runtime/observability.py                     | smartRelocate | 否    |
| backend/agent/tools/search_adapter.py             | backend/agent/runtime/tools/search_adapter.py              | smartRelocate | 否    |
| backend/agent/planner.py                          | backend/agent/legacy/planner_rule.py                       | smartRelocate | 是    |
| backend/agent/planner_hybrid.py                   | backend/agent/legacy/ils_planner.py                        | smartRelocate | 是    |
| backend/agent/planner_llm_first.py                | backend/agent/legacy/llm_first_planner.py                  | smartRelocate | 是    |
| backend/agent/llm_planner.py                      | backend/agent/legacy/llm_planner.py                        | smartRelocate | 否    |
| backend/agent/critics.py                          | backend/agent/legacy/ils_score_critic.py                   | smartRelocate | 是    |
| backend/agent/executor.py                         | backend/agent/legacy/executor.py                           | smartRelocate | 否    |
| backend/agent/segment_decider.py                  | backend/agent/legacy/segment_decider.py                    | smartRelocate | 否    |
```

总计 38 次 smartRelocate 操作，其中 6 次涉及改名（intent_parser.py / planner.py / planner_hybrid.py / planner_llm_first.py / critics.py / system_prompt.py）。

### smartRelocate 工具用法

Kiro 的 `smartRelocate` 工具调用模板：

```text
sourcePath: d:\桌面\美团AI Hackathon\backend\agent\<旧路径>.py
destinationPath: d:\桌面\美团AI Hackathon\backend\agent\<新路径>.py
```

工具行为：
- 自动创建目标目录（如果不存在）
- 自动更新所有 `from backend.agent.<旧路径> import` 引用为 `from backend.agent.<新路径> import`
- 自动更新相对引用 `from . import xxx` / `from ..xxx import yyy`
- 不动文件内容（只动位置 + 引用）

**注意**：smartRelocate 改名（如 intent_parser.py → parser.py）时，**类/函数名不变**——只动文件名。所有 `from backend.agent.intent_parser import IntentParser` 自动变成 `from backend.agent.intent.parser import IntentParser`。

### __init__.py 处理

每个新子目录创建空 `__init__.py`：
- `core/__init__.py`
- `intent/__init__.py`
- `intent/prompts/__init__.py`
- `planning/__init__.py`
- `planning/blueprint/__init__.py`
- `planning/blueprint/prompts/__init__.py`
- `planning/critic/__init__.py`
- `planning/commute/__init__.py`
- `runtime/__init__.py`
- `runtime/tools/__init__.py`
- `legacy/__init__.py`（含 docstring 说明冻结纪律）

**根 `backend/agent/__init__.py` 处理**：保持解锁状态（不重新加 eager-import）。如果有 ≤ 5 个公开 API 需要稳定（如已被外部模块用 `from backend.agent import LLMClient` 导入），在 `__init__.py` 内显式列出 re-export，否则空。

**legacy/__init__.py docstring**（设计稿）：

```python
"""Legacy / FROZEN agent modules.

These modules are kept for fallback / safety-net behavior only.
DO NOT add new features here. New code goes in:
- intent/ for intent understanding
- planning/ for planning main path
- graph/ for LangGraph nodes
- runtime/ for Pydantic AI framework
- core/ for shared infrastructure

See AGENTS.md §3.3.1 for the freeze discipline.
"""
```

### verify_legacy_frozen.py 验收脚本（设计稿）

```python
"""Verify all legacy/ modules carry the # FROZEN marker comment."""

from __future__ import annotations
import sys
from pathlib import Path

LEGACY_DIR = Path(__file__).resolve().parent.parent / "agent" / "legacy"
MARKER = "# FROZEN: 详见 AGENTS.md §3.3.1"


def main() -> int:
    missing: list[str] = []
    for py_file in LEGACY_DIR.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        head = py_file.read_text(encoding="utf-8").splitlines()[:20]
        if not any(MARKER in line for line in head):
            missing.append(str(py_file.relative_to(LEGACY_DIR.parent.parent)))
    if missing:
        print("FROZEN marker missing in:")
        for m in missing:
            print(f"  - {m}")
        return 1
    print(f"OK: all {len(list(LEGACY_DIR.glob('*.py'))) - 1} legacy modules carry FROZEN marker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

## Data Models

本 spec 不引入新数据模型——只动文件位置。所有 Pydantic 模型（IntentExtraction / Itinerary / SuggestedDuration 等）保持现位置 / 现内容不变（这些在 spec A 已落地）。

## Error Handling

### 错误场景 1：smartRelocate 中途失败

**触发**：smartRelocate 工具报错（如目标目录不存在但创建失败、git 工作区脏导致冲突等）。

**处理**：

1. 立即停止后续 smartRelocate 调用
2. 跑 `git status` 列出已挪动的文件
3. 跑 `git restore <已挪动文件>` 撤销本次操作
4. 报告失败位置 + 原因，由用户决定下一步

### 错误场景 2：批次结束 pytest 红灯

**触发**：某批次 smartRelocate 全部完成后跑 pytest，有测试失败（最常见原因：smartRelocate 漏改某个相对引用）。

**处理**：

1. 立即停止后续批次
2. 输出失败的测试名 + traceback 中命中的旧 import 路径
3. 用户决定是手工修复（grep + 改一行）还是回滚整批（`git restore .`）
4. **禁止跨批次修复**——如果批 2 出错，不能在批 3 里"顺手"把批 2 漏的 import 也修了

### 错误场景 3：FastAPI 启动后 `/health` 不返回 200

**触发**：所有 6 批次完成 + pytest 全过，但 `python -m backend.main` 启动后某个 router 模块 import 出错。

**处理**：

1. 跑 `python -c "from backend.main import app"` 单独验证 import
2. 如果失败，traceback 会指出哪个模块（通常是 `backend/api/` 下的 router）漏改路径
3. 手工修复后重启验证
4. 这是预期可能漏点（pytest 不会发现所有 import 错误，运行时才发现）——所以验收清单 §4.1 末尾必须包含 FastAPI 启动 + curl /health。

### 错误场景 4：前端构建失败

**触发**：`npm run build` 失败（理论上不应发生，前端不依赖后端 import）。

**处理**：检查是否有任何配置文件（如 `frontend/.env.local`）硬编码了后端 import 路径——这种概率极低，但作为最后一道防线列出。

## Testing Strategy

### 1. 测试基线（重组前）

执行 spec B **第一步**：跑全套测试 + 启动 FastAPI + 启动前端，记录基线（必须全绿）。如果重组前已有红灯，**立即停止 spec B**，先修复红灯再启动。

```bash
# 后端测试
cd backend
pytest tests/ -v --tb=short > /tmp/baseline_pytest.txt 2>&1
python scripts/verify_planning.py >> /tmp/baseline_verify.txt
python scripts/verify_edge_model.py >> /tmp/baseline_verify.txt

# FastAPI 启动
python -m backend.main &
sleep 3 && curl -s http://localhost:8000/health
kill %1
```

### 2. 每批次后的 smoke test（增量验证）

每批次结束跑：

```bash
pytest backend/tests/ -x --tb=short
```

`-x` 模式遇到第一个失败立即停（不浪费时间跑后续）。

### 3. 全部完成后的完整验证（acceptance test）

```bash
pytest backend/tests/ -v --tb=short
python backend/scripts/verify_planning.py
python backend/scripts/verify_edge_model.py
python backend/scripts/verify_legacy_frozen.py  # 新增
python -m backend.main & sleep 3 && curl -s http://localhost:8000/health
cd frontend && npm run build
```

### 4. 新增测试：tests/test_import_paths.py

显式断言新路径可 import（防回归）：

```python
"""Verify new agent/ subdirectory imports work after restructure."""

def test_core_imports() -> None:
    from backend.agent.core.llm_client import LLMClient  # noqa: F401
    from backend.agent.core.feedback_detector import looks_like_feedback  # noqa: F401
    from backend.agent.core.trace import DecisionTrace  # noqa: F401


def test_intent_imports() -> None:
    from backend.agent.intent.parser import IntentParser  # noqa: F401
    from backend.agent.intent.refiner import RefinerState  # noqa: F401
    from backend.agent.intent.router import RouterDecision  # noqa: F401
    from backend.agent.intent.narrator import generate_narration  # noqa: F401


def test_planning_imports() -> None:
    from backend.agent.planning.blueprint.blueprint import Blueprint  # noqa: F401
    from backend.agent.planning.blueprint.blueprint_llm import _poi_preview  # noqa: F401
    from backend.agent.planning.blueprint.node_decider import decide_node_kind  # noqa: F401
    from backend.agent.planning.critic.critics_v2 import ViolationCode  # noqa: F401
    from backend.agent.planning.commute.lookup_hop import lookup_hop  # noqa: F401


def test_runtime_imports() -> None:
    from backend.agent.runtime.react_agent import ReActAgent  # noqa: F401
    from backend.agent.runtime.tools.search_adapter import build_search_adapter  # noqa: F401


def test_legacy_imports() -> None:
    from backend.agent.legacy.planner_rule import plan_itinerary  # noqa: F401
    from backend.agent.legacy.ils_planner import _utility  # noqa: F401
    from backend.agent.legacy.executor import execute_itinerary  # noqa: F401


def test_old_paths_no_longer_exist() -> None:
    """Old import paths should fail."""
    import importlib
    for old_path in [
        "backend.agent.intent_parser",
        "backend.agent.blueprint",
        "backend.agent.planner",
        "backend.agent.v2.react_agent",
        "backend.agent.tools.search_adapter",
    ]:
        try:
            importlib.import_module(old_path)
        except ImportError:
            continue
        raise AssertionError(f"old path {old_path} should not be importable")
```

### 5. 不做的测试

- 不做 mocked planning quality 端到端验证（spec A 已做）
- 不做 LLM 调用相关测试（重组不动业务）
- 不做 narrator 输出多样性验证（spec A 已做）

## Correctness Properties

### Property 1: 业务行为零变化

**Validates: Requirements 4.4, 4.5**

**验证方法**：重组前后跑同一个 verify_planning.py，输出 diff 应为空。

### Property 2: pytest 0 红灯

**Validates: Requirements 2.4, 4.1, 4.2**

**验证方法**：每批次后 `pytest backend/tests/ -x --tb=short` 必须 exit 0。

### Property 3: FastAPI 启动 0 错误

**Validates: Requirements 2.5, 4.4**

**验证方法**：`python -m backend.main` 启动后 `curl http://localhost:8000/health` 返回 200。

### Property 4: 前端构建 0 红灯

**Validates: Requirements 4.4**

**验证方法**：`cd frontend && npm run build` 必须 exit 0。

### Property 5: 旧路径不再可 import

**Validates: Requirements 2.2, 2.4**

**验证方法**：`tests/test_import_paths.py::test_old_paths_no_longer_exist` 通过——`importlib.import_module("backend.agent.intent_parser")` 等老路径必须 ImportError。

### Property 6: legacy/ 全部含 FROZEN 标记

**Validates: Requirements 3.1, 3.4, 3.5**

**验证方法**：`python backend/scripts/verify_legacy_frozen.py` 必须 exit 0——grep 出 7 个 legacy `.py` 全含 `# FROZEN: 详见 AGENTS.md §3.3.1` 注释。

## Migration & Compatibility

### git 提交策略

- spec B 全部 6 批次完成后，**一次性原子 commit**——回滚便利
- commit message: `refactor(agent): restructure into 5 subdirs + legacy/ for clarity`
- **不**在中途 commit（避免半成品出现在 git 历史中）

### 回滚策略

如果 spec B 全部完成 + 测试通过 + commit 后，evening rehearsal / demo 现场出现意外问题（极低概率，已被前置验证覆盖），回滚操作：

```bash
# 回滚到 spec B 之前的状态
git revert <spec-b-commit-sha>

# 或者
git reset --hard v-spec-a-done  # 回到 spec A 完成的 tag
```

### 与外部代码的兼容性

**外部消费方**（`backend/api/` / `backend/main.py`）的 import 路径会被 smartRelocate 自动更新，无需手工。

**前端**（`frontend/`）不依赖后端 import 路径，0 影响。

**docs/**（`docs/03-implementation/`）若有路径示例引用旧路径，本 spec **不**手工更新（保留为"历史记录"），但会在 progress.md 决策记录段说明本次重组。

## Decisions Log

记录本 spec 设计阶段拍板的决策（与上面 §Architecture 决策日志互补，记录的是"为什么这么写 spec"层面的决策）：

**D-SPEC-1：spec B 是否覆盖 mock_data/ 重组**

- 决定：不覆盖。adversarial-review §2 冲突 5 已拒 mock_data/v2/ 子目录。
- 理由：mock_data/ 不是公共 API，只 backend/data/loader.py 一个消费方，分版本无收益。

**D-SPEC-2：是否在重组同一个 spec 里加 meta_critic_node**

- 决定：不加。adversarial-review §8.3 已拒。
- 理由：meta_critic 留 spec C，配 ENV 开关。本 spec 范围严格限定"只动位置"。

**D-SPEC-3：是否同步重组 backend/api/ 的 router 文件**

- 决定：不重组。`backend/api/` 已经是分模块结构（routers/）。
- 理由：本 spec 范围只在 `backend/agent/`，避免范围蔓延。

**D-SPEC-4：tests/ 是否同步重组**

- 决定：不重组。保留扁平 `tests/test_*.py` 结构，import 路径已被 smartRelocate 自动更新。
- 理由：测试文件按"功能"扁平命名足够清晰，不需要按目录层级镜像 `agent/` 结构。

**D-SPEC-5：__init__.py 是否加 re-export**

- 决定：可选，按需 ≤ 5 个。默认空 `__init__.py`。
- 理由：`backend/agent/__init__.py` 已被解锁（spec A 之前），重新加 re-export 容易引入"路径耦合"——重组后 import 路径就是稳定 API。

## Out of Scope（再次确认）

```text
| 不做的事                        | 理由                                          |
|--------------------------------|----------------------------------------------|
| 删除任何 legacy 模块            | 仍是 fallback 链路依赖项                      |
| 改业务行为                      | 重组只动位置                                  |
| 改 graph/build.py 拓扑          | 编排冻结纪律                                  |
| 加 meta_critic_node            | 留 spec C                                    |
| mock_data/v2/ 子目录            | adversarial §2 冲突 5 已拒                    |
| backend/api/ 重组               | 范围蔓延                                      |
| tests/ 重组                     | 范围蔓延                                      |
| weights_llm.py 拆解             | 单一职责，无需拆                              |
| 改 docs/ 历史路径示例           | 保留为历史记录                                |
```

## Risk Assessment

```text
| 风险                                              | 概率 | 影响 | 缓解                                                   |
|---------------------------------------------------|------|-----|--------------------------------------------------------|
| smartRelocate 漏改某个相对引用                    | 中   | 中  | 分 6 批次 + 每批次 pytest -x 立即定位                 |
| FastAPI 启动 import 错误（pytest 不发现）          | 中   | 中  | 6 批次完成后 curl /health 验证                        |
| 改名（intent_parser→parser）破断字符串 import     | 低   | 中  | grep 全仓库的 importlib.import_module / __import__    |
| spec B 在 spec A 联调前启动                       | 低   | 高  | 启动检查清单（前置条件 5 项）+ 用户人工确认            |
| commit 中途出现，回滚困难                         | 低   | 低  | 一次性原子 commit，禁止中途提交                        |
| AGENTS.md §3.3.1 路径漂移（spec B 改完没同步）    | 中   | 低  | 批 6 同步更新 + 验收清单含 grep                        |
| 前端 .env / 配置硬编码后端路径                    | 极低 | 低  | npm run build 验证                                     |
```

## Estimated Effort

```text
| 批次          | 工时     | 备注                                            |
|--------------|---------|-------------------------------------------------|
| 批 1: core/   | 0.3h    | 5 文件，简单                                    |
| 批 2: intent/ | 0.6h    | 4 .py + 4 prompts/.py，含 1 处改名              |
| 批 3: planning/| 1.0h   | 9 文件 + 子子目录，最复杂                       |
| 批 4: runtime/ | 0.5h   | 8 .py + tools/，简单                            |
| 批 5: legacy/  | 0.5h   | 7 文件，含 4 处改名                             |
| 批 6: 收尾     | 0.5h   | __init__.py docstring + AGENTS.md + verify 脚本 |
| 文档同步       | 0.3h   | progress.md / pitfalls.md / problem.md          |
| pytest 缓冲    | 0.5h   | 6 次 pytest 累计                                |
| **总计**      | **4.2h**| ≈ 4h，符合 adversarial §6 砍后估算（3-6h）      |
```
