"""agent.v2.deps —— Pydantic AI Agent 依赖注入容器。

每个 v2 Agent 通过 RunContext[AgentDeps] 拿到这些依赖：
- user_id：当前会话用户（驱动 persona / memory prior）
- planner_mode："llm" | "rule"，控制 planner 走 LLM Function Calling 还是规则化
- tracer：把工具调用 / 中间过程推到 SSE（前端 Agent 思考链路面板需要）

为什么用 dataclass 而非 Pydantic：
- Pydantic AI 推荐 deps 用普通 dataclass / object（避免序列化开销）
- 我们这里包含 callable / tracer 等不可序列化字段
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# 重用旧的 Tracer（agent/trace.py），它已经被 main.py 消费
# 旧 Tracer 接口：tracer.emit(type_str, payload_dict) → 同步推一条 TraceRecord
# 后续可能会迁到 Pydantic AI 的 message stream，但保持向后兼容


@dataclass
class AgentDeps:
    """v2 Agent 通用依赖。

    所有 v2 Agent 都通过 `Agent(deps_type=AgentDeps)` 接受此依赖；
    在 `@agent.tool` 内通过 `ctx.deps.user_id` 等访问。
    """

    # ---- 必填 ----
    user_id: str
    """当前 user_id（demo_user 兜底）。"""

    # ---- 可选 ----
    planner_mode: str = "llm"
    """规划策略：'llm'=LLM Function Calling 自主决策；'rule'=规则化 planner（demo 安全网）。"""

    tracer: Optional[Any] = None
    """旧 agent.trace.Tracer 实例，用于把 Tool 调用进/出推给前端。
    None 时表示不追踪（单测 / 静默调用）。"""

    session_id: str = ""
    """对话 session id，用于日志关联。"""

    extra: dict[str, Any] = field(default_factory=dict)
    """业务扩展字段（如 refine 路径需要的 original_intent）。"""


__all__ = ["AgentDeps"]
