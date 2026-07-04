# ADR 索引 · 落地状态

> 两个维度,别混:
> - **决策状态**(Nygard ADR 生命周期):`Accepted` / `Superseded` / `Deprecated` —— 「我们决定这么干」。
> - **落地状态**:代码是否已经这么干了 —— `✅ 已落地` / `🔁 部分落地` / `⏳ 待实现`。
>
> 规则:标 `✅ 已落地` **必须挂证据锚点**(commit / 测试 / 代码符号),否则视同未核验 —— 防止状态标记变成「说谎的 docstring」(空头声明会随代码漂移而骗人)。逐条详细脚注见各 ADR 文件末尾。

| ADR | 标题 | 范围 | 决策状态 | 落地状态 | 证据 / 备注 |
|---|---|---|---|---|---|
| [0001](0001-routing-one-deep-module.md) | 路由层收口为一个 deep module(route_turn) | 路由 | Accepted | ✅ 已落地 | `routing/route_turn.py` + 三 adapter 委托(586b846) |
| [0002](0002-dialogue-act-sealed-collaborator.md) | 对话行为判定保留为密封协作者 | 路由 | **Superseded by 0011** | 🪦 已退役 | E-2-c(5321d8d)落地:classify_dialogue_act/DialogueAct 删除,统一路由脑子吸收;字面确认/预约规则下放 dialogue_acts.py 保留,BOOKING/CONFIRM 合流 confirm |
| [0003](0003-route-turn-interface.md) | route_turn 接口(显式参数 + RouteOutcome) | 路由 | Accepted | ✅ 已落地 | 签名 + `routing/outcome.py`;backlog:去 `classify_fn` |
| [0004](0004-collapse-entrypoints-shared-route-turn.md) | 三入口收口共享 route_turn + 薄 adapter | 路由 | Accepted | ✅ 已落地 | 三 adapter 委托(586b846);V1/V2 adapter 已随 0007 删除 |
| [0005](0005-routing-package-and-routekind-relocation.md) | route_turn 住 routing/ 包;RouteKind 挪位断环 | 路由 | Accepted | ✅ 已落地 | `routing/` 包 + `kinds.py`,graph 反向 import |
| [0006](0006-injection-helper-and-routekind-deferral.md) | 注入婉拒为内部 helper;RouteKind 收窄缓做 | 路由 | Accepted | ✅ 已落地 | 6a 内联;6b 按设计缓做(单独立项) |
| [0007](0007-collapse-planning-to-langgraph-stack.md) | 规划层三栈收口为单一 LangGraph 栈 + rule 地板 | 规划 | Accepted | ✅ 已落地 | 5 簇 test-first（commit 4cbc09e→50a9709）;910 passed/0 failed |
| [0008](0008-critic-staged-single-registry.md) | critic 校验层重设计：分阶段 hard/soft 单注册表 | 规划 | Accepted | 🔁 部分落地 | Phase A/B-1/B-2a/B-2b 已落地（commit 2535d94/f7f7ad2/7977097/963b39a，936 passed）；Phase C（接 ILS + 删死 blueprint 层）转由 ADR-0009 承接（已落地）|
| [0009](0009-ils-real-rung-and-critic-repair-loop.md) | ILS 升为真实 replan 梯级 + critic-to-solver 闭环修复 | 规划 | Accepted | ✅ 已落地 | C-1→C-5 全落地（f35fccb→fb29dcf，950 passed）；ILS 真组装候选 + 年龄进组装器(α) + critic-to-solver 有界修复闭环 + 删死 blueprint critics/ils_score_critic |
| [0010](0010-multi-activity-toptw-planning.md) | 规划升为按需求涌现的多活动 TOPTW | 规划 | Accepted | ✅ 已落地 | D-1→D-8 全落地（e0eb0c1 起 10 个 commit，1070 passed；shake 实测砍除=#8 终局；pinning+advisory 贯通到 SSE；5 段遗产死代码清尽） |
| [0011](0011-llm-first-routing-obligations.md) | 路由层重设计：一脑三壳（LLM-first + 义务闭集 + 上下文打包器） | 路由 | Accepted | 🔁 部分落地 | 6 标签闭集=[L0 契约](../L0-响应义务契约.md)投影；吸收 dialogue_acts；地板反转（绝不默认规划）；澄清状态机；**E-1 已落地**（地板反转+词表清洗+壳2 canonical 短路）；**E-2 全部收口**——a 会话日志(c7638fd)/b 打包器双底座(c26213c)/c 统一路由脑子(5321d8d:一次 LLM 出 6 标签+槽位+置信度,RouteKind 7→6 前后端同步,classify_input+dialogue_act 双调用退役,真 LLM 冒烟 8/8);1363 passed;余 E-3(含 ADR-0014 挂钩两项)/E-4 |
| [0012](0012-session-substrate-single-truth.md) | 会话底座收口：图状态单一真相源 + 旧藤葬礼 | 底座 | Accepted | ✅ 已落地 | E-0 四片全落地：a 葬礼+回写+结构诚实（e08398e）/b 38 字段生命周期表（6c92dac）/骑手 决策 11 全路径兑现（b10c4b1）/c 确认流合一+双轨记忆+USE_LANGGRAPH 退役；1087 passed；主 App 偏好标签不累积的 bug 修复（探针先红后绿） |
| [0013](0013-node-adjustment-and-room-negotiation.md) | 节点级协商：局部重解 + 调整按钮 + 房间轻量成员体系 | 规划+协作 | Accepted | ✅ 已落地 | F-1→F-6 全落地（引擎 ead7f12/台账 a402396/按钮备选 ba06f7c/单人入口 9985392/房间收尾+路由同权 ea236de）；确认后调整守门（c′批 2a23f8b）；跨层 import 债已还（node_swap_support 中立 seam，c7de86f）；余留痕见 ADR 落地状态节 |
| [0014](0014-requirement-analysis-provenance-and-hard-constraints.md) | 需求分析治理：字段出处 + 硬软约束 + 消费断链收口 | 抽取+消费 | Accepted | ✅ 已落地 | G-0→G-4b 单日全落地（3b217f6/206de2e/53ba99b/711a034/2ce0341/60f6130）+两 bug 先行修（e70c6c1）：四值出处/hard 永不放宽+出口审计/预算定量定性分轨/消费完备性三轴 gate；1454 passed；余 E-3 挂钩两项与 mock 补数据拍板 |
| [0015](0015-llm-trust-boundary.md) | LLM 信任边界：自由域、确定域与词表缓存域 | 全后端 | Accepted | 🔁 部分落地 | 原则/不变式四条/词表三纪律/接缝模式已由既有代码体现（9eecef0/cddde19/d35435f/fd22ff6 + exit_audit/execute_finalize）；普查 ~120 决策点九成以上归属正确；余修缮批 6 件+根治批 1 件在途、演进阶梯三级全部未开闸（I 类探针 b56e953=级 1 的门） |

## 图例

- **落地状态**:`✅ 已落地` · `🔁 部分落地` · `⏳ 待实现`
- **决策状态**:走 Nygard ADR 生命周期。某 ADR 被新决策推翻时,旧 ADR 标 `Superseded by ADR-xxxx` 并双向互链(本表「证据/备注」列注明)。
- 0007 已分 5 簇 test-first 落地（D2 → 拆 ReAct → 拆 V1-API → 拆 V1-planner → 收尾;commit 4cbc09e→50a9709）;残留文档级 follow-up 见 0007 脚注。
