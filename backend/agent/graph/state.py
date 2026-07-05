"""agent.graph.state —— LangGraph 全局 State schema。

LangGraph 的 State 是节点间共享的"工作内存"——每个节点接受 State，返回 State diff，
框架自动 merge。本 State 复用 v2 已有的 IntentExtraction / Itinerary / Violation 等
schema，不发明新结构。

字段命名规范：
- 数据型字段（intent / itinerary / blueprint）用名词
- 状态型字段（retry_count / route_decision）用动作-结果合成名
- 流式事件型字段（sse_events）由 sse_adapter 消费
- 业务快照（messages 含 ModelMessage list）在节点间持久化（Phase 12 接 InMemorySaver）

不负责：
- LangGraph SDK 的 messages reducer（用框架默认 add_messages）
- SSE 事件序列化（在 sse_adapter.py）

【spec planning-quality-deep-review R6+R7（Task 6 + Agent H P2-H8）】
- 删除已死的 routes: list[Any] 字段（execute.py 未填、其他节点未消费；
  routes.json 真值 lookup_hop / assemble 内部直接调，state 层不做缓存）
- 新增 quality_issues: list[Any] 字段，承载 narrator 主动质疑信号（由 intent_node
  的词典外社交意图检测写入，narrate_node 只读取消费；ADR-0012 决策 4 起随
  reset_for_new_episode() 在每次规划事件开始时清零，见字段生命周期表）
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

# 业务领域类型（保留所有 v1 已有 schema）
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary
from schemas.router import RouterDecision

# RouteKind 已迁移至 agent/routing/kinds.py（见 ADR-0005）。
# 此处 re-export 保持向后兼容——所有 `from agent.graph.state import RouteKind`
# 的现有 importer 零改动照常工作。
from agent.routing.kinds import RouteKind  # noqa: F401

# 蓝图（plan）层
from agent.planning.blueprint.blueprint import PlanBlueprint
from agent.planning.weights_llm import PlanningWeights

# Critic 层（v2 critic 兼容；旧 critics 仍由 hybrid 内部使用）
try:
    from agent.planning.critic.critics_v2 import Violation as CriticViolation
except ImportError:
    CriticViolation = Any  # type: ignore[misc, assignment]


# ============================================================
# Replan 决策类型
# ============================================================

ReplanStrategy = Literal[
    "llm_backprompt",   # 让 LLM 重出 plan（≤2 次）
    "ils_fallback",     # 转 hybrid ILS 算法兜底
    "give_up",          # 兜底失败，rule planner 兜
]


# ============================================================
# 主 State
# ============================================================

def _merge_demand_ledger(old: list[dict], new: list[dict]) -> list[dict]:
    """demand_ledger 通道归并器(F-2 深审修正,SESSION_SCOPED 的结构性保障)。

    不配 reducer 的 last-value 通道下,make_initial_state 每轮写 [] 会把
    checkpointer 里的台账静默清空。语义:空更新=保留旧值(每轮初始化天然
    no-op,同 messages 的 add_messages 先例),非空=整体替换(record_demand
    返回含顶替状态改写的**全量**列表——条目状态会被改写,append 语义不适用)。
    "清空台账"在业务上不存在:条目只标记(被顶替/已满足)不删除(ADR-0013
    决策 3),故空列表永远只可能来自初始化,当 no-op 处理是安全的。
    """
    if not new:
        return list(old or [])
    return list(new)


class AgentState(TypedDict, total=False):
    """LangGraph 全局 State。

    所有字段都是可选的（total=False）；节点按需读写自己关心的字段。

    【字段生命周期表（ADR-0012 决策 4）】每个字段的行尾标了它属于下面三档生命周期
    之一（模块级 frozenset TURN_SCOPED / EPISODE_SCOPED / SESSION_SCOPED 是真值，
    下面的行尾注释只是人读的索引）；完备性由 tests/test_state_lifecycle.py 强制
    ——三个 frozenset 的并集必须等于 AgentState 全部字段、两两交集必须为空。

    - TURN_SCOPED   每个 turn 开始清零（make_initial_state 显式给初值）。
    - EPISODE_SCOPED「规划事件」边界重置（新需求进 intent_node / 反馈进 refiner_node
      时，两者共用 reset_for_new_episode() 生成的同一份 diff）——这批字段在两次
      规划事件之间的 turn（如 chitchat）里保留原值，靠 make_initial_state 不碰它们
      实现「persistence by omission」，但重置本身是显式的，不是隐式漏掉。
    - SESSION_SCOPED 跨轮持久，从不被上面两种重置动到。
    """

    # ---- 输入与会话身份 ----
    user_input: str  # TURN_SCOPED：本轮用户原话，每轮由调用方全新给出
    user_id: str  # SESSION_SCOPED：会话身份；调用方每轮透传同一值（非"重置"）
    session_id: str  # SESSION_SCOPED：同上，thread_id 取自它
    # SESSION_SCOPED：E-2 RoutingContext 打包器的画像素材（ADR-0011 决策 3）；
    # 当前传入即止，图内无读者——接线归 E-2（ADR-0012 决策 6）。
    scenario_id: Optional[str]

    # ---- 双范式切换（spec interaction-experience-review）----
    # "rule" 走纯规则路径（不调 LLM，毫秒级出方案，断网也能跑）
    # "llm" 走 LLM-First Planner（默认；让大模型自己拿主意）
    # None  默认行为（保留向后兼容；当前等同 "llm"）
    planner_mode: Optional[Literal["rule", "llm"]]  # SESSION_SCOPED：会话级范式开关

    # ---- 跨 turn 消息历史（Pydantic AI / LangGraph 标准） ----
    # ADR-0011 前置核实①（会话日志基础设施，E-2 第一块砖）：轮次日志接的就是
    # 这个既有通道——HumanMessage=用户原话（router_node 写；壳1 拦截的轮次写
    # 占位文本，不回灌攻击原文，见 nodes/router.py 护栏注释）/AIMessage=agent
    # 侧发言（router_node 写 chitchat 类气泡回复；narrate_node 写规划/反馈轮
    # 叙事文案）。
    # 【护栏4·存储无界，二选一显式拍板】demo 规模显式接受：messages 通道与
    # checkpointer 历史本身无界增长（InMemorySaver 每 checkpoint 全留，从不
    # 修剪），本砖不在这里加修剪。修剪方向留给 E-2 会话上下文打包器那层的
    # "上下文包"保险丝（ADR-0011 决策 3：约最近 40 轮/8K token，钉锚永不丢）
    # ——那层护的是"喂给路由脑子的切片"，不是存储本身。存储层修剪待真实多
    # 租户场景（有真实 TTL/归档需求时）再补，不在 demo 阶段假装解决一个还不
    # 存在的问题。
    # 【护栏5·换 persona 不换 session】日志随 session（thread_id）持续追加，
    # 不因同一 session 中途 user_id（persona）切换而清空/分叉——`resolve_
    # user_id` 每请求可变，但 messages 通道只认 thread_id；画像相关字段
    # （scenario_id / user_profile 等）才按当轮 user_id 现取，两者语义不同，
    # 不因为"看起来都是会话状态"就混为一谈。
    messages: Annotated[list[BaseMessage], add_messages]  # SESSION_SCOPED：add_messages 累积通道，天然跨轮

    # ---- 路由结果 ----
    router_decision: Optional[RouterDecision]  # TURN_SCOPED：router_node 本轮无条件重算
    route_kind: Optional[RouteKind]  # TURN_SCOPED：同上
    # TURN_SCOPED×2:refiner 本轮产出的人话变更摘要与自报说明——emit_refiner 把它们
    # 装进 REFINEMENT_DONE payload(修复"changed_fields 恒硬编码 [] 致前端 toast
    # 永远拿不到真实变更清单"的契约漂移,api_contract §分支B 本就承诺
    # payload=RefinementOutput.model_dump())。仅反馈轮由 refiner_node 写入。
    refinement_changed_fields: Optional[list[str]]
    refinement_note: Optional[str]

    # ---- 意图层 ----
    intent: Optional[IntentExtraction]  # EPISODE_SCOPED：新规划事件的意图，reset 后立即被 intent/refiner 自己的输出覆盖

    # ---- 锁定清单（赞锁定根治批：房间 locked_stages → 重排引擎）----
    pinned_targets: list[dict]  # EPISODE_SCOPED：本次规划事件必须保留的实体清单。
    # 条目形状 {"kind": "poi"|"restaurant", "target_id": str, "name": str}——**刻意
    # 存 plain dict 而非 schemas.pin.PinSpec**：checkpoint msgpack 白名单
    # （build.py::_build_checkpoint_serde）外的 Pydantic 对象会**无声类型擦除**
    # （cddde19 教训，读回静默变 dict 零告警）；纯 dict 天然免白名单，消费点
    # （ils_replan_node）再按需构造 PinSpec。生产写手只有房间反馈重排
    # （collab/room.py::_replan_with_refiner 注入，锁定归名留在房间侧不进图——
    # 引擎不需要"谁锁的"，分层不泄漏）；消费者三处：planner_node（蓝图用户消息
    # 「必须保留」段先验）、critic_node（check_pinned_presence 硬判据）、
    # ils_replan_node（plan_hybrid(pinned=...) 原生保护）。单人路径无生产者，
    # 恒为空列表 = 行为与本批之前完全一致。归 EPISODE_SCOPED：锁绑定"这一次
    # 重排事件"——反馈轮由房间按当时的 locked_stages 重新注入（覆盖 reset 的
    # 空值），新需求进 intent_node 则随 episode 重置清零（旧方案的锁对全新
    # 方案无意义）。

    # ---- 候选数据（execute 阶段并行写入）----
    pois: list[Any]            # EPISODE_SCOPED：execute 阶段候选池，随新规划事件失效重搜（list[Poi]，用 Any 避开 TypedDict 泛型限制）
    restaurants: list[Any]      # EPISODE_SCOPED：同上（list[Restaurant]）
    user_profile: Optional[Any]  # EPISODE_SCOPED：GetUserProfileOutput，execute 阶段随事件重取
    # Step 6：tag relaxation 路径（split per worker 避免 reduce 冲突）
    pois_relaxed_tags: list[str]  # EPISODE_SCOPED：同 pois 生命周期
    restaurants_relaxed_tags: list[str]  # EPISODE_SCOPED：同 restaurants 生命周期

    # ---- Plan 层（LLM-First） ----
    weights: Optional[PlanningWeights]  # EPISODE_SCOPED：本次规划事件的偏好权重
    blueprint: Optional[PlanBlueprint]  # EPISODE_SCOPED：本次规划事件的蓝图
    plan_attempt: int           # EPISODE_SCOPED：本次规划事件内 planner 跑的次数（含重试）

    # ---- Itinerary（assemble 阶段产出） ----
    itinerary: Optional[Itinerary]  # EPISODE_SCOPED：核心方案；新事件必须先清空再重搜/重排，否则 router 之后的节点会看到上一版方案

    # ---- Critic 反馈 ----
    violations: list[CriticViolation]  # EPISODE_SCOPED：critic 状态，随事件重置
    has_critical: bool  # EPISODE_SCOPED：同上
    critic_feedback_text: Optional[str]  # EPISODE_SCOPED：backprompt 用的格式化文本；planner.py 直读，不清会把上一事件的违规反馈喂进全新规划（ADR-0012 背景 5 的定时炸弹）

    # ---- Replan ----
    retry_count: int  # EPISODE_SCOPED：本次规划事件的重排计数，替换/backprompt 循环全程在同一事件内完成，从不跨事件累积
    replan_strategy: Optional[ReplanStrategy]  # EPISODE_SCOPED：同上

    # ---- Decision trace（Step 4+7：决策可解释性） ----
    decision_trace: Optional[Any]  # EPISODE_SCOPED：DecisionTrace（用 Any 避循环 import）；顶层键本身只被 reset_for_new_episode() 清零过，真正的 trace 挂在 itinerary.decision_trace 上（assemble/narrate 写的是嵌套字段，不是这个顶层键——顶层字段疑似已死，但删除属死字段清查范畴，不在本次 E-0-b 任务书内，先按其设计意图归档生命周期）
    fallback_chain: list[Any]      # EPISODE_SCOPED：list[FallbackHop]，随事件重置
    critic_attempts: list[Any]     # EPISODE_SCOPED：list[CriticAttempt]，随事件重置
    alternatives: list[Any]        # EPISODE_SCOPED：list[AlternativeCandidate]，随事件重置
    quality_issues: list[Any]      # EPISODE_SCOPED：list[str]，narrator 主动质疑信号（spec R6）；intent_node 每次规划事件唯一写手，不应跨事件累积
    advisories: list[Any]          # EPISODE_SCOPED：list[dict]（Advisory.model_dump()）：D-7「绝不默默
    # 忽略」的结构化告知——ils_replan_node 在 hybrid 成功时写入（见 replan.py），
    # narrate_node 消费并透传进 SSE（见 _emit_handlers.emit_narrate）。
    give_up_chips: list[Any]       # EPISODE_SCOPED：list[dict]（CtaChip.model_dump()）。
    # ADR-0014 决策 2（G-2）配套三件之一：ILS + rule planner 都彻底失败（真正
    # "hard 卡死"，无任何方案可交付）时，`ils_replan_node` 写入"放宽建议"结构化
    # chips（见 `agent.planning.planners.rule_planner.relax_suggestion_chips`），
    # narrate_node 在 itinerary=None 的兜底分支消费，emit_narrate 透传进 SSE。
    node_actions: dict[str, Any]   # EPISODE_SCOPED：{target_id: {chips, alternatives}}
    # (ADR-0013 F-3):narrate_node 唯一写手,emit_narrate 装进 ITINERARY_READY
    # payload 兄弟字段;绑定"这一版方案",换方案即失效。F-3 实测:LangGraph 1.2
    # 会静默丢弃节点返回的未声明字段——本行不声明,narrate 算得再对也到不了 SSE。

    # ---- 暖语气 ----
    narration: Optional[str]  # EPISODE_SCOPED：本次规划事件的方案文案（narrate_node 每事件必写一次；提前清零防御未来新增的"narrate 前读者"）

    # ---- spec algorithm-redesign R5：memory_writer 副作用结果（用于 SSE memory_persisted 推送）----
    memory_status: Optional[dict[str, Any]]  # EPISODE_SCOPED：confirm 侧效果快照，绑定"当前这版方案"，换方案即失效

    # ---- 确认态字段（不是图内 interrupt——HITL 三按钮里只有 confirm 会写这里，
    # 经 /chat/confirm 的 HTTP 旁路 aupdate_state 回写；ADR-0012 决策 2）----
    # EPISODE_SCOPED（ADR-0012 决策 4 原文）。已知丢失标注：user_decision / orders
    # 归事件级是正确语义（订单跟着"这一版被确认的方案"走，反馈换方案后旧订单状态
    # 不该继续冒充"当前方案已确认"）；代价是"用户下过单"这一事实，在下一次反馈
    # 触发新规划事件时会被 reset_for_new_episode() 清空——E-2 方案版本志出生前，
    # 这段窗口是已知取舍，不是本次疏漏，也不是后人可以顺手"修复"的 bug（ADR-0012 /
    # ADR-0011）。
    user_decision: Optional[Literal["confirm", "refine", "cancel"]]
    orders: list[Any]           # EPISODE_SCOPED：list[Order]，同上已知丢失标注
    share_message: Optional[str]  # EPISODE_SCOPED：confirm 阶段生成的转发文案，绑定当前方案
    execution_tool_results: list[Any]  # EPISODE_SCOPED：confirm 阶段执行类 Tool 调用结果（SSE 适配用），绑定当前方案

    # ---- chitchat 输出（非 planning 路径用）----
    chitchat_reply_text: Optional[str]  # TURN_SCOPED：chitchat_node 本轮无条件重算
    chitchat_tone: Optional[str]  # TURN_SCOPED：同上
    chitchat_chips: list[Any]    # TURN_SCOPED：同上（list[dict]，CtaChip.model_dump()）

    # ---- 诉求台账（ADR-0013 决策 3 / F-2）----
    demand_ledger: Annotated[list[dict], _merge_demand_ledger]  # SESSION_SCOPED：
    # list[dict]（schemas.demand_ledger.LedgerEntry.model_dump()）。跨"规划事件"
    # 存活是这个字段存在的唯一意义——ADR-0013 决策 3「诉求不随重排自动死」,
    # 故不归 EPISODE_SCOPED(对比 advisories:那是"这一版方案"的即时告知,换方案
    # 该清零;台账是跨版本的诉求史,语义正相反)。
    # 【归并器,深审修正】不配 reducer 的 last-value 通道下,make_initial_state
    # 每轮写 [] 会把 checkpointer 里的台账**静默清空**——"靠 F-4 接线时记得
    # 读回传参"是我们一路在消灭的靠人记得式设计。照 messages(add_messages)
    # 先例配 _merge_demand_ledger:空更新=保留旧值(每轮初始化天然 no-op),
    # 非空=整体替换(record_demand 返回含顶替改写的全量列表,append 语义不适用)。
    # 写入/消费接线归 F-4/F-5,但存活性从此是结构保障,不依赖任何调用方自觉。

    # ---- 方案版本志（ADR-0011 前置核实①/决策 3：会话上下文打包器的版本
    # 摘要素材，E-2 第一块砖第二件）----
    plan_version_log: Annotated[list[dict], operator.add]  # SESSION_SCOPED：
    # list[dict]，条目形状 {version_n, summary, trigger, timestamp}——纯 dict，
    # 免 build.py serde 白名单（护栏1「非纯类型须补白名单」的另一面：纯类型
    # 天然不用补）。
    # 【reducer 选型，对照 _merge_demand_ledger 先例，二选一后的拍板】
    # demand_ledger 的写手语义是"整体替换成全量重写后的列表"（条目会被顶替/
    # 改写，如满足状态翻转），因此需要自定义合并器区分"空更新=保留旧值"与
    # "非空=整体替换"。plan_version_log 的写手语义相反——finalize_plan_node
    # / _writeback_graph_state 每次只返回**本轮新增的那一条**，历史条目永远
    # 不被改写/删除，是纯追加日志（更接近 messages 的 add_messages 先例，只是
    # 不需要它那套按 message id 去重合并的复杂度）。纯追加语义下 `operator.add`
    # （list 拼接）就是完整答案：`old + []`（每轮初始化写的空列表）在数学上
    # 恒等于 `old`本身——不需要像 _merge_demand_ledger 那样手写"空更新短路"，
    # 拼接空列表天然就是幺元操作。这也是 ADR-0011 护栏1 原文举例直接写
    # `Annotated[list, operator.add]` 的原因（LangGraph 标准累加器模式，见
    # 官方文档 state-reducers 一节，非本改动发明）。


# ============================================================
# 字段生命周期表（ADR-0012 决策 4）—— 真值来源
# ============================================================
# 完备性由 tests/test_state_lifecycle.py 强制执行：走查 AgentState.__annotations__
# 全部字段，断言每个字段在下面三个 frozenset 里恰好出现一次（并集=全字段、两两
# 交集=空）。新增 state 字段时必须同步登记到其中一个，否则该测试会红。

TURN_SCOPED: frozenset[str] = frozenset({
    "user_input",
    "route_kind",
    "router_decision",
    "chitchat_reply_text",
    "chitchat_tone",
    "chitchat_chips",
    "refinement_changed_fields",
    "refinement_note",
})
"""每个 turn 开始清零：make_initial_state 显式给这批字段赋初值。

