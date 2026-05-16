"""tools.generate_share_message —— T7 生成可一键复制的转发文案。

输入/输出：GenerateShareMessageInput / GenerateShareMessageOutput
失败分支：
- INVALID_INPUT：itinerary_summary 为空白（已被 schema 兜住，这里再防一次）

设计说明：
- 评分项「转发文案 + 调性匹配」靠这个 Tool。家庭口吻 vs 商务口吻通过模板差异体现。
- 不调用 LLM——评委关心 Agent 编排，单一文案 LLM 化反而引入不确定性。
- 模板按 social_context 分支，输出格式固定为「开场 + 行程 + 落点 + 收尾」四段。
"""

from __future__ import annotations

from schemas.errors import FailureReason
from schemas.tags import SocialContext
from schemas.tools import GenerateShareMessageInput, GenerateShareMessageOutput

from .registry import register_tool


_DESC = (
    "把行程摘要包装成对应社交语境的转发文案（家庭口吻 / 商务口吻 / 闺蜜亲昵 等）。"
    "audience 字段控制称呼，如 妻子 / 朋友群 / 客户。"
)


# (开场, 收尾)
_TEMPLATES: dict[str, tuple[str, str]] = {
    "家庭日常": ("亲爱的，", "出门记得带水壶哦~"),
    "老人伴助": ("爸/妈，", "您慢慢走，时间充裕。"),
    "闺蜜聊天": ("姐妹，", "记得带相机！"),
    "朋友热闹": ("兄弟姐妹们，", "下午集合，不见不散。"),
    "情侣亲密": ("亲爱的，", "等你下班一起走。"),
    "商务接待": ("您好，", "期待与您见面，如有调整请提前告知。"),
    "同学重聚": ("老同学们，", "好久不见，今天叙叙旧。"),
    "独处放空": ("（给自己）", "今天就好好放空，电话静音。"),
    "纪念日仪式感": ("家人/亲爱的，", "今天值得纪念，已为你准备了惊喜。"),
}


def _build_message(
    summary: str, ctx: SocialContext, audience: str | None
) -> str:
    opener, closer = _TEMPLATES.get(ctx, ("Hi，", ""))
    audience_part = f"{audience}：" if audience else ""
    body = f"今天的安排是这样的：{summary}"
    return f"{audience_part}{opener}{body}\n{closer}".strip()


@register_tool(
    name="generate_share_message",
    description=_DESC,
    input_model=GenerateShareMessageInput,
    output_model=GenerateShareMessageOutput,
)
def generate_share_message(
    inp: GenerateShareMessageInput,
) -> GenerateShareMessageOutput:
    summary = inp.itinerary_summary.strip()
    if not summary:
        return GenerateShareMessageOutput(
            success=False,
            reason=FailureReason.INVALID_INPUT,
            message=None,
        )
    return GenerateShareMessageOutput(
        success=True,
        message=_build_message(summary, inp.social_context, inp.audience),
    )
