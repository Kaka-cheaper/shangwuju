"""schemas.category_vocab —— 品类/活动词汇单一真相源（canonical → 别名集）。

【背景：三套词汇从未对齐（真 LLM 冒烟发现"诚实告知说谎"bug）】
本仓库有三套独立演化、从未互相校对过的品类/活动词汇：
1. intent 词典（`agent/intent/prompts/intent_parser_prompt.py` 的
   「明示餐饮/活动品类必须保留」段）：告诉 LLM 哪些自由文本词要原样写进
   `preferred_poi_types`（如「KTV」「看展」）。
2. mock 数据（`mock_data/pois.json` / `restaurants.json`）的 `type` / `tags` /
   `cuisine` 字面值：例如 POI 用英文 `"KTV"`，展览类用 `"展览"`/`"画廊"`。
3. `agent.runtime.tools.search_adapter.poi_desire_match`：纯双向 substring
   判定"诉求词是否与候选相关"，不认识任何同义词。

三者没有单一真相源时，LLM 真实产出的诉求词（如把「KTV」说成更口语的
「K歌」/「唱K」）与 mock 数据字面值（"KTV"）没有公共子串，
`poi_desire_match` 判定"不相关"——于是出现"方案里明明有 KTV，却告知
用户 K歌没找到"的诚实告知说谎 bug（真 LLM 冒烟复现）。

本模块把"canonical 品类词 → 别名集"钉成一张显式表，作为这类判定的**单一
真相源**：与 `schemas/tags.py` 三本 Literal 词典同一屋檐、不同关注点——
tags.py 管"LLM 允许输出哪些受控词"（词典出口防御），本模块管"哪些自由文本
词说的是同一件事"（品类等价判定）。

【来源纪律：不闭门造词】
表里每一条 canonical key 都来自 `INTENT_PARSER_SYSTEM_PROMPT`「明示餐饮/
活动品类必须保留」段落里逐字列出的例词（见
`backend/tests/test_category_vocab_alignment.py::test_prompt_category_words_subset_of_vocab`——
该测试从 prompt 源文本正则提取例词，断言 ⊆ 本表 canonical 集，prompt 加了新
例词却忘记登记本表 → 测试变红，防止两处再度失配）。
别名（多于自身的部分）只在**有具体证据**时才加：
- "KTV" 别名（K歌/唱K/唱歌/量贩…）：真 LLM 冒烟实测复现的 bug 本身，且与
  `agent/intent/title_builder.py` 的 `_ACTIVITY_VERB_HINTS` 里已经承认的
  KTV 同义词集合（`("KTV","ktv","唱K","唱k","K歌","k歌","量贩")`）同源——
  那张表管"怎么显示"，这张表管"怎么判相关"，是同一个现实世界等价类的两个
  不同消费方，故对齐用词，避免两处各说各话。
- "看展" 别名（展览/画廊）：mock_data 里这两个 POI.type 目前**恰好**都在
  `tags` 里带了字面 "看展"（P002/P008），才勉强靠 tags 命中；这层隐式依赖
  一旦某条新展览类数据忘记打 "看展" tag 就会重蹈 KTV 覆辙。本表把它显式化，
  不再指望"策展人记得打 tag"这种脆弱前提。
- "真人 CS" 别名（真人CS/真人cs）：纯空格/大小写变体，同一个词，不是新造类目。

其余 canonical 词（撸串/烧烤/夜宵/火锅/川菜/桌游/密室/攀岩/网红打卡）当前
没有发现类似的字面失配证据——原样登记（别名集只含自身），**不**为它们杜撰
额外同义词；如未来发现新的失配证据，照 KTV/看展 的方式补充别名，而不是绕开
本表另开补丁。

【cuisine 侧结论（同一份检查任务的一部分）】
本模块只覆盖 POI 侧（`poi_desire_match`/`detect_unmet_poi_preference`）。
经系统扫描 `agent.intent.narrator._CUISINE_HINT_TOKENS` + mock 餐厅
cuisine 值，未发现同类"标准词与实际值零字面重合但指同一品类"的失配
（`日料`/`粤菜`/`下午茶`/`甜品`/`烧烤`/`火锅` 等词典词与 mock cuisine 值
均已有子串命中；"东南亚菜/本帮菜/杭帮菜/法餐/湘菜/西餐/面食" 等 cuisine
值没有词典快捷词纯属"词典没枚举全部菜系"，靠 LLM 原样保留用户原词直接
substring 命中即可，不是失配）——因此 cuisine 侧暂不接入本表，见
`agent.intent.narrator._cuisine_match` 调用点注释。

【使用方】
- `agent.runtime.tools.search_adapter.poi_desire_match`：先查本表
  `canonical_equivalent`，再退回既有双向 substring（只增不减命中面）。
"""

from __future__ import annotations


# canonical 品类词 -> 别名 frozenset（canonical 自身也算一个"别名"，
# 便于统一用集合做等价判定；见 canonical_equivalent 实现）。
_CATEGORY_ALIASES: dict[str, frozenset[str]] = {
    # ---- 「明示餐饮/活动品类必须保留」段例词：字面已与 mock 数据对齐，
    # ---- 暂无已知失配证据，别名集只含自身（见模块 docstring 来源纪律）。
    "撸串": frozenset({"撸串"}),
    "烧烤": frozenset({"烧烤"}),
    "夜宵": frozenset({"夜宵"}),
    "火锅": frozenset({"火锅"}),
    "川菜": frozenset({"川菜"}),
    "桌游": frozenset({"桌游"}),
    "密室": frozenset({"密室"}),
    "攀岩": frozenset({"攀岩"}),
    "网红打卡": frozenset({"网红打卡"}),
    # ---- 有具体失配证据、需要额外别名的 canonical 词 ----
    "KTV": frozenset(
        {
            "KTV", "ktv", "唱K", "唱k", "K歌", "k歌", "唱歌",
            "量贩", "量贩式KTV", "量贩KTV",
        }
    ),
    "看展": frozenset({"看展", "展览", "画廊"}),
    "真人 CS": frozenset({"真人 CS", "真人CS", "真人cs", "真人 cs"}),
}


def canonical_equivalent(a: str, b: str) -> bool:
    """判断两个品类/活动词是否属于词汇表里同一个 canonical 等价类。

    大小写不敏感（覆盖 "KTV"/"ktv" 混用）；两词只要精确等于同一 canonical
    条目下的两个（可相同）别名即算等价。未登记的词一律返回 False——调用方
    应退回自己既有的宽松 substring 判定，本函数只**增强**命中面，不收窄。
    """
    if not a or not b:
        return False
    a_norm, b_norm = a.strip().lower(), b.strip().lower()
    if a_norm == b_norm:
        return True
    for canonical, aliases in _CATEGORY_ALIASES.items():
        norm_aliases = {canonical.lower()} | {x.lower() for x in aliases}
        if a_norm in norm_aliases and b_norm in norm_aliases:
            return True
    return False


def all_canonical_terms() -> frozenset[str]:
    """本表覆盖的全部词（canonical key + 别名），供测试做 prompt/词汇表
    同步校验（`test_category_vocab_alignment.py`）。"""
    out: set[str] = set()
    for canonical, aliases in _CATEGORY_ALIASES.items():
        out.add(canonical)
        out.update(aliases)
    return frozenset(out)


def canonical_keys() -> frozenset[str]:
    """本表全部 canonical key（不含别名），供测试遍历。"""
    return frozenset(_CATEGORY_ALIASES.keys())


__all__ = ["canonical_equivalent", "all_canonical_terms", "canonical_keys"]
