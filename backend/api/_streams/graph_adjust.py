"""_graph_adjust —— /chat/adjust 的单人节点调整 SSE 流（ADR-0013 F-4）。

把 F-1 局部重解引擎（`agent.planning.planners.node_swap`）+ F-2 诉求台账
（`schemas.demand_ledger`）+ F-3 按钮/备选生成（`agent.intent.narrator` 模板
生成器 + `agent.graph.nodes.narrate._build_node_actions`）接成用户可点的最后
一环：点节点行的「定向调整按钮 / 具名备选 / 点踩」三选一，直接换菜，不经过
LLM 路由（ADR-0013 决策 4「三者点击殊途同归进同一引擎，且都是结构化指令」）。

【三种 action 怎么各自喂给 resolve_node_swap】

- `adjust`：`adjustment` 原样透传；同时记账（`record_demand`），换菜成功且
  `degrade_tier ∈ {1, 2}`（谓词确实被满足，非"近似"）才把刚记的这条诉求标
  `SATISFIED`（见 `schemas.demand_ledger.mark_satisfied` docstring）。
- `dislike`：`adjustment=None`（无方向局部重解）；不记账（ADR 原文"点踩收编"
  是"选定/否决"不是"诉求"）。单人 UI 暂不发出这个 action，协议先立好给
  F-5 房间复用。
- `alternative`：**候选池收窄手法**（本文件的关键设计决策，见
  `_narrow_pool_to_single_alternative` docstring）——`resolve_node_swap` 的
  降级序列本质是"在候选池里挑最优"，若原样传全量召回池，用户点的这个具名
  备选可能被同池里评分更高的另一候选顶替，违背"点这个就该换成这个"的字面
  承诺。收窄传入的候选池到"当前已选中的全部实体（覆盖 `resolve_node_swap`
  前置条件 2）∪ 这一个被点中的备选"，让降级序列的候选集合里只有它一个
  "新"选项——`route_builder.repair_route` 自身按 `kept_keys` 排除"已经在场"
  的实体，故已选中实体混进候选池不会被误选为新增候选，最终真正竞争"这一格"
  的就只剩这一个被点中的备选。不记账（"选定"不是"诉求"）。

【候选池来源：全量目录，不是 execute 阶段窄池（体感编排批 ⑤）】

`pois`/`restaurants` 曾经取 `state.get("pois")`/`state.get("restaurants")`
（execute 阶段搜索 worker 写入的候选池，出于性能考虑截得比较窄）。
`resolve_node_swap` 的前置条件 2（见 `node_swap.py` 模块 docstring）要求候选
池覆盖当前方案里**全部**已选节点（目标 + 全部保留节点）——真实 LLM 规划出的
方案，其选中的实体常常不在这个窄池里（LLM 蓝图路径的候选来源与 execute 阶段
搜索并非同一次截断），导致 `_build_full_scheduled` 反查不到实体直接
`ValueError`，换菜请求基本不可用。改用 `data.loader.load_pois()/
load_restaurants()`（全量目录）覆盖任何真实存在的实体，不再依赖 execute 阶段
窄池——`resolve_node_swap`/`feasible_alternatives`/`_narrow_pool_to_single_
alternative` 本身的降级序列/收窄逻辑不变，只是调用点传入的候选池变大；
`alternative` action 的"点这个就该换成这个"承诺仍由 `_narrow_pool_to_single_
alternative` 收窄保证，不受候选池变大影响（见上方「候选池收窄手法」节）。

【resolve_node_swap 调用不显式传 user_profile/weights】

与 F-3 `agent.graph.nodes.narrate._build_node_actions`/其内部对
`feasible_alternatives` 的既有调用同一先例——该函数也不传这两个可选形参，
走内部默认（`load_user_profile()` / `get_planning_weights(intent, client=None)`）。
这不是偷懒省事：`AgentState.user_profile` 存的是 `GetUserProfileOutput`
包装（`.profile` 才是 `resolve_node_swap` 期望的 `UserProfile`），贸然直传
会传错类型；`state.weights` 虽类型对齐，但为了和 F-3 已确立的"节点级操作走
默认值，不强行跨阶段复用一次性算好的权重"路径保持一致，本文件同样选择不传，
避免引入一条 F-3 没有验证过的旁路。

【业务性失败 vs 契约违反】

`SwapResult.success=False`（无可换候选 / 保留节点排不到一块儿）是**业务性
失败**——ADR-0013 决策 4"绝不默默忽略"的延伸：AGENT_NARRATION 只带告知文案
+ done，方案不动，不是 `stream_error`（`api/adjust.py` 的 `safe_stream` 包装
只兜底真正的异常）。`resolve_node_swap` 自身对"node_id 不存在"等调用方契约
违反抛 `ValueError`——这类会自然落进 `safe_stream` 的兜底分支变成
`stream_error`，与"业务性失败"刻意区分对待（同 `node_swap.py` 模块 docstring
"前置条件……不满足即 ValueError，不是业务失败"的既定分层）。

不负责：
- 前置校验（session 有没有 checkpoint / 有没有方案）——在 `api/adjust.py`
  （对齐 `chat.py::chat_turn` 探活 + 直接 `HTTPException` 的既有风格，SSE
  流开始之前就该判完，不该流到一半才 4xx）。
- 房间侧接线（F-5）——房间处理器未来复用同一个 `_graph_adjust`（换掉
  `session_id`→`thread_id` 的取数来源即可），本文件不引入任何单人特有假设
  之外的耦合。
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional, Union

from agent.graph.nodes.narrate import _build_node_actions
from agent.intent.narrator import generate_template_node_chips
from agent.planning.planners.node_swap import SwapResult, resolve_node_swap
from data.loader import load_pois, load_restaurants
from schemas import IntentExtraction, Itinerary, SseEvent, SseEventType
from schemas.demand_ledger import (
    LedgerEntry,
    NodeRef,
    active_adjustments,
    ledger_for_display,
    mark_satisfied,
    record_demand,
)
from schemas.domain import Poi, Restaurant
from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension

from .._session_store import sync_snapshot
from .._sse_helpers import now_ms as _now_ms
from .models import (
    AdjustActionAdjust,
    AdjustActionAlternative,
    AdjustActionDislike,
    ChatAdjustRequest,
)

logger = logging.getLogger(__name__)

Entity = Union[Poi, Restaurant]

# ============================================================
# 诉求台账 source_text 兜底合成（label 缺省时用；见 AdjustActionAdjust docstring）
# ============================================================

_DIMENSION_ZH: dict[NodeAdjustmentDimension, str] = {
    NodeAdjustmentDimension.PRICE: "价格",
    NodeAdjustmentDimension.DISTANCE: "距离",
    NodeAdjustmentDimension.CUISINE_OR_TYPE: "类型",
    NodeAdjustmentDimension.DIETARY: "口味",
    NodeAdjustmentDimension.AMBIENCE: "氛围",
    NodeAdjustmentDimension.CROWD_FIT: "适配",
}


def _synthesize_source_text(adjustment: NodeAdjustment) -> str:
    dim_zh = _DIMENSION_ZH.get(adjustment.dimension, "调整")
    return f"{dim_zh}：{adjustment.value}"


# ============================================================
# 节点定位 + 换菜说明文案（自包含中文句子，同 Advisory.message 纪律）
# ============================================================


def _target_kind(itinerary: Itinerary, node_id: str) -> Optional[str]:
    for node in itinerary.nodes:
        if node.target_kind != "home" and node.target_id == node_id:
            return node.target_kind
    return None


def _node_title(itinerary: Itinerary, target_id: str) -> str:
    for node in itinerary.nodes:
        if node.target_id == target_id:
            return node.title
    return target_id


def _find_entity(kind: str, target_id: str, pois: list[Poi], restaurants: list[Restaurant]) -> Optional[Entity]:
    pool: list[Entity] = pois if kind == "poi" else restaurants
    return next((e for e in pool if e.id == target_id), None)


_AMBIENCE_ZH = {"安静聊天": "更安静", "热闹": "更热闹"}


def _adjustment_descriptor(adjustment: NodeAdjustment) -> str:
    """把定向调整翻成一小句人话，拼进换菜说明（"更安静"这类风格短语）。"""
    dim, value = adjustment.dimension, adjustment.value
    if dim == NodeAdjustmentDimension.PRICE:
        return "更便宜" if value == "cheaper" else "贵一点但应该更值"
    if dim == NodeAdjustmentDimension.DISTANCE:
        return "更近" if value == "closer" else "稍远一点但更合适"
    if dim == NodeAdjustmentDimension.CUISINE_OR_TYPE:
        return f"换成{value}口味"
    if dim == NodeAdjustmentDimension.AMBIENCE:
        return _AMBIENCE_ZH.get(value, f"更{value}")
    # DIETARY / CROWD_FIT：value 本身就是可读短语（"不辣" / "亲子友好"）
    return value


def _build_success_narration(
    action: Union[AdjustActionAdjust, AdjustActionAlternative, AdjustActionDislike],
    old_title: str,
    new_title: str,
) -> str:
    """单人无归名的换菜说明（ADR-0013 F-4 任务书"按你的要求换成了X，更安静"风格）。"""
    if isinstance(action, AdjustActionAdjust):
        descriptor = _adjustment_descriptor(action.adjustment)
        return f"按你的要求，把「{old_title}」换成了「{new_title}」，{descriptor}。"
    if isinstance(action, AdjustActionAlternative):
        return f"已经按你选的，把「{old_title}」换成了「{new_title}」。"
    return f"收到，已经把「{old_title}」换掉了，换成了「{new_title}」，看看这个怎么样。"


def _compose_narration_text(base: str, advisories: list[dict]) -> str:
    """advisory message 并入 text（同 narrate.py 一贯把 D-7 告知拼进暖语气正文的纪律）。"""
    extra = "".join(a["message"] for a in advisories if a.get("message"))
    return f"{base}{extra}" if extra else base


# ============================================================
# 「具名备选」候选池收窄（见模块 docstring「候选池收窄手法」）
# ============================================================


def _narrow_pool_to_single_alternative(
    itinerary: Itinerary,
    pois: list[Poi],
    restaurants: list[Restaurant],
    kind: str,
    chosen: Entity,
) -> tuple[list[Poi], list[Restaurant]]:
    kept_poi_ids = {n.target_id for n in itinerary.nodes if n.target_kind == "poi"}
    kept_rest_ids = {n.target_id for n in itinerary.nodes if n.target_kind == "restaurant"}
    call_pois = [p for p in pois if p.id in kept_poi_ids]
    call_rests = [r for r in restaurants if r.id in kept_rest_ids]
    if kind == "poi" and chosen.id not in kept_poi_ids:
        call_pois.append(chosen)  # type: ignore[arg-type]
    elif kind == "restaurant" and chosen.id not in kept_rest_ids:
        call_rests.append(chosen)  # type: ignore[arg-type]
    return call_pois, call_rests


# ============================================================
# 主流程
# ============================================================


async def _graph_adjust(
    req: ChatAdjustRequest,
    *,
    graph: Any,
    config: dict[str, Any],
    state: dict[str, Any],
) -> AsyncIterator[SseEvent]:
    seq = 0

    def emit(type_: SseEventType, payload: Optional[dict[str, Any]] = None) -> SseEvent:
        nonlocal seq
        ev = SseEvent(type=type_, seq=seq, payload=payload or {}, timestamp_ms=_now_ms())
        seq += 1
        return ev

    action = req.action
    yield emit(SseEventType.AGENT_THOUGHT, {"text": "收到，这就帮你调整一下这一站……"})

    itinerary: Itinerary = state["itinerary"]
    intent: IntentExtraction = state["intent"]
    # 体感编排批 ⑤：候选池改用全量目录，不再吃 execute 阶段窄池（见模块
    # docstring「候选池来源」）——resolve_node_swap 的前置条件 2 要求候选池
    # 覆盖当前方案里全部已选节点，execute 阶段搜索 worker 的候选池经常裁得比
    # 这更窄，方案实际选中的实体不在池里时会直接 ValueError。
    pois: list[Poi] = load_pois()
    restaurants: list[Restaurant] = load_restaurants()
    ledger_raw: list[dict] = state.get("demand_ledger") or []
    ledger = [LedgerEntry.model_validate(d) for d in ledger_raw]

    kind = _target_kind(itinerary, req.node_id)
    if kind is None:
        raise ValueError(
            f"node_id={req.node_id!r} 在当前方案里找不到匹配的非 home 节点——"
            "方案可能已在你点击的同时被别的操作换掉，请刷新后重试"
        )
    old_title = _node_title(itinerary, req.node_id)
    node_ref = NodeRef(kind=kind, target_id=req.node_id)  # type: ignore[arg-type]

    updated_ledger = ledger
    satisfied_dimension: Optional[NodeAdjustmentDimension] = None

    if isinstance(action, AdjustActionDislike):
        result: SwapResult = resolve_node_swap(
            itinerary, intent, pois, restaurants,
            target_node_id=req.node_id,
            adjustment=None,
            ledger_slice=active_adjustments(ledger, node_ref=node_ref),
        )

    elif isinstance(action, AdjustActionAdjust):
        source_text = (action.label or "").strip() or _synthesize_source_text(action.adjustment)
        new_entry = LedgerEntry(
            member_id=None,
            nickname=None,
            node_ref=node_ref,
            adjustment=action.adjustment,
            source_text=source_text,
        )
        # ledger_slice 用记账**前**的既有生效诉求——本次新提的这条诉求已经通过
        # resolve_node_swap 的 `adjustment` 主参数表达，不需要在 ledger_slice
        # 里再放一份自己（见 node_swap.py「ledger_slice 消费接口」：它偏置的是
        # "其它"标准生效诉求）。
        ledger_slice = active_adjustments(ledger, node_ref=node_ref)
        result = resolve_node_swap(
            itinerary, intent, pois, restaurants,
            target_node_id=req.node_id,
            adjustment=action.adjustment,
            ledger_slice=ledger_slice,
        )
        updated_ledger = record_demand(ledger, new_entry)
        if result.success and result.degrade_tier in (1, 2):
            satisfied_dimension = action.adjustment.dimension
            updated_ledger = mark_satisfied(
                updated_ledger, member_id=None, node_ref=node_ref, dimension=satisfied_dimension
            )

    else:  # AdjustActionAlternative
        assert isinstance(action, AdjustActionAlternative)
        chosen_entity = _find_entity(kind, action.target_id, pois, restaurants)
        if chosen_entity is None:
            # 候选池陈旧（罕见竞态：展示时还在，点击时已从召回结果里消失）——
            # 业务性告知，不是 stream_error，方案不动。
            yield emit(
                SseEventType.AGENT_NARRATION,
                {"text": "这个备选好像已经不在候选里了，我再帮你看看还有什么可以换。", "stage": "stream"},
            )
            yield emit(SseEventType.DONE)
            return
        call_pois, call_rests = _narrow_pool_to_single_alternative(itinerary, pois, restaurants, kind, chosen_entity)
        result = resolve_node_swap(
            itinerary, intent, call_pois, call_rests,
            target_node_id=req.node_id,
            adjustment=None,
            ledger_slice=(),
        )

    # ---- 业务性失败：方案不动，只告知（不是 stream_error）----
    if not result.success:
        if isinstance(action, AdjustActionAdjust):
            # 诉求依然记账（换不成不代表用户不再想要——F-2"诉求不随重排自动
            # 死"的既定语义），即便这次没能满足也要持久化。
            await graph.aupdate_state(
                config, {"demand_ledger": [e.model_dump() for e in updated_ledger]}, as_node="narrate"
            )
        message = result.advisories[0].message if result.advisories else "这一步暂时没能调整成功，方案维持不变。"
        yield emit(SseEventType.AGENT_NARRATION, {"text": message, "stage": "stream"})
        yield emit(SseEventType.DONE)
        return

    # ---- 成功：重算 node_actions + 回写图状态 + 同步 SESSION_STORE 投影 ----
    new_itinerary = result.new_itinerary
    node_chips = generate_template_node_chips(new_itinerary, intent, pois, restaurants)
    node_actions = _build_node_actions(new_itinerary, intent, pois, restaurants, node_chips)
    advisory_dicts = [a.model_dump() for a in result.advisories]

    await graph.aupdate_state(
        config,
        {
            "itinerary": new_itinerary,
            "node_actions": node_actions,
            "demand_ledger": [e.model_dump() for e in updated_ledger],
            "advisories": advisory_dicts,
        },
        as_node="narrate",
    )
    # 换菜后确认必须拿到新方案——/chat/confirm 只读 SESSION_STORE 投影，不读图状态
    # （见 api/_session_store.py::sync_snapshot docstring「这是什么问题」）。
    sync_snapshot(req.session_id, itinerary=new_itinerary.model_dump())

    new_title = _node_title(new_itinerary, result.swapped_to or "")
    narration_text = _compose_narration_text(
        _build_success_narration(action, old_title, new_title), advisory_dicts
    )

    yield emit(SseEventType.ITINERARY_READY, new_itinerary.model_dump())

    narration_payload: dict[str, Any] = {"text": narration_text, "stage": "stream"}
    if advisory_dicts:
        narration_payload["messages"] = [
            {"kind": "advisory", "code": a.get("code"), "text": a.get("message")}
            for a in advisory_dicts
            if a.get("message")
        ]
    if node_actions:
        narration_payload["node_actions"] = node_actions
    ledger_display = ledger_for_display(updated_ledger)
    if ledger_display:
        narration_payload["demand_ledger"] = ledger_display
    yield emit(SseEventType.AGENT_NARRATION, narration_payload)

    yield emit(SseEventType.DONE)
