"""agent.planning.critic.exit_audit —— 出口满足度审计（ADR-0014 决策 2 · G-2）。

【这是什么问题 + 为什么不在搜索期/三条规划路径各自里做】

`tools._helpers.relax_tag_search` 里的 soft tag 渐进放宽（ADR-0014 决策 2）
发生在**搜索期**——三条规划路径（`agent/runtime/tools/search_adapter.py`
execute 主路径 / `ils_planner.py` / `rule_planner.py`）各自独立调 Tool，
若要在"放宽的那一刻"就告知用户，需要在三处构造点各自接一遍"这个 tag 被
放宽了要不要告知"的判断——ADR-0014 决策 2 原方案叫"三路径收口"，二轮拷问后
被改判：搜索期的中间尝试不是最终真相（同一个 tag 可能第一次被某条路径放宽，
但最终选中的候选恰好又满足它；也可能被放宽后 critic 修复闭环又换了一个满足
它的候选）——**只有方案定稿那一刻的最终 itinerary 才是该不该告知的唯一真相
来源**。

本模块因此把"告知"收口成单点：在方案定稿处（`agent.graph.nodes.
finalize_plan.finalize_plan_node`）统一比对**最终** itinerary 每个节点的
tags vs intent 的全部约束（dietary/physical/experience），天然覆盖三条
路径产出的任何最终方案，不必在搜索期插桩、不必信任中间层的"放宽记录"是否
准确传导到了这里。与 `agent.intent.narrator.detect_unmet_cuisine_preference`/
`detect_unmet_poi_preference`（unmet 家族）同一模式：单点实现、比对最终
产物、不发明新的 tag 匹配算法（复用 `tools._helpers.has_all_tags`，与
`agent.planning.critic._rules.checks.check_dietary` 同一"required ⊆ 候选
tags"判定原语）。

【只管 soft，不管 hard】

hard 约束（不辣/无牛肉/软烂/无台阶/无障碍/适合老人/可休息）的"必须满足"由
`check_dietary`/`check_physical`（HARD severity，gate 修复闭环）保证——
一个干净通过 critic 的方案，hard 约束不可能不满足（除非走到 give_up 这种
"critic 都没能收敛"的边界，那是另一套告知机制，见任务报告"give_up 放宽
建议"节，不在本模块职责内）。本模块只处理 soft 约束——它们从设计上就不
gate，缺了不是方案的缺陷，是"这组约束下能做到的最好结果"，需要的只是
诚实说一声，不是拦下来重做。

【出处口径：为什么 default 不告知】

三种出处（user_stated/prior/inferred）未满足时都告知，措辞按"哪个口径"
区分（你说的/你档案里的/我猜你想要的）；`default`（纯 schema 默认值/无
出处数据）不产生 advisory——用户对这件事从未表达过任何信号（无论是原话、
档案先验还是推断），没有什么"值得说一声"的立场可以归因，强行编一句"你
没提过的 XX 这次没做到"没有意义，也不是"先丢不打扰"这条纪律的例外。

不负责：
- 告知条数限额（≤2 条 + 折叠句）——在 `agent.intent.narrator` 的 advisory
  渲染层统一处理（跨 D-7 既有 advisory + 本模块产出的合并列表限额，见该
  模块 `_apply_advisory_disclosure_cap`）。
- hard 约束核验（`agent.planning.critic._rules.checks.check_dietary` /
  `check_physical`）。
- relax_tag_search 的搜索期降级本身（`tools._helpers.relax_tag_search`）。
"""

from __future__ import annotations

from schemas.advisory import Advisory, AdvisoryCode
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary
from schemas.tags import is_hard_tag

from ._rules.helpers import safe_load_pois, safe_load_restaurants
from tools._helpers import has_all_tags


