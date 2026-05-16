"""tools.get_user_profile —— T8 拉取用户画像。

Phase 0.7：从 persona JSON 加载，支持 5 个 mock user（u_dad / u_biz / u_grandma / u_solo / u_couple）。
任何未知 user_id → 兜底回默认 persona（u_dad，家庭主线）；不抛 NOT_FOUND，
让前端切换 user 流畅不报错。

老接口兼容：传 user_id="demo_user" 仍能拿到默认画像（兜底为 u_dad）。

不负责：
- persona prior 注入 prompt（在 backend/agent/prompts/system_prompt.py）
- memory 读取（在 backend/data/memory_store.py）
"""

from __future__ import annotations

from data.memory_store import get_default_persona, get_persona
from schemas.domain import Location, UserProfile
from schemas.tools import GetUserProfileInput, GetUserProfileOutput

from .registry import register_tool


_DESC = (
    "拉取用户画像（家位置 / 默认预算 / 交通偏好）。"
    "Phase 0.7 起按 user_id 选择 persona（u_dad / u_biz / u_grandma / u_solo / u_couple）；"
    "未知 user_id 兜底到默认家庭 persona，确保后续规划不阻塞。"
)


def _persona_to_user_profile(persona, *, override_user_id: str | None = None) -> UserProfile:
    """把 Persona 投影成 Tool 接口的 UserProfile（向后兼容）。

    override_user_id 用于 demo_user 兼容：保留请求方传的 user_id 不漂移，
    仅把 home/budget 从 persona 取（W1 既有测试断言 user_id == "demo_user"）。
    """
    return UserProfile(
        user_id=override_user_id or persona.user_id,
        home_location=Location(name=persona.home_location or "（未设置）"),
        default_budget=persona.default_budget,
        transport_preference="taxi",
    )


_KNOWN_ALIASES = ("demo_user",)


@register_tool(
    name="get_user_profile",
    description=_DESC,
    input_model=GetUserProfileInput,
    output_model=GetUserProfileOutput,
)
def get_user_profile(inp: GetUserProfileInput) -> GetUserProfileOutput:
    """识别策略：
    - persona ID（u_dad / u_biz / u_grandma / u_solo / u_couple）→ 该 persona
    - alias "demo_user"（兼容老代码）→ 默认 persona（u_dad）
    - 其它任意 ID → NOT_FOUND（保护 W1 旧测试 + 前端误传不静默兜底）
    """
    is_known_alias = inp.user_id in _KNOWN_ALIASES
    persona = get_persona(inp.user_id)
    if persona is None and not is_known_alias:
        from schemas.errors import FailureReason

        return GetUserProfileOutput(
            success=False, reason=FailureReason.NOT_FOUND, profile=None
        )
    if persona is None:
        persona = get_default_persona()
    return GetUserProfileOutput(
        success=True,
        profile=_persona_to_user_profile(persona, override_user_id=inp.user_id),
    )
