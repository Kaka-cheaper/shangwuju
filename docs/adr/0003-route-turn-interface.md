# ADR-0003 · route_turn 的接口（显式参数 + 类型化 RouteOutcome）

- **状态**：Accepted（2026-06-22 · grilling 候选1·问题3）
- **范围**：backend agent 路由层（承接 ADR-0001/0002）

## 背景
`router_node` 现接口 = **吞整个 `AgentState` dict、返回松散 dict** `{"route_kind":…, "router_decision":…}`（有时 `router_decision=None`）。后果：测 5 层得先攒整个 graph state（接口即测试面，这个测试面太大）；松散 dict 还是 `dialogue_acts` 与路由返回**耦合的根**。

## 决策
```
route_turn(utterance: str, itinerary: Itinerary | None, user_id: str | None,
           *, client: LLMClient) -> RouteOutcome
RouteOutcome(kind: RouteKind, decision: RouterDecision | None)
```
- **输入显式 3 参**（路由实际只依赖 `user_input`/`itinerary`/`user_id`）；`client` 作为**注入依赖**，测试传 stub。
- **输出类型化 `RouteOutcome`**：把"去哪（kind）+ 可选回复 payload（decision）"显式化。测试直接 `assert outcome.kind == "feedback"`。
- 各 **adapter** 把 RouteOutcome 翻成自己的形状（V3 graph node → `state` dict 更新；HTTP 端点 → 各自）。

## 备选与拒因
- **输入 (b) `RouterInput` 值对象**——拒：仅 3 个稳定字段，值对象过度设计。
- **输出 (i) 松散 dict**——拒：无类型，且是 dialogue_acts 与路由返回耦合的根。

## 实现期补充（T2，2026-06-22）
落地签名多了一个 `classify_fn: Any = None` 参数（依赖注入口）：adapter（V3 `router_node`）传入其自身命名空间的 `classify_input`，使现有 `monkeypatch.setattr(router_mod, "classify_input", ...)` 测试零修改仍生效。**接受为务实 DI 缝**（与 `client` 同性质）。**backlog**：把测试 monkeypatch 目标迁到 `route_turn` 模块后即可去掉此参数、回归 ADR 纯签名。

---
**落地状态**：✅ 已落地（核验 2026-06-23 · commit 586b846 · 签名 `route_turn(utterance, itinerary, user_id, *, client) -> RouteOutcome` + `routing/outcome.py::RouteOutcome(kind, decision)`）
> backlog（不影响落地）：移除 T2 的 `classify_fn` 参数、回归纯签名（待测试 monkeypatch 目标迁入 route_turn 模块）。