# 出处 → 口径短语（ADR-0014 决策 2：「哪条约束、按出处的口径」）。
# `default`（纯 schema 默认值 / 无出处数据）不在这里——不产生告知，见模块
# docstring「出处口径」节。
_PROVENANCE_CLAUSE: dict[str, str] = {
    "user_stated": "你说的",
    "prior": "你档案里的",
    "inferred": "我猜你想要的",
}


def _unmet_soft_tags(required: list[str], satisfied_tags: set[str]) -> list[str]:
    """`required` 里 soft 子集中、`satisfied_tags` 没覆盖到的那些（保序去重）。

    hard tag 一律排除（不是本模块职责，见模块 docstring）。复用
    `tools._helpers.has_all_tags` 做"是否全满足"的判定原语——与
    `check_dietary`/`check_physical` 同一套"required ⊆ 候选 tags"语义，
    不发明第二套匹配算法。
    """
    soft_required = [t for t in dict.fromkeys(required) if t and not is_hard_tag(t)]
    if not soft_required:
        return []
    if has_all_tags(satisfied_tags, soft_required):
        return []
    return [t for t in soft_required if t not in satisfied_tags]


def _render_message(tag: str, provenance: str) -> str:
    clause = _PROVENANCE_CLAUSE[provenance]
    return f"{clause}『{tag}』这次没能完全满足，已经用更合适的候选顶上了。"


def audit_constraint_relaxation(
    itinerary: Itinerary, intent: IntentExtraction
) -> list[Advisory]:
    """比对最终 itinerary 每个节点 vs intent 全部约束，soft 未满足 → advisory。

    Args:
        itinerary: 已定稿的最终方案（不区分来自哪条规划路径）。
        intent: 本轮意图（读 dietary_constraints/physical_constraints/
            experience_tags 三类受控词典 + field_provenance）。

    Returns:
        `AdvisoryCode.CONSTRAINT_RELAXED` 列表；每条对应一个未满足的 soft
        tag（出处 ∈ user_stated/prior/inferred）。无未满足项，或全部未满足
        项出处都是 default/无出处数据 → 返回空列表。
    """
    restaurants_by_id = {r.id: r for r in safe_load_restaurants()}
    pois_by_id = {p.id: p for p in safe_load_pois()}

    restaurant_tags: set[str] = set()
    poi_tags: set[str] = set()
    for node in itinerary.nodes:
        if node.target_kind == "restaurant":
            rest = restaurants_by_id.get(node.target_id or "")
            if rest is not None:
                restaurant_tags |= set(rest.tags or [])
        elif node.target_kind == "poi":
            poi = pois_by_id.get(node.target_id or "")
            if poi is not None:
                poi_tags |= set(poi.tags or [])

    # (field, tag) 顺序：dietary → physical → experience，字段内按 intent
    # 原始列表顺序——决定「限额只显 2 条时哪 2 条优先露出」的确定性顺序
    # （narrator 侧的 ≤2 cap 不改这里的产出顺序，只截断展示）。
    unmet: list[tuple[str, str]] = []
    for tag in _unmet_soft_tags(list(intent.dietary_constraints), restaurant_tags):
        unmet.append(("dietary_constraints", tag))
    for tag in _unmet_soft_tags(list(intent.physical_constraints), poi_tags):
        unmet.append(("physical_constraints", tag))
    for tag in _unmet_soft_tags(
        list(intent.experience_tags), restaurant_tags | poi_tags
    ):
        unmet.append(("experience_tags", tag))

    provenance_map = intent.field_provenance or {}
    advisories: list[Advisory] = []
    for field, tag in unmet:
        provenance = provenance_map.get(f"{field}:{tag}")
        if provenance not in _PROVENANCE_CLAUSE:
            continue  # default / 无出处数据 → 不打扰，见模块 docstring
        advisories.append(
            Advisory(
                code=AdvisoryCode.CONSTRAINT_RELAXED,
                message=_render_message(tag, provenance),
            )
        )
    return advisories


__all__ = ["audit_constraint_relaxation"]
