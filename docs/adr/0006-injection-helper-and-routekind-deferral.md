# ADR-0006 · 注入婉拒为内部 helper；RouteKind 枚举收窄缓做

- **状态**：Accepted（2026-06-22 · grilling 候选1·问题6）
- **范围**：backend agent 路由层（承接 ADR-0001/0004）

## 6a · 注入「安全婉拒」= route_turn 内部 helper
检测 `detect_injection` 已是独立 module；命中后的安全婉拒响应（ADR-0004 收口后归一处）**留作 route_turn Layer 0 的内部 helper**，不单独成 module。
- 判据：仅 1 个消费者 = 假想 seam；**仅当出现独立测试**（第二个 adapter）才提升为密封 module。

## 6b · RouteKind 枚举收窄 —— 缓做（不并进本次重构）
审计候选 5：7 个 RouteKind 值里 meta/emotional/off_topic/ambiguous 在 `route_after_router` 都折进 chitchat 节点，接口比"实际去向"宽一倍。**决定缓做，单独立项。**
- **载重理由（记此以免未来评审重复提议）**：这 7 个值**不只管路由、还携带回复语义**（chitchat ≠ emotional ≠ off_topic 出不同气泡 / decision），收窄会丢语义；且枚举**散用在全代码库 + 遥测**，blast radius 大、相对收益小。与"路由收口"是正交的两件事。
