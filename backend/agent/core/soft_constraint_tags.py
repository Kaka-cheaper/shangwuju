"""agent.core.soft_constraint_tags —— 弦外之音关键词 → 词典 tag 规则表（纯数据）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
沿革（对话轮路由规则层重构，2026-07-12）：

  这份关键词→tag 规则表原是 `agent/core/soft_constraint_sniffer.py`（已删除）
  的一部分，服务于该模块已删除的**路由角色**——"提约束·没说改 → 在规则层
  直接短路成主动问气泡"。BLOCK 1 决策 #2/B'：不建独立安全网，这类判定改由
  路由脑子少样本承接（`agent/routing/brain_prompt.py` BRAIN_FEW_SHOTS），
  brain 判 clarify 时代码拼一颗「换成X的」chip（`schemas/router.py:54`
  纪律：send 不许 LLM 发明）。

  这颗 chip 需要知道"换成哪个词典 tag"——brain 本身不做抽取（`agent/routing/
  brain.py` 模块 docstring："脑子不顺产 intent 草稿"），所以判定"这句话隐含
  哪个词典 tag"这一步，不能塞进 brain 的 LLM 输出字段，而是在 brain 判定
  `clarify` 之后，**独立于 LLM 判断内容**，直接对原始用户输入跑这份关键词
  规则表——命中即用于 chip 内容 enrichment（不参与是否短路的判定，只参与
  chip 里填什么词）。这与旧模块的核心区别：旧版命中即在规则层直接拍板路由
  （零成本短路，不问脑子）；新版判 clarify 已经由脑子决定，这份表只负责
  "chip 上写什么"，是纯粹的内容生成辅助，不再是路由判据。

  这份表本身的关键词/tag 映射逻辑未变（沿用规则表原文），只是消费方从
  "route_turn.py 的 Layer 1.8 短路判定" 变成 "brain.py 的 clarify chip 内容
  生成"。

不负责：
  - LLM 兜底嗅探（旧 `sniff_llm`，随路由角色一起删除——隐晦表达现在整体交给
    脑子理解上下文后自行判 clarify，不再需要专门的 LLM 兜底调用）；
  - chip 构造 / RouterDecision 拼装（在 `agent/routing/brain.py`）；
  - 词典出口防御以外的任何路由判定。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

from dataclasses import dataclass

from schemas.tags import DIETARY_TAGS, EXPERIENCE_TAGS, PHYSICAL_TAGS

# 软约束只可能落在「物理 / 饮食 / 体验」三类硬 tag 词典里（social_context 是单值
# 场景，不属于「随口提一句的硬约束」范畴，故不纳入）。
VALID_SOFT_TAGS: frozenset[str] = PHYSICAL_TAGS | DIETARY_TAGS | EXPERIENCE_TAGS


@dataclass(frozen=True)
class SoftConstraintHit:
    """一次关键词规则表命中。

    tags   : 命中的**词典内** tag（已保证 ⊆ VALID_SOFT_TAGS）。
    empathy: 一句共情回话，可用于 chip 相邻文案的共情前缀。
    """

    tags: tuple[str, ...]
    empathy: str = ""


# ============================================================
# 规则表：弦外之音关键词 → 词典 tag（沿用原 soft_constraint_sniffer._RULE_TABLE）
# ============================================================
# 纪律（沿用未变）：
# - 关键词用「具体多字词」，避开「累 / 老」这种会误吞（积累 / 老板）的单字；
# - tag 必须精确等于 schemas/tags.py 词典原词，否则下游 refiner 并入会被 Literal 拦；
# - empathy 是给 chip 相邻文案的共情句，写成人话、各条点到各自的痛点。
_RULE_TABLE: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (
        ("膝盖", "腿脚", "上年纪", "老人家", "年纪大了", "走不动", "走不远", "腿不好"),
        ("适合老人", "可休息"),
        "老人家腿脚不便，是得挑好走、能歇脚的地方。",
    ),
    (
        ("婴儿车", "推车", "抱娃", "背奶", "推着娃"),
        ("无台阶", "无障碍"),
        "推着娃出门，台阶和电梯确实得当回事。",
    ),
    (
        ("不能吃辣", "不太能吃辣", "怕辣", "不吃辣", "受不了辣", "一吃辣"),
        ("不辣",),
        "吃不了辣，那重口的就先给你避开。",
    ),
    (
        ("人太多", "怕挤", "怕人多", "太吵", "想清静", "想安静", "嫌吵", "图个清静"),
        ("安静聊天",),
        "想图个清静，人多的点就别凑了。",
    ),
    (
        ("好累", "乏了", "没精神", "想放松", "歇会", "歇歇", "想躺平", "提不起劲"),
        ("低强度", "可休息"),
        "今天想松快点，那就别给你排太满。",
    ),
    (
        ("孩子蔫", "娃困", "孩子累", "宝宝闹", "孩子犯困", "娃没精神"),
        ("低强度", "可休息"),
        "娃没什么精神，节奏放慢点稳妥。",
    ),
)


def sniff_tags(text: str) -> list[SoftConstraintHit]:
    """关键词命中 → 词典 tag（去重保序）。命中即出，不调 LLM，纯规则。"""
    if not text:
        return []
    out: list[SoftConstraintHit] = []
    seen_tags: set[str] = set()
    for keywords, tags, empathy in _RULE_TABLE:
        if any(k in text for k in keywords):
            fresh = tuple(t for t in tags if t not in seen_tags)
            if not fresh:
                continue
            seen_tags.update(fresh)
            out.append(SoftConstraintHit(tags=fresh, empathy=empathy))
    return out


def build_chip_send(hit: SoftConstraintHit) -> str:
    """把一次命中拼成 chip 的 send 文案（唯一的拼接模板，供 brain.py 与
    canonical_shortcut.py 共用，避免两处各写一份导致字面漂移）。

    「换成X的」chip 的核心纪律：send 全由代码模板 + 合法 tag 拼，LLM 不碰
    （`schemas/router.py:54`）。
    """
    tag_phrase = "、".join(hit.tags)
    reason = hit.empathy or tag_phrase
    return f"这版方案不合适，{reason}，帮我换成{tag_phrase}的"


def all_possible_chip_sends() -> frozenset[str]:
    """穷举规则表**全部**关键词组合可能产出的 chip send 文案。

    `_RULE_TABLE` 是有限的关键词表（非用户自由文本），故"这句话命中规则表
    第 N 条"的可能结果集合是有限、可枚举的——用于
    `agent.routing.canonical_shortcut` 把这些固定模板字符串注册进壳2 exact-
    match 集合（BLOCK 1 决策 #4：chip 的 send 归 canonical_shortcut 精确相等
    层管），使用户点击后的回传不必依赖 `looks_like_feedback_strong` 关键词
    二次辨认，直接 FP≈0 确定性短路成 feedback。
    """
    return frozenset(
        build_chip_send(SoftConstraintHit(tags=tags, empathy=empathy))
        for _keywords, tags, empathy in _RULE_TABLE
    )


__all__ = [
    "SoftConstraintHit",
    "VALID_SOFT_TAGS",
    "sniff_tags",
    "build_chip_send",
    "all_possible_chip_sends",
]
