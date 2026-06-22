# ADR-0002 · 对话行为判定保留为密封协作者（classify_dialogue_act）

- **状态**：Accepted（2026-06-22 · grilling 候选1·问题2）
- **范围**：backend agent 路由层（见 `backend/agent/CONTEXT.md`，承接 ADR-0001）

## 背景
"有方案后把一句话判成对话行为（提问 / 预约 / 确认 / 提约束没说改）"现由 `agent/core/dialogue_acts.py::resolve_session_act` 实现，被 `router_node` 的 Layer 3 调用。两个问题：
- 它**返回 router_node 的 dict 形状** `{"route_kind":…, "router_decision":…}` → 与路由返回耦合。
- 内部 `looks_like_confirm` / `looks_like_booking` 靠**反向调** `looks_like_feedback/question/explicit_revise` 排除来做互斥（浅 module 味，= 审计候选 3）。

## 决策
**保留为密封、可独立测试的协作者**（概念名 `classify_dialogue_act`），不摊进 route_turn：
- route_turn 把它当**一层**来调；它**返回自己的类型**（一个 act / 干净 decision），由 route_turn 做 act→route 映射，**不再返回 router 的 dict**。
- 内部"靠反向调排除做互斥"的加深（收成一张优先级/分类表）留作后续子决策（候选 3）。

## 备选与拒因
- **(a) 摊成 route_turn 的内部私有分支**——**拒**：会抹掉一个**真 seam**。判据（codebase-design "一个 adapter=假想 seam，两个=真"）：`resolve_session_act` 已有两个消费者——`router_node`（生产）+ `test_dialogue_acts.py`（测试），是真 seam；且它有独立的输出空间（对话行为 ≠ RouteKind）与独立的运行阶段（仅有方案时）。摊平等于把 pitch 着重讲的"7 类对话行为分类器"重新埋回过程代码。
