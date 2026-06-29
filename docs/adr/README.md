# ADR 索引 · 落地状态

> 两个维度,别混:
> - **决策状态**(Nygard ADR 生命周期):`Accepted` / `Superseded` / `Deprecated` —— 「我们决定这么干」。
> - **落地状态**:代码是否已经这么干了 —— `✅ 已落地` / `🔁 部分落地` / `⏳ 待实现`。
>
> 规则:标 `✅ 已落地` **必须挂证据锚点**(commit / 测试 / 代码符号),否则视同未核验 —— 防止状态标记变成「说谎的 docstring」(空头声明会随代码漂移而骗人)。逐条详细脚注见各 ADR 文件末尾。

| ADR | 标题 | 范围 | 决策状态 | 落地状态 | 证据 / 备注 |
|---|---|---|---|---|---|
| [0001](0001-routing-one-deep-module.md) | 路由层收口为一个 deep module(route_turn) | 路由 | Accepted | ✅ 已落地 | `routing/route_turn.py` + 三 adapter 委托(586b846) |
| [0002](0002-dialogue-act-sealed-collaborator.md) | 对话行为判定保留为密封协作者 | 路由 | Accepted | ✅ 已落地 | `core/dialogue_acts.py` 返回 DialogueAct(586b846/ac8e58f);backlog:候选3 互斥表 |
| [0003](0003-route-turn-interface.md) | route_turn 接口(显式参数 + RouteOutcome) | 路由 | Accepted | ✅ 已落地 | 签名 + `routing/outcome.py`;backlog:去 `classify_fn` |
| [0004](0004-collapse-entrypoints-shared-route-turn.md) | 三入口收口共享 route_turn + 薄 adapter | 路由 | Accepted | ✅ 已落地 | 三 adapter 委托(586b846);⚠️ 部分将被 0007 接管 |
| [0005](0005-routing-package-and-routekind-relocation.md) | route_turn 住 routing/ 包;RouteKind 挪位断环 | 路由 | Accepted | ✅ 已落地 | `routing/` 包 + `kinds.py`,graph 反向 import |
| [0006](0006-injection-helper-and-routekind-deferral.md) | 注入婉拒为内部 helper;RouteKind 收窄缓做 | 路由 | Accepted | ✅ 已落地 | 6a 内联;6b 按设计缓做(单独立项) |
| [0007](0007-collapse-planning-to-langgraph-stack.md) | 规划层三栈收口为单一 LangGraph 栈 + rule 地板 | 规划 | Accepted | ⏳ 待实现 | 见 0007「待办」;按簇 test-first 实现 |

## 图例

- **落地状态**:`✅ 已落地` · `🔁 部分落地` · `⏳ 待实现`
- **决策状态**:走 Nygard ADR 生命周期。某 ADR 被新决策推翻时,旧 ADR 标 `Superseded by ADR-xxxx` 并双向互链(本表「证据/备注」列注明)。
- 待落地的 ADR(当前仅 0007)按「减法 → 加网(D2) → 深化 → 复评」分簇、test-first 推进;详见 0007。
