"""agent.core.soft_constraint_sniffer —— 闲聊里的「弦外之音」软约束嗅探。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
业务逻辑（锁定，先想清楚再写代码 —— 改实现前先改这段）：

  问题：用户已有方案后随口一句「我妈膝盖不好」「我不太能吃辣」——这类话被路由判成
        emotional / chitchat 走了闲聊气泡，里面藏的硬约束（适合老人 / 不辣）就被一刀
        切丢了。但它们恰恰是最真实的偏好。

  为什么不直接进 refiner：那句话用户**没明说要改方案**，直接重算（refiner 立刻改）
        可能过激。正确的分寸是「听懂、但不擅自改」——主动**问一句**，把决定权交回用户。

  做法（主动建议 chip → 用户一键转正）：
    1) 对判成 emotional / chitchat 的输入跑本嗅探器，抽出**词典内**的隐含硬约束 tag；
    2) 把 tag 拼成一个针对性引导 chip（「换成适合老人的」），追加进 chitchat 气泡；
    3) 用户点 chip → chip.send 文案带着「不太合适 / 换 + 词典原词」重入路由 →
       L1 强反馈命中 → feedback → refiner 在原 intent 上并入该 tag（**复用现有链路，
       本嗅探器不写任何 memory、不碰 inferred 持久层、不自己重规划**）。

  受控纪律（schemas/router.py:54）：CtaChip.send 不许 LLM 发明。所以——
    - LLM 版只负责「这句隐含哪些词典 tag」这一个判断；
    - chip 的 label / send 一律由**代码用合法 tag 拼模板**，LLM 不碰 send。
    这样转正文案格式恒定，下游 refiner 能稳定并入。

  规则 + LLM 双轨（便宜先挡、贵的最后上，与 intent_parser / refiner 的 rule_fallback 同精神）：
    - 规则版先跑：关键词 → 固定词典 tag，零成本、零抖动；
    - 规则空了、且句子有实义时，才调一次 LLM 兜底隐晦表达（「我家那位上了年纪」）；
    - LLM 抖 / 超时 → 退空（就当纯闲聊），绝不卡住气泡。

  边界 / 不负责：
    - 不在无实义的纯问候（「你好」「谢谢」）上调 LLM；
    - 注入输入永不进入本路径（router L0 已拦在最前）；
    - 不做持久记忆 / inferred 层（产品决策：第一版只做「主动问 + 一键转正」）。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from schemas.router import CtaChip, InputKind, RouterDecision
from schemas.tags import DIETARY_TAGS, EXPERIENCE_TAGS, PHYSICAL_TAGS

from .llm_client import LLMClient, LLMMessage, strip_json_fence

logger = logging.getLogger("agent.core.soft_constraint_sniffer")

# 软约束只可能落在「物理 / 饮食 / 体验」三类硬 tag 词典里（social_context 是单值场景，
# 不属于「随口提一句的硬约束」范畴，故不纳入）。
_VALID_SOFT_TAGS: frozenset[str] = PHYSICAL_TAGS | DIETARY_TAGS | EXPERIENCE_TAGS


@dataclass(frozen=True)
class SoftConstraint:
    """一条嗅出的软约束。

    tags   : 命中的**词典内** tag（已保证 ⊆ _VALID_SOFT_TAGS）。
    reason : 一句人话，仅用于 chip label 的共情前缀（规则版给固定文案；LLM 版可为空）。
             **不进 send**——send 只由 tags 拼，守 router.py:54「send 不许 LLM 发明」。
    """

    tags: tuple[str, ...]
    reason: str = ""
    empathy: str = ""  # 一句共情回话，定制 chitchat 的 reply_text；规则版填，LLM 版留空


# ============================================================
# 规则表：弦外之音关键词 → 词典 tag（高置信、零成本，先跑）
# ============================================================
# 纪律：
# - 关键词用「具体多字词」，避开「累 / 老」这种会误吞（积累 / 老板）的单字；
# - tag 必须精确等于 schemas/tags.py 词典原词，否则下游 refiner 并入会被 Literal 拦；
# - empathy 是给 reply_text 的共情句，写成人话、各条点到各自的痛点（老人=好走、辣=口味）。
_RULE_TABLE: tuple[tuple[tuple[str, ...], tuple[str, ...], str, str], ...] = (
    (
        ("膝盖", "腿脚", "上年纪", "老人家", "年纪大了", "走不动", "走不远", "腿不好"),
        ("适合老人", "可休息"),
        "老人腿脚不便",
        "老人家腿脚不便，是得挑好走、能歇脚的地方。",
    ),
    (
        ("婴儿车", "推车", "抱娃", "背奶", "推着娃"),
        ("无台阶", "无障碍"),
        "推婴儿车不便",
        "推着娃出门，台阶和电梯确实得当回事。",
    ),
    (
        ("不能吃辣", "不太能吃辣", "怕辣", "不吃辣", "受不了辣", "一吃辣"),
        ("不辣",),
        "吃不了辣",
        "吃不了辣，那重口的就先给你避开。",
    ),
    (
        ("人太多", "怕挤", "怕人多", "太吵", "想清静", "想安静", "嫌吵", "图个清静"),
        ("安静聊天",),
        "想清静些",
        "想图个清静，人多的点就别凑了。",
    ),
    (
        ("好累", "乏了", "没精神", "想放松", "歇会", "歇歇", "想躺平", "提不起劲"),
        ("低强度", "可休息"),
        "想轻松点",
        "今天想松快点，那就别给你排太满。",
    ),
    (
        ("孩子蔫", "娃困", "孩子累", "宝宝闹", "孩子犯困", "娃没精神"),
        ("低强度", "可休息"),
        "孩子状态不佳",
        "娃没什么精神，节奏放慢点稳妥。",
    ),
)


def sniff_rule(text: str) -> list[SoftConstraint]:
    """规则版：扫关键词，命中即出（去重保序）。"""
    if not text:
        return []
    out: list[SoftConstraint] = []
    seen_tags: set[str] = set()
    for keywords, tags, reason, empathy in _RULE_TABLE:
        if any(k in text for k in keywords):
            # 去掉已被前一条软约束覆盖的 tag，避免「可休息」重复出现
            fresh = tuple(t for t in tags if t not in seen_tags)
            if not fresh:
                continue
            seen_tags.update(fresh)
            out.append(SoftConstraint(tags=fresh, reason=reason, empathy=empathy))
    return out


# ============================================================
# LLM 版：规则没命中的隐晦表达兜底（只判 tag，不碰 send）
# ============================================================

# 只在句子有实义时才花这次调用（纯问候 / 极短不调）。
_MIN_LLM_LEN = 5

_SNIFF_SYSTEM_PROMPT = (
    "你是「下午局」助手的软约束嗅探器。用户在闲聊里可能**没明说**、但隐含了对出行的硬约束"
    "（如提到家里老人腿脚不好 → 需要『适合老人』『可休息』）。\n"
    "你的唯一任务：判断这句话隐含了下面词典里的哪些 tag。\n"
    "硬性规则：\n"
    "1. tag 只能从给定词典里**逐字**选，禁止发明、禁止改字（含空格也要一致）。\n"
    "2. 只在**高置信**时输出；只是普通问候 / 情绪、没有可执行约束，就输出空数组。\n"
    "3. 不要输出任何 send / 文案 / 解释，只输出 tag。\n"
    '4. 严格输出 JSON：{"tags": ["适合老人", "可休息"]}；无命中输出 {"tags": []}。\n'
    f"【物理】{ '、'.join(sorted(PHYSICAL_TAGS)) }\n"
    f"【饮食】{ '、'.join(sorted(DIETARY_TAGS)) }\n"
    f"【体验】{ '、'.join(sorted(EXPERIENCE_TAGS)) }"
)


def sniff_llm(text: str, client: LLMClient) -> list[SoftConstraint]:
    """LLM 版：让模型只挑词典 tag；非法 tag 一律丢弃。失败返空（绝不抛）。"""
    if not text or len(text.strip()) < _MIN_LLM_LEN:
        return []
    try:
        resp = client.chat(
            [
                LLMMessage(role="system", content=_SNIFF_SYSTEM_PROMPT),
                LLMMessage(role="user", content=text.strip()),
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        cleaned = strip_json_fence(resp.content)
        if not cleaned:
            return []
        payload = json.loads(cleaned)
        raw_tags = payload.get("tags") if isinstance(payload, dict) else None
        if not isinstance(raw_tags, list):
            return []
        # 词典出口防御：只留合法 tag，去重保序
        valid: list[str] = []
        for t in raw_tags:
            if isinstance(t, str) and t in _VALID_SOFT_TAGS and t not in valid:
                valid.append(t)
        if not valid:
            return []
        return [SoftConstraint(tags=tuple(valid))]
    except Exception:  # noqa: BLE001 — 软约束是锦上添花，抖了就当纯闲聊
        logger.debug("sniff_llm failed; treat as plain chitchat", exc_info=True)
        return []


def sniff_soft_constraints(
    text: str,
    *,
    client: LLMClient | None = None,
    use_llm: bool = True,
) -> list[SoftConstraint]:
    """规则优先、LLM 兜底的统一入口。

    规则命中即返（不调 LLM，省）；规则空且句子有实义且允许 LLM 时，才调一次 LLM。
    """
    rule_hits = sniff_rule(text)
    if rule_hits:
        return rule_hits
    if not use_llm or client is None:
        return []
    return sniff_llm(text, client)


# ============================================================
# 软约束 → 引导 chip（label/send 全由合法 tag 拼，守 send 受控纪律）
# ============================================================

# 单次最多塞 2 个软约束 chip，给原有引导 chip 留位（RouterDecision.cta_chips 上限 4）。
_MAX_SOFT_CHIPS = 2


def build_soft_constraint_chips(
    constraints: list[SoftConstraint],
    *,
    has_itinerary: bool,
) -> list[CtaChip]:
    """把软约束拼成引导 chip。

    - 有方案：chip 引导「把当前方案换成 X 的」——send 含「不太合适 / 换 + 词典原词」，
      点击后 router L1 强反馈命中 → feedback → refiner 在原 intent 上并入。
    - 无方案：chip 引导「安排一个 X 的下午」——点击后走 planning，X 经 raw_input 进 intent。
    送进 send 的只有词典 tag（代码拼），不含任何 LLM 自由文本。
    """
    chips: list[CtaChip] = []
    for c in constraints[:_MAX_SOFT_CHIPS]:
        tag_phrase = "、".join(c.tags)
        if has_itinerary:
            label = f"换成{c.tags[0]}的"
            # send 含强反馈词「不合适」+「换」+ 词典原词：点击后 router L1 强反馈直接命中
            # → feedback → refiner，不依赖 LLM 二次分类（looks_like_feedback_strong 含「不合适」）。
            reason = c.reason or tag_phrase
            send = f"这版方案不合适，{reason}，帮我换成{tag_phrase}的"
        else:
            label = f"安排{c.tags[0]}的"
            send = f"帮我安排一个{tag_phrase}的下午"
        chips.append(CtaChip(label=label[:24], send=send[:200]))
    return chips


def compose_soft_constraint_reply(
    constraints: list[SoftConstraint],
    *,
    has_itinerary: bool,
) -> str | None:
    """据嗅出的软约束拼一句共情回话，替换 chitchat 原本的泛回话。

    用第一条（最贴当下的）软约束的 empathy 开头；LLM 版无 empathy 时退到一句基于 tag
    的通用引导。reply_text 是展示文案、不受 send 白名单约束，故可由代码生成。
    """
    if not constraints:
        return None
    c = constraints[0]
    tag_phrase = "、".join(c.tags)
    lead = c.empathy or f"想找{tag_phrase}的，我记着了。"
    tail = "要不要我把这版照着调一下？" if has_itinerary else "要不要我照这个帮你安排？"
    return lead + tail


# ============================================================
# 「明说要改方案」判定 + 直接构造主动问气泡（dialogue-act-routing C3）
# ============================================================

# 明确的"动手改方案"祈使词。命中 → 用户明说要改（提约束·明说改），直接走 refiner 重规划，
# 不再出气泡问。区别于"提约束·没说改"（我妈膝盖不好）——后者才主动问。
_EXPLICIT_REVISE_KEYWORDS: tuple[str, ...] = (
    "换成", "改成", "换个", "换一个", "帮我换", "帮我改", "给我换",
    "去掉", "重新规划", "重新安排", "重排", "重新来", "重做", "调整成",
)


def looks_like_explicit_revise(text: str) -> bool:
    """这句是不是「明确要求改方案」（含祈使替换词）。

    用于区分对话行为：提约束·没说改（→主动问气泡）vs 提约束·明说改（→直接重规划）。
    chip 点击后的转正句（"…帮我换成适合老人的"）含"换成"→ True → 走重规划，不被气泡二次拦。
    """
    if not text:
        return False
    return any(k in text for k in _EXPLICIT_REVISE_KEYWORDS)


def build_soft_constraint_decision(
    user_input: str,
    *,
    has_itinerary: bool,
    client: LLMClient | None = None,
    use_llm: bool = True,
) -> RouterDecision | None:
    """「提约束·没说改」→ 构造一个主动问的 emotional 气泡 decision；否则 None。

    用于 router L3 拆桶（dialogue-act-routing C3）：has_itinerary + ambiguous/planning 时，
    若这句是"提了新软约束、但没明说要改"，就主动问要不要照此调整，而不是闷头重规划。
    返回 None 的两种情况，都让上层继续走正常 feedback 重规划（红线：真反馈不漏）：
      - 明说要改（换成/改成/帮我换…）→ 用户已表态要改，无需再问；
      - 没嗅到任何软约束 → 不是提约束，可能是真反馈/提问，交回兜底。
    """
    if looks_like_explicit_revise(user_input):
        return None
    hits = sniff_soft_constraints(user_input, client=client, use_llm=use_llm)
    if not hits:
        return None
    chips = build_soft_constraint_chips(hits, has_itinerary=has_itinerary)
    if not chips:
        return None
    reply = compose_soft_constraint_reply(hits, has_itinerary=has_itinerary)
    return RouterDecision(
        input_kind=InputKind.EMOTIONAL,
        confidence=0.8,
        reply_text=reply or "我记下了，要不要照这个帮你调整一下？",
        tone="empathetic",
        cta_chips=chips,
        rationale="soft_constraint_proactive_ask",
    )


__all__ = [
    "SoftConstraint",
    "sniff_rule",
    "sniff_llm",
    "sniff_soft_constraints",
    "build_soft_constraint_chips",
    "compose_soft_constraint_reply",
    "looks_like_explicit_revise",
    "build_soft_constraint_decision",
]
