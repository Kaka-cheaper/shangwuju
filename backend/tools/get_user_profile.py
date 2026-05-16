"""tools.get_user_profile —— T8 拉取硬编码用户画像。

输入/输出：GetUserProfileInput / GetUserProfileOutput
失败分支：
- NOT_FOUND：user_id 与 mock 中 demo_user 不一致

Demo 阶段只有一个 demo_user（home_location / 默认预算 / 交通偏好）。
Agent 在每个对话开头都会调用这个 Tool 取 home，再把 home 当 from_location 传给
estimate_route_time。
"""

from __future__ import annotations

from data.loader import load_user_profile
from schemas.errors import FailureReason
from schemas.tools import GetUserProfileInput, GetUserProfileOutput

from .registry import register_tool


_DESC = (
    "拉取用户画像（家位置 / 默认预算 / 交通偏好）。Demo 阶段只识别 user_id=demo_user，"
    "其它 id 返 reason=not_found。"
)


@register_tool(
    name="get_user_profile",
    description=_DESC,
    input_model=GetUserProfileInput,
    output_model=GetUserProfileOutput,
)
def get_user_profile(inp: GetUserProfileInput) -> GetUserProfileOutput:
    profile = load_user_profile()
    if inp.user_id != profile.user_id:
        return GetUserProfileOutput(
            success=False,
            reason=FailureReason.NOT_FOUND,
            profile=None,
        )
    return GetUserProfileOutput(success=True, profile=profile)
