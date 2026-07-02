"""agent.router —— 输入域 LLM 前置分类器（Phase 0.8）。

定位：
- 在 intent_parser **之前**对用户输入做 6 类分类
- 一次 LLM 调用同时产出：input_kind + 暖心回话 + 引导按钮（不再二次调 LLM 生成回话）
- 失败时降级为**保守地板**（ADR-0011 决策 2）：无方案→陪聊引导，有方案→澄清引导，
  绝不再假设 PLANNING（旧「听不懂就动手」违反 L0 禁令 1，见 `fallback_decision` docstring）

设计取舍（与方案 D 关键词 fast path 的对比）：
- 关键词 fast path 覆盖窄、对「我累死了」这类情绪表达不敏感
- 方案 A LLM 前置分类通用性强，新类别只加 prompt 不改代码
- 代价：每次多 1 次 LLM 调用（约 +1-3 秒）；首字节超时由调用方推 agent_thought 心跳兜底

不负责：
- LLM 客户端实现（在 agent/llm_client.py）
- prompt 文案与 cta 白名单（在 agent/prompts/router_prompt.py）
- SSE 序列化（在 backend/main.py）
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from schemas.router import CtaChip, InputKind, RouterDecision

from ..core.llm_client import LLMClient, LLMMessage, strip_json_fence
from ..core.prompt_guard import wrap_user_input
from .prompts.router_prompt import (
    FEEDBACK_CONTEXT_HINT,
    FLOOR_CLARIFY_CTAS,
    PRIMARY_CTAS,
    ROUTER_FEW_SHOTS,
    ROUTER_SYSTEM_PROMPT,
)


# 白名单：cta_chips.send 必须精确等于其中一条
_WHITELIST_SENDS: frozenset[str] = frozenset(c["send"] for c in PRIMARY_CTAS)


@dataclass
class RouterError(Exception):
    """路由分类失败。调用方应按 PLANNING 兜底。"""

    reason: str
    raw_text: str | None = None

    def __str__(self) -> str:  # pragma: no cover
        return f"RouterError({self.reason})"


def _build_messages(user_input: str, *, has_itinerary: bool = False) -> list[LLMMessage]:
    # spec prompt-injection-defense L3：用边界标记包裹用户输入，防指令/数据混淆
    wrapped = wrap_user_input(user_input)
    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=ROUTER_SYSTEM_PROMPT),
    ]
    for fs_user, fs_assistant in ROUTER_FEW_SHOTS:
        messages.append(LLMMessage(role="user", content=fs_user))
        messages.append(LLMMessage(role="assistant", content=fs_assistant))
    # spec feedback-routing-fix R3：已有方案时注入反馈上下文，让 LLM 区分反馈 vs 新需求
    if has_itinerary:
        messages.append(
            LLMMessage(role="user", content=f"{FEEDBACK_CONTEXT_HINT}\n{wrapped}")
        )
    else:
        messages.append(LLMMessage(role="user", content=wrapped))
    return messages


def _sanitize_cta_chips(chips_raw: list[dict]) -> list[CtaChip]:
    """白名单校验 + 去重 + 截断到 4 个。

    防 LLM 发明 send 文本：任何不在白名单里的 chip 直接丢弃。
    """
    seen: set[str] = set()
    out: list[CtaChip] = []
    for raw in chips_raw or []:
        if not isinstance(raw, dict):
            continue
        send = (raw.get("send") or "").strip()
        if send not in _WHITELIST_SENDS:
            continue  # 丢弃发明的 send
        if send in seen:
            continue  # 去重
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


def classify_input(
    user_input: str,
    *,
    client: LLMClient,
    has_itinerary: bool = False,
) -> RouterDecision:
    """主入口：用 LLM 对用户输入做 6 类分类。

    Args:
        user_input: 用户原文。
        client: LLM 客户端（同 intent_parser 共用）。
        has_itinerary: 当前 session 是否已有行程方案（spec feedback-routing-fix R3）。
            True 时注入反馈上下文提示，让 LLM 把反馈措辞判为 ambiguous，
            由 router_node Layer 3 接管为 feedback；不影响无方案时的分类行为。

    Returns:
        RouterDecision；cta_chips 中的 send 已经过白名单校验。

    Raises:
        RouterError: LLM 多次失败 / JSON 解析失败 / Pydantic 校验失败。
            调用方（main.py）应捕获并按 PLANNING 兜底。
    """
    messages = _build_messages(user_input, has_itinerary=has_itinerary)
    response = client.chat(
        messages,
        temperature=0.3,  # 比 intent_parser 高一些，让暖心回话更自然
        response_format={"type": "json_object"},
    )

    if not response.content:
        raise RouterError(reason="empty_response")

    cleaned = strip_json_fence(response.content) or ""
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RouterError(
            reason="json_decode_failed",
            raw_text=response.content,
        ) from e

    if not isinstance(payload, dict):
        raise RouterError(reason="not_a_json_object", raw_text=response.content)

    # 容错：把 cta_chips 单独抽出过白名单，再回灌到 payload
    chips_raw = payload.get("cta_chips") or []
    sanitized_chips = _sanitize_cta_chips(chips_raw)
    payload["cta_chips"] = [chip.model_dump() for chip in sanitized_chips]

    # 容错：confidence 缺失时按 0.7 兜底
    if "confidence" not in payload:
        payload["confidence"] = 0.7
    # 容错：tone 缺失时按 input_kind 给默认值
    if "tone" not in payload:
        kind = (payload.get("input_kind") or "").lower()
        payload["tone"] = {
            "emotional": "empathetic",
            "off_topic": "playful",
            "meta": "neutral",
        }.get(kind, "warm")

    try:
        decision = RouterDecision.model_validate(payload)
    except ValidationError as ve:
        raise RouterError(
            reason="schema_validation_failed",
            raw_text=str(ve),
        ) from ve

    # planning 类强制清空 chips（防 LLM 没读懂硬约束）
    if decision.input_kind == InputKind.PLANNING and decision.cta_chips:
        decision = decision.model_copy(update={"cta_chips": []})

    return decision


def fallback_decision(
    user_input: str,
    *,
    reason: str = "router_fallback",
    has_itinerary: bool = False,
) -> RouterDecision:
    """LLM 不可用时的保守地板（ADR-0011 决策 2；壳3）。

    ADR-0011 背景 2 实测钉死的病灶：旧版「LLM 不可用→直接判 PLANNING」在 stub/断网时
    把「你好」「asdfgh」「帮我写作业」全部当规划硬跑、有方案时全部当反馈硬猜重规划——
    听不懂就动手，正面违反 L0 响应义务契约禁令 1（"不确定用户要什么时，绝不默认规划/
    重规划；降级地板同样受此约束——往保守退，不往鲁莽退"）。

    新行为（**绝不返回 PLANNING**）：
    - 无方案 → 暖引导陪聊气泡（CHITCHAT + PRIMARY_CTAS 引导 chips，复用
      `_safe_refusal_decision` 的 chips 构造手法）：听不懂就问，不动手规划。
    - 有方案 → 澄清式引导（AMBIGUOUS + 三个 FLOOR_CLARIFY_CTAS chips）：不确定是要
      调整现有方案还是聊别的，问一句，不默默重规划、也不默默无视。

    Args:
        user_input: 用户原文（仅供未来 rationale/日志扩展，当前不影响分支）。
        reason: 触发原因（LLM 异常类别等），写进 rationale 供排查。
        has_itinerary: 当前 session 是否已有方案（route_turn 按 state 展平传入）。
            决定走哪条保守地板分支——这是本函数与 `make_planning_decision` 的核心区别：
            那边只服务壳2 canonical 字面命中（已确定性证明是合法规划请求），
            这里服务的是「不知道该怎么办」的降级，两者绝不可互相替代。
    """
    if has_itinerary:
        chips = [
            CtaChip(label=c["label"], send=c["send"], icon=c.get("icon"))
            for c in FLOOR_CLARIFY_CTAS
        ]
        return RouterDecision(
            input_kind=InputKind.AMBIGUOUS,
            confidence=0.5,
            reply_text="你是想调整现在的方案，还是聊点别的？",
            tone="warm",
            cta_chips=chips,
            rationale=f"LLM 路由不可用，按保守地板降级为澄清引导（{reason}）",
        )
    chips = [
        CtaChip(label=c["label"][:12], send=c["send"], icon=c.get("icon"))
        for c in PRIMARY_CTAS[:3]
    ]
    return RouterDecision(
        input_kind=InputKind.CHITCHAT,
        confidence=0.5,
        reply_text="我没太听清你想要什么~跟我说说你下午想怎么过？",
        tone="warm",
        cta_chips=chips,
        rationale=f"LLM 路由不可用，按保守地板降级为陪聊引导（{reason}）",
    )


def make_planning_decision(user_input: str, *, reason: str) -> RouterDecision:
    """壳2 canonical 字面短路命中时构造 PLANNING RouterDecision（ADR-0011 决策 2）。

    为什么不复用 fallback_decision（两者语义完全不同，硬拆开防止未来被"顺手"合并）：
    - fallback_decision 现在是**降级地板**——服务"LLM 不可用/异常，不知道用户要什么"
      的保守退让，已改为绝不返回 PLANNING（见其 docstring）。
    - 本函数服务**壳2 的确定性字面命中**——命中的文本是系统自己吐出去的 canonical
      文案（PRIMARY_CTAS 引导 chip / 地板澄清 chip「重新规划一个」/ /scenarios 端点的
      8 个演示场景 input），用户点击回传即**已确定性证明**是一句完整、合法的规划请求，
      不是"猜"出来的——这与降级地板"不知道该怎么办"的语义正相反，绝不能共用同一个
      函数（否则未来谁改动 fallback_decision 的保守行为，会误伤这条壳2 命中通道，
      或反过来谁想让壳2"更聪明点"会误改回降级地板的保守语义）。

    Args:
        user_input: 命中壳2 的原始文本（仅供 rationale 展示，不影响分支）。
        reason: 命中来源（如 "primary_cta_literal" / "demo_scenario_literal" /
            "floor_clarify_replan"），写进 rationale 供排查。
    """
    return RouterDecision(
        input_kind=InputKind.PLANNING,
        confidence=0.99,
        reply_text="正在为你规划下午行程……",
        tone="warm",
        cta_chips=[],
        rationale=f"壳2 canonical 短路命中，判定 planning（{reason}）",
    )
