"""agent.graph.build —— LangGraph 拓扑编织。

最终拓扑：

START
  → router (条件分支)
       ├── chitchat → END
       ├── refiner → execute → planner → ...
       └── intent → execute → planner → ...

execute 阶段（并行 worker）：
  - search_pois_worker
  - search_restaurants_worker
  - get_user_profile_worker
所有 worker 完成后汇聚到 execute_collect

execute_collect → planner（出 weights + blueprint）
                → assemble（蓝图→Itinerary）
                → critic（验证）
                  ├── 通过 → finalize_plan → narrate → END
                  └── 硬违规 → replan_router
                              ├── llm_backprompt → planner（带 critic_feedback）
                              ├── ils_fallback → ils_replan → finalize_plan → narrate
                              └── give_up → finalize_plan → narrate

【体感编排批 P1："先出方案，后出文案"】三条入 narrate 的边（critic 通过 /
replan give_up / ils 成功）统一先经 `finalize_plan`（纯规则：规则标题 /
pending_actions / decision_trace 收尾，见 `agent.graph.nodes.finalize_plan`
docstring），再进 `narrate`（叙事 LLM）。`ITINERARY_READY` 因此在 `finalize_plan`
完成的那一刻就推给前端——不必等 narrate 的 LLM 调用（真实场景数秒到数十秒）跑完，
方案本身早就定稿了，用户不该为一句锦上添花的文案多等一整轮 LLM 往返。

narrate → END 是图的真实终点（不是虚构的图内 interrupt；ADR-0012 决策 2「结构
诚实」）。前端的「三按钮」不靠图内 HITL 实现：
  - confirm：不进图。/chat/confirm 走 HTTP 旁路（api/_streams/graph_confirm.py），
    直调 graph/nodes/execute_finalize.py 的 execute_finalize_node 函数完成下单，
    再用 aupdate_state 把终版方案 + user_decision="confirm" 回写进本图 checkpoint。
    execute_finalize_node 函数体保留，但**不是**本图注册的节点（见下方 g.add_node 注释）。
  - refine：前端再发一轮 /chat/turn，router 识别为反馈 → refiner → execute → planner → ...
  - cancel：前端不再调用任何后端接口，图不感知。

InMemorySaver checkpointer：thread_id=session_id，跨 turn 持久化 messages。
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agent.graph._resilience import drain_on_error
from agent.graph.nodes.assemble import assemble_node
from agent.graph.nodes.chitchat import chitchat_node
from agent.graph.nodes.critic import critic_node, route_after_critic
from agent.graph.nodes.execute import (
    execute_collect_node,
    get_user_profile_worker,
    search_pois_worker,
    search_restaurants_worker,
)
from agent.graph.nodes.finalize_plan import finalize_plan_node
from agent.graph.nodes.intent import intent_node
from agent.graph.nodes.narrate import narrate_node
from agent.graph.nodes.planner import planner_node
from agent.graph.nodes.refiner import refiner_node
from agent.graph.nodes.replan import (
    ils_replan_node,
    replan_router_node,
    route_after_replan,
)
from agent.graph.nodes.router import route_after_router, router_node
from agent.graph.state import AgentState


def _route_after_ils(state: AgentState) -> str:
    """ils_replan 后总走 finalize_plan → narrate，不再回 critic。

    设计原因（防 ILS 死循环 P1，2026-05-23）：
        ILS 自身不解决 commute_infeasible（蓝图段间通勤可达性约束，详见
        pitfalls P1-2026-05-22）。如果让 ILS 输出过 critic，遇到 commute
        违规会再次进 replan_router → ils_fallback → ils_replan，构成死循环。
        这里硬性接到 finalize_plan → narrate：ILS / rule fallback 已经尽力了，
        让用户先看到方案，commute 问题由 narration 文案兜底（"实际通勤可能比
        预估稍长"）。

    退化路径：
        - replan_strategy="give_up" → 也走 finalize_plan → narrate（兜底文案）
        - itinerary=None → 也走 finalize_plan → narrate（finalize_plan 对此
          no-op，用户起码看到状态而不是无限转圈）
    """
    return "finalize_plan"


# ============================================================
# 模块级单例（首次 build 后缓存）
# ============================================================

_compiled_graph: Any = None


def _build_checkpoint_serde():
    """构造注册了业务 Pydantic 类型的 msgpack serde（spec feedback-routing-fix R5）。

    背景：InMemorySaver 默认 JsonPlusSerializer 的 allowed_msgpack_modules=True
    语义是「全允许但对未注册类型发警告」（langgraph 1.2 源码 _check_allowed）。
    跨 turn 反序列化 AgentState 里的 Poi / Restaurant / IntentExtraction / Itinerary
    等会刷一堆「Deserializing unregistered type ... blocked in future version」警告。

    本函数传**具体类型清单**（非 True），消除警告。清单由 scripts 动态穷举跨 turn +
    rule/llm 双路径实际序列化的全部顶层类型得到（Pydantic 模型整体作为一个 msgpack ext，
    内部嵌套字段不单独触发，所以只需顶层 11 类）。

    重要：传具体清单后，**不在清单的类型会被 block**（langgraph STRICT 逻辑）。
    因此清单必须覆盖所有路径会序列化的类型；新增 state 业务类型时需同步补此清单。
    失败兜底：构造异常时回退默认 InMemorySaver（保留警告但功能不受影响）。
    """
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    # AgentState 跨 turn + rule/llm 双路径实际序列化的全部顶层类型
    # （由 scripts 动态穷举 emit_serde_event 得到；新增 state 业务类型需同步补充）
    from schemas.domain import Poi, Restaurant
    from schemas.intent import IntentExtraction
    from schemas.itinerary import Itinerary
    from schemas.router import InputKind, RouterDecision
    from schemas.tools import GetUserProfileOutput
    from agent.planning.weights_llm import PlanningWeights
    from agent.planning.blueprint.blueprint import (
        BlueprintNode,
        BlueprintTargetKind,
        PlanBlueprint,
    )
    from agent.planning.critic._rules.types import (
        Severity,
        Violation,
        ViolationCode,
    )
    # ADR-0013 F-4：demand_ledger（SESSION_SCOPED，跨 turn 存活）第一次被真实
    # 图状态写回 + 读回（aupdate_state/aget_state，见 api/_streams/graph_adjust.py）
    # 实测出的两个新顶层类型——`LedgerEntry.model_dump()`（默认 python mode）
    # 不把嵌套 Enum 字段转成 plain str（与 Pydantic 默认行为一致，`mode="json"`
    # 才会转换），`dimension`/`status` 两个字段在 dict 里仍是活的 Enum 实例，
    # 会被 msgpack 当成"顶层"类型单独 ext 编码（不是「Pydantic 模型整体一个
    # ext」那条免检路径——demand_ledger 类型标注是 `list[dict]`，从不出现
    # `LedgerEntry`/`NodeRef`/`NodeAdjustment` 的活实例，只有这两个 Enum 会
    # 触发未注册告警）。补齐即可，同文件顶部注释"新增 state 业务类型时需
    # 同步补此清单"的既定维护纪律。
    from schemas.demand_ledger import LedgerEntryStatus
    from schemas.node_adjustment import NodeAdjustmentDimension

    # 深审顺带发现的 D-7 既有缺口（不是 F-4 新引入，但 F-4 是第一个真实踩中它
    # 的调用方）：`state.advisories`（`Advisory.model_dump()` 列表）自 D-7 起就
    # 是 EPISODE_SCOPED 字段，`ils_replan_node` 在图内正常执行时就会把非空
    # advisories 写进 diff，随每个节点后的自动 checkpoint 落盘——同样嵌了活的
    # `AdvisoryCode` 枚举实例，同一条"未注册顶层类型"告警路径。此前无人实测
    # 到是因为凑巧没有现有测试在"advisories 非空的那一轮"之后又去
    # `aget_state` 读一次；F-4 的 `_graph_adjust` 在换菜命中 tier 3
    # （`SWAP_DEGRADED`）时会真实产出非空 advisories 并显式 aupdate_state，
    # 顺手补齐，不留一个"已知但没人修"的告警在日志里。
    from schemas.advisory import AdvisoryCode

    allowlist = [
        Poi,
        Restaurant,
        IntentExtraction,
        Itinerary,
        InputKind,
        RouterDecision,
        GetUserProfileOutput,
        PlanningWeights,
        ViolationCode,
        Severity,
        Violation,
        # spec planning-pipeline-consolidation R4：反馈走 /chat/turn 依赖 checkpointer
        # 跨 turn 恢复 blueprint，补这三类避免反序列化被 block 致 blueprint 丢失。
        PlanBlueprint,
        BlueprintNode,
        BlueprintTargetKind,
        # ADR-0013 F-4：demand_ledger 跨 turn 回写读取（见上方本段注释）。
        NodeAdjustmentDimension,
        LedgerEntryStatus,
        # D-7 既有缺口，F-4 顺手补齐（见上方本段注释）。
        AdvisoryCode,
    ]
    return JsonPlusSerializer(allowed_msgpack_modules=allowlist)


def build_graph(*, with_checkpointer: bool = True, checkpointer: Any = None) -> Any:
    """构造并编译 LangGraph。

    Args:
        with_checkpointer: 是否加默认 InMemorySaver；测试时可关。
        checkpointer: 显式传入的 checkpointer（如 AsyncRedisSaver）；传了就用它，
            忽略 with_checkpointer。由 warm_up_graph() 在 SESSION_STORE=redis 时注入。
    """
    g = StateGraph(AgentState)

    # ---- 节点 ----
    g.add_node("router", router_node)
    g.add_node("chitchat", chitchat_node)
    g.add_node("intent", intent_node)
    g.add_node("refiner", refiner_node)

    # ---- D2 失败降级阶梯（output degradation ladder）----
    # 在「注册时」给规划主链节点挂 drain_on_error 安全网，把策略集中在此处一目了然：
    #   planner / assemble / critic / replan_*  → rule_floor（非预期异常 → 规则地板方案）
    #   finalize_plan                           → passthrough（体感编排批 P1：原样透传现有 itinerary）
    #   narrate                                 → emit_plan（推已通过的方案、跳文案）
    #   3 个搜索 worker                          → empty（空候选继续）
    # 这些节点的【非预期】异常不再冒泡成裸 STREAM_ERROR；控制流异常（interrupt/command）
    # 仍被 drain_on_error 原样 re-raise。intent 节点走自身内部兜底（见 intent_node），不在此挂。

    # execute 阶段：并行 worker（异常 → 空候选继续）
    g.add_node("search_pois_worker", drain_on_error(search_pois_worker, "empty"))
    g.add_node(
        "search_restaurants_worker", drain_on_error(search_restaurants_worker, "empty")
    )
    g.add_node(
        "get_user_profile_worker", drain_on_error(get_user_profile_worker, "empty")
    )
    g.add_node("execute_collect", execute_collect_node)

    # plan 阶段（异常 → 规则地板）
    g.add_node("planner", drain_on_error(planner_node, "rule_floor"))
    g.add_node("assemble", drain_on_error(assemble_node, "rule_floor"))

    # critic + replan（异常 → 规则地板）
    g.add_node("critic", drain_on_error(critic_node, "rule_floor"))
    g.add_node("replan_router", drain_on_error(replan_router_node, "rule_floor"))
    g.add_node("ils_replan", drain_on_error(ils_replan_node, "rule_floor"))

    # finalize_plan（体感编排批 P1：先出方案，后出文案——见节点/build 模块 docstring）
    g.add_node("finalize_plan", drain_on_error(finalize_plan_node, "passthrough"))

    # narrate（异常 → 推已通过的方案、跳文案）
    # execute_finalize 不在这里注册（ADR-0012 决策 2「结构诚实」）：它是确认流
    # HTTP 旁路直调的函数（api/_streams/graph_confirm.py），不是图节点——曾经的
    # add_node("execute_finalize", ...) 注册过一个【无入边】永达不到的死节点，
    # 现在把"不进图"这件事做成结构事实，而不只是靠注释描述。
    g.add_node("narrate", drain_on_error(narrate_node, "emit_plan"))

    # ---- 边 ----
    # START → router
    g.add_edge(START, "router")

    # router 后分支
    g.add_conditional_edges(
        "router",
        route_after_router,
        {
            "chitchat": "chitchat",
            "intent": "intent",
            "refiner": "refiner",
        },
    )

    # chitchat → END
    g.add_edge("chitchat", END)

    # intent / refiner 都进 execute（并行 worker）
    for src in ("intent", "refiner"):
        g.add_edge(src, "search_pois_worker")
        g.add_edge(src, "search_restaurants_worker")
        g.add_edge(src, "get_user_profile_worker")

    # 3 个 worker 都完成后汇聚 execute_collect
    g.add_edge("search_pois_worker", "execute_collect")
    g.add_edge("search_restaurants_worker", "execute_collect")
    g.add_edge("get_user_profile_worker", "execute_collect")

    # execute_collect → planner → assemble → critic
    g.add_edge("execute_collect", "planner")
    g.add_edge("planner", "assemble")
    g.add_edge("assemble", "critic")

    # critic 后分支
    # 体感编排批 P1：route_after_critic（agent/graph/nodes/critic.py，本批范围外
    # 不改）返回值字面仍是 "narrate"——这里把它重定向到 "finalize_plan"（path_map
    # 只是 {返回值: 目标节点名}，键不必等于目标节点名），不必改 critic.py 本身。
    g.add_conditional_edges(
        "critic",
        route_after_critic,
        {
            "narrate": "finalize_plan",
            "replan_router": "replan_router",
        },
    )

    # replan_router 后分支（route_after_replan 同上，返回值字面仍是 "narrate"，
    # 本批不改 agent/graph/nodes/replan.py，只重定向 path_map 目标）
    g.add_conditional_edges(
        "replan_router",
        route_after_replan,
        {
            "planner": "planner",         # llm_backprompt 回 planner
            "ils_replan": "ils_replan",   # ils_fallback
            "narrate": "finalize_plan",   # give_up → finalize_plan → narrate
        },
    )

    # ils_replan → 条件分支：成功走 critic 验证，失败走 finalize_plan → narrate（不再循环）
    g.add_conditional_edges(
        "ils_replan",
        _route_after_ils,
        {
            "critic": "critic",
            "finalize_plan": "finalize_plan",
        },
    )

    # finalize_plan → narrate（体感编排批 P1：三条入 narrate 的边统一先经这里）
    g.add_edge("finalize_plan", "narrate")

    # narrate → END（confirm 不在图内：HTTP 旁路 /chat/confirm 直调 execute_finalize_node
    # 函数体 + aupdate_state 写回图状态，见 api/_streams/graph_confirm.py；ADR-0012 决策 2）
    # 三按钮里 refine / cancel 由前端再次发起 /chat/turn 触发新的 graph 执行：
    #   - refine  → 走 refiner_node 路径（user_input 是反馈）
    #   - cancel  → 不再触发 graph
    g.add_edge("narrate", END)

    # ---- 编译 ----
    if checkpointer is not None:
        # 显式 checkpointer（redis 模式由 warm_up_graph 传入 AsyncRedisSaver）
        return g.compile(checkpointer=checkpointer)
    if with_checkpointer:
        return g.compile(checkpointer=_build_memory_checkpointer())
    return g.compile()


def _build_memory_checkpointer() -> Any:
    """InMemorySaver（memory 模式 / 默认）。注册业务类型消除反序列化警告。"""
    # spec feedback-routing-fix R5：注册业务类型，消除反序列化警告 + 防未来 block
    try:
        serde = _build_checkpoint_serde()
        return InMemorySaver(serde=serde)
    except Exception:  # noqa: BLE001
        # 注册 API 不可用（langgraph 版本差异）→ 回退默认（保留警告但不阻断）
        return InMemorySaver()


async def _build_redis_checkpointer() -> Any:
    """AsyncRedisSaver（仅 SESSION_STORE=redis 时构造）。

    懒导入 langgraph-checkpoint-redis，绝不影响 memory 路径。
    asetup() 建 redisvl 索引（幂等）。默认 serde（JsonPlusRedisSerializer）对未注册
    业务类型「全允许 + 警告」，功能不受影响；如需消除警告可改用 saver.with_allowlist(...)。
    """
    import os

    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

    redis_url = os.getenv("REDIS_URL") or "redis://localhost:6379/0"
    saver = AsyncRedisSaver(redis_url=redis_url)
    await saver.asetup()
    return saver


def get_compiled_graph() -> Any:
    """模块级单例（同步入口，sse_adapter / chat / room 等 5 处都用它）。

    - 已被 warm_up_graph() 预初始化（redis 模式 startup）→ 直接返回该单例；
    - 否则（memory 模式 / 测试 / 未预热）→ 同步构造 InMemorySaver 版，
      与重构前行为 100% 一致，保证默认裸机路径零回归。
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph(with_checkpointer=True)
    return _compiled_graph


async def warm_up_graph() -> str:
    """startup 钩子：按 SESSION_STORE 预初始化 graph 单例，返回实际后端名。

    - SESSION_STORE=redis → 用 AsyncRedisSaver（await asetup）编译并缓存单例，
      使跨 turn checkpoint 落 Redis、多实例一致、进程重启可恢复；
    - 其他（memory）→ 走默认 InMemorySaver（与不预热等价）；
    - redis 初始化失败（如 redis 未起）→ 回退 InMemorySaver 保证可用，并 warning。
    """
    global _compiled_graph
    import logging
    import os

    session_store = (os.getenv("SESSION_STORE") or "memory").strip().lower()
    if session_store == "redis":
        try:
            cp = await _build_redis_checkpointer()
            _compiled_graph = build_graph(checkpointer=cp)
            logging.getLogger("graph").info("langgraph checkpointer backend: redis")
            return "redis"
        except Exception as e:  # noqa: BLE001
            logging.getLogger("graph").warning(
                "redis checkpointer init failed, fallback to InMemorySaver: %s: %s",
                type(e).__name__,
                e,
            )
    if _compiled_graph is None:
        _compiled_graph = build_graph(with_checkpointer=True)
    return "memory"
