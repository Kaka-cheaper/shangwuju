"""agent.intent.router —— 壳2/壳3 的决策构造器（ADR-0011 E-2-c 精简）。

历史：Phase 0.8 时本模块是"输入域 LLM 前置分类器"（`classify_input`），在
intent_parser 之前对用户输入做 6 类分类，一次 LLM 调用产出 input_kind + 暖心
回话 + 引导按钮。

ADR-0011 E-2-c 改造：`classify_input` 连同它的 6 旧类 prompt（`ROUTER_SYSTEM_
PROMPT`/`ROUTER_FEW_SHOTS`/`FEEDBACK_CONTEXT_HINT`）整体退役——它是"输入表面
特征"分类，被"系统欠用户哪种响应义务"分类的统一路由脑子（`agent.routing.
brain.classify_turn`）取代，不是简单改名（分类轴变了），继续维护一份平行的
旧分类器只会制造两套判断口径的漂移，故整体删除而非保留死代码。

本模块现在只剩两个"不调 LLM、构造决策"的入口，两者服务完全不同的语义（各自
docstring 有详细拒因）：
- `make_planning_decision`：壳2 canonical 字面短路命中时构造 PLANNING（`agent.
  routing.canonical_shortcut` 消费）。
- `fallback_decision`：壳3 保守地板，LLM 不可用/低置信度时的降级（`agent.
  routing.brain` 与 `agent.routing.route_turn` 消费）。

不负责：
- LLM 客户端实现（在 agent/core/llm_client.py）
- 路由脑子的 prompt 与调用（在 agent/routing/brain.py + brain_prompt.py）
- SSE 序列化（在 backend/main.py）
"""

from __future__ import annotations

from schemas.router import CtaChip, InputKind, RouterDecision

from .prompts.router_prompt import FLOOR_CLARIFY_CTAS, PRIMARY_CTAS


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
    - 有方案 → 澄清式引导（CLARIFY + 三个 FLOOR_CLARIFY_CTAS chips，原
      AMBIGUOUS 改名，E-2-c 7→6 塌缩）：不确定是要调整现有方案还是聊别的，
      问一句，不默默重规划、也不默默无视。

    E-2-c 新增消费方：`agent.routing.brain._apply_confidence_floor` 在脑子
    低置信度时也复用本函数的文案（`reason="brain_low_confidence"`），只是把
    最终 label 钉成 "clarify"——本函数自身行为不因此改变，仍是纯粹的"给定
    has_itinerary，返回哪套保守文案"，不感知调用方是壳3 兜底还是脑子降级。

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
            input_kind=InputKind.CLARIFY,
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
