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
    # ===== Plan-and-Execute critic 闭环（Step 2 新增） =====
    # critic 命中 critical 违规；payload 含完整 violations 列表
    # payload = {"violations": [{"code": str, "severity": str, "message": str, "field_path": str}], "fix_attempt": int}
    CRITIC_VIOLATIONS = "critic_violations"
    # LLM backprompt 修正第 N 次尝试；payload = {"attempt": int, "feedback_text": str}
    CRITIC_FIX_ATTEMPT = "critic_fix_attempt"
    # plan-and-execute 4 级 fallback 链每跳一级；payload = {"from": str, "to": str, "reason": str}
    # 取值约定：from/to ∈ {"llm_first", "llm_backprompt", "ils", "rule", "error", "give_up"}
    PLAN_FALLBACK = "plan_fallback"
    # Agent 思考中间态（可选，用于流式打字效果）
    AGENT_THOUGHT = "agent_thought"
    # 最终方案产出
    ITINERARY_READY = "itinerary_ready"
    # ===== 用户反馈 → 重规划（Phase 0.6 新增；今由 /chat/turn 统一路由的
    # feedback 义务触发 refiner_node 时发出，V1 /chat/refine 端点已退役） =====
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
    # ===== 用户画像副作用（spec algorithm-redesign R5 / TravelAgent 范式） =====
    # narrate 末尾把当前行程写回 user_profile.json 的 recent_trips
    # payload = {"social_context": str, "summary_preview": str, "success": bool, "skipped_reason": str | None}
    # 仅在真实写入或显式跳过时推；幂等命中 / cancel 跳过 / 不可写路径都返 success=false 并附 skipped_reason
    MEMORY_PERSISTED = "memory_persisted"
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
    - CRITIC_VIOLATIONS payload = {"violations": [...], "fix_attempt": int}
    - CRITIC_FIX_ATTEMPT payload = {"attempt": int, "feedback_text": str}
    - PLAN_FALLBACK    payload = {"from": str, "to": str, "reason": str}
    - AGENT_THOUGHT   payload = {"text": str}
    - ITINERARY_READY payload = Itinerary.model_dump()，可选再加一个兄弟字段
      "node_actions": {node_id: {"chips": [NodeChip.model_dump(), ...],
      "alternatives": [AlternativeOption(dataclass) 的字段字典, ...]}}
      （ADR-0013 F-3「节点调整按钮 + 具名备选」；node_id = ActivityNode.
      target_id。"node_actions" 只在至少一个节点有 chips 或 alternatives 时
      才出现——同 "messages" 字段"无内容不加字段"的先例，见下方 AGENT_
      NARRATION 说明。不进 Itinerary schema 本体，是 ITINERARY_READY 顶层
      payload 的兄弟键，由 `agent.graph._emit_handlers.emit_narrate` 组装，
      业务逻辑（chips 生成/备选预验证）在 `agent.graph.nodes.narrate` 完成）
    - REFINEMENT_START payload = {"feedback_text": str}
    - REFINEMENT_DONE  payload = RefinementOutput.model_dump()
    - CHITCHAT_REPLY   payload = RouterDecision.model_dump()
    - AGENT_NARRATION  payload = {"text": str, "stage": "stream" | "confirm",
      "messages": [{"kind": "advisory", "code": str, "text": str}, ...]}
      （"messages" 可选，仅当规划器产出「绝不默默忽略」的结构化告知时出现——
      ADR-0010 D-7 / ADR-0011 决策 5「统一 agent 消息面」；kind 目前恒为
      "advisory"，未来澄清等消息类型复用同一形状/字段）
    - MEMORY_PERSISTED payload = {"social_context": str, "summary_preview": str, "success": bool, "skipped_reason": str | None}
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
