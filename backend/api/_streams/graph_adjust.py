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
- `alternative`：**候选池收窄手法**——`resolve_node_swap` 的降级序列本质是
  "在候选池里挑最优"，若原样传全量召回池，用户点的这个具名备选可能被同池里
  评分更高的另一候选顶替，违背"点这个就该换成这个"的字面承诺。机制与设计
  理由见 `agent.planning.planners.node_swap_support.narrow_pool_to_single_
  alternative` docstring（原是本文件私有 helper，ADR-0013 已知留痕"待抽
  中立 seam"落地后就近搬去与 `resolve_node_swap` 同层，供本文件与
  `collab/room.py` 共同复用）。不记账（"选定"不是"诉求"）。

【候选池来源：全量目录，不是 execute 阶段窄池（体感编排批 ⑤）】

`pois`/`restaurants` 曾经取 `state.get("pois")`/`state.get("restaurants")`
（execute 阶段搜索 worker 写入的候选池，出于性能考虑截得比较窄）。
`resolve_node_swap` 的前置条件 2（见 `node_swap.py` 模块 docstring）要求候选
池覆盖当前方案里**全部**已选节点（目标 + 全部保留节点）——真实 LLM 规划出的
方案，其选中的实体常常不在这个窄池里（LLM 蓝图路径的候选来源与 execute 阶段
搜索并非同一次截断），导致 `_build_full_scheduled` 反查不到实体直接
`ValueError`，换菜请求基本不可用。改用 `data.loader.load_pois()/
load_restaurants()`（全量目录）覆盖任何真实存在的实体，不再依赖 execute 阶段
窄池——`resolve_node_swap`/`feasible_alternatives`/`node_swap_support.
narrow_pool_to_single_alternative` 本身的降级序列/收窄逻辑不变，只是调用点
传入的候选池变大；`alternative` action 的"点这个就该换成这个"承诺仍由
`node_swap_support.narrow_pool_to_single_alternative` 收窄保证，不受候选池
变大影响（见上方「候选池收窄手法」节）。

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
- 房间侧接线（F-5）——房间处理器（`collab/room.py::RoomManager.
  _resolve_and_broadcast_adjust`）不是直接复用本文件的 `_graph_adjust`（房间
  是长连接多人会话，候选池现场重查/串行队列/归名广播这几处与单人 SSE 请求
  有真实差异，见该方法 docstring），而是与本文件一起共同复用更底层的
  `agent.planning.planners.node_swap.resolve_node_swap` 引擎 + 同层的
  `agent.planning.planners.node_swap_support` 编排 helper（节点定位/候选池
  整理/文案翻译），本文件不引入任何单人特有假设之外的耦合。
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional, Union

from agent.graph.nodes.narrate import _build_node_actions, _build_node_detail
from agent.intent.narrator import generate_template_node_chips
from agent.planning.planners.node_swap import SwapResult, resolve_node_swap
from agent.planning.planners.node_swap_support import (
    CONFIRMED_ADJUST_BLOCKED_MESSAGE,
    adjustment_descriptor,
    compose_narration_text,
    find_entity,
    narrow_pool_to_single_alternative,
    node_title,
    synthesize_source_text,
    target_kind,
)
from data.loader import load_pois, load_restaurants
from schemas import IntentExtraction, Itinerary, SseEvent, SseEventType
from schemas.advisory import AdvisoryCode
from schemas.demand_ledger import (
    LedgerEntry,
    NodeRef,
    active_adjustments,
    ledger_for_display,
    mark_satisfied,
    record_demand,
)
from schemas.domain import Poi, Restaurant
from schemas.node_adjustment import NodeAdjustmentDimension

from .._session_store import sync_snapshot
from .._sse_helpers import now_ms as _now_ms
from .models import (
    AdjustActionAdjust,
    AdjustActionAlternative,
    AdjustActionDislike,
    ChatAdjustRequest,
)

