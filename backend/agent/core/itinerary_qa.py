"""agent.core.itinerary_qa —— 对已有行程方案的「提问」做接地问答（grounded QA）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
业务逻辑（锁定 · spec dialogue-act-routing C4）：

  问题：已有方案后用户问「这家贵不贵 / 这个公园远吗 / 要等位吗」，6 类里没有「提问」，
        被判成 ambiguous → 当反馈**重新规划**（答非所问）。

  成熟做法（每个决策点对应的范式）：
    1) 识别提问 = question / request-info 对话行为 → 疑问句式特征（吗/呢/?/多远/贵不贵…）。
    2) 接地问答（grounded QA）：答案**只能基于查到的数据**，不编造（编造=faithfulness
       hallucination）。
    3) 校准弃答（abstention）+ 来源标注：字段没对上就诚实弃答，可凭经验补一句、
       但**标注**是经验不是数据。措辞纪律（分界修缮批 任务 5）：弃答只坦白
       「没对上」，**不断言**「数据里没有记录」——cue 词表未命中是识别没接上，
       不等于数据缺失，假负面断言也是编造。
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
import re
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
# 任务 3：要解释类线索（单一真相源：问句识别与 why_rationale 字段映射两处消费、
# 一处定义）——要解释的问法常不带问尾（"为什么把餐厅排后面"），故也是问句线索。
_WHY_CUES = ("为什么", "为啥", "凭什么")
# 句中疑问线索
_QUESTION_CUES = (
    "多少", "几点", "多远", "多久", "多大", "几个", "几公里", "怎么样", "如何",
    "贵不贵", "远不远", "多不多", "有没有", "能不能", "是不是", "好不好", "贵吗", "远吗",
    # 「有什么招牌 / 招牌是啥 / 哪个近」——避开裸「什么」，防「没什么意思」误判成提问
    "有什么", "是什么", "什么好", "哪些", "哪个", "啥",
) + _WHY_CUES
# 比较级改请求线索（"近一点 / 再便宜"是要改，不是问）
_CHANGE_REQUEST_HINTS = ("一点", "点儿", "再近", "再远", "再便宜", "再贵", "近点", "远点")

# ── 否定辖域护栏（对话行为分类的 negation scope）──────────────────────────
# 「为什么没安排上烧烤 / 为啥没有烧烤」问的是**缺席**——一个用户点名要、却没进
# 方案的诉求，是"未满足诉求的追问 / 申诉"，语义上更接近 feedback（我还是要烧烤）
# 或"未满足约束的解释"，**不是**对某个**在场实体**的数据提问（"这家贵不贵"）。
# _answer_why 的既定假设是"为什么=解释在场的推荐/排序/定价"，对"为什么**没**X"
# 会抓个在场实体（离得最近的那家）背它的评分/距离——答非所问。故在 QA 入口把这类
# 剥出去（返 None → 落穿到脑子按 feedback/解释处理），与上面「疑问式改请求」
# （_CHANGE_REQUEST_HINTS / looks_like_explicit_revise 同样返 None 落穿）同一纪律。
# 精度优先（规则层无兜底）：只认 why-cue **紧跟**否定，或明确的排程否定短语——
# "为什么这么贵""为什么把餐厅排后面""为什么这家没wifi"都不命中（保持既有 QA）。
_WHY_NOT_RE = re.compile(r"(为什么|为啥|凭什么)(没|不给|不安排|不选|没能|没给)")
_UNMET_SCHEDULING_NEG = (
    "没安排", "没排上", "没排进", "没选上", "没给我安排", "没帮我安排", "没安排上",
)


def _looks_like_unmet_complaint(text: str) -> bool:
    """「为什么没安排上X / 为啥没有X」——对未满足诉求的追问/申诉，不是接地问答。"""
    t = (text or "").strip()
    if not t or not any(c in t for c in _WHY_CUES):
        return False
    if _WHY_NOT_RE.search(t):
        return True
    return any(neg in t for neg in _UNMET_SCHEDULING_NEG)


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
#
# 点火前小修批 任务 3（K7/K9/K10 实锤）追加末尾三个**方案级字段**——治
# 「数据在≠答得出」：为什么推荐/还有别的选/数据是真的吗 的材料（实体字段+
# 意图命中关系、narrate 预计算的 node_actions、产品事实边界）本来就在现场，
# 但此前词典只有实体数据字段，这三类问句全部落弃答。词表三纪律：
# ① 线索词单一真相源：新字段的 cue 与既有 8 个字段同表，不另起第二套词法；
# ② 漏配落既有弃答：材料拿不到（无 node_actions/备选为空/组句为空）→ 答复器
#    返 None → 主入口落 _abstain，绝不硬造；
# ③ 模板化接地生成，零 LLM 调用。
# 排在既有字段之后 = 「具体短语优先」既有纪律的延续（"为什么这么贵"若同时
# 命中价格具体短语则价格先答；实测既有 8 组无裸「贵」，why 仍接得住）。
#
# 【词目审查（9eecef0 精度契约：能想象日常语境里词目出现但用户不在问这个，
#  就剪；且全部先过 looks_like_question + 非改请求两道既有闸）】
# why_rationale：
#   - "为什么/为啥"——问句语境下几乎恒为要解释（为什么推荐/为什么这么排/
#     为什么这么贵）；用数据组句作答对全部变体语义不错位。
#   - "凭什么"——对抗式要解释（"凭什么排这家"），仍是要依据 → 数据组句正对。
#   - 剪掉候选"怎么想的"（出现率低且"你怎么想的"可以是闲聊征询）。
# alternatives：
#   - "别的选/别的选择/其他选择/其他选项/备选/别的推荐/其他推荐"——问句语境
#     下恒为求备选。剪掉候选"有没有别的"（"有没有别的时间"是在问时间不是问
#     备选）与"能换吗"（是改请求探路，归 feedback/adjust 域）。
# data_trust：
#   - "是真的吗/真的假的/随便编/瞎编/编造/骗我/骗人"——恒为质疑数据真伪 →
#     诚实边界话术正对。剪掉候选"靠谱吗"（"这家店靠谱吗"是在问商家口碑，
#     归 review 域，不是质疑系统数据）。
_FIELD_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("price", ("贵不贵", "贵吗", "多少钱", "人均", "价格", "便宜吗", "划算吗", "贵不", "性价比")),
    ("distance", ("多远", "远不远", "远吗", "距离", "多久到", "几公里", "近吗", "远近")),
    ("hours", ("几点", "开门", "关门", "营业", "开到几点", "几点关", "几点开")),
    ("elderly_kid", ("适合老人", "老人能", "适合孩子", "适合几岁", "有台阶", "无障碍", "轮椅", "老人去", "带娃合适")),
    ("queue", ("要等", "排队", "等位", "人多吗", "人多不多", "挤吗", "等多久", "要等多久", "满不满")),
    ("capacity", ("包间", "能坐", "坐得下", "几个人", "多少人", "位子")),
    ("review", ("评分", "评价", "口碑", "好吃吗", "好玩吗", "怎么样", "好不好", "值不值")),
    ("signature", ("招牌", "特色菜", "必点", "什么好吃", "推荐菜", "拿手")),
    # ---- 方案级字段（任务 3）：不吃 places 单参，分发见 answer_itinerary_question ----
    ("why_rationale", _WHY_CUES),
    ("alternatives", ("别的选", "别的选择", "其他选择", "其他选项", "备选", "别的推荐", "其他推荐")),
    ("data_trust", ("是真的吗", "真的假的", "随便编", "瞎编", "编造", "骗我", "骗人")),
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
# 4b. 方案级答复器（点火前小修批 任务 3；K7/K9/K10 实锤）
# ============================================================
# 三个答复器全部只用现场可达的结构化数据组句（模板化接地生成，零 LLM）；
# 材料拿不到时返 None → 主入口落既有 _abstain（漏配纪律，见 _FIELD_CUES 注释）。
# 出口统一钳制（_clamp_reply）：与弃答同一根因（RouterDecision.reply_text
# max_length=400，超限 = router 层 ValidationError → 整轮 stream_error）。


def _clamp_reply(text: str) -> str:
    # 与弃答共用 _ABSTAIN_REPLY_MAX（第 5 节定义，调用期解析）——不另起第二个上限
    if len(text) > _ABSTAIN_REPLY_MAX:
        return text[: _ABSTAIN_REPLY_MAX - 1].rstrip() + "…"
    return text


def _iget(intent: Any, key: str, default: Any = None) -> Any:
    """intent 双形态取值：图状态是 IntentExtraction 对象，房间路径是 dict
    （room.current_intent_dict）——同一答复器两处消费，鸭子型取字段。"""
    if intent is None:
        return default
    if isinstance(intent, dict):
        val = intent.get(key, default)
    else:
        val = getattr(intent, key, default)
    return default if val is None else val


# 顺序类问法线索（"为什么把餐厅放在活动后面"）——命中时在组句前加方案时间轴，
# 让"这么排"的回答落在可核对的排程数据上，而不是只讲单店理由。
_ORDER_CUES = ("顺序", "后面", "前面", "这么排", "先去", "排在", "放在")


def _answer_why(
    places: list[Any], intent: Any, itinerary: Any, user_input: str
) -> str | None:
    """「为什么推荐这家 / 为什么这么排」→ 实体字段 × 意图命中关系组句。

    每个地点从五类**都在现场**的结构化字段里挑命中项：评分 / tag∩意图约束
    （品类命中你要的X）/ suitable_for∋social_context / 距离（对照
    distance_max_km）/ 人均（对照 budget_per_person）；餐厅另带数据集自带的
    recommendation_reason。intent 缺省（旧会话）→ 退化为纯实体字段组句。
    """
    wants: list[str] = []
    for f in ("dietary_constraints", "experience_tags", "physical_constraints"):
        wants += [str(x) for x in (_iget(intent, f) or [])]
    preferred_types = [str(x) for x in (_iget(intent, "preferred_poi_types") or [])]
    social = _iget(intent, "social_context")
    dist_max = _iget(intent, "distance_max_km")
    budget = _iget(intent, "budget_per_person")

    parts: list[str] = []
    for p in places:
        frags: list[str] = []
        tags = [str(t) for t in (getattr(p, "tags", []) or [])]
        tag_hits = [w for w in wants if w in tags]
        if tag_hits:
            frags.append(f"标着「{'、'.join(tag_hits[:2])}」，正对上你的要求")
        ptype = getattr(p, "type", None)
        if ptype and str(ptype) in preferred_types:
            frags.append(f"是你点名要的「{ptype}」")
        if social and str(social) in [str(s) for s in (getattr(p, "suitable_for", []) or [])]:
            frags.append(f"适配「{social}」场景")
        rating = getattr(p, "rating", None)
        if rating is not None:
            frags.append(f"评分 {rating}")
        d = getattr(p, "distance_km", None)
        if d is not None:
            seg = f"离家 {d} 公里"
            if dist_max and float(d) <= float(dist_max):
                seg += f"（在你要的 {dist_max} 公里内）"
            frags.append(seg)
        if _is_restaurant(p) and budget and float(p.avg_price) <= float(budget):
            frags.append(f"人均 {int(p.avg_price)} 元贴着你 {int(budget)} 元的预算")
        reason = getattr(p, "recommendation_reason", None)
        if reason:
            frags.append(str(reason))
        if frags:
            parts.append(f"{p.name}：{'，'.join(frags[:4])}")

    if not parts:
        return None

    lead = ""
    if any(c in user_input for c in _ORDER_CUES):
        timeline = _timeline_of(itinerary)
        if timeline:
            lead = f"先后顺序按方案时间轴来的：{timeline}。再说选点——"
    return _clamp_reply(lead + "；".join(parts) + "。这些都是方案数据里的字段，不是我现编的。")


def _timeline_of(itinerary: Any) -> str | None:
    """方案时间轴一行（接地于 nodes 的 start_time/title；home 首尾跳过）。"""
    try:
        data = itinerary.model_dump() if hasattr(itinerary, "model_dump") else dict(itinerary)
    except Exception:  # noqa: BLE001
        return None
    segs = []
    for n in data.get("nodes") or []:
        if not isinstance(n, dict) or n.get("target_kind") == "home":
            continue
        t, title = n.get("start_time"), n.get("title")
        if title:
            segs.append(f"{t} {title}" if t else str(title))
    return " → ".join(segs) if segs else None


def _answer_alternatives(itinerary: Any, node_actions_provider: Any) -> str | None:
    """「还有别的选吗」→ 报 narrate 预计算的具名备选（node_actions.alternatives，
    与「换成◯◯」按钮同一份预验证真相源）+ 一句引导。

    node_actions_provider 是惰性口子（`() -> {target_id: {chips, alternatives}}`）：
    单人路径由 router adapter 传图状态的 node_actions；房间路径传
    Room._snapshot_node_actions（现算，与 get_state_snapshot 同源）。只在本
    字段命中时才调用——别的问句一分钱都不付。拿不到 / 为空 / 抛异常 → None
    → 落既有弃答（诚实：识别到了但材料不在手上，不硬造备选名）。
    """
    if node_actions_provider is None:
        return None
    try:
        actions = node_actions_provider() or {}
    except Exception:  # noqa: BLE001
        logger.warning("alternatives 答复器取 node_actions 失败，落弃答", exc_info=True)
        return None
    if not actions:
        return None

    titles: dict[str, str] = {}
    try:
        data = itinerary.model_dump() if hasattr(itinerary, "model_dump") else dict(itinerary)
        for n in data.get("nodes") or []:
            if isinstance(n, dict) and n.get("target_id"):
                titles[n["target_id"]] = n.get("title") or n["target_id"]
    except Exception:  # noqa: BLE001
        pass

    parts: list[str] = []
    for target_id, acts in actions.items():
        names = [
            str(a.get("name"))
            for a in (acts or {}).get("alternatives") or []
            if isinstance(a, dict) and a.get("name")
        ]
        if not names:
            continue
        label = titles.get(target_id, target_id)
        parts.append(f"「{label}」可以换 {'、'.join(names[:2])}")
    if not parts:
        return None
    return _clamp_reply(
        "有的，这些备选都预验证过、能直接排进方案："
        + "；".join(parts)
        + "。想换哪一站，点那一站下面的「换成◯◯」小按钮就行，或者直接告诉我。"
    )


# 「你这数据是真的吗」→ 诚实边界话术（固定模板，零 LLM）。措辞红线：不得包含
# 任何虚假现实声称短语（真实库存/实时数据/已预订/预订成功……，K10 探针的
# _FAKE_REALITY_CLAIM_PHRASES 红线子集）——演示原型绝不宣称接了真实库存，
# 但机制（规划引擎/约束检查/下单流程）是真实在跑的，两半都要说清。
_DATA_TRUST_REPLY = (
    "跟你说实话：这是个演示原型，方案里的商家、价格、时段来自内置的演示数据集，"
    "不是线上现拉的；但规划引擎、约束检查、预约下单这套流程机制都是真实在跑的。"
    "真要按这个出门，出发前在 App 里再核对一遍商家信息最稳。"
)


# ============================================================
# 5. 弃答（abstention）+ 来源标注
# ============================================================

# A1（2026-07-04 prompt 防护补齐）：本调用位曾是全仓唯一"用户原始文本直喂 LLM +
# 无 L2 角色锁定无 L3 输入隔离 + 输出自由文本直接展示给用户"的位置——弃答文案
# 会原样进 chitchat 气泡，注入面最大，故用完整版 ROLE_LOCK_NOTICE（非 BRIEF）+
# wrap_user_input 补齐两道防线；行为语义（弃答+经验标注）不变。
#
# 分界修缮批 任务 5（2026-07-04 措辞判据变更）：弃答不再断言「方案数据里没有
# 这个信息/没有记录」——触发条件只是字段 cue 词表（_FIELD_CUES）未命中，这是
# **识别**没接上，不是**数据**缺失（数据可能明明有）；宣称数据缺失是对用户的
# 假负面断言，与"确定域的事实断言不许编造"同一纪律。改为坦白「没对上」
# （识别层面的诚实），弃答 + 经验标注语义保留。
_ABSTAIN_SYSTEM = (
    ROLE_LOCK_NOTICE + "\n\n"
    "用户在问关于一个已定下午行程的问题，但系统没能把这个问题对应到方案数据里的"
    "任何一项——可能数据里本来就没有，也可能只是没识别出来，你无法区分是哪种，"
    "所以**绝不要断言**『数据里没有这个信息』『没有记录』这类数据缺失的说法。"
    "请用一句中文坦诚说明这条你没对上方案里的数据，再凭常识给一句简短建议，"
    "并**明确标注**这是经验、不是查到的数据（如『一般来说…，到店问下最稳』）。不超过 60 字。"
)

# 弃答输出钳制：本函数返回值直塞 RouterDecision.reply_text（max_length=400），
# 超限 = router 层 ValidationError → 整轮 stream_error（I 类元对话探针 I3 在
# stub 下实锤：stub 固定 JSON 顶穿上限把弃答轮整个炸掉）。prompt 约束 60 字是
# 软约束，LLM 可能无视——这里是硬保险。380 留余量（下游若再拼措辞不至于贴边）。
_ABSTAIN_REPLY_MAX = 380


def _abstain(text: str, client: LLMClient | None) -> str:
    # 措辞纪律（任务 5，见 _ABSTAIN_SYSTEM 上方注释）：说「没对上」不说
    # 「没有记录」——识别未命中 ≠ 数据缺失，不下假负面断言。
    base = "你问的这个我没对上方案里的数据，"
    if client is None:
        return base + "凭经验建议到店或在 App 上确认一下。"
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
        if len(out) > _ABSTAIN_REPLY_MAX:
            out = out[: _ABSTAIN_REPLY_MAX - 1].rstrip() + "…"
        return out or (base + "凭经验建议到店或在 App 上确认一下。")
    except Exception:  # noqa: BLE001
        return base + "凭经验建议到店或在 App 上确认一下。"


# ============================================================
# 6. 主入口
# ============================================================

def answer_itinerary_question(
    user_input: str,
    itinerary: Any,
    *,
    client: LLMClient | None = None,
    intent: Any = None,
    node_actions_provider: Any = None,
) -> str | None:
    """对「关于已有方案的提问」给接地回答；不是可回答的提问 → None。

    None 表示「这句不该走 QA」（不是提问 / 是改请求 / 没有方案数据），交回上层兜底。
    查到字段 → 模板答；提问但查不到字段 → 弃答（诚实 + 标注经验）。

    任务 3 新增两个可选材料口（缺省 None = 材料不可达，相应方案级字段落弃答）：
    - intent：当前意图（IntentExtraction 或 dict，见 _iget）——why_rationale 用；
    - node_actions_provider：惰性取 node_actions 的 0 参 callable——alternatives 用。
    """
    if not looks_like_question(user_input):
        return None
    if looks_like_explicit_revise(user_input):
        return None  # "帮我换成…吗" 是改请求
    if any(h in user_input for h in _CHANGE_REQUEST_HINTS):
        return None  # "近一点吗" 是改请求
    if _looks_like_unmet_complaint(user_input):
        return None  # "为什么没安排上X" 是对缺席/未满足诉求的追问 → 落穿到脑子（feedback/解释），不背在场实体数据
    places = _resolve_places(itinerary)
    if not places:
        return None  # 没方案数据可对照，交回兜底
    field = _detect_field(user_input)
    ans: str | None = None
    if field == "why_rationale":
        ans = _answer_why(places, intent, itinerary, user_input)
    elif field == "alternatives":
        ans = _answer_alternatives(itinerary, node_actions_provider)
    elif field == "data_trust":
        ans = _DATA_TRUST_REPLY
    elif field is not None:
        ans = _FIELD_ANSWERERS[field](places)
    if ans:
        return ans
    # 是提问、但字段没对上（停车/wifi/支付…）或方案级材料不可达 → 弃答
    return _abstain(user_input, client)


def build_question_decision(
    user_input: str,
    itinerary: Any,
    *,
    client: LLMClient | None = None,
    intent: Any = None,
    node_actions_provider: Any = None,
) -> RouterDecision | None:
    """把提问回答包成走 chitchat 气泡的 RouterDecision；不是提问 → None。"""
    answer = answer_itinerary_question(
        user_input,
        itinerary,
        client=client,
        intent=intent,
        node_actions_provider=node_actions_provider,
    )
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
