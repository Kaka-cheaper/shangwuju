# CONTEXT · agent 路由层（域术语表 / ubiquitous language）

> 路由层 = 把「一句话 + 会话状态」判定成「这是什么 turn / 该去哪」的子系统。
> 本表是该 context 的统一语言；架构决策见同目录 `docs/adr/`。术语随 grilling 增补。

## 术语

- **route_turn**（已落地 `agent/routing/route_turn.py`）— 路由层**唯一的 deep module / public 入口**：
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
  收口已完成：`graph/nodes/router.py` 是唯一 adapter；V1 `_streams/route.py`、V2 `orchestrator.decide_turn_kind` 已随 ADR-0007 删除。
- **classify_dialogue_act（密封协作者，旧名 `resolve_session_act`）**— 有方案后把一句话归为 提问 / 预约 / 确认 / 提约束没说改 等对话行为。
  保留为独立可测的真 seam（route_turn 调它做一层），**返回自己的 act 类型**、由 route_turn 做 act→route 映射。见 ADR-0002。
  **ADR-0011 已议定其被统一路由脑子吸收**（E-2 落地时 ADR-0002 标 Superseded；字面规则部分下放壳层保留）。
- **响应义务闭集（6 标签）** — 路由输出的唯一词汇：`满足-首轮 / 满足-反馈 / 澄清 / 防御 / 陪聊 / 确认`，
  是 [L0 响应义务契约](../../docs/L0-响应义务契约.md) 的路由投影（「告知」是 planner 侧附属输出，刻意不在闭集）。见 ADR-0011。
- **一脑三壳** — 路由架构：壳1 安全规则（注入→防御，LLM 前）→ 壳2 字面短路（FP≈0 才配）→
  **脑子 = 一次 LLM 调用**（6 标签+槽位+置信度；置信度低→澄清）→ 壳3 保守地板（LLM 挂→陪聊/澄清引导，**绝不默认规划**）。
  旧七层级联塌缩至此；语义判断只此一处。见 ADR-0011。
- **会话上下文打包器（RoutingContext）** — 每轮一次、确定性打包「消毒轮次日志 + 方案版本志 + 当前方案摘要 +
  画像 + pending_clarification + 待确认态」；全量为默认、保险丝上限 + 钉锚兜边界；一处打包多处消费（路由/refiner/narration），
  禁止各节点自拼上下文。见 ADR-0011。
- **澄清状态机（pending_clarification）** — 显式会话状态承载「问了什么/选项/因何而问」；同一话题至多澄清一次，
  再不清则保守解释 + advisory 出路；呈现复用「气泡 + cta_chips」。见 ADR-0011。
- **诉求台账（demand ledger）** — 会话上下文家族第三器官（前两个：轮次日志=原始证据、方案版本志=方案演化史）：
  记名的结构化诉求索引（谁 · 什么调整 · 针对哪个节点 · 全局/局部语义 · 状态），每条**指回来源轮次，不重复存原话**。
  与 intent 的分工：台账=「谁要了什么」的不压扁历史；intent=「当前有效全局约束」的合并视图（全局语义诉求两边都进，
  节点级只进台账）。**失效语义**：诉求不随重排自动死——节点在场时是硬约束，节点没了降级为「尽量满足+主动提及+
  不满则告知」的偏好信号。**顶替规则**：同节点同维度后者顶替前者（旧条目标「已被顶替」不删）；跨成员矛盾不顶替，
  共存并暴露交 LLM 调解。属打包器钉锚集（日志裁剪永不丢）。单人来源=图状态字段，房间来源=房间状态（一个打包器
  多来源）。见 ADR-0013（起草中）。
