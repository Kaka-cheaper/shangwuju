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

【ADR-0013 F-3：节点调整按钮（node_chips）】
- `generate_title_and_narration` 同次产出第三项 node_chips（`schemas.node_chip.
  NodeChip` 列表）：LLM 路径搭车 narrator 既有调用（JSON 增列，零额外延迟）；
  stub/rule 模式或 LLM 校验失败/缺字段时整体回落 `generate_template_node_chips`
  （按节点 kind + 实体字段/tags 的确定性模板，见该函数 docstring 规则表）。
- `generate_narration`（不产 title 的旧入口）不受影响，仍是 2 元素调用约定。
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from agent.core.llm_client import (
    LLMMessage,
    MIMO_THINKING_DISABLED_EXTRA_BODY as _MIMO_THINKING_DISABLED_EXTRA_BODY,
    get_llm_client,
)
from agent.intent.prompts.narrator_prompt import (
    NARRATOR_SYSTEM_PROMPT,
    build_narrator_user_message,
)
from agent.intent.title_builder import (
    build_xiaohongshu_title,
    companions_to_title_phrase,
    node_to_title_phrase,
)
from schemas.domain import Poi, Restaurant
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary
from schemas.node_adjustment import AMBIENCE_VALUES, NodeAdjustment, NodeAdjustmentDimension
from schemas.node_chip import NodeChip

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
    """双向 substring 宽松匹配（与 search_adapter._rerank_by_preferred_cuisine 同源）。

    未接入 `schemas.category_vocab`（POI 侧 `poi_desire_match` 已接入的
    canonical 等价表）：系统扫描 DIETARY_TAGS/`_CUISINE_HINT_TOKENS` 与
    mock 餐厅 cuisine 值后，未发现同类"标准词与实际值零字面重合但指同一
    品类"的失配（KTV/看展 那种 bug）——已有词典词（日料/粤菜/下午茶/甜品
    等）与 mock cuisine 值均已字面命中；没有词典词的 cuisine（东南亚菜/
    本帮菜/杭帮菜/法餐/湘菜/西餐/面食）靠 LLM 原样保留用户原词直接
    substring 命中即可，不是失配。故 cuisine 侧暂不引入词汇表，保持纯
    substring；若未来出现类似失配证据，按 `category_vocab.py` docstring
    的来源纪律补充，而不是就地加同义词特判。
    """
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


def split_unmet_by_nearby_availability(
    unmet: list[str],
    intent: IntentExtraction,
    pois: Sequence[Poi],
    restaurants: Sequence[Restaurant],
) -> tuple[list[str], list[str]]:
    """把未满足诉求按"附近到底有没有这类去处"分成两组（文案修缮批，C2 实锤）。

    Returns:
        `(not_found_nearby, not_scheduled)`——前者是**验证过**目录里距离半径内
        确实没有匹配实体的诉求（可以诚实说"附近没找到 X"）；后者是附近有、但
        这版方案没安排上的诉求（只能说"这次没安排上 X"，把方案取舍说成找不到
        是撒谎——C2 里 KTV 第一轮还在方案里，第二轮被"累了"催生的新约束滤掉，
        叙事却说"附近没找到合适的KTV"）。

    判定材料全部在现场：全量目录实体（调用方传入）自带 `distance_km`，
    `intent.distance_max_km` 是用户半径；词法匹配复用未满足检测本身的口径
    （餐厅走 `_cuisine_match` 双向 substring，POI 走 `poi_desire_match`），
    保证"要是排进去了会算满足"与"附近有货"是同一把尺子。

    fail-safe 方向与检测函数相反且刻意：分类失败（依赖导入失败等）→ 全部归
    "这版没安排"组——"这次没安排上 X"无论真实原因是哪种都为真；"附近没找到 X"
    只在验证过缺货时才为真。宁可少断言，不可说假话。
    """
    if not unmet:
        return [], []
    try:
        try:
            from agent.runtime.tools.search_adapter import poi_desire_match
        except Exception:  # noqa: BLE001
            poi_desire_match = None  # 只剩餐厅口径可用，POI 诉求会落入"没安排"组

        max_km = intent.distance_max_km

        def within(entity: Any) -> bool:
            if max_km is None:
                return True
            d = getattr(entity, "distance_km", None)
            return True if d is None else d <= max_km

        def available_nearby(pref: str) -> bool:
            for r in restaurants:
                if within(r) and _cuisine_match(pref, r.cuisine or ""):
                    return True
            if poi_desire_match is None:
                return False
            for p in pois:
                if within(p) and poi_desire_match(
                    pref, p.type or "", p.name or "", list(p.tags or [])
                ):
                    return True
            return False

        not_found: list[str] = []
        not_scheduled: list[str] = []
        for pref in unmet:
            (not_scheduled if available_nearby(pref) else not_found).append(pref)
        return not_found, not_scheduled
    except Exception:  # noqa: BLE001
        return [], list(unmet)


# ============================================================
# ADR-0014 决策 1（G-1）：出处诚实告知（narration 两个消费方之一）
# ============================================================

_PROVENANCE_TAG_FIELDS_FOR_DISCLOSURE = (
    "dietary_constraints",
    "physical_constraints",
    "experience_tags",
)


def _provenance_hints(intent: IntentExtraction) -> dict:
    """从 intent.field_provenance 提炼"值得跟用户说一声"的出处信号。

    只挑这几类信号（其余出处值 user_stated/prior 对本消费方没有"讲出来有用"
    的价值——G-2 才会引入 hard×prior 的告知口径区分，本期不做）：
    - distance_max_km 出处为 default（用户没提距离、也没有先验）
    - 三类受控标签里第一个出处为 inferred 的元素（按 dietary→physical→
      experience 顺序取第一个命中，避免同一句话堆一串标签）
    - budget_per_person 听到但没法量化（ADR-0014 决策 3·G-3："别太贵"类定性
      预算表达——见下方独立判断）

    intent.field_provenance 为 None/空（旧数据、stub、未跑校正）→ 距离/标签
    两类信号返回 {}；budget 信号只依赖 `ambiguous_fields`，不依赖
    field_provenance，两者独立判断（不因为没有 provenance 数据就连这条也吞掉）。
    """
    provenance = intent.field_provenance or {}
    hints: dict = {}
    if provenance.get("distance_max_km") == "default":
        hints["distance_default"] = True
        hints["distance_km"] = intent.distance_max_km

    for field in _PROVENANCE_TAG_FIELDS_FOR_DISCLOSURE:
        values = getattr(intent, field) or []
        inferred_value = next(
            (v for v in values if provenance.get(f"{field}:{v}") == "inferred"),
            None,
        )
        if inferred_value is not None:
            hints["inferred_tag"] = inferred_value
            break

    # ADR-0014 决策 3（G-3）：S1"预算别太贵"类定性表达——系统不编造一个
    # budget_per_person 数字，但也不能让"用户提过预算"这件事凭空消失。
    # parser 把这类表达自报进 ambiguous_fields（见 intent_parser_prompt.py
    # 【预算抽取规则】），本函数据此产出"听到了但没法量化"的诚实信号——
    # 只在 budget_per_person 确实是 None 时才触发（若已经有具体数字，说明
    # 这轮/上轮已经量化过，不再需要这句"没法量化"的说明）。
    if intent.budget_per_person is None and "budget_per_person" in (
        intent.ambiguous_fields or []
    ):
        hints["budget_ambiguous"] = True

    return hints


