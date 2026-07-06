"""nodes.store_swap —— B2："换个店铺"聊天反馈的换全店/点名换店编排节点。

【这是什么问题 + 为什么不是新写一套排除/降级逻辑】
`agent.routing.store_swap_router.classify_store_swap` 已经把"这句反馈是不是
换店"判完（点名 / 泛化 / 都不是）；本节点负责**执行**——把判定结果接到既有的
局部重解引擎 `agent.planning.planners.node_swap.resolve_node_swap`（ADR-0013
F-1，按钮点击换菜用的同一个引擎：黑名单排除 + 三级降级 + 诚实兜底，见该模块
docstring）。"泛化换店"= 对方案里**每一个**非 home、非赞锁定的节点依次调用
一次 `resolve_node_swap`（复用现成引擎循环，不新写第二份"排除+降级"逻辑）；
"点名换店"= 只对点中的那一个节点调用一次。两种模式共享同一份编排代码，差异
只在"要处理哪些 target_id"。

【累积排除（会话级，防 ping-pong）】
每次成功换掉一个节点，把**被换掉的旧实体 id** 并入 `state.
swapped_out_entity_ids`（`agent.graph.state` 的 SESSION_SCOPED 字段，归并器
`_merge_swapped_out_entity_ids` 取并集、跨轮不清零）。下一次换店时，喂给
`resolve_node_swap` 的候选池要先剔除"当前会话已经换掉过的全部实体"——否则
"换店 A→B"之后再"换店 B→A"会把 A 换回来，用户在两个版本之间来回横跳，
"换了"这句话失去意义。**只过滤候选池，不碰 `resolve_node_swap` 的
`pois`/`restaurants` 原始契约**：过滤掉的实体保证不会是"当前已在场的节点"
（它们要么是本会话更早被换掉的（已不在场），要么是本次循环里本节点自己的
目标（`resolve_node_swap` 内部按 `blacklist_poi`/`blacklist_rest={target_id}`
自己排除，见该模块「Visit 重建」节关于黑名单必须命中的前置条件），因此在
调用前把这批 id 从候选池里删掉，不会破坏"候选池须覆盖当前方案里全部已选
节点"这条前置条件。

【赞锁定（locked_targets）怎么跳过】
"泛化换店"：目标节点清单 = 方案里全部非 home 节点，减去 `state.
pinned_targets` 点名的那些（房间"赞"锁定，单人路径恒为空列表，天然全换——
见 `agent.planning.planners.node_swap.py` 与 `pinned_targets` 字段 docstring
里"锁绑定这一次重排事件"的既定语义）。"点名换店"：如果用户点名的那一个恰好
是锁定节点，不静默执行也不静默跳过——产 `AdvisoryCode.SWAP_TARGET_LOCKED`
如实告知，方案该节点保持不变（锁定优先级高于这一次点名请求，ADR 一贯的
"赞锁定必须保留"语义）。

【换不出的诚实兜底】
`resolve_node_swap` 本身对"降级三级全试完仍无可行替补"/"钉住的其余节点排不
到一块"给出确定性的 `SWAP_NO_ALTERNATIVE_FOUND`/`SWAP_KEPT_NODE_UNFIT`
advisory（该模块既有语义，本节点不重复发明）——本节点只把这些 advisory 原样
收集进返回 diff 的 `advisories`，narrate 既有的诚实告知通道（`_merge_
advisories`/`_extract_advisories`，见 `agent.graph.nodes.narrate`）会自动
把它们拼进叙事，绝不宣称"换了"却其实没换（ADR-0015 诚实红线）。

【为什么不自己拼一句"换菜确认"文案】
单节点换菜的 HTTP 旁路（`api/_streams/graph_adjust.py`）自己拼确认句，是
因为那条路径**不经过图**、没有 `narrate` 节点帮它把新方案讲一遍。本节点身处
图内，紧接着的既有边是 `store_swap → finalize_plan → narrate`（与
intent/refiner 后接 execute→planner→…→finalize_plan→narrate 的既有拓扑同一
终点）——`narrate_node` 会像描述任何一版新方案一样，按当前 itinerary.nodes
真实内容逐站复述（新换的店名自然出现在文案里），`finalize_plan_node` 既有的
`_plan_recap_clause` 机制（读 `plan_version_log` 最后一条 trigger=="feedback"
的原话）会自动带出"这版是照你『换个店铺』的反馈调过的"这句确定性回顾——两者
组合已经如实传达"这版因换店请求而生 + 现在长什么样"，不需要在本节点重复
拼一份文案（不新写第二套叙事逻辑，复用既有 narrate 管线）。

【为什么整段用 reset_for_new_episode() 打底】
换店本质上和 intent_node/refiner_node 一样是"新一次规划事件"（方案确实变了
一版）——沿用两者共用的 `agent.graph.state.reset_for_new_episode()` diff
铺底，能一次性避免"上一版事件遗留的 critic_attempts/violations/quality_
issues/fallback_chain 等 EPISODE_SCOPED 字段静默漏进这一版"这一整类问题
（`narrate_node` 会读 `critic_attempts`/`violations`/`quality_issues` 拼文案，
`finalize_plan_node` 的 decision_trace 兜底分支会读 `fallback_chain`——留着
不清，narrate 可能把上一版的 critic 修正历史/软违规错按到这一版头上）。铺底
之后覆盖本节点真正要保留/产出的字段：`intent`（不变，换店不重新解析意图）、
`pinned_targets`（不变，保留赞锁定，reset 的空值只是给"没有生产者"的单人
路径兜底）、`itinerary`（换店结果）、`advisories`（本次换店的诚实告知）、
`swapped_out_entity_ids`（SESSION_SCOPED，不在 reset 的 key 集合里，单独
累加）。

不负责：
- "这句话是不是换店/点名了谁"的判定——`agent.routing.store_swap_router`。
- 局部重解算法本体（降级序列/候选打分/`repair_route`）——`node_swap.py`。
- 方案定稿收尾（规则标题/pending_actions/版本志/出口审计）——图既有的
  `finalize_plan_node`，本节点产出的 itinerary/advisories 原样喂给它，不
  重复实现。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.graph.state import AgentState, reset_for_new_episode
from agent.planning.planners.node_swap import SwapResult, resolve_node_swap
from agent.planning.planners.node_swap_support import node_title
from agent.routing.store_swap_router import classify_store_swap
from data.loader import load_pois, load_restaurants
from schemas.advisory import Advisory, AdvisoryCode

logger = logging.getLogger(__name__)


def _locked_ids(pinned_targets: list[dict]) -> set[str]:
    return {t.get("target_id") for t in (pinned_targets or []) if t.get("target_id")}


def _swappable_target_ids(itinerary: Any, locked_ids: set[str]) -> list[str]:
    """"泛化换店"的目标清单：全部非 home、非锁定节点，按方案原有顺序。"""
    return [
        n.target_id
        for n in itinerary.nodes
        if n.target_kind != "home" and n.target_id not in locked_ids
    ]


def _swap_target_locked_advisory(label: str) -> Advisory:
    return Advisory(
        code=AdvisoryCode.SWAP_TARGET_LOCKED,
        message=(
            f"「{label}」是之前定下来必须保留的一站，没有帮你换——"
            "真要换的话，需要先取消这一站的锁定。"
        ),
    )


def store_swap_node(state: AgentState) -> dict[str, Any]:
    """B2 图节点：`route_after_router` 判定这轮反馈是"换店"时接到这里。"""
    itinerary = state.get("itinerary")
    intent = state.get("intent")
    utterance = state.get("user_input") or ""
    if itinerary is None or intent is None:
        return {}

    classification = classify_store_swap(utterance, itinerary)
    if classification is None:
        # 防御性兜底：route_after_router 用同一份纯函数（同输入必同输出）
        # 判过一次才会路由到本节点，正常不会落到这里；出现只可能是 state
        # 在两次调用之间发生了不该发生的变化——保守地不改动方案，不猜。
        logger.warning(
            "store_swap_node: classify_store_swap 复判返回 None，原样透传不改动"
        )
        return {}

    pois = load_pois()
    restaurants = load_restaurants()
    locked_ids = _locked_ids(state.get("pinned_targets") or [])
    cumulative_excluded: set[str] = set(state.get("swapped_out_entity_ids") or [])

    advisories: list[Advisory] = []
    if classification.mode == "named":
        named_id = classification.target_node_id
        if named_id in locked_ids:
            advisories.append(_swap_target_locked_advisory(node_title(itinerary, named_id)))
            target_ids: list[str] = []
        else:
            target_ids = [named_id]
    else:
        target_ids = _swappable_target_ids(itinerary, locked_ids)

    current_itinerary = itinerary
    newly_excluded: set[str] = set()

    for target_id in target_ids:
        blacklist = cumulative_excluded | newly_excluded
        call_pois = [p for p in pois if p.id not in blacklist]
        call_rests = [r for r in restaurants if r.id not in blacklist]
        result: SwapResult = resolve_node_swap(
            current_itinerary,
            intent,
            call_pois,
            call_rests,
            target_node_id=target_id,
            adjustment=None,
        )
        advisories.extend(result.advisories)
        if result.success:
            newly_excluded.add(target_id)
            current_itinerary = result.new_itinerary

    updated_excluded = sorted(cumulative_excluded | newly_excluded)

    return {
        **reset_for_new_episode(),
        "intent": intent,
        "pinned_targets": state.get("pinned_targets") or [],
        "itinerary": current_itinerary,
        "advisories": [a.model_dump() for a in advisories],
        "swapped_out_entity_ids": updated_excluded,
    }


__all__ = ["store_swap_node"]
