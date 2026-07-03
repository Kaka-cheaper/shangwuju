"""多人实时协作：HTTP 创建房间 + WebSocket 房间通信。

设计：见 .kiro/specs/realtime-collaboration-room/design.md。

依赖：
- backend/collab/room.py 提供 RoomManager / Room 业务逻辑
- backend/api/_session_store.SESSION_STORE 取已规划行程作为初始方案
"""

from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from collab import get_room_manager

from ._session_store import SESSION_STORE
from ._streams.models import AdjustAction

router = APIRouter(tags=["协作房间"])

# ADR-0013 F-5：WS "adjust" 消息的 action 字段校验——复用 F-4 单人 `/chat/adjust`
# 同一份判别式 schema（`AdjustActionAdjust`/`AdjustActionAlternative`/
# `AdjustActionDislike`），节点行的定向调整按钮/具名备选/点踩三个入口在协议层
# 就已经殊途同归，不为房间另起一套平行的 action 校验。`TypeAdapter` 而非
# `BaseModel.model_validate`——`AdjustAction` 是判别式 `Union` 类型别名本身，不是
# 一个 `BaseModel` 子类，这是 pydantic v2 校验裸 `Union`/`Annotated` 类型的标准写法。
_ADJUST_ACTION_ADAPTER: TypeAdapter[AdjustAction] = TypeAdapter(AdjustAction)


class CreateRoomRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(..., min_length=1, max_length=64)
    nickname: str = Field(default="发起人", max_length=32)
    # 可选：把当前 session 的行程带入房间作为初始方案
    session_id: Optional[str] = Field(default=None, max_length=128)
    # 可选：前端对话历史（参与者加入时同步显示）
    chat_messages: Optional[list[dict[str, Any]]] = Field(default=None)
    # 可选：前端规划事件历史（参与者加入时回放 ToolTracePanel）
    planning_events: Optional[list[dict[str, Any]]] = Field(default=None)
    # 可选：前端主 store 快照（参与者加入时同步所有展示组件）
    chat_state: Optional[dict[str, Any]] = Field(default=None)


class CreateRoomResponse(BaseModel):
    room_id: str
    share_url: str
    owner_id: str


@router.post("/room/create", summary="创建多人协作房间")
async def create_room(req: CreateRoomRequest, request: Request) -> CreateRoomResponse:
    """创建协作房间。

    如果提供 session_id 且该 session 有已规划的行程，
    会把行程和意图带入房间作为初始方案（参与者加入即可看到）。
    """
    manager = get_room_manager()
    room = manager.create_room(owner_id=req.user_id, nickname=req.nickname)
    room.session_id = req.session_id

    # 如果有现有 session 的行程，带入房间
    # 唯一来源 = SESSION_STORE（ADR-0012 决策 3：曾经的「路径 1」ConversationRepository
    # 已删除——实证 intent_snapshot 全仓无生产写入，那条路径永远读到 None，真实数据
    # 全靠这里）
    if req.session_id and req.session_id in SESSION_STORE:
        cached = SESSION_STORE[req.session_id]
        room.current_intent_dict = cached.get("intent")
        room.current_itinerary_dict = cached.get("itinerary")
        # 带入规划事件历史（新成员加入时回放 ToolTracePanel）
        planning_events = cached.get("planning_events")
        if planning_events:
            room.planning_events_history = list(planning_events)

    # 带入对话历史（前端传入）
    if req.chat_messages:
        room.chat_messages = list(req.chat_messages)
    if req.chat_state:
        room.chat_state_snapshot = dict(req.chat_state)
        room.current_itinerary_dict = room.current_itinerary_dict or req.chat_state.get("itinerary")
        room.current_intent_dict = room.current_intent_dict or req.chat_state.get("intent")
    # 带入规划事件历史（前端传入，优先级高于后端 SESSION_STORE 里的）
    if req.planning_events:
        room.planning_events_history = list(req.planning_events)
    if req.session_id and room.current_itinerary_dict:
        SESSION_STORE[req.session_id] = {
            **SESSION_STORE.get(req.session_id, {}),
            "intent": room.current_intent_dict,
            "itinerary": room.current_itinerary_dict,
            "user_id": req.user_id,
            "planning_events": room.planning_events_history,
        }
    # 初始化 LLM 上下文：把初始行程摘要写入，让后续重规划时 LLM 知道"之前规划了什么"
    if room.current_itinerary_dict:
        summary = room.current_itinerary_dict.get("summary", "已有行程")
        room.llm_context_messages.append(
            {
                "role": "assistant",
                "content": f"初始行程方案：{summary}",
                "timestamp": time.time(),
            }
        )
    if room.current_intent_dict:
        raw_input = room.current_intent_dict.get("raw_input", "")
        if raw_input:
            room.llm_context_messages.insert(
                0,
                {
                    "role": "user",
                    "content": f"发起人原始需求：{raw_input}",
                    "timestamp": time.time(),
                },
            )

    # 构造分享 URL（用请求的 host 拼）
    host = request.headers.get("host", "localhost:3000")
    scheme = "https" if "https" in str(request.url) else "http"
    # 前端路由：/room/[id]
    share_url = f"{scheme}://{host.replace(':8000', ':3000')}/room/{room.room_id}"

    return CreateRoomResponse(
        room_id=room.room_id,
        share_url=share_url,
        owner_id=req.user_id,
    )