route_kind/router_decision/chitchat_* 的清零其实是"表=代码"的精确性而非防护
——它们各自唯一的写手（router_node / chitchat_node）本轮必定无条件重跑并整体
覆盖，就算 make_initial_state 不清零也不会有陈旧值被读到。"""

SESSION_SCOPED: frozenset[str] = frozenset({
    "user_id",
    "session_id",
    "scenario_id",
    "planner_mode",
    "messages",
    "demand_ledger",
    "plan_version_log",
})
"""跨轮持久，从不被 make_initial_state 的"清零"语义覆盖：
messages 靠 add_messages reducer 自身跨轮累积；user_id/session_id/scenario_id/
planner_mode 由调用方（sse_adapter）每轮传入同一值透传进 state——是"确认"不是
"重置"，多数轮次这一步是 no-op（值和上一轮相同）。demand_ledger 靠
_merge_demand_ledger 归并器跨轮存活（空更新 no-op,同 messages 先例）——
存活性是结构保障,不依赖调用方读回传参;写入/消费接线归 F-4/F-5。
plan_version_log 靠 operator.add 归并器跨轮累积存活（ADR-0011 前置核实①）
——尤其是 confirm 回写（graph_confirm._writeback_graph_state）追加的
"已确认下单"条目：反馈触发新规划事件的 reset_for_new_episode() 只清
EPISODE_SCOPED，够不着 SESSION_SCOPED 的本字段，这正是它被登记在这里而不是
EPISODE_SCOPED 的唯一理由——治 user_decision/orders 那段"已知丢失窗口"。"""

EPISODE_SCOPED: frozenset[str] = frozenset({
    "intent",
    "pinned_targets",
    "pois",
    "restaurants",
    "user_profile",
    "pois_relaxed_tags",
    "restaurants_relaxed_tags",
    "weights",
    "blueprint",
    "plan_attempt",
    "itinerary",
    "violations",
    "has_critical",
    "critic_feedback_text",
    "retry_count",
    "replan_strategy",
    "decision_trace",
    "fallback_chain",
    "critic_attempts",
    "alternatives",
    "quality_issues",
    "advisories",
    "give_up_chips",
    "node_actions",
    "narration",
    "memory_status",
    "user_decision",
    "orders",
    "share_message",
    "execution_tool_results",
})
"""「规划事件」边界重置：新需求进 intent_node / 反馈进 refiner_node 时，两者共用
reset_for_new_episode() 生成的同一份 diff。make_initial_state 完全不碰这批字段
——它们跨 turn 默认保留（persistence by omission），直到下一次规划事件把它们
清零。已知丢失标注见 user_decision / orders 声明处注释（ADR-0012 决策 4）。"""


# ============================================================
# Helper：事件级重置 diff（ADR-0012 决策 4）
# ============================================================

def reset_for_new_episode() -> dict[str, Any]:
    """新规划事件开始时的重置 diff，供 intent_node / refiner_node 共用。

    调用纪律（两个调用点一致）：返回值必须铺在调用方自己的业务输出**之前**
    展开——`{**reset_for_new_episode(), **own_output}`——否则会把本轮刚解析/
    刚精炼出的 intent 等业务输出冲掉。EPISODE_SCOPED 里的 "intent" 也在本
    diff 内清零，只是因为两个调用点都保证自己的业务输出会立即覆盖它；这样
    reset_for_new_episode() 才能对 EPISODE_SCOPED 全集生成完整 diff，不必
    每个调用点各自记一份"该重置哪些、不该重置哪些"的手工清单。

    首轮 no-op：turn 1 时 make_initial_state 根本没写这批键，intent_node /
    refiner_node 读到的是"键缺失"（.get 等价于取到 None/空），reset diff 给出的
    同款零值不改变任何可观察行为——见 test_state_lifecycle.py 的首轮 no-op 测试。
    """
    diff: dict[str, Any] = {
        "intent": None,
        "pinned_targets": [],
        "pois": [],
        "restaurants": [],
        "user_profile": None,
        "pois_relaxed_tags": [],
        "restaurants_relaxed_tags": [],
        "weights": None,
        "blueprint": None,
        "plan_attempt": 0,
        "itinerary": None,
        "violations": [],
        "has_critical": False,
        "critic_feedback_text": None,
        "retry_count": 0,
        "replan_strategy": None,
        "decision_trace": None,
        "fallback_chain": [],
        "critic_attempts": [],
        "alternatives": [],
        "quality_issues": [],
        "advisories": [],
        "give_up_chips": [],
        "node_actions": {},
        "narration": None,
        "memory_status": None,
        "user_decision": None,
        "orders": [],
        "share_message": None,
        "execution_tool_results": [],
    }
    assert set(diff.keys()) == EPISODE_SCOPED, (
        "reset_for_new_episode() 的 key 集合必须与 EPISODE_SCOPED 完全一致"
        "——新增/删除 EPISODE_SCOPED 字段时两处要同步改"
    )
    return diff


# ============================================================
# Helper：State 工厂（给入口与测试用）
# ============================================================

def make_initial_state(
    *,
    user_input: str,
    user_id: str = "demo_user",
    session_id: str = "sess_default",
    scenario_id: Optional[str] = None,
    planner_mode: Optional[str] = None,
) -> AgentState:
    """构造本轮图调用的输入 diff（每轮调用；ADR-0012 决策 4）。

    只覆盖 TURN_SCOPED ∪ SESSION_SCOPED 两类字段（断言强制），完全不碰
    EPISODE_SCOPED——那批字段的清零职责在 reset_for_new_episode()，由
    intent_node / refiner_node 在"新规划事件开始"那一刻才触发。这样
    itinerary/critic_feedback_text 等字段才能真正跨 turn 存活到下一次
    router 读取（router_node 需要上一版 itinerary 判断新需求 vs 反馈），
    而不是被本函数在 router 跑之前就提前清空。

    `demand_ledger` 无需形参（深审修正）：它挂 `_merge_demand_ledger` 归并器,
    此处的 `[]` 经归并器是 no-op(同 messages 先例)——存档里的台账天然跨轮
    存活,不需要任何调用方"记得读回传参"。`plan_version_log` 同理：挂
    `operator.add` 归并器,此处的 `[]` 经拼接是幺元操作(no-op),版本志跨轮
    存活不需要任何调用方"记得读回传参"（ADR-0011 前置核实①）。
    """
    state = AgentState(
        # ---- TURN_SCOPED：清零成本轮初值 ----
        user_input=user_input,
        route_kind=None,
        router_decision=None,
        chitchat_reply_text=None,
        chitchat_tone=None,
        chitchat_chips=[],
        refinement_changed_fields=None,
        refinement_note=None,
        # ---- SESSION_SCOPED：调用方透传（非"重置"）----
        user_id=user_id,
        session_id=session_id,
        scenario_id=scenario_id,
        planner_mode=planner_mode if planner_mode in ("rule", "llm") else None,
        messages=[],
        demand_ledger=[],  # 经归并器 no-op,见字段注释
        plan_version_log=[],  # 经 operator.add 归并器 no-op,见字段注释
    )
    assert set(state.keys()) == (TURN_SCOPED | SESSION_SCOPED), (
        "make_initial_state 只应覆盖 TURN_SCOPED ∪ SESSION_SCOPED"
        "——EPISODE_SCOPED 字段的清零职责在 reset_for_new_episode()"
    )
    return state