def _provenance_honest_clause(intent: IntentExtraction) -> str:
    """出处诚实告知一句话——模板路径确定性生成（LLM 路径走 prompt 指令，
    见 `_call_llm_narrator` 传的 provenance_hints + narrator_prompt.py
    【出处诚实告知】段；两条路径各自生成文案，不共享文本，同 unmet_cuisines/
    advisories 的"模板确定性、LLM 走指令"分工）。

    返回空串 = 无信号（intent.field_provenance 为 None，或没有 default/
    inferred 命中）——不影响既有 narration 回归（旧数据 field_provenance
    默认 None，本函数天然跳过）。
    """
    hints = _provenance_hints(intent)
    if not hints:
        return ""

    parts: list[str] = []
    if hints.get("distance_default"):
        dist = hints["distance_km"]
        dist_str = f"{dist:.0f}" if float(dist).is_integer() else f"{dist}"
        parts.append(f"距离你没提，我按默认 {dist_str} 公里安排的")
    inferred_tag = hints.get("inferred_tag")
    if inferred_tag:
        parts.append(f"我从你的话里猜你可能想要「{inferred_tag}」，不合适可以跟我说")
    if hints.get("budget_ambiguous"):
        parts.append("你提到预算别太贵，但没给具体数字，我没法编一个卡预算，这次尽量控制着来")

    if not parts:
        return ""
    return "，".join(parts) + "。"


# ============================================================
# ADR-0013 F-3：节点调整按钮（模板确定性生成器 + LLM 输出校验）
# ============================================================
#
# 【为什么模板生成器落在 narrator.py，不是 node_swap.py】
# node_swap.py（F-1）只关心"给定 dimension+value，怎么找替代候选"——它不决定
# "该给用户看哪几个按钮"。按钮生成是叙事/展示层的决策（同一节点，不同产品会
# 挑不同的"典型分歧点"当按钮），与 narrator 已经在做的"title/narration 同次
# LLM 产出"是同一件事的延伸（ADR-0013 决策 5："narrate 的既有 LLM 调用搭车产
# 出"）；stub/rule 模式的确定性兜底也自然放在同一模块，两条路径（LLM/模板）
# 产出同一个 NodeChip 形状，调用方（agent.graph.nodes.narrate）不用关心走的
# 是哪条路。
#
# 【每 kind 的模板规则表（ADR-0013 决策 5 "按 kind 走模板兜底" 的具体化）】
# restaurant：price(cheaper) 恒生成；ambience 取当前 tags 里"安静聊天/热闹"
#   的反向（无信号则不生成，不瞎猜）；dietary 仅当 intent.dietary_constraints
#   有信号时，取其第一项作为目标值。
# poi：distance(closer) 恒生成；ambience 同上取反向；crowd_fit 仅当
#   intent.physical_constraints 有信号时，取其第一项作为目标值。
# 两个 kind 都恒 ≤3 个（每类最多 3 条候选规则，天然满足 ADR 上限）；
# dimension 全部来自 `NodeAdjustmentDimension`——该枚举只有 6 个节点级维度，
# 没有"路线级"维度可选，"路线级按钮禁入节点下方"（ADR 决策 5）在 schema 层
# 就是不可达状态，不需要本模块额外过滤。


def _reverse_ambience(tags: list[str]) -> Optional[str]:
    """氛围反向：当前 tags 命中 `AMBIENCE_VALUES`（"安静聊天"/"热闹"）两极之一
    → 建议另一极；两者都没有时返回 None（没有锚点可反，不瞎猜——ADR-0013
    决策 5 只说"取反向"，没说"没有原值也要编一个"）。直接从
    `schemas.node_adjustment.AMBIENCE_VALUES` 取值，不在本模块重复硬编码这
    两个字符串，避免两处拼写漂移。"""
    for value in AMBIENCE_VALUES:
        if value in tags:
            return next(v for v in AMBIENCE_VALUES if v != value)
    return None


def _compact_chip_label(text: str, max_len: int = 8) -> str:
    """去内部空格 + 硬截断，保证 chip label 永远满足 `NodeChip.label` 的
    ≤8 字校验。

    `PHYSICAL_TAGS` 词典里"适合 5-10 岁"带内部空格（去空格后 7 字，"…的"
    后缀恰好 8 字）；截断是防未来词典扩容时静默超限导致 `NodeChip(...)`
    构造抛 ValidationError——防御性兜底，不是当前词典表已知会触发。"""
    return text.replace(" ", "")[:max_len]


def _template_chips_for_restaurant(
    node_id: str, entity: Restaurant, intent: IntentExtraction
) -> list[NodeChip]:
    chips = [
        NodeChip(
            node_id=node_id,
            label="更便宜的",
            adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.PRICE, value="cheaper"),
        )
    ]
    ambience_value = _reverse_ambience(list(entity.tags or []))
    if ambience_value is not None:
        chips.append(
            NodeChip(
                node_id=node_id,
                label=_compact_chip_label(f"更{ambience_value}"),
                adjustment=NodeAdjustment(
                    dimension=NodeAdjustmentDimension.AMBIENCE, value=ambience_value
                ),
            )
        )
    if intent.dietary_constraints:
        value = intent.dietary_constraints[0]
        chips.append(
            NodeChip(
                node_id=node_id,
                label=_compact_chip_label(f"{value}的"),
                adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.DIETARY, value=value),
            )
        )
    return chips[:3]


def _template_chips_for_poi(
    node_id: str, entity: Poi, intent: IntentExtraction
) -> list[NodeChip]:
    chips = [
        NodeChip(
            node_id=node_id,
            label="更近的",
            adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.DISTANCE, value="closer"),
        )
    ]
    ambience_value = _reverse_ambience(list(entity.tags or []))
    if ambience_value is not None:
        chips.append(
            NodeChip(
                node_id=node_id,
                label=_compact_chip_label(f"更{ambience_value}"),
                adjustment=NodeAdjustment(
                    dimension=NodeAdjustmentDimension.AMBIENCE, value=ambience_value
                ),
            )
        )
    if intent.physical_constraints:
        value = intent.physical_constraints[0]
        chips.append(
            NodeChip(
                node_id=node_id,
                label=_compact_chip_label(f"{value}的"),
                adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.CROWD_FIT, value=value),
            )
        )
    return chips[:3]


