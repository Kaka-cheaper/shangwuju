"""test_room_lifecycle_characterization —— F-5「房间成员体系」动刀前的安全网。

问题命名：特征化测试（characterization test，Feathers《Working Effectively with
Legacy Code》）——ADR-0013 决策 6/F-5 排定要在 `collab/room.py` 上动手术（进房卡、
身份房内归属+重连凭证、TTL 50min 清扫器连台账销毁、串行队列+锁定态广播、点踩收编、
台账房间侧接线）。清架构审查记录点名的既有缺口是："collab create_room 无测试覆盖"——
本文件补的就是这块空白：房间**生命周期**（建房 → 加入/重连 → 离开 → 快照演化 →
manager 管理），风格对齐已有的
`test_room_confirm_characterization.py` / `test_room_route_turn_dispatch.py`
（RoomManager 直驱、不起真 WS）。

铁律：本文件只新增测试，零生产代码改动。跑测中若发现疑似 bug/异味，只钉进本文件的
docstring/断言注释里说明现状，不修生产代码——修复留给 F-5 正式动刀时判断。

并行纪律：另一子代理同时在改 `api/adjust.py`（新）/`main.py`/前端。本文件因此：
- 不 `import main`（main.py 正在被并行改动，import 会绑定到不稳定的中间态）；
- WS 相关行为（join/leave/broadcast）沿用两份先例的手法——`RoomManager()` 直接
  构造房间，成员用**假 WebSocket**（只记录 `send_json` 调用列表，不做真实网络
  I/O）而非 `ws=None`——因为本文件要断言广播 payload 的具体内容（先例两个文件都
  只需要 `ws=None` 因为它们不看广播内容本身）；
- HTTP 层（`POST /room/create` + `GET /room/{id}/state`）验证过 `import backend.main`
  在当前状态下可行，但为了不被并行改动的 main.py 拖累/污染，改用「只挂 collab
  路由」的隔离 FastAPI app + `TestClient`（`app.include_router(api.collab.router)`）
  ——已验证足以让这两个端点独立工作，不依赖 main.py 的 lifespan/其余 10 个 router。

覆盖清单（对应任务书 1-5 点，均是 F-5 动刀前必须先钉死的现状）：
1. 建房：`create_room` 的 owner 初始化；`/room/create` 从 SESSION_STORE 带入方案/
   事件史（ADR-0012 决策 3：SESSION_STORE 是唯一真相源）、前端传入
   chat_messages/chat_state 的带入与优先级。
2. 加入/重连：首次加入 vs 同 user_id 重连（ws 更新、成员对象不重建）；新成员收到的
   全量快照字段清单（demand_ledger 目前刻意不在快照里，F-2 拍板，本文件钉住现状）；
   member_joined 广播 + exclude 语义。
3. 离开：`leave` 只置 ws=None 不删成员；member_left 广播；全员离线房间仍保留
   （现状注释自认"评委可能刷新页面"——TTL 清扫是 F-5 范围，本文件只钉现状不越界）。
4. 快照形状：votes/locked_stages 随 `update_vote` 真实演化（而非直接摆字段）；
   planning_active 随 planning_task 生命周期变化。
5. RoomManager 管理：`delete_room` 取消在跑任务、`list_rooms`、room_id 生成唯一性。
"""

from __future__ import annotations

import asyncio
import sys
import types
import uuid
from pathlib import Path
from typing import Any

import pytest

# ============================================================
# 过渡态桥（与 test_critics_v2 / test_room_confirm_characterization /
# test_room_route_turn_dispatch 同款：见那三个文件对该桥的解释）
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from collab import RoomManager, get_room_manager  # noqa: E402
from collab.room import Member  # noqa: E402
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary  # noqa: E402


# ============================================================
# 共用构造/驱动 helper
# ============================================================


