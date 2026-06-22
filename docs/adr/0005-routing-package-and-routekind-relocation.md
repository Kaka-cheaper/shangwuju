# ADR-0005 · route_turn 住新 `agent/routing/` 包；RouteKind 挪出 graph 断环

- **状态**：Accepted（2026-06-22 · grilling 候选1·问题5）
- **范围**：backend agent 路由层（承接 ADR-0001/0003/0004）

## 背景
`router_node` 有 **4 处函数内延迟 import**（persona_qa / dialogue_acts / `_safe_refusal_decision` 的 schemas+prompt）——绕循环依赖的补丁。根源：`RouteKind` 住在 `agent/graph/state.py`（graph 层），而路由积木（injection/feedback/dialogue_acts）在 `agent/core/`，module 升不到能 module 级 import 全部层。

## 决策
- 新建 **`agent/routing/` 包**，作为该 bounded context（见 `CONTEXT.md`）的家，装 **route_turn + RouteOutcome + RouteKind + 信号表**。
- **RouteKind 从 `graph/state.py` 挪到 `agent/routing/`**。graph node 反过来 import 它（graph→routing 单向，无环）；那 4 处延迟 import 升回 module 级。
- 积木（injection_detector / feedback_detector / dialogue_acts 等）**留在 `agent/core/`**，被 route_turn import（它们是共享原语，不为此搬家）。密封协作者 `classify_dialogue_act` 默认留 core，可后议。

## 备选与拒因
- **(a) `agent/core/route_turn.py`**——拒（弱）：更省，但没有给路由 context 一个清晰的家；`routing/` 更利 AI 导航、RouteKind 落此天然断环。

## 待实现时确认
精确的循环 import 卡在哪条边，留到实现时逐行钉死；但"route_turn 移出 graph + RouteKind 移到中立位"足以断这一类环。
