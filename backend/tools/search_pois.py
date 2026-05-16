"""tools.search_pois —— T1 查询 POI（活动地点）候选。

输入/输出：schemas.tools.SearchPoisInput / SearchPoisOutput
失败分支：
- EMPTY_CANDIDATES：所有过滤条件叠加后 0 候选

埋点的失败案例：mock 中 P002（展览售罄）、P006（茶馆售罄）、P010（SPA 满）、P013（密室满）
本 Tool 只查询不预约，「售罄」状态体现在 capacity.available_slots=0，但
**仍可作为候选返回**——是否触发售罄异常由调用方在 buy_ticket 阶段判断。
这与 search_restaurants 同理：查询类 Tool 返回候选清单，可用性由专用 Tool 校验。
"""

from __future__ import annotations

from data.loader import load_pois
from schemas.errors import FailureReason
from schemas.tools import SearchPoisInput, SearchPoisOutput

from .registry import register_tool
from ._helpers import has_all_tags, has_any_tag


_DESC = (
    "按距离 / 物理标签 / 体验标签 / 社交语境 / 同行人年龄查询活动地点（POI）候选。"
    "输入约束越多过滤越严；过严会返 success=false + reason=empty_candidates。"
    "返回候选不保证库存可用，门票 / 预约通过 buy_ticket 类 Tool 单独校验。"
)


@register_tool(
    name="search_pois",
    description=_DESC,
    input_model=SearchPoisInput,
    output_model=SearchPoisOutput,
)
def search_pois(inp: SearchPoisInput) -> SearchPoisOutput:
    candidates = []
    for poi in load_pois():
        # 距离过滤
        if poi.distance_km > inp.distance_max_km:
            continue
        # 物理约束：必须 *全部* 命中（亲子友好+适合 5-10 岁）
        if not has_all_tags(poi.tags, inp.physical_constraints):
            continue
        # 体验偏好：命中任意一个即可（"网红打卡"或"安静聊天"任一即过）
        if inp.experience_tags and not has_any_tag(poi.tags, inp.experience_tags):
            continue
        # social_context：若指定，POI 必须在 suitable_for 中声明可适配
        if inp.social_context and inp.social_context not in poi.suitable_for:
            continue
        # 偏好类型：若指定，POI.type 必须命中其一
        if inp.preferred_types and poi.type not in inp.preferred_types:
            continue
        # 同行年龄：若指定且 POI 给了 age_range，则全员必须落在区间
        if inp.age_in_party and poi.age_range:
            lo, hi = poi.age_range[0], poi.age_range[1]
            if not all(lo <= age <= hi for age in inp.age_in_party):
                continue
        candidates.append(poi)

    # 按 rating 倒序，取 limit
    candidates.sort(key=lambda p: p.rating, reverse=True)
    candidates = candidates[: inp.limit]

    if not candidates:
        return SearchPoisOutput(
            success=False,
            reason=FailureReason.EMPTY_CANDIDATES,
            candidates=[],
        )
    return SearchPoisOutput(success=True, candidates=candidates)
