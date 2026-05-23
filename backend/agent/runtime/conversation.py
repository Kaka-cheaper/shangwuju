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
# 实现 2：RedisRepository（Phase 0.22 真实现；FC 部署主路径）
# ============================================================


class RedisRepository:
    """跨进程 Redis 持久化。

    设计动机（Phase 0.22 真实现，2026-05-22）：
    - FC Custom Container 是 serverless，单实例无状态——会话必须外置
    - docker compose 本地环境也用同一套（compose 自带 redis service）
    - InMemory 只在 dev 直跑（pytest / 单 worker uvicorn）下走

    序列化策略：
    - messages 用 pydantic_ai.messages.ModelMessagesTypeAdapter（官方推荐）
    - intent_snapshot / itinerary_snapshot / extra 是 dict → json.dumps
    - user_id 是字符串
    - 整个 ConversationState 序列化成单个 hash（HSET）

    key 规范：
    - `shangwuju:conv:{session_id}` —— 单 session 状态
    - 默认 TTL 24h；confirm 后续期 7d（在 record_confirm_result hook 里调 EXPIRE）

    并发：
    - asyncio.Lock 仅保单实例内顺序；跨实例互斥用 Redis WATCH/MULTI 模式
      （demo 阶段单实例足够；多实例上线时再加分布式锁）
    """

    name = "redis"
    _KEY_PREFIX = "shangwuju:conv"
    _DEFAULT_TTL_SECONDS = 24 * 3600

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self._redis_url = (
            redis_url
            or os.getenv("REDIS_URL")
            or "redis://localhost:6379/0"
        )
        self._client: Any = None
        self._locks: dict[str, asyncio.Lock] = {}

    def _key(self, session_id: str) -> str:
        return f"{self._KEY_PREFIX}:{session_id}"

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    async def _get_client(self) -> Any:
        """懒初始化 redis 客户端（避免 import time 副作用）。"""
        if self._client is None:
            try:
                import redis.asyncio as redis_async  # type: ignore[import-not-found]
            except ImportError as e:
                raise RuntimeError(
                    "redis 包未安装，无法使用 SESSION_STORE=redis。"
                    "请用 `uv sync --extra runtime` 装齐运行时依赖。"
                ) from e
            self._client = redis_async.from_url(
                self._redis_url,
                decode_responses=False,  # 我们自己处理 bytes 序列化
                socket_connect_timeout=5,
                socket_timeout=10,
                max_connections=10,
            )
        return self._client

    async def _close(self) -> None:
        """unit test / shutdown 用。生产不需要主动调（连接池长驻）。"""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._client = None

    async def get_or_create(
        self,
        session_id: str,
        *,
        user_id: str = "demo_user",
    ) -> ConversationState:
        async with self._lock_for(session_id):
            state = await self._load(session_id)
            if state is None:
                state = ConversationState(session_id=session_id, user_id=user_id)
                await self._dump(state)
            elif state.user_id != user_id:
                # user 切换 → 同样规则：清 messages，新建 state（与 InMemory 行为一致）
                state = ConversationState(session_id=session_id, user_id=user_id)
                await self._dump(state)
            return state

    async def get(self, session_id: str) -> Optional[ConversationState]:
        return await self._load(session_id)

    async def save(self, state: ConversationState) -> None:
        await self._dump(state)

    async def delete(self, session_id: str) -> None:
        client = await self._get_client()
        await client.delete(self._key(session_id))
        self._locks.pop(session_id, None)

    def stats(self) -> dict[str, int]:
        # Redis 实时 count 需要 SCAN，不在 stats 中跑（性能成本）
        # 调用方有需要可单独跑 redis-cli SCAN
        return {"sessions": -1, "backend": "redis"}  # type: ignore[return-value]

    # ---- 内部：序列化 ----

    async def _load(self, session_id: str) -> Optional[ConversationState]:
        client = await self._get_client()
        raw = await client.get(self._key(session_id))
        if raw is None:
            return None
        try:
            return self._deserialize(raw)
        except Exception:  # noqa: BLE001
            # 反序列化失败（schema drift / 损坏数据）→ 返 None 让上层重建
            return None

    async def _dump(self, state: ConversationState) -> None:
        client = await self._get_client()
        payload = self._serialize(state)
        await client.set(
            self._key(state.session_id), payload, ex=self._DEFAULT_TTL_SECONDS
        )

    def _serialize(self, state: ConversationState) -> bytes:
        """序列化为 JSON bytes（messages 用 Pydantic AI 官方 adapter）。"""
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        import json

        msg_bytes = ModelMessagesTypeAdapter.dump_json(state.messages)
        # msg_bytes 是 bytes（list[ModelMessage] 的 JSON），转 str 以便嵌套在外层 JSON
        msg_str = msg_bytes.decode("utf-8")
        envelope = {
            "session_id": state.session_id,
            "user_id": state.user_id,
            "messages_json": msg_str,
            "intent_snapshot": state.intent_snapshot,
            "itinerary_snapshot": state.itinerary_snapshot,
            "extra": state.extra,
        }
        return json.dumps(envelope, ensure_ascii=False).encode("utf-8")

    def _deserialize(self, raw: bytes) -> ConversationState:
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        import json

        envelope = json.loads(raw.decode("utf-8"))
        messages_raw = envelope.get("messages_json", "[]")
        if isinstance(messages_raw, str):
            messages = ModelMessagesTypeAdapter.validate_json(messages_raw.encode("utf-8"))
        else:
            messages = []
        return ConversationState(
            session_id=envelope["session_id"],
            user_id=envelope.get("user_id", "demo_user"),
            messages=messages,
            intent_snapshot=envelope.get("intent_snapshot"),
            itinerary_snapshot=envelope.get("itinerary_snapshot"),
            extra=envelope.get("extra") or {},
        )


# 旧名兼容：RedisRepositoryStub 仍可 import（部分测试 / 旧文档引用）
RedisRepositoryStub = RedisRepository


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
            _default_repo = RedisRepository()
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
    "RedisRepository",
    "RedisRepositoryStub",  # 旧名 alias，兼容旧 import
    # 单例
    "get_default_repo",
    # 向后兼容（旧名）
    "ConversationStore",
    "get_default_store",
]
