"""agent.graph.state —— LangGraph 全局 State schema。

LangGraph 的 State 是节点间共享的"工作内存"——每个节点接受 State，返回 State diff，
框架自动 merge。本 State 复用 v2 已有的 IntentExtraction / Itinerary / Violation 等
schema，不发明新结构。

字段命名规范：
- 数据型字段（intent / itinerary / blueprint）用名词
- 状态型字段（retry_count / route_decision）用动作-结果合成名
- 流式事件型字段（sse_events）由 sse_adapter 消费
- 业务快照（messages 含 ModelMessage list）在节点间持久化（Phase 12 接 InMemorySaver）

不负责：
- LangGraph SDK 的 messages reducer（用框架默认 add_messages）
- SSE 事件序列化（在 sse_adapter.py）

【spec planning-quality-deep-review R6+R7（Task 6 + Agent H P2-H8）】
- 删除已死的 routes: list[Any] 字段（execute.py 未填、其他节点未消费；
  routes.json 真值 lookup_hop / assemble 内部直接调，state 层不做缓存）
- 新增 quality_issues: list[Any] 字段，承载 narrator 主动质疑信号
  （目前由 narrate_node 内部计算，refiner_node 在反馈合并时重置）
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

# 业务领域类型（保留所有 v1 已有 schema）
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary
from schemas.router import RouterDecision

# 蓝图（plan）层
from agent.blueprint import PlanBlueprint
from agent.weights_llm import PlanningWeights

# Critic 层（v2 critic 兼容；旧 critics 仍由 hybrid 内部使用）
try:
    from agent.v2.critics_v2 import Violation as CriticViolation
except ImportError:
    CriticViolation = Any  # type: ignore[misc, assignment]


# ============================================================
# 路由结果（router_node 输出）
# ============================================================

RouteKind = Literal[
    "planning",   # 进 intent → planner → execute 主路径
    "chitchat",   # 闲聊回话直接出
    "meta",       # 元能力问答（你能做什么）
    "emotional",  # 情绪共情
    "off_topic",  # 范围外礼貌拒答
    "ambiguous",  # 输入歧义需要反问
    "feedback",   # 对已有方案的反馈（走 refiner）
]


# ============================================================
# Replan 决策类型
# ============================================================

ReplanStrategy = Literal[
    "llm_backprompt",   # 让 LLM 重出 plan（≤2 次）
    "ils_fallback",     # 转 hybrid ILS 算法兜底
    "give_up",          # 兜底失败，rule planner 兜
]


# ============================================================
# 主 State
# ============================================================

class AgentState(TypedDict, total=False):
    """LangGraph 全局 State。

    所有字段都是可选的（total=False）；节点按需读写自己关心的字段。
    """

    # ---- 输入与会话身份 ----
    user_input: str
    user_id: str
    session_id: str
    scenario_id: Optional[str]

    # ---- 跨 turn 消息历史（Pydantic AI / LangGraph 标准） ----
    messages: Annotated[list[BaseMessage], add_messages]

    # ---- 路由结果 ----
    router_decision: Optional[RouterDecision]
    route_kind: Optional[RouteKind]

    # ---- 意图层 ----
    intent: Optional[IntentExtraction]
    intent_overrides: Optional[dict[str, Any]]  # refiner 增量覆盖

    # ---- 候选数据（execute 阶段并行写入）----
    pois: list[Any]            # list[Poi] —— 用 Any 避开 TypedDict 泛型限制
    restaurants: list[Any]      # list[Restaurant]
    user_profile: Optional[Any]  # GetUserProfileOutput
    # Step 6：tag relaxation 路径（split per worker 避免 reduce 冲突）
    pois_relaxed_tags: list[str]
    restaurants_relaxed_tags: list[str]

    # ---- Plan 层（LLM-First） ----
    weights: Optional[PlanningWeights]
    blueprint: Optional[PlanBlueprint]
    plan_attempt: int           # planner 跑的次数（含重试）

    # ---- Itinerary（assemble 阶段产出） ----
    itinerary: Optional[Itinerary]

    # ---- Critic 反馈 ----
    violations: list[CriticViolation]
    has_critical: bool
    critic_feedback_text: Optional[str]  # backprompt 用的格式化文本

    # ---- Replan ----
    retry_count: int
    replan_strategy: Optional[ReplanStrategy]

    # ---- Decision trace（Step 4+7：决策可解释性） ----
    decision_trace: Optional[Any]  # DecisionTrace；用 Any 避循环 import
    fallback_chain: list[Any]      # list[FallbackHop]
    critic_attempts: list[Any]     # list[CriticAttempt]
    alternatives: list[Any]        # list[AlternativeCandidate]
    quality_issues: list[Any]      # list[str]：narrator 主动质疑信号（spec R6）

    # ---- 暖语气 ----
    narration: Optional[str]

    # ---- HITL（interrupt 后等三按钮）----
    user_decision: Optional[Literal["confirm", "refine", "cancel"]]
    refine_feedback: Optional[str]
    orders: list[Any]           # list[Order]
    share_message: Optional[str]

    # ---- chitchat 输出（非 planning 路径用）----
    chitchat_reply_text: Optional[str]
    chitchat_tone: Optional[str]
    chitchat_chips: list[Any]    # list[CtaChip]


# ============================================================
# Helper：State 工厂（给入口与测试用）
# ============================================================

def make_initial_state(
    *,
    user_input: str,
    user_id: str = "demo_user",
    session_id: str = "sess_default",
    scenario_id: Optional[str] = None,
) -> AgentState:
    """构造干净的 AgentState（list 字段都给空数组，避免 None 报错）。"""
    return AgentState(
        user_input=user_input,
        user_id=user_id,
        session_id=session_id,
        scenario_id=scenario_id,
        messages=[],
        pois=[],
        restaurants=[],
        violations=[],
        has_critical=False,
        plan_attempt=0,
        retry_count=0,
        orders=[],
        chitchat_chips=[],
        fallback_chain=[],
        critic_attempts=[],
        alternatives=[],
        quality_issues=[],
        pois_relaxed_tags=[],
        restaurants_relaxed_tags=[],
    )
