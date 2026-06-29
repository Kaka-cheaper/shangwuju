# ADR-0004 · 三入口收口为共享 route_turn + 薄 adapter（保留 fallback）

- **状态**：Accepted（2026-06-22 · grilling 候选1·问题4）
- **范围**：backend agent 路由层（承接 ADR-0001/0003）

## 背景
路由判定有三处并存实现（V3 `router_node` / V1 `_streams/route.py` / V2 `orchestrator.decide_turn_kind`），`_PLANNING_*_SIGNALS` 词典重复且漂移。
**但存在既有决策**：`react_agent.py` 头注 + spec *planning-pipeline-consolidation R5* 明确 V1/V2 作为 `USE_LANGGRAPH=0` 的 **fallback「不删」**。"删三入口"会与该 ADR 冲突。

> improve-codebase-architecture 纪律：别为重构推翻既有 ADR，除非摩擦大到值得重开。这里真正的摩擦是**重复/漂移**，不是"存在多入口"。

## 关键 enabler
V1 是 V3 的**无状态子集**（只认 5 类非规划 + 规划 fast-path，没有 `has_itinerary` 那几层）；而 route_turn 传 `itinerary=None` 时那些 session 层**本就自动跳过**，级联自然退化成 V1 那套 ⇒ 三入口可真共享、零重复。

## 决策
- 从 V3 级联抽出**共享 `route_turn`**（ADR-0001/0003 的那个 deep module）。
- **V3 graph node / V1 `_routed_stream_real`+`_stub_route` / V2 `decide_turn_kind` 全部退成薄 adapter** 调 route_turn；V1 传 `itinerary=None` 得无状态子集。
- **一张信号表、一处级联**（单一真相源），消灭漂移。
- **fallback 路径保留**（尊重既有决策），仅去重、不删。

## 备选与拒因
- **(b) 删 V1/V2**——拒：越界、风险高、且推翻深思熟虑的"保留 fallback"决策；而摩擦（重复）已被 (a) 在不动它的前提下完全消除。

## 实现期补充（T3 · V1 适配器，2026-06-22）
落地发现：旧 V1 的 `_stub_route` 额外有「5 类非规划（闲聊/元能力/情绪/范围外/歧义）关键词表」，靠关键词秒判闲聊、**不调 LLM**——这是 **V1 独有优化，V3 从无**（V3 一直靠 LLM Layer 2 分类闲聊）。
**决定：丢弃该 V1 专属优化、向 V3 对齐**——`_routed_stream_real`（仅真 LLM 路径）的闲聊类输入改由 route_turn Layer 2 的 LLM 分类。
- 影响：规划句仍走 L1.5 关键词 fast-path 不调 LLM；只有闲聊类边角输入多一次 LLM 调用、结果正确。V1-real 是 `USE_LANGGRAPH=0` 冷门 fallback，demo 走 V3 不碰。stub 模式由**未改动的** `_routed_stream_stub` 处理（仍 `_stub_route` 关键词、不调 LLM）。
- **载重理由（记此免未来重提）**：把 5 类关键词表塞回 route_turn 会让它比 canonical 的 V3 还厚，违背"V3 canonical"。

---
**落地状态**：✅ 已落地（核验 2026-06-23 · commit 586b846 · V1 `_routed_stream_real` / V2 `decide_turn_kind` / V3 `router_node` 三 adapter 均委托 route_turn，信号表收成单一源）
> ⚠️ 将被 ADR-0007 部分接管：0007 删除 V1/V2 规划栈时一并移除 V1 `_routed_stream_real`、V2 `decide_turn_kind` 这两个 adapter 调用点；路由收口本身不回退。
