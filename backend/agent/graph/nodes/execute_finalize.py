"""nodes.execute_finalize —— confirm 后下单 / 购票 / 生成转发文案（edge_v1）。

interrupt(plan_ready) 后用户选了 confirm → 进本节点：
1. reserve_restaurant（**全量遍历**所有 target_kind="restaurant" 节点；spec R6 修复）
2. buy_ticket（v1 留口子；当前未启用）
3. generate_share_message
4. confirm 阶段 narrator（spec R6：把"已下单"的最终文案推给前端）

【字段路径变更（Wave 5）】

旧 stages 模型：找用餐段是 `next(s for s in itinerary.stages if s.kind=="用餐" and s.restaurant_id)`，
然后取 `s.restaurant_id` / `s.start`。

新 edge_v1 模型：用餐节点是 `target_kind=="restaurant"` 的 ActivityNode；
取 `n.target_id` / `n.start_time`。`OrderRecord` 字段名也变了（`target_kind` 必填、
明细字段叫 `detail` 不再叫 `details`、新增 `target_name`）。
`ReserveRestaurantInput` 的备注字段叫 `extra_notes`（不再是 `user_note`）。

【spec planning-quality-deep-review R6+R7（Task 6）】
- 旧实现用 `next((n for n in itinerary.nodes if n.target_kind=="restaurant"), None)`
  只取第一个餐厅节点，如果方案里有下午茶 + 晚餐两段都需预约会漏掉第二段；
  改为 `[n for n in nodes if n.target_kind=="restaurant"]` 全量遍历，每段都试预约。
- 新增 confirm 阶段 narrator 调用：`generate_narration(stage="confirm")`，
  把"都搞定了，可以放心了"的安抚文案写进 itinerary.narration 字段，让前端
  ITINERARY_READY 推流时可一次性带出。

输入：state["intent"] / state["itinerary"]
输出：state["itinerary"] 含 orders + share_message + narration（confirm 文案）
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.core.llm_client import get_llm_client
from agent.intent.narrator import generate_narration
from schemas.itinerary import OrderRecord
from schemas.tools import (
    GenerateShareMessageInput,
    ReserveRestaurantInput,
)
from tools.registry import invoke_tool


def execute_finalize_node(state: AgentState) -> dict[str, Any]:
    itinerary = state.get("itinerary")
    intent = state.get("intent")

    if itinerary is None or intent is None:
        return {}

    orders: list[OrderRecord] = []

    # 1. 找到**所有**用餐节点（target_kind="restaurant"）→ 每段都试预约
    #    edge_v1：用餐是「在某餐厅停留 N 分钟」的 node，不再是「用餐 stage」。
    #    spec R6 修复：旧 next(...) 只取首段，下午茶 + 晚餐组合会漏掉第二段。
    restaurant_nodes = [
        n for n in itinerary.nodes if n.target_kind == "restaurant"
    ]
    party_size = max(1, sum(c.count for c in intent.companions) + 1)
    for restaurant_node in restaurant_nodes:
        try:
            inp = ReserveRestaurantInput(
                restaurant_id=restaurant_node.target_id,
                time=restaurant_node.start_time,
                party_size=party_size,
                extra_notes=None,
            )
            out = invoke_tool("reserve_restaurant", inp.model_dump())
            if out and getattr(out, "success", False):
                orders.append(
                    OrderRecord(
                        order_id=out.order_id,
                        kind="餐厅预约",
                        target_kind="restaurant",
                        target_id=restaurant_node.target_id,
                        target_name=restaurant_node.title,
                        detail=f"{restaurant_node.start_time} 预订 {party_size} 人",
                    )
                )
        except Exception:  # noqa: BLE001
            # 单段预约失败不影响其他段（demo 韧性）
            continue

    # 2. 生成转发文案（永远调）
    first_restaurant = restaurant_nodes[0] if restaurant_nodes else None
    summary_text = (
        f"{itinerary.summary} · "
        f"{first_restaurant.start_time if first_restaurant else ''}"
    ).strip(" ·")
    share_msg = ""
    try:
        # 取前 3 个非 home 节点的 title 作为亮点
        highlights = [
            n.title
            for n in itinerary.nodes
            if n.target_kind != "home"
        ][:3]
        inp = GenerateShareMessageInput(
            itinerary_summary=summary_text,
            social_context=intent.social_context,
            audience=None,
        )
        # highlights 当前 GenerateShareMessageInput 不直接接受；
        # tool 内部会基于 itinerary_summary 自行衍生文案。保留变量以便后续接入。
        _ = highlights
        out = invoke_tool("generate_share_message", inp.model_dump())
        if out and getattr(out, "success", False):
            share_msg = out.message
    except Exception:  # noqa: BLE001
        share_msg = "下午行程已搞定，各位准时呀。"

    # 3. spec R6：confirm 阶段 narrator → 「都搞定了」的安抚文案
    confirm_narration = ""
    try:
        client = get_llm_client()
        use_llm = (
            client is not None and getattr(client, "provider", None) != "stub"
        )
        confirm_narration = generate_narration(
            intent=intent,
            itinerary=itinerary,
            stage="confirm",
            use_llm=use_llm,
        )
    except Exception:  # noqa: BLE001
        confirm_narration = ""

    # 写回 itinerary（含 orders + share_message；narration 字段如有则更新）
    update_kwargs: dict[str, Any] = {
        "orders": orders,
        "share_message": share_msg,
    }
    new_itin = itinerary.model_copy(update=update_kwargs)

    out_state: dict[str, Any] = {
        "itinerary": new_itin,
        "orders": orders,
        "share_message": share_msg,
    }
    if confirm_narration:
        out_state["narration"] = confirm_narration

    # spec algorithm-redesign R5（迁移自 narrate_node，2026-05-25 用户反馈）
    # 「已记住此次场景偏好」应该是用户**确认预约**后才记住——不是方案就绪就记住。
    # 路径 B（design.md §Component 4 决策点 4）：不动 graph 拓扑（spec B 锁的编排冻结纪律），
    # 只把副作用挂位从 narrate（方案就绪）迁到 execute_finalize（用户确认下单后），与 memory_writer
    # 的 success=bool(user_decision == "confirm") 语义对齐。
    # 失败时 try/except 吞掉异常，不阻断 finalize 主输出。
    try:
        from agent.planning.memory_writer import persist_memory

        # finalize 节点的 state 中 user_decision 一定是 "confirm"（cancel/refine 不进本节点）
        # 但仍把 user_decision 显式补一下，避免 memory_writer 内部因字段缺失误判
        finalize_state = dict(state)
        finalize_state.setdefault("user_decision", "confirm")
        finalize_state["itinerary"] = new_itin  # 用更新后含 orders 的 itinerary

        memory_client = get_llm_client()
        ok = persist_memory(finalize_state, client=memory_client)
        social_ctx = getattr(intent, "social_context", "") or ""
        try:
            mid_kinds = [
                ("活动" if n.target_kind == "poi" else "用餐")
                for n in new_itin.nodes
                if n.target_kind in ("poi", "restaurant")
            ]
            summary_preview = (
                f"{social_ctx}场景 · " + " → ".join(mid_kinds)
                if mid_kinds
                else f"{social_ctx}场景"
            )
        except Exception:
            summary_preview = social_ctx
        out_state["memory_status"] = {
            "social_context": social_ctx,
            "summary_preview": summary_preview[:80],
            "success": bool(ok),
            "skipped_reason": None if ok else "duplicate_within_5min",
        }
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug(
            "execute_finalize_node: persist_memory side-effect failed: %s", exc
        )

    return out_state
