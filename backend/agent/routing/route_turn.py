"""agent.routing.route_turn —— 纯函数路由主干（ADR-0011 E-2-c 统一脑子收口）。

把 router_node 的整条级联从 graph 层抽到 routing bounded context。
graph/nodes/router.py 退薄为 adapter，体内只剩"调 route_turn → 展平为 dict"。

禁止：本模块不得 import agent/graph/*（否则 routing→graph 成环）。

【级联层次（E-2-c 收口后，六标签闭集 = L0 响应义务契约的路由投影）】
    壳1  注入检测（不调 LLM）→ RouteOutcome(defense, SafeRefusalDecision,
         injection_blocked=True)——判定结果通过 `injection_blocked` 字段带出去，
         `graph/nodes/router.py` 据此决定会话日志是否打码，不再重复调用
         `detect_injection`（收敛此前两处各判一次的重复调用——E-2-a 时 route_turn.py
         不在改动范围内，只能各写一份；E-2-c 范围扩大到 route_turn.py 后一并收口）。
    壳2  canonical 字面短路（不调 LLM）→ RouteOutcome(planning|feedback|confirm, ...)
         系统自己发出的精确全串确定性高于启发式信号，先于 Layer 1（深审修正，E-1）。
    Layer 1   强信号反馈（不调 LLM）→ RouteOutcome(feedback, None)
    Layer 1.7 用户画像问答（规则，不调 LLM）→ RouteOutcome(chitchat, PersonaDecision)
    Layer 1.8 会话内规则判定（has_itinerary 时才跑，不调 LLM 主判定；仅 QA 弃答/
              软约束隐晦兜底各自可能带一次窄范围 LLM 调用，跟"路由脑子"是两回事）：
              提问 → chitchat；预约指令 → confirm；纯确认 → confirm；提约束没说
              改 → chitchat。这四个判定原属 C2 时期的 `classify_dialogue_act`
              （旧 Layer 3），当时排在"Layer 2 LLM 分类"**之后**、但命中即无条件
              覆盖 Layer 2 结果——前移到这里（脑子调用之前）是行为不变的优化：
              反正命中就覆盖，不命中才要走脑子，没理由让命中的情形多烧一次
              LLM（见 `agent/core/dialogue_acts.py` 模块 docstring）。
    脑子      统一路由脑子（`agent.routing.brain.classify_turn`）：一次 LLM 调用，
              吃 `RoutingContext.render_text()` + 本轮输入 + has_itinerary，出
              6 标签 + 槽位 + 置信度。失败（网络异常/坏 JSON/schema 校验失败）
              → 返回 None → 壳3。低置信度已在 brain 内部归并为 clarify。
    壳3       LLM/脑子不可用 → 保守地板：无方案→陪聊引导；有方案→澄清引导。
              绝不默认规划/重规划（`fallback_decision`，ADR-0011 决策 2 / E-1）。

旧"兜底归并"（has_itinerary + planning/ambiguous → 强制 feedback，route_turn.py
旧 :300-302）已在 E-1 删除；旧"Layer 2 `classify_input` + Layer 3
`classify_dialogue_act` 两次 LLM 调用"已在本批（E-2-c）塌缩为一次脑子调用，
`_ACT_TO_ROUTE_KIND` 缝合表随之删除（`classify_dialogue_act` 已退役，其规则
子判定前移见上）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

# ── 路由基础设施（routing 包内，无环）
from agent.routing.outcome import RouteOutcome
from agent.routing.canonical_shortcut import canonical_shortcut_decision

# ── core 层（routing 依赖 core，不依赖 graph）
from agent.core.feedback_detector import looks_like_feedback_strong
from agent.core.injection_detector import detect_injection
from agent.core.itinerary_qa import build_question_decision
from agent.core.dialogue_acts import build_booking_decision, build_confirm_decision
from agent.core.soft_constraint_sniffer import build_soft_constraint_decision
from agent.core.persona_qa import build_persona_decision

# ── 会话上下文打包器（ADR-0011 决策 3；脑子的唯一上下文来源，禁止自拼）
from agent.context.packer import pack_routing_context
from agent.context.types import SessionContextSource

# ── 统一路由脑子（ADR-0011 决策 1/2；E-2-c）
from agent.routing.brain import RouteJudgment, classify_turn

# ── 壳2/壳3 决策构造器
from agent.intent.router import fallback_decision

# ── schemas（无环）
from schemas.router import CtaChip, InputKind, RouterDecision

# ── 安全婉拒 prompt（agent.intent.prompts，无环）
from agent.intent.prompts.router_prompt import PRIMARY_CTAS

logger = logging.getLogger("agent.routing.route_turn")


# ============================================================
# 内部 helper
# ============================================================

def _safe_refusal_decision() -> RouterDecision:
    """命中注入时的固定安全婉拒 RouterDecision（spec prompt-injection-defense R4）。

    设计：
    - input_kind=DEFENSE → 走 chitchat 气泡通道（复用现有通道，不新增 UI；
      route_after_router 对非 planning/feedback 一律送 chitchat 节点）
    - reply_text 是固定常量，**绝不含任何用户输入文本**（防 echo 攻击内容 R4.2）
    - 附引导 chips 把用户拉回主路径
    """
    chips = [
        CtaChip(label=c["label"][:12], send=c["send"], icon=c.get("icon"))
        for c in PRIMARY_CTAS[:3]
    ]
    return RouterDecision(
        input_kind=InputKind.DEFENSE,
        confidence=0.99,
        reply_text=(
            "这个我帮不上忙哦～不过下午局规划是我的强项~ "
            "试试告诉我你下午想做什么？"
        ),
        tone="playful",
        cta_chips=chips,
        rationale="prompt_injection_blocked",
    )


# B2（2026-07-04 路演前小修批）：Layer 1 强反馈的问句排除护栏。
# 实锤误判：「太久没回复我了，人还在吗」含强信号词"太久"，被 Layer 1 直觉判成
# "嫌方案太久"送去重排——整句在问不在评。兄弟层（dialogue_acts 的 booking/
# confirm）都有 looks_like_question 类排除，本层此前没有。
_QUESTION_TAIL_MARKS: tuple[str, ...] = ("吗", "呢", "？", "?")


def _is_interrogative_tail(txt: str) -> bool:
    """句尾疑问标记判定（刻意收窄的护栏判据）。

    只认句尾 吗/呢/？/?（信息寻求问句）；"吧＋问号"（"太远了吧？"）按附加问/
    揣测语气处理＝陈述性抱怨，不算问句——与 itinerary_qa「"吧"不算问」同一
    判据。刻意**不**用 looks_like_question 的句中线索（多少/能不能/有没有…）：
    那会把"有没有便宜点的"这类问句形真反馈也排除出 Layer 1、误送 QA 弃答，
    精度护栏反伤召回主胜场。
    """
    t = (txt or "").strip()
    if not t:
        return False
    core = t.rstrip("？?！! ")
    if core.endswith("吧"):
        return False
    return t.endswith(_QUESTION_TAIL_MARKS)


def _looks_like_feedback_strong_from_state(utterance: str, itinerary: Any) -> bool:
    """Layer 1 强信号：has_itinerary + 命中强信号子集（不误吞新需求）。

    B2 护栏：句尾疑问标记的输入不在本层拍板（重排是确定性动作、无兜底），
    放行到 Layer 1.8 问答 / 脑子——慢一点但判得对，这是设计预期不是回归。
    """
    if not itinerary:
        return False
    txt = (utterance or "").strip()
    if _is_interrogative_tail(txt):
        return False
    return looks_like_feedback_strong(txt)


def _judgment_to_outcome(judgment: RouteJudgment) -> RouteOutcome:
    """脑子判定 → RouteOutcome。

    planning/feedback 不携带 decision——`route_after_router` 直送 intent/refiner，
    `emit_router` 也不为它们推 CHITCHAT_REPLY（各自另有固定文案，见该模块），
    brain 为这两类生成的 reply_text 天然无人读取；与 Layer 1 强信号命中
    `RouteOutcome(kind="feedback", decision=None)` 同一纪律，不特殊化。
    """
    if judgment.label in ("planning", "feedback"):
        return RouteOutcome(kind=judgment.label, decision=None)
    decision = RouterDecision(
        input_kind=InputKind(judgment.label),
        confidence=judgment.confidence,
        reply_text=judgment.reply_text,
        tone=judgment.tone,
        cta_chips=judgment.cta_chips,
        rationale=judgment.rationale,
    )
    return RouteOutcome(kind=judgment.label, decision=decision)


# ============================================================
# 主函数
# ============================================================

def route_turn(
    utterance: str,
    itinerary: Any,
    user_id: Any,
    *,
    client: Any,
    context_source: SessionContextSource,
    classify_fn: Any = None,
) -> RouteOutcome:
    """路由主干——整条级联，纯函数，返回 RouteOutcome。

    Args:
        utterance:     用户当轮输入文本（对应 state["user_input"]）。
        itinerary:     当前会话方案（对应 state["itinerary"]），无则 None/{}。
        user_id:       用户 ID（供 persona_qa 查画像）。
        client:        LLM 客户端（脑子 + QA/软约束兜底使用）。
        context_source: 会话上下文来源（`GraphStateSource`/`RoomSource` 之一，
            见 `agent/context/sources.py`）——脑子调用前打包成
            `RoutingContext.render_text()`；只在真正要调脑子时才打包（壳1/壳2/
            Layer 1/1.7/1.8 命中时不必付这份打包成本）。
        classify_fn:   可选的脑子调用注入口（供测试 monkeypatch；None 时使用
            模块级 `classify_turn`）。adapter（graph/nodes/router.py）传入其自身
            命名空间的 `classify_turn`，使得 `monkeypatch.setattr(router_mod, ...)`
            仍然有效。

    Returns:
        RouteOutcome(kind, decision)
    """
    _classify = classify_fn if classify_fn is not None else classify_turn
    has_itinerary = bool(itinerary)

    # ---- 壳1：提示词注入检测（spec prompt-injection-defense L1，最前置） ----
    verdict = detect_injection(utterance)
    if verdict.is_injection and verdict.severity == "high":
        logger.warning(
            "prompt_injection_blocked: category=%s matched=%s input_head=%r",
            verdict.category,
            verdict.matched,
            utterance[:40],
        )
        return RouteOutcome(
            kind="defense", decision=_safe_refusal_decision(), injection_blocked=True
        )

    # ---- 壳2：canonical 字面短路 ----
    # 深审修正(E-1)：壳2 必须先于 Layer 1——canonical 是系统自己发出的精确全串
    # (FP≈0 的确定性)，优先级高于启发式强信号。
    shortcut = canonical_shortcut_decision(utterance, has_itinerary=has_itinerary)
    if shortcut is not None:
        return shortcut

    # ---- Layer 1：强信号启发式（has_itinerary + 强信号子集） ----
    if _looks_like_feedback_strong_from_state(utterance, itinerary):
        return RouteOutcome(kind="feedback", decision=None)

    # ---- Layer 1.7：用户画像问答（规则识别，不调 LLM；ungated，无方案时也答）----
    persona_decision = build_persona_decision(utterance, user_id)
    if persona_decision is not None:
        return RouteOutcome(kind="chitchat", decision=persona_decision)

    # ---- Layer 1.8：会话内规则判定（has_itinerary 时才跑，前移自旧 Layer 3）----
    if has_itinerary:
        qa_decision = build_question_decision(utterance, itinerary, client=client)
        if qa_decision is not None:
            return RouteOutcome(kind="chitchat", decision=qa_decision)

        booking_decision = build_booking_decision(utterance)
        if booking_decision is not None:
            return RouteOutcome(kind="confirm", decision=booking_decision)

        confirm_decision = build_confirm_decision(utterance)
        if confirm_decision is not None:
            return RouteOutcome(kind="confirm", decision=confirm_decision)

        soft_decision = build_soft_constraint_decision(
            utterance, has_itinerary=True, client=client
        )
        if soft_decision is not None:
            return RouteOutcome(kind="chitchat", decision=soft_decision)

    # ---- 脑子：一次 LLM 调用（吃打包器产出的会话上下文）----
    context_text = pack_routing_context(context_source).render_text()
    judgment: Optional[RouteJudgment] = _classify(
        context_text, utterance, has_itinerary, client=client
    )

    # ---- 壳3：脑子失败（坏 JSON/超时/schema 校验失败）→ 保守地板 ----
    if judgment is None:
        decision = fallback_decision(utterance, has_itinerary=has_itinerary, reason="brain_unavailable")
        return RouteOutcome(kind=decision.input_kind.value, decision=decision)

    return _judgment_to_outcome(judgment)


__all__ = ["route_turn"]