def generate_template_node_chips(
    itinerary: Itinerary,
    intent: IntentExtraction,
    pois: Sequence[Poi],
    restaurants: Sequence[Restaurant],
) -> list[NodeChip]:
    """确定性模板路径生成器——stub/rule 模式的地板，也是 LLM 搭车解析失败时
    的整体回落目标（`generate_title_and_narration` 里 "LLM chips 为空 → 走
    这里" 的唯一兜底，不半信半用）。

    按方案里每个非 home 节点的 `target_kind` + 对应实体的字段/tags 生成
    ≤3 个 chip（规则表见模块顶部注释）。候选池（`pois`/`restaurants`）里查
    不到对应实体的节点静默跳过（不影响其它节点的按钮——与 `node_swap.py`
    "候选池须覆盖全部已选节点"是不同层面的契约：那是"能不能执行换菜"的硬
    前置条件，这里只是"能不能生成按钮"的展示层，查不到就不生成，不报错）。
    """
    poi_by_id = {p.id: p for p in pois}
    rest_by_id = {r.id: r for r in restaurants}
    chips: list[NodeChip] = []
    for node in itinerary.nodes:
        if node.target_kind == "restaurant":
            entity = rest_by_id.get(node.target_id)
            if entity is not None:
                chips.extend(_template_chips_for_restaurant(node.target_id, entity, intent))
        elif node.target_kind == "poi":
            entity = poi_by_id.get(node.target_id)
            if entity is not None:
                chips.extend(_template_chips_for_poi(node.target_id, entity, intent))
    return chips


_MAX_CHIPS_PER_NODE = 3
"""每节点最多展示的 chip 数（ADR-0013 决策 5 硬上限），LLM 搭车路径的裁剪
阈值——模板路径因规则表本身 ≤3 条不需要裁剪，只有 LLM 路径可能"太热情"。"""


def _validate_llm_node_chips(
    raw_chips: Any, valid_node_ids: set[str]
) -> list[NodeChip]:
    """校验 LLM 搭车产出的 `node_chips` 原始 JSON——"不半信半用"：结构不对 /
    字段缺失 / `dimension`+`value` 不在受控枚举组合 / `label` 超 8 字 /
    `node_id` 不是当前方案里的真实节点，任何一条不满足 → 返回空列表，调用方
    据此整体回落模板生成器（不是"挑出合法的那几条凑合用"——半信半用等于告诉
    用户"这个按钮我们验证过"，其实只验证了一部分，比全部不信更危险）。

    全部合法时按 `node_id` 分组、每组截断到 `_MAX_CHIPS_PER_NODE`（数量超标
    是"太热情"不是"不合法"，只裁剪不弃权——LLM 其它判断仍然可信）。
    """
    if not isinstance(raw_chips, list):
        return []

    parsed: list[NodeChip] = []
    for item in raw_chips:
        if not isinstance(item, dict):
            return []
        node_id = item.get("node_id")
        if node_id not in valid_node_ids:
            return []
        try:
            chip = NodeChip(
                node_id=node_id,
                label=item.get("label"),
                adjustment=NodeAdjustment(
                    dimension=item.get("dimension"), value=item.get("value")
                ),
            )
        except Exception:  # noqa: BLE001
            return []
        parsed.append(chip)

    per_node_count: dict[str, int] = {}
    capped: list[NodeChip] = []
    for chip in parsed:
        n = per_node_count.get(chip.node_id, 0)
        if n >= _MAX_CHIPS_PER_NODE:
            continue
        per_node_count[chip.node_id] = n + 1
        capped.append(chip)
    return capped


def _node_chip_context(
    itinerary: Itinerary, pois: Sequence[Poi], restaurants: Sequence[Restaurant]
) -> list[dict]:
    """给 LLM 的每节点上下文（node_id + 关键字段），喂进 prompt 让它"按活动的
    典型分歧点起 label"（而不是瞎编）。查不到对应实体的节点跳过（同
    `generate_template_node_chips` 的静默跳过纪律）。"""
    poi_by_id = {p.id: p for p in pois}
    rest_by_id = {r.id: r for r in restaurants}
    out: list[dict] = []
    for n in itinerary.nodes:
        if n.target_kind == "poi":
            e = poi_by_id.get(n.target_id)
            if e is None:
                continue
            out.append(
                {
                    "node_id": n.target_id,
                    "kind": "poi",
                    "title": n.title,
                    "type": e.type,
                    "tags": list(e.tags or []),
                    "distance_km": e.distance_km,
                }
            )
        elif n.target_kind == "restaurant":
            e = rest_by_id.get(n.target_id)
            if e is None:
                continue
            out.append(
                {
                    "node_id": n.target_id,
                    "kind": "restaurant",
                    "title": n.title,
                    "cuisine": e.cuisine,
                    "tags": list(e.tags or []),
                    "avg_price": e.avg_price,
                }
            )
    return out


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
    joined = roles[0] if len(roles) == 1 else "、".join(roles)
    # 文案修缮批（B9/G3/G4/H1 实锤）："和"+"2 位兄弟" 直接拼出"和2 位"——
    # 空格插错位（本 docstring 期望本来就是「和 4 个朋友」）。短语以数字开头
    # 时在"和"后补一格，对齐全文件"数字两侧留空格"的既有排版（"4.5 小时"）。
    if joined and joined[0].isdigit():
        return f"和 {joined}"
    return f"和{joined}"


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

    # 文案修缮批（B9/G3/G4/H1 实锤）：曾按「 · 」劈开只取后半截当短名，但
    # mock 目录里「 · 」是**全名的一部分**（"麦霸欢唱 KTV · 旗舰店"，102 个
    # 实体里 67 个如此），截半拼出"去旗舰店""去私房包房"读着像断句——兜底
    # 模板必须取全名（终审拍板，纯字符串级改动）。
    # 用餐节点：尽量带上预约信息
    if target_kind == "restaurant" or "用餐" in kind or "夜宵" in kind:
        if note and "预约" in note:
            return f"{start} 到{title}，{note.replace('待你确认后为你预约', '给你预约了')}"
        return f"{start} 到{title}吃饭"
    # POI 节点：按 kind 区分主活动 / 自由 / 其他
    if "主活动" in kind:
        return f"{start} 去{title}"
    return f"{start} {title}"


def _template_title(intent: IntentExtraction, itinerary: Itinerary) -> str:
    """规则版小红书风格大标题（itinerary.summary 兜底）。

    LLM 未配 / stub / 解析不出 title 时走这里。**信息全**：遍历全部中间站点
    （跳过 home），用动作短语 + 同行短语 + 时长拼一句口语标题。

    例（室友 4 人 · 烧烤 + KTV · 4.5h）：「和室友撸串+唱K，4.5小时」。
    """
    nodes_dump = [
        n.model_dump() if hasattr(n, "model_dump") else n for n in itinerary.nodes
    ]
    station_phrases: list[str] = []
    for n in nodes_dump:
        phrase = node_to_title_phrase(
            title=(n.get("title") or ""),
            kind=(n.get("kind") or ""),
            target_kind=(n.get("target_kind") or ""),
        )
        if phrase:
            station_phrases.append(phrase)

    companions_phrase = companions_to_title_phrase(
        [c.model_dump() if hasattr(c, "model_dump") else c for c in intent.companions]
    )
    total_hours = (itinerary.total_minutes or 0) / 60
    return build_xiaohongshu_title(
        station_phrases=station_phrases,
        companions_phrase=companions_phrase,
        total_hours=total_hours,
    )


_MULTI_ACTIVITY_RATIONALE_MIN_NODES = 3
"""触发"选择与顺序理由"一句话的活动数阈值（ADR-0010 边界节："3 个活动要讲清
为什么这几个、为什么这个顺序"）。少于该阈值时没什么好解释的，硬加理由是做作。"""

