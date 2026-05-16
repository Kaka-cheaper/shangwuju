"""agent.executor —— 用户确认后下发执行类 Tool。

职责：
- 接 Itinerary（来自 planner）+ 用户确认信号
- 调 reserve_restaurant / buy_ticket / generate_share_message
- 把订单号回填到 Itinerary.orders
- 处理执行类 Tool 的失败（E2 门票售罄等）

设计取舍：
- MVP-1：planner 直接产出方案后立刻调 reserve（"一气呵成"）
- MVP-2：在 planner 输出后等用户确认才调 executor（D5 决议）
- 本模块对两种模式都适配：execute(itinerary) 是无状态的纯函数

不负责：
- 规划循环（在 planner.py）
- LLM 调用（仅 generate_share_message Tool 内部用，不在本模块直接调）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemas.errors import FailureReason
from schemas.itinerary import Itinerary, OrderRecord
from schemas.tags import SocialContext
from schemas.tools import (
    BuyTicketInput,
    BuyTicketOutput,
    GenerateShareMessageInput,
    GenerateShareMessageOutput,
    ReserveRestaurantInput,
    ReserveRestaurantOutput,
)

from .trace import Tracer
from tools.registry import ToolInvocationResult, invoke_tool


@dataclass
class ExecutionResult:
    success: bool
    itinerary: Itinerary
    failed_tools: list[tuple[str, FailureReason]]
    tracer: Tracer


def execute_plan(
    itinerary: Itinerary,
    *,
    party_size: int = 1,
    social_context: SocialContext = "家庭日常",
    audience: str | None = None,
    tracer: Tracer | None = None,
    buy_ticket_for_main_poi: bool = False,
) -> ExecutionResult:
    """用户确认后的执行流。

    流程：
    1. 餐厅预约：找出 itinerary 里 kind="用餐" 的 stage，调 reserve_restaurant
    2. 门票（可选）：buy_ticket_for_main_poi=True 时调 buy_ticket
       - E2 售罄 → 不阻塞流程，记 failed_tools
    3. 转发文案：调 generate_share_message
    4. 把订单号回填 itinerary.orders；把文案填 itinerary.share_message
    """
    tracer = tracer or Tracer()
    failed: list[tuple[str, FailureReason]] = []
    orders = list(itinerary.orders)

    # ---- 1. 餐厅预约 ----
    dining_stage = next(
        (s for s in itinerary.stages if s.kind == "用餐" and s.restaurant_id), None
    )
    if dining_stage and dining_stage.restaurant_id:
        confirmed_time = _extract_reserved_time(dining_stage.note) or dining_stage.start
        result = _call(
            tracer,
            "reserve_restaurant",
            ReserveRestaurantInput(
                restaurant_id=dining_stage.restaurant_id,
                time=confirmed_time,
                party_size=party_size,
            ).model_dump(),
        )
        if result.success:
            out = ReserveRestaurantOutput.model_validate(result.output)
            if out.success and out.order_id:
                orders.append(
                    OrderRecord(
                        order_id=out.order_id,
                        kind="餐厅预约",
                        target_id=dining_stage.restaurant_id,
                        target_name=dining_stage.title,
                        detail=f"{out.confirmed_time or confirmed_time}（{out.confirmed_party_size or party_size} 人）",
                    )
                )
            elif out.reason:
                failed.append(("reserve_restaurant", out.reason))
        else:
            failed.append(("reserve_restaurant", result.reason or FailureReason.UPSTREAM_FAILURE))

    # ---- 2. 门票（可选）----
    if buy_ticket_for_main_poi:
        main_stage = next(
            (s for s in itinerary.stages if s.kind == "主活动" and s.poi_id), None
        )
        if main_stage and main_stage.poi_id:
            result = _call(
                tracer,
                "buy_ticket",
                BuyTicketInput(
                    poi_id=main_stage.poi_id, quantity=party_size
                ).model_dump(),
            )
            if result.success:
                out = BuyTicketOutput.model_validate(result.output)
                if out.success and out.order_id:
                    orders.append(
                        OrderRecord(
                            order_id=out.order_id,
                            kind="门票",
                            target_id=main_stage.poi_id,
                            target_name=main_stage.title,
                            detail=f"{out.quantity or party_size} 张 / 总价 {out.total_price or '—'} 元",
                        )
                    )
                elif out.reason:
                    failed.append(("buy_ticket", out.reason))
            else:
                failed.append(("buy_ticket", result.reason or FailureReason.UPSTREAM_FAILURE))

    # ---- 3. 转发文案 ----
    share_message: str | None = itinerary.share_message
    msg_result = _call(
        tracer,
        "generate_share_message",
        GenerateShareMessageInput(
            itinerary_summary=itinerary.summary,
            social_context=social_context,
            audience=audience,
        ).model_dump(),
    )
    if msg_result.success:
        out = GenerateShareMessageOutput.model_validate(msg_result.output)
        if out.success and out.message:
            share_message = out.message
        elif out.reason:
            failed.append(("generate_share_message", out.reason))
    else:
        failed.append(
            ("generate_share_message", msg_result.reason or FailureReason.UPSTREAM_FAILURE)
        )

    new_itinerary = itinerary.model_copy(
        update={"orders": orders, "share_message": share_message}
    )
    tracer.emit("itinerary_ready", payload=new_itinerary.model_dump())

    return ExecutionResult(
        success=not any(reason == FailureReason.RESTAURANT_FULL for _, reason in failed),
        itinerary=new_itinerary,
        failed_tools=failed,
        tracer=tracer,
    )


def _call(tracer: Tracer, tool: str, args: dict[str, Any]) -> ToolInvocationResult:
    tracer.emit("tool_call_start", {"tool": tool, "input": args})
    result = invoke_tool(tool, args)
    tracer.emit(
        "tool_call_end",
        {
            "tool": tool,
            "output": result.output,
            "success": result.success,
            "reason": result.reason.value if result.reason else None,
            "duration_ms": result.duration_ms,
        },
    )
    return result


def _extract_reserved_time(note: str | None) -> str | None:
    """从 note 文本中提取已为你预留的时间。简单实现，匹配 HH:MM。"""
    if not note:
        return None
    import re

    m = re.search(r"\b(\d{1,2}:\d{2})\b", note)
    return m.group(1) if m else None
