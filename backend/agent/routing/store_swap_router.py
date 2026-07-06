"""agent.routing.store_swap_router —— B2："换个店铺"聊天反馈的分路判定。

【病灶（治的是这个）】用户在聊天里说"换个店铺"（自由文本反馈，不是点"换菜"
按钮）——`agent.intent.prompts.refiner_prompt` 的 C 类（"只是想换一个备选"）
明写：这类反馈 intent 字段基本不动，且系统"没有按店名排除的机制，没有任何
planner 路径按它排除实体"（该 prompt 的既定诊断，非本模块猜测）。于是"换个
店铺"→ 路由判 feedback → `refiner_node` → **全量重排**（execute 重搜同一批
候选 → planner/蓝图重新挑）→ 蓝图 LLM 大概率再选出同样的店 → **0 处变化**，
但叙事仍可能说"这版照你说的换了店铺"——诚实红线破防。

真正贴合"换店"语义的通道其实已经存在：`agent.planning.planners.node_swap.
resolve_node_swap`（ADR-0013 F-1 局部重解引擎）——按钮点击换菜走的就是它，
自带黑名单排除 + 三级降级 + 诚实兜底（见该模块 docstring）。`node_swap.py`
docstring 原文点破两条通道的分野："换菜按钮"（每节点、真换、诚实、三级降级）
vs "自由文本反馈"（全局重排、无排除）——"换个店铺"这句话本该走前一条通道，
现状却错落进了后一条。

【这是什么问题】对话行为分类（dialogue act classification）里的一个细分子类：
在"反馈"这个大类下，区分"要求替换当前实体"（换店，entity substitution）与
"要求调整其它维度"（距离/价格/节奏等，仍需全量重排）。这不是意图识别的语义
理解问题（不需要 LLM），而是**实体链接**（entity linking）问题：这句话有没有
点名方案里的某个具体地点？点没点名决定后续是"点名换店"（换那一个）还是
"泛化换店"（全部非锁定站点都换）。

【判定策略 + 为什么不需要 LLM】
1. **点名换店**（"换掉那家KTV" / "把老王烧烤换了"）：句子里提到了方案某个
   非 home 节点的名字（或名字的一个有辨识度的片段，如"麦霸欢唱 KTV · 旗舰店"
   里的"KTV"）——entity linking 从简（同 `agent.core.itinerary_qa.
   _resolve_places` 的既有纪律：不做完整指代消解，只用现场可达的结构化
   数据做双向 substring 判定）。恰好命中一个节点才算数；0 个或 ≥2 个命中都
   判"不是点名"（消歧失败，交给下一步"泛化"判定或维持现状，不猜是哪一个
   ——猜错=换错店，比"没识别出来、维持全局重排"代价更高）。
2. **泛化换店**（"换个店铺" / "换一批" / "都换换" / "换别的店" / "一个店都
   没改怎么回事"）：显式关键词表——这类短语几乎不可能出现在"全新需求"或
   "其它反馈"里（对比 `agent.core.feedback_detector` 强信号子集"每条词目须
   单独接近百分百精度，召回归脑子管"的既定精度纪律，本表同一原则：只收
   "几乎不会误判"的短语，不追求穷尽召回）。
3. **其它反馈**（"太远/太贵/太赶/换场景"）：两步都不命中 → 返回 `None`，
   调用方（`agent.graph.nodes.router.route_after_router`）维持现状路由到
   `refiner`（全局重排），行为完全不变——本模块只做"加法"，不改变任何既有
   反馈的路由结果。

不需要 LLM 调用：两类判据都是确定性字符串匹配（关键词表 / itinerary 节点
名称 substring），零延迟、可单测、不受 stub/真 LLM 环境影响——与
`agent.core.feedback_detector.looks_like_feedback_strong`（Layer 1 强信号，
同样不调 LLM）同一层次的设计选择。

【调用方 / 消费点】
`agent.graph.nodes.router.route_after_router`：`route_kind=="feedback"` 时
调 `classify_store_swap`，非 None → 路由到新图节点 `store_swap`（换全店/点名
换店编排，见 `agent.graph.nodes.store_swap`），维持 None → 路由到既有
`refiner`（全局重排，行为不变）。`store_swap_node` 内部会**再调一次**本函数
（同一份纯函数、同样的输入，结果必然一致）取回具体的 `mode`/`target_node_id`
——两处调用不共享一次计算结果，是因为 LangGraph 的条件边函数只能返回下一个
节点名（不能顺带向 state 写一个"顺便算好的中间结果"），重新算一次比新增一个
只为传这一个中间值的 state 字段更简单、也更不容易在两处漂移（纯函数、同输入
同输出，重算零风险）。

不负责：
- 换全店/点名换店的执行编排（黑名单叠加、循环调 `resolve_node_swap`、累积
  排除会话状态）——`agent.graph.nodes.store_swap`。
- "维持现状"分支（`None`）的全局重排本身——`agent.graph.nodes.refiner`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Optional

StoreSwapMode = Literal["all", "named"]


@dataclass(frozen=True)
class StoreSwapClassification:
    """`classify_store_swap` 的判定结果。

    `mode="named"` 时 `target_node_id` 是命中的那个非 home 节点的
    `ActivityNode.target_id`；`mode="all"` 时该字段恒为 `None`（"全部非锁定
    节点都换"不需要点名哪一个）。
    """

    mode: StoreSwapMode
    target_node_id: Optional[str] = None


# ============================================================
# 点名换店：entity linking 从简（同 itinerary_qa._resolve_places 纪律）
# ============================================================

_TITLE_SPLIT_RE = re.compile(r"[·・]")
_MIN_TOKEN_LEN = 2


def _title_segments(title: str) -> list[str]:
    """把节点 title（如"麦霸欢唱 KTV · 旗舰店"）拆成可辨识片段/词。

    mock 数据里餐厅/POI 命名惯例是"{系列名} · {分店/描述}"（见
    `data/mock_data/*.json` 实例），但用户口语化简称往往比"系列名"这个片段
    本身还短（"那家KTV"只提了"麦霸欢唱 KTV"这个片段里的后半个词，不是整段）
    ——先按分隔号切成片段，再按空白进一步切成词（"麦霸欢唱 KTV"→"麦霸欢唱"/
    "KTV"），任一词命中即算这个节点被点了名。`_MIN_TOKEN_LEN` 过滤掉单字词
    （中文单字太容易在任意句子里偶然出现，误判风险高于漏判——精度优先，同
    `feedback_detector` 强信号子集"规则层每条词目须接近百分百精度"的既定
    纪律）。片段/词本身没有空白可切时保留整段（如纯中文连写店名）。
    """
    raw = (title or "").strip()
    if not raw:
        return []
    tokens: list[str] = []
    for part in _TITLE_SPLIT_RE.split(raw):
        for word in part.split():
            w = word.strip()
            if len(w) >= _MIN_TOKEN_LEN and w not in tokens:
                tokens.append(w)
    return tokens


def find_named_swap_target(utterance: str, itinerary: Any) -> Optional[str]:
    """在当前方案的非 home 节点里找"用户点名要换的那一个"。

    恰好命中一个节点的 `target_id` 才返回（消歧成功）；0 个或 ≥2 个命中都
    返回 `None`（消歧失败——不确定具体是哪一个时，宁可交给"泛化换店"/"其它
    反馈"兜底，也不猜一个可能猜错的目标）。
    """
    text = (utterance or "").strip()
    if not text or not itinerary:
        return None
    nodes = getattr(itinerary, "nodes", None) or []
    matched: list[str] = []
    for node in nodes:
        if getattr(node, "target_kind", None) == "home":
            continue
        target_id = getattr(node, "target_id", None)
        if not target_id:
            continue
        segments = _title_segments(getattr(node, "title", "") or "")
        if any(seg in text for seg in segments):
            matched.append(target_id)
    if len(matched) == 1:
        return matched[0]
    return None


# ============================================================
# 泛化换店：显式关键词表（同 feedback_detector 强信号子集的精度纪律）
# ============================================================

_GENERIC_SWAP_KEYWORDS: tuple[str, ...] = (
    "换个店铺", "换个店", "换一批店", "换一批", "都换换", "换别的店",
    "换掉这些店", "这些店都换了", "都没换", "一个店都没改", "全部换掉",
    "都换一遍", "换套店铺", "都不满意换一批", "都不满意，换一批",
    "重新换一批店", "换换看别的店", "全都换了吧", "店都不满意换一批",
    "这几家店都换了", "全都不满意换一批", "换个地方吧", "换个地方",
)


def _looks_like_generic_swap(text: str) -> bool:
    return any(kw in text for kw in _GENERIC_SWAP_KEYWORDS)


# ============================================================
# 主入口
# ============================================================


def classify_store_swap(
    utterance: str, itinerary: Any
) -> Optional[StoreSwapClassification]:
    """B2 分路判定：这句反馈是"换店"（点名或泛化）还是"其它反馈"。

    Returns:
        `StoreSwapClassification` — 命中"换店"语义（点名优先于泛化判定：
        点名成功即认定是针对那一个节点，不再看是否也命中泛化关键词）；
        `None` — 两类都不命中，调用方维持既有全局重排路由。
    """
    if not itinerary or not getattr(itinerary, "nodes", None):
        return None
    text = (utterance or "").strip()
    if not text:
        return None

    named_target = find_named_swap_target(text, itinerary)
    if named_target is not None:
        return StoreSwapClassification(mode="named", target_node_id=named_target)

    if _looks_like_generic_swap(text):
        return StoreSwapClassification(mode="all")

    return None


__all__ = [
    "StoreSwapClassification",
    "classify_store_swap",
    "find_named_swap_target",
]
