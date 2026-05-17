"""agent.v2.conversation —— 对话状态持久化。

替代旧 main.py 里 `_SESSION_STORE: dict[str, Any]` 的简陋 in-memory cache。

核心改进：
- 把 Pydantic AI 的 `list[ModelMessage]` 跨 turn 持久化（解决「dock 直接反馈无上下文」根因）
- 同时保留向后兼容的 itinerary / intent 快照（confirm 流路径用得到）
- 单 process in-memory（demo 级；生产可换 Redis / Postgres ConversationCheckpointer）

线程安全：
- demo 单进程 + asyncio 模型，使用 dict 即可
- 多进程部署需替换为 Redis（接口已抽象 ConversationStore Protocol，方便后续切）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic_ai.messages import ModelMessage


@dataclass
class ConversationState:
    """单次对话（session）的全量状态。

    `messages` 是 Pydantic AI ModelMessage list，喂回 agent.run(message_history=)
    可让 LLM 看到完整上下文（用户上次说的 + Agent 上次回的 + Tool 调用）。
    """

    session_id: str
    user_id: str = "demo_user"
    """当前 user_id（影响意图解析的 persona prior）。"""

    messages: list[ModelMessage] = field(default_factory=list)
    """对话历史（跨 turn 累计），Pydantic AI 原生格式。"""

    # ---- 业务快照（向后兼容旧 confirm/refine 路径）----
    intent_snapshot: Optional[dict[str, Any]] = None
    """最后一次 IntentExtraction.model_dump()。"""

    itinerary_snapshot: Optional[dict[str, Any]] = None
    """最后一次 Itinerary.model_dump()。"""

    extra: dict[str, Any] = field(default_factory=dict)
    """杂项快照（演示场景标签 / refinement 临时数据等）。"""


class ConversationStore:
    """In-memory ConversationState 存储。

    线程/协程安全：每个 session 独立 lock；store 全局只用 plain dict。
    """

    def __init__(self) -> None:
        self._states: dict[str, ConversationState] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    async def get_or_create(
        self,
        session_id: str,
        *,
        user_id: str = "demo_user",
    ) -> ConversationState:
        """读已有 state，或创建空白 state。"""
        async with self._lock_for(session_id):
            state = self._states.get(session_id)
            if state is None:
                state = ConversationState(session_id=session_id, user_id=user_id)
                self._states[session_id] = state
            elif state.user_id != user_id:
                # user 切换：保留 messages 还是清？
                # 决策：清 messages（不同人对 Agent 的偏好不同），但保留 session_id
                state = ConversationState(session_id=session_id, user_id=user_id)
                self._states[session_id] = state
            return state

    async def get(self, session_id: str) -> Optional[ConversationState]:
        return self._states.get(session_id)

    async def save(self, state: ConversationState) -> None:
        """覆盖式保存。"""
        self._states[state.session_id] = state

    async def delete(self, session_id: str) -> None:
        self._states.pop(session_id, None)
        self._locks.pop(session_id, None)

    def stats(self) -> dict[str, int]:
        return {"sessions": len(self._states)}


# ============================================================
# 单例（demo 共用）
# ============================================================


_default_store: Optional[ConversationStore] = None


def get_default_store() -> ConversationStore:
    """获取全局默认 ConversationStore（demo 单例）。"""
    global _default_store
    if _default_store is None:
        _default_store = ConversationStore()
    return _default_store


__all__ = [
    "ConversationState",
    "ConversationStore",
    "get_default_store",
]
