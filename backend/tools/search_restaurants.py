"""tools.search_restaurants —— T2 查询餐厅候选。

输入/输出：schemas.tools.SearchRestaurantsInput / SearchRestaurantsOutput
失败分支：
- EMPTY_CANDIDATES：候选 0 条

容量约束：
- capacity_requirement=4 → 餐厅必须存在 4 人桌；=6 必须存在 6 人桌；以此类推
- require_private_room=True → 餐厅必须有包间

座位时段可用性 *不在* 本 Tool 里判断——交给 check_restaurant_availability。
"""

from __future__ import annotations

from data.loader import load_restaurants
from schemas.domain import RestaurantCapacity
from schemas.errors import FailureReason
from schemas.tools import SearchRestaurantsInput, SearchRestaurantsOutput

from .registry import register_tool
from ._helpers import has_all_tags, has_any_tag


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
    candidates = []
    for r in load_restaurants():
        if r.distance_km > inp.distance_max_km:
            continue
        # 饮食约束：必须全部命中（低脂 + 健康轻食 + 有儿童餐 等）
        if not has_all_tags(r.tags, inp.dietary_constraints):
            continue
        # 体验偏好：命中任一即可
        if inp.experience_tags and not has_any_tag(r.tags, inp.experience_tags):
            continue
        if inp.social_context and inp.social_context not in r.suitable_for:
            continue
        # 桌型
        if inp.capacity_requirement and not _capacity_ok(
            r.capacity, inp.capacity_requirement
        ):
            continue
        if inp.require_private_room and not r.capacity.private_room:
            continue
        candidates.append(r)

    candidates.sort(key=lambda x: x.rating, reverse=True)
    candidates = candidates[: inp.limit]

    if not candidates:
        return SearchRestaurantsOutput(
            success=False,
            reason=FailureReason.EMPTY_CANDIDATES,
            candidates=[],
        )
    return SearchRestaurantsOutput(success=True, candidates=candidates)
