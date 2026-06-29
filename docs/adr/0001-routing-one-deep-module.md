# ADR-0001 · 路由层收口为一个 deep module（route_turn）

- **状态**：Accepted（2026-06-22 · improve-codebase-architecture 审计 + grilling 候选1·问题1）
- **范围**：backend agent 路由层（见 `backend/agent/CONTEXT.md`）

## 背景
路由判定（"一句话是什么意图 / 该走哪条路"）当前由**三套并存入口**各实现一份：
- **V3** `agent/graph/nodes/router.py::router_node`（LangGraph，`USE_LANGGRAPH=1`，活跃默认）
- **V1** `api/_streams/route.py`（`_stub_route` / `_routed_stream_real`，旧 `/chat/stream` 端点 + fallback）
- **V2** `agent/runtime/orchestrator.py::decide_turn_kind`（已 deprecated）

`_PLANNING_*_SIGNALS` 信号词典在 V1/V3 各一份且**已漂移**（V3 有"接待/安排"，V1 有"陪/喝茶/拍照"）——改一条规则要找齐多处。模块**浅而散**（deletion test：删掉旁路是复杂度**集中**而非搬移）。

## 决策
路由收口为**一个 deep module `route_turn`**：
- **接口简单**：`route_turn(最小输入：utterance + itinerary + user_id) → RouteDecision`（不再吞整个 `AgentState` dict）。
- 内部藏整条**分层级联**（注入→强信号→fast-path→画像→LLM→对话行为→兜底），层次为私有实现。
- V1/V2/V3 三个调用点退成**薄 adapter** 调 route_turn；信号词典收成**一处单一真相源**。
- 对话行为判定作为内部协作者（其 seam 归属见 ADR-0002）。

## 备选与拒因
- **B · 三层叠放 public module**（`classify_turn` → `resolve_route` → `classify_act`）——**拒**：三个对外接口 = 整体更浅；调用方只需"这一轮该干嘛"一个简单接口即可。

## 影响
- 模块数对路演 pitch（`路演PPT/hero.html`）**不可见**——pitch 画的是逻辑级联，不随 A/B 变。pitch 与本结构的"保真 pass"为**可选**下游事项（如是否给画像问答补一关）。

---
**落地状态**：✅ 已落地（核验 2026-06-23 · commit 586b846 · `routing/route_turn.py` deep module + 三 adapter 委托 `graph/nodes/router.py` / `api/_streams/route.py` / `runtime/orchestrator.py` · 测试 `test_router*.py`）
