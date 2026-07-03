"""agent.routing.brain —— 统一路由脑子（ADR-0011 E-2-c）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
业务逻辑（锁定）：

  问题：路由层原本一轮最多烧两次 LLM——`classify_input`（Layer 2，6 旧类）+
        `classify_dialogue_act`（Layer 3，会话内对话行为）——两套词汇靠映射表
        缝合，「确认」被塞进 chitchat。ADR-0011 决策 1/2 把这收口成"一脑三壳"：
        壳1/壳2/强信号/画像/QA/预约/确认等零成本规则先挡在前面，剩下才轮到
        本模块——一次 LLM 调用，出 6 类闭集标签 + 槽位 + 置信度。

  成熟做法：
    1) 这是分类任务（intent/dialogue-act classification）的收口——把两次调用
       合并成一次，prompt 用少样本（few-shot）而非代码 if 分支承载"同一句话
       不同上下文不同判法"的语境敏感性（ADR-0011 决策 2 原文）。
    2) 置信度校准（confidence calibration）+ 弃答（abstention）：低置信度不是
       "凑合选一个"，而是主动降级为更安全的响应（此处=澄清），对应 L0 全局
       禁令 1"不确定时绝不默认规划/重规划"。

  本模块只负责"判"，不负责"抽"（ADR-0011 拍板 d："脑子不顺产 intent 草稿——
  判定与抽取分离，parse_intent 保持独立不动"）：`node_ref`/`feedback_hint` 是
  松散的提示槽位，供未来消费方参考，不是结构化的 `NodeAdjustment`/`IntentExtraction`
  草稿——精确抽取仍是 refiner/parse_intent 的职责，本模块不越界。

  失败即哨兵（sentinel）：网络异常/超时/坏 JSON/schema 校验失败一律捕获后返回
  `None`，调用方（`route_turn.py`）据此走壳3 保守地板——不在本模块内部"猜"，
  哨兵语义与 L0 禁令 1 一致（失败 = 不确定，交给更保守的下游处理）。

  低置信度处理（ADR-0011 拍板 b："置信度低归并澄清，绝不归并 feedback"）：
    不是简单把 `label` 改个字符串——原 `reply_text`/`cta_chips` 是照着"原判定"
    写的内容，标签一换内容就答非所问。故复用 `fallback_decision`（壳3同款
    保守文案，行为已经过 E-1 验证）整体替换文案，只把 `label` 钉成 "clarify"。
    已经是 "clarify" 的判定不重复处理（它已经在问，不需要被"降级"两次）。

  不负责：
    - 会话上下文的打包（`agent/context/`，本模块只吃 `render_text()` 产出的
      纯文本）。
    - 规则层的零成本短路（壳1 注入 / 壳2 canonical / 强信号反馈 / 画像 / QA /
      预约 / 确认 —— 这些在 `route_turn.py` 里跑在本模块**之前**，命中就不会
      调到这里）。
    - RouteKind → graph 边的映射（`route_turn.py` / `graph/nodes/router.py`）。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.core.dialogue_acts import CONFIRM_CTA_CHIP
from agent.core.llm_client import (
    LLMClient,
    LLMMessage,
    MIMO_THINKING_DISABLED_EXTRA_BODY,
    strip_json_fence,
)
from agent.core.prompt_guard import wrap_user_input
from agent.intent.prompts.router_prompt import FLOOR_CLARIFY_CTAS, PRIMARY_CTAS
from agent.intent.router import fallback_decision
from agent.routing.brain_prompt import BRAIN_FEW_SHOTS, BRAIN_SYSTEM_PROMPT
from agent.routing.kinds import RouteKind
from schemas.router import CtaChip, ReplyTone

logger = logging.getLogger("agent.routing.brain")


# ============================================================
# 输出 schema
# ============================================================


class RouteJudgment(BaseModel):
    """路由脑子一次调用的结构化产出（ADR-0011 决策 1/2）。

    `label` 复用 `RouteKind`（6 值闭集），不新造一套平行的分类枚举——`route_turn.py`
    直接拿 `label` 当 `RouteOutcome.kind` 用。
    """

    model_config = ConfigDict(extra="forbid")

    label: RouteKind
    confidence: float = Field(..., ge=0.0, le=1.0)
    reply_text: str = Field(..., min_length=1, max_length=400)
    tone: ReplyTone = "warm"
    cta_chips: list[CtaChip] = Field(default_factory=list, max_length=4)
    node_ref: Optional[str] = Field(default=None, max_length=100)
    feedback_hint: Optional[str] = Field(default=None, max_length=200)
    rationale: Optional[str] = Field(default=None, max_length=200)


# ============================================================
# 常量
# ============================================================

CONFIDENCE_FLOOR = 0.6
"""低于此值 → 归并澄清（ADR-0011 拍板 b）。数值沿用旧 `schemas/router.py`
`RouterDecision.confidence` 字段文档里"<0.6 时主调用方兜底"的既有阈值——
沿用同一数字，只是把"兜底动作"从旧世界的 PLANNING 翻转成新世界的 CLARIFY
（与 `fallback_decision` 的 E-1 翻转同一精神：往保守退，不往鲁莽退）。"""

_MAX_TOKENS = 600
"""路由判定输出短（label/confidence/一段 reply_text/≤4 chips/两个槽位），
600 tokens 对中文 reply_text（≤400 字，按保守 1.5 token/字估算约 600 token
上限）+ JSON 结构开销留了余量，同时不像蓝图生成那样给几千 token 的大预算——
"适度"取"够用不浪费"，不是精确计算出来的临界值。"""

_CHIP_WHITELIST: frozenset[str] = frozenset(
    {c["send"] for c in PRIMARY_CTAS} | {c["send"] for c in FLOOR_CLARIFY_CTAS}
)
"""cta_chips.send 白名单 = 引导返回主路径的场景文案（PRIMARY_CTAS）∪ 地板澄清
三选项（FLOOR_CLARIFY_CTAS）——brain 只在 chitchat/clarify 两类里可能建议按钮，
两个白名单的并集覆盖这两类的合理选项来源，不给 LLM 发明文案的空间（同
`agent/intent/router.py` 旧 `classify_input` 的白名单纪律，防止 send 文本失控
导致下游意图解析翻车）。"""


def _sanitize_chips(chips_raw: Any) -> list[CtaChip]:
    """白名单校验 + 去重 + 截断到 4 个（同 `classify_input` 旧纪律，见模块 docstring）。"""
    if not isinstance(chips_raw, list):
        return []
    seen: set[str] = set()
    out: list[CtaChip] = []
    for raw in chips_raw:
        if not isinstance(raw, dict):
            continue
        send = (raw.get("send") or "").strip()
        if send not in _CHIP_WHITELIST or send in seen:
            continue
        seen.add(send)
        try:
            chip = CtaChip(
                label=(raw.get("label") or "")[:24] or "试试看",
                send=send,
                icon=raw.get("icon"),
            )
        except ValidationError:
            continue
        out.append(chip)
        if len(out) >= 4:
            break
    return out


# ============================================================
# 消息构造
# ============================================================


def _build_messages(
    context_text: str, user_input: str, *, has_itinerary: bool
) -> list[LLMMessage]:
    messages: list[LLMMessage] = [LLMMessage(role="system", content=BRAIN_SYSTEM_PROMPT)]
    for fs_user, fs_assistant in BRAIN_FEW_SHOTS:
        messages.append(LLMMessage(role="user", content=fs_user))
        messages.append(LLMMessage(role="assistant", content=fs_assistant))

    plan_flag = "是" if has_itinerary else "否"
    user_msg = (
        f"【会话上下文】\n{context_text}\n\n"
        f"【本轮用户输入】\n{wrap_user_input(user_input)}\n\n"
        f"【当前是否已有方案】{plan_flag}"
    )
    messages.append(LLMMessage(role="user", content=user_msg))
    return messages


# ============================================================
# 置信度地板 + 标签专属后处理
# ============================================================


def _apply_confidence_floor(
    judgment: RouteJudgment, *, user_input: str, has_itinerary: bool
) -> RouteJudgment:
    """低置信度 → 归并澄清（ADR-0011 拍板 b），文案整体换成壳3同款保守文案。"""
    if judgment.confidence >= CONFIDENCE_FLOOR or judgment.label == "clarify":
        return judgment
    conservative = fallback_decision(
        user_input, has_itinerary=has_itinerary, reason="brain_low_confidence"
    )
    return judgment.model_copy(
        update={
            "label": "clarify",
            "reply_text": conservative.reply_text,
            "tone": conservative.tone,
            "cta_chips": conservative.cta_chips,
            "rationale": (
                f"confidence_floor(orig_label={judgment.label!r}, "
                f"confidence={judgment.confidence:.2f})"
            ),
        }
    )


def _apply_label_chip_policy(judgment: RouteJudgment) -> RouteJudgment:
    """标签专属的按钮纪律——不信任 LLM 对这几类标签的按钮选择，代码兜底：

    - confirm：无论 LLM 提了什么 chips，一律钉死唯一的「确认预约」action chip
      （L0 全局禁令 1："文本确认只引导显式按钮，绝不自动下单"——这枚 chip 是
      唯一授权的执行入口，不能被 LLM 的自由选择替换/增补）。
    - planning / feedback：这两类不展示气泡（`route_after_router` 直接送
      intent/refiner，`emit_router` 也不为它们推 CHITCHAT_REPLY），chips 恒为
      空，防止 LLM 塞了内容却从无人读取造成误解（同旧 `classify_input`
      "planning 类强制清空 chips" 的纪律，此处扩大到 feedback 同理）。
    """
    if judgment.label == "confirm":
        return judgment.model_copy(update={"cta_chips": [CONFIRM_CTA_CHIP]})
    if judgment.label in ("planning", "feedback") and judgment.cta_chips:
        return judgment.model_copy(update={"cta_chips": []})
    return judgment


# ============================================================
# 主入口（seam function）
# ============================================================


def classify_turn(
    context_text: str,
    user_input: str,
    has_itinerary: bool,
    *,
    client: LLMClient,
) -> Optional[RouteJudgment]:
    """一次 LLM 调用产出路由判定；失败一律返回 `None`（哨兵，调用方走壳3）。

    Args:
        context_text: `RoutingContext.render_text()` 的产出（会话历史/方案摘要/
            画像/待澄清/台账等，纯文本，本函数不关心它怎么来的）。
        user_input:   本轮用户原始输入（未消毒，本函数内部用 `wrap_user_input`
            做注入隔离——但真正的攻击拦截已经在 `route_turn.py` 壳1 完成，本函数
            正常不会收到已判定为注入的文本；这里的包裹是纵深防御第二层）。
        has_itinerary: 当前会话是否已有方案。
        client: LLM 客户端（stub/真实皆可）。

    Returns:
        `RouteJudgment`；`None` 表示 LLM 调用失败 / JSON 解析失败 / schema
        校验失败——调用方应据此走壳3 保守地板，不得自行猜测标签。
    """
    messages = _build_messages(context_text, user_input, has_itinerary=has_itinerary)

    try:
        response = client.chat(
            messages,
            temperature=0.2,
            response_format={"type": "json_object"},
            max_tokens=_MAX_TOKENS,
            extra_body=MIMO_THINKING_DISABLED_EXTRA_BODY,
        )
    except Exception:  # noqa: BLE001 —— 网络异常/超时一律走哨兵
        logger.warning("route_brain: LLM 调用失败", exc_info=True)
        return None

    if not response.content:
        logger.warning("route_brain: 空响应")
        return None

    cleaned = strip_json_fence(response.content) or ""
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("route_brain: JSON 解析失败 head=%r", response.content[:120])
        return None
    if not isinstance(payload, dict):
        logger.warning("route_brain: 输出非 JSON 对象")
        return None

    payload["cta_chips"] = [c.model_dump() for c in _sanitize_chips(payload.get("cta_chips"))]
    payload.setdefault("confidence", 0.5)  # 缺失时保守估计，落在地板以下自然触发降级
    payload.setdefault("tone", "warm")

    try:
        judgment = RouteJudgment.model_validate(payload)
    except ValidationError:
        logger.warning("route_brain: schema 校验失败 payload=%r", payload)
        return None

    judgment = _apply_confidence_floor(
        judgment, user_input=user_input, has_itinerary=has_itinerary
    )
    judgment = _apply_label_chip_policy(judgment)
    return judgment


__all__ = ["RouteJudgment", "classify_turn", "CONFIDENCE_FLOOR"]