@router.get("/room/{room_id}/state", summary="拉房间当前状态")
async def get_room_state(room_id: str) -> dict[str, Any]:
    """获取房间当前状态（HTTP 拉取，用于 SSR 或 WS 连接前预加载）。"""
    manager = get_room_manager()
    room = manager.get_room(room_id)
    if room is None:
        raise HTTPException(status_code=404, detail=f"房间不存在：{room_id}")
    return room.get_state_snapshot()


@router.websocket("/ws/{room_id}")
async def ws_collab(websocket: WebSocket, room_id: str):
    """多人协作 WebSocket 端点。

    连接参数（query string）：
    - user_id: 用户 ID（必填）
    - nickname: 昵称（可选，默认用 user_id）

    上行消息格式：
    - {"type": "constraint", "text": "不要辣的"}
    - {"type": "vote", "stage_index": 3, "action": "dislike"}
    - {"type": "vote", "stage_index": 1, "action": "like"}
    - {"type": "confirm"}
    - {"type": "adjust", "node_id": "R001", "action": {"type": "adjust", "adjustment": {...}, "label": "..."}}
      | {"type": "adjust", "node_id": "R001", "action": {"type": "alternative", "target_id": "..."}}
      | {"type": "adjust", "node_id": "R001", "action": {"type": "dislike"}}
      （ADR-0013 F-5：节点行定向调整按钮/具名备选，`action` 判别式协议同 F-4
      单人 `/chat/adjust` 的 `AdjustAction`；点踩走既有 `vote` 消息，`RoomManager.
      update_vote` 内部收编转调同一个 `RoomManager.adjust()` 引擎，不走这个消息类型）

    下行消息格式：见设计文档 §2 WebSocket 协议设计；F-5 新增 `node_locked`/
    `node_unlocked`（adjust 处理期锁定态广播）与 `member_reconnected`（区别于
    `member_joined`，见 `collab/room.py::RoomManager.join` docstring）。
    """
    manager = get_room_manager()
    room = manager.get_room(room_id)

    if room is None:
        await websocket.accept()
        try:
            await websocket.send_json({"type": "error", "message": f"房间不存在：{room_id}"})
            await websocket.close(code=4004, reason="房间不存在")
        except Exception:  # noqa: BLE001
            pass
        return

    # 解析 query 参数
    user_id = websocket.query_params.get("user_id", "anonymous")
    nickname = websocket.query_params.get("nickname", user_id)

    await websocket.accept()

    # 加入房间
    await manager.join(room, user_id, nickname, websocket)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "constraint":
                text = data.get("text", "").strip()
                if text:
                    await manager.add_constraint(room, user_id, text, source="text")

            elif msg_type == "vote":
                stage_index = data.get("stage_index")
                action = data.get("action", "")
                if isinstance(stage_index, int) and action in ("like", "dislike"):
                    await manager.update_vote(room, user_id, stage_index, action)

            elif msg_type == "adjust":
                node_id = data.get("node_id")
                raw_action = data.get("action")
                if not isinstance(node_id, str) or not node_id or not isinstance(raw_action, dict):
                    await websocket.send_json({
                        "type": "error",
                        "message": "adjust 消息缺少合法的 node_id/action",
                    })
                    continue
                try:
                    action = _ADJUST_ACTION_ADAPTER.validate_python(raw_action)
                except ValidationError as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"action 字段格式不对：{str(e)[:200]}",
                    })
                    continue
                await manager.adjust(room, user_id, node_id, action)

            elif msg_type == "confirm":
                # 仅 owner 可确认
                if user_id != room.owner_id:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "只有发起人可以确认下单",
                        }
                    )
                    continue
                # 确认统一走 _graph_confirm（ADR-0012 决策 5）；它不读 PLANNER_MODE，
                # confirm narration 恒为快速规则文案，故这里不再需要解析 mode。
                await manager.confirm(room, user_id)

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        await manager.leave(room, user_id)
    except Exception:  # noqa: BLE001
        # 任何异常都尝试清理房间状态
        try:
            await manager.leave(room, user_id)
        except Exception:  # noqa: BLE001
            pass
