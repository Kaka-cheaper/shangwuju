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
# 诚实告知：用户指定品类未被满足检测（spec planning-pipeline-consolidation 后续）
# ============================================================


def _known_restaurant_cuisines() -> set[str]:
    """mock 数据里真实存在的餐厅菜系集合（用于过滤非菜系词如 KTV / 啤酒）。

    失败兜底返空集——检测降级为"不报未满足"（宁可不告知，不可误报）。
    """
    try:
        from data.loader import load_restaurants

        return {(r.cuisine or "").strip() for r in load_restaurants() if r.cuisine}
    except Exception:  # noqa: BLE001
        return set()


def _cuisine_match(pref: str, cuisine: str) -> bool:
    """双向 substring 宽松匹配（与 search_adapter._rerank_by_preferred_cuisine 同源）。"""
    if not pref or not cuisine:
        return False
    return (pref in cuisine) or (cuisine in pref)


# 餐饮品类指示词：含这些 token 的 preferred 词视为"明确餐饮诉求"
# （即使 mock 里没有该菜系，也应诚实告知"本地没有"，而非当活动词跳过）。
# 不含这些 token 的词（KTV / 展览 / 攀岩 / 密室）视为活动类，不参与餐厅品类判定。
_CUISINE_HINT_TOKENS = (
    "烤肉", "烧烤", "火锅", "串", "菜", "餐", "料理", "面", "饭",
    "日料", "粤", "川", "湘", "韩", "西餐", "甜品", "茶", "咖啡", "简餐",
)


def _looks_like_cuisine_request(pref: str, known: set[str]) -> bool:
    """判断 preferred 词是否为"餐饮品类诉求"。

    命中任一即视为餐饮诉求：
    1. 与 mock 已有菜系双向 substring 命中（如「烧烤」）
    2. 含餐饮指示 token（如「韩式烤肉」含「烤肉」——本地无此菜系也要诚实告知）
    排除：纯活动词（KTV / 展览 / 攀岩 / 啤酒）——不含 token 且非已知菜系。
    """
    if any(_cuisine_match(pref, c) for c in known):
        return True
    return any(tok in pref for tok in _CUISINE_HINT_TOKENS)


def detect_unmet_cuisine_preference(
    preferred_poi_types: list[str],
    itinerary_restaurant_cuisines: list[str],
) -> list[str]:
    """检测用户明示的餐饮品类是否未出现在最终行程的餐厅里。

    诚实告知场景（用户观察的 bug）：用户要「烧烤」但匹配餐厅都超距被过滤，
    最终排了火锅——应诚实告知"附近没找到烧烤"，而非假装满足。也覆盖
    "本地压根没有该品类"（如「韩式烤肉」mock 里无）的情况。

    判定规则：
    - 仅对**餐饮品类诉求**做判定（已知 mock 菜系 OR 含餐饮指示 token；
      过滤 "啤酒" / "KTV" / "展览" 等非餐厅菜系/活动词，避免误报）
    - 该品类未与任一行程餐厅 cuisine 双向 substring 命中 → 计入未满足
    - 无 preferred_poi_types / 无餐饮诉求命中 → 返空列表（不告知）

    Returns:
        未被满足的品类词列表（保序去重）；空列表 = 全部满足或无需告知。
    """
    if not preferred_poi_types:
        return []
    known = _known_restaurant_cuisines()

    unmet: list[str] = []
    seen: set[str] = set()
    for pref in preferred_poi_types:
        p = (pref or "").strip()
        if not p or p in seen:
            continue
        # 只判定"餐饮品类诉求"的 preferred（KTV/啤酒/展览 等跳过）
        if not _looks_like_cuisine_request(p, known):
            continue
        satisfied = any(
            _cuisine_match(p, c) for c in itinerary_restaurant_cuisines if c
        )
        if not satisfied:
            unmet.append(p)
            seen.add(p)
    return unmet


