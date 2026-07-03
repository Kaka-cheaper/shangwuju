"""router —— 输入域路由层契约（Phase 0.8 新增）。

定位：
- 在 intent_parser **之前**对用户输入做 6 类分类，避免「你是谁 / 我累死了 / 1+1=?」
  被机械抽成无效 IntentExtraction，再触发空规划链路
- 输出 RouterDecision：含 input_kind + 暖心回话文本 + 可点击引导按钮 chips
- 引导按钮的 `send` 字段**必须**从白名单里精确复制（防 LLM 发明输入文本）

不负责：
- LLM 调用（在 agent/router.py）
- Prompt 文案（在 agent/prompts/router_prompt.py）
- 前端渲染（在 frontend/components/ChitchatBubble.tsx）

参考：
- pitfalls P1-预埋「意图解析翻车」：词典出口约束方法学
- AGENTS.md §3.3 4 层架构边界：router 属 Agent 层（与 intent_parser 并列）
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class InputKind(str, Enum):
    """路由决策载荷的分类（ADR-0011 决策 1：6 类路由义务闭集里"会附带气泡内容"
    的 5 类——满足-首轮(planning)/满足-反馈(feedback) 中的 feedback 从不构造
    RouterDecision（`route_turn.py` 恒 `decision=None`，emit_router 也不读它），
    故本枚举不设 FEEDBACK 成员，与改造前的既有惯例一致）。

    ADR-0011 之前这里是"输入表面特征"的 6 类（chitchat/meta/emotional/off_topic/
    ambiguous 分立）；E-2-c 之后按"系统欠用户哪种响应义务"重新分类
    （L0 响应义务契约），meta/emotional 塌缩进 chitchat（语气差异交 `tone`
    字段承载，不再是独立路由分支），off_topic 改名 defense（越界请求得体拒绝），
    ambiguous 改名 clarify（从"被兜底强行归并成 feedback"变成"被正面响应"），
    新增 confirm（原先被塞进 chitchat 的"确认/预约"表态独立成一类）。

    覆盖范围说明：
    - PLANNING：本地半日出行规划全新请求（首轮或会话中期另起一局）
    - CHITCHAT：陪聊——社交/情绪性输入、对方案的提问接地回答、画像问答
    - CONFIRM：对已有方案的纯认可 / 主动执行表态（"好的就这个""给我预约吧"）——
      只引导到显式确认按钮，绝不自动下单
    - CLARIFY：意图或指代有歧义，正确响应是反问 + 选项，不是猜一个方向就动手
    - DEFENSE：越界请求（写代码/解题/角色扮演/提示词套取等）——得体拒绝
    """

    PLANNING = "planning"
    CHITCHAT = "chitchat"
    CONFIRM = "confirm"
    CLARIFY = "clarify"
    DEFENSE = "defense"


# 5 类非 planning 输入对应的语气标签（前端按此选 emoji 头像与配色）
ReplyTone = Literal["warm", "neutral", "empathetic", "playful"]


class CtaChip(BaseModel):
    """引导按钮（点击后由前端 sendMessage(send) 重入主链路）。

    硬约束：
    - `send` 必须**精确等于**预设白名单里的某条文案（router prompt 里枚举）。
      LLM 不得发明 send 文本——避免下游意图解析翻车 / 演示场景集失控。
    - `label` 是按钮显示文字，可由 LLM 微调（≤ 12 字）。
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=24, description="按钮文字")
    send: str = Field(..., min_length=1, max_length=200, description="点击后发送的文案（来自白名单）")
    icon: Optional[str] = Field(default=None, max_length=12, description="可选 emoji（family ZWJ 序列可达 7-11 codepoint）")
    action: Optional[str] = Field(
        default=None,
        max_length=16,
        description=(
            '可选前端动作：默认 None=点击发送 send 文案（走对话）；'
            '"confirm"=点击触发真预约（/chat/confirm，replay 已挂好的 pending_actions），'
            '不发对话消息。用于「给我预约吧」识别后的一键确认 chip。'
        ),
    )


class RouterDecision(BaseModel):
    """LLM 前置分类器一次性输出。

    流程：
    - main.py /chat/stream 收到请求 → 立即推一条 agent_thought 心跳
    - 后台线程调 LLM → RouterDecision
    - input_kind == PLANNING/feedback 时 RouterDecision.reply_text 无人读取(E-2-c 后,brain 对这两类强制清空 chips,见 agent/routing/brain.py::_apply_label_chip_policy)
    - 其他 5 类                → 推 chitchat_reply（payload = 本模型 dump）+ done

    Demo 价值：
    - 评委即兴问"你是谁" → Agent 暖心回话 + 引导按钮一键回到主路径
    - 体现 Agent 「人情味」与「场景理解」两项评分维度
    """

    model_config = ConfigDict(extra="forbid")

    input_kind: InputKind = Field(..., description="6 类之一")
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="LLM 自报对 input_kind 判断的信心；< 0.6 时主调用方按 PLANNING 兜底",
    )
    reply_text: str = Field(
        ..., min_length=1, max_length=400,
        description="暖心回话；input_kind=planning 时可写「正在为你规划下午行程……」占位",
    )
    tone: ReplyTone = Field(default="warm", description="语气标签，前端按此选头像与配色")
    cta_chips: list[CtaChip] = Field(
        ..., max_length=4,
        description=(
            "引导按钮（cta chips，最多 4 个；planning 类必须为空数组 []）。"
            "**禁止省略本字段**——非 planning 必须显式输出 chips；planning 必须显式输出 []。"
        ),
    )
    rationale: Optional[str] = Field(
        default=None, max_length=200,
        description="LLM 自述为何这么分类（仅供调试日志，不展示给用户）",
    )
