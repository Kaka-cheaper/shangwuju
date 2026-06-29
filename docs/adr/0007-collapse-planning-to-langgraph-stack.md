# ADR-0007 · 规划层三栈收口为单一 LangGraph 栈 + 栈内 rule 地板（删 ReAct 驱动 + V1）

- **状态**：Accepted（2026-06-23 · grilling 候选1）
- **范围**：backend 规划层（`planning/`）+ 规划入口（`api/chat.py`、`graph/`、`runtime/`）。承接 ADR-0004（路由层同款「三入口收口」）。
- **推翻**：spec *planning-pipeline-consolidation R5* / `react_agent.py` 头注的「V1/V2 作为 `USE_LANGGRAPH=0` fallback **不删**」决策——见下「关键发现」1–4。

## 背景

三条**规划栈**并存（与 ADR-0004 的「路由判定入口」是不同层）：

- **V3 LangGraph**：`planner → assemble → critic → replan`，蓝图式规划。
- **V2 ReAct**：`react_agent.py`，LLM 直接吐 Itinerary，`critics_v2` 作 output validator，**完全不碰 `planning/`**。
- **V1 旧端点**：`/chat/stream`、`/chat/refine` → `plan_itinerary_with_mode` → `llm_planner.py`。

`chat.py` 的兜底链是 `LangGraph → ReAct → V1`，**按代码辈分（新→旧）分层，不是按失败独立性分层**。

部署真相：`Dockerfile:91` + `docker-compose.yml:82` + `docker-compose.redis.yml:29` 都 `USE_LANGGRAPH=1` ⇒ **V3 是活栈**。但 `.env.example:50` 写 `USE_LANGGRAPH=0`，同文件 216/223 行又说「当前 =1」——**配置自相矛盾**，含糊到连独立的代码排查都会对「哪条是活的」得出相反结论。

## 关键发现（推翻「保留 fallback」）

1. **共因失败 = 假冗余**：三栈共享 `agent.core.llm_client` / `schemas` / `data.loader`。底层一坏，三栈一起 import 失败，互相救不了。冗余只有在失败模式独立时才买得到可用性。
2. **ReAct 几乎永不触发**：`chat.py:187-199` 的 `try` 只包 `get_compiled_graph()`（模块单例，开机编译一次即缓存），**不包流式执行**。只有图**编译期**崩才轮到 ReAct——那是 CI 该拦的部署期静态失败；编译成功后 ReAct 这辈子调不到，运行中途异常直接抛 SSE error、不掉 ReAct。
3. **真正该兜的失败无人兜**：运行时中途异常（LLM 出蓝图超时、节点遇怪输入抛错）现在只被 `sse_adapter`（:137-160）/ `safe_stream` 转成 `STREAM_ERROR`「报错、无方案」。ReAct / llm_planner 都不在这条路径上。
4. **正解在栈内**：`rule_planner` 地板（毫秒、不调 LLM、离线、产出本产品形态 Itinerary）本就为「无论如何给个方案」而生，只是**异常路径没回流到它**。
5. **端点是 optics，不是契约**：小团无真实调用；`/chat/stream`、`/chat/refine` 是为评分项「部署简便性」预留的接口；live 前端 `store.ts` 只调 `/chat/turn` + `/chat/confirm`。**更多端点 ≠ 更易集成**——`/chat/turn` 自身 docstring 就是「无需客户端维护『调哪个端点』状态机」的简便性卖点。

## 决策

- **LangGraph 为唯一规划栈**。图编译失败 = **大声报错**（部署 bug，CI 拦），不再悄悄切换到平行范式。
- **栈内 rule 地板为唯一兜底（"D2 / failure-drain"）**：一轮里**任何**失败（critic 违规 + 意外异常）都汇流进 `rule_planner` 地板 → 永远产出本产品形态的 Itinerary，**绝不「无方案」**。意外异常成为 **replan 策略**认识的一种失败输入（与 ADR 报告 #3「单一 replan 策略」合流）。`safe_stream` 退为「连地板都抛了才报错」的最终兜底。
- **删除**：ReAct 驱动（`react_agent.py` + `orchestrator` 的 react/legacy 部分 + `chat.py` 两处兜底分支）；整条 V1（`llm_planner.py` + `plan_itinerary_with_mode` + `planner_stream.py` + `refine_real.py`）。**确认流不受影响**——`build_confirm_actions` 定义在 `execute_finalize.py`（图层自带），与 ReAct/orchestrator 无关。
- **对外端点收口为 `/chat/turn` + `/chat/confirm`**（均图驱动）；删 `/chat/stream` + `/chat/refine`；「部署简便性」的功夫投到**一份清爽的 API 契约文档**，不靠预留端点。

## 备选与拒因

- **(b) 留 ReAct 作永久兜底** —— 拒：共因失败假冗余（发现1）+ 编译成功后永不触发（发现2）+ 它是**另一个产品**（无 blueprint / DecisionTrace、自带 critic、不碰 `planning/`，所有加深都保护不到它 ⇒ 会漂移、会烂、且测不到）。
- **(c) `/chat/stream` 留壳重定向到图** —— 拒：多一个端点稀释「部署简便性」、多一套 SSE schema 要维护；单入口 + 契约文档才是最强简便性论据（发现5）。
- **(d) 路演前先上 D1 边界兜底**（在 `sse_adapter` except 里直接调 `plan_itinerary`）—— 可作临时保命，但 D2 才是概念完整形态（异常路径与预期失败路径收成一个 replan 策略），直接做 D2。

## 待办（实现期 · 本 ADR 未动代码）

- D2 接线：图内捕获意外异常 → 归入 replan 策略 → 落 `rule_planner` 地板；前提是 `intent` 已解析（`intent` 节点之后的失败均满足；`intent` 节点本身失败则老实报错）。
- 删除清单：ReAct 驱动 + 整条 V1（含端点、planner_stream、refine_real）。
- 端点：保留 `/chat/turn` + `/chat/confirm`；老 `verify` / 压测脚本迁到 `/chat/turn` 或删。
- 修 `.env.example` 的 `USE_LANGGRAPH` 矛盾：标注 `=1` 为 canonical。

---
**落地状态**：⏳ 待实现（决策 2026-06-23 · 见上「待办（实现期）」；按簇串行 + test-first 实现，减法 → 加网(D2) → 深化 → 复评）
