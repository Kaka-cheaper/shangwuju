"""test_e0c_graph_confirm_memory_dual_track —— ADR-0012 决策 5 硬门 1 探针。

背景 7 的真 bug（2026-07-02 只读架构审查挖出）：两套记忆**不是**同一件事的两份
实现——`memory_writer.persist_memory`（graph confirm 触发）写 `user_profile.json`
的 `recent_trips`；`data/memory_store`（迁移前只有 `_stub_confirm` 经
`_accumulate_memory_after_confirm` 触发）写 per-user 的偏好标签 / 访问历史
（UserMemory）。而 UserMemory 的读者全在主路径上（persona_qa / intent_parser_prompt
的 persona prior / search_adapter 排重 / `/preferences` API）——**主 App 确认走
`_graph_confirm`，此前从不调 `_accumulate_memory_after_confirm`，主路径自己的画像
问答 / 意图先验读的库，只有协作房间的确认在喂。**

本测试直调 `_graph_confirm`（不经协作房间），确认 confirm 后两种记忆副作用
都真的落地（ADR-0012 决策 5 硬门 1：统一后的确认流必须同时执行两种记忆副作用，
memory_writer 与 memory_store 标签累积并列，不是二选一）。

【判据变更理由（记忆身份读写分离批，ADR-0015 身份边界补充决策，2026-07-05）】
旧判据：累积记在 `get_memory(user_id)` 头上——demo 单用户假设下成立；演示日
多访客并发时同一 user_id（画像模板 id）被多个访客共用，A 确认的行程会污染 B。
新判据：确认产生的一切累积按 **session_id 键控**（会话即身份）——
- `get_memory(session_id)` 能看到标签 / 访问累积（双轨仍然都落地）；
- `get_memory(user_id)` 必须为空（user_id 只再用于共享只读的画像模板）；
- 行程档案（recent_trips）同样落在 `get_recent_trips(session_id)`。
生产迁移 = 把键从会话 ID 换成账号 ID，机制不动。

不调真 LLM；`persist_memory` 走真实实现（写进程内会话档案，不落文件）。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

from data.memory_store import get_memory, get_recent_trips, reset_all_memory

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from api._session_store import SESSION_STORE  # noqa: E402
from api._streams import graph_confirm as _gc  # noqa: E402
from api._streams.graph_confirm import _graph_confirm  # noqa: E402
from api._streams.models import ChatConfirmRequest  # noqa: E402
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary  # noqa: E402


async def _confirm_and_drain(req: ChatConfirmRequest) -> list:
    events = [ev async for ev in _graph_confirm(req)]
    pending = [t for t in _gc._BACKGROUND_TASKS if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return events


def test_graph_confirm_accumulates_memory_store_tags_and_visits():
    """主 App 确认（直调 _graph_confirm）后会话私有 UserMemory 可见标签 / 访问累积。"""
    reset_all_memory()

    session_id = "e0c_probe_graph_confirm_memory_dual_track"
    user_id = "u_e0c_probe"
    intent = _make_intent(social_context="家庭日常")
    itinerary = _make_legal_itinerary()
    SESSION_STORE[session_id] = {
        "intent": intent.model_dump(),
        "itinerary": itinerary.model_dump(),
        "user_id": user_id,
    }

    events = asyncio.run(
        _confirm_and_drain(ChatConfirmRequest(session_id=session_id, decision="confirm"))
    )
    types_ = [ev.type.value for ev in events]
    assert "stream_error" not in types_, f"确认不应报错，events={types_}"
    assert types_[-1] == "done"

    memory = get_memory(session_id)
    visited_ids = {v.target_id for v in memory.visited_targets}
    expected_ids = {
        n.target_id for n in itinerary.nodes if n.target_kind in ("poi", "restaurant")
    }
    assert expected_ids <= visited_ids, (
        "ADR-0012 决策 5 硬门 1 探针（键语义已改会话）：主 App 确认（_graph_confirm）"
        f"必须把累积记在会话头上；期望 get_memory(session_id) 的 visited ⊇ {expected_ids}，"
        f"实际 visited_ids={visited_ids}"
    )

    # 读写分离批新增：user_id 键上不得再有累积（模板只读，累积会话私有）
    assert get_memory(user_id).visited_targets == [], (
        "确认累积不得再写 user_id 键——多访客共用画像模板 id 时会跨访客串味"
    )

    # 双轨另一轨（memory_writer 行程档案）同样落在会话键
    trips = get_recent_trips(session_id)
    assert trips and trips[0].social_context == "家庭日常", (
        f"行程档案应落在会话私有存储，实际 get_recent_trips({session_id!r})={trips}"
    )
