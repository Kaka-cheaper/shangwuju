"""会话快照 + user_id 解析（demo 级 in-memory）。

- `SESSION_STORE`：session_id → {"intent": IntentExtraction dict, "itinerary": Itinerary dict, ...}
- `resolve_user_id`：body.user_id > X-User-Id header > "demo_user"

真上线时 SESSION_STORE 切换为 Redis（参考 backend/agent/v2/conversation.py 抽象层）。
"""

from __future__ import annotations

from typing import Any, Optional

# session_id -> {"intent": ..., "itinerary": ...}（demo 级 in-memory）
SESSION_STORE: dict[str, dict[str, Any]] = {}


def resolve_user_id(
    body_user_id: Optional[str],
    header_user_id: Optional[str],
) -> str:
    """优先级：body.user_id > X-User-Id header > "demo_user"。"""
    for candidate in (body_user_id, header_user_id):
        if candidate and candidate.strip():
            return candidate.strip()
    return "demo_user"
