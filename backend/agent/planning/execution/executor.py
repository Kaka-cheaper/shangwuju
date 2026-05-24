"""agent.planning.execution.executor —— 执行类活代码（用户确认后下发 reserve / buy / share Tool）。

【与 graph/nodes/execute_finalize 的关键差异】（spec D v3 task 2 实测发现）：

- executor.execute_plan：用 `_extract_reserved_time(restaurant_node.note)` 解析「已为你预留 X」
  类预留时段，**与 mock 时段严格匹配（HH:MM 整 30 分）**
- graph/execute_finalize：直接用 `restaurant_node.start_time`（含通勤后的实际抵达时刻），
  在 mock 严格匹配下失败

两者行为**不等价**——本模块是 rule planner / ils_planner 路径下用户确认的执行入口；
graph 路径下用户确认的执行入口是 graph/nodes/execute_finalize。

【被以下入口消费】

- `tests/test_agent_flow.py`（rule planner 路径主流程 → executor）
- `tests/test_8_scenarios.py`（8 场景 reserve + share）
- `agent/__init__.py` re-export `execute_plan` / `ExecutionResult`

职责：
- 接 Itinerary（来自 planner）+ 用户确认信号
- 调 reserve_restaurant / buy_ticket / generate_share_message
- 把订单号回填到 Itinerary.orders
- 处理执行类 Tool 的失败（E2 门票售罄等）

设计取舍：
- MVP-1：planner 直接产出方案后立刻调 reserve（"一气呵成"）
- MVP-2：在 planner 输出后等用户确认才调 executor（D5 决议）
- 本模块对两种模式都适配：execute(itinerary) 是无状态的纯函数

【edge_v1 字段路径迁移（Wave 5）】

旧 stages 模型按 `kind=="用餐" / kind=="主活动"` 找用餐 / 主活动段，
edge_v1 改为按 `target_kind=="restaurant" / target_kind=="poi"` 找节点：

```
旧：next(s for s in itinerary.stages if s.kind == "用餐" and s.restaurant_id)
新：next(n for n in itinerary.nodes if n.target_kind == "restaurant")
```

OrderRecord 在 edge_v1 加了必填字段 `target_kind: Literal["poi", "restaurant"]`
+ 字段名由 `details` 改为 `detail`（与 schemas/itinerary.py 对齐）。

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

from ...core.trace import Tracer
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

    流程（edge_v1 字段路径）：
    1. 餐厅预约：找出 itinerary.nodes 中 target_kind=="restaurant" 的节点，
       调 reserve_restaurant
    2. 门票（可选）：buy_ticket_for_main_poi=True 时找 target_kind=="poi" 的节点，
       调 buy_ticket
       - E2 售罄 → 不阻塞流程，记 failed_tools
    3. 转发文案：调 generate_share_message
    4. 把订单号回填 itinerary.orders；把文案填 itinerary.share_message
    """
    tracer = tracer or Tracer()
    failed: list[tuple[str, FailureReason]] = []
    orders = list(itinerary.orders)

    # ---- 1. 餐厅预约 ----
    restaurant_node = next(
        (n for n in itinerary.nodes if n.target_kind == "restaurant"), None
    )
    if restaurant_node is not None:
        confirmed_time = (
            _extract_reserved_time(restaurant_node.note) or restaurant_node.start_time
        )
        result = _call(
            tracer,
            "reserve_restaurant",
            ReserveRestaurantInput(
                restaurant_id=restaurant_node.target_id,
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
                        target_kind="restaurant",
                        target_id=restaurant_node.target_id,
                        target_name=restaurant_node.title,
                        detail=f"{out.confirmed_time or confirmed_time}（{out.confirmed_party_size or party_size} 人）",
                    )
                )
            elif out.reason:
                failed.append(("reserve_restaurant", out.reason))
        else:
            failed.append(("reserve_restaurant", result.reason or FailureReason.UPSTREAM_FAILURE))

    # ---- 2. 门票（可选）----
    if buy_ticket_for_main_poi:
        poi_node = next(
            (n for n in itinerary.nodes if n.target_kind == "poi"), None
        )
        if poi_node is not None:
            result = _call(
                tracer,
                "buy_ticket",
                BuyTicketInput(
                    poi_id=poi_node.target_id, quantity=party_size
                ).model_dump(),
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