class _FakeWebSocket:
    """假 WS：只记录 `send_json` 调用，不做真实网络 I/O。

    与先例（`test_room_confirm_characterization.py` / `test_room_route_turn_dispatch.py`）
    的 `ws=None` 不同——本文件要断言广播 payload 的具体内容（谁收到了什么），
    所以需要一个能"看得见"的假连接。`fail=True` 用于模拟连接已失效
    （`send_json` 抛异常），驱动 `RoomManager.broadcast` 的自动清理分支。
    """

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self._fail = fail

    async def send_json(self, message: dict[str, Any]) -> None:
        if self._fail:
            raise RuntimeError("simulated disconnect")
        self.sent.append(message)


def _seed_room(owner_id: str) -> tuple[RoomManager, "Room"]:  # noqa: F821 - Room 仅类型注释
    """构造一个已有 intent + 合法 itinerary 的房间（不起 WS，成员 ws=None）。"""
    manager = RoomManager()
    room = manager.create_room(owner_id=owner_id, nickname="发起人")
    room.current_intent_dict = _make_intent().model_dump()
    room.current_itinerary_dict = _make_legal_itinerary().model_dump()
    return manager, room


async def _vote_and_drain(
    manager: RoomManager, room, user_id: str, stage_index: int, action: str
) -> None:
    """跑一次 `update_vote`，并在**同一个事件循环**里排空它可能挂起的重排任务。

    原因同 `test_room_route_turn_dispatch.py::_add_constraint_and_drain` 的
    docstring：`update_vote` 对 dislike 是 fire-and-forget 地
    `asyncio.create_task(...)` 出重排任务的，必须在同一次 `asyncio.run` 里
    触发+等待，否则任务随上一个事件循环关闭被取消。
    """
    await manager.update_vote(room, user_id, stage_index, action)
    if room.planning_task is not None:
        await room.planning_task


def _event_types(room) -> list[str]:
    return [
        e["type"].value if hasattr(e["type"], "value") else e["type"]
        for e in room.planning_events_history
    ]


# ============================================================
# 1a. 建房：create_room 的 owner 初始化（RoomManager 单元级）
# ============================================================


def test_create_room_initializes_owner_member_and_registers_room():
    manager = RoomManager()
    room = manager.create_room(owner_id="owner_init_test", nickname="小美")

    assert manager.get_room(room.room_id) is room
    assert set(room.members.keys()) == {"owner_init_test"}
    owner = room.members["owner_init_test"]
    assert owner.role == "owner"
    assert owner.nickname == "小美"
    assert owner.ws is None
    assert room.owner_id == "owner_init_test"
    assert room.member_list == [
        {"user_id": "owner_init_test", "nickname": "小美", "role": "owner", "online": False}
    ]


def test_create_room_default_nickname_is_fa_qi_ren():
    """未显式传 nickname 时的默认值——`/room/create` 请求模型也用同一个默认值。"""
    manager = RoomManager()
    room = manager.create_room(owner_id="owner_default_nick_test")
    assert room.members["owner_default_nick_test"].nickname == "发起人"


# ============================================================
# 1b. 建房：/room/create HTTP 层（隔离 app，只挂 collab 路由）
# ============================================================


