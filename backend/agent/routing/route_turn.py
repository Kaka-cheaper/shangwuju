"""agent.routing.route_turn —— 纯函数路由主干（V3 抽取，T2；ADR-0011 决策 2 / E-1 精修）。

把 router_node 的整条级联从 graph 层抽到 routing bounded context。
graph/nodes/router.py 退薄为 adapter，体内只剩"调 route_turn → 展平为 dict"。

禁止：本模块不得 import agent/graph/*（否则 routing→graph 成环）。

级联层次（ADR-0011「一脑三壳」E-1 收口后）：
    Layer 0  壳1·注入检测（不调 LLM）→ RouteOutcome(off_topic, SafeRefusalDecision)
    壳2      canonical 字面短路（不调 LLM）→ RouteOutcome(planning|feedback|chitchat, ...)
             取代旧 Layer 1.5 的规划信号表 fast path（词表已删，见 ADR-0011 决策 2）；
             先于 Layer 1——系统发出的精确全串确定性高于启发式信号（深审修正）。
    Layer 1  强信号反馈（不调 LLM）→ RouteOutcome(feedback, None)
    Layer 1.7 用户画像问答（规则，不调 LLM）→ RouteOutcome(chitchat, PersonaDecision)
    Layer 2  LLM 分类（带 has_itinerary 上下文）；异常/失败 → 壳3 保守地板
             （fallback_decision：无方案→陪聊引导，有方案→澄清引导，绝不 PLANNING）
    Layer 3  会话内对话行为统一判定（classify_dialogue_act → act→RouteKind 映射）

    旧"兜底归并"（has_itinerary + planning/ambiguous → 强制 feedback）已删除
    （ADR-0011 决策 2：没有任何下游会"问"，实测「我不想玩这个了」被硬猜重规划）。
    删除后 ambiguous 走 chitchat 气泡通道（emit_router 对非 planning/feedback kind
    一律推 CHITCHAT_REPLY），planning 在会话中期也能触达 intent 路径（ADR-0012
    背景 5 描述的"会话中期新需求"场景，intent_node 已收口 episode 字段重置）。
"""

from __future__ import annotations

import logging
from typing import Any

# ── 路由基础设施（routing 包内，无环）
from agent.routing.kinds import RouteKind
from agent.routing.outcome import RouteOutcome
from agent.routing.canonical_shortcut import canonical_shortcut_decision

# ── core 层（routing 依赖 core，不依赖 graph）
from agent.core.feedback_detector import looks_like_feedback_strong
from agent.core.injection_detector import detect_injection

# ── 分类器 + fallback（agent.intent 层）
from agent.intent.router import classify_input, fallback_decision

# ── schemas（无环）
from schemas.router import CtaChip, InputKind, RouterDecision

# ── 画像问答（core 层，无环）
from agent.core.persona_qa import build_persona_decision

# ── 对话行为分类（core 层；act 枚举解耦 RouteKind，映射在本模块持有）
from agent.core.dialogue_acts import DialogueAct, classify_dialogue_act

# ── 安全婉拒 prompt（agent.intent.prompts，无环）
from agent.intent.prompts.router_prompt import PRIMARY_CTAS

logger = logging.getLogger("agent.routing.route_turn")


# ============================================================
# 内部 helper（从 router.py 原样搬入）
# ============================================================

def _safe_refusal_decision() -> RouterDecision:
    """命中注入时的固定安全婉拒 RouterDecision（spec prompt-injection-defense R4）。

    设计：
    - input_kind=off_topic → 走 chitchat 气泡（复用现有通道，不新增 UI）
    - reply_text 是固定常量，**绝不含任何用户输入文本**（防 echo 攻击内容 R4.2）
    - 附引导 chips 把用户拉回主路径
    """
    chips = [
        CtaChip(label=c["label"][:12], send=c["send"], icon=c.get("icon"))
        for c in PRIMARY_CTAS[:3]
    ]
    return RouterDecision(
        input_kind=InputKind.OFF_TOPIC,
        confidence=0.99,
        reply_text=(
            "这个我帮不上忙哦～不过下午局规划是我的强项~ "
            "试试告诉我你下午想做什么？"
        ),
        tone="playful",
        cta_chips=chips,
        rationale="prompt_injection_blocked",
    )


def _looks_like_feedback_strong_from_state(utterance: str, itinerary: Any) -> bool:
    """Layer 1 强信号：has_itinerary + 命中强信号子集（不误吞新需求）。

    对应 router.py 的 _looks_like_feedback_strong(state)，但接受展平参数，
    不依赖 AgentState（避免 graph 层依赖）。
    """
    if not itinerary:
        return False
    txt = (utterance or "").strip()
    return looks_like_feedback_strong(txt)


