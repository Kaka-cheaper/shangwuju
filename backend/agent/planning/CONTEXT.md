# CONTEXT · agent 规划层（planning layer）域术语表 / ubiquitous language

> 规划层 = 把「意图 + 候选 POI/餐厅」变成「经 critic 校验、可执行的 Itinerary」的子系统。
> 本表是该 context 的统一语言；架构决策见 `docs/adr/`（规划相关从 ADR-0007 起）。术语随 grilling 增补。
> 姊妹 context：路由层见 `backend/agent/CONTEXT.md`。

## 术语

- **规划栈（planning stack）** — 一条完整的「意图 → Itinerary」实现路径。历史上三栈并存：
  V3 LangGraph（`planner → assemble → critic → replan`）/ V2 ReAct（LLM 直吐 Itinerary）/ V1 旧端点（`plan_itinerary_with_mode`）。
  **ADR-0007 起 V3 LangGraph 为唯一 canonical 栈**，V2/V1 删除。
- **栈内 rule 地板（in-stack rule floor）** — 规划层唯一兜底：`rule_planner` 纯规则模式产出本产品形态的 Itinerary
  （毫秒、不调 LLM、离线）。区别于「平行范式兜底」——地板与主栈同产品、同数据模型。见 ADR-0007。
- **failure-drain（失败汇流 / "D2"）** — 设计原则：一轮里**所有**失败——预期的 critic 违规 + 意外的运行时异常——
  都路由到栈内 rule 地板，**绝不「无方案」**；意外异常是 replan 策略认识的一种失败输入。
  `safe_stream` 仅作「连地板都抛了才报错」的最终兜底。见 ADR-0007。
- **replan 策略** — 失败后「下一步走哪」的单一真相源（阶梯：`llm_backprompt → ils → rule 地板 → give_up`）。
  failure-drain 把意外异常也并入它，使异常路径与预期失败路径收敛为一个策略。
- **对话入口** — 对外只暴露 `/chat/turn`（对话主入口，自动判定首轮规划 / 反馈重规划 / 闲聊）
  + `/chat/confirm`（确认下单）。二者均图驱动。「更少入口 = 更易集成」是部署简便性的取向。见 ADR-0007。
- **编译期 vs 运行期失败** — 图**编译/import 失败**属部署期静态失败（CI 该拦）→ 大声报错，不切栈；
  图**运行中途异常**属运行期失败 → 经 failure-drain 落地板。两者处理方式不同是刻意的。见 ADR-0007。
- **critic（校验层）/ `validate(plan, ctx)`** — 规划层唯一的方案校验 deep module：单一入口，内部是**分阶段的 Check 注册表**，
  返回 `Violation` 列表。统一替代历史上的 `critics_v2` + `ils_score_critic` + 已死的 blueprint critics。LangGraph 与 ILS 两路共用。见 ADR-0008。
- **Check（校验规则）** — 一条可组合的 Specification：`(plan, ctx) -> list[Violation]`，注册时声明**阶段**与**tier（hard/soft）**。
  各 Check 纯函数、独立可测；编排/顺序/严重度**显式声明**，不用隐式规则引擎。见 ADR-0008。
- **CriticContext** — 一次性载入 pois/restaurants/profile/tool_results，传给所有 Check（消除「每个 check 各自重建字典」）。见 ADR-0008。
- **分阶段校验（staged validation）** — Stage 0 结构门（不变量/节点完整/时间可解析/反幻觉，命中即**短路**）→
  Stage 1 hard 语义（gate 修复）→ Stage 2 soft（只建议/narration）。**阶段内 collect-all、阶段间短路；接受与否只由 hard 层决定**（LLM-Modulo）。见 ADR-0008。
- **hard / soft（严重度即动作）** — `hard` = 进修复闭环（驱动 backprompt/replan）；`soft` = 只建议（narration），不 gate。
  单一 `Violation` 类型承载，消灭旧的 CRITICAL/WARNING 与 hard/soft 双词汇。见 ADR-0008。
- **Violation** — 单一校验产出类型：`code / severity(hard|soft) / 节点定位 / fix-hint`。hard 违规 **collect-all 拼成一条 backprompt**（可执行反馈，VAL 教训）。见 ADR-0008。
