"""agent.narrator —— Agent 暖心开场白生成器。

行程出炉时把 itinerary.summary 替换成像导游开场白一样有温度的两三句话。

模式：
- LLM 模式：调 llm_client，温度 0.7（要"人味"），短 prompt 快返回（<2s）
- Fallback / 规则模式：模板拼，无依赖

调用约定（main.py 在 itinerary_ready 推送之前调一次）：

    from agent.narrator import generate_narration

    narration = generate_narration(
        intent=intent,
        itinerary=itinerary,
        stage="stream",          # 或 "confirm"
        use_llm=True,            # mode == "llm" 或 _use_real_planner()
    )

不负责：
- prompt 文本（在 prompts/narrator_prompt.py）
- SSE 推送（在 main.py）
- 行程组装（在 planner_*.py）
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.llm_client import LLMMessage, get_llm_client
from agent.prompts.narrator_prompt import (
    NARRATOR_SYSTEM_PROMPT,
    build_narrator_user_message,
)
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary

logger = logging.getLogger(__name__)


# ============================================================
# 模板兜底（规则模式 + LLM 失败回退）
# ============================================================


def _format_companions(companions: list) -> str:
    """同行人 → 中文短语。

    "妻子 1 + 孩子 5 岁 1" → "和老婆孩子"
    "外公 1 + 外婆 1"      → "陪外公外婆"
    "朋友 4"               → "和 4 个朋友"
    "（空）"               → "（独处）"
    """
    if not companions:
        return "一个人"

    roles = []
    for c in companions:
        role = (c.get("role") if isinstance(c, dict) else getattr(c, "role", None)) or ""
        count = (c.get("count") if isinstance(c, dict) else getattr(c, "count", None)) or 1
        age = c.get("age") if isinstance(c, dict) else getattr(c, "age", None)

        if not role:
            continue
        # 常见角色口语化
        normalized = role.replace("妻子", "老婆")
        if "朋友" in role and count > 1:
            roles.append(f"{count} 个{role}")
        elif age is not None and age <= 12:
            roles.append(f"孩子")
        else:
            if count > 1 and "孩子" not in role:
                roles.append(f"{count} 位{normalized}")
            else:
                roles.append(normalized)

    if not roles:
        return "一个人"
    if len(roles) == 1:
        return f"和{roles[0]}"
    return "和" + "、".join(roles)


def _stage_to_phrase(stage: dict, idx: int, total: int) -> Optional[str]:
    """把单个 stage 转一句话；返回 None 表示不出现在开场白里（如「转场」「出发」太琐碎）。"""
    kind = (stage.get("kind") or "").strip()
    title = (stage.get("title") or "").strip()
    start = (stage.get("start") or "").strip()
    note = (stage.get("note") or "").strip()

    if kind in ("出发", "返回"):
        # 首段说"X 出发"，末段说"X 回家"，其它跳过
        if idx == 0:
            return f"{start} 从家出发"
        if idx == total - 1:
            return f"{start} 打车回家"
        return None
    if kind == "转场":
        return None
    # 主活动 / 用餐 / 附加
    short_title = title.split(" · ")[-1] if " · " in title else title
    if kind == "用餐":
        if note and "预约" in note:
            return f"{start} 到{short_title}，{note.replace('待你确认后为你预约', '给你预约了')}"
        return f"{start} 到{short_title}吃饭"
    if kind == "主活动":
        return f"{start} 去{short_title}"
    return f"{start} {short_title}"


def _template_narration(
    intent: IntentExtraction,
    itinerary: Itinerary,
    stage_label: str,
) -> str:
    """规则模板拼开场白（fallback 也走这个）。

    格式（暖语气）：
        "{开场} {时长} 的安排——{主活动短语}；{用餐短语}；{回家短语}。{结尾}"
    """
    total_h = itinerary.total_minutes / 60
    companions_phrase = _format_companions(
        [c.model_dump() if hasattr(c, "model_dump") else c for c in intent.companions]
    )

    # 抽几个关键 stage
    stages_dump = [s.model_dump() if hasattr(s, "model_dump") else s for s in itinerary.stages]
    phrases: list[str] = []
    for i, s in enumerate(stages_dump):
        p = _stage_to_phrase(s, i, len(stages_dump))
        if p:
            phrases.append(p)

    # 头：根据 social_context 选不同口吻
    social = (intent.social_context or "").strip()
    if "独处" in social:
        opener = f"给你安排了一个 {total_h:.1f} 小时的安静下午——"
    elif "商务" in social:
        opener = f"接待方案 · {total_h:.1f} 小时——"
    elif "家庭" in social or "亲子" in social:
        # 家庭场景下若 companions 已具体化，加上"和老婆孩子"等修饰
        if companions_phrase != "一个人":
            opener = f"这是{companions_phrase}下午 {total_h:.1f} 小时的安排——"
        else:
            opener = f"这是下午 {total_h:.1f} 小时的家庭安排——"
    elif "情侣" in social:
        opener = f"给你和女朋友安排了 {total_h:.1f} 小时——"
    elif "老人" in social or "长辈" in social or "适老" in social:
        opener = f"陪老人的 {total_h:.1f} 小时安排——"
    elif "朋友" in social or "闺蜜" in social:
        if companions_phrase != "一个人":
            opener = f"{companions_phrase}的 {total_h:.1f} 小时——"
        else:
            opener = f"{total_h:.1f} 小时的下午局——"
    else:
        opener = f"下午 {total_h:.1f} 小时的安排——"

    body = "，".join(phrases[:3]) if phrases else f"{itinerary.summary}"

    # 尾
    if stage_label == "confirm":
        ending = "都给你搞定了，可以放心出门了。"
    else:
        ending = "哪里不合适跟我说一声。"

    return f"{opener}{body}。{ending}"


# ============================================================
# LLM 主路径
# ============================================================


def _call_llm_narrator(
    *,
    intent: IntentExtraction,
    itinerary: Itinerary,
    stage_label: str,
) -> Optional[str]:
    """调 LLM 生成开场白；任何异常返 None 让上层走 fallback。"""
    try:
        client = get_llm_client()
    except Exception as e:  # noqa: BLE001
        logger.warning("[narrator] get_llm_client 失败：%s", e)
        return None

    user_msg = build_narrator_user_message(
        intent_dict=intent.model_dump(),
        itinerary_dict=itinerary.model_dump(),
        stage_label=stage_label,
    )

    try:
        resp = client.chat(
            messages=[
                LLMMessage(role="system", content=NARRATOR_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_msg),
            ],
            temperature=0.7,  # 要人味
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[narrator] LLM chat 失败：%s", e)
        return None

    text = (resp.content or "").strip()
    if not text:
        return None

    # 防御：剥可能的 markdown 围栏 / 引号
    if text.startswith("```"):
        # 取去围栏后的内容
        from agent.llm_client import strip_json_fence

        stripped = strip_json_fence(text) or text
        text = stripped.strip()
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("「") and text.endswith("」")
    ):
        text = text[1:-1].strip()

    # 长度兜底（防 LLM 失控写一篇散文）
    if len(text) > 320:
        text = text[:280] + "……"

    return text


# ============================================================
# 公共入口
# ============================================================


def generate_narration(
    *,
    intent: IntentExtraction,
    itinerary: Itinerary,
    stage: str = "stream",
    use_llm: bool = True,
) -> str:
    """生成 Agent 暖心开场白。

    Args:
        intent: 用户意图（驱动语气选择 + 同行人）。
        itinerary: 当前 itinerary。
        stage: "stream"（行程刚出炉，邀请反馈结尾）或
               "confirm"（已下单，安抚式结尾）。
        use_llm: 是否走 LLM；False 则直接走模板（规则模式 + 单测）。

    Returns:
        2-3 句中文文案（80-200 字）。永远返回非空字符串。
    """
    if use_llm:
        text = _call_llm_narrator(
            intent=intent,
            itinerary=itinerary,
            stage_label=stage,
        )
        if text:
            return text

    # Fallback / 规则模式
    return _template_narration(intent, itinerary, stage)


__all__ = ["generate_narration"]