_SLACK_FRACTION_ROOMY = 0.22
"""slack（留白）占总时长比例 ≥ 此值 → 措辞为"特意多留了走停时间"（对齐 ADR-0010
决策 4 的 pace_budget 留白档位量级：relaxed 档 slack_fraction=0.30，本处取比它
略低的阈值，让"留白明显"的判定不必卡在最松的一档才触发）。"""

_SLACK_FRACTION_PACKED = 0.08
"""slack 占比 ≤ 此值 → 措辞为"排得比较紧凑"（对齐 pace_budget 的 energetic 档
slack_fraction=0.05 量级）。"""


def _multi_activity_rationale(
    itinerary: Itinerary,
    nodes_dump: list[dict],
) -> str:
    """活动数 ≥3 时补一句"为什么选这几个、为什么这样排"（ADR-0010 边界节遗留：
    "3 个活动要讲清...否则多活动反而更让人困惑"）。

    材料全部从 itinerary 本身现算，不读 planner 内部字段（节奏档/route 元数据等）
    ——保持 narrator 与 planning 层解耦，模板路径可确定性单测，LLM 路径走对应
    prompt 指令自行生成（见 narrator_prompt.py）：

    - **留白/节奏**：total_minutes 减去活动 duration_min 之和、再减去 hop 通勤
      分钟之和 = slack；slack 占比高 → "特意多留了走停时间"，占比低 → "排得
      比较紧凑"，居中 → "松紧刚好"。呼应 ADR-0010 决策 4"slack 是一等公民"。
    - **顺序（flow）**：用餐/夜宵节点若落在活动序列的中后段 → 点名"饭放在
      后段垫肚子"（呼应决策 10"饭点窗把饭推中后段"）；否则退化比较首尾活动
      时长——首段更长 → "精力多的排前面，后面轻松收尾"（呼应"活跃靠前、
      舒缓靠后"）。两者都不成立时该子句留空，不硬凑。

    同行人适配（幼童/老人/朋友等）已由调用方的 opener（`_template_narration`
    的 social_context 分支）承担，这里不重复提，避免同一信号被讲两遍。

    Returns:
        自成一句的中文短句（含句号）；活动数 <3 或无法得出任何子句时返回空串。
    """
    activity_nodes = [n for n in nodes_dump if (n.get("target_kind") or "") != "home"]
    n = len(activity_nodes)
    if n < _MULTI_ACTIVITY_RATIONALE_MIN_NODES:
        return ""

    total_minutes = itinerary.total_minutes or 0
    activity_minutes = sum(nd.get("duration_min") or 0 for nd in activity_nodes)
    hop_minutes = sum(h.minutes for h in itinerary.hops)
    slack = max(0, total_minutes - activity_minutes - hop_minutes)
    slack_fraction = (slack / total_minutes) if total_minutes else 0.0

    if slack_fraction >= _SLACK_FRACTION_ROOMY:
        pace_clause = f"{n} 个选得不算多，特意多留了些走停的时间"
    elif slack_fraction <= _SLACK_FRACTION_PACKED:
        pace_clause = f"{n} 个排得比较紧凑，路上不空等"
    else:
        pace_clause = f"{n} 个活动松紧刚好"

    meal_idx = next(
        (
            i
            for i, nd in enumerate(activity_nodes)
            if (nd.get("target_kind") == "restaurant")
            or ("用餐" in (nd.get("kind") or ""))
            or ("夜宵" in (nd.get("kind") or ""))
        ),
        None,
    )
    order_clause = ""
    if meal_idx is not None and meal_idx >= (n - 1) / 2:
        order_clause = "饭放在后段垫肚子"
    else:
        first_dur = activity_nodes[0].get("duration_min") or 0
        last_dur = activity_nodes[-1].get("duration_min") or 0
        if first_dur > last_dur:
            order_clause = "精力多的排前面，后面轻松收尾"

    clause = pace_clause if not order_clause else f"{pace_clause}；{order_clause}"
    return f"{clause}。"


_FEEDBACK_INVITE_MARKERS = ("跟我说", "告诉我", "我再换")
"""正文里算作"已邀请反馈"的标记词——出处告知（"不合适可以跟我说"）、未满足
告知（"不满意我再换"）、advisory（"想省钱可以告诉我砍哪一站"）都会命中。
命中任一则 `_template_narration` 的 stream 收尾句不再追加（收尾邀请去重）。"""


