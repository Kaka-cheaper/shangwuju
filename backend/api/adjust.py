"""api.adjust —— POST /chat/adjust：单人节点调整入口（ADR-0013 F-4）。

把 F-1 局部重解引擎 + F-2 诉求台账 + F-3 按钮/备选生成串成用户可点的最后
一环：行程卡片节点行的「定向调整按钮 / 具名备选」点击（单人模式；「点踩」
协议同款支持，但单人 UI 暂不发出，见 `api/_streams/models.py::
AdjustActionDislike`）。房间侧接线是 F-5，本文件不做。

对齐 `api/chat.py` 现有端点风格：本文件只做「读图状态做前置校验 + 建 SSE
流」两件事——SSE 流的完整实现细节在 `api/_streams/graph_adjust.py`（同
`chat.py` 与 `api/_streams/graph_confirm.py` 的分工：端点文件薄，实现细节
挪进 `_streams` 子包）。

前置校验（同步 HTTP 4xx，不流到一半才报错——同 `chat_turn` 对
`langgraph_unavailable` 的既有处理）：session 没有图 checkpoint，或图状态里
没有 itinerary/intent（还没跑过一次 `/chat/turn` 出方案）→ 直接 404，人话
`detail`。`resolve_node_swap` 自身对"node_id 不存在"这类调用方契约违反抛
`ValueError`——那个发生在 SSE 流已经打开之后，交给 `safe_stream` 兜底转
`stream_error`（与本文件的同步前置校验是两个不同阶段，见
`api/_streams/graph_adjust.py` 模块 docstring「业务性失败 vs 契约违反」）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from ._sse_helpers import safe_stream
from ._streams.graph_adjust import _graph_adjust
from ._streams.models import ChatAdjustRequest

router = APIRouter()


@router.post(
    "/chat/adjust",
    tags=["小团接入"],
    summary="单人节点调整：定向调整按钮 / 具名备选二选一，直接换菜不经过 LLM 路由",
)
async def chat_adjust(req: ChatAdjustRequest) -> EventSourceResponse:
    """SSE 序列：

        agent_thought（开工提示）
        → 成功：itinerary_ready（新方案，纯 Itinerary dump） + agent_narration
          （text=换菜说明，node_actions=重算的按钮/备选，messages=advisory 条目，
          demand_ledger=台账展示投影）
        → 业务性失败（无可换候选 / 保留节点排不到一块儿）：agent_narration
          （只带告知文案，方案不动）
        → done

    `resolve_node_swap` 抛 `ValueError`（node_id 不存在等调用方契约违反）会被
    `safe_stream` 兜底转成 `stream_error` + `done`，不是本端点的 4xx 范畴。
    """
    from agent.graph.build import get_compiled_graph

    try:
        graph = get_compiled_graph()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"langgraph_unavailable: {type(e).__name__}: {e}",
        )

    config = {"configurable": {"thread_id": req.session_id}}
    snapshot = await graph.aget_state(config)
    state = snapshot.values if snapshot else {}
    if not state or state.get("itinerary") is None or state.get("intent") is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"session_id={req.session_id} 还没有可调整的方案，"
                "请先完成一次规划（POST /chat/turn）再调整节点"
            ),
        )

    inner = _graph_adjust(req, graph=graph, config=config, state=state)
    return EventSourceResponse(safe_stream(inner), media_type="text/event-stream")
