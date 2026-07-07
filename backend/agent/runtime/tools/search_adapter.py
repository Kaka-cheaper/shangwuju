"""agent.tools.search_adapter —— 把 IntentExtraction 转成 ToolInput 调工具。

execute 阶段的 worker 调它：

    pois = search_pois_for_intent(intent)
    rests = search_restaurants_for_intent(intent)

不抛异常：失败/空集返回空 list（让 replan 去判断）。

不发明 schema —— 直接复用 schemas/tools.py 的 Input/Output。
"""

from __future__ import annotations

from typing import Optional

from schemas.category_vocab import poi_desire_match, restaurant_desire_match
from schemas.domain import Poi, Restaurant
from schemas.intent import IntentExtraction, extract_tag_provenance
from schemas.tools import (
    GetUserProfileInput,
    GetUserProfileOutput,
    SearchPoisInput,
    SearchRestaurantsInput,
)
from tools.registry import invoke_tool


def _resolve_user_coords(user_id: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """从 user_profile 取 home_location 的 lat/lng；缺省返 (None, None)。

    NearbySearchProvider 拿到 (lat, lng) 才能实时算距离；缺省时 search_pois /
    search_restaurants 会回退到 mock 数据预填的 distance_km 字段。
    """
    if not user_id:
        return (None, None)
    try:
        out = invoke_tool(
            "get_user_profile", GetUserProfileInput(user_id=user_id).model_dump()
        )
        if not out or not getattr(out, "success", False):
            return (None, None)
        profile = GetUserProfileOutput.model_validate(out.output).profile
        if profile is None:
            return (None, None)
        loc = profile.home_location
        return (loc.lat, loc.lng)
    except Exception:  # noqa: BLE001
        return (None, None)


def _resolve_excluded_visited_ids(
    session_id: Optional[str], *, kind: str
) -> list[str]:
    """从会话私有 UserMemory 取最近 30 天访问过的 target_id（按 kind 过滤）。

    键语义（记忆身份读写分离批，ADR-0015 身份边界补充决策）：访问史是"确认
    累积"，按 **session_id** 键控（会话即身份）——不再按 user_id：那是共享
    只读的画像模板 id，按它排重会让 A 访客确认过的地方从 B 访客的候选里消失。

    Args:
        session_id: 会话 id；空则返空 list（无排重）
        kind: 'poi' 或 'restaurant'

    失败兜底返空——不应影响主路径。
    """
    if not session_id:
        return []
    try:
        from data.memory_store import get_memory

        memory = get_memory(session_id)
        if not memory.visited_targets:
            return []
        recent_ids = set(memory.recently_visited_ids(within_days=30))
        # 仅返指定 kind 的
        return [
            r.target_id
            for r in memory.visited_targets
            if r.target_id in recent_ids and r.target_kind == kind
        ]
    except Exception:  # noqa: BLE001
        return []


def search_pois_for_intent(
    intent: IntentExtraction,
    *,
    limit: int = 5,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> tuple[list[Poi], list[str]]:
    """按 intent 调 search_pois，返回 (候选, relaxed_tags)；失败返 ([], [])。

    双键（读写分离批）：
    - user_id（模板）：从 user_profile 取 home_location 作 NearbyProvider 的查询基准
    - session_id（累积）：从会话私有 UserMemory 取最近 30 天访问过的 POI id 排除
      （Step 7 个性化记忆——排重范围是"这段会话确认过的"，跨访客不串味）
    Step 6：tag relaxation 透出 relaxed_tags 让上层（execute_worker / sse_adapter）
    把放宽路径透传给前端 / LLM。

    spec narration-and-intent-fidelity R3：用户明示活动诉求（preferred_poi_types）时
    扩池抓取 + 按 type/name/tags 词法重排把命中诉求的候选前置（治本"说了看展方案里有展"）。
    """
    age_in_party = sorted(
        {c.age for c in intent.companions if c.age is not None}
    )
    user_lat, user_lng = _resolve_user_coords(user_id)
    excluded_ids = _resolve_excluded_visited_ids(session_id, kind="poi")
    # spec narration-and-intent-fidelity R3（POI 诉求轻量词法重排）：
    # 用户明示活动诉求（preferred_poi_types）时扩大抓取池，避免词法命中的候选
    # （rating 可能不是最高）在 Tool 层 top-k 截断阶段被高分泛候选挤掉；
    # 扩池后在编排层按 type/name/tags 词法重排，再截断回 limit。
    # 不把 preferred_poi_types 塞进 SearchPoisInput.preferred_types——那是精确
    # `poi.type not in` 匹配，「看展」∉「展览」会归零（见 design.md Property 5）。
    fetch_limit = max(limit, 15) if intent.preferred_poi_types else limit
    inp = SearchPoisInput(
        distance_max_km=intent.distance_max_km or 5.0,
        physical_constraints=list(intent.physical_constraints),
        experience_tags=list(intent.experience_tags),
        # ADR-0014 决策 2（G-2）：出处透传三处构造点之一（改一处查三处，另两
        # 处见 rule_planner.py / ils_planner.py 的 _query_pois），供
        # relax_tag_search 的 soft tag 降级序排序。
        tag_provenance=extract_tag_provenance(
            intent, "physical_constraints", intent.physical_constraints
        ),
        social_context=intent.social_context,
        # L1 anchor-escape：显式点名的活动品类（preferred_poi_types）作为 anchor_terms
        # 传入——命中的候选在工具内跳过 experience_tags / social_context 两道推断场景
        # 硬过滤（显式诉求压过推断调性），非锚候选照旧硬过滤（case(b) 零回归）。
        anchor_terms=list(intent.preferred_poi_types) or None,
        age_in_party=list(age_in_party),
        user_lat=user_lat,
        user_lng=user_lng,
        exclude_visited_ids=excluded_ids,
        limit=fetch_limit,
    )
    out = invoke_tool("search_pois", inp.model_dump())
    if not out or not getattr(out, "success", False):
        # 即使失败仍尝试取 relaxed_tags（让上层知道哪些被放过）
        relaxed = (out.output or {}).get("relaxed_tags") if out else []
        return [], list(relaxed or [])
    output_dict = out.output or {}
    candidates = output_dict.get("candidates") or []
    relaxed = output_dict.get("relaxed_tags") or []
    # output 是 dict, candidates 内可能是 dict 也可能是 Poi 对象
    result: list[Poi] = []
    for c in candidates:
        if isinstance(c, Poi):
            result.append(c)
        elif isinstance(c, dict):
            try:
                result.append(Poi.model_validate(c))
            except Exception:  # noqa: BLE001
                continue
    # spec narration-and-intent-fidelity R3（治本核心）：用户明示活动诉求
    # （preferred_poi_types，如「看展」）时，把 type/name/tags 词法命中的候选前置，
    # 避免被高 rating 但不对味的候选（猫咖/甜品）挤出 blueprint top-k 预览。
    # 与餐厅侧 _rerank_by_preferred_cuisine 同源策略——不改 Tool（守 §3.4 场景无感）、
    # 不改 graph 拓扑。召回仍走宽松 has_any_tag，只动排序。
    result = _rerank_by_preferred_poi_types(result, intent.preferred_poi_types)
    # 扩池后截断回原 limit（rerank 已把命中诉求前置，截断不丢命中候选）
    return result[:limit], list(relaxed)


# ============================================================
# POI 明示诉求轻量词法匹配 + 重排（spec narration-and-intent-fidelity R3）
# ============================================================


def _rerank_by_preferred_poi_types(
    pois: list[Poi], preferred_poi_types: list[str]
) -> list[Poi]:
    """把 type/name/tags 与 preferred_poi_types 任一词词法命中的 POI 稳定前置。

    无 preferred_poi_types 或无命中 → 原序返回（稳定排序不打乱原 rating 序，零回归）。
    与餐厅侧 _rerank_by_preferred_cuisine 同源；判定走 poi_desire_match（谓词 SoT 已
    下沉 `schemas.category_vocab`）。

    【L1 anchor-escape 上线后（兜底）】工具侧 `search_pois` 已在截断前保护命中
    anchor_terms 的候选出 top-k，本重排对该目的冗余，保留做兜底（见
    `_rerank_by_preferred_cuisine` 同款注记）。

    PUBLIC SEAM（改口根治批）：`agent.planning.planners.ils_planner._query_pois`
    顶层 import 本函数做 ILS 召回的品类感知重排——虽带下划线，事实上是跨模块
    公开契约（同 ils_planner._env_int 的先例）。删除/改签名前先迁移那处 import。
    """
    if not preferred_poi_types:
        return pois
    prefs = [p for p in preferred_poi_types if p and p.strip()]
    if not prefs:
        return pois

    def _match(poi: Poi) -> bool:
        return any(
            poi_desire_match(p, poi.type, poi.name, list(poi.tags or []))
            for p in prefs
        )

    matched = [p for p in pois if _match(p)]
    rest = [p for p in pois if not _match(p)]
    return matched + rest


def search_restaurants_for_intent(
    intent: IntentExtraction,
    *,
    limit: int = 5,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> tuple[list[Restaurant], list[str]]:
    """按 intent 调 search_restaurants，返回 (候选, relaxed_tags)；失败返 ([], [])。

    双键（读写分离批，同 search_pois_for_intent）：
    - user_id（模板）：从 user_profile 取 home_location 作 NearbyProvider 的查询基准
    - session_id（累积）：从会话私有 UserMemory 取最近 30 天访问过的餐厅 id 排除（Step 7）
    """
    # party_size：本路径（execute 阶段主路径）用「实付头数」自算——self + 全部
    # companions.count，与 rule_planner/ils_planner 两处 _query_restaurants 刻意
    # 不同源（那两处直传 intent.capacity_requirement，LLM 按"≥4 人才填"规则自填，
    # 目的是让搜索期过滤与 critic._rules.checks.check_capacity 的判定用**同一个
    # 字段**、避免"搜索按 X 过滤、critic 按 Y 判"的双真源不一致）。本路径不复用
    # 该字段：execute 阶段已有 companions 明细，算真实头数比依赖 LLM 是否恰好
    # 填了该 optional 字段更可靠——不为统一而统一（判断详见任务报告"party_size
    # 语义核查"节）。
    party_size = max(1, sum(c.count for c in intent.companions) + 1)  # +1 自己
    user_lat, user_lng = _resolve_user_coords(user_id)
    excluded_ids = _resolve_excluded_visited_ids(session_id, kind="restaurant")
    # 块B-2（R2）：用户明示品类时扩大抓取池，避免 cuisine 命中的候选（评分略低）
    # 在 Tool 层 top-k 截断阶段就被挤掉；扩池后在编排层重排再截断回 limit。
    fetch_limit = max(limit, 15) if intent.preferred_poi_types else limit
    # 三处 SearchRestaurantsInput 构造点之一（改一处查三处，另两处见
    # ils_planner.py::_query_restaurants / rule_planner.py::_query_restaurants）：
    # experience_tags 必须显式传（tools/search_restaurants.py 内部用 has_any_tag
    # 宽松过滤氛围词候选）——此前本处漏传，是 bug 之一，见任务报告。
    # capacity_requirement 直传 party_size（不再做 2/4/6/8 精确匹配守门——那是
    # 另一个 bug：非精确桌型人数会被误判为"不过滤"，见任务报告）；
    # tools/search_restaurants.py::_capacity_ok 本就按 ≤2/≤4/≤6/其余 分档，任意
    # 整数都能正确分档，不需要在这里预先对齐到桌型档位。
    inp = SearchRestaurantsInput(
        distance_max_km=intent.distance_max_km or 5.0,
        dietary_constraints=list(intent.dietary_constraints),
        experience_tags=list(intent.experience_tags),
        # ADR-0014 决策 2（G-2）：出处透传三处构造点之一，见上方
        # search_pois_for_intent 同款注释。
        tag_provenance=extract_tag_provenance(
            intent, "dietary_constraints", intent.dietary_constraints
        ),
        social_context=intent.social_context,
        # L1 anchor-escape：显式点名的餐饮品类（如「烧烤」）作为 anchor_terms——命中
        # 的候选在工具内跳过 experience_tags / social_context 硬过滤（治「独处放空推断
        # 场景把显式烧烤删光」），非锚候选照旧硬过滤（case(b) 零回归）。
        anchor_terms=list(intent.preferred_poi_types) or None,
        capacity_requirement=party_size,
        user_lat=user_lat,
        user_lng=user_lng,
        exclude_visited_ids=excluded_ids,
        limit=fetch_limit,
    )
    out = invoke_tool("search_restaurants", inp.model_dump())
    if not out or not getattr(out, "success", False):
        relaxed = (out.output or {}).get("relaxed_tags") if out else []
        return [], list(relaxed or [])
    output_dict = out.output or {}
    candidates = output_dict.get("candidates") or []
    relaxed = output_dict.get("relaxed_tags") or []
    result: list[Restaurant] = []
    for c in candidates:
        if isinstance(c, Restaurant):
            result.append(c)
        elif isinstance(c, dict):
            try:
                result.append(Restaurant.model_validate(c))
            except Exception:  # noqa: BLE001
                continue
    # spec planning-pipeline-consolidation 块B-2（R2）：用户明示餐饮品类
    # （preferred_poi_types，如「烧烤」）时，把 cuisine 命中的候选提前，
    # 避免被高评分但不对味的候选（火锅/日料）挤出 blueprint top-k 预览。
    # search_restaurants Tool 本身不消费 preferred_poi_types（Tool 层对场景无感，
    # 见 AGENTS.md §3.4），故在编排层做 cuisine 重排——不改 Tool、不改 graph 拓扑。
    result = _rerank_by_preferred_cuisine(result, intent.preferred_poi_types)
    # 扩池后截断回原 limit（rerank 已把命中品类前置，截断不丢命中候选）
    return result[:limit], list(relaxed)


def _rerank_by_preferred_cuisine(
    restaurants: list[Restaurant], preferred_poi_types: list[str]
) -> list[Restaurant]:
    """把 cuisine 与 preferred_poi_types 任一词互相包含的候选稳定前置。

    匹配规则（宽松双向 substring，走 restaurant_desire_match 同一把尺子）：
    preferred 词 in cuisine 或 cuisine in preferred 词，例如 preferred=["烧烤"] 命中
    cuisine="烧烤"；preferred=["串"] 命中 "串串"。
    无 preferred_poi_types 或无命中 → 原序返回（稳定排序不打乱原 rating 序）。

    【L1 anchor-escape 上线后的定位（兜底）】工具侧 `search_restaurants` 现已在
    截断前把命中 anchor_terms 的候选稳定前置、保护出 top-k，本编排层重排因此对
    「保护出 top-k」这一目的已冗余；保留它做**兜底排序**（工具无锚保护时、或
    ils/rule 其它路径复用时仍前置命中品类），不删（删要动 ils 顶层 import，牵连
    面大）。谓词 SoT 见 `schemas.category_vocab.restaurant_desire_match`。
    """
    if not preferred_poi_types:
        return restaurants
    prefs = [p for p in preferred_poi_types if p]
    if not prefs:
        return restaurants

    matched = [r for r in restaurants if restaurant_desire_match(prefs, r.cuisine)]
    rest = [r for r in restaurants if not restaurant_desire_match(prefs, r.cuisine)]
    return matched + rest


def get_user_profile_for_user(user_id: str) -> Optional[GetUserProfileOutput]:
    """调 get_user_profile；失败返 None。"""
    try:
        inp = GetUserProfileInput(user_id=user_id)
        out = invoke_tool("get_user_profile", inp.model_dump())
        if not out or not getattr(out, "success", False):
            return None
        try:
            return GetUserProfileOutput.model_validate(out.output)
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None
