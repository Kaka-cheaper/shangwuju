"""agent.v2.conversation —— 跨 turn 持久化抽象层（Phase 0.11 重构）。

商业演进路径（让评委看到 demo 不止于 demo）：
    - Demo（当前）：InMemoryRepository（单进程 dict）
    - MVP：       RedisRepository（多实例共享 / 跨设备）
    - 真产品：     PostgresRepository（永久存档 + analytics）

切换方式：
    在 backend/.env 设 SESSION_STORE=memory|redis（默认 memory）。
    第一次调 get_default_repo() 时按 env 实例化对应 backend；后续复用。

向后兼容（重要）：
    旧名 ConversationStore / get_default_store 仍然 import 与可调用：
        ConversationStore   = InMemoryRepository           # type alias
        get_default_store() = get_default_repo()           # 委托
    main.py / orchestrator.py 都不需要改 import。

依赖：
    - 序列化 ModelMessage（Pydantic AI 原生格式）需要
      pydantic_ai.messages.ModelMessagesTypeAdapter（Redis 实现接入时用，stub 暂不依赖）
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic_ai.messages import ModelMessage


# ============================================================
# 状态字段（向后兼容，字段不动）
# ============================================================


@dataclass
class ConversationState:
    """单次对话（session）的全量状态。

    `messages` 是 Pydantic AI ModelMessage list，喂回 agent.run(message_history=)
    可让 LLM 看到完整上下文（用户上次说的 + Agent 上次回的 + Tool 调用）。

    字段保持与重构前 100% 一致 —— main.py 在直接读 `.messages` / `.itinerary_snapshot`，
    任何字段重命名都会破。
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


# ============================================================
# Repository 抽象（Protocol）
# ============================================================


@runtime_checkable
class ConversationRepository(Protocol):
    """跨 turn 状态持久化的统一接口。

    任何 backend（memory / redis / postgres / ...）只要满足以下五个方法 +
    一个 `name` 属性，即可注入 `_default_repo`。
    """

    name: str  # "memory" | "redis" | "postgres" | ...

    async def get_or_create(
        self,
        session_id: str,
        *,
        user_id: str = "demo_user",
    ) -> ConversationState: ...

    async def get(self, session_id: str) -> Optional[ConversationState]: ...

    async def save(self, state: ConversationState) -> None: ...

    async def delete(self, session_id: str) -> None: ...

    def stats(self) -> dict[str, int]: ...


# ============================================================
# 实现 1：InMemoryRepository（demo 默认）
# ============================================================


class InMemoryRepository:
    """单进程 dict + 每 session asyncio.Lock。

    线程/协程安全：每个 session 独立 lock；store 全局只用 plain dict。

    限制：
    - 不跨进程（uvicorn workers > 1 时各 worker 各看各的）
    - 不跨设备（手机 + 电脑同 session 看不到对方）
    - 进程重启即清

    评委演示足够；商业化需切到 RedisRepository。
    """

    name = "memory"

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
        """读已有 state，或创建空白 state。

        user_id 切换时清 messages（不同人对 Agent 的偏好不同），但保留 session_id
        让前端 sticky session 不抖。
        """
        async with self._lock_for(session_id):
            state = self._states.get(session_id)
            if state is None:
                state = ConversationState(session_id=session_id, user_id=user_id)
                self._states[session_id] = state
            elif state.user_id != user_id:
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
# 实现 2：RedisRepositoryStub（Milestone 2 接入点）
# ============================================================


class RedisRepositoryStub:
    """Redis 持久化的占位实现（Milestone 2 计划）。

    评委把 SESSION_STORE 切到 `redis` 时会看到本 stub 抛出友好提示，
    说明商业化路径已经预留接入点 —— 不是 demo 偷懒，是 demo 先聚焦闭环。

    真实 Redis 实现要点（接入清单）：
    1. 增加 redis-py / aioredis 依赖
    2. 序列化 ConversationState：
       - messages 用 `pydantic_ai.messages.ModelMessagesTypeAdapter` 转 JSON-safe bytes
       - intent_snapshot / itinerary_snapshot / extra 都是 dict，json.dumps 即可
    3. key 规范：`shangwuju:conv:{session_id}` （便于按 prefix 清场）
    4. TTL：默认 24h，confirm 后续期 7d（用户分享出去也能回看）
    5. lock：用 redis SETNX + Lua script 替代 asyncio.Lock 实现跨实例互斥

    详见 `docs/06-business/02-持久化演进.md`（Milestone 2 spec）。
    """

    name = "redis"

    def _not_impl(self) -> None:
        raise NotImplementedError(
            "Redis 持久化是 Milestone 2 计划。详见 docs/06-business/02-持久化演进.md。"
            "切回 SESSION_STORE=memory 即可恢复 Demo 模式。"
        )

    async def get_or_create(
        self,
        session_id: str,
        *,
        user_id: str = "demo_user",
    ) -> ConversationState:
        self._not_impl()
        raise AssertionError("unreachable")  # for type checkers

    async def get(self, session_id: str) -> Optional[ConversationState]:
        self._not_impl()
        raise AssertionError("unreachable")

    async def save(self, state: ConversationState) -> None:
        self._not_impl()

    async def delete(self, session_id: str) -> None:
        self._not_impl()

    def stats(self) -> dict[str, int]:
        return {"sessions": 0, "backend": "redis-stub"}  # type: ignore[return-value]


# ============================================================
# 单例 + .env 路由
# ============================================================


_default_repo: Optional[ConversationRepository] = None


def get_default_repo() -> ConversationRepository:
    """从 .env SESSION_STORE 解析 backend；默认 memory。

    - SESSION_STORE 缺省 / "memory" → InMemoryRepository
    - SESSION_STORE = "redis"      → RedisRepositoryStub（Milestone 2 接入点）
    - 其他值                        → ValueError（fail fast 不要静默降级）

    单例：第一次调用决定 backend，后续调用复用同一实例（避免多次实例化产生孤立状态）。
    """
    global _default_repo
    if _default_repo is None:
        name = (os.getenv("SESSION_STORE") or "memory").strip().lower()
        if name == "memory":
            _default_repo = InMemoryRepository()
        elif name == "redis":
            _default_repo = RedisRepositoryStub()
        else:
            raise ValueError(
                f"Unknown SESSION_STORE: {name!r}; valid values: memory|redis"
            )
    return _default_repo


def _reset_default_repo_for_tests() -> None:
    """测试专用：清空 _default_repo 让下一次 get_default_repo() 重新按 env 解析。

    生产代码不应调；只给 verify_repository.py 在切 SESSION_STORE 间用。
    """
    global _default_repo
    _default_repo = None


# ============================================================
# 旧名保留（main.py / orchestrator.py 仍在用，不能改）
# ============================================================

# 旧名 ConversationStore = InMemoryRepository（type alias，等价 import）
ConversationStore = InMemoryRepository


def get_default_store() -> ConversationRepository:
    """旧名保留兼容；委托给 get_default_repo()。

    main.py / orchestrator.py 都通过此入口拿 store，重构后无需改任何 import。
    """
    return get_default_repo()


__all__ = [
    # 抽象
    "ConversationState",
    "ConversationRepository",
    # 实现
    "InMemoryRepository",
    "RedisRepositoryStub",
    # 单例
    "get_default_repo",
    # 向后兼容（旧名）
    "ConversationStore",
    "get_default_store",
]
