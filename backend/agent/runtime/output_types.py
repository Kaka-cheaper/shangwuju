"""output_types —— ReAct 单一 Agent 的二选一输出契约。

Pydantic AI 的 `output_type` 接受 `Union[A, B]` —— LLM 在生成最终回复时
自己决定走哪个分支：

- 不调工具 / 仅闲聊 / 元能力问答 / 拒答 → ``ChatResponse``
- 调用 N 个工具完成完整规划 → ``ItineraryResponse``

设计原则：
- 两个模型都开 ``extra="forbid"``，防 LLM 漂出未预期字段
- 字段 description 用「中文 + 英文括号补充」双语形式 ——
  让 MiMo / GPT 类模型在生成 OpenAI Function 参数时都能稳抽
- 长度限制（min_length / max_length）卡在合理范围，
  防 LLM 把 200 字 narration 写成 5 行小作文或一行水货

不负责：
- LLM 调用 / Agent 编排（在 react_agent.py）
- 业务约束校验（在 critics_v2 / output_validator）
- Itinerary schema 本身（在 schemas.itinerary，A 已定型，禁动）
"""

from __future__ import annotations

from typing import Union

from pydantic import BaseModel, ConfigDict, Field

from schemas.itinerary import Itinerary


class ChatResponse(BaseModel):
    """LLM 决定不输出行程时的回话（chitchat / meta / Q&A / 拒答类）。

    适用场景：
    - 用户闲聊、问候、自我介绍 → 暖心回话
    - 用户问元能力（"你能做什么 / 你支持哪些场景"）→ 介绍 + suggestions
    - 用户问 POI / 餐厅细节（"P004 适合 5 岁吗"）→ 调工具后用此回答
    - 用户问范围外（写代码 / 数学题 / 八卦）→ 礼貌拒答 + 引导回主线
    - 用户输入歧义 / 缺约束 → 反问澄清
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        ...,
        min_length=1,
        max_length=600,
        description=(
            "给用户的中文回话（reply text）；80-200 字最佳。"
            "必须中文，避免英文/拼音；语气随场景：闲聊 warm、元能力 neutral、"
            "情绪共情 empathetic、拒答 playful。"
        ),
    )
    suggestions: list[str] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "可选的引导短语列表（suggestion chips，前端可作 chip 渲染）；"
            "每条 ≤ 24 字中文短语，例如「带娃放电」「一个人放空」。"
            "planning 主路径时通常为空数组 []。"
        ),
    )


class ItineraryResponse(BaseModel):
    """LLM 完成完整规划后的行程方案。

    规则（critic 会强校验）：
    - itinerary.nodes 首尾固定为 home（虚拟节点 duration_min=0），中间节点 ≥ 1
    - 中间节点 target_kind ∈ {poi, restaurant}，至少应包含主活动 / 用餐其中之一
      （`kind` 字段是中文标签：主活动 / 用餐 / 夜宵 / 自由 等）
    - itinerary.hops 长度恒等于 nodes - 1，每条 hop 的 minutes / mode / path_type
      由系统按 routes.json 自动计算，LLM 不需要也不要去构造
    - itinerary.schedule 是派生只读视图，由后端 assemble 阶段填充
    - orders 仅在用户已确认「下单」后才填；规划阶段保持空数组
    """

    model_config = ConfigDict(extra="forbid")

    itinerary: Itinerary = Field(
        ...,
        description=(
            "完整行程（complete itinerary，schema_version='edge_v1'）：包含三个数组——"
            "`nodes`（活动节点，首尾固定 home，中间节点 ≥ 1）、`hops`（相邻节点之间的通勤段，"
            "长度 = nodes - 1）、`schedule`（按时间序展平的派生只读视图，前端时间轴消费）。"
            "另含可选 orders + 可选 share_message + total_minutes。"
            "orders 在规划阶段必须为空数组（[]）——下单由 reserve_restaurant / buy_ticket / order_extra_service "
            "工具完成后由后端追加，LLM 不要假装已下单。"
        ),
    )
    narration: str = Field(
        ...,
        min_length=20,
        max_length=320,
        description=(
            "暖语气导游开场白（warm narration）；80-200 字最佳。"
            "直接称呼「你」（不用「您」/「用户」），1-3 个自然句，"
            "包含时长、几个主要时间锚点、主活动、关键预约，最后一句邀请反馈。"
            "禁用「已为您规划：」「方案如下：」等公文开头；禁用 POI / 候选 / Tag 等专业词。"
        ),
    )


# Pydantic AI output_type 接受 Union[ChatResponse, ItineraryResponse]，
# 运行时框架会让 LLM 自己选一个分支输出。
AgentOutput = Union[ChatResponse, ItineraryResponse]


__all__ = [
    "ChatResponse",
    "ItineraryResponse",
    "AgentOutput",
]