def detect_unmet_poi_preference(
    preferred_poi_types: list[str],
    itinerary_poi_types: list[str],
    itinerary_poi_names: list[str],
    itinerary_poi_tags: list[str],
) -> list[str]:
    """检测用户明示的 POI 活动诉求是否未出现在最终行程的 POI 里（spec narration-and-intent-fidelity R4）。

    诚实告知场景（用户观察的 bug 扩展）：用户明说「看展」，但方案里一个展都没有
    （重排后仍没选上 / 本地无此类场所 / 被距离过滤）——应诚实告知"看展这次没安排上，
    先帮你选了替代"，而非默默给个不相关的活动。这是 cuisine 版（detect_unmet_cuisine_preference）
    在 POI 活动维度的对称扩展。

    判定规则（与 search_adapter._rerank_by_preferred_poi_types 同源词法，保证
    "重排说命中、告知说未命中"不会出现）：
    - 对每个 preferred 诉求词：与行程任一 POI 的 type/name/tags 双向 substring 命中 → 满足
    - 未命中任一行程 POI → 计入未满足
    - **餐饮品类诉求**（含明显餐饮 token，如「烧烤」「火锅」）交给 cuisine 版处理，
      本函数跳过，避免同一诉求被双重告知
    - 无 preferred_poi_types → 返空列表（不告知）
    - fail-safe：词法 helper 导入失败时降级为不告知（宁缺毋误报）

    Returns:
        未被满足的活动诉求词列表（保序去重）；空列表 = 全部满足或无需告知。
    """
    if not preferred_poi_types:
        return []
    try:
        from agent.runtime.tools.search_adapter import poi_desire_match
    except Exception:  # noqa: BLE001
        return []

    known_cuisines = _known_restaurant_cuisines()

    unmet: list[str] = []
    seen: set[str] = set()
    for pref in preferred_poi_types:
        p = (pref or "").strip()
        if not p or p in seen:
            continue
        # 餐饮品类诉求交给 cuisine 版，POI 版不重复计（避免双重告知）
        if _looks_like_cuisine_request(p, known_cuisines):
            continue
        # 满足判定：诉求词与行程任一 POI 的 type/name 命中，或与合并 tags 池任一命中。
        # poi_desire_match 内部已对 type/name/tags 做双向 substring；这里把行程所有 POI
        # 的 type 与 name 逐个喂进去（tags 用合并池，任一 POI 的 tag 命中即算满足）。
        all_tags = list(itinerary_poi_tags or [])
        satisfied = False
        n_pois = max(len(itinerary_poi_types), len(itinerary_poi_names))
        for i in range(n_pois):
            ptype = itinerary_poi_types[i] if i < len(itinerary_poi_types) else ""
            pname = itinerary_poi_names[i] if i < len(itinerary_poi_names) else ""
            if poi_desire_match(p, ptype, pname, all_tags):
                satisfied = True
                break
        # 行程无 POI 节点但有 tag（极端兜底）：仅按 tags 判定一次
        if not satisfied and n_pois == 0 and all_tags:
            satisfied = poi_desire_match(p, "", "", all_tags)
        if not satisfied:
            unmet.append(p)
            seen.add(p)
    return unmet


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
    unmet_cuisines: Optional[list[str]] = None,
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

    # spec narration-and-intent-fidelity R1.4：复述全部活动节点（去掉旧 phrases[:3] 截断，
    # 否则「活动→用餐→活动」结构里餐后活动被砍掉，narration 讲到吃饭就收尾）。
    # demo 场景最多 3-4 活动；>6 活动时温和截断 + 「等」避免极端长文。
    if not phrases:
        body = f"{itinerary.summary}"
    elif len(phrases) > 6:
        body = "，".join(phrases[:6]) + " 等"
    else:
        body = "，".join(phrases)

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

    # 诚实告知：用户明示品类未排进行程（如附近没烧烤）→ 先坦白再说替代
    honest_text = ""
    if unmet_cuisines:
        cuisines_str = "、".join(unmet_cuisines[:2])
        honest_text = (
            f"说明一下，你想要的{cuisines_str}附近没找到合适的，"
            f"先帮你选了方案里的替代，不满意我再换。"
        )

    return f"{opener}{body}。{honest_text}{challenge_text}{ending}"


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
    unmet_cuisines: Optional[list[str]] = None,
) -> Optional[str]:
    """调 LLM 生成开场白；任何异常返 None 让上层走 fallback。

    spec R6：透传 critic_summary / quality_warnings，prompt 里有「主动质疑规则」
    段会指导 LLM 在收到这两个字段时主动加一句质疑性建议。
    unmet_cuisines：诚实告知未满足的用户指定品类。
    """
    try:
        client = get_llm_client(task="narration")
    except Exception as e:  # noqa: BLE001
        logger.warning("[narrator] get_llm_client 失败：%s", e)
        return None

    user_msg = build_narrator_user_message(
        intent_dict=intent.model_dump(),
        itinerary_dict=itinerary.model_dump(),
        stage_label=stage_label,
        critic_summary=critic_summary,
        quality_warnings=list(quality_warnings or []),
        unmet_cuisines=list(unmet_cuisines or []),
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
            # 优化 2：限制输出长度（中文每字 ≈ 2 token；80 字 ≈ 160 token + 余量）
            max_tokens=180,
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
# 流式入口（spec speed-constraints 优化 1：让 narration 逐字推到前端）
# ============================================================


def stream_llm_narrator(
    *,
    intent: IntentExtraction,
    itinerary: Itinerary,
    stage_label: str,
    critic_summary: str = "",
    quality_warnings: Optional[list[str]] = None,
    unmet_cuisines: Optional[list[str]] = None,
):
    """流式生成 narration；逐 chunk yield 文本片段。

    用途：
    - _planner_stream 末尾让前端逐字看到 narration 打字效果（评委体感「Agent 在思考」）
    - 失败 yield 空（调用方走 fallback 模板）

    与 _call_llm_narrator 区别：
    - 流式：第 1 个 chunk 来后立刻 yield；首字延迟 ~500ms vs 一次性 20s
    - max_tokens=180：限制总长度（中文每字 ~2 token；80 字 ≈ 160 token + 余量）

    设计纪律：
    - 前缀「```」/ 引号清理在调用方（流式过程中无法可靠剥）
    - 异常 yield 0 chunk（不抛），让 yield-from 链路友好
    """
    try:
        client = get_llm_client(task="narration")
    except Exception as e:  # noqa: BLE001
        logger.warning("[narrator] stream get_llm_client 失败：%s", e)
        return

    user_msg = build_narrator_user_message(
        intent_dict=intent.model_dump(),
        itinerary_dict=itinerary.model_dump(),
        stage_label=stage_label,
        critic_summary=critic_summary,
        quality_warnings=list(quality_warnings or []),
        unmet_cuisines=list(unmet_cuisines or []),
    )

    try:
        for chunk in client.stream_chat(
            messages=[
                LLMMessage(role="system", content=NARRATOR_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_msg),
            ],
            temperature=0.5,
            max_tokens=180,
        ):
            if chunk:
                yield chunk
    except Exception as e:  # noqa: BLE001
        logger.warning("[narrator] stream_chat 失败：%s", e)
        return


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
    unmet_cuisines: Optional[list[str]] = None,
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
        unmet_cuisines: 诚实告知用。用户明示但未排进行程的餐饮品类（如「烧烤」）
            ——narrator 须诚实说明"附近没找到 X，帮你换了方案里的替代品"。

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
            unmet_cuisines=unmet_cuisines,
        )
        if text:
            return text

    # Fallback / 规则模式（含 spec R6 兜底质疑 + 诚实告知）
    return _template_narration(
        intent, itinerary, stage, quality_warnings, unmet_cuisines
    )


__all__ = ["generate_narration"]
