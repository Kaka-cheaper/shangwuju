"""nodes.execute_finalize —— confirm 后下单 / 购票 / 生成转发文案（edge_v1）。

interrupt(plan_ready) 后用户选了 confirm → 进本节点：
1. reserve_restaurant（仅当 itinerary.nodes 含 target_kind="restaurant" 节点时）
2. buy_ticket（v1 留口子；当前未启用）
3. generate_share_message

【字段路径变更（Wave 5）】

旧 stages 模型：找用餐段是 `next(s for s in itinerary.stages if s.kind=="用餐" and s.restaurant_id)`，
然后取 `s.restaurant_id` / `s.start`。

新 edge_v1 模型：用餐节点是 `target_kind=="restaurant"` 的 ActivityNode；
取 `n.target_id` / `n.start_time`。`OrderRecord` 字段名也变了（`target_kind` 必填、
明细字段叫 `detail` 不再叫 `details`、新增 `target_name`）。
`ReserveRestaurantInput` 的备注字段叫 `extra_notes`（不再是 `user_note`）。

输入：state["intent"] / state["itinerary"]
输出：state["itinerary"] 含 orders + share_message
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
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

    # 1. 找到用餐节点（target_kind="restaurant"），调 reserve_restaurant
    #    edge_v1：用餐是「在某餐厅停留 N 分钟」的 node，不再是「用餐 stage」。
    restaurant_node = next(
        (n for n in itinerary.nodes if n.target_kind == "restaurant"),
        None,
    )
    if restaurant_node is not None:
        party_size = max(1, sum(c.count for c in intent.companions) + 1)
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
            pass

    # 2. 生成转发文案（永远调）
    summary_text = (
        f"{itinerary.summary} · "
        f"{restaurant_node.start_time if restaurant_node else ''}"
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

    # 写回 itinerary（含 orders + share_message）
    new_itin = itinerary.model_copy(
        update={
            "orders": orders,
            "share_message": share_msg,
        }
    )
    return {"itinerary": new_itin, "orders": orders, "share_message": share_msg}
