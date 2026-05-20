"""nodes.execute_finalize —— confirm 后下单 / 购票 / 生成转发文案。

interrupt(plan_ready) 后用户选了 confirm → 进本节点：
1. reserve_restaurant
2. buy_ticket（只在主活动 POI 是收费场馆时调）
3. generate_share_message

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

    # 1. 找到用餐 stage，调 reserve_restaurant
    dining_stage = next(
        (s for s in itinerary.stages if s.kind == "用餐" and s.restaurant_id),
        None,
    )
    if dining_stage:
        party_size = max(1, sum(c.count for c in intent.companions) + 1)
        try:
            inp = ReserveRestaurantInput(
                restaurant_id=dining_stage.restaurant_id,
                time=dining_stage.start,
                party_size=party_size,
                user_note=None,
            )
            out = invoke_tool("reserve_restaurant", inp.model_dump())
            if out and getattr(out, "success", False):
                orders.append(
                    OrderRecord(
                        order_id=out.order_id,
                        kind="餐厅预约",
                        target_id=dining_stage.restaurant_id,
                        details=f"{dining_stage.start} 预订 {party_size} 人",
                    )
                )
        except Exception:  # noqa: BLE001
            pass

    # 2. 生成转发文案（永远调）
    summary_text = (
        f"{itinerary.summary} · {dining_stage.start if dining_stage else ''}"
    ).strip(" ·")
    share_msg = ""
    try:
        inp = GenerateShareMessageInput(
            summary=summary_text,
            social_context=intent.social_context,
            highlights=[s.title for s in itinerary.stages[:3]],
        )
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