logger = logging.getLogger(__name__)

# ============================================================
# 单人换菜说明文案（房间侧有自己的归名版本，见 collab/room.py::
# RoomManager._build_room_narration；两者共享更细粒度的 node_swap_support.
# adjustment_descriptor / node_swap_support.compose_narration_text）
# ============================================================


def _build_success_narration(
    action: Union[AdjustActionAdjust, AdjustActionAlternative, AdjustActionDislike],
    old_title: str,
    new_title: str,
) -> str:
    """单人无归名的换菜说明（ADR-0013 F-4 任务书"按你的要求换成了X，更安静"风格）。

    注意：这是**完全命中**（tier 1/2 / 具名备选 / 点踩）的确认句——降级
    （SWAP_DEGRADED）时不能用它（"按你的要求…{descriptor}。"在降级语境下是
    假话：降级恰恰意味着没找到满足 descriptor 的候选），拼装收口在
    `compose_swap_success_narration`，那里会整句换成合并的诚实告知。
    """
    if isinstance(action, AdjustActionAdjust):
        descriptor = adjustment_descriptor(action.adjustment)
        return f"按你的要求，把「{old_title}」换成了「{new_title}」，{descriptor}。"
    if isinstance(action, AdjustActionAlternative):
        return f"已经按你选的，把「{old_title}」换成了「{new_title}」。"
    return f"收到，已经把「{old_title}」换掉了，换成了「{new_title}」，看看这个怎么样。"


def split_swap_degraded_advisory(
    advisory_dicts: list[dict],
) -> tuple[Optional[dict], list[dict]]:
    """把 SWAP_DEGRADED 从 advisory 列表里挑出来，返回 (该条或 None, 其余)。

    文案修缮批（真 LLM 点火 G1 实锤）：SWAP_DEGRADED 的 message 与换菜确认句
    首尾串联会把新店名说两遍（"换成了「X」…给你换了个最接近的——「X」"），
    且确认句在降级语境下宣称"按你的要求"是假话。修法是把该条的内容**并进
    主句**（见 `build_degraded_swap_narration`），不再尾拼原 message——其余
    advisory（CONSTRAINT_RELAXED 等）照旧尾拼。结构化通道（narration_payload
    ["messages"] / 图状态 advisories）不动：那是前端面板/审计的镜像，本函数
    只管说出来的那句话。

    比对兼容 str 枚举值与活的 AdvisoryCode 实例（AdvisoryCode 是 str Enum，
    `==` 对两种形态都成立——model_dump(mode="python") 给枚举实例，检查点
    序列化后回读是纯字符串）。
    """
    for i, a in enumerate(advisory_dicts):
        if a.get("code") == AdvisoryCode.SWAP_DEGRADED:
            return a, [x for j, x in enumerate(advisory_dicts) if j != i]
    return None, list(advisory_dicts)


def build_degraded_swap_narration(
    old_title: str,
    new_title: str,
    *,
    descriptor: str = "",
    requester: str = "你",
) -> str:
    """降级换菜的**一句**合并诚实告知（换菜确认 + 最接近告知不再各说一遍）。

    终审拍板口径："没找到完全符合的，换成最接近的「X」，不满意再告诉我"——
    诚实（不宣称要求已达成）、不重复（店名只出现一次）、不泄气（去掉"先将就
    一下"）。`descriptor` 是约束回声（"不辣"这类，终审点名要保留的好东西），
    以"{requester}要的「不辣」"的形式归位到**诉求**上，而不是宣称新店已满足。
    `requester` 供房间归名版复用（多人场景必须点名是谁提的，同
    `RoomManager._build_room_narration` 的既有纪律）。

    理想归属地是 `node_swap_support`（单人/房间共享的更细粒度文案 helper 都
    在那），本批 planning/ 冻结（并行改动在途），先落在单人拼装处、房间侧
    import 复用——搬家留给收口批。
    """
    want = f"{requester}要的「{descriptor}」" if descriptor else f"{requester}要的"
    return (
        f"{want}没找到完全符合的，把「{old_title}」换成了最接近的"
        f"「{new_title}」，不满意再告诉我。"
    )


