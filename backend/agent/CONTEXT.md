# CONTEXT · agent 路由层（域术语表 / ubiquitous language）

> 路由层 = 把「一句话 + 会话状态」判定成「这是什么 turn / 该去哪」的子系统。
> 本表是该 context 的统一语言；架构决策见同目录 `docs/adr/`。术语随 grilling 增补。

## 术语

- **route_turn**（待建）— 路由层**唯一的 deep module / public 入口**：
  `route_turn(utterance, itinerary, user_id, *, client) → RouteOutcome`。简单接口，内部藏整条分层级联。
  调用方（graph 边、各端点）只问"这一轮该干嘛"。见 ADR-0001 / 0003。
- **RouteOutcome** — route_turn 的类型化产出：`RouteOutcome(kind: RouteKind, decision: RouterDecision | None)`，
  把"去哪 + 可选回复 payload"显式化；各 adapter 各自翻译。见 ADR-0003。
- **分层级联（the cascade）**— route_turn 内部的**私有**判定顺序，每层命中即短路：
  注入检测 → 强信号反馈 → 规划 fast-path → 画像问答 → LLM 分类 → 对话行为判定 → 兜底归并。
- **RouteKind** — 路由结果枚举（planning / feedback / chitchat / emotional / meta / off_topic / ambiguous），决定下一节点。
  收口后住 `agent/routing/`（从 `graph/state.py` 挪出以断循环依赖，见 ADR-0005）。
- **agent/routing/（新包）** — 路由 bounded context 的家：route_turn + RouteOutcome + RouteKind + 信号表；积木留 `agent/core/`。见 ADR-0005。
- **RouteDecision** — 路由产出的回复决策（input_kind / reply_text / cta_chips / rationale …）。
- **adapter（入口适配器）**— graph node / HTTP 端点等调用点。收口后退成调 route_turn 的薄壳。
  当前有**三处并存实现**（V3 `router_node` 活跃 · V1 `_streams/route.py` · V2 `orchestrator.decide_turn_kind` 已弃）是收口目标。
- **classify_dialogue_act（密封协作者，现 `resolve_session_act`）**— 有方案后把一句话归为 提问 / 预约 / 确认 / 提约束没说改 等对话行为。
  保留为独立可测的真 seam（route_turn 调它做一层），**返回自己的 act 类型**、由 route_turn 做 act→route 映射。见 ADR-0002。
