"""tests.test_wangjing_live_dataset —— 望京活集（仓库顶层 mock_data/）回归测试。

【背景：为什么本文件不用 conftest 的隔离拷贝】

`conftest.py` 的 autouse fixture `_isolate_tools_and_loader_cache` 把
`SHANGWUJU_MOCK_DIR` 指向 `mock_data/hangzhou/` 的 session 临时拷贝——这是为了
让 1700+ 存量测试（逐字锚定 R001/P040/P001 等杭州实体 ID）继续在杭州归档数据上
跑绿，不受"顶层 mock_data/ 已切换成望京现场演示集"这件事影响。

但本文件的职责恰恰相反：**验证望京活集本身**（评委路演当天真正会用到的那份
`mock_data/pois.json` / `restaurants.json` / `user_profile(s).json`）。因此每个
测试都必须显式把 env 指回仓库顶层 `mock_data/`（不能依赖 conftest 的隔离），
并在测试前后正确处理三层缓存：
1. `data.loader` 的 4 个 `lru_cache`（`reset_cache()`）
2. `agent.planning.commute.lookup_hop` 的 3 个模块级 `lru_cache`
   （`reset_cache()`，望京数据集切换批实测撞见的缺口——见 conftest.py
   `_reset_all_mock_caches` 的详细背景说明）
3. `agent.planning.blueprint.demand_scope._dining_cuisines`（独立 `lru_cache`，
   同一个模块 docstring 里点名"测试若切换 mock 数据集需显式 cache_clear()"）

`_wangjing_live_env` fixture 把这套动作封装成一个可复用的上下文，测试用完
（无论成功/失败）都会把 env 和缓存还原回 conftest 期望的状态，不泄漏给后续
测试（哪怕本文件本身在全量套件里跑在其它文件中间）。

【内容三块】
① 数据质量校验（可机器核查的验收规格：无英文占位、无 Sample 残留、
   suitable_for 全在九元枚举内、distance_km 全 ≤5、关键品类词法命中数达标）
② 8 场景召回冒烟（canonical_shortcut.DEMO_SCENARIOS 对应的核心诉求词在活集上
   词法命中 ≥1，且各自 social_context 对应 suitable_for 非空）
③ 烧烤锚复验（仿照 test_explicit_cuisine_anchor.py 的 3a：显式点名烧烤 +
   独处放空推断场景，在望京活集上真跑 search_restaurants_for_intent，
   断言 L1 anchor-escape 在新数据集上依然生效）
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WANGJING_MOCK_DIR = str(_REPO_ROOT / "mock_data")


def _reset_all_caches() -> None:
    from data.loader import reset_cache as _reset_loader_cache

    _reset_loader_cache()
    from agent.planning.commute.lookup_hop import reset_cache as _reset_hop_cache

    _reset_hop_cache()
    from agent.planning.blueprint.demand_scope import _dining_cuisines

    _dining_cuisines.cache_clear()


@pytest.fixture
def wangjing_live(monkeypatch):
    """把 SHANGWUJU_MOCK_DIR 显式改指仓库顶层 mock_data/（望京活集），
    测试前后清三层缓存，用完自动还原（monkeypatch 负责 env 还原，本 fixture
    负责缓存还原——不依赖 conftest 的隔离夹具）。"""
    monkeypatch.setenv("SHANGWUJU_MOCK_DIR", _WANGJING_MOCK_DIR)
    _reset_all_caches()
    yield
    _reset_all_caches()


# ============================================================
# ① 数据质量校验
# ============================================================


_SOCIAL_CONTEXTS = frozenset(
    {
        "家庭日常", "老人伴助", "闺蜜聊天", "朋友热闹", "情侣亲密",
        "商务接待", "同学重聚", "独处放空", "纪念日仪式感",
    }
)

# 英文占位残留探测：3 个及以上连续英文字母视为疑似未汉化文本；豁免真实品牌名
# （国际连锁在中国市场官方就用这些英文/半英文名，如 KTV/SOHO/MALL/UCCA 等，
# 属于"这本来就是它的名字"，不是"没翻译完"）。
_ENGLISH_RUN = re.compile(r"[A-Za-z]{3,}")
_BRAND_ALLOWLIST = re.compile(
    r"KTV|SOHO|MALL|UCCA|IKEA|Manner|blue frog|798|CS\b|NPC",
    re.IGNORECASE,
)


def _has_untranslated_english(text: str) -> bool:
    if not text:
        return False
    for m in _ENGLISH_RUN.finditer(text):
        if not _BRAND_ALLOWLIST.search(text[max(0, m.start() - 15) : m.end() + 15]):
            return True
    return False


def test_no_sample_placeholder_names(wangjing_live):
    """展示名字段不得残留 'Sample' 占位（路演会直接原样显示在行程卡上）。"""
    from data.loader import load_pois, load_restaurants

    offenders = [
        p.id for p in load_pois() if "sample" in p.name.lower()
    ] + [r.id for r in load_restaurants() if "sample" in r.name.lower()]
    assert not offenders, f"以下实体名字仍含 Sample 占位：{offenders}"


def test_no_untranslated_english_in_display_fields(wangjing_live):
    """菜名（signature_dishes）不得有未汉化的英文残留（品牌名允许，见
    _BRAND_ALLOWLIST）。

    口径收窄（2026-07-10 评论富化批 + 用户裁决）：`reviews` /
    `recommendation_reason` 两个**口语 prose 字段豁免**——真实评论天然混用
    拉丁品牌名/外来语（Wagas / livehouse / SPA / brunch / Gelato / App…），
    用户明确拍板"拉丁名保留、口语反而显真实"；prose 文风由评论富化流程的
    红线校验（禁平台点名/emoji/"不是X而是Y"修辞套路）+ 主代理抽样审查把关，
    不适合再用"含 3+ 拉丁字母即疑似未汉化"这把词法尺子扫。本检查的初衷
    （逮 Sample 式未汉化残留）由 `test_no_sample_placeholder_names` +
    本函数对菜名的检查继续承担。"""
    from data.loader import load_restaurants

    offenders: list[str] = []
    for r in load_restaurants():
        for d in r.signature_dishes:
            if _has_untranslated_english(d):
                offenders.append(f"{r.id}.signature_dishes: {d}")
    assert not offenders, f"以下菜名疑似未汉化：{offenders}"


def test_suitable_for_within_nine_enum(wangjing_live):
    """全部 suitable_for 值必须落在九元 SocialContext 枚举内（无野生值）。"""
    from data.loader import load_pois, load_restaurants

    offenders = []
    for p in load_pois():
        for s in p.suitable_for:
            if s not in _SOCIAL_CONTEXTS:
                offenders.append((p.id, s))
    for r in load_restaurants():
        for s in r.suitable_for:
            if s not in _SOCIAL_CONTEXTS:
                offenders.append((r.id, s))
    assert not offenders, f"以下 suitable_for 值不在九元枚举内：{offenders}"


def test_suitable_for_nine_enum_coverage_and_friends_lively(wangjing_live):
    """九元枚举每个 ≥1 个场所覆盖（POI+餐厅合计）；"朋友热闹"必须有像样覆盖
    （≥5，根治 wjc 交付时"朋友热闹 0 条"的已知回归目标）。"""
    from data.loader import load_pois, load_restaurants
    import collections

    counter: collections.Counter = collections.Counter()
    for e in list(load_pois()) + list(load_restaurants()):
        for s in e.suitable_for:
            counter[s] += 1

    zero_coverage = [s for s in _SOCIAL_CONTEXTS if counter[s] == 0]
    assert not zero_coverage, f"以下 social_context 望京活集零覆盖：{zero_coverage}（{dict(counter)}）"
    assert counter["朋友热闹"] >= 5, (
        f"「朋友热闹」覆盖不足（实际 {counter['朋友热闹']}）——预设场景 S4"
        f"「朋友 4 人 2 男 2 女」的 social 硬闸会被打空。全量分布：{dict(counter)}"
    )


def test_distance_km_within_5km_of_venue(wangjing_live):
    """全部望京实体 distance_km ≤5（会场坐标重算后的直线距离语义自洽）。
    终极批例外:坐标校正到高德真店后,已报备白名单(见⑤节 _KNOWN_OVER_5KM)
    允许超出——真店真坐标,不夹不删。"""
    from data.loader import load_pois, load_restaurants

    offenders = [
        (e.id, e.distance_km)
        for e in list(load_pois()) + list(load_restaurants())
        if e.distance_km > 5.0 and e.id not in {"WJR054"}
    ]
    assert not offenders, f"以下实体 distance_km 超过 5km：{offenders}"


def test_bbq_cuisine_literal_hit_at_least_two(wangjing_live):
    """硬指标：「烧烤」词法命中 ≥2 家（撸串场景是招牌场景，也是 anchor-escape
    bug 根治批的现场复验点——见 ③ 更完整的端到端复验）。"""
    from data.loader import load_restaurants
    from schemas.category_vocab import restaurant_desire_match

    rests = load_restaurants()
    hits = [r.id for r in rests if restaurant_desire_match(["烧烤"], r.cuisine)]
    assert len(hits) >= 2, f"「烧烤」词法命中不足 2 家：{hits}"


def test_key_category_literal_hits(wangjing_live):
    """8 场景相关的核心品类/活动词法命中数达标（把「三、」审计表的失灵项在
    整改后逐条转成断言）。"""
    from data.loader import load_pois
    from schemas.category_vocab import poi_desire_match

    pois = load_pois()

    def hits(desire: str) -> list[str]:
        return [p.id for p in pois if poi_desire_match(desire, p.type, p.name, p.tags)]

    expectations = {
        "KTV": 1,
        "K歌": 1,
        "电影": 1,
        "公园": 1,
        "看展": 1,
        "桌游": 1,
        "密室": 1,
    }
    failures = {}
    for desire, min_count in expectations.items():
        n = len(hits(desire))
        if n < min_count:
            failures[desire] = n
    assert not failures, f"以下品类/活动词法命中数低于预期：{failures}"


def test_age_range_only_set_for_kid_venues(wangjing_live):
    """age_range 只在真正的亲子场所上设置（search_pois 语义：None=不参与年龄
    过滤，缺失≠禁止；只有需要精确年龄闸门的场所才该填）。"""
    from data.loader import load_pois

    kid_like = [
        p for p in load_pois() if p.age_range is not None
    ]
    # 每条有 age_range 的 POI 名字/type 都应该是亲子相关（弱校验：不强制要求
    # 覆盖多少条，只要求"填了的都合理"，避免过度断言脆弱）
    for p in kid_like:
        assert "亲子" in p.type or "亲子" in p.name or "儿童" in p.name or "儿童" in p.type, (
            f"{p.id} 填了 age_range={p.age_range} 但名字/type 看不出是亲子场所：{p.name}/{p.type}"
        )


# ============================================================
# ② 8 场景召回冒烟
# ============================================================

# 对应 agent/routing/canonical_shortcut.py::DEMO_SCENARIOS（S1-S8），每项给出
# 该场景的核心诉求词（词法命中探针）+ social_context（suitable_for 非空探针）。
_SCENARIO_PROBES = [
    ("S1_KTV学生党", ["KTV", "K歌"], "朋友热闹"),
    ("S2_兄弟撸串", ["烧烤"], "朋友热闹"),
    ("S3_家庭亲子", ["亲子乐园", "亲子"], "家庭日常"),
    ("S4_朋友4人", ["拍照", "社交"], "朋友热闹"),
    ("S5_情侣看展", ["看展"], "情侣亲密"),
    ("S6_闺蜜下午茶", ["下午茶", "网红打卡"], "闺蜜聊天"),
    ("S7_商务接待", ["商务体面", "有包间"], "商务接待"),
    ("S8_独处放空", ["独处舒缓"], "独处放空"),
]


@pytest.mark.parametrize("scenario_id,desire_terms,social_context", _SCENARIO_PROBES)
def test_scenario_recall_smoke(wangjing_live, scenario_id, desire_terms, social_context):
    """每个预设场景：核心诉求词在活集上词法命中 ≥1（POI 或餐厅任一），且该场景
    social_context 对应的 suitable_for 非空（否则 social_context 硬闸会全灭）。"""
    from data.loader import load_pois, load_restaurants
    from schemas.category_vocab import poi_desire_match, restaurant_desire_match

    pois = load_pois()
    rests = load_restaurants()

    any_hit = False
    for term in desire_terms:
        if any(poi_desire_match(term, p.type, p.name, p.tags) for p in pois):
            any_hit = True
            break
        if any(restaurant_desire_match([term], r.cuisine) for r in rests):
            any_hit = True
            break
        # 体验词也可能只在 tags 里（如"社交"/"独处舒缓"/"商务体面"这类
        # experience_tags，不一定构成 type/cuisine 的字面命中，需要单独查 tags）
        if any(term in (p.tags or []) for p in pois) or any(
            term in (r.tags or []) for r in rests
        ):
            any_hit = True
            break
    assert any_hit, f"[{scenario_id}] 诉求词 {desire_terms} 在望京活集上词法命中为空"

    social_hit = any(social_context in p.suitable_for for p in pois) or any(
        social_context in r.suitable_for for r in rests
    )
    assert social_hit, f"[{scenario_id}] social_context={social_context!r} 望京活集 suitable_for 零覆盖"


# ============================================================
# ③ 烧烤锚复验（仿 test_explicit_cuisine_anchor.py 3a，在望京活集上真跑）
# ============================================================


def test_explicit_bbq_anchor_recalled_on_wangjing_dataset(wangjing_live):
    """显式点名『烧烤』+ 独处放空推断场景 → L1 anchor-escape 应让热闹烧烤真
    召回（不被推断场景在工具层硬删）——这是刚修完的 anchor-escape 链路在望京
    活集上的复验，防止"链路修好了、但新数据词法不可中导致空转"的回归。"""
    from agent.runtime.tools.search_adapter import search_restaurants_for_intent
    from schemas.intent import IntentExtraction

    intent = IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 6],
        distance_max_km=5.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=["独处舒缓"],
        social_context="独处放空",
        preferred_poi_types=["烧烤"],
        raw_input="我想吃个烧烤",
        parse_confidence=0.9,
    )
    rests, _ = search_restaurants_for_intent(intent)
    bbq_ids = [r.id for r in rests if "烧烤" in (r.cuisine or "")]
    assert bbq_ids, (
        "L1 anchor-escape 应在望京活集上让显式点名的烧烤真召回；"
        f"实际召回={[(r.id, r.cuisine) for r in rests]}"
    )


def test_case_b_no_explicit_desire_keeps_scene_filter_on_wangjing(wangjing_live):
    """case(b) 反断言：无显式诉求时，独处放空推断场景仍应硬过滤热闹烧烤——
    证明 L1 只放松了显式锚，没有在望京数据集上意外砸穿场景硬闸。"""
    from agent.runtime.tools.search_adapter import search_restaurants_for_intent
    from schemas.intent import IntentExtraction

    intent = IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 6],
        distance_max_km=5.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=["独处舒缓"],
        social_context="独处放空",
        preferred_poi_types=[],
        raw_input="我想吃个烧烤",
        parse_confidence=0.9,
    )
    rests, _ = search_restaurants_for_intent(intent)
    bbq_ids = [r.id for r in rests if "烧烤" in (r.cuisine or "")]
    assert not bbq_ids, (
        "case(b) 无显式诉求不应在望京活集上召回热闹烧烤（场景硬闸保留）；"
        f"实际召回={[(r.id, r.cuisine) for r in rests]}"
    )


# ============================================================
# ④ 数据补齐批（2026-07-10 加码版）目标表逐项断言
#    —— 每个数字都对应任务书的一条硬指标；掉下去 = 数据回归
# ============================================================


def _load_all(wangjing_live=None):
    from data.loader import load_pois, load_restaurants

    return list(load_pois()), list(load_restaurants())


def test_physical_hard_tags_minimum_coverage(wangjing_live):
    """四个物理硬标签（适合老人/无台阶/可休息/无障碍）各 ≥3 家（POI+餐厅合计）。
    打标纪律：商场/室内公共空间/公园主路如实推断；密室/桌游/精酿吧绝不打。"""
    pois, rests = _load_all()
    entities = pois + rests
    failures = {}
    for tag in ("适合老人", "无台阶", "可休息", "无障碍"):
        n = sum(1 for e in entities if tag in (e.tags or []))
        if n < 3:
            failures[tag] = n
    assert not failures, f"物理硬标签覆盖不足（各需 ≥3）：{failures}"
    # 反向红线：刺激/嘈杂场所不得挂物理无障碍类标签
    for p in pois:
        if p.type in ("密室", "桌游馆", "酒吧"):
            bad = {"适合老人", "无台阶", "无障碍"} & set(p.tags or [])
            assert not bad, f"{p.id}({p.type}) 不应挂物理硬标签：{bad}"


def test_dietary_tags_minimum_coverage(wangjing_live):
    """饮食标签：不辣 ≥3、软烂 ≥2（餐厅侧；粥/蒸菜/日料/轻食/汤面类如实）。"""
    _, rests = _load_all()
    n_bula = sum(1 for r in rests if "不辣" in (r.tags or []))
    n_ruanlan = sum(1 for r in rests if "软烂" in (r.tags or []))
    assert n_bula >= 3, f"「不辣」标签餐厅不足 3 家（实际 {n_bula}）"
    assert n_ruanlan >= 2, f"「软烂」标签餐厅不足 2 家（实际 {n_ruanlan}）"


def test_poi_category_targets(wangjing_live):
    """POI 品类目标表：KTV≥3、亲子乐园≥4、桌游馆/台球厅/密室/书店 各≥2、
    展览≥3（S5 换菜池）、下午茶适配 POI（tags 含「下午茶」）≥4、
    商务体面 POI（tags 商务体面 且 suitable_for 商务接待）≥3。"""
    pois, _ = _load_all()
    from collections import Counter

    types = Counter(p.type for p in pois)
    targets = {"KTV": 3, "亲子乐园": 4, "桌游馆": 2, "台球厅": 2, "密室": 2, "书店": 2, "展览": 3}
    failures = {t: types[t] for t, need in targets.items() if types[t] < need}
    assert not failures, f"POI 品类数量低于目标：{failures}（目标 {targets}）"

    tea = [p.id for p in pois if "下午茶" in (p.tags or [])]
    assert len(tea) >= 6, f"下午茶适配 POI 不足 6：{tea}"

    biz = [
        p.id for p in pois
        if "商务体面" in (p.tags or []) and "商务接待" in (p.suitable_for or [])
    ]
    assert len(biz) >= 6, f"商务体面 POI（茶室/会客）不足 6：{biz}"

    solo = [p.id for p in pois if "独处舒缓" in (p.tags or [])]
    assert len(solo) >= 6, f"独处舒缓 POI 不足 6：{len(solo)}"
    social = [p.id for p in pois if "朋友热闹" in (p.suitable_for or [])]
    assert len(social) >= 6, f"朋友聚会向 POI 不足 6：{len(social)}"


def test_poi_category_floors_ultimate(wangjing_live):
    """终极批 POI 品类地板（高德周边搜索加码后锁死）：
    8 场景主品类各 ≥6；长尾各 ≥3；猫咖/保龄为「现实封顶」——
    around 5km 半径实际各仅 1 家可用（撸猫咖啡馆在 8km 外、Xmax 暂停营业），
    地板即现实,不造假凑数。"""
    pois, _ = _load_all()
    from collections import Counter

    types = Counter(p.type for p in pois)
    targets = {
        "KTV": 6, "亲子乐园": 6, "展览": 6, "书店": 6, "城市公园": 6,
        "桌游馆": 4, "台球厅": 4, "密室": 4, "画廊": 4, "电影院": 4,
        "剧本杀": 3, "瑜伽馆": 3, "SPA": 3, "轰趴馆": 3, "健身房": 3,
        "烘焙工坊": 3, "猫咖": 1, "保龄球馆": 1,
    }
    failures = {t: types[t] for t, need in targets.items() if types[t] < need}
    assert not failures, f"POI 品类低于地板：{failures}"
    n_tea = types["茶馆"] + types["商务茶室"]
    assert n_tea >= 6, f"茶空间（茶馆+商务茶室）不足 6：{n_tea}"


def test_restaurant_category_targets(wangjing_live):
    """餐厅品类地板（终极批锁死）：烧烤≥6、烤肉串类家族≥8、主要菜系
    （火锅/川/湘/粤/日料/韩/西餐/轻食/面食）各≥4、烤鸭≥3、
    商务宴请池≥6、纪念日仪式感全集≥4 且餐厅≥2。"""
    pois, rests = _load_all()
    from schemas.category_vocab import restaurant_desire_match

    bbq = [r.id for r in rests if restaurant_desire_match(["烧烤"], r.cuisine)]
    assert len(bbq) >= 6, f"「烧烤」词法命中不足 6：{bbq}"

    def cz(*words):
        return [r.id for r in rests if any(w in (r.cuisine or "") for w in words)]

    floors = {
        "烤肉串类": (cz("烧烤", "烤肉", "串"), 8),
        "火锅": (cz("火锅", "涮"), 4), "川菜": (cz("川菜"), 4),
        "湘菜": (cz("湘", "湖南"), 4), "粤菜": (cz("粤"), 4),
        "日料": (cz("日料", "日式"), 4), "韩餐": (cz("韩"), 4),
        "西餐": (cz("西餐", "披萨"), 4), "轻食": (cz("轻食"), 4),
        "面食": (cz("面", "粉"), 4), "烤鸭": (cz("烤鸭"), 3),
    }
    fails = {k: len(pool) for k, (pool, need) in floors.items() if len(pool) < need}
    assert not fails, f"菜系地板不达标：{fails}"

    biz_r = [r.id for r in rests if "商务接待" in (r.suitable_for or [])]
    assert len(biz_r) >= 6, f"商务宴请池（suitable_for 商务接待）不足 6：{biz_r}"

    anniv_r = [r.id for r in rests if "纪念日仪式感" in (r.suitable_for or [])]
    anniv_p = [p.id for p in pois if "纪念日仪式感" in (p.suitable_for or [])]
    assert len(anniv_r) >= 2, f"纪念日仪式感餐厅不足 2：{anniv_r}"
    assert len(anniv_r) + len(anniv_p) >= 4, (
        f"纪念日仪式感全集不足 4：R={anniv_r} P={anniv_p}"
    )


def test_birthday_service_targets_valid_and_ritual(wangjing_live):
    """extra_services 生日布置（XS003）的 target_ids 必须全部指向真实存在的
    望京餐厅，且 ≥1 家带「纪念日仪式感」suitable_for（服务与数据不脱节）。"""
    import json
    from pathlib import Path

    _, rests = _load_all()
    by_id = {r.id: r for r in rests}
    extra = json.loads(
        (Path(_WANGJING_MOCK_DIR) / "extra_services.json").read_text(encoding="utf-8")
    )
    xs003 = next(s for s in extra if s["id"] == "XS003")
    dangling = [t for t in xs003["target_ids"] if t not in by_id]
    assert not dangling, f"XS003 target_ids 指向不存在的餐厅：{dangling}"
    ritual = [t for t in xs003["target_ids"] if "纪念日仪式感" in (by_id[t].suitable_for or [])]
    assert ritual, "XS003 目标餐厅中没有一家带「纪念日仪式感」suitable_for"


# 8 场景「换菜不空」：各场景主品类的同子类（type/cuisine 精确同词或词法家族）
# 场所数 ≥4（终极批升级）—— 行程卡"切换备选"至少能给出 3 个具名备选。
# 主品类来自 8 场景端到端冒烟的实际锚定结果（见交付报告"D"节）。
# S4 街区漫步为既有品类（望京无新增可考的真实"街区漫步"型场所）,保持 4 恰好达标。
_SWAP_POOLS = [
    ("S1_KTV", "poi_type", "KTV", 4),
    ("S2_烧烤", "cuisine_lexical", "烧烤", 4),
    ("S3_亲子", "poi_type", "亲子乐园", 4),
    ("S4_街区漫步", "poi_type", "街区漫步", 4),
    ("S5_展览", "poi_type", "展览", 4),
    ("S6_下午茶甜品", "cuisine_family", ("下午茶", "甜品", "甜点"), 4),
    ("S7_商务宴请", "biz_pool", None, 4),
    ("S8_城市公园", "poi_type", "城市公园", 4),
]


@pytest.mark.parametrize("label,kind,key,minimum", _SWAP_POOLS)
def test_scenario_swap_pool_not_empty(wangjing_live, label, kind, key, minimum):
    """换菜不空：每个演示场景主品类的候选池 ≥4（选 1 个还剩 ≥3 个具名备选）。"""
    pois, rests = _load_all()
    from schemas.category_vocab import restaurant_desire_match

    if kind == "poi_type":
        pool = [p.id for p in pois if p.type == key]
    elif kind == "cuisine_lexical":
        pool = [r.id for r in rests if restaurant_desire_match([key], r.cuisine)]
    elif kind == "cuisine_family":
        pool = [r.id for r in rests if any(k in (r.cuisine or "") for k in key)]
    else:  # biz_pool：商务宴请 = suitable_for 商务接待 的餐厅
        pool = [r.id for r in rests if "商务接待" in (r.suitable_for or [])]
    assert len(pool) >= minimum, f"[{label}] 换菜池不足 {minimum}：{pool}"


def test_reservation_slots_evening_capable(wangjing_live):
    """治「全集只有 17:00/17:30/18:00 三格」的时段贫化（evening 场景用餐节点
    静默丢失的根因）：全集时刻种类 ≥8，且烧烤池 ≥2 家在 18:30 及以后有可订
    时段（撸串场景晚上出发可落座）。"""
    _, rests = _load_all()
    from schemas.category_vocab import restaurant_desire_match

    all_times = {s.time for r in rests for s in (r.reservation_slots or [])}
    assert len(all_times) >= 8, f"全集预约时刻种类过少（{sorted(all_times)}）"

    bbq_evening = [
        r.id for r in rests
        if restaurant_desire_match(["烧烤"], r.cuisine)
        and any(s.time >= "18:30" and s.available for s in (r.reservation_slots or []))
    ]
    assert len(bbq_evening) >= 2, f"烧烤池 18:30+ 可订的不足 2 家：{bbq_evening}"


# ============================================================
# ⑤ 终极批（2026-07-10 高德真值版）几何一致性断言
#    家坐标 = 高德「望京数字创意园」官方词条（GCJ-02）；
#    场所坐标 = place/around·place/text 店级真值（校正不到的保留商圈锚）。
# ============================================================

_HOME_TRUTH = (40.006730, 116.484563)  # lat, lng
# 唯一超 5km 的真实店：将太无二真店在大屯金泉广场（第一批曾误锚望京）。
# 按任务书如实保留真坐标、不夹不删,处置权在主代理。
_KNOWN_OVER_5KM = {"WJR054"}


def _haversine(lat1, lng1, lat2, lng2):
    import math

    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lng2 - lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(a))


def test_home_location_is_amap_truth(wangjing_live):
    """demo 用户家坐标必须等于高德官方词条真值（两份 profile 一致）。"""
    import json
    from pathlib import Path

    for fn, path in (("user_profile.json", ()), ("user_profiles.json", ("demo_user",))):
        d = json.loads((Path(_WANGJING_MOCK_DIR) / fn).read_text(encoding="utf-8"))
        for k in path:
            d = d[k]
        loc = d["home_location"]
        assert (loc["lat"], loc["lng"]) == _HOME_TRUTH, (
            f"{fn} 家坐标 {loc} ≠ 高德真值 {_HOME_TRUTH}"
        )


def test_distance_km_consistent_with_coords(wangjing_live):
    """distance_km 必须与(家真值坐标, 场所坐标)的 haversine 一致（±0.02km）——
    防止坐标换真值后距离字段漏算。"""
    pois, rests = _load_all()
    bad = []
    for v in pois + rests:
        d = _haversine(_HOME_TRUTH[0], _HOME_TRUTH[1], v.location.lat, v.location.lng)
        if abs(d - v.distance_km) > 0.02:
            bad.append((v.id, v.distance_km, round(d, 3)))
    assert not bad, f"distance_km 与坐标不一致：{bad[:8]}"


def test_over_5km_whitelist(wangjing_live):
    """召回半径 5km:超出者必须在已报备白名单内(真店真坐标,不夹不删)。"""
    pois, rests = _load_all()
    over = {v.id for v in pois + rests if v.distance_km > 5.0}
    assert over <= _KNOWN_OVER_5KM, f"出现未报备的超 5km 场所：{over - _KNOWN_OVER_5KM}"


def test_routes_cover_every_venue(wangjing_live):
    """routes.json 必须给全库每个场所一条 home 边,分钟数与公式族一致口径
    （下限闸:步行≥4/打车≥3/公交≥5)。"""
    import json
    from pathlib import Path

    pois, rests = _load_all()
    routes = json.loads((Path(_WANGJING_MOCK_DIR) / "routes.json").read_text(encoding="utf-8"))
    homes = {r["to_location"]: r for r in routes if r["from_location"] == "home"}
    missing = [v.id for v in pois + rests if v.id not in homes]
    assert not missing, f"缺 home 边：{missing[:10]}"
    for r in homes.values():
        assert r["walking_minutes"] >= 4 and r["taxi_minutes"] >= 3 and r["bus_minutes"] >= 5


def test_physical_redline_extended_venues(wangjing_live):
    """打标红线（终极批扩充）：密室/桌游馆/酒吧/轰趴馆/剧本杀 绝不挂
    适合老人/无台阶/无障碍。"""
    pois, _ = _load_all()
    for p in pois:
        if p.type in ("密室", "桌游馆", "酒吧", "轰趴馆", "剧本杀"):
            bad = {"适合老人", "无台阶", "无障碍"} & set(p.tags or [])
            assert not bad, f"{p.id}({p.type}) 不应挂物理硬标签：{bad}"
