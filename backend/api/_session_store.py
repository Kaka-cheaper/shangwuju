"""会话快照 + user_id 解析。

`SESSION_STORE`：session_id → {"intent": dict, "itinerary": dict, "user_id": str,
"planning_events": list[dict]}，供 /chat/confirm、/chat/refine、协作房间创建读取。

存储行为按 env `SESSION_STORE` 切换（dict-like，调用点零改动）：
  - `memory`(默认 / 裸机)：纯进程内存 dict，行为与普通 dict **完全一致**，
    不 import redis、不连 redis（零回归）。
  - `redis`：内存为主 + 写时 fire-and-forget 镜像到 Redis；进程启动时
    `warm_from_redis()` 从 Redis 预热回内存 → **单实例重启可恢复**。

定位说明：读路径走本进程内存，不做多实例实时一致（与协作房间「单实例」一致）。
真正需要多实例一致的「跨 turn 对话上下文」由 LangGraph 的 Redis checkpointer 负责。
注意：对 `SESSION_STORE[sid]` 返回的内层 dict 做**原地**修改（如
`SESSION_STORE[sid]["planning_events"] = ...`）不会触发镜像，Redis 快照以整体赋值
（`SESSION_STORE[sid] = {...}`）为准——这对 confirm/refine 所需的 intent/itinerary 无影响。

`resolve_user_id`：body.user_id > X-User-Id header > "demo_user"。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "shangwuju:snap"
_SNAPSHOT_TTL_SECONDS = 24 * 3600


class _SessionSnapshotStore:
    """dict-like 会话快照存储；redis 模式下额外镜像 / 预热到 Redis。

    刻意实现完整 dict 接口，使现有 `SESSION_STORE[...]` 调用点零改动。
    """

    def __init__(self) -> None:
        self._mem: dict[str, dict[str, Any]] = {}
        self._redis_enabled: Optional[bool] = None
        self._client: Any = None

    # ---- 模式判断（lazy；env 在 main.load_dotenv() 后才稳定）----
    def _redis_on(self) -> bool:
        if self._redis_enabled is None:
            self._redis_enabled = (
                os.getenv("SESSION_STORE") or "memory"
            ).strip().lower() == "redis"
        return self._redis_enabled

    def _get_client(self) -> Any:
        if self._client is None:
            import redis.asyncio as redis_async  # 懒导入：memory 模式永不 import

            url = os.getenv("REDIS_URL") or "redis://localhost:6379/0"
            self._client = redis_async.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=10,
            )
        return self._client

    def _key(self, session_id: str) -> str:
        return f"{_REDIS_KEY_PREFIX}:{session_id}"

    # ---- 镜像写（fire-and-forget，不阻塞 SSE 流）----
    def _mirror(self, session_id: str) -> None:
        if not self._redis_on():
            return
        data = self._mem.get(session_id)
        if data is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 无事件循环（同步上下文 / 测试）→ 仅内存，跳过镜像
        loop.create_task(self._write_redis(session_id, dict(data)))

    async def _write_redis(self, session_id: str, data: dict[str, Any]) -> None:
        try:
            client = self._get_client()
            await client.set(
                self._key(session_id),
                json.dumps(data, ensure_ascii=False),
                ex=_SNAPSHOT_TTL_SECONDS,
            )
        except Exception:  # noqa: BLE001
            logger.debug("session snapshot redis mirror failed", exc_info=True)

    def _del_async(self, session_id: str) -> None:
        if not self._redis_on():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._delete_redis(session_id))

    async def _delete_redis(self, session_id: str) -> None:
        try:
            await self._get_client().delete(self._key(session_id))
        except Exception:  # noqa: BLE001
            logger.debug("session snapshot redis delete failed", exc_info=True)

    # ---- startup 预热 ----
    async def warm_from_redis(self) -> int:
        """从 Redis 把所有会话快照加载回内存（单实例重启恢复）。返回加载条数。"""
        if not self._redis_on():
            return 0
        try:
            client = self._get_client()
            n = 0
            async for k in client.scan_iter(match=f"{_REDIS_KEY_PREFIX}:*"):
                raw = await client.get(k)
                if not raw:
                    continue
                sid = k.split(":", 2)[-1] if isinstance(k, str) else k
                try:
                    self._mem[sid] = json.loads(raw)
                    n += 1
                except Exception:  # noqa: BLE001
                    continue
            logger.info("session snapshots warmed from redis: %d", n)
            return n
        except Exception:  # noqa: BLE001
            logger.warning("session snapshot warm_from_redis failed", exc_info=True)
            return 0

    # ---- dict 接口（内存为真相源；写后镜像）----
    def __getitem__(self, k: str) -> dict[str, Any]:
        return self._mem[k]

    def __setitem__(self, k: str, v: dict[str, Any]) -> None:
        self._mem[k] = v
        self._mirror(k)

    def __delitem__(self, k: str) -> None:
        self._mem.pop(k, None)
        self._del_async(k)

    def __contains__(self, k: str) -> bool:
        return k in self._mem

    def __iter__(self) -> Iterator[str]:
        return iter(self._mem)

    def __len__(self) -> int:
        return len(self._mem)

    def get(self, k: str, default: Any = None) -> Any:
        return self._mem.get(k, default)

    def setdefault(self, k: str, default: Any = None) -> Any:
        existed = k in self._mem
        v = self._mem.setdefault(k, default)
        if not existed:
            self._mirror(k)
        return v

    def pop(self, k: str, *args: Any) -> Any:
        v = self._mem.pop(k, *args)
        self._del_async(k)
        return v

    def keys(self):  # noqa: ANN201
        return self._mem.keys()

    def values(self):  # noqa: ANN201
        return self._mem.values()

    def items(self):  # noqa: ANN201
        return self._mem.items()

    def clear(self) -> None:
        """清空内存（测试重置用；redis 模式不批量删 Redis）。"""
        self._mem.clear()


# 全局单例（接口与原 dict 兼容；memory 模式行为完全等价 dict）
SESSION_STORE: _SessionSnapshotStore = _SessionSnapshotStore()


def resolve_user_id(
    body_user_id: Optional[str],
    header_user_id: Optional[str],
) -> str:
    """优先级：body.user_id > X-User-Id header > "demo_user"。"""
    for candidate in (body_user_id, header_user_id):
        if candidate and candidate.strip():
            return candidate.strip()
    return "demo_user"
