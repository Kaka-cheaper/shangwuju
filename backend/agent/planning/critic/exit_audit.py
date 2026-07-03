"""agent.planning.critic.exit_audit —— 出口满足度审计（ADR-0014 决策 2 · G-2）。

【ADR-0014 横向深审 P2 补丁：满足判定改逐节点判定】

改判前的实现把「同一类目标下全部节点」的 tags 取并集，再整体比对 required
soft tags 是否被并集覆盖——这与 `check_dietary`/`check_physical`（本模块
docstring 下方引用）的 **逐节点 ALL-match** 语义自相矛盾：两顿饭一顿日料、
一顿不是，并集里能凑出「日料」这个 tag，就被判"满足"，但实际上有一顿饭
根本没吃到日料。hard 子集已经改判成"每个 node 独立核验"（一票否决不能
被别的 node 平均掉），soft 子集用的却是"合起来算"的口径，两条语义在同一个
字段（dietary_constraints/physical_constraints）内自相矛盾。

改判后：**dietary_constraints** 逐 restaurant 节点独立核验、**physical_
constraints** 逐 poi 节点独立核验——与 `check_dietary`/`check_physical` 完全
同一粒度（`_unmet_by_tag_per_node` 复用 `has_all_tags`/`_unmet_soft_tags`，
不发明第二套匹配算法）；无该 kind 节点时不产生任何未满足记录（与
`check_dietary`/`check_physical` 遍历 0 个匹配节点时"零违规"的空集短路
行为一致——ADR-0010 决策 9 允许"多 POI 无饭"的涌现组成，找不到 restaurant
节点不该被本模块臆造成"全部 dietary 都没满足"）。

去重键（同一 tag 在多个节点未满足时如何呈现，避免告知条数随节点数线性
增长、连累 narrator 侧 `_apply_advisory_disclosure_cap` 的 ≤2 条限额）：
**按 tag 合并成一条 advisory**，message 里列出所有未满足的站名（复用
`humanize_node` 的「第 N 段「kind · title」」人话格式）。备选方案"每站
一条"被否决：advisory 条数会随行程节点数增长（多活动 TOPTW 下一趟行程可能
有 3-4 个同类节点），在 narrator 侧 ≤2 条限额下会不成比例挤占其它更该露出
的告知，而"同一个 tag 在哪几站没对上"本就是一句话能说清的信息，没必要拆
成多条。

**experience_tags 维持"整段行程任一节点覆盖即算达成"的既有并集语义，不
逐节点强判**——与 dietary/physical 是两类不同的约束：dietary/physical 描述
的是每一站各自独立的底线（每顿饭都要符合忌口、每个活动地点都要无障碍），
从"部分节点满足、部分节点不满足"里不能推出"总体达成"；而 experience_tags
描述的是整段行程的综合氛围（如"网红打卡"+"独处舒缓"两个体验诉求，完全可以
分别由不同节点各自贡献——上午安静喝茶、下午网红打卡拍照，两种体验都在
这一天里发生了，谁也没被"平均掉"），不存在 hard 版对称 check 可比照的
"每站都要独立满足"语义，改判范围不含它（见任务报告 P2 节的取舍说明）。

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

import logging

from schemas.advisory import Advisory, AdvisoryCode
from schemas.intent import IntentExtraction
from schemas.itinerary import Itinerary
from schemas.tags import is_hard_tag

from ._rules.helpers import humanize_node, safe_load_pois, safe_load_restaurants
from tools._helpers import has_all_tags

logger = logging.getLogger(__name__)


# 出处 → 口径短语（ADR-0014 决策 2：「哪条约束、按出处的口径」）。
# `default`（纯 schema 默认值 / 无出处数据）不在这里——不产生告知，见模块
# docstring「出处口径」节。
_PROVENANCE_CLAUSE: dict[str, str] = {
    "user_stated": "你说的",
    "prior": "你档案里的",
    "inferred": "我猜你想要的",
}


def _soft_tags_in_order(required: list[str]) -> list[str]:
    """`required` 保序去重后只留 soft 子集（hard 一律排除，不是本模块职责）。"""
    return [t for t in dict.fromkeys(required) if t and not is_hard_tag(t)]


def _unmet_soft_tags(required: list[str], satisfied_tags: set[str]) -> list[str]:
    """`required` 里 soft 子集中、`satisfied_tags` 没覆盖到的那些（保序去重）。

    复用 `tools._helpers.has_all_tags` 做"是否全满足"的判定原语——与
    `check_dietary`/`check_physical` 同一套"required ⊆ 候选 tags"语义，
    不发明第二套匹配算法。`satisfied_tags` 可以是单个节点的 tags（逐节点
    核验，见 `_unmet_by_tag_per_node`），也可以是多节点并集（experience_tags
    的整段行程口径，见模块 docstring）——本函数只负责"给定一个 tags 集合，
    required 里差哪些"，不关心调用方传入的是哪种粒度。
    """
    soft_required = _soft_tags_in_order(required)
    if not soft_required:
        return []
    if has_all_tags(satisfied_tags, soft_required):
        return []
    return [t for t in soft_required if t not in satisfied_tags]


def _unmet_by_tag_per_node(
    itinerary: Itinerary,
    required: list[str],
    *,
    target_kind: str,
    entities_by_id: dict,
) -> dict[str, list[str]]:
    """`required` 里 soft tag → 未覆盖到它的 `target_kind` 节点站名列表。

    逐 `target_kind` 节点独立核验（与 `check_dietary`/`check_physical` 同一
    "required ⊆ 该节点 tags"判定粒度，见模块 docstring「逐节点判定」节）；
    非 `target_kind` 的节点不参与。同一 tag 在多个节点未满足时，dedup 键
    是 tag 本身——站名追加进同一个 list（去重合并的拍板见模块 docstring）。

    无 `target_kind` 节点（如整趟行程没有餐厅）→ 返回空 dict，与
    `check_dietary`/`check_physical` 遍历 0 个匹配节点时的"零违规"空集
    短路行为一致，不臆造"全部未满足"。

    Returns:
        `{tag: [站名, ...]}`；key 顺序不保证 = intent 原始顺序（按节点遍历
        顺序首次出现即插入），调用方应按 `_soft_tags_in_order(required)`
        的顺序枚举 key 取值，见 `audit_constraint_relaxation`。
    """
    unmet: dict[str, list[str]] = {}
    for idx, node in enumerate(itinerary.nodes):
        if node.target_kind != target_kind:
            continue
        entity = entities_by_id.get(node.target_id or "")
        node_tags = set(entity.tags or []) if entity is not None else set()
        for tag in _unmet_soft_tags(required, node_tags):
            unmet.setdefault(tag, []).append(humanize_node(idx, node))
    return unmet


def _render_message(tag: str, provenance: str, unmet_stations: list[str]) -> str:
    """渲染单条 advisory 文案。

    `unmet_stations` 非空（dietary/physical 逐节点判定产出）→ 文案里点名
    是哪几站没对上（人话，复用 `humanize_node` 的站名格式）；为空
    （experience_tags 的整段行程口径，或历史调用）→ 退回原有"整体没能
    完全满足"措辞，不强行编造一个不存在的"站"。
    """
    clause = _PROVENANCE_CLAUSE[provenance]
    if not unmet_stations:
        return f"{clause}『{tag}』这次没能完全满足，已经用更合适的候选顶上了。"
    joined = "、".join(unmet_stations)
    verb = "没对上" if len(unmet_stations) == 1 else "都没对上"
    return (
        f"{clause}『{tag}』这次没能完全满足（{joined}{verb}），"
        "已经用更合适的候选顶上了。"
    )


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
        tag（出处 ∈ user_stated/prior/inferred）。dietary/physical 逐
        restaurant/poi 节点独立核验（未满足时文案点名哪几站）；experience_tags
        维持整段行程并集口径（见模块 docstring）。无未满足项，或全部未满足
        项出处都是 default/无出处数据 → 返回空列表。
    """
    restaurants_by_id = {r.id: r for r in safe_load_restaurants()}
    pois_by_id = {p.id: p for p in safe_load_pois()}

    # dietary_constraints：逐 restaurant 节点独立核验（P2 改判核心）。
    dietary_unmet = _unmet_by_tag_per_node(
        itinerary,
        list(intent.dietary_constraints),
        target_kind="restaurant",
        entities_by_id=restaurants_by_id,
    )
    # physical_constraints：逐 poi 节点独立核验（P2 改判核心）。
    physical_unmet = _unmet_by_tag_per_node(
        itinerary,
        list(intent.physical_constraints),
        target_kind="poi",
        entities_by_id=pois_by_id,
    )

    # experience_tags：维持"整段行程任一节点覆盖即算达成"的既有并集语义
    # （不逐节点强判，理由见模块 docstring「experience_tags 维持既有并集
    # 语义」节）。
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
    experience_unmet_tags = _unmet_soft_tags(
        list(intent.experience_tags), restaurant_tags | poi_tags
    )

    # (field, tag, unmet_stations) 顺序：dietary → physical → experience，
    # 字段内按 intent 原始列表顺序——决定「限额只显 2 条时哪 2 条优先露出」
    # 的确定性顺序（narrator 侧的 ≤2 cap 不改这里的产出顺序，只截断展示）。
    unmet: list[tuple[str, str, list[str]]] = []
    for tag in _soft_tags_in_order(list(intent.dietary_constraints)):
        stations = dietary_unmet.get(tag)
        if stations:
            unmet.append(("dietary_constraints", tag, stations))
    for tag in _soft_tags_in_order(list(intent.physical_constraints)):
        stations = physical_unmet.get(tag)
        if stations:
            unmet.append(("physical_constraints", tag, stations))
    for tag in experience_unmet_tags:
        unmet.append(("experience_tags", tag, []))

    provenance_map = intent.field_provenance or {}
    advisories: list[Advisory] = []
    log_entries: list[dict] = []
    for field, tag, stations in unmet:
        provenance = provenance_map.get(f"{field}:{tag}")
        if provenance not in _PROVENANCE_CLAUSE:
            continue  # default / 无出处数据 → 不打扰，见模块 docstring
        advisories.append(
            Advisory(
                code=AdvisoryCode.CONSTRAINT_RELAXED,
                message=_render_message(tag, provenance, stations),
            )
        )
        log_entries.append(
            {
                "field": field,
                "tag": tag,
                "provenance": provenance,
                "unmet_stations": stations,
            }
        )

    if log_entries:
        # 卫生债补丁（ADR-0014 横向深审 P3）：soft 约束被判"部分满足/放宽"
        # 是关键决策，此前零日志痕迹——补一条结构化 info，供事后排障对照
        # "这次到底哪个 tag 在哪一站没对上"。
        logger.info("[exit_audit] soft 约束部分满足，产出告知：%s", log_entries)

    return advisories


__all__ = ["audit_constraint_relaxation"]
