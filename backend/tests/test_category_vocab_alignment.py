"""三套品类词汇（intent 词典 / mock 数据 / poi_desire_match）对齐回归测试。

背景：真 LLM 冒烟测试复现"诚实告知说谎"bug——用户说「KTV」，LLM 抽取诉求词
写成同义表达「K歌」，与 mock_data 里 POI.type 字面值 "KTV" 没有公共子串，
`poi_desire_match` 判定不相关 → 明明方案里排了 KTV，却告知用户"K歌没找到"。

根治方案（见 `schemas/category_vocab.py`）：建一张 canonical→别名 单一真相源，
`poi_desire_match` 先查等价表再退回双向 substring。本文件覆盖三层验收：

(a) mock 数据全量 POI type 值 —— 每个要么被词汇表/词典覆盖，要么是"孤儿"
    （没有词典快捷词，但用户说原词也能直接 substring 命中，非 bug）。孤儿
    集合按当前 mock_data 快照锁定；若变化，说明 mock_data 新增/改名了
    POI 类型——需要人工再判断"这是新孤儿（照旧，加进快照）还是新的 KTV 式
    失配（照 category_vocab.py 的来源纪律补别名）"。
(b) intent 词典 prompt 里「明示餐饮/活动品类必须保留」段落逐字列出的例词
    ⊆ 词汇表 canonical 集——prompt 加了新例词却忘记登记词汇表，本测试变红。
(c) 冒烟原案回归：desire="K歌" × 行程里有 type="KTV" 的 POI → 相关判定
    命中，`detect_unmet_poi_preference` 不再误报"K歌"未满足。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent.intent.narrator import detect_unmet_poi_preference
from agent.intent.prompts.intent_parser_prompt import INTENT_PARSER_SYSTEM_PROMPT
from schemas.category_vocab import poi_desire_match
from schemas.category_vocab import all_canonical_terms, canonical_equivalent
from schemas.tags import DIETARY_TAGS, EXPERIENCE_TAGS, PHYSICAL_TAGS

_MOCK_DIR = Path(__file__).resolve().parents[2] / "mock_data"


def _load_poi_types() -> list[str]:
    with (_MOCK_DIR / "pois.json").open(encoding="utf-8") as f:
        pois = json.load(f)
    return sorted({p.get("type", "") for p in pois if p.get("type")})


def _bidir_substring(a: str, b: str) -> bool:
    return bool(a) and bool(b) and ((a in b) or (b in a))


def _resolvable(poi_type: str, standard_words: set[str]) -> bool:
    """poi_type 能否被某个标准词覆盖（canonical 等价 或 双向 substring）。"""
    return any(
        canonical_equivalent(w, poi_type) or _bidir_substring(w, poi_type)
        for w in standard_words
    )


# ============================================================
# (a) mock POI type 全量扫描：孤儿值快照
# ============================================================

# 当前 mock_data/pois.json 快照（46 个 type 中的 39 个孤儿）：没有词典/词汇表
# 快捷词，但用户说这些词的原字面就能被 poi_desire_match 直接 substring 命中
# ——不是失配，是"词典没枚举全部类目"，符合"不闭门造词"纪律不需要额外别名。
_EXPECTED_ORPHAN_POI_TYPES = frozenset(
    {
        "DIY 工坊", "SPA", "livehouse", "主题乐园", "书店", "亲子乐园",
        "亲子博物馆", "亲子游乐场", "会客中心", "健身房", "儿童阅读馆",
        "剧本杀", "台球厅", "咖啡馆", "商务茶室", "商务茶馆", "图书馆",
        "城市公园", "城市观光", "复古街机", "复合体验馆", "复合空间",
        "庆典花园", "影像馆", "戏曲园", "游艇茶歇", "演出", "烘焙工坊",
        "猫咖", "瑜伽馆", "电影院", "私享空间", "私人定制", "美甲", "茶馆",
        "街区漫步", "运动步道", "酒吧", "雪茄会所",
    }
)


def test_poi_type_orphan_snapshot():
    """全量 POI type 扫描：非孤儿的必须能被词汇表/词典解出；孤儿集合锁定快照。"""
    standard_words = set(PHYSICAL_TAGS) | set(DIETARY_TAGS) | set(EXPERIENCE_TAGS) | set(
        all_canonical_terms()
    )
    poi_types = _load_poi_types()
    assert poi_types, "mock_data/pois.json 未加载到任何 POI type（检查数据路径）"

    orphans = {t for t in poi_types if not _resolvable(t, standard_words)}
    assert orphans == _EXPECTED_ORPHAN_POI_TYPES, (
        "POI type 孤儿集合发生变化——mock_data 新增/改名了 POI 类型。"
        "请人工判断：新孤儿是否与某个词典词是同一现实类目却字面不重合"
        "（KTV 式失配，应在 schemas/category_vocab.py 补别名）？"
        "若不是，把新孤儿加进本测试的 _EXPECTED_ORPHAN_POI_TYPES 快照。"
        f"\n实际孤儿：{sorted(orphans)}"
    )

    # 回归断言的核心：曾经的失配 case（KTV/展览/画廊）现在必须已解出
    for fixed in ("KTV", "展览", "画廊"):
        assert fixed not in orphans, f"{fixed!r} 应已被词汇表覆盖，不应再是孤儿"


# ============================================================
# (b) prompt 例词 ⊆ 词汇表 canonical 集
# ============================================================

# 精确定位 INTENT_PARSER_SYSTEM_PROMPT 里「明示餐饮/活动品类必须保留」段落的
# 两处例词列表（而非笼统抓全文所有「」引号——那会连"孩子 5 岁"这类例句也
# 抓进来，噪声太大）：
# 1.「词典内**没有**对应词的品类（如「撸串」「烧烤」…等）」
# 2.「像「看展」「网红打卡」这类既是 experience_tags 词典词、又是活动品类」
_CATEGORY_LIST_RE = re.compile(r"词典内\*\*没有\*\*对应词的品类（如(?P<list>(?:「[^」]+」)+)等）")
_MIRROR_RE = re.compile(r"像(?P<list>(?:「[^」]+」){2,})这类")


def _extract_quoted(s: str) -> list[str]:
    return re.findall(r"「([^」]+)」", s)


def _extract_prompt_category_words() -> list[str]:
    words: list[str] = []
    m = _CATEGORY_LIST_RE.search(INTENT_PARSER_SYSTEM_PROMPT)
    assert m, (
        "没在 INTENT_PARSER_SYSTEM_PROMPT 里找到「明示餐饮/活动品类必须保留」"
        "例词列表段落——prompt 措辞改了，需要同步更新本测试的抽取正则。"
    )
    words.extend(_extract_quoted(m.group("list")))
    m2 = _MIRROR_RE.search(INTENT_PARSER_SYSTEM_PROMPT)
    assert m2, "没找到「像「看展」「网红打卡」这类」镜像段落——prompt 措辞改了。"
    words.extend(_extract_quoted(m2.group("list")))
    return words


def test_prompt_category_words_subset_of_vocab():
    """prompt 里逐字列出的品类例词必须全部登记进 category_vocab 词汇表。"""
    prompt_words = _extract_prompt_category_words()
    assert prompt_words, "抽取到 0 个例词，正则可能失效"

    vocab = all_canonical_terms()
    missing = [w for w in prompt_words if w not in vocab]
    assert not missing, (
        f"prompt 提到但词汇表未登记的品类词：{missing}——"
        "在 schemas/category_vocab.py 的 _CATEGORY_ALIASES 里补一条"
        "（至少自映射；有失配证据再加别名）。"
    )


# ============================================================
# (c) 冒烟原案回归：desire="K歌" × type="KTV"
# ============================================================


def test_ktv_desire_matches_ktv_type_via_canonical_vocab():
    """desire='K歌'（LLM 真实产出的同义表达）应命中 type='KTV' 的 POI。"""
    assert poi_desire_match("K歌", "KTV", "麦霸欢唱 KTV · 旗舰店", ["热闹", "社交", "有包间", "室内"])
    # 反向也应成立（对称）
    assert poi_desire_match("KTV", "KTV", "星光量贩式 KTV", ["热闹", "社交"])
    # 其它 title_builder.py 里承认的同义变体也应命中
    for alias in ("唱K", "唱k", "k歌", "唱歌", "量贩式KTV"):
        assert poi_desire_match(alias, "KTV", "麦霸欢唱 KTV", []), f"{alias!r} 应命中 KTV"


def test_detect_unmet_poi_preference_no_longer_false_reports_ktv():
    """冒烟原案：用户诉求「K歌」，行程里已经排了 KTV POI → 不应再误报未满足。"""
    unmet = detect_unmet_poi_preference(
        preferred_poi_types=["K歌"],
        itinerary_poi_types=["KTV"],
        itinerary_poi_names=["麦霸欢唱 KTV · 旗舰店"],
        itinerary_poi_tags=["热闹", "社交", "有包间", "室内"],
    )
    assert unmet == [], f"「K歌」应判定为已满足（行程里有 KTV），实际未满足列表：{unmet}"


def test_zhankan_desire_matches_exhibition_type():
    """「看展」应命中 type='展览'/'画廊'（即使 POI 恰好没打 "看展" tag，
    canonical 表也兜得住，不再依赖数据方记得打 tag）。"""
    assert poi_desire_match("看展", "展览", "西溪艺术展中心 · 当季特展", [])
    assert poi_desire_match("看展", "画廊", "云上私人画廊", [])
    assert poi_desire_match("展览", "画廊", "云上私人画廊", [])


@pytest.mark.parametrize(
    "desire,poi_type",
    [("K歌", "KTV"), ("看展", "展览"), ("看展", "画廊"), ("展览", "画廊")],
)
def test_canonical_equivalent_symmetric(desire: str, poi_type: str):
    """canonical_equivalent 对称——顺序不影响判定结果。"""
    assert canonical_equivalent(desire, poi_type) == canonical_equivalent(poi_type, desire)