def compose_swap_success_narration(
    base: str,
    advisory_dicts: list[dict],
    *,
    old_title: str,
    new_title: str,
    descriptor: str = "",
    requester: str = "你",
) -> str:
    """换菜成功文案的统一收口：降级时整句换成合并诚实告知（SWAP_DEGRADED 的
    内容并入主句、不再尾拼），其余 advisory 照旧走 `compose_narration_text`
    尾拼。完全命中路径 `base` 原样透传（G2 实录「已经按你选的…」是好的）。"""
    degraded, rest = split_swap_degraded_advisory(advisory_dicts)
    if degraded is not None:
        base = build_degraded_swap_narration(
            old_title, new_title, descriptor=descriptor, requester=requester
        )
    return compose_narration_text(base, rest)


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

    # ---- L0 禁令 2 守门：已确认下单的方案不静默换菜（见上方常量 docstring）----
    if state.get("user_decision") == "confirm":
        yield emit(
            SseEventType.AGENT_NARRATION,
            {"text": CONFIRMED_ADJUST_BLOCKED_MESSAGE, "stage": "stream"},
        )
        yield emit(SseEventType.DONE)
        return

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

    kind = target_kind(itinerary, req.node_id)
    if kind is None:
        raise ValueError(
            f"node_id={req.node_id!r} 在当前方案里找不到匹配的非 home 节点——"
            "方案可能已在你点击的同时被别的操作换掉，请刷新后重试"
        )
    old_title = node_title(itinerary, req.node_id)
    node_ref = NodeRef(kind=kind, target_id=req.node_id)  # type: ignore[arg-type]

    updated_ledger = ledger
    satisfied_dimension: Optional[NodeAdjustmentDimension] = None

    # swap_phrase：版本志 summary 的动作措辞（见下方成功分支的版本志追加）——
    # 三个动作分支各自最清楚"用户做了什么"，在这里就地定措辞，不在事后反推。
    if isinstance(action, AdjustActionDislike):
        swap_phrase = "点踩"
        result: SwapResult = resolve_node_swap(
            itinerary, intent, pois, restaurants,
            target_node_id=req.node_id,
            adjustment=None,
            ledger_slice=active_adjustments(ledger, node_ref=node_ref),
        )

    elif isinstance(action, AdjustActionAdjust):
        source_text = (action.label or "").strip() or synthesize_source_text(action.adjustment)
        swap_phrase = f"按『{source_text}』"
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
        swap_phrase = "指名"
        chosen_entity = find_entity(kind, action.target_id, pois, restaurants)
        if chosen_entity is None:
            # 候选池陈旧（罕见竞态：展示时还在，点击时已从召回结果里消失）——
            # 业务性告知，不是 stream_error，方案不动。
            yield emit(
                SseEventType.AGENT_NARRATION,
                {"text": "这个备选好像已经不在候选里了，我再帮你看看还有什么可以换。", "stage": "stream"},
            )
            yield emit(SseEventType.DONE)
            return
        call_pois, call_rests = narrow_pool_to_single_alternative(itinerary, pois, restaurants, kind, chosen_entity)
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
    # node_detail 平价补齐（ADR-0015 现场落地，随换菜后的新方案重算）：pois/
    # restaurants 此处即全量目录（本文件 load_pois/load_restaurants，见上），直接
    # 复用反查每个节点真实数据详情——不换池，同 narrate._build_node_detail 口径。
    node_detail = _build_node_detail(new_itinerary, pois, restaurants)
    advisory_dicts = [a.model_dump() for a in result.advisories]

    # ---- 版本志：换菜也是新版本（E-2-a 已知留待项，c′落地后补齐）----
    # 常规写手是 finalize_plan（trigger=first/feedback），confirm 一笔在
    # graph_confirm（trigger=confirm）；换菜不走图、方案却真的变了——不记这笔，
    # 版本志的"方案史"承诺就有洞（E-2 打包器/refiner 读到的历史会缺换菜版本）。
    # 条目形状与两位写手完全同款，version_n 续既有编号。trigger="adjust"。
    existing_log: list = state.get("plan_version_log") or []
    version_n = len(existing_log) + 1
    new_title = node_title(new_itinerary, result.swapped_to) if result.swapped_to else "新的一站"
    version_entry = {
        "version_n": version_n,
        "summary": f"v{version_n}: {swap_phrase}把「{old_title}」换成「{new_title}」",
        "trigger": "adjust",
        "timestamp": _now_ms(),
    }

    await graph.aupdate_state(
        config,
        {
            "itinerary": new_itinerary,
            "node_actions": node_actions,
            "node_detail": node_detail,
            "demand_ledger": [e.model_dump() for e in updated_ledger],
            "advisories": advisory_dicts,
            "plan_version_log": [version_entry],
        },
        as_node="narrate",
    )
    # 换菜后确认必须拿到新方案——/chat/confirm 只读 SESSION_STORE 投影，不读图状态
    # （见 api/_session_store.py::sync_snapshot docstring「这是什么问题」）。
    sync_snapshot(req.session_id, itinerary=new_itinerary.model_dump())

    new_title = node_title(new_itinerary, result.swapped_to or "")
    # 文案修缮批（G1 实锤）：降级换菜时确认句+最接近告知合并成一句诚实告知，
    # 店名不再说两遍（见 compose_swap_success_narration docstring）。
    narration_text = compose_swap_success_narration(
        _build_success_narration(action, old_title, new_title),
        advisory_dicts,
        old_title=old_title,
        new_title=new_title,
        descriptor=adjustment_descriptor(action.adjustment)
        if isinstance(action, AdjustActionAdjust)
        else "",
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
    if node_detail:
        narration_payload["node_detail"] = node_detail
    ledger_display = ledger_for_display(updated_ledger)
    if ledger_display:
        narration_payload["demand_ledger"] = ledger_display
    # 换菜备选收据（2026-07-11，见路演PPT/信任带设计终稿.md 同日修订「五收据」
    # 换菜备选行）：**不进信任带**——adjust 流不喂带是既定设计（单思考面，
    # 修订4）。这个数字只挂在换菜结果的这条 AGENT_NARRATION 上，供换菜响应 UI
    # （ItineraryCard 的 NarrationBlock 附近）显示"同类替补 N 家"小行。数据
    # 直接读 `node_actions[swapped_to]["alternatives"]`（刚好在这一刻现算好，
    # 与"点击换菜"实际消费的候选池/预验证同一条真相源，见 `_build_node_actions`
    # docstring）——不需要前端另外做"这个节点是不是刚被换过"的映射推断（换菜
    # 前后 target_id 会变，位置也可能因 SWAP_REORDERED 等 advisory 移动，前端
    # 侧推断不可靠；服务端在换菜发生的同一次请求里直接知道 swapped_to 是谁，
    # 是唯一干净的数据源）。"无内容不加字段"：swapped_to 为 None（三种 action
    # 分支里理论上 resolve_node_swap 成功时恒非 None，此处仍防御性判断）或该
    # 节点没有 alternatives 时不挂字段，换菜响应 UI 据此不渲染这一行。
    if result.swapped_to:
        swapped_actions = node_actions.get(result.swapped_to) if node_actions else None
        alt_count = len(swapped_actions.get("alternatives") or []) if swapped_actions else 0
        if alt_count > 0:
            narration_payload["swap_alternatives_count"] = alt_count
    yield emit(SseEventType.AGENT_NARRATION, narration_payload)

    yield emit(SseEventType.DONE)
