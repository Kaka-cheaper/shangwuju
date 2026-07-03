"""tools.search_restaurants —— T2 查询餐厅候选。

输入/输出：schemas.tools.SearchRestaurantsInput / SearchRestaurantsOutput
失败分支：
- EMPTY_CANDIDATES：候选 0 条

容量约束：
- capacity_requirement=4 → 餐厅必须存在 4 人桌；=6 必须存在 6 人桌；以此类推
- require_private_room=True → 餐厅必须有包间

座位时段可用性 *不在* 本 Tool 里判断——交给 check_restaurant_availability。

Step 6：tag relaxation（ADR-0014 决策 2 · G-2 改造）
- dietary_constraints 全命中打到空集时自动渐进放宽
- hard tag（不辣 / 无牛肉 / 软烂，见 schemas.tags.DIETARY_HARD_TAGS）永不
  放宽；soft tag（低脂 / 日料 / 高人均 等风格型）按 inp.tag_provenance 出处
  降级序丢弃
- output.relaxed_tags 只列实际丢弃的 soft tag，是纯调试信息（见
  schemas.tools.SearchRestaurantsOutput.relaxed_tags docstring）
"""

from __future__ import annotations

from data.loader import load_restaurants
from schemas.domain import RestaurantCapacity
from schemas.errors import FailureReason
from schemas.tools import SearchRestaurantsInput, SearchRestaurantsOutput

from .registry import register_tool
from ._helpers import has_any_tag, relax_tag_search


_DESC = (
    "按距离 / 饮食标签 / 体验标签 / 社交语境 / 桌型 / 是否需要包间查询餐厅候选。"
    "返回候选不代表当时段可订位；时段可用性由 check_restaurant_availability 单独校验。"
    "0 候选返 success=false + reason=empty_candidates。"
)


def _capacity_ok(cap: RestaurantCapacity, party: int) -> bool:
    """party 人数对应的桌型是否存在。"""
    if party <= 2:
        return cap.two
    if party <= 4:
        return cap.four
    if party <= 6:
        return cap.six
    return cap.eight


@register_tool(
    name="search_restaurants",
    description=_DESC,
    input_model=SearchRestaurantsInput,
    output_model=SearchRestaurantsOutput,
)
def search_restaurants(inp: SearchRestaurantsInput) -> SearchRestaurantsOutput:
    # 候选源：提供 user_lat/user_lng 时走 NearbySearchProvider 实时算距离；
    # 缺省时回退到 mock 数据本身的 distance_km 字段（向后兼容）
    if inp.user_lat is not None and inp.user_lng is not None:
        from data.nearby_provider import get_nearby_provider

        provider = get_nearby_provider()
        source_rests = provider.search_restaurants_nearby(
            inp.user_lat, inp.user_lng, inp.distance_max_km
        )
    else:
        source_rests = list(load_restaurants())

    # 第一道：与 dietary tag 无关的硬过滤
    excluded = set(inp.exclude_visited_ids or [])

    def _non_tag_filter(r):
        if r.id in excluded:
            return False
        if r.distance_km > inp.distance_max_km:
            return False
        # 体验偏好：命中任一即可
        if inp.experience_tags and not has_any_tag(r.tags, inp.experience_tags):
            return False
        if inp.social_context and inp.social_context not in r.suitable_for:
            return False
        # 桌型
        if inp.capacity_requirement and not _capacity_ok(
            r.capacity, inp.capacity_requirement
        ):
            return False
        if inp.require_private_room and not r.capacity.private_room:
            return False
        return True

    # 第二道：dietary tag 渐进放宽（多 tag 复合饮食约束兜底）
    candidates, relaxed_tags = relax_tag_search(
        list(inp.dietary_constraints),
        source_rests,
        extract_tags=lambda r: r.tags,
        additional_filter=_non_tag_filter,
        max_relax_levels=3,
        tag_provenance=inp.tag_provenance,
    )

    candidates.sort(key=lambda x: x.rating, reverse=True)
    candidates = candidates[: inp.limit]

    if not candidates:
        return SearchRestaurantsOutput(
            success=False,
            reason=FailureReason.EMPTY_CANDIDATES,
            candidates=[],
            relaxed_tags=relaxed_tags,
        )
    return SearchRestaurantsOutput(
        success=True,
        candidates=candidates,
        relaxed_tags=relaxed_tags,
    )
