"""agent.core.itinerary_qa —— 对已有行程方案的「提问」做接地问答（grounded QA）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
业务逻辑（锁定 · spec dialogue-act-routing C4）：

  问题：已有方案后用户问「这家贵不贵 / 这个公园远吗 / 要等位吗」，6 类里没有「提问」，
        被判成 ambiguous → 当反馈**重新规划**（答非所问）。

  成熟做法（每个决策点对应的范式）：
    1) 识别提问 = question / request-info 对话行为 → 疑问句式特征（吗/呢/?/多远/贵不贵…）。
    2) 接地问答（grounded QA）：答案**只能基于查到的数据**，不编造（编造=faithfulness
       hallucination）。
    3) 校准弃答（abstention）+ 来源标注：查不到字段就诚实说「没这个信息」，可凭经验补一句、
       但**标注**是经验不是数据。
    4) 模板化 NLG：字段命中走模板（零漂移）；查不到才用 LLM 凭经验（且强制标注）。
    5) 实体链接（entity linking）第一版从简：对方案里**拥有该字段的所有地点**作答，
       免做完整指代消解（"这家"到底指谁）。

  边界（想透的歧义）：
    - 「能不能近一点 / 再便宜点」是**疑问式的改请求**，不是提问——靠 explicit_revise +
      比较级线索（一点/再X）挡掉，交回 feedback/refiner。
    - **提问优先于提约束**：用户在「问」就先答他，而不是自作主张提议改（提问句即便含
      "老人"这种软约束词，也先回答）。

  数据来源：从 itinerary.nodes 的 target_id 反查 data.loader.load_pois/load_restaurants
            （复用 memory.py 反查套路），**不造新 tool**。

  不负责：重新规划、改方案（那是 refiner）；提问之外的对话行为（在 router_node / sniffer）。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import logging
from typing import Any

from schemas.router import InputKind, RouterDecision

from .llm_client import LLMClient, LLMMessage, MIMO_THINKING_DISABLED_EXTRA_BODY
from .prompt_guard import ROLE_LOCK_NOTICE, wrap_user_input
from .soft_constraint_sniffer import looks_like_explicit_revise

logger = logging.getLogger("agent.core.itinerary_qa")


# ============================================================
# 1. 识别「这是提问」（question / request-info 对话行为）
# ============================================================

# 句尾疑问词（"吧"不算——"就这样吧"是确认不是问）
_QUESTION_TAILS = ("吗", "呢", "?", "？")
# 句中疑问线索
_QUESTION_CUES = (
    "多少", "几点", "多远", "多久", "多大", "几个", "几公里", "怎么样", "如何",
    "贵不贵", "远不远", "多不多", "有没有", "能不能", "是不是", "好不好", "贵吗", "远吗",
    # 「有什么招牌 / 招牌是啥 / 哪个近」——避开裸「什么」，防「没什么意思」误判成提问
    "有什么", "是什么", "什么好", "哪些", "哪个", "啥",
)
# 比较级改请求线索（"近一点 / 再便宜"是要改，不是问）
_CHANGE_REQUEST_HINTS = ("一点", "点儿", "再近", "再远", "再便宜", "再贵", "近点", "远点")


def looks_like_question(text: str) -> bool:
    """疑问句式判定：句尾疑问词 或 句中疑问线索。"""
    if not text:
        return False
    t = text.strip()
    if t.endswith(_QUESTION_TAILS):
        return True
    return any(c in t for c in _QUESTION_CUES)


# ============================================================
# 2. 问句线索 → 字段（slot → KB schema 映射）
# ============================================================
# (field_id, 线索词)。命中即认定用户在问这个字段。具体短语优先，避免误吞。
_FIELD_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("price", ("贵不贵", "贵吗", "多少钱", "人均", "价格", "便宜吗", "划算吗", "贵不", "性价比")),
    ("distance", ("多远", "远不远", "远吗", "距离", "多久到", "几公里", "近吗", "远近")),
    ("hours", ("几点", "开门", "关门", "营业", "开到几点", "几点关", "几点开")),
    ("elderly_kid", ("适合老人", "老人能", "适合孩子", "适合几岁", "有台阶", "无障碍", "轮椅", "老人去", "带娃合适")),
    ("queue", ("要等", "排队", "等位", "人多吗", "人多不多", "挤吗", "等多久", "要等多久", "满不满")),
    ("capacity", ("包间", "能坐", "坐得下", "几个人", "多少人", "位子")),
    ("review", ("评分", "评价", "口碑", "好吃吗", "好玩吗", "怎么样", "好不好", "值不值")),
    ("signature", ("招牌", "特色菜", "必点", "什么好吃", "推荐菜", "拿手")),
)


def _detect_field(text: str) -> str | None:
    for field_id, cues in _FIELD_CUES:
        if any(c in text for c in cues):
            return field_id
    return None


# ============================================================
# 3. 反查方案里的地点（entity linking 从简）
# ============================================================

def _resolve_places(itinerary: Any) -> list[Any]:
    """从 itinerary.nodes 的 target_id 反查出 Poi / Restaurant 模型对象（跳过 home）。"""
    if not itinerary:
        return []
    try:
        data = itinerary.model_dump() if hasattr(itinerary, "model_dump") else dict(itinerary)
    except Exception:  # noqa: BLE001
        return []
    nodes = data.get("nodes") or []
    if not nodes:
        return []
    try:
        from data.loader import load_pois, load_restaurants

        pois = {p.id: p for p in load_pois()}
        rests = {r.id: r for r in load_restaurants()}
    except Exception:  # noqa: BLE001
        return []

    out: list[Any] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        tk, tid = n.get("target_kind"), n.get("target_id")
        if not tid or tk == "home":
            continue
        obj = pois.get(tid) if tk == "poi" else rests.get(tid) if tk == "restaurant" else None
        if obj is not None and obj not in out:
            out.append(obj)
    return out


def _is_restaurant(obj: Any) -> bool:
    return hasattr(obj, "avg_price")  # 餐厅独有字段


# ============================================================
# 4. 字段命中 → 模板回答（grounded · 零漂移）
# ============================================================

def _answer_price(places: list[Any]) -> str | None:
    parts: list[str] = []
    for p in places:
        if _is_restaurant(p):
            ap = p.avg_price
            tag = "偏高一些" if ap >= 150 else "挺实惠" if ap <= 60 else "中等价位"
            parts.append(f"{p.name} 人均约 {int(ap)} 元（{tag}）")
        elif getattr(p, "price_range", None):
            lo, hi = p.price_range[0], p.price_range[-1]
            parts.append(f"{p.name} 门票约 {int(lo)}-{int(hi)} 元")
    return "；".join(parts) if parts else None


def _answer_distance(places: list[Any]) -> str | None:
    parts = []
    for p in places:
        d = getattr(p, "distance_km", None)
        if d is None:
            continue
        tag = "有点远" if d > 5 else "挺近" if d <= 3 else "不算远"
        parts.append(f"{p.name} 距你家 {d} 公里（{tag}）")
    return "；".join(parts) if parts else None


def _answer_hours(places: list[Any]) -> str | None:
    parts = [f"{p.name} {p.opening_hours}" for p in places if getattr(p, "opening_hours", None)]
    return "；".join(parts) if parts else None


def _answer_elderly_kid(places: list[Any]) -> str | None:
    _ELDER = {"适合老人", "无台阶", "无障碍", "可休息"}
    parts = []
    for p in places:
        tags = set(getattr(p, "tags", []) or []) | set(getattr(p, "suitable_for", []) or [])
        hit = tags & _ELDER
        if hit:
            parts.append(f"{p.name} 标了「{'、'.join(sorted(hit))}」，适老比较友好")
        else:
            parts.append(f"{p.name} 没有明确的适老标注，保险起见到店前问一下")
    return "；".join(parts) if parts else None


def _answer_queue(places: list[Any]) -> str | None:
    parts = []
    for p in places:
        if _is_restaurant(p):
            slots = getattr(p, "reservation_slots", None) or []
            avail = [s for s in slots if getattr(s, "available", False)]
            if avail:
                s = avail[0]
                q = getattr(s, "queue_minutes", 0)
                parts.append(
                    f"{p.name} 最近可订 {s.time}"
                    + (f"，预估排队 {q} 分钟" if q else "，基本不用等")
                )
            else:
                parts.append(f"{p.name} 当前时段都约满了，得换时间或排队")
        else:
            avail = getattr(getattr(p, "capacity", None), "available_slots", None)
            if avail is not None:
                tag = "比较空" if avail > 50 else "人不少" if avail < 20 else "适中"
                parts.append(f"{p.name} 还有 {avail} 个名额（{tag}）")
    return "；".join(parts) if parts else None


def _answer_capacity(places: list[Any]) -> str | None:
    parts = []
    for p in places:
        if not _is_restaurant(p):
            continue
        cap = getattr(p, "capacity", None)
        if cap is None:
            continue
        room = "有包间" if getattr(cap, "private_room", False) else "没有包间"
        parts.append(f"{p.name} {room}")
    return "；".join(parts) if parts else None


def _answer_review(places: list[Any]) -> str | None:
    parts = []
    for p in places:
        rating = getattr(p, "rating", None)
        if rating is None:
            continue
        seg = f"{p.name} 评分 {rating}"
        reviews = getattr(p, "reviews", None) or []
        if reviews:
            txt = (getattr(reviews[0], "text", "") or "").strip()
            if txt:
                seg += f"，有人说「{txt[:30]}…」"
        parts.append(seg)
    return "；".join(parts) if parts else None


def _answer_signature(places: list[Any]) -> str | None:
    parts = []
    for p in places:
        dishes = getattr(p, "signature_dishes", None)
        if dishes:
            parts.append(f"{p.name} 招牌是 {('、'.join(dishes[:3]))}")
    return "；".join(parts) if parts else None


_FIELD_ANSWERERS = {
    "price": _answer_price,
    "distance": _answer_distance,
    "hours": _answer_hours,
    "elderly_kid": _answer_elderly_kid,
    "queue": _answer_queue,
    "capacity": _answer_capacity,
    "review": _answer_review,
    "signature": _answer_signature,
}


# ============================================================
# 5. 弃答（abstention）+ 来源标注
# ============================================================

# A1（2026-07-04 prompt 防护补齐）：本调用位曾是全仓唯一"用户原始文本直喂 LLM +
# 无 L2 角色锁定无 L3 输入隔离 + 输出自由文本直接展示给用户"的位置——弃答文案
# 会原样进 chitchat 气泡，注入面最大，故用完整版 ROLE_LOCK_NOTICE（非 BRIEF）+
# wrap_user_input 补齐两道防线；行为语义（弃答+经验标注）不变。
_ABSTAIN_SYSTEM = (
    ROLE_LOCK_NOTICE + "\n\n"
    "用户在问关于一个已定下午行程的问题，但方案数据里**没有**这条信息。"
    "请用一句中文坦诚说明『方案数据里没有这个信息』，再凭常识给一句简短建议，"
    "并**明确标注**这是经验、不是查到的数据（如『一般来说…，到店问下最稳』）。不超过 60 字。"
)


def _abstain(text: str, client: LLMClient | None) -> str:
    base = "你问的这个，方案数据里没有记录。"
    if client is None:
        return base + "建议到店或在 App 上确认一下。"
    try:
        resp = client.chat(
            [LLMMessage(role="system", content=_ABSTAIN_SYSTEM),
             LLMMessage(role="user", content=wrap_user_input(text.strip()))],
            temperature=0.3,
            # A6：关思考模式——弃答只要一句短文案，思考 token 挤占输出预算会把
            # 正文截空（narrator.py 有同款事故的根因记录），与 narrator/blueprint/
            # brain 三处已验证写法对齐。
            extra_body=MIMO_THINKING_DISABLED_EXTRA_BODY,
        )
        out = (resp.content or "").strip()
        return out or (base + "建议到店或在 App 上确认一下。")
    except Exception:  # noqa: BLE001
        return base + "建议到店或在 App 上确认一下。"


# ============================================================
# 6. 主入口
# ============================================================

def answer_itinerary_question(
    user_input: str,
    itinerary: Any,
    *,
    client: LLMClient | None = None,
) -> str | None:
    """对「关于已有方案的提问」给接地回答；不是可回答的提问 → None。

    None 表示「这句不该走 QA」（不是提问 / 是改请求 / 没有方案数据），交回上层兜底。
    查到字段 → 模板答；提问但查不到字段 → 弃答（诚实 + 标注经验）。
    """
    if not looks_like_question(user_input):
        return None
    if looks_like_explicit_revise(user_input):
        return None  # "帮我换成…吗" 是改请求
    if any(h in user_input for h in _CHANGE_REQUEST_HINTS):
        return None  # "近一点吗" 是改请求
    places = _resolve_places(itinerary)
    if not places:
        return None  # 没方案数据可对照，交回兜底
    field = _detect_field(user_input)
    if field is not None:
        ans = _FIELD_ANSWERERS[field](places)
        if ans:
            return ans
    # 是提问、但字段查不到（停车/wifi/支付…）→ 诚实弃答 + 经验
    return _abstain(user_input, client)


def build_question_decision(
    user_input: str,
    itinerary: Any,
    *,
    client: LLMClient | None = None,
) -> RouterDecision | None:
    """把提问回答包成走 chitchat 气泡的 RouterDecision；不是提问 → None。"""
    answer = answer_itinerary_question(user_input, itinerary, client=client)
    if not answer:
        return None
    return RouterDecision(
        input_kind=InputKind.CHITCHAT,  # 复用闲聊出口：输出一段回话、不改方案、不重规划
        confidence=0.85,
        reply_text=answer,
        tone="neutral",
        cta_chips=[],
        rationale="itinerary_question_answered",
    )


__all__ = [
    "looks_like_question",
    "answer_itinerary_question",
    "build_question_decision",
]