@pytest.fixture()
def collab_client():
    """只挂 collab 路由的隔离 FastAPI app + TestClient。

    刻意不 `import backend.main`（并行代理正在改 main.py/api/adjust.py，避免绑定到
    不稳定中间态）。已验证 `app.include_router(api.collab.router)` 足以让
    `/room/create` 与 `/room/{id}/state` 独立工作——两者只依赖
    `collab.get_room_manager()`（进程内单例）与 `api._session_store.SESSION_STORE`
    （同上），不依赖 main.py 的 lifespan 或其余 10 个 router。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api import collab as collab_api

    app = FastAPI()
    app.include_router(collab_api.router)
    with TestClient(app) as client:
        yield client


def test_http_create_room_without_session_id_has_empty_baseline(collab_client):
    resp = collab_client.post(
        "/room/create", json={"user_id": "http_owner_1", "nickname": "老张"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["owner_id"] == "http_owner_1"
    room_id = body["room_id"]

    state = collab_client.get(f"/room/{room_id}/state").json()
    assert state["itinerary"] is None
    assert state["intent"] is None
    assert state["chat_messages"] == []
    assert state["planning_events"] == []
    owner_entry = next(m for m in state["members"] if m["user_id"] == "http_owner_1")
    assert owner_entry["role"] == "owner"
    assert owner_entry["nickname"] == "老张"
    assert owner_entry["online"] is False, "HTTP 建房不建立 WS 连接，owner 初始必为离线"


def test_http_create_room_brings_in_session_store_itinerary_and_intent(collab_client):
    """ADR-0012 决策 3：SESSION_STORE 是唯一真相源——带入行程/意图/事件史。"""
    from api._session_store import SESSION_STORE

    session_id = f"collab-http-test-{uuid.uuid4().hex[:8]}"
    intent = _make_intent().model_dump()
    itinerary = _make_legal_itinerary().model_dump()
    SESSION_STORE[session_id] = {
        "intent": intent,
        "itinerary": itinerary,
        "planning_events": [{"type": "itinerary_ready", "payload": {}}],
    }
    try:
        resp = collab_client.post(
            "/room/create",
            json={"user_id": "http_owner_2", "session_id": session_id},
        )
        assert resp.status_code == 200
        room_id = resp.json()["room_id"]
        state = collab_client.get(f"/room/{room_id}/state").json()
        assert state["intent"] == intent
        assert state["itinerary"] == itinerary
        assert state["planning_events"] == [{"type": "itinerary_ready", "payload": {}}]

        cached = SESSION_STORE[session_id]
        assert cached["user_id"] == "http_owner_2", "写回时 user_id 应记为建房请求的 user_id"
        assert cached["intent"] == intent
        assert cached["itinerary"] == itinerary
    finally:
        SESSION_STORE.pop(session_id, None)


def test_http_create_room_missing_session_id_in_store_leaves_baseline_empty(collab_client):
    resp = collab_client.post(
        "/room/create",
        json={"user_id": "http_owner_3", "session_id": "does-not-exist-in-store"},
    )
    assert resp.status_code == 200
    room_id = resp.json()["room_id"]
    state = collab_client.get(f"/room/{room_id}/state").json()
    assert state["itinerary"] is None
    assert state["intent"] is None


def test_http_create_room_accepts_frontend_chat_messages_and_chat_state(collab_client):
    chat_messages = [{"id": "m1", "role": "user", "text": "大家好", "createdAt": 1}]
    chat_state = {"itinerary": {"summary": "前端本地态"}, "intent": {"raw_input": "前端需求"}}

    resp = collab_client.post(
        "/room/create",
        json={
            "user_id": "http_owner_4",
            "chat_messages": chat_messages,
            "chat_state": chat_state,
        },
    )
    assert resp.status_code == 200
    room_id = resp.json()["room_id"]
    state = collab_client.get(f"/room/{room_id}/state").json()
    assert state["chat_messages"] == chat_messages
    assert state["chat_state"] == chat_state
    # 无 SESSION_STORE 命中时，chat_state 的 itinerary/intent 兜底填充 current_*
    assert state["itinerary"] == {"summary": "前端本地态"}
    assert state["intent"] == {"raw_input": "前端需求"}


def test_http_create_room_session_store_itinerary_takes_priority_over_chat_state_fallback(
    collab_client,
):
    """`current_itinerary_dict = ... or req.chat_state.get("itinerary")`——SESSION_STORE
    已有值时 chat_state 的兜底不应覆盖它（`or` 语义，优先级钉住）。"""
    from api._session_store import SESSION_STORE

    session_id = f"collab-http-test-{uuid.uuid4().hex[:8]}"
    store_itinerary = _make_legal_itinerary().model_dump()
    SESSION_STORE[session_id] = {"intent": None, "itinerary": store_itinerary}
    try:
        resp = collab_client.post(
            "/room/create",
            json={
                "user_id": "http_owner_5",
                "session_id": session_id,
                "chat_state": {"itinerary": {"summary": "不该生效的前端兜底"}},
            },
        )
        room_id = resp.json()["room_id"]
        state = collab_client.get(f"/room/{room_id}/state").json()
        assert state["itinerary"] == store_itinerary
    finally:
        SESSION_STORE.pop(session_id, None)


def test_http_create_room_explicit_planning_events_override_session_store_cache(
    collab_client,
):
    """前端显式传入的 planning_events 优先级高于 SESSION_STORE 缓存
    （代码注释明写"优先级高于后端 SESSION_STORE 里的"）。"""
    from api._session_store import SESSION_STORE

    session_id = f"collab-http-test-{uuid.uuid4().hex[:8]}"
    SESSION_STORE[session_id] = {
        "intent": None,
        "itinerary": None,
        "planning_events": [{"type": "cached_event"}],
    }
    try:
        resp = collab_client.post(
            "/room/create",
            json={
                "user_id": "http_owner_6",
                "session_id": session_id,
                "planning_events": [{"type": "frontend_event"}],
            },
        )
        room_id = resp.json()["room_id"]
        state = collab_client.get(f"/room/{room_id}/state").json()
        assert state["planning_events"] == [{"type": "frontend_event"}]
    finally:
        SESSION_STORE.pop(session_id, None)


def test_http_create_room_with_fresh_session_id_and_chat_state_writes_through_to_session_store(
    collab_client,
):
    """全新 session_id（SESSION_STORE 里还没有）配合 chat_state 传入 itinerary 时，
    应写透进 SESSION_STORE——供后续 /chat/confirm 等端点按 session_id 取用
    （ADR-0012 决策 3 单一真相源的另一半：不仅"读"这里，产生新数据也要"写"回这里）。
    """
    from api._session_store import SESSION_STORE

    session_id = f"collab-http-test-{uuid.uuid4().hex[:8]}"
    assert session_id not in SESSION_STORE
    chat_state = {"itinerary": {"summary": "全新会话的前端态"}}
    try:
        resp = collab_client.post(
            "/room/create",
            json={"user_id": "http_owner_8", "session_id": session_id, "chat_state": chat_state},
        )
        assert resp.status_code == 200
        assert session_id in SESSION_STORE
        assert SESSION_STORE[session_id]["itinerary"] == chat_state["itinerary"]
        assert SESSION_STORE[session_id]["user_id"] == "http_owner_8"
    finally:
        SESSION_STORE.pop(session_id, None)


def test_http_create_room_llm_context_seeds_raw_input_before_itinerary_summary(
    collab_client,
):
    """llm_context_messages 播种顺序：raw_input 用 `insert(0, ...)` 顶到最前，
    itinerary 摘要靠 `append` 排在其后——顺序钉住（重规划时喂给 LLM 的上下文顺序）。
    """
    from api._session_store import SESSION_STORE

    session_id = f"collab-http-test-{uuid.uuid4().hex[:8]}"
    itinerary = _make_legal_itinerary().model_dump()
    intent = _make_intent().model_dump()
    intent["raw_input"] = "带我去海洋馆"
    SESSION_STORE[session_id] = {"intent": intent, "itinerary": itinerary}
    try:
        resp = collab_client.post(
            "/room/create", json={"user_id": "http_owner_7", "session_id": session_id}
        )
        room_id = resp.json()["room_id"]

        manager = get_room_manager()
        room = manager.get_room(room_id)
        assert [m["role"] for m in room.llm_context_messages] == ["user", "assistant"]
        assert room.llm_context_messages[0]["content"] == "发起人原始需求：带我去海洋馆"
        assert room.llm_context_messages[1]["content"].startswith("初始行程方案：")
    finally:
        SESSION_STORE.pop(session_id, None)


def test_http_get_room_state_404_for_unknown_room(collab_client):
    resp = collab_client.get("/room/does-not-exist-000/state")
    assert resp.status_code == 404


# ============================================================
# 2. 加入/重连（RoomManager 直驱 + 假 WebSocket）
# ============================================================


def test_join_new_participant_gets_full_snapshot_and_others_get_member_joined_excluding_self():
    async def scenario():
        manager, room = _seed_room("owner_snap_test")

        owner_ws = _FakeWebSocket()
        await manager.join(room, "owner_snap_test", "发起人", owner_ws)
        owner_ws.sent.clear()

        new_ws = _FakeWebSocket()
        await manager.join(room, "participant_1", "小明", new_ws)

        # 新成员且仅收到一条 room_state 快照，形状与 get_state_snapshot() 一致
        assert len(new_ws.sent) == 1
        snapshot_msg = new_ws.sent[0]
        assert snapshot_msg["type"] == "room_state"
        assert snapshot_msg == room.get_state_snapshot()
        assert "demand_ledger" not in snapshot_msg, (
            "F-2 拍板：诉求台账刻意不进快照（房间侧只是存储位），现状钉住"
        )
        assert all(m["type"] != "member_joined" for m in new_ws.sent), (
            "join() 用 exclude=user_id 广播 member_joined——新成员自己不该收到关于自己的通知"
        )

        # 已在场的房主应收到关于新成员的 member_joined 广播
        assert owner_ws.sent == [
            {
                "type": "member_joined",
                "user_id": "participant_1",
                "nickname": "小明",
                "role": "participant",
            }
        ]

    asyncio.run(scenario())


def test_join_owner_reconnect_keeps_role_and_updates_ws_without_recreating_member():
    """HTTP 建房只创建 `ws=None` 的 owner 成员，随后房主真正连 WS 走的是
    `join()` 的"已在 members 里→只更新 ws"分支——role 不应被重置，Member 对象
    不应被重建（重连凭证语义，F-5「身份房内归属+重连凭证」动刀前必须先钉死现状）。
    """

    async def scenario():
        manager = RoomManager()
        room = manager.create_room(owner_id="owner_join_test", nickname="房主本尊")
        original_member = room.members["owner_join_test"]
        assert original_member.ws is None
        assert original_member.role == "owner"

        observer_ws = _FakeWebSocket()
        await manager.join(room, "observer", "旁观者", observer_ws)
        observer_ws.sent.clear()

        ws1 = _FakeWebSocket()
        await manager.join(room, "owner_join_test", "房主本尊", ws1)
        assert room.members["owner_join_test"] is original_member, "重连不应重建 Member 对象"
        assert room.members["owner_join_test"].ws is ws1
        assert room.members["owner_join_test"].role == "owner"
        joined_msgs = [m for m in observer_ws.sent if m["type"] == "member_joined"]
        assert len(joined_msgs) == 1
        assert joined_msgs[0] == {
            "type": "member_joined",
            "user_id": "owner_join_test",
            "nickname": "房主本尊",
            "role": "owner",
        }

        # 二次重连：换个 ws + 换个昵称参数
        ws2 = _FakeWebSocket()
        await manager.join(room, "owner_join_test", "改名后的房主", ws2)
        assert room.members["owner_join_test"] is original_member
        assert room.members["owner_join_test"].ws is ws2
        assert room.members["owner_join_test"].nickname == "房主本尊", (
            "重连时传入的新昵称不会更新已存昵称——现状行为（疑似异味，非本次断言引入的"
            "新逻辑）：若前端允许重连时改昵称，这里会静默丢弃，见任务报告"
        )
        # 现状：重连仍会无条件再广播一次 member_joined（非去重）——记入报告供 F-5 参考
        joined_msgs2 = [m for m in observer_ws.sent if m["type"] == "member_joined"]
        assert len(joined_msgs2) == 2, "现状：每次 join()（含重连）都会再广播一次 member_joined"

    asyncio.run(scenario())


# ============================================================
# 3. 离开（RoomManager 直驱 + 假 WebSocket）
# ============================================================


def test_leave_marks_offline_without_removing_member_and_broadcasts_member_left():
    async def scenario():
        manager, room = _seed_room("owner_leave_test")
        owner_ws = _FakeWebSocket()
        await manager.join(room, "owner_leave_test", "发起人", owner_ws)
        p_ws = _FakeWebSocket()
        await manager.join(room, "participant_leave", "小红", p_ws)
        owner_ws.sent.clear()

        await manager.leave(room, "participant_leave")

        assert "participant_leave" in room.members, (
            "leave 只标记离线，不删除成员——F-5「身份房内归属+重连凭证」要保留的正是这份状态"
        )
        assert room.members["participant_leave"].ws is None
        assert owner_ws.sent == [{"type": "member_left", "user_id": "participant_leave"}]

    asyncio.run(scenario())


def test_leave_all_members_offline_room_still_retained():
    """全员离线后房间现状仍保留（room.py leave() 的注释自认"评委可能刷新页面"）——
    TTL 清扫是 F-5 范围，本文件只钉现状，不要求也不假设未来会加清理。"""

    async def scenario():
        manager, room = _seed_room("owner_alloffline_test")
        ws = _FakeWebSocket()
        await manager.join(room, "owner_alloffline_test", "发起人", ws)

        await manager.leave(room, "owner_alloffline_test")

        assert all(m.ws is None for m in room.members.values())
        assert manager.get_room(room.room_id) is not None

    asyncio.run(scenario())


def test_broadcast_marks_member_offline_on_send_failure_without_breaking_others():
    """`broadcast()` 对送达失败的连接做自动清理——不应让一个坏连接拖垮整次广播。"""

    async def scenario():
        manager, room = _seed_room("owner_bcast_test")
        good_ws = _FakeWebSocket()
        bad_ws = _FakeWebSocket(fail=True)
        await manager.join(room, "owner_bcast_test", "发起人", good_ws)
        await manager.join(room, "flaky_participant", "抖动参与者", bad_ws)
        good_ws.sent.clear()

        await manager.broadcast(room, {"type": "probe"})

        assert good_ws.sent == [{"type": "probe"}]
        assert room.members["flaky_participant"].ws is None, (
            "send_json 抛异常的连接应被 broadcast 自动标记离线（room.members[uid].ws = None）"
        )

    asyncio.run(scenario())


# ============================================================
# 4. 快照形状：随真实操作演化
# ============================================================


def test_snapshot_key_shape_and_demand_ledger_excluded():
    manager, room = _seed_room("owner_snapshot_keys_test")
    room.demand_ledger.append({"member_id": "owner_snapshot_keys_test", "field": "distance_max_km"})

    snapshot = room.get_state_snapshot()

    expected_keys = {
        "type",
        "room_id",
        "owner_id",
        "members",
        "constraints",
        "votes",
        "itinerary",
        "previous_itinerary",
        "intent",
        "locked_stages",
        "planning_events",
        "chat_messages",
        "chat_state",
        "planning_active",
    }
    assert set(snapshot.keys()) == expected_keys, (
        f"快照字段清单变化——F-5 接线台账前请先确认是否为有意变更。实际={set(snapshot.keys())}"
    )
    assert "demand_ledger" not in snapshot, (
        "F-2 拍板：诉求台账刻意不进快照（房间侧只是存储位，接线归 F-4/F-5），"
        "即便 room.demand_ledger 已有数据也不应泄漏进快照——现状钉住"
    )


def test_snapshot_votes_and_locked_stages_evolve_with_update_vote():
    """votes/locked_stages 随真实 `update_vote` 调用演化（而非直接摆字段）：
    - 赞不产生约束、不触发重排，只写 votes + 加入 locked_stages；
    - 对某一段踩，只解锁那一段，不影响其它段已有的赞锁定；
    - 踩仍走既有 refiner 合并重排路径（回归防护）。
    """

    async def scenario():
        manager, room = _seed_room("owner_vote_snap_test")

        await _vote_and_drain(manager, room, "owner_vote_snap_test", 0, "like")
        await _vote_and_drain(manager, room, "owner_vote_snap_test", 1, "like")
        snap = room.get_state_snapshot()
        assert snap["votes"] == {
            "0": {"owner_vote_snap_test": "like"},
            "1": {"owner_vote_snap_test": "like"},
        }
        assert set(snap["locked_stages"]) == {0, 1}
        assert room.constraints == []
        assert room.planning_task is None

        await _vote_and_drain(manager, room, "owner_vote_snap_test", 0, "dislike")
        snap2 = room.get_state_snapshot()
        assert snap2["votes"]["0"] == {"owner_vote_snap_test": "dislike"}
        assert set(snap2["locked_stages"]) == {1}, (
            "对第 0 段踩只应解锁第 0 段，第 1 段的赞锁定不受影响"
        )
        assert len(room.constraints) == 1
        assert room.constraints[0].source == "vote_dislike"

        assert "refinement_done" in _event_types(room), "踩应沿用既有 refiner 合并重排路径"

    asyncio.run(scenario())


def test_snapshot_planning_active_reflects_task_lifecycle():
    async def scenario():
        manager, room = _seed_room("owner_active_flag_test")
        assert room.get_state_snapshot()["planning_active"] is False

        room.planning_task = asyncio.create_task(asyncio.sleep(0.05))
        assert room.get_state_snapshot()["planning_active"] is True

        await room.planning_task
        assert room.get_state_snapshot()["planning_active"] is False

    asyncio.run(scenario())


# ============================================================
# 5. RoomManager 管理
# ============================================================


def test_delete_room_cancels_running_planning_task():
    async def scenario():
        manager = RoomManager()
        room = manager.create_room(owner_id="owner_delete_test")
        task = asyncio.create_task(asyncio.sleep(5))
        room.planning_task = task

        manager.delete_room(room.room_id)

        assert manager.get_room(room.room_id) is None
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    asyncio.run(scenario())


def test_delete_room_unknown_id_is_noop():
    manager = RoomManager()
    manager.delete_room("unknown-room-id")  # 不应抛异常


def test_delete_room_with_already_finished_task_does_not_error():
    async def scenario():
        manager = RoomManager()
        room = manager.create_room(owner_id="owner_delete_done_test")
        task = asyncio.create_task(asyncio.sleep(0))
        room.planning_task = task
        await task  # 提前跑完

        manager.delete_room(room.room_id)  # 不应对已完成任务再调用 cancel 出错

        assert manager.get_room(room.room_id) is None

    asyncio.run(scenario())


def test_list_rooms_reports_active_rooms_with_expected_shape():
    manager = RoomManager()
    room_a = manager.create_room(owner_id="owner_list_a")
    room_b = manager.create_room(owner_id="owner_list_b")
    room_a.members["extra_participant"] = Member(
        user_id="extra_participant", nickname="旁听", role="participant"
    )

    listing = {r["room_id"]: r for r in manager.list_rooms()}
    assert set(listing.keys()) == {room_a.room_id, room_b.room_id}
    assert listing[room_a.room_id]["owner_id"] == "owner_list_a"
    assert listing[room_a.room_id]["member_count"] == 2
    assert listing[room_b.room_id]["member_count"] == 1
    assert isinstance(listing[room_a.room_id]["created_at"], float)

    manager.delete_room(room_b.room_id)
    remaining = {r["room_id"] for r in manager.list_rooms()}
    assert remaining == {room_a.room_id}


def test_generate_room_id_is_six_char_hex_and_effectively_unique():
    ids = {RoomManager._generate_room_id() for _ in range(200)}
    assert len(ids) == 200, "200 次生成中出现哈希碰撞——超出 6 位 hex 空间下的合理预期"
    for rid in ids:
        assert len(rid) == 6
        assert all(c in "0123456789abcdef" for c in rid)