def _template_narration(
    intent: IntentExtraction,
    itinerary: Itinerary,
    stage_label: str,
    quality_warnings: Optional[list[str]] = None,
    unmet_cuisines: Optional[list[str]] = None,
    advisories: Optional[list[str]] = None,
    plan_recap: Optional[str] = None,
    *,
    unmet_not_scheduled: Optional[list[str]] = None,
) -> str:
    """规则模板拼开场白（fallback 也走这个）。

    文案修缮批（C2 实锤）：`unmet_cuisines` 语义收窄为"验证过附近确实没有"的
    诉求（措辞"附近没找到"），新增 kw-only `unmet_not_scheduled` 承载"附近有
    但这版没安排"的诉求（措辞"这次没安排上"，不许把方案取舍说成找不到）——
    分组由 `split_unmet_by_nearby_availability` 在构建处完成。

    格式（暖语气）：
        "{开场} {回顾句}{主活动短语}；{用餐短语}；{回家短语}。{质疑}{结尾}"

    spec R6 兜底质疑：
    - 含 ≤6 岁孩 + 任一非 home 节点 duration_min > 90 时，强制追加质疑短语，
      让 LLM 失败的兜底路径也能让用户感知"AI 在为我考虑"。
    - quality_warnings（如果由调用方传入）会被合并进质疑短语。

    ADR-0010 D-7：`advisories`（planner 产出的「绝不默默忽略」告知，每条已是
    自包含中文完整句）并入 honest_text 段（"说明一下，……"）——与 unmet_cuisines
    同属"诚实告知"语义，共用同一个开场词，不新起一段（见函数体 honest_text 拼接）。

    ADR-0010 边界节（narration 覆盖多活动）：活动数 ≥3 时在活动复述之后、诚实
    告知段之前插入 `_multi_activity_rationale` 产出的"选择与顺序理由"一句话
    （见该函数 docstring）。

    ADR-0011 决策 3（narration 切片，2026-07-03 新增）：`plan_recap` 非空时
    （调用方——`agent/graph/nodes/narrate.py`——只在本轮确实是反馈触发的新
    版本时才传值，见该文件 `_plan_recap_clause`"首轮不硬扯"纪律）在开场之后
    插入一句确定性回顾，如"这版是照你『太远了』的反馈调过的，"。None/空串 =
    不插入（首轮/全新解析没有"上一条反馈"可回顾）。
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
        # 同 _node_to_phrase：店名取全名，「 · 」是名字的一部分不可截半。
        long_title = getattr(long_kid_node, "title", "") or ""
        long_dur = getattr(long_kid_node, "duration_min", 0)
        challenge_text = (
            f"提醒一下，{long_title} 安排了 {long_dur} 分钟，宝贝可能会累，"
            f"可以中途休息一下。"
        )
    elif quality_warnings:
        # 没命中 ≤6 岁规则，但调用方传了 quality_warnings → 也融进文案
        challenge_text = "提醒一下，" + "；".join(quality_warnings[:2]) + "。"

    # 诚实告知：用户明示品类未排进行程（如附近没烧烤）→ 先坦白再说替代；
    # D-7：advisories（点名排不进/超预算/时长不足等）并入同一段，共用"说明一下"
    # 开场词——两者都是"诚实告知限制"的同一语义（不新起一段）。
    honest_segments: list[str] = []
    if unmet_cuisines:
        cuisines_str = "、".join(unmet_cuisines[:2])
        honest_segments.append(
            f"你想要的{cuisines_str}附近没找到合适的，"
            f"先帮你选了方案里的替代，不满意我再换。"
        )
    # C2 实锤分叉：附近有但这版没安排 → 只坦白"没安排上"，不归因于"找不到"。
    # 改口根治批核查：模板句本身天然"只陈述不归因"（真实原因引擎未透出，
    # 不编因果；recap 句只回顾这版因何触发，不解释某项为何没排上——LLM 路径
    # 的同名纪律见 narrator_prompt 的【这版没安排】块）。
    if unmet_not_scheduled:
        not_scheduled_str = "、".join(unmet_not_scheduled[:2])
        honest_segments.append(
            f"你想要的{not_scheduled_str}这次没安排上，"
            f"先帮你选了方案里的替代，不满意我再换。"
        )
    if advisories:
        # 不在这里截断：本函数收到的 advisories 已经是调用方
        # （generate_narration/generate_title_and_narration）用
        # `_apply_advisory_disclosure_cap` 限额过的列表（ADR-0014 决策 2·
        # G-2：≤2 条 + 折叠句），全量渲染即可，不必二次截断（旧「不截断」
        # 深审修正的结论仍成立，只是"为什么不必截断"的前提从"天然只有几句"
        # 换成了"已经在更上游被限额"）。
        honest_segments.extend(advisories)
    # ADR-0014 决策 1（G-1）：出处诚实告知——"距离你没提我按默认" /
    # "我从你的话里猜你想要 X"，与上面两类同属"诚实"语义，并入同一段。
    provenance_clause = _provenance_honest_clause(intent)
    if provenance_clause:
        honest_segments.append(provenance_clause)
    honest_text = ("说明一下，" + "".join(honest_segments)) if honest_segments else ""

    # ADR-0010 边界节：活动数 ≥3 时追加"为什么选这几个、为什么这样排"一句话
    # （见 _multi_activity_rationale docstring）；<3 个活动返回空串，不硬加。
    rationale_text = _multi_activity_rationale(itinerary, nodes_dump)

    # ADR-0011 决策 3（narration 切片）：反馈轮的确定性回顾句，插在开场之后
    # ——调用方只在"本轮是反馈触发的新版本"时传值（首轮不硬扯，见 docstring）。
    recap_text = f"{plan_recap.strip()}，" if plan_recap and plan_recap.strip() else ""

    # 尾（文案修缮批 · 收尾邀请去重，A6/A8 实锤）：诚实告知段常自带邀请反馈语
    # （"不合适可以跟我说" / "不满意我再换"），固定收尾"哪里不合适跟我说一声。"
    # 再追加就是同一个意思背靠背说两遍——正文已含邀请反馈语则不拼（LLM 路径的
    # 对应病灶由 narrator_prompt.py【邀请反馈只说一次】规则治，两条路径同一纪律）。
    pre_ending = f"{opener}{recap_text}{body}。{rationale_text}{honest_text}{challenge_text}"
    if stage_label == "confirm":
        ending = "都给你搞定了，可以放心出门了。"
    elif any(marker in pre_ending for marker in _FEEDBACK_INVITE_MARKERS):
        ending = ""
    else:
        ending = "哪里不合适跟我说一声。"

    return f"{pre_ending}{ending}"


# ============================================================
# LLM 主路径
# ============================================================


def _clean_narration_text(text: str) -> str:
    """剥围栏 / 引号 + 长度兜底，得到可直接展示的 narration 纯文本。"""
    text = (text or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
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


def _parse_title_narration(raw: str) -> tuple[Optional[str], str, Optional[list]]:
    """从 LLM 原始输出解析 (title, narration, node_chips_raw)。

    want_title=True 时 LLM 被要求输出 JSON {"title":..., "narration":...,
    "node_chips":...}（node_chips 见 `NODE_CHIPS_OUTPUT_INSTRUCTION`，ADR-0013
    F-3 搭车产出，非必然存在——旧版 prompt/未提供 node_chip_context 时不会有）。
    解析策略（层层兜底，保证永远拿得到 narration）：
    1. 剥 markdown 围栏后 json.loads；取 title / narration / node_chips 字段。
    2. JSON 解析失败 / 缺 narration → 整段当 narration（title=None，让上层走规则兜底标题）。
    title 做小红书规格清理：去前缀「半日方案·」、去「（约X小时）」括号、去引号、长度裁剪。

    node_chips_raw 只做"提取"，不做 schema 校验（校验在 `_validate_llm_node_chips`，
    需要 valid_node_ids 这个额外上下文，本函数不关心）——返回 None 表示 JSON 里
    压根没有这个字段（调用方据此直接判定"缺字段"，走模板回落，见 `_call_llm_narrator`）。
    """
    import json

    from agent.core.llm_client import strip_json_fence

    raw = (raw or "").strip()
    if not raw:
        return None, "", None

    candidate = strip_json_fence(raw) or raw
    title: Optional[str] = None
    narration_raw: Optional[str] = None
    node_chips_raw: Optional[list] = None
    try:
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            t = obj.get("title")
            n = obj.get("narration")
            if isinstance(t, str) and t.strip():
                title = t.strip()
            if isinstance(n, str) and n.strip():
                narration_raw = n.strip()
            raw_chips = obj.get("node_chips")
            if isinstance(raw_chips, list):
                node_chips_raw = raw_chips
    except (ValueError, TypeError):
        pass

    # json.loads 失败最常见的根因（真机实锤）：narration 值里嵌了**未转义的
    # 双引号**——反馈轮复述用户原话「"吃饭前想去个KTV"」时，那个 " 提前闭合
    # 了 JSON 字符串 → 整个对象非法 → 旧代码把整段 {"title":...,"narration":...}
    # 原样当 narration 抖给用户（气泡里显示原始 JSON）。这里先容错抢救
    # narration 字段的值（能救回 LLM 那句正常叙事）。
    if narration_raw is None:
        narration_raw = _salvage_narration(candidate)

    if narration_raw is not None:
        narration = _clean_narration_text(narration_raw)
    elif candidate.lstrip().startswith("{"):
        # 抢救也失败，但内容明显是（坏的）JSON 对象——**绝不 dump 原始 JSON**。
        # 返回空串 → generate_title_and_narration 的 `llm_narration or 模板`
        # 自动回落干净的规则模板叙事（最坏也是一句正常的话，不是一坨 JSON）。
        narration = ""
    else:
        # 真·纯文本（模型没走 JSON、直接给了一段话）→ 保留旧行为用原文当叙事。
        narration = _clean_narration_text(raw)
    return _sanitize_title(title), narration, node_chips_raw


def _salvage_narration(candidate: str) -> Optional[str]:
    """从**解析失败**的叙事 JSON 里容错抠出 narration 字段的值。

    根因见 `_parse_title_narration`：narration 值里嵌未转义 " 会让 json.loads
    炸掉。这里用贪婪匹配 + 结构锚点（narration 后面跟 node_chips / title / 收尾
    }）把 narration 的完整值抠出来——贪婪 .* 允许值里含内层引号，锚点保证匹到
    的是真正的闭合引号而非内层引号。best-effort：抠不出返 None，由调用方走
    "空串→模板"硬兜底，无论如何用户不会看到原始 JSON。
    """
    import re

    for pat in (
        r'"narration"\s*:\s*"(.*)"\s*,\s*"node_chips"\s*:',  # narration 后跟 node_chips
        r'"narration"\s*:\s*"(.*)"\s*,\s*"title"\s*:',        # narration 后跟 title
        r'"narration"\s*:\s*"(.*)"\s*\}',                      # narration 是最后一个字段
    ):
        m = re.search(pat, candidate, re.DOTALL)
        if m:
            val = m.group(1).strip()
            if val:
                # 还原 JSON 字符串里的合法转义序列（\" \n \t \\）
                val = (
                    val.replace('\\"', '"')
                    .replace("\\n", "\n")
                    .replace("\\t", "\t")
                    .replace("\\\\", "\\")
                )
                return val or None
    return None


def _sanitize_title(title: Optional[str]) -> Optional[str]:
    """清理 LLM 给的 title，贴合小红书规格；非法/空 → None。"""
    if not title:
        return None
    import re

    t = title.strip()
    # 去引号
    if (t.startswith('"') and t.endswith('"')) or (
        t.startswith("「") and t.endswith("」")
    ):
        t = t[1:-1].strip()
    # 去禁用前缀「半日方案 ·」「轻量方案 ·」等
    t = re.sub(r"^(半日|轻量|用餐|短途)方案\s*[·:：]?\s*", "", t).strip()
    # 去「（约 X 小时）」括号
    t = re.sub(r"[（(]约\s*\d+\.?\d*\s*小时[）)]", "", t).strip()
    # 过长裁剪（标题应短；防 LLM 把整段开场白塞进 title）
    if len(t) > 40:
        t = t[:38] + "…"
    return t or None


# ============================================================
# 深度思考模式关闭（真 LLM 冒烟发现：叙事 LLM 静默全灭根因之一）
# ============================================================
#
# 根因链：LLM_MODEL=mimo-v2.5-pro 是深度思考模型，思考过程的 token 计入
# max_tokens 预算；narrator 原先只给 180-400 max_tokens，思考阶段就把预算
# 耗尽，正文被截成空字符串，_call_llm_narrator 静默 return None（无日志），
# 上层无感知地退化到模板兜底——"LLM 路径叙事静默全灭"。
#
# 双保险（用户拍板"皮带加背带"）：
# 1. 皮带：显式关闭深度思考（MiMo 官方文档 mimo.mi.com/docs "Deep Thinking
#    Mode"：`extra_body={"thinking": {"type": "disabled"}}`，OpenAI SDK
#    无该字段，须走 extra_body 透传——已联网核实，无 `enable_thinking` 这个
#    别名，backend/scripts/smoke_langgraph_mimo.py 里的 `enable_thinking`
#    实为误用，未在本次改动范围内处理）。关闭后思考模型退化为普通模型，
#    不产生 reasoning token，也顺带恢复 temperature/top_p 自定义生效
#    （文档：深度思考开启时 temperature/top_p 会被强制改为 1.0/0.95，
#    与 spec R6 "温度 0.5 让质疑指令更稳定" 的前提冲突）。
# 2. 背带：即使 provider 拒绝/忽略 thinking 参数（透传失败、模型版本不识别
#    该字段等），也把 max_tokens 同时拉高（want_title 400→2400 / 纯
#    narration 180→1200），让思考 token 吃掉一部分预算后仍有余量吐正文，
#    不完全依赖关思考成功。
#
# 真因修复批 item 5：这份常量原本在本文件私有定义，现搬到
# `agent.core.llm_client`（LLM 客户端的共享归属地，见该模块）供
# `blueprint_llm.py` 复用——蓝图生成同样是"只要结构化 JSON、不要思考过程"的
# 场景，同一份保险。本文件顶部已改为从那里 import（`as` 保留同名局部引用
# `_MIMO_THINKING_DISABLED_EXTRA_BODY`），下面两处调用点不必改。


def _diagnose_empty_llm_response(resp, client) -> str:
    """空 content 时拼一句可诊断的日志摘要（model / finish_reason / 是否有
    思考输出 / reasoning token 用量），不再静默 return None。

    resp.raw 是 `chat.completions.create()` 响应的 model_dump()；MiMo 思考
    模型的 reasoning 内容/token 用量按官方文档挂在
    `choices[0].message.reasoning_content` 与
    `usage.completion_tokens_details.reasoning_tokens`——两者都是 SDK 未建
    模的 provider 特有字段，只能从 raw dict 里挖，挖不到就老实说"未知"。
    """
    model = getattr(client, "model", "?")
    finish_reason = getattr(resp, "finish_reason", "?")
    raw = getattr(resp, "raw", None) or {}
    reasoning_len = None
    reasoning_tokens = None
    try:
        message = ((raw.get("choices") or [{}])[0] or {}).get("message") or {}
        reasoning_content = message.get("reasoning_content")
        if isinstance(reasoning_content, str):
            reasoning_len = len(reasoning_content)
        usage = raw.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = details.get("reasoning_tokens")
    except Exception:  # noqa: BLE001
        pass
    return (
        f"model={model} finish_reason={finish_reason} "
        f"reasoning_content_len={reasoning_len} reasoning_tokens={reasoning_tokens}"
    )


def _call_llm_narrator(
    *,
    intent: IntentExtraction,
    itinerary: Itinerary,
    stage_label: str,
    critic_summary: str = "",
    quality_warnings: Optional[list[str]] = None,
    unmet_cuisines: Optional[list[str]] = None,
    unmet_not_scheduled: Optional[list[str]] = None,
    advisories: Optional[list[str]] = None,
    want_title: bool = False,
    pois: Sequence[Poi] = (),
    restaurants: Sequence[Restaurant] = (),
    plan_recap: str = "",
) -> Optional[tuple[Optional[str], str, list[NodeChip]]]:
    """调 LLM 生成开场白（want_title=True 时同次产出 title + narration + node_chips）。

    任何异常 / 空输出返 None 让上层走 fallback。

    spec R6：透传 critic_summary / quality_warnings，prompt 里有「主动质疑规则」
    段会指导 LLM 在收到这两个字段时主动加一句质疑性建议。
    unmet_cuisines：诚实告知未满足的用户指定品类。
    advisories（ADR-0010 D-7）：planner「绝不默默忽略」的结构化告知（完整句子），
    进 prompt 的诚实告知区，与 unmet_cuisines 同一纪律——先坦白、不假装满足。

    want_title：True 时要求 LLM 用同一次调用输出 JSON {"title","narration",
    "node_chips"}——title 写回 itinerary.summary（小红书风格大标题），
    narration 作开场白，node_chips 是 ADR-0013 F-3 的节点调整按钮搭车产出
    （pois/restaurants 给 prompt 提供每节点上下文，见 `_node_chip_context`）。
    不新增独立 LLM 调用；解析失败时 title=None（上层用规则兜底标题），
    narration 用整段原文，node_chips 校验失败/缺字段时返回空列表（调用方
    `generate_title_and_narration` 据此整体回落模板生成器，不半信半用）。

    Returns:
        (title 或 None, narration, node_chips)；narration 永远非空
        （除非 LLM 完全没返回 → None）；node_chips 校验失败/未产出时为 []。
    """
    try:
        client = get_llm_client(task="narration")
    except Exception as e:  # noqa: BLE001
        logger.warning("[narrator] get_llm_client 失败：%s", e)
        return None

    node_chip_context = _node_chip_context(itinerary, pois, restaurants) if want_title else []
    user_msg = build_narrator_user_message(
        intent_dict=intent.model_dump(),
        itinerary_dict=itinerary.model_dump(),
        stage_label=stage_label,
        critic_summary=critic_summary,
        quality_warnings=list(quality_warnings or []),
        unmet_cuisines=list(unmet_cuisines or []),
        unmet_not_scheduled=list(unmet_not_scheduled or []),
        advisories=list(advisories or []),
        want_title=want_title,
        node_chip_context=node_chip_context,
        plan_recap=plan_recap,
        # ADR-0014 决策 1（G-1）：出处诚实告知信号（distance 默认 / 首个
        # inferred 标签），LLM 路径走 prompt 指令自行组词（narrator_prompt.py
        # 【出处诚实告知】段），不复用模板路径 `_provenance_honest_clause` 的
        # 现成句子。
        provenance_hints=_provenance_hints(intent),
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
            # 优化 2 + 冒烟修复"皮带加背带"背带半：限制输出长度（中文每字
            # ≈ 2 token）。原 400/180 是按"不思考"预算给的；深度思考模型的
            # 思考 token 也计入这个预算，原值被思考过程吃空导致正文截空
            # （见 _MIMO_THINKING_DISABLED_EXTRA_BODY 注释的根因链）。拉高
            # 到 2400/1200：即使皮带（关思考）失效，背带（更大预算）也兜得住。
            max_tokens=2400 if want_title else 1200,
            # 源头减少坏 JSON（叙事气泡显示原始 JSON 的 bug 根治·源头层）：
            # want_title 路径要求 JSON 输出，强制 response_format=json_object 让
            # provider 保证合法 JSON（内层引号由 provider 转义），从源头压低
            # "narration 嵌未转义引号→json.loads 炸→dump 原文"的触发率；
            # _parse_title_narration 的抢救+空串硬兜底是"抢救+背带"，双保险。
            # 非 want_title 路径是纯文本叙事，不加约束。
            response_format={"type": "json_object"} if want_title else None,
            # 皮带半：显式关闭 MiMo 深度思考模式（见上方
            # _MIMO_THINKING_DISABLED_EXTRA_BODY 注释）。对非思考模型/不认识
            # 该字段的 provider 是无害的多余字段（OpenAI 兼容服务通常忽略
            # 未知字段），不需要按 provider 分支。
            extra_body=_MIMO_THINKING_DISABLED_EXTRA_BODY,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[narrator] LLM chat 失败：%s", e)
        return None

    raw = (resp.content or "").strip()
    if not raw:
        # 曾经静默 return None，评委/开发者完全看不出 LLM 路径已经失效、
        # 静默退化到模板兜底——冒烟修复要求"绝不静默"，补一条可诊断日志
        # （model / finish_reason / 思考输出痕迹），见 _diagnose_empty_llm_response。
        logger.warning(
            "[narrator] LLM 返回空 content，回退模板兜底：%s",
            _diagnose_empty_llm_response(resp, client),
        )
        return None

    if want_title:
        title, narration, raw_chips = _parse_title_narration(raw)
        valid_node_ids = {n.target_id for n in itinerary.nodes if n.target_kind != "home"}
        node_chips = _validate_llm_node_chips(raw_chips, valid_node_ids) if raw_chips is not None else []
        return title, narration, node_chips
    # 不要 title：保持旧行为——整段当 narration
    return None, _clean_narration_text(raw), []


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
    - max_tokens=1200：限制总长度（中文每字 ~2 token；同 _call_llm_narrator
      纯 narration 分支一样"皮带加背带"拉高——原 180 只够"不思考"预算，
      深度思考模型的思考 token 计入同一预算会把正文截空，见
      `_MIMO_THINKING_DISABLED_EXTRA_BODY` 注释的根因链）

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
        chunk_count = 0
        for chunk in client.stream_chat(
            messages=[
                LLMMessage(role="system", content=NARRATOR_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_msg),
            ],
            temperature=0.5,
            max_tokens=1200,
            extra_body=_MIMO_THINKING_DISABLED_EXTRA_BODY,
        ):
            if chunk:
                chunk_count += 1
                yield chunk
        if chunk_count == 0:
            # 曾经的静默失败点：流式 0 chunk 时调用方只会看到"没有输出"，
            # 看不出是 LLM 真吐了空串还是链路本身没问题——冒烟修复要求
            # 补可诊断日志（stream_chat 没有 resp 对象可挖，只能记录"0 chunk"
            # 这个事实本身 + model 名）。
            logger.warning(
                "[narrator] stream_chat 0 chunk（model=%s），回退模板兜底",
                getattr(client, "model", "?"),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("[narrator] stream_chat 失败：%s", e)
        return


# ============================================================
# ADR-0014 决策 2（G-2）：advisory 告知限额——多路合并去重后≤2 条 + 折叠句
# ============================================================
#
# 【与旧「不截断」决策的关系】`_template_narration` 内部拼 honest_text 那段
# 曾明确"不截断（深审修正）"，理由是"advisory 在 planner 侧已按码合并，每码
# 至多一句，现实上限 ~4 句"——这个假设在 D-7 五个码的年代成立，但 ADR-0014
# 决策 2 引入的 `CONSTRAINT_RELAXED` 一个 tag 一条、上限跟着 intent 里 soft
# tag 的数量走，"~4 句封顶"的前提不再成立。本函数在 D-7 既有 advisory 与
# CONSTRAINT_RELAXED 合并去重**之后**（调用方——`agent/graph/nodes/
# narrate.py`——先做 `_merge_advisories` 去重，再传本函数）统一限额，
# `_template_narration` 内部那句"不截断"依然成立（它确实不再对本函数已经
# 限额过的列表做二次截断），只是"不必再截断"的前提从"天然只有几句"变成
# "已经在更上游被限额过"。

_ADVISORY_DISCLOSURE_LIMIT = 2


def _apply_advisory_disclosure_cap(advisories: list[str]) -> list[str]:
    """多路 advisory 合并去重后限额：最多呈现 `_ADVISORY_DISCLOSURE_LIMIT`
    条，余下折叠为一句"还有 N 处小取舍"。

    措辞纪律（任务拍板）：自信的取舍说明，不是道歉——不用"抱歉"/"不好意思"
    之类的歉意措辞，用"取舍"/"顶上了"这类确定性、掌控感强的表达。

    条数 ≤ 限额 → 原样返回（不折叠，不产生"还有 0 处"这种废话）。
    """
    if len(advisories) <= _ADVISORY_DISCLOSURE_LIMIT:
        return advisories
    kept = advisories[:_ADVISORY_DISCLOSURE_LIMIT]
    remainder = len(advisories) - _ADVISORY_DISCLOSURE_LIMIT
    folded = f"另外还有 {remainder} 处按实际情况做了些取舍，方案细节里都能看到。"
    return kept + [folded]


# ============================================================
# 公共入口
# ============================================================


def generate_title_and_narration(
    *,
    intent: IntentExtraction,
    itinerary: Itinerary,
    stage: str = "stream",
    use_llm: bool = True,
    critic_summary: str = "",
    quality_warnings: Optional[list[str]] = None,
    unmet_cuisines: Optional[list[str]] = None,
    unmet_not_scheduled: Optional[list[str]] = None,
    advisories: Optional[list[str]] = None,
    pois: Sequence[Poi] = (),
    restaurants: Sequence[Restaurant] = (),
    plan_recap: str = "",
) -> tuple[str, str, list[NodeChip]]:
    """同次产出 (title, narration, node_chips)。

    title：小红书风格行程卡片大标题（写回 itinerary.summary 用），覆盖**所有主要站点**。
    narration：暖语气开场白（与单独 generate_narration 行为/质量一致）。
    node_chips（ADR-0013 F-3）：每个非 home 节点的定向调整按钮（≤3/节点）。

    LLM 路径：复用同一次 LLM 调用（want_title=True，JSON 输出）拿到三者，
    零额外延迟（ADR-0013 决策 5"搭车产出"）；解析不出 title 时只兜 title
    （用规则模板），narration 仍用 LLM 原文；node_chips 校验失败/缺字段/
    LLM 判定 0 个都会得到空列表——**只要是空列表就整体回落模板生成器**
    （`generate_template_node_chips`），不区分"LLM 主动说不需要"与"LLM 没
    产出/产出非法"这两种情况：前者从产品角度也不该让用户看不到任何按钮
    （模板兜底永远能给出至少 1-3 个确定性建议），不是信任问题，是"按钮
    这个交互面永远该有地板"的产品决策。
    规则 / stub 路径（use_llm=False）：title 走 _template_title，
    narration 走 _template_narration，node_chips 直接走模板生成器。

    advisories（ADR-0010 D-7）：planner「绝不默默忽略」的结构化告知（完整句子
    列表），并入 narration 的诚实告知段——见 `_template_narration`/prompt。

    plan_recap（ADR-0011 决策 3 narration 切片，2026-07-03 新增）：非空时是
    "这版是照哪条反馈调的"回顾材料——LLM 路径经 prompt 指令自然带出，模板
    路径插确定性回顾句（见 `_template_narration` 同名参数）。空串 = 首轮/
    非反馈轮，两条路径都不硬扯。

    Returns:
        (title, narration, node_chips)；title/narration 永远非空，
        node_chips 可能是空列表（itinerary 没有非 home 节点这种边界情况）。
    """
    # ADR-0014 决策 2（G-2）：告知限额——LLM 路径与模板路径共用同一份已限额
    # 列表，保证两条路径呈现的告知条数一致（见 `_apply_advisory_disclosure_cap`）。
    advisories = _apply_advisory_disclosure_cap(list(advisories or []))
    llm_title: Optional[str] = None
    llm_narration: Optional[str] = None
    llm_node_chips: list[NodeChip] = []
    if use_llm:
        out = _call_llm_narrator(
            intent=intent,
            itinerary=itinerary,
            stage_label=stage,
            critic_summary=critic_summary,
            quality_warnings=quality_warnings,
            unmet_cuisines=unmet_cuisines,
            unmet_not_scheduled=unmet_not_scheduled,
            advisories=advisories,
            want_title=True,
            pois=pois,
            restaurants=restaurants,
            plan_recap=plan_recap,
        )
        if out is not None:
            llm_title, llm_narration, llm_node_chips = out

    # narration：LLM 有就用 LLM；否则规则模板兜底
    narration = llm_narration or _template_narration(
        intent, itinerary, stage, quality_warnings, unmet_cuisines, advisories,
        plan_recap=plan_recap, unmet_not_scheduled=unmet_not_scheduled,
    )
    # title：LLM 解析出来就用；否则规则模板兜底（信息全 = 含所有主要站点）
    title = llm_title or _template_title(intent, itinerary)
    # node_chips：LLM 产出非空就用（已通过 _validate_llm_node_chips 校验）；
    # 否则（LLM 未用 / 解析失败 / 校验不通过 / LLM 判定 0 个）整体回落模板生成器。
    node_chips = llm_node_chips or generate_template_node_chips(itinerary, intent, pois, restaurants)
    return title, narration, node_chips


def generate_narration(
    *,
    intent: IntentExtraction,
    itinerary: Itinerary,
    stage: str = "stream",
    use_llm: bool = True,
    critic_summary: str = "",
    quality_warnings: Optional[list[str]] = None,
    unmet_cuisines: Optional[list[str]] = None,
    unmet_not_scheduled: Optional[list[str]] = None,
    advisories: Optional[list[str]] = None,
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
        advisories: ADR-0010 D-7 新增。planner「绝不默默忽略」的结构化告知
            （完整句子，如「点名想去的『XX馆』这次塞不进去了」），并入诚实告知段。

    Returns:
        2-3 句中文文案（80-200 字）。永远返回非空字符串。

    注：本函数只产 narration（不要 title）；需要同次产 title 的调用方用
    generate_title_and_narration（narrate 节点写回 itinerary.summary 用）。
    """
    # ADR-0014 决策 2（G-2）：告知限额，见 generate_title_and_narration 同款注释。
    advisories = _apply_advisory_disclosure_cap(list(advisories or []))
    if use_llm:
        out = _call_llm_narrator(
            intent=intent,
            itinerary=itinerary,
            stage_label=stage,
            critic_summary=critic_summary,
            quality_warnings=quality_warnings,
            unmet_cuisines=unmet_cuisines,
            unmet_not_scheduled=unmet_not_scheduled,
            advisories=advisories,
            want_title=False,
        )
        if out is not None:
            _title, narration, _node_chips = out
            if narration:
                return narration

    # Fallback / 规则模式（含 spec R6 兜底质疑 + 诚实告知）
    return _template_narration(
        intent, itinerary, stage, quality_warnings, unmet_cuisines, advisories,
        unmet_not_scheduled=unmet_not_scheduled,
    )


__all__ = [
    "generate_narration",
    "generate_title_and_narration",
    "build_template_title",
    "generate_template_node_chips",
    "split_unmet_by_nearby_availability",
]


# 公开规则版标题构造器（供最底层 summary 兜底路径在无 intent 时复用同一套逻辑）
def build_template_title(intent: IntentExtraction, itinerary: Itinerary) -> str:
    """规则版小红书标题（公开别名，等价 _template_title）。"""
    return _template_title(intent, itinerary)
