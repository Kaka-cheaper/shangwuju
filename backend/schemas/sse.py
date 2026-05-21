"""sse —— /chat/stream 的 SSE 事件类型与 payload。

后端 Agent 在 ReAct 循环中会推送以下事件，前端 EventSource 实时消费用于
渲染「Tool 调用链路可视化」（评委加分项）。

不负责：
- SSE 传输（在 backend/main.py + sse-starlette）。
- 前端渲染（在 frontend/）。
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SseEventType(str, Enum):
    """SSE 事件类型枚举。前端按此值切换渲染分支。"""

    # 意图解析阶段
    INTENT_PARSED = "intent_parsed"
    # 单个 Tool 调用开始
    TOOL_CALL_START = "tool_call_start"
    # 单个 Tool 调用完成
    TOOL_CALL_END = "tool_call_end"
    # 异常重规划被触发
    REPLAN_TRIGGERED = "replan_triggered"
    # Agent 思考中间态（可选，用于流式打字效果）
    AGENT_THOUGHT = "agent_thought"
    # 最终方案产出
    ITINERARY_READY = "itinerary_ready"
    # ===== 用户反馈 → 重规划（/chat/refine 专用，Phase 0.6 新增） =====
    # refiner 开始合并反馈
    REFINEMENT_START = "refinement_start"
    # refiner 合并完毕，下游进入完整 plan 流程；payload = RefinementOutput.model_dump()
    REFINEMENT_DONE = "refinement_done"
    # ===== 输入域路由（Phase 0.8 新增） =====
    # 非 planning 输入（chitchat / meta / emotional / off_topic / ambiguous）
    # 的 Agent 暖心回话气泡；payload = RouterDecision.model_dump()
    CHITCHAT_REPLY = "chitchat_reply"
    # ===== Agent 暖心开场白（行程出炉时 / confirm 后） =====
    # payload = {"text": str, "stage": "stream" | "confirm"}
    AGENT_NARRATION = "agent_narration"
    # 错误（区别于 Tool 内部失败：这是流终止）
    STREAM_ERROR = "stream_error"
    # 流结束
    DONE = "done"


class SseEvent(BaseModel):
    """SSE 单条事件包装。

    payload 用 dict[str, Any] 而非具体 BaseModel，
    是因为不同 type 的 payload 形状不同——前端按 type 自行解构。

    约定（字段对应 type）：
    - INTENT_PARSED   payload = IntentExtraction.model_dump()
    - TOOL_CALL_START payload = {"tool": str, "input": dict}
    - TOOL_CALL_END   payload = {"tool": str, "output": dict, "duration_ms": int}
    - REPLAN_TRIGGERED payload = {"reason": FailureReason.value, "from_tool": str}
    - AGENT_THOUGHT   payload = {"text": str}
    - ITINERARY_READY payload = Itinerary.model_dump()
    - REFINEMENT_START payload = {"feedback_text": str}
    - REFINEMENT_DONE  payload = RefinementOutput.model_dump()
    - CHITCHAT_REPLY   payload = RouterDecision.model_dump()
    - AGENT_NARRATION  payload = {"text": str, "stage": "stream" | "confirm"}
    - STREAM_ERROR    payload = {"reason": str, "detail": str}
    - DONE            payload = {}
    """

    model_config = ConfigDict(extra="forbid")

    type: SseEventType
    seq: int = Field(..., ge=0, description="单次会话内单调递增的序号")
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp_ms: Optional[int] = Field(
        default=None, description="服务端时间戳（ms）；可选，便于前端调试"
    )
