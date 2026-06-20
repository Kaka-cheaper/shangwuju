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
from schemas.itinerary import ActivityNode, OrderRecord, PendingAction
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


# ============================================================
# 工具前移：规划期生成「确认动作清单」（spec dialogue-act-routing）
# ============================================================

def build_confirm_actions(itinerary: Any, intent: Any) -> list[PendingAction]:
    """把 confirm 要调的工具 + 参数全算好，生成一份可直接 replay 的清单。

    plan-and-execute 分离：规划期调本函数（intent 在手，能算全 party_size / 加购 / 场景），
    confirm 期直接 replay 返回的清单，不再读 intent。纯规则、不调 LLM。
    intent 缺省（如 ReAct 路径 intent 没落库）时降级：人数取默认、跳过加购，餐厅/门票/文案照出。
    """
    restaurant_nodes = [n for n in itinerary.nodes if n.target_kind == "restaurant"]
    poi_nodes = [n for n in itinerary.nodes if n.target_kind == "poi"]
    party_size = _party_size(intent) if intent is not None else 2
    social_context = getattr(intent, "social_context", None) or "家庭日常"

    actions: list[PendingAction] = []

    # 1. 餐厅预约：全量遍历用餐节点
    for n in restaurant_nodes:
        actions.append(
            PendingAction(
                tool="reserve_restaurant",
                args=ReserveRestaurantInput(
                    restaurant_id=n.target_id,
                    time=_reservation_time(n),
                    party_size=party_size,
                    extra_notes=None,
                ).model_dump(),
                label=n.title,
            )
        )

    # 2. 门票：每个 POI 一张清单项
    for n in poi_nodes:
        actions.append(
            PendingAction(
                tool="buy_ticket",
                args=BuyTicketInput(poi_id=n.target_id, quantity=party_size).model_dump(),
                label=n.title,
            )
        )

    # 3. 附加服务：按 intent.extra_services（intent 缺省时无加购），挂靠首个餐厅
    service_target = (
        restaurant_nodes[0] if restaurant_nodes else (poi_nodes[0] if poi_nodes else None)
    )
    if service_target is not None and intent is not None:
        for service_type in _extra_services(intent):
            actions.append(
                PendingAction(
                    tool="order_extra_service",
                    args=OrderExtraServiceInput(
                        service_type=service_type,
                        target_kind=service_target.target_kind,
                        target_id=service_target.target_id,
                        quantity=1,
                        scheduled_time=_service_time(service_target),
                        recipient_note=f"{social_context}场景",
                    ).model_dump(),
                    label=service_target.title,
                )
            )

    # 4. 转发文案：永远生成
    actions.append(
        PendingAction(
            tool="generate_share_message",
            args=GenerateShareMessageInput(
                itinerary_summary=_summary_for_share(itinerary, restaurant_nodes),
                social_context=social_context,
                audience=None,
            ).model_dump(),
            label="转发文案",
        )
    )
    return actions


def execute_finalize_node(state: AgentState) -> dict[str, Any]:
    itinerary = state.get("itinerary")
    if itinerary is None:
        return {}
    intent = state.get("intent")  # 不再强依赖：有 pending_actions 时根本用不到（拆掉 ReAct 断点）

    # 工具前移：优先 replay 规划期挂好的清单；没有（旧方案/兼容）才现算（此分支仍需 intent）。
    actions = list(getattr(itinerary, "pending_actions", None) or [])
    if not actions:
        actions = build_confirm_actions(itinerary, intent)

    orders, execution_tool_results, share_msg = replay_confirm_actions(actions)

    # confirm 阶段 narrator（intent 缺省时降级为空，不挡执行类 Tool 返回）
    defer_post_confirm_effects = bool(state.get("defer_post_confirm_effects"))
    confirm_narration = (
        _generate_confirm_narration(intent, itinerary, use_llm=not defer_post_confirm_effects)
        if intent is not None
        else ""
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
    elif intent is not None:
        _persist_memory_side_effect(state, intent, new_itin, out_state)

    return out_state


def replay_confirm_actions(
    actions: list[PendingAction],
) -> tuple[list[OrderRecord], list[dict[str, Any]], str]:
    """忠实 replay 动作清单：逐条 invoke_tool，按 tool 把输出拼成 OrderRecord / 文案。

    不读 intent、不重新决策——动作和参数都是规划期定死的（执行与所见一致 + 锁死目标防编造）。
    """
    orders: list[OrderRecord] = []
    sink: list[dict[str, Any]] = []
    share_msg = "下午行程已搞定，各位准时呀。"

    for action in actions:
        args = dict(action.args)
        try:
            result = _invoke_execution_tool(action.tool, args, sink)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).debug(
                "replay step failed: %s", action.tool, exc_info=True
            )
            continue
        if not result.success:
            continue

        if action.tool == "reserve_restaurant":
            out = ReserveRestaurantOutput.model_validate(result.output)
            if out.success and out.order_id:
                orders.append(
                    OrderRecord(
                        order_id=out.order_id,
                        kind="餐厅预约",
                        target_kind="restaurant",
                        target_id=args.get("restaurant_id", ""),
                        target_name=action.label,
                        detail=(
                            f"{out.confirmed_time or args.get('time')} 预订 "
                            f"{out.confirmed_party_size or args.get('party_size')} 人"
                        ),
                    )
                )
        elif action.tool == "buy_ticket":
            out = BuyTicketOutput.model_validate(result.output)
            if out.success and out.order_id:
                orders.append(
                    OrderRecord(
                        order_id=out.order_id,
                        kind="门票",
                        target_kind="poi",
                        target_id=args.get("poi_id", ""),
                        target_name=action.label,
                        detail=f"{out.quantity or args.get('quantity')} 张 / 总价 {out.total_price or 0} 元",
                    )
                )
        elif action.tool == "order_extra_service":
            out = OrderExtraServiceOutput.model_validate(result.output)
            if out.success and out.order_id:
                service_name = out.service.name if out.service else out.service_type
                time_text = f"{out.scheduled_time} 送达 / " if out.scheduled_time else ""
                orders.append(
                    OrderRecord(
                        order_id=out.order_id,
                        kind=f"{out.service_type}加购",
                        target_kind=args.get("target_kind", "restaurant"),
                        target_id=args.get("target_id", ""),
                        target_name=action.label,
                        detail=(
                            f"{time_text}{service_name} x{out.quantity or 1}"
                            f" / 总价 {out.total_price or 0} 元"
                        ),
                    )
                )
        elif action.tool == "generate_share_message":
            out = GenerateShareMessageOutput.model_validate(result.output)
            if out.success and out.message:
                share_msg = out.message

    return orders, sink, share_msg


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