# ============================================================
# act → RouteKind 映射（routing 层唯一持有，T2 精修）
# ============================================================
# 对话行为（core 层 DialogueAct 枚举）到路由目标（RouteKind）的映射：
#   QUESTION / BOOKING / CONFIRM → "chitchat"
#   SOFT_CONSTRAINT              → "emotional"
#   None（都不是）               → None（交兜底）
#
# core 层（dialogue_acts.py）只产出 DialogueAct 枚举，不感知任何 RouteKind 值。
# 映射在此唯一维护，符合关注点分离原则。

_ACT_TO_ROUTE_KIND: dict[DialogueAct, str] = {
    DialogueAct.QUESTION:        "chitchat",
    DialogueAct.BOOKING:         "chitchat",
    DialogueAct.CONFIRM:         "chitchat",
    DialogueAct.SOFT_CONSTRAINT: "emotional",
}


def _act_outcome_to_route_outcome(act_result: Any) -> RouteOutcome | None:
    """把 classify_dialogue_act 的结果转为 RouteOutcome。

    act_result 为 DialogueActResult 或 None。
    None → None（交兜底）。
    act.QUESTION / BOOKING / CONFIRM → RouteOutcome("chitchat", decision)
    act.SOFT_CONSTRAINT              → RouteOutcome("emotional", decision)
    """
    if act_result is None:
        return None
    route_kind = _ACT_TO_ROUTE_KIND[act_result.act]
    return RouteOutcome(kind=route_kind, decision=act_result.decision)


# ============================================================
# 主函数
# ============================================================

def route_turn(
    utterance: str,
    itinerary: Any,
    user_id: Any,
    *,
    client: Any,
    classify_fn: Any = None,
) -> RouteOutcome:
    """路由主干——整条级联，纯函数，返回 RouteOutcome。

    Args:
        utterance:    用户当轮输入文本（对应 state["user_input"]）。
        itinerary:    当前会话方案（对应 state["itinerary"]），无则 None/{}。
        user_id:      用户 ID（供 persona_qa 查画像）。
        client:       LLM 客户端（Layer 2 + QA 使用）。
        classify_fn:  可选的 LLM 分类器注入口（供测试 monkeypatch；None 时使用模块级
                      classify_input）。adapter（graph/nodes/router.py）传入其自身命名空间
                      的 classify_input，使得 monkeypatch.setattr(router_mod, ...) 仍然有效。

    Returns:
        RouteOutcome(kind, decision)
    """
    _classify = classify_fn if classify_fn is not None else classify_input
    has_itinerary = bool(itinerary)

    # ---- Layer 0：提示词注入检测（spec prompt-injection-defense L1，最前置） ----
    verdict = detect_injection(utterance)
    if verdict.is_injection and verdict.severity == "high":
        logger.warning(
            "prompt_injection_blocked: category=%s matched=%s input_head=%r",
            verdict.category,
            verdict.matched,
            utterance[:40],
        )
        return RouteOutcome(kind="off_topic", decision=_safe_refusal_decision())

    # ---- 壳2：canonical 字面短路（替代旧 Layer 1.5 规划信号表 fast path）----
    # 深审修正(E-1):壳2 必须先于 Layer 1——canonical 是系统自己发出的精确全串
    # (FP≈0 的确定性),优先级高于启发式强信号。否则场景文案里恰好含保留词
    # (如 S1「预算别太贵」的「贵」)时,会话中期点场景卡会被 Layer 1 吞成
    # feedback 去改旧方案,而不是按语义开新规划。
    shortcut = canonical_shortcut_decision(utterance, has_itinerary=has_itinerary)
    if shortcut is not None:
        return shortcut

    # ---- Layer 1：强信号启发式（has_itinerary + 强信号子集） ----
    if _looks_like_feedback_strong_from_state(utterance, itinerary):
        return RouteOutcome(kind="feedback", decision=None)

    # ---- Layer 1.7：用户画像问答（规则识别，不调 LLM）----
    persona_decision = build_persona_decision(utterance, user_id)
    if persona_decision is not None:
        return RouteOutcome(kind="chitchat", decision=persona_decision)

    # ---- Layer 2：LLM 分类（带 has_itinerary 上下文）；失败 → 壳3 保守地板 ----
    try:
        decision = _classify(utterance, client=client, has_itinerary=has_itinerary)
    except Exception:  # noqa: BLE001
        decision = fallback_decision(utterance, has_itinerary=has_itinerary)

    route_kind: RouteKind = decision.input_kind  # type: ignore[assignment]

    # ---- Layer 3：会话内对话行为统一判定（C2 收口） ----
    if has_itinerary:
        act_result = classify_dialogue_act(utterance, itinerary, client=client)
        outcome = _act_outcome_to_route_outcome(act_result)
        if outcome is not None:
            return outcome

    return RouteOutcome(kind=route_kind, decision=decision)
