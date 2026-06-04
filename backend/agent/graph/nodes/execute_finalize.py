"""nodes.execute_finalize -- confirm 后下单 / 购票 / 加购 / 生成转发文案。

职责：
- 用户确认方案后，调用执行类 Tool，把订单写回 Itinerary.orders。
- 生成 share_message 与 confirm 阶段 narration。
- 在确认成功后触发 memory_writer，把本次场景偏好写回记忆。

不负责：
- 规划与重规划。
- 修改 mock_data 库存或预约状态。
- Graph 拓扑变更。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agent.core.llm_client import get_llm_client
from agent.graph.state import AgentState
from agent.intent.narrator import generate_narration
from schemas.itinerary import ActivityNode, OrderRecord
from schemas.tools import (
    BuyTicketInput,
    BuyTicketOutput,
    GenerateShareMessageInput,
    GenerateShareMessageOutput,
    OrderExtraServiceInput,
    OrderExtraServiceOutput,
    ReserveRestaurantInput,
    ReserveRestaurantOutput,
)
from tools.registry import ToolInvocationResult, invoke_tool


def execute_finalize_node(state: AgentState) -> dict[str, Any]:
    itinerary = state.get("itinerary")
    intent = state.get("intent")

    if itinerary is None or intent is None:
        return {}

    orders: list[OrderRecord] = []
    execution_tool_results: list[dict[str, Any]] = []
    party_size = _party_size(intent)

    restaurant_nodes = [
        n for n in itinerary.nodes if n.target_kind == "restaurant"
    ]
    poi_nodes = [n for n in itinerary.nodes if n.target_kind == "poi"]

    # 1. 餐厅预约：全量遍历所有用餐节点。
    for restaurant_node in restaurant_nodes:
        try:
            inp = ReserveRestaurantInput(
                restaurant_id=restaurant_node.target_id,
                time=_reservation_time(restaurant_node),
                party_size=party_size,
                extra_notes=None,
            )
            result = _invoke_execution_tool(
                "reserve_restaurant", inp.model_dump(), execution_tool_results
            )
            if result.success:
                out = ReserveRestaurantOutput.model_validate(result.output)
                if out.success and out.order_id:
                    confirmed_time = out.confirmed_time or inp.time
                    confirmed_party_size = out.confirmed_party_size or party_size
                    orders.append(
                        OrderRecord(
                            order_id=out.order_id,
                            kind="餐厅预约",
                            target_kind="restaurant",
                            target_id=restaurant_node.target_id,
                            target_name=restaurant_node.title,
                            detail=(
                                f"{confirmed_time} 预订 {confirmed_party_size} 人"
                            ),
                        )
                    )
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).debug(
                "reserve_restaurant finalize step failed", exc_info=True
            )

    # 2. 门票：对每个 POI 尝试购票；售罄 / 免票不阻断确认流。
    for poi_node in poi_nodes:
        try:
            inp = BuyTicketInput(poi_id=poi_node.target_id, quantity=party_size)
            result = _invoke_execution_tool(
                "buy_ticket", inp.model_dump(), execution_tool_results
            )
            if result.success:
                out = BuyTicketOutput.model_validate(result.output)
                if out.success and out.order_id:
                    orders.append(
                        OrderRecord(
                            order_id=out.order_id,
                            kind="门票",
                            target_kind="poi",
                            target_id=poi_node.target_id,
                            target_name=poi_node.title,
                            detail=(
                                f"{out.quantity or party_size} 张 / "
                                f"总价 {out.total_price or 0} 元"
                            ),
                        )
                    )
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).debug(
                "buy_ticket finalize step failed", exc_info=True
            )

    # 3. 附加服务：按 intent.extra_services 下单，优先挂靠首个餐厅。
    service_target = (
        restaurant_nodes[0]
        if restaurant_nodes
        else (poi_nodes[0] if poi_nodes else None)
    )
    if service_target is not None:
        for service_type in _extra_services(intent):
            try:
                inp = OrderExtraServiceInput(
                    service_type=service_type,
                    target_kind=service_target.target_kind,
                    target_id=service_target.target_id,
                    quantity=1,
                    scheduled_time=_service_time(service_target),
                    recipient_note=f"{intent.social_context}场景",
                )
                result = _invoke_execution_tool(
                    "order_extra_service", inp.model_dump(), execution_tool_results
                )
                if result.success:
                    out = OrderExtraServiceOutput.model_validate(result.output)
                    if out.success and out.order_id:
                        service_name = out.service.name if out.service else service_type
                        time_text = (
                            f"{out.scheduled_time} 送达 / "
                            if out.scheduled_time
                            else ""
                        )
                        orders.append(
                            OrderRecord(
                                order_id=out.order_id,
                                kind=f"{out.service_type}加购",
                                target_kind=service_target.target_kind,
                                target_id=service_target.target_id,
                                target_name=service_target.title,
                                detail=(
                                    f"{time_text}{service_name} x{out.quantity or 1}"
                                    f" / 总价 {out.total_price or 0} 元"
                                ),
                            )
                        )
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).debug(
                    "order_extra_service finalize step failed", exc_info=True
                )

    # 4. 转发文案：永远尝试生成。
    share_msg = _generate_share_message(
        itinerary_summary=_summary_for_share(itinerary, restaurant_nodes),
        social_context=intent.social_context,
        execution_tool_results=execution_tool_results,
    )

    # 5. confirm 阶段 narrator。真实 /chat/confirm 会把 LLM 文案和 memory_writer
    # 放到预约成功之后，因此这里用模板文案避免挡住执行类 Tool 的返回。
    defer_post_confirm_effects = bool(state.get("defer_post_confirm_effects"))
    confirm_narration = _generate_confirm_narration(
        intent,
        itinerary,
        use_llm=not defer_post_confirm_effects,
    )

    new_itin = itinerary.model_copy(
        update={"orders": orders, "share_message": share_msg}
    )

    out_state: dict[str, Any] = {
        "itinerary": new_itin,
        "orders": orders,
        "share_message": share_msg,
        "execution_tool_results": execution_tool_results,
    }
    if confirm_narration:
        out_state["narration"] = confirm_narration

    if defer_post_confirm_effects:
        out_state["post_confirm_effects_deferred"] = True
    else:
        _persist_memory_side_effect(state, intent, new_itin, out_state)

    return out_state


def _invoke_execution_tool(
    tool: str,
    args: dict[str, Any],
    sink: list[dict[str, Any]],
) -> ToolInvocationResult:
    result = invoke_tool(tool, args)
    sink.append(_tool_result_event(tool, args, result))
    return result


def _tool_result_event(
    tool: str,
    input_: dict[str, Any],
    result: ToolInvocationResult,
) -> dict[str, Any]:
    output = dict(result.output or {})
    reason = output.get("reason")
    if hasattr(reason, "value"):
        output["reason"] = reason.value
    return {
        "tool": tool,
        "input": input_,
        "output": output,
        "success": bool(result.success),
        "reason": result.reason.value if result.reason else None,
        "duration_ms": result.duration_ms,
    }


def _party_size(intent: Any) -> int:
    companion_count = sum(getattr(c, "count", 0) or 0 for c in intent.companions)
    capacity_requirement = getattr(intent, "capacity_requirement", None) or 0
    return max(1, companion_count + 1, int(capacity_requirement))


def _extra_services(intent: Any) -> list[str]:
    return [
        str(x).strip()
        for x in (getattr(intent, "extra_services", None) or [])
        if str(x).strip()
    ]


def _summary_for_share(itinerary: Any, restaurant_nodes: list[ActivityNode]) -> str:
    first_restaurant = restaurant_nodes[0] if restaurant_nodes else None
    return (
        f"{itinerary.summary} · "
        f"{first_restaurant.start_time if first_restaurant else ''}"
    ).strip(" ·")


def _generate_share_message(
    *,
    itinerary_summary: str,
    social_context: str,
    execution_tool_results: list[dict[str, Any]],
) -> str:
    try:
        inp = GenerateShareMessageInput(
            itinerary_summary=itinerary_summary,
            social_context=social_context,
            audience=None,
        )
        result = _invoke_execution_tool(
            "generate_share_message", inp.model_dump(), execution_tool_results
        )
        if result.success:
            out = GenerateShareMessageOutput.model_validate(result.output)
            if out.success and out.message:
                return out.message
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).debug(
            "generate_share_message finalize step failed", exc_info=True
        )
    return "下午行程已搞定，各位准时呀。"


def _generate_confirm_narration(
    intent: Any,
    itinerary: Any,
    *,
    use_llm: bool | None = None,
) -> str:
    try:
        if use_llm is None:
            client = get_llm_client()
            use_llm = client is not None and getattr(client, "provider", None) != "stub"
        return generate_narration(
            intent=intent,
            itinerary=itinerary,
            stage="confirm",
            use_llm=bool(use_llm),
        )
    except Exception:  # noqa: BLE001
        return ""


def _persist_memory_side_effect(
    state: AgentState,
    intent: Any,
    new_itin: Any,
    out_state: dict[str, Any],
) -> None:
    try:
        from agent.planning.memory_writer import persist_memory

        finalize_state = dict(state)
        finalize_state.setdefault("user_decision", "confirm")
        finalize_state["itinerary"] = new_itin

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
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).debug(
            "execute_finalize_node: persist_memory side-effect failed: %s", exc
        )


def _service_time(node: ActivityNode) -> str:
    if node.target_kind == "restaurant":
        return _reservation_time(node)
    return node.start_time


def _reservation_time(node: ActivityNode) -> str:
    return _extract_reserved_time(node.note) or _ceil_to_half_hour(node.start_time)


def _extract_reserved_time(note: str | None) -> str | None:
    if not note:
        return None
    m = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", note)
    return m.group(0) if m else None


def _ceil_to_half_hour(value: str) -> str:
    try:
        hour_s, minute_s = value.split(":", 1)
        hour = int(hour_s)
        minute = int(minute_s[:2])
    except Exception:
        return value
    if minute == 0 or minute == 30:
        return f"{hour:02d}:{minute:02d}"
    if minute < 30:
        return f"{hour:02d}:30"
    return f"{min(hour + 1, 23):02d}:00"
