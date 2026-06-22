"""agent.core.dialogue_acts —— 会话内对话行为的统一判定（C2 收口）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
业务逻辑（锁定 · spec dialogue-act-routing C2）：

  问题：已有方案后「判断这句是什么对话行为、该去哪」这件事，原本被切碎散在 router 的
        L3（ambiguous 拆桶）和 L3.5（emotional/chitchat 闲聊 sniff）两处，逻辑重复、
        且各自只覆盖一半入口（一个 question 被 L2 判 chitchat 就漏出 QA）。

  成熟做法（每个决策点对应的范式）：
    1) 对话行为分类（dialogue act classification）：把已有方案后的话归到 INFORM-constraint /
       REQUEST-info(question) / CONFIRM / ...，互斥且完备（Stolcke 2000; Montenegro 2019）。
    2) 收口手法（Fowler）：两处分支里相同的「判断对话行为→构造 decision」片段，用
       **Consolidate Duplicate Conditional Fragments + Extract Function** 提成本模块的
       classify_dialogue_act，router 只调一处。行为保持（重构纪律）：原 L3/L3.5 已验证的
       去向不变，只是合并入口、并顺带补齐「被判 chitchat 的提问」这个原先漏掉的口子。

  classify_dialogue_act 的判定顺序（对话行为优先级）：
    ① 提问（QUESTION / request-info）→ 查数据接地回答
    ② 预约指令（BOOKING / commit）「给我预约吧 / 下单」→ 一键确认 chip（**不重规划**）
    ③ 确认（CONFIRM / accept）「好的 / 就这个」→ 肯定 + 引导下一步（**不重规划**）
    ④ 提约束·没说改（SOFT_CONSTRAINT / inform-constraint）→ 主动问要不要照此调整
    ⑤ 以上都不是 → None，交回 router 兜底（planning/ambiguous → feedback 重规划）

  act → RouteKind 映射由 routing 层（route_turn.py）持有，core 层不感知 RouteKind。

  边界：
    - 「确认」必须是**纯肯定**——含反馈 / 疑问 / 明确改 / 追加词的，都不算确认（"好的但太远"
      是反馈、"可以近一点吗"是请求），交回兜底。
    - 「追加」（还想喝杯咖啡）不在这里拦：它本就该走 feedback → refiner 增量合并，不需要单独出口。

  不负责：提问回答（itinerary_qa）、软约束气泡（soft_constraint_sniffer）、重规划（refiner）。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from schemas.router import CtaChip, InputKind, RouterDecision

from .feedback_detector import looks_like_feedback
from .itinerary_qa import build_question_decision, looks_like_question
from .llm_client import LLMClient
from .soft_constraint_sniffer import (
    build_soft_constraint_decision,
    looks_like_explicit_revise,
)


# ============================================================
# 对话行为枚举（闭集，core 层自有，不依赖 routing）
# ============================================================

class DialogueAct(Enum):
    """会话内已有方案时的对话行为闭集（Stolcke 2000 子集）。

    QUESTION        提问 / request-info（"这家餐厅贵不贵"）
    BOOKING         预约指令 / commit-to-execute（"给我预约吧"）
    CONFIRM         纯确认 / accept（"好的，就这个"）
    SOFT_CONSTRAINT 提约束·没说改 / inform-constraint（"我妈膝盖不好走不远"）
    """

    QUESTION = "QUESTION"
    BOOKING = "BOOKING"
    CONFIRM = "CONFIRM"
    SOFT_CONSTRAINT = "SOFT_CONSTRAINT"


# ============================================================
# 确认（CONFIRM 对话行为）
# ============================================================

# 纯肯定词（"ok" 用小写匹配，中文不受 lower 影响）
_CONFIRM_WORDS: tuple[str, ...] = (
    "好的", "好嘞", "好呀", "好啊", "可以", "行", "确定", "没问题",
    "就这个", "就它", "就这样", "就这么定", "就酱", "挺好", "没意见",
    "听你的", "ok", "可以的", "妥了", "没毛病",
)

# 追加词：含这些是"加一项"（→ refiner），不是确认
_ADD_HINTS: tuple[str, ...] = ("加个", "加一", "还想", "再来", "再去", "再加", "顺便", "另外加")


def looks_like_confirm(text: str) -> bool:
    """是不是「纯确认 / 采纳」。

    必须含确认词，且不含反馈 / 疑问 / 明确改 / 追加——否则不是纯确认（交回兜底）。
    """
    if not text:
        return False
    t = text.strip()
    tl = t.lower()
    if not any(w in tl for w in _CONFIRM_WORDS):
        return False
    if looks_like_feedback(t):
        return False  # "好的但太远了" 是反馈
    if looks_like_question(t):
        return False  # "可以近一点吗" 是请求
    if looks_like_explicit_revise(t):
        return False
    if any(h in t for h in _ADD_HINTS):
        return False  # "行，加个咖啡" 是追加
    return True


def build_confirm_decision(text: str) -> RouterDecision | None:
    """纯确认 → 肯定 + 引导下一步（chitchat 出口，不重规划）；否则 None。"""
    if not looks_like_confirm(text):
        return None
    return RouterDecision(
        input_kind=InputKind.CHITCHAT,  # 复用闲聊出口：回个话、不改方案、不重规划
        confidence=0.85,
        reply_text="好嘞，那就按这个来。想订位、出张分享海报，或者再改两笔，随时招呼我。",
        tone="warm",
        cta_chips=[],
        rationale="dialogue_act_confirm",
    )


# ============================================================
# 预约指令（BOOKING / commit-to-execute 对话行为）
# ============================================================
# 区别于「确认」（好的/就这个=认可方案，弱 ack）：预约指令是**主动发起终态执行**
# （给我预约吧/帮我订/下单=strong commitment，Clark grounding 里的动作级证据）。
# 它绝不能落 feedback 重规划——而是给用户一个一键确认按钮，复用 /chat/confirm 真预约。
_BOOKING_WORDS: tuple[str, ...] = (
    "预约", "订位", "下单", "预定", "帮我订", "订吧", "约位", "去订", "帮我约", "约一下",
)


def looks_like_booking(text: str) -> bool:
    """是不是「主动发起预约 / 下单」的执行指令。

    含预约词，且不是疑问（"可以预约吗"=提问）、不是反馈（"别预约太远"=约束）、
    不是明确改方案——这些都交回各自通道。
    """
    if not text:
        return False
    t = text.strip()
    if not any(w in t for w in _BOOKING_WORDS):
        return False
    if looks_like_question(t):
        return False
    if looks_like_feedback(t):
        return False
    if looks_like_explicit_revise(t):
        return False
    return True


def build_booking_decision(text: str) -> RouterDecision | None:
    """预约指令 → 回引导 + 一键「确认预约」chip（action=confirm，点击走真 confirm）。"""
    if not looks_like_booking(text):
        return None
    return RouterDecision(
        input_kind=InputKind.CHITCHAT,  # chitchat 出口：回个话 + 按钮，不重规划、不动方案
        confidence=0.9,
        reply_text="好的，点一下「确认预约」就帮你把整桌都锁定（订位 / 门票 / 加购一并搞定）。",
        tone="warm",
        # 不带 emoji icon（前端对 action=confirm 的 chip 用 lucide Check 图标 + 主题实心按钮渲染，
        # 避免 label/icon 双对钩 + emoji 塑料感）。
        cta_chips=[CtaChip(label="确认预约", send="确认预约", icon=None, action="confirm")],
        rationale="dialogue_act_booking",
    )


# ============================================================
# 对话行为结果类型（T2 精修：act 枚举解耦 RouteKind）
# ============================================================

@dataclass(frozen=True)
class DialogueActResult:
    """classify_dialogue_act 的类型化返回值。

    Attributes:
        act:      命中的对话行为枚举值（DialogueAct）。
        decision: RouterDecision payload，供 chitchat_node / emotional_node 渲染回复。

    act → RouteKind 映射由 routing 层（route_turn._act_outcome_to_route_outcome）持有，
    core 层不感知任何 RouteKind 值。
    """

    act: DialogueAct
    decision: Optional[RouterDecision]


# ============================================================
# 收口入口（classify_dialogue_act）
# ============================================================

def classify_dialogue_act(
    user_input: str,
    itinerary: Any,
    *,
    client: LLMClient | None = None,
) -> DialogueActResult | None:
    """已有方案时，统一判这句是哪种对话行为，返回自己的类型化结果。

    命中 → DialogueActResult(act, decision)。
    都不是 → None，交回 route_turn 兜底（planning/ambiguous → feedback）。
    顺序（对话行为优先级）：提问 → 预约指令 → 确认 → 提约束没说改。

    act → RouteKind 映射由 routing 层持有（route_turn._act_outcome_to_route_outcome）：
        QUESTION / BOOKING / CONFIRM → chitchat
        SOFT_CONSTRAINT              → emotional
    """
    # ① 提问 → 接地回答
    qa = build_question_decision(user_input, itinerary, client=client)
    if qa is not None:
        return DialogueActResult(act=DialogueAct.QUESTION, decision=qa)

    # ② 预约指令（给我预约吧 / 下单）→ 一键确认 chip（绝不重规划）
    booking = build_booking_decision(user_input)
    if booking is not None:
        return DialogueActResult(act=DialogueAct.BOOKING, decision=booking)

    # ③ 确认 → 肯定 + 引导（不重规划）
    confirm = build_confirm_decision(user_input)
    if confirm is not None:
        return DialogueActResult(act=DialogueAct.CONFIRM, decision=confirm)

    # ④ 提约束·没说改 → 主动问气泡
    bubble = build_soft_constraint_decision(
        user_input, has_itinerary=True, client=client
    )
    if bubble is not None:
        return DialogueActResult(act=DialogueAct.SOFT_CONSTRAINT, decision=bubble)

    return None


__all__ = [
    "DialogueAct",
    "DialogueActResult",
    "classify_dialogue_act",
    "looks_like_confirm",
    "build_confirm_decision",
    "looks_like_booking",
    "build_booking_decision",
]
