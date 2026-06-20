"""nodes.router —— LangGraph 输入域路由节点。

复用 backend/agent/router.py 的 LLM 6 类分类逻辑。

输入：state["user_input"]
输出：state.update({"router_decision": ..., "route_kind": ...})

路由结果决定下一节点：
- planning  → intent_node → planner → ...
- feedback  → refiner_node（前提：state.intent / state.itinerary 已存在）
- 其他 5 类 → chitchat_node
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agent.core.feedback_detector import looks_like_feedback, looks_like_feedback_strong
from agent.core.injection_detector import detect_injection
from agent.graph.state import AgentState, RouteKind
from agent.intent.router import classify_input, fallback_decision
from agent.core.llm_client import get_llm_client

logger = logging.getLogger("agent.graph.router")


_PLANNING_TIME_SIGNALS = (
    "今天下午",
    "明天下午",
    "周末",
    "周日",
    "周六",
    "周五晚",
    "周五晚上",
    "今晚",
    "今天晚上",
    "明天晚上",
    "下午",
    "晚上",
)

_PLANNING_ACTION_SIGNALS = (
    "出去玩",
    "出去走",
    "散步",
    "出门",
    "去玩",
    "找个地方",
    "看展",
    "k 歌",
    "k歌",
    "ktv",
    "唱歌",
    "撸串",
    "夜宵",
    "下午茶",
    "聚会",
    "约会",
    "见面",
    "接待",
    "安排",
)

_PLANNING_COMPANION_SIGNALS = (
    "老婆",
    "孩子",
    "宝宝",
    "娃",
    "外公",
    "外婆",
    "爷爷",
    "奶奶",
    "父母",
    "妈妈",
    "爸爸",
    "客户",
    "闺蜜",
    "女朋友",
    "男朋友",
    "朋友",
    "兄弟",
    "同事",
    "同学",
    "室友",
)

_PLANNING_CONSTRAINT_SIGNALS = (
    "公里以内",
    "公里内",
    "km以内",
    "km内",
    "公里",
    "千米",
    "几个小时",
    "几小时",
    "半天",
    "预算",
    "人均",
    "别太贵",
    "别离家太远",
)


def _safe_refusal_decision() -> Any:
    """命中注入时的固定安全婉拒 RouterDecision（spec prompt-injection-defense R4）。

    设计：
    - input_kind=off_topic → 走 chitchat 气泡（复用现有通道，不新增 UI）
    - reply_text 是固定常量，**绝不含任何用户输入文本**（防 echo 攻击内容 R4.2）
    - 附引导 chips 把用户拉回主路径
    """
    # 延迟 import 避免循环依赖
    from schemas.router import CtaChip, InputKind, RouterDecision
    from agent.intent.prompts.router_prompt import PRIMARY_CTAS

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


def _looks_like_feedback_strong(state: AgentState) -> bool:
    """Layer 1 强信号：has_itinerary + 命中强信号子集（不误吞新需求）。

    用 looks_like_feedback_strong（强信号子集），区别于全集 looks_like_feedback——
    强信号词（太远 / 太赶 / 数字单位 / 以内）几乎不可能是新需求开头，命中即可
    直接判 feedback 不调 LLM；弱信号词（换 / 改）交 Layer 2 LLM 区分。
    """
    if not state.get("itinerary"):
        return False
    txt = (state.get("user_input") or "").strip()
    return looks_like_feedback_strong(txt)


def _looks_like_new_planning(user_input: str) -> bool:
    """Detect clear planning requests before asking the LLM router."""
    text = (user_input or "").lower().strip()
    if len(text) < 6:
        return False

    has_time = any(s in text for s in _PLANNING_TIME_SIGNALS)
    has_action = any(s in text for s in _PLANNING_ACTION_SIGNALS)
    has_companion = any(s in text for s in _PLANNING_COMPANION_SIGNALS)
    has_constraint = any(s in text for s in _PLANNING_CONSTRAINT_SIGNALS)
    has_group_size = bool(re.search(r"\b\d+\s*(?:个)?人\b", text))

    if has_time and (has_action or has_companion or has_group_size):
        return True
    if has_action and (has_companion or has_constraint or has_group_size):
        return True
    if has_companion and has_constraint:
        return True
    return False


def router_node(state: AgentState) -> dict[str, Any]:
    """同步节点。LLM 分类（异常时启发式兜底）。

    五层防御（spec feedback-routing-fix + prompt-injection-defense + session-no-new-request）：
        Layer 0（注入检测，不调 LLM）：detect_injection 命中 high → off_topic 安全婉拒。
        Layer 1（强信号，不调 LLM）：has_itinerary + looks_like_feedback_strong → feedback。
        Layer 1.5（正向规划 fast path，不调 LLM）：命中时间 / 活动 / 同行 / 预算距离时长等
                  通用规划信号 → 无方案时判 planning（首轮全新规划）；
                  **有方案时判 feedback**——会话内没有"该丢上下文的新需求"，读着像新规划的话
                  也交 refiner 在上一版 intent 上合并/换场景覆盖，绝不丢已有约束。
        Layer 2（LLM 分类，带上下文）：classify_input(has_itinerary=...)。
        Layer 3（会话内归并）：has_itinerary 时，LLM 判 planning（看似新需求）或 ambiguous
                  （模糊反馈）→ **一律 feedback**，交 refiner 带上一版 intent 上下文处理
                  （合并 / 换场景覆盖 / 换备选）。chitchat / meta / emotional / off_topic 有明确
                  社交语义，保持各自气泡、不重规划（修复「你好」被误判反馈重规划的 bug）。

    设计前提（session-no-new-request）：对话始终在一个 session 内，已有方案后任何"规划/反馈"
    类输入都是对上下文的延续，应交 refiner 带上下文判（拒绝原因 / 硬约束 / 还是单纯想换）。
    无 itinerary 的首轮 session：行为与重构前一致（R6.4），planning 走全新抽取。
    """
    user_input = state.get("user_input") or ""
    has_itinerary = bool(state.get("itinerary"))

    # ---- Layer 0：提示词注入检测（spec prompt-injection-defense L1，最前置） ----
    # 命中 high → 直接判 off_topic 安全婉拒，不调任何 LLM、不回显攻击文本。
    verdict = detect_injection(user_input)
    if verdict.is_injection and verdict.severity == "high":
        logger.warning(
            "prompt_injection_blocked: category=%s matched=%s input_head=%r",
            verdict.category,
            verdict.matched,
            user_input[:40],
        )
        return {
            "route_kind": "off_topic",
            "router_decision": _safe_refusal_decision(),
        }

    # ---- Layer 1：强信号启发式（has_itinerary + 强信号子集） ----
    if _looks_like_feedback_strong(state):
        return {
            "route_kind": "feedback",
            "router_decision": None,  # refiner 不需要 RouterDecision
        }

    # ---- Layer 1.5：正向规划 fast path（无场景枚举，仅看通用规划信号）----
    if _looks_like_new_planning(user_input):
        if has_itinerary:
            # 会话内没有"全新需求"：读着像新规划的话，也当带上下文的反馈，交 refiner
            # 在上一版 intent 上合并 / 换场景覆盖——绝不丢已有约束。不调 LLM。
            return {"route_kind": "feedback", "router_decision": None}
        return {
            "route_kind": "planning",
            "router_decision": fallback_decision(
                user_input, reason="planning_fast_path"
            ),
        }

    # ---- Layer 1.7：用户画像问答（规则识别，不调 LLM）----
    # 「我是谁 / 我的画像 / 你了解我」用系统 persona + 累积偏好作答。必须放在 L2 与
    # Layer 3 的 itinerary QA 之前——否则含疑问词的画像问会被「关于方案的提问」抢去弃答
    # （本 bug 根因）。有无方案都答（兑现「懂你」主题）。
    from agent.core.persona_qa import build_persona_decision

    persona_decision = build_persona_decision(user_input, state.get("user_id"))
    if persona_decision is not None:
        return {"route_kind": "chitchat", "router_decision": persona_decision}

    # ---- Layer 2：LLM 分类（带 has_itinerary 上下文） ----
    client = get_llm_client()
    try:
        decision = classify_input(
            user_input, client=client, has_itinerary=has_itinerary
        )
    except Exception:  # noqa: BLE001
        decision = fallback_decision(user_input)

    # router 的 input_kind 与 RouteKind 字段名一致
    route_kind: RouteKind = decision.input_kind  # type: ignore[assignment]

    # ---- Layer 3：会话内对话行为统一判定（C2 收口 · spec dialogue-act-routing） ----
    # 原 L3 拆桶（ambiguous）+ L3.5 闲聊 sniff（emotional/chitchat）两处重复的
    # 「判断这句是什么对话行为 → 构造 decision」，提成一个 resolve_session_act
    # （Fowler: Consolidate Duplicate Conditional Fragments + Extract Function）。
    # 已有方案时一处判完：提问 → 接地回答；确认 → 肯定不重规划；提约束没说改 → 主动问气泡。
    # 顺带补齐原先漏掉的口子：被 L2 判成 chitchat 的提问，现在也能进 QA。
    if has_itinerary:
        from agent.core.dialogue_acts import resolve_session_act

        act = resolve_session_act(user_input, state.get("itinerary"), client=client)
        if act is not None:
            return act

    # 兜底归并：已有方案 + planning/ambiguous（认不出的对话行为）→ feedback（红线：真反馈不漏）。
    # chitchat/meta/emotional/off_topic 有明确社交语义，保持各自气泡、不重规划。
    if has_itinerary and route_kind in ("planning", "ambiguous"):
        return {"route_kind": "feedback", "router_decision": None}

    return {
        "router_decision": decision,
        "route_kind": route_kind,
    }


def route_after_router(state: AgentState) -> str:
    """conditional edge 函数。返回下一节点名。"""
    kind = state.get("route_kind")
    if kind == "planning":
        return "intent"
    if kind == "feedback":
        return "refiner"
    # chitchat / meta / emotional / off_topic / ambiguous
    return "chitchat"
