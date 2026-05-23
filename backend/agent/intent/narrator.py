"""agent.narrator —— Agent 暖心开场白生成器。

行程出炉时把 itinerary.summary 替换成像导游开场白一样有温度的两三句话。

模式：
- LLM 模式：调 llm_client，温度 0.5（spec R6 把质疑稳定性提上来；之前 0.7 偶尔
  发散导致 critic_summary 指令被忽略），短 prompt 快返回（<2s）
- Fallback / 规则模式：模板拼，无依赖

调用约定（main.py 在 itinerary_ready 推送之前调一次）：

    from agent.intent.narrator import generate_narration

    narration = generate_narration(
        intent=intent,
        itinerary=itinerary,
        stage="stream",          # 或 "confirm"
        use_llm=True,            # mode == "llm" 或 _use_real_planner()
        critic_summary="",       # spec R6：critic 历史摘要 → 触发主动质疑
        quality_warnings=[],     # spec R6：可选 meta-critic 输出
    )

不负责：
- prompt 文本（在 prompts/narrator_prompt.py）
- SSE 推送（在 main.py / sse_adapter.py）
- 行程组装（在 planner_*.py）

【spec planning-quality-deep-review R6（Task 6）】
- build_narrator_user_message 加 critic_summary / quality_warnings 两形参
- 主路径 LLM 温度从 0.7 降到 0.5
- _template_narration 兜底：含 ≤6 岁孩 + 任一 node.duration_min > 90 时强制
  追加质疑短语（"宝贝可能会累" / "可以中途休息"），让 LLM 失败时模板路径
  也能让用户感知"AI 在为我考虑"
- generate_narration 透传 critic_summary / quality_warnings 给 LLM 与模板兜底
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.core.llm_client import LLMMessage, get_llm_client
from agent.intent.prompts.narrator_prompt import (
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


def _node_to_phrase(node: dict, idx: int, total: int) -> Optional[str]:
    """把单个 node 转一句话；返回 None 表示不出现在开场白里（如 home 起讫太琐碎）。

    edge_v1 nodes 首尾固定 home（target_kind="home"）；narrator 不讲 home 节点
    （home 是抽象起讫，用户看不到）。中间节点按 kind / target_kind 派文案。
    """
    target_kind = (node.get("target_kind") or "").strip()
    kind = (node.get("kind") or "").strip()
    title = (node.get("title") or "").strip()
    start = (node.get("start_time") or "").strip()
    note = (node.get("note") or "").strip()

    # home 节点：仅当首段 / 末段时点一句"出发 / 回家"，其它跳过
    if target_kind == "home":
        if idx == 0:
            return f"{start} 从家出发" if start else "从家出发"
        if idx == total - 1:
            return f"{start} 打车回家" if start else "打车回家"
        return None

    short_title = title.split(" · ")[-1] if " · " in title else title
    # 用餐节点：尽量带上预约信息
    if target_kind == "restaurant" or "用餐" in kind or "夜宵" in kind:
        if note and "预约" in note:
            return f"{start} 到{short_title}，{note.replace('待你确认后为你预约', '给你预约了')}"
        return f"{start} 到{short_title}吃饭"
    # POI 节点：按 kind 区分主活动 / 自由 / 其他
    if "主活动" in kind:
        return f"{start} 去{short_title}"
    return f"{start} {short_title}"


def _template_narration(
    intent: IntentExtraction,
    itinerary: Itinerary,
    stage_label: str,
    quality_warnings: Optional[list[str]] = None,
) -> str:
    """规则模板拼开场白（fallback 也走这个）。

    格式（暖语气）：
        "{开场} {时长} 的安排——{主活动短语}；{用餐短语}；{回家短语}。{质疑}{结尾}"

    spec R6 兜底质疑：
    - 含 ≤6 岁孩 + 任一非 home 节点 duration_min > 90 时，强制追加质疑短语，
      让 LLM 失败的兜底路径也能让用户感知"AI 在为我考虑"。
    - quality_warnings（如果由调用方传入）会被合并进质疑短语。
    """
    total_h = itinerary.total_minutes / 60
    companions_phrase = _format_companions(
        [c.model_dump() if hasattr(c, "model_dump") else c for c in intent.companions]
    )

    # 抽几个关键 node（edge_v1：跳过 home 起讫由 _node_to_phrase 内部决定；
    # 这里全量传入以保留首尾"出发 / 回家"的可选点缀）
    nodes_dump = [
        n.model_dump() if hasattr(n, "model_dump") else n for n in itinerary.nodes
    ]
    phrases: list[str] = []
    for i, n in enumerate(nodes_dump):
        p = _node_to_phrase(n, i, len(nodes_dump))
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

    # spec R6 兜底质疑：含 ≤6 岁孩 + 任 node.duration_min > 90 → 强制追加
    challenge_text = ""
    has_young_kid = any(
        getattr(c, "age", None) is not None and c.age <= 6
        for c in intent.companions
    )
    long_kid_node = None
    if has_young_kid:
        for n in itinerary.nodes:
            target_kind = getattr(n, "target_kind", None)
            duration_min = getattr(n, "duration_min", 0) or 0
            if target_kind in (None, "home"):
                continue
            if duration_min > 90:
                long_kid_node = n
                break
    if long_kid_node is not None:
        long_title = (getattr(long_kid_node, "title", "") or "").split(" · ")[-1]
        long_dur = getattr(long_kid_node, "duration_min", 0)
        challenge_text = (
            f"提醒一下，{long_title} 安排了 {long_dur} 分钟，宝贝可能会累，"
            f"可以中途休息一下。"
        )
    elif quality_warnings:
        # 没命中 ≤6 岁规则，但调用方传了 quality_warnings → 也融进文案
        challenge_text = "提醒一下，" + "；".join(quality_warnings[:2]) + "。"

    # 尾
    if stage_label == "confirm":
        ending = "都给你搞定了，可以放心出门了。"
    else:
        ending = "哪里不合适跟我说一声。"

    return f"{opener}{body}。{challenge_text}{ending}"


# ============================================================
# LLM 主路径
# ============================================================


def _call_llm_narrator(
    *,
    intent: IntentExtraction,
    itinerary: Itinerary,
    stage_label: str,
    critic_summary: str = "",
    quality_warnings: Optional[list[str]] = None,
) -> Optional[str]:
    """调 LLM 生成开场白；任何异常返 None 让上层走 fallback。

    spec R6：透传 critic_summary / quality_warnings，prompt 里有「主动质疑规则」
    段会指导 LLM 在收到这两个字段时主动加一句质疑性建议。
    """
    try:
        client = get_llm_client()
    except Exception as e:  # noqa: BLE001
        logger.warning("[narrator] get_llm_client 失败：%s", e)
        return None

    user_msg = build_narrator_user_message(
        intent_dict=intent.model_dump(),
        itinerary_dict=itinerary.model_dump(),
        stage_label=stage_label,
        critic_summary=critic_summary,
        quality_warnings=list(quality_warnings or []),
    )

    try:
        resp = client.chat(
            messages=[
                LLMMessage(role="system", content=NARRATOR_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_msg),
            ],
            # spec R6：温度从 0.7 降到 0.5，让"主动质疑"指令更稳定被遵守
            # （0.7 偶发跳过 critic_summary 段直接给暖文案）
            temperature=0.5,
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
        from agent.core.llm_client import strip_json_fence

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
    critic_summary: str = "",
    quality_warnings: Optional[list[str]] = None,
) -> str:
    """生成 Agent 暖心开场白。

    Args:
        intent: 用户意图（驱动语气选择 + 同行人）。
        itinerary: 当前 itinerary。
        stage: "stream"（行程刚出炉，邀请反馈结尾）或
               "confirm"（已下单，安抚式结尾）。
        use_llm: 是否走 LLM；False 则直接走模板（规则模式 + 单测）。
        critic_summary: spec R6 新增。critic 修正历史摘要（含 critical 违规码 +
            修复反馈），narrator 据此在文案中追加一句质疑性建议。
            空串 = 一次过没 critic 命中，narrator 不必质疑。
        quality_warnings: spec R6 新增。可选 meta-critic 输出的额外质量提醒
            （如「老人单段过长」），LLM 与模板兜底都会消费。

    Returns:
        2-3 句中文文案（80-200 字）。永远返回非空字符串。
    """
    if use_llm:
        text = _call_llm_narrator(
            intent=intent,
            itinerary=itinerary,
            stage_label=stage,
            critic_summary=critic_summary,
            quality_warnings=quality_warnings,
        )
        if text:
            return text

    # Fallback / 规则模式（含 spec R6 兜底质疑）
    return _template_narration(intent, itinerary, stage, quality_warnings)


__all__ = ["generate_narration"]
