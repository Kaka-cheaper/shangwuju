"""agent.core.persona_qa —— 关于「用户自己」的问答（我是谁 / 我的画像 / 你了解我）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
业务逻辑（锁定）：

  问题：用户选了画像（如「新手爸爸」）后问"我是谁 / 我的用户画像是什么"，却被
        itinerary QA（关于方案的提问）误捕获 → 方案里没有"画像"字段 → 弃答 →
        泛答"没有存储您的信息"。而系统明明有完整画像（persona + 累积偏好）。

  成熟做法：对话系统里「关于用户自己的信息 / 偏好」是 personalization / user-model
        query 的标准类型。系统持有 user model（persona + memory priors）就该能回答
        "你是谁 / 你了解我什么 / 我的偏好"。这也兑现本产品「懂你」的核心主题。

  做法：规则识别 persona 问题（不调 LLM）→ 用 get_persona + compute_priors 数据
        grounded 作答（label + 累积偏好），自信展示"我记着你"。

  身份边界（记忆身份读写分离批，ADR-0015 身份边界补充决策，2026-07-05）：
    demo 无账号体系，会话即身份——回答按**双键**取数：
    - 模板（label / persona 默认 tag）按 user_id：共享只读，onboarding 选谁答谁；
    - 累积（确认攒下的偏好）按 session_id：会话私有，A 会话攒的绝不出现在 B
      会话的回答里（隐私式诚实：跨会话就该"不认识"）。
    房间路径：模板按**发话人**的 uid（问画像是"我是谁"，不是"房间归谁"），
    累积按**房间会话**键（本房确认过的行程是全房共享上下文；任何人的场外
    历史都在别的会话键下，结构上不可达）。
    生产迁移 = 把会话键换成账号键，机制不动。

  边界（和邻居的联动）：
    - 必须**优先于 itinerary QA**——persona 问题含疑问词会被它抢先弃答（本 bug 根因）。
    - "你是谁"（问 AI 身份）≠"我是谁"（问用户画像）：cues 只认"我 / 我的"，不碰"你是谁"。
    - 数据全 grounded，不编造；没累积偏好时只说画像 + "多用几次会更懂你"。

  不负责：方案提问（itinerary_qa）、偏好的写入（memory_store）。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import logging

from schemas.router import InputKind, RouterDecision

logger = logging.getLogger("agent.core.persona_qa")


# 明确指向「问用户自身画像 / 偏好」的线索；都含「我 / 我的」，不碰「你是谁」（那是问 AI）。
_PERSONA_CUES: tuple[str, ...] = (
    "我是谁", "我的画像", "用户画像", "我的用户画像",
    "你了解我", "你知道我", "你懂我", "懂我多少", "对我了解",
    "我的偏好", "我有什么偏好", "我喜欢什么", "我喜欢啥",
    "我是什么样", "我是什么用户", "我是什么人设", "我的人设",
    "记得我", "还记得我", "我的标签", "我的资料",
)


def looks_like_persona_question(text: str) -> bool:
    """是不是在问「用户自己」的画像 / 偏好。"""
    if not text:
        return False
    return any(c in text for c in _PERSONA_CUES)


def answer_persona_question(
    user_id: str | None, session_id: str | None = None
) -> str:
    """双键 grounded 作答：模板按 user_id、累积按 session_id；数据缺失时降级，不编造。

    session_id 缺省（None）→ 纯模板回答（零累积）——绝不混入别的会话攒下的偏好。
    """
    uid = user_id or "demo_user"
    try:
        from data.memory_store import compute_priors, get_default_persona, get_persona

        persona = get_persona(uid) or get_default_persona()
        label = (persona.label or "").strip() or "默认用户"
        try:
            priors = compute_priors(uid, session_id).top_priors
        except Exception:  # noqa: BLE001
            priors = []
    except Exception:  # noqa: BLE001 — persona 库读不到也别让回话崩
        logger.debug("answer_persona_question failed", exc_info=True)
        return "你的画像我这边暂时读不到，多用几次我会慢慢记住你的偏好。"

    if priors:
        return (
            f"你是「{label}」。我记着你偏好：{'、'.join(priors[:4])}"
            "——下次规划我会自动往这上面靠，不用你每次重说。"
        )
    return f"你是「{label}」。多用几次、确认几回方案，我会记住你的偏好，越来越懂你。"


def build_persona_decision(
    user_input: str, user_id: str | None, session_id: str | None = None
) -> RouterDecision | None:
    """persona 问题 → 用画像数据作答的 chitchat decision；不是 persona 问题 → None。

    双键：user_id=模板（发话人是谁），session_id=累积（这段会话攒了什么）。
    """
    if not looks_like_persona_question(user_input):
        return None
    return RouterDecision(
        input_kind=InputKind.CHITCHAT,  # 复用闲聊出口：回个话、不重规划、不动方案
        confidence=0.92,
        reply_text=answer_persona_question(user_id, session_id),
        tone="neutral",
        cta_chips=[],
        rationale="persona_question",
    )


__all__ = [
    "looks_like_persona_question",
    "answer_persona_question",
    "build_persona_decision",
]
