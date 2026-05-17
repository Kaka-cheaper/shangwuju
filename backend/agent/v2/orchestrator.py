"""agent.v2.orchestrator —— 单一对话入口编排器。

解决的根因问题（参考 problem.md 问题 18）：
    旧架构里用户在 dock 直接输入反馈会触发 /chat/stream（全新规划流），
    LLM 看不到「上次 Agent 提议了什么」，把"不要太远"当成新需求重解析。
    本质：缺 conversation_id / message_history 概念。

本模块做的事：
    1. 单一入口 turn_chat()：根据 ConversationState 判断这是「新需求」还是「对上次的反馈」
    2. 自动选择路径：
        - 新输入 → router_v2 路由 → planning 走原 planner_stream / 其他走 chitchat
        - 反馈输入（已有 itinerary） → refiner 路径，把 intent 调整后重规划
    3. 跨 turn 持久化 message_history：用 Pydantic AI 风格的 ModelMessage list
       未来要让 intent_parser/router 看到上下文时，从这里取

向后兼容（重要）：
    - 不替换 main.py 现有的 /chat/stream / /chat/refine / /chat/confirm
    - 这些端点继续工作，但它们会调本模块的 hooks（save_state_after_*）
      让 ConversationStore 里有数据
    - 新增 /chat/turn 端点是「智能版」入口，前端 dock 直接发消息时调它
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from agent.v2.conversation import (
    ConversationState,
    ConversationStore,
    get_default_store,
)
from schemas import IntentExtraction, Itinerary, RouterDecision

logger = logging.getLogger(__name__)


# ============================================================
# Turn 决策：判断这次输入是「新需求」还是「对上次的反馈」
# ============================================================


# 反馈类关键词（轻量启发式；后续可替换成 LLM 分类）
_FEEDBACK_KEYWORDS = [
    "太远", "近一点", "近点", "别走太远", "别太远", "再近",
    "不要", "去掉", "换一个", "换", "改一下", "再想想",
    "不喜欢", "不太行", "不行", "不合适",
    "便宜", "贵", "再贵点", "更高级",
    "公里以内", "km以内", "公里内", "km内",
    "改成", "改为", "调到", "缩短", "延长",
    "时间", "早点", "晚点", "提前", "推迟",
]


def looks_like_feedback(message: str) -> bool:
    """轻量判断这条消息是不是「对已有方案的反馈」。

    判断条件：
    - 含反馈关键词
    - 或形如"X 公里以内 / X 小时" 等明显的"调整指令"

    误判风险：
    - 新需求里也可能含"不要"（如"不要太累"）→ 调用方需结合 ConversationState
      是否有 itinerary_snapshot 一起判断（无 itinerary 即不可能是反馈）
    """
    if not message:
        return False
    txt = message.strip()
    for kw in _FEEDBACK_KEYWORDS:
        if kw in txt:
            return True
    # "X 公里" / "X 小时" 一类的纯调整指令
    if re.search(r"\d+\s*(公里|km|千米|小时|h)", txt, re.IGNORECASE):
        return True
    return False


def decide_turn_kind(
    message: str,
    state: ConversationState,
) -> str:
    """决定这次 turn 走哪条路径。

    Returns:
        "feedback"  → 当前 itinerary 已存在 + message 看着像反馈 → 走 refine 路径
        "fresh"     → 视为新规划请求 → 走 router → planner / chitchat
    """
    if state.itinerary_snapshot is None:
        return "fresh"
    if looks_like_feedback(message):
        return "feedback"
    # 已有 itinerary 但消息不像反馈 —— 例如用户主动说"我想换一个场景"
    # 默认按 fresh 走（重新路由），让 router 决定（也可能 router 判 chitchat）
    return "fresh"


# ============================================================
# 状态写入 hooks（main.py 现有端点调用）
# ============================================================


async def record_planning_result(
    *,
    session_id: str,
    user_id: str,
    intent: IntentExtraction,
    itinerary: Itinerary,
    user_message: str,
    agent_message: str,
    store: Optional[ConversationStore] = None,
) -> ConversationState:
    """planner_stream 跑完后调本函数把状态写入 store。

    把 user_message + agent_message 转成 Pydantic AI 兼容的 ModelMessage 序列，
    后续 turn 调用 LLM 时可作 message_history 喂回。

    设计取舍：
        我们没让旧 intent_parser / planner 真的用 Pydantic AI Agent 跑，
        所以这里手工构造 ModelMessage（user_msg + agent_text_response）。
        如果后续 narrator 用 Pydantic AI，可让其 result.all_messages() 直接
        merge 进 state.messages。
    """
    s = store or get_default_store()
    state = await s.get_or_create(session_id, user_id=user_id)
    state.user_id = user_id
    state.intent_snapshot = intent.model_dump()
    state.itinerary_snapshot = itinerary.model_dump()

    # 追加这次 turn 的对话到 message_history
    # 用 Pydantic AI 的 ModelRequest/ModelResponse 而不是 raw dict，
    # 这样以后任何 v2 Agent 调 message_history= 都能直接消费
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    state.messages.append(
        ModelRequest(parts=[UserPromptPart(content=user_message)])
    )
    state.messages.append(
        ModelResponse(parts=[TextPart(content=agent_message)])
    )

    await s.save(state)
    return state


async def record_refinement_result(
    *,
    session_id: str,
    user_id: str,
    refined_intent: IntentExtraction,
    new_itinerary: Itinerary,
    feedback_text: str,
    agent_message: str,
    store: Optional[ConversationStore] = None,
) -> ConversationState:
    """refine_stream 跑完后调本函数。

    与 record_planning_result 类似，但 user_message 包了「（反馈）」前缀，
    便于上层在 message_history 里做语义分流。
    """
    s = store or get_default_store()
    state = await s.get_or_create(session_id, user_id=user_id)
    state.user_id = user_id
    state.intent_snapshot = refined_intent.model_dump()
    state.itinerary_snapshot = new_itinerary.model_dump()

    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    user_with_marker = f"（反馈）{feedback_text}" if feedback_text else "（反馈）"
    state.messages.append(
        ModelRequest(parts=[UserPromptPart(content=user_with_marker)])
    )
    state.messages.append(
        ModelResponse(parts=[TextPart(content=agent_message)])
    )

    await s.save(state)
    return state


async def record_confirm_result(
    *,
    session_id: str,
    user_id: str,
    final_itinerary: Itinerary,
    agent_message: str,
    store: Optional[ConversationStore] = None,
) -> ConversationState:
    """confirm_stream 跑完后调（用户已下单）。

    更新 itinerary_snapshot（含 orders + share_message），
    并追加一条 agent confirmation 消息。
    """
    s = store or get_default_store()
    state = await s.get_or_create(session_id, user_id=user_id)
    state.itinerary_snapshot = final_itinerary.model_dump()

    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    state.messages.append(
        ModelRequest(parts=[UserPromptPart(content="（确认下单）")])
    )
    state.messages.append(
        ModelResponse(parts=[TextPart(content=agent_message)])
    )

    await s.save(state)
    return state


async def record_chitchat_result(
    *,
    session_id: str,
    user_id: str,
    user_message: str,
    decision: RouterDecision,
    store: Optional[ConversationStore] = None,
) -> ConversationState:
    """router 判 chitchat / meta / emotional 等非 planning 后的状态写入。

    这种 turn 不更新 itinerary_snapshot，但把对话写进 messages。
    """
    s = store or get_default_store()
    state = await s.get_or_create(session_id, user_id=user_id)

    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    state.messages.append(
        ModelRequest(parts=[UserPromptPart(content=user_message)])
    )
    state.messages.append(
        ModelResponse(parts=[TextPart(content=decision.reply_text)])
    )

    await s.save(state)
    return state


# ============================================================
# 上下文增强：让 intent_parser 看到历史
# ============================================================


def enhance_message_with_context(
    new_message: str,
    state: ConversationState,
    *,
    max_history_chars: int = 800,
) -> str:
    """把 new_message 拼上压缩的 message_history 喂回 LLM。

    用法：
        当 turn_kind=="feedback" 时，refiner 看到的不仅是反馈本身，
        还包括"上次 Agent 提议了什么"。这样它能正确理解"换近一点的"
        是相对哪个 baseline 在调整。

    实现：
        把 state.messages 的最后 N 轮（user + agent）拼成纯文本前缀，
        加在新消息前面。max_history_chars 控制总长度避免吃 token。

    Returns:
        增强后的消息：「【上次对话】... \n\n 【本次反馈】new_message」
    """
    if not state.messages:
        return new_message

    parts: list[str] = []
    total_chars = 0
    # 反向遍历最近的对话
    for msg in reversed(state.messages[-6:]):  # 最多 6 条 = 3 轮
        # 取 ModelRequest/ModelResponse 的文本内容
        text = _extract_msg_text(msg)
        if not text:
            continue
        role = "用户" if "Request" in type(msg).__name__ else "Agent"
        line = f"{role}：{text}"
        if total_chars + len(line) > max_history_chars:
            break
        parts.insert(0, line)
        total_chars += len(line)

    if not parts:
        return new_message

    history_block = "\n".join(parts)
    return (
        f"【上次对话】\n{history_block}\n\n"
        f"【本次输入】{new_message}"
    )


def _extract_msg_text(msg: Any) -> str:
    """从 ModelRequest/ModelResponse 抽取文本内容（容错）。"""
    try:
        parts = getattr(msg, "parts", None) or []
        for p in parts:
            content = getattr(p, "content", None)
            if isinstance(content, str) and content:
                return content
    except Exception:  # noqa: BLE001
        pass
    return ""


__all__ = [
    "looks_like_feedback",
    "decide_turn_kind",
    "record_planning_result",
    "record_refinement_result",
    "record_confirm_result",
    "record_chitchat_result",
    "enhance_message_with_context",
]
