"""agent.planning.planners.node_swap —— ADR-0013 F-1：局部重解引擎（换菜/定向调整）。

【这是什么问题 + prior art】

多人协商场景里嫌一个节点（"这家火锅不好"）不该触发整个方案洗牌——现状
（房间点踩合成「不满意第 N 段，请换一个」→ 全量重排，`room.py:273`）正是
ADR-0013 要治的病根。这是经典的**局部修复 / min-conflicts**问题（Minton et
al. 1992："只重赋参与被违反约束的那个变量"，ADR-0009 已引为 prior art），
不是"重新规划一次"——钉住其余节点不动、只在腾出来的这一格里重解。

本模块是该语义的**具名落地**：`resolve_node_swap` 针对**用户点名的这一个
节点**做定向修复（区别于 `ils_planner.plan_hybrid` 内部那条给 critic 违规
用的、blame 驱动的修复闭环——两者现在共享同一个求解算子
`route_builder.repair_route`，见下方「机制」节）；`feasible_alternatives`
用同一套候选/可行性判定预览"还能换成什么"，供前端"具名备选"列表使用，
与点击换菜同一真相源（不会出现"预览里有、点了却换不成"的割裂）。

【机制（ADR-0013 F-1 "2026-07-03 机制修正"）：为什么不走 build_route】

局部重解直觉上像"重新构造一遍路线"，但 `route_builder.build_route` 的贪心
插入构造含**涌现填充循环**——选定锚点后会持续拿边际分最高的候选往里塞，
直到触发四条停止条件之一。若换上的替代品比原节点"更短/更好排"，涌现循环
会在腾出来的时间预算里**加塞额外活动**，破坏"只动这一格"的承诺（用户点了
"换个粤菜馆"，方案却凭空多了一站）。

真正贴合"腾格→只补该格→不加塞"语义的是原 `ils_planner._repair_route`（给
critic 违规做定向修复的既有算子，ADR-0013 F-1 起已搬到并改名
`route_builder.repair_route`，本模块 import 的正是这个新名字，见下方
import 块）：把命中黑名单的节点从上一轮排程里剔除，
只为空出的那个槽位找边际分最高的替补插回，其余节点原样保留，找不到替补就
让槽位空着（不强凑）。ADR-0013 裁决：**提升它为共享 seam**（`route_builder.
repair_route`，本模块直接复用，不新写第二份"只补一格"的逻辑）——局部重解
= 把"要挪走的目标"设成它的一次性黑名单（`blacklist_poi={target_id}` 或
`blacklist_rest={target_id}`，二选一），候选池按下方"降级序列"分级传入。

【Visit 重建（当前方案 → 候选池反查）】

`Itinerary.ActivityNode` 只存 `target_kind`/`target_id`/`duration_min`/
`start_time`/`note`，不带完整实体（价格/标签/评分等业务字段）——降级序列的
子类判定、方向性谓词（更便宜/更近）都需要这些字段。本模块因此对方案里
**每一个非 home 节点**（含目标节点与全部保留节点）按 `target_id` 反查调用方
传入的 `pois`/`restaurants` 候选池，用 `activity_pool.build_visit_from_poi`/
`build_visit_from_restaurant`（与涌现候选完全同一条构造路径，口径不漂移）
重建 `Visit`。**已知取舍**：重建时不传 `semantic_scores`（本会话 LLM 语义分
只在首次规划时算过一次，局部重解不重新调 LLM——engine 免 LLM 是 ADR-0013
F-1 的既定设计，"替换候选走中性默认"，见 `activity_pool._utility` 的
`semantic_scores=None` 分支），因此重建出的 `base_score` 与**原方案构造时**
若曾有语义分参与，存在打分基准漂移——这是"局部重解不调 LLM"的必然代价，
不是遗漏，不要试图在这里悄悄接 LLM 补分。

**前置条件（调用方契约，不满足即 `ValueError`，不是业务失败）**：
1. `target_node_id` 必须真实指向 `itinerary.nodes` 里某个非 home 节点
   （home 排除，调用方负责不传 home 的 id——ADR-0013 原文"不会歧义"）。
2. `pois`/`restaurants` 候选池必须覆盖当前方案里**全部**已选节点（目标节点 +
   全部保留节点），否则无法反查其属性重建 `Visit`。生产调用方应传入"用与
   原规划相同的 intent 重新召回"的结果（含 grounding 过滤），这天然覆盖
   仍然存在于目录里的已选实体；若某已选实体因外部数据变化（如下架）从
   召回结果里消失，这是候选池陈旧的调用方问题，不是本模块要兜底的场景。

【降级序列 + kind 永不跨】

候选池先按目标节点的 `target_kind` 过滤（poi 只换 poi、restaurant 只换
restaurant——大类永不跨，跨大类是结构变更，归全局反馈通道，ADR-0013 决策 2
明文）。"子类" = `activity_pool.poi_category`/`restaurant_category` 既有口径
（`Poi.type`/`Restaurant.cuisine`，D-1 判断点 4 的既有理由：精确匹配、无需
调参、命中 mock 目录真实"同款扎堆"）。三级降级：

1. **同子类满足**：候选与目标节点同子类，且满足 `adjustment`（若有）。
2. **同大类异子类满足**：放宽子类，只要求满足 `adjustment`（若有）——是
   tier 1 的超集，tier 1 打不出候选/全时间不可行时才轮到这里。
3. **近似满足+告知**：连 `adjustment` 谓词也放宽（同 kind 内任意候选）——
   只有 `adjustment is not None` 时这一级才与 tier 2 有区别（无方向的
   "点踩换菜"场景下 tier 2 已是全量同 kind 候选，tier 3 恒等，直接跳过，
   不做无意义的重复求解）。命中 tier 3 产 `SWAP_DEGRADED` advisory。

`ledger_slice`（生效中诉求，F-2 消费接口——见下方"消费接口"节）在每一级
内部再分一次优先级：先试"同时满足 ledger 全部诉求"的子集，找不到可行替补
才退回该级的完整候选集——**ledger 是软偏置，不是硬门槛**，不会导致整体
降级失败（与 F-2 "节点在场=硬约束，节点没了=尽量满足+告知"的既定语义一致：
本模块只在"挑哪个候选"这一步体现 ledger 偏好，不因 ledger 未被满足而拒绝
交付一个换菜结果）。

【ledger_slice 消费接口（本模块拍板，F-2 按此实现存储）】

`ledger_slice: Sequence[NodeAdjustment] = ()`——诉求的核心可满足载荷复用
`schemas.node_adjustment.NodeAdjustment`（dimension + value），与调整按钮/
点击换菜的载荷同一形状，不新造平行结构。F-2 的"谁 · 针对哪个节点 · 全局/
局部语义 · 生效状态 · 指回来源轮次"是外层信封——**调用方**（F-2 落地后的
图节点/房间处理器）负责按"当前节点在场""生效未被顶替"过滤出这个切片再传
进来；本模块不关心信封字段，也不解决"新点的 `adjustment` 与 `ledger_slice`
里同维度旧诉求冲突"这类顶替判定（F-2 的"同节点同维度后者顶替前者"规则下，
真正生效的切片理应已经去重，本模块假定传入的就是当下真正该考虑的那些）。

【advisory：新增 3 码（`schemas/advisory.py`）】

- `SWAP_DEGRADED`：命中 tier 3，给了近似最接近的候选，未必满足 `adjustment`。
- `SWAP_KEPT_NODE_UNFIT`：钉住不动的其余节点在去掉目标后本身就排不到一块儿
  （如中间站被抽走后两端直达通勤暴涨，`schedule_route(kept)` 返回
  None）——复用 D-7 `PINNED_UNSATISFIABLE` 的"绝不静默、如实告知"先例语义。
  方案保持原样未变（`success=False`）。
- `SWAP_NO_ALTERNATIVE_FOUND`：三级降级全部试完，同 kind 候选池里没有一个能
  塞进现有时间/路线（如全部时间不可行）——这一格彻底换不了，方案保持原样
  未变（`success=False`）。这一码是本步在 ADR 给出的两个建议码之外新增的
  第三个：ADR 只举了两个例子（原文"如"），但"三级降级仍然一个都插不进去"
  是真实可达的边界（`repair_route` 对空/全不可行候选池的既有语义就是"不
  强凑"），不处理会让调用方拿到一个语义不明的 `success=False` 却猜不出
  原因——按"绝不默默忽略"一贯纪律补齐，非节外生枝。

【ADR-0014 横向深审 P0：候选池 hard 恒定过滤——引擎自己守不变量】

深审实锤：本模块曾经零处过滤 hard 约束（`intent.dietary_constraints`/
`physical_constraints` 里 `schemas.tags.is_hard_tag` 判定的那个安全子集）——
`_degrade_tiers` 的降级序列只按"同子类/adjustment 满足"分级，从不检查候选
是否本身就违反用户明确表达过的一票否决底线（"不吃辣"）。两个调用方
（`api/_streams/graph_adjust.py` 单人 SSE / `collab/room.py` 房间 WS）各自
喂给本模块的候选池是否已预先过滤是"调用方记不记得"的偶然属性，不是本模块
可以依赖的不变量——`graph_adjust.py` 直接读 `data.loader.load_pois()/
load_restaurants()`（未过滤的全量目录）；`room.py` 虽然现场重查
`_query_pois`/`_query_restaurants` 走 `search_pois`/`search_restaurants`
Tool（那两个 Tool 内部的 `tools._helpers.relax_tag_search` 确实会做 hard
恒定过滤），但那是**搜索期**的另一层防线，"具名备选"点击后走
`node_swap_support.narrow_pool_to_single_alternative` 收窄的候选未必再过一遍
那层搜索——引擎本体若不自己兜底，两条调用链任何一处"忘了先筛一遍"就是
"用户说不吃辣，点了某站的『更便宜』/点踩/具名备选，结果换进辣店零告知"
的演示翻车。修法（`_filter_hard_violations`）：在 `resolve_node_swap` /
`feasible_alternatives` 内部、`_degrade_tiers` 之前，对喂入的候选池按
kind 恒定过滤——`kind="restaurant"` 用 `intent.dietary_constraints` 的 hard
子集、`kind="poi"` 用 `intent.physical_constraints` 的 hard 子集，命中判定
复用 `tools._helpers.has_all_tags`（与 `critic._rules.checks.check_dietary`/
`check_physical` 的 hard 核验同一套"required ⊆ 候选 tags"原语，不发明第二套
匹配算法）。

**关键实现细节：只过滤"新候选"局部变量，不碰调用方传入的 `pois`/
`restaurants` 原始形参**——`_build_full_scheduled`（模块 docstring「Visit
重建」节）要靠原始未过滤的 `pois`/`restaurants` 反查方案里**当前已在场**的
全部节点实体（前置条件 2），若在这里提前过滤，一个"更早、在没有这条 hard
约束时被合法选中、现在恰好不满足新约束"的已在场节点会从池子里消失，
`_entity_by_id` 反查落空直接 `ValueError`（契约违反级别的假警报，不是本步
想要的行为）。`resolve_node_swap` 里过滤发生在 `candidates = pois if kind ==
"poi" else restaurants` 这一行**之后**（只产出一份新的局部列表喂给
`_degrade_tiers`），`_build_full_scheduled` 仍然读未经改动的 `pois`/
`restaurants` 原始形参——两者各司其职，互不冲突。`narrow_pool_to_single_
alternative` 收窄出的"具名备选候选池"同一时刻既含"当前已在场实体"（覆盖
前置条件 2）又含"这一个新点的备选"，本过滤只会剔除后者（若它恰好违反
hard），不会动前者——即便这次点选的具名备选真的违反 hard，收窄后只剩它
一个候选，过滤后候选池归零，最终降级到 `SWAP_NO_ALTERNATIVE_FOUND` 业务性
失败，不是 `ValueError` 契约违反，语义上是对的（"你点的这个不安全，没法换"
是业务结果，不是程序错误）。`feasible_alternatives` 走同一份 `_filter_hard_
violations`，理由对称：展示给用户的"具名备选"列表如果混进 hard 违规候选，
用户点了会被拒绝，是同一个"预验证却不可信"的翻车。

【ADR-0014 横向深审 P0：换后单点审计——新换入节点也要过一遍 soft 审计】

`agent.planning.critic.exit_audit.audit_constraint_relaxation` 是方案**定稿**
时对**整条**最终 itinerary 做的 soft 约束满足度审计（hard 由 critic gate 保证，
不劳它管）；但换菜走 HTTP/WS 旁路，压根不经过 `finalize_plan_node`，新换入
的节点即使 soft 未满足也不会被那次全局审计捕捉到（它审计的是"上一次定稿"
那份旧 itinerary）。修法：换菜成功分支对**新换入的这一个节点**单独跑一次
轻量审计——`_audit_swapped_node` 复用 `exit_audit` 现有的比对/措辞原语
（`_unmet_soft_tags`/`_render_message`/`_PROVENANCE_CLAUSE`，只读不改，
消息措辞与出处口径与"定稿审计"完全一致），产出同一枚 `AdvisoryCode.
CONSTRAINT_RELAXED`，走换菜既有的 `advisories` 通道（narration 已消费，见
`api/_streams/graph_adjust.py`/`collab/room.py` 现有拼句逻辑，不需要新增
消费端）。

**审计范围刻意收窄到"这一个节点自己的 tags"，不重新审计整条方案、也不查
`experience_tags`**：`exit_audit`（ADR-0014 横向深审 P2 补丁后）对
`dietary_constraints`/`physical_constraints` 已经是**逐节点独立核验**语义
（与 `check_dietary`/`check_physical` 的 hard 核验同一粒度，见该模块
docstring「逐节点判定」节）——`_audit_swapped_node` 只是把这套"逐节点"
核验收窄成"只核验这次真正变了的那一个节点"，是同一套语义的正确投影，
不是近似。`experience_tags` 仍维持"整段行程任一节点覆盖即算达成"的并集
语义（同一理由：该体验诉求可以由方案里**另一个**没被这次换菜动过的节点
贡献，只看新节点会产生假阳性）——因此本函数不查 `experience_tags`，交给
定稿时的整段审计处理，不在这里重复/误判。措辞对齐：`_render_message` 现在
要求 `unmet_stations` 形参点名哪几站没对上（同 P2 补丁的人话格式），
`_audit_swapped_node` 传入`[humanize_node(该节点在新 itinerary 里的 idx,
该节点)]`——单节点审计天然只有一个站名，格式仍与"定稿审计"完全对齐。

【追加：`feasible_alternatives` 排除方案内已在场实体】

冒烟实测炸出的真实 bug（非推测）：`feasible_alternatives` 用 `try_insert`
直接预验证候选是否能插回目标节点腾出的槽位，但 `try_insert` 不检查"这个
候选是不是已经在方案的**另一个**节点上占着"；而真正执行换菜的
`resolve_node_swap` 走 `route_builder.repair_route`，其内部 `kept_keys =
{(v.kind, v.target_id) for v in kept}` 会自动把"已在 kept 集合里的实体"从
候选池剔除（同一个实体不能同时是"保留"又是"新插入"）。两条路径由此对
"方案里另一个节点正在用的实体能不能当这个节点的候选"给出了不一致的答案——
`feasible_alternatives` 会把它当"可行备选"展示，用户点了之后
`narrow_pool_to_single_alternative` 收窄+ `resolve_node_swap` 执行却发现
"新候选"其实是空的（唯一候选已经在 kept 里），以 `SWAP_NO_ALTERNATIVE_FOUND`
业务性失败拒绝——违反 ADR-0013"预验证可行才展示，不拿未验证的备选充数"的
字面承诺。修法：`feasible_alternatives` 额外排除"当前 itinerary 全部非
home 节点的 target_id"（覆盖目标节点自身与其余全部保留节点），与 `resolve_
node_swap`/`repair_route` 的"已在场实体不可能同时又是新候选"这条不变量
对齐，不需要改 `resolve_node_swap`/`repair_route` 本身（那条路径已经对，
问题只出在 `feasible_alternatives` 自己的预验证没有复刻这条排除规则）。

不负责：
- 按钮生成 / 前端 payload 形状（F-3/F-4）。
- 诉求台账的记账存储与生效状态机（F-2）。
- 跨大类结构变更（归全局反馈通道，走既有 `plan_hybrid` 重规划路径）。
- 房间并发/串行队列（F-5）——本模块是纯函数，不处理"同一节点被连点两次"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Union

from schemas.advisory import Advisory, AdvisoryCode
from schemas.domain import Poi, Restaurant, UserProfile
from schemas.intent import IntentExtraction
from schemas.itinerary import ActivityNode, Itinerary
from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension
from schemas.tags import is_hard_tag

from data.loader import load_user_profile
from tools._helpers import has_all_tags

from ..blueprint.assemble_blueprint import assemble_from_blueprint
from ..critic._rules.helpers import humanize_node, parse_hhmm
from ..critic.exit_audit import _PROVENANCE_CLAUSE, _render_message, _unmet_soft_tags
from ..weights_llm import PlanningWeights, get_planning_weights
from .activity_pool import (
    Visit,
    build_visit_from_poi,
    build_visit_from_restaurant,
    poi_category,
    restaurant_category,
)
from .pace_budget import interval_fill_targets, pace
from .route_builder import make_commute_fn, repair_route, route_to_blueprint
from .route_scheduler import CommuteFn, RouteSchedule, ScheduledVisit, schedule_route, try_insert

Entity = Union[Poi, Restaurant]


# ============================================================
# 结果值对象
# ============================================================


@dataclass
class SwapResult:
    """`resolve_node_swap` 的产出（不是公共 API 之外的 wire 形状——F-4 决定
    怎么包成 SSE/HTTP payload）。

    `success=False` 时 `new_itinerary` 恒等于调用方传入的原 `itinerary`
    （未做任何改动，语义与 `HybridResult` 失败分支"账单作废"一致）；
    `success=True` 时 `new_itinerary` 是经 `repair_route` + 组装管线产出的
    新方案，`swapped_to` 是新换入实体的 `target_id`。`degrade_tier`
    （1/2/3）只在 `success=True` 时有意义，供测试/诊断读取命中了哪一级。
    """

    success: bool
    new_itinerary: Itinerary
    advisories: list[Advisory]
    swapped_to: Optional[str] = None
    degrade_tier: Optional[int] = None


@dataclass(frozen=True)
class AlternativeOption:
    """`feasible_alternatives` 的一项——具名备选的展示要素。"""

    kind: str
    target_id: str
    name: str
    rating: float
    distance_km: float
    price: float
    category: str


# ============================================================
# 定向调整满足谓词（维度表见 `schemas.node_adjustment` 模块 docstring）
# ============================================================


def _unit_price(kind: str, entity: Entity) -> float:
    if kind == "poi":
        price_range = getattr(entity, "price_range", None)
        return float(price_range[0]) if price_range else 0.0
    return float(entity.avg_price)


def _category_of(kind: str, entity: Entity) -> str:
    return poi_category(entity) if kind == "poi" else restaurant_category(entity)


def _adjustment_satisfied(adjustment: NodeAdjustment, kind: str, candidate: Entity, original: Entity) -> bool:
    """候选实体是否满足一条定向调整——维度→字段→谓词的具体实现（表见
    `schemas.node_adjustment` 模块 docstring，本函数只是把表格翻成代码）。
    """
    dim = adjustment.dimension
    if dim == NodeAdjustmentDimension.PRICE:
        cand_price, orig_price = _unit_price(kind, candidate), _unit_price(kind, original)
        return cand_price < orig_price if adjustment.value == "cheaper" else cand_price > orig_price
    if dim == NodeAdjustmentDimension.DISTANCE:
        cand_dist, orig_dist = candidate.distance_km, original.distance_km
        return cand_dist < orig_dist if adjustment.value == "closer" else cand_dist > orig_dist
    if dim == NodeAdjustmentDimension.CUISINE_OR_TYPE:
        return _category_of(kind, candidate) == adjustment.value
    # DIETARY / AMBIENCE / CROWD_FIT：统一查受控 tag 词典是否命中候选.tags
    return adjustment.value in getattr(candidate, "tags", [])


# ============================================================
# 方案 → Visit 反查重建
# ============================================================


def _find_target_node(itinerary: Itinerary, target_node_id: str) -> ActivityNode:
    for node in itinerary.nodes:
        if node.target_kind != "home" and node.target_id == target_node_id:
            return node
    raise ValueError(
        f"target_node_id={target_node_id!r} 在 itinerary.nodes 里找不到匹配的非 home "
        "节点——调用方契约：目标节点必须真实存在于当前方案（见模块 docstring「前置条件」）"
    )


def _entity_by_id(kind: str, target_id: str, pois: Sequence[Poi], restaurants: Sequence[Restaurant]) -> Optional[Entity]:
    pool = pois if kind == "poi" else restaurants
    return next((e for e in pool if e.id == target_id), None)


def _build_visit(kind: str, entity: Entity, intent: IntentExtraction, weights: PlanningWeights) -> Visit:
    """免 LLM 重建（不传 semantic_scores，见模块 docstring「Visit 重建」的已知取舍）。"""
    if kind == "poi":
        return build_visit_from_poi(entity, intent, weights)
    return build_visit_from_restaurant(entity, intent, weights)


def _build_full_scheduled(
    itinerary: Itinerary,
    pois: Sequence[Poi],
    restaurants: Sequence[Restaurant],
    intent: IntentExtraction,
    weights: PlanningWeights,
) -> list[ScheduledVisit]:
    """把方案里**全部**非 home 节点（含目标节点）反查重建成 `ScheduledVisit`
    （`repair_route` 的 `previous_scheduled` 形参只读其 `.visit` 属性，
    `start_min`/`natural_arrival_min` 在这里恒为占位值，不参与任何判定）。

    **关键：不在这一步排除目标节点**——`repair_route` 自己的黑名单机制
    （`blacklist_poi`/`blacklist_rest={target_id}`）要靠"目标节点确实出现在
    `previous_scheduled` 里"才能命中并 `removed_kinds.append(kind)`，从而
    触发补位搜索；若在此处提前排除，`repair_route` 内部循环永远不会看到
    目标节点、`removed_kinds` 恒为空，会在"if not removed_kinds: return
    schedule"直接短路返回——**替补搜索根本不会被尝试**（曾是本模块早期草稿
    的一个真实 bug，写测试前已自查修正）。目标节点的排除交给 `repair_route`
    的黑名单参数完成，与 `ils_planner.plan_hybrid` 的既有用法一致。

    重复 id 保护：若方案里出现两个节点共享同一 `target_id`（理论上不该发生，
    ADR-0013"不会歧义"的前提），`repair_route` 的黑名单按 id 匹配——两个都
    会被一并清出（已知限制，不是本函数的职责去侦测/报错这种上游异常方案）。
    """
    out: list[ScheduledVisit] = []
    for node in itinerary.nodes:
        if node.target_kind == "home":
            continue
        entity = _entity_by_id(node.target_kind, node.target_id, pois, restaurants)
        if entity is None:
            raise ValueError(
                f"节点 {node.target_kind}:{node.target_id} 在候选池里找不到对应实体——"
                "resolve_node_swap 要求候选池覆盖当前方案里全部已选节点（见模块 docstring"
                "「前置条件」2），否则无法反查其属性重建 Visit"
            )
        visit = _build_visit(node.target_kind, entity, intent, weights)
        out.append(ScheduledVisit(visit=visit, start_min=0, natural_arrival_min=0))
    return out


# ============================================================
# 候选池 hard 恒定过滤（ADR-0014 横向深审 P0 修法 1；见模块 docstring）
# ============================================================


def _hard_required_tags(kind: str, intent: IntentExtraction) -> list[str]:
    """kind 对应受控词典里的 hard 子集——餐厅↔dietary_constraints，
    poi↔physical_constraints（与 `check_dietary`/`check_physical` 的 hard 核验
    读同一个字段，不发明第三个"这个 kind 该看哪个字段"的映射）。"""
    source = intent.dietary_constraints if kind == "restaurant" else intent.physical_constraints
    return [t for t in source if is_hard_tag(t)]


def _filter_hard_violations(kind: str, entities: Sequence[Entity], intent: IntentExtraction) -> list[Entity]:
    """候选池 hard 恒定过滤——只产出一份新的局部列表，不改调用方传入的
    `pois`/`restaurants` 原始序列本身（模块 docstring「关键实现细节」节解释
    了为什么不能改原始形参：`_build_full_scheduled` 反查当前已在场节点要靠
    未过滤的原始池子）。hard 子集为空（用户没有一票否决类约束）→ 原样返回，
    不做任何过滤，这是最常见的路径，避免无意义的列表拷贝判定开销。"""
    hard_required = _hard_required_tags(kind, intent)
    if not hard_required:
        return list(entities)
    return [e for e in entities if has_all_tags(getattr(e, "tags", None) or [], hard_required)]


# ============================================================
# 降级序列（tier 候选集合）
# ============================================================


def _degrade_tiers(
    kind: str,
    candidates: Sequence[Entity],
    target_id: str,
    target_entity: Entity,
    adjustment: Optional[NodeAdjustment],
) -> list[list[Entity]]:
    """三级降级候选集合（见模块 docstring「降级序列」）。

    `adjustment is None`（无方向换/点踩）时 tier 2 已是"同 kind 全量候选"，
    tier 3 与之恒等——不产出 tier 3，调用方按"只有 2 级"处理，省一次重复
    的 `repair_route` 调用（也让 `degrade_tier` 诊断值语义不含糊：无方向换
    最多只报 1/2）。
    """
    others = [e for e in candidates if e.id != target_id]
    original_category = _category_of(kind, target_entity)

    def satisfies(e: Entity) -> bool:
        return adjustment is None or _adjustment_satisfied(adjustment, kind, e, target_entity)

    tier1 = [e for e in others if _category_of(kind, e) == original_category and satisfies(e)]
    tier2 = [e for e in others if satisfies(e)]
    if adjustment is None:
        return [tier1, tier2]
    tier3 = others
    return [tier1, tier2, tier3]


def _ledger_priority_groups(
    kind: str,
    entities: list[Entity],
    target_entity: Entity,
    ledger_slice: Sequence[NodeAdjustment],
) -> list[list[Entity]]:
    """把一级候选按「是否同时满足全部生效诉求」拆成优先级两组（ledger 是软
    偏置，不是硬门槛——见模块 docstring「降级序列」尾段）。

    偏好组非空且是真子集才拆两组试；否则只试一组（避免对"偏好组==全量"或
    "偏好组为空"这两种平凡情形做两次重复的 `repair_route` 调用）。
    """
    if not ledger_slice or not entities:
        return [entities]

    def ledger_ok(e: Entity) -> bool:
        return all(_adjustment_satisfied(d, kind, e, target_entity) for d in ledger_slice)

    preferred = [e for e in entities if ledger_ok(e)]
    if preferred and len(preferred) < len(entities):
        return [preferred, entities]
    return [entities]


def _attempt_pool(
    kind: str,
    entities: list[Entity],
    full_scheduled: list[ScheduledVisit],
    intent: IntentExtraction,
    weights: PlanningWeights,
    *,
    depart_min: int,
    budget_min: int,
    commute_fn: CommuteFn,
    money_budget: float,
    target_id: str,
) -> Optional[RouteSchedule]:
    """用给定候选子集尝试补回目标节点的槽位（`repair_route` 的一次调用）。

    `full_scheduled`：**含目标节点**的完整排程（`repair_route` 靠
    `blacklist_poi`/`blacklist_rest` 自己识别并清出目标节点，见
    `_build_full_scheduled` docstring——不能传已经排除过目标的列表，否则
    `removed_kinds` 恒为空，`repair_route` 会在补位搜索之前就短路返回）。

    `target_id` 是本轮唯一黑名单条目——局部重解永远是"换整个实体"，不是
    "同一实体挪时段"（那是 ILS 修复闭环自己的用法），故 `blacklist_rest_time`
    恒传空集。
    """
    if not entities:
        return None
    visits = [_build_visit(kind, e, intent, weights) for e in entities]
    return repair_route(
        full_scheduled,
        visits if kind == "poi" else [],
        visits if kind == "restaurant" else [],
        weights,
        depart_min=depart_min,
        budget_min=budget_min,
        commute_fn=commute_fn,
        money_budget=money_budget,
        blacklist_poi={target_id} if kind == "poi" else set(),
        blacklist_rest={target_id} if kind == "restaurant" else set(),
        blacklist_rest_time=set(),
    )


# ============================================================
# advisory 构造
# ============================================================


def _swap_degraded_advisory(new_name: str) -> Advisory:
    return Advisory(
        code=AdvisoryCode.SWAP_DEGRADED,
        message=(
            f"没找到完全符合你要求的，给你换了个最接近的——『{new_name}』，"
            "先将就一下，不满意再告诉我？"
        ),
    )


def _kept_node_unfit_advisory() -> Advisory:
    return Advisory(
        code=AdvisoryCode.SWAP_KEPT_NODE_UNFIT,
        message=(
            "换掉这一站之后，你原来留着的其他安排在时间上凑不到一块儿了"
            "（大概率是绕路变远了），没法只动这一格——要不我把别的站也一起挪一挪？"
        ),
    )


def _no_alternative_advisory() -> Advisory:
    return Advisory(
        code=AdvisoryCode.SWAP_NO_ALTERNATIVE_FOUND,
        message="这一类里翻遍了候选也没找到能塞进现有时间和路线的替代，这一站暂时换不了。",
    )


def _audit_swapped_node(
    kind: str, entity: Entity, intent: IntentExtraction, node: ActivityNode, node_idx: int
) -> list[Advisory]:
    """换后单点审计（ADR-0014 横向深审 P0 修法 2；见模块 docstring「换后单点
    审计」节）——只审新换入的这一个节点自己的 tags，不重跑整条方案的
    `exit_audit.audit_constraint_relaxation`，也不查 `experience_tags`（理由
    见模块 docstring）。复用 `exit_audit` 现成的比对/措辞原语，只读不改；
    `node`/`node_idx` 只用于拼 `humanize_node` 站名给 `_render_message` 的
    `unmet_stations` 形参（P2 补丁后该形参必填，单节点审计天然只有一站）。
    """
    field = "dietary_constraints" if kind == "restaurant" else "physical_constraints"
    required = list(getattr(intent, field))
    unmet = _unmet_soft_tags(required, set(getattr(entity, "tags", None) or []))
    if not unmet:
        return []
    station = humanize_node(node_idx, node)
    provenance_map = intent.field_provenance or {}
    advisories: list[Advisory] = []
    for tag in unmet:
        provenance = provenance_map.get(f"{field}:{tag}")
        if provenance not in _PROVENANCE_CLAUSE:
            continue  # default / 无出处数据 → 不打扰，同 exit_audit 口径
        advisories.append(
            Advisory(code=AdvisoryCode.CONSTRAINT_RELAXED, message=_render_message(tag, provenance, [station]))
        )
    return advisories


# ============================================================
# 公开接口
# ============================================================


def _resolve_common(
    itinerary: Itinerary,
    intent: IntentExtraction,
    pois: Sequence[Poi],
    restaurants: Sequence[Restaurant],
    target_node_id: str,
    user_profile: Optional[UserProfile],
    weights: Optional[PlanningWeights],
):
    """两个公开函数共享的准备步骤——同一真相源（返回值供调用方各自续接）。"""
    target_node = _find_target_node(itinerary, target_node_id)
    kind = target_node.target_kind
    target_entity = _entity_by_id(kind, target_node_id, pois, restaurants)
    if target_entity is None:
        raise ValueError(
            f"目标节点 {kind}:{target_node_id} 本身在候选池里找不到对应实体——"
            "无法反查其价格/子类等属性做降级判定（见模块 docstring「前置条件」2）"
        )

    user_profile = user_profile or load_user_profile()
    weights = weights or get_planning_weights(intent, client=None)
    commute_fn = make_commute_fn(user_profile)
    depart_min = parse_hhmm(itinerary.nodes[0].start_time)
    if depart_min is None:  # 防御性：home 起点 start_time 恒由 assemble 产出合法 HH:MM
        depart_min = 0
    budget_min = interval_fill_targets(intent, pace(intent)).hi_min

    # 含目标节点本身（`repair_route` 的黑名单机制要靠它在场才能命中并触发补位
    # 搜索，见 `_build_full_scheduled` docstring）。
    full_scheduled = _build_full_scheduled(itinerary, pois, restaurants, intent, weights)
    return target_node, kind, target_entity, user_profile, weights, commute_fn, depart_min, budget_min, full_scheduled


def resolve_node_swap(
    itinerary: Itinerary,
    intent: IntentExtraction,
    pois: Sequence[Poi],
    restaurants: Sequence[Restaurant],
    target_node_id: str,
    adjustment: Optional[NodeAdjustment] = None,
    *,
    ledger_slice: Sequence[NodeAdjustment] = (),
    user_profile: Optional[UserProfile] = None,
    weights: Optional[PlanningWeights] = None,
) -> SwapResult:
    """局部重解：钉住其余节点 + 拉黑目标 + 按降级序列在缺口重解（模块 docstring
    有完整机制说明）。`adjustment=None` 时是「点踩，无方向换」。

    Returns:
        `SwapResult`。找不到任何替代 / 保留节点排不到一块儿 → `success=False`，
        `new_itinerary` 原样返回，`advisories` 说明原因（见模块 docstring
        「advisory」节）；否则 `success=True`，`new_itinerary` 是新方案，
        `swapped_to` 是新换入的 `target_id`。
    """
    (
        target_node,
        kind,
        target_entity,
        user_profile,
        weights,
        commute_fn,
        depart_min,
        budget_min,
        full_scheduled,
    ) = _resolve_common(itinerary, intent, pois, restaurants, target_node_id, user_profile, weights)
    money_budget = user_profile.default_budget

    kept_visits_only = [sv.visit for sv in full_scheduled if sv.visit.target_id != target_node_id]
    if schedule_route(kept_visits_only, depart_min=depart_min, budget_min=budget_min, commute_fn=commute_fn) is None:
        return SwapResult(
            success=False,
            new_itinerary=itinerary,
            advisories=[_kept_node_unfit_advisory()],
        )

    original_ids_of_kind = {v.target_id for v in kept_visits_only if v.kind == kind}
    # ADR-0014 横向深审 P0 修法 1：候选池 hard 恒定过滤——只产出新的局部列表，
    # 不改 pois/restaurants 原始形参本身（`_build_full_scheduled` 已经用过
    # 未过滤的原始池子完成了反查，这里往后只影响"新候选"，见模块 docstring）。
    candidates = _filter_hard_violations(kind, pois if kind == "poi" else restaurants, intent)
    tiers = _degrade_tiers(kind, candidates, target_node_id, target_entity, adjustment)

    for tier_index, tier_entities in enumerate(tiers, start=1):
        for group in _ledger_priority_groups(kind, tier_entities, target_entity, ledger_slice):
            new_schedule = _attempt_pool(
                kind,
                group,
                full_scheduled,
                intent,
                weights,
                depart_min=depart_min,
                budget_min=budget_min,
                commute_fn=commute_fn,
                money_budget=money_budget,
                target_id=target_node_id,
            )
            if new_schedule is None or len(new_schedule.scheduled) != len(full_scheduled):
                continue  # 这个候选子集没找到能插回的替补，试下一组/下一级

            swapped_visit = next(
                sv.visit
                for sv in new_schedule.scheduled
                if sv.visit.kind == kind and sv.visit.target_id not in original_ids_of_kind
            )
            blueprint = route_to_blueprint(new_schedule, intent, depart_min)
            new_itinerary = assemble_from_blueprint(intent, blueprint, user_profile)

            new_entity = _entity_by_id(kind, swapped_visit.target_id, pois, restaurants)
            advisories: list[Advisory] = []
            if tier_index == 3 and adjustment is not None:
                advisories.append(_swap_degraded_advisory(new_entity.name if new_entity else swapped_visit.target_id))
            # ADR-0014 横向深审 P0 修法 2：换后单点审计（见模块 docstring）——
            # 无论命中哪一级 tier，新换入的这一个节点都要过一遍 soft 审计。
            new_node_match = next(
                (
                    (idx, node)
                    for idx, node in enumerate(new_itinerary.nodes)
                    if node.target_kind == kind and node.target_id == swapped_visit.target_id
                ),
                None,
            )
            if new_entity is not None and new_node_match is not None:
                new_node_idx, new_node = new_node_match
                advisories.extend(_audit_swapped_node(kind, new_entity, intent, new_node, new_node_idx))

            return SwapResult(
                success=True,
                new_itinerary=new_itinerary,
                advisories=advisories,
                swapped_to=swapped_visit.target_id,
                degrade_tier=tier_index,
            )

    return SwapResult(
        success=False,
        new_itinerary=itinerary,
        advisories=[_no_alternative_advisory()],
    )


def feasible_alternatives(
    itinerary: Itinerary,
    intent: IntentExtraction,
    pois: Sequence[Poi],
    restaurants: Sequence[Restaurant],
    target_node_id: str,
    *,
    k: int = 3,
    user_profile: Optional[UserProfile] = None,
    weights: Optional[PlanningWeights] = None,
) -> list[AlternativeOption]:
    """预验证具名备选（右侧"换成 XX 店"列表）——与 `resolve_node_swap` 同一
    真相源：同一降级序列排优先级、同一 `try_insert` 判可行，只是不提交
    （不落 `repair_route`/不组装新 Itinerary），逐候选试插到凑够 `k` 个或
    候选耗尽为止。

    无 `adjustment`/`ledger_slice` 形参——这是"这个节点还能换成什么"的通用
    预览，不针对某个定向调整；三级降级里 tier 3 在 `adjustment=None` 时恒
    等于 tier 2（`_degrade_tiers` 已内部省略），故只消费 tier 1/2。

    Returns:
        最多 `k` 个 `AlternativeOption`，同子类优先、其内按 `base_score`
        降序；`try_insert` 不通过的候选不出现在结果里（"预验证"的字面含义）。
        保留节点本身排不到一块儿 → 返回空列表（没有可预览的备选）。违反
        `intent` hard 约束的候选、或已经是方案里**另一个**节点在用的实体，
        恒不出现在结果里（ADR-0014 横向深审 P0，见模块 docstring）。
    """
    (
        _target_node,
        kind,
        target_entity,
        _user_profile,
        weights,
        commute_fn,
        depart_min,
        budget_min,
        full_scheduled,
    ) = _resolve_common(itinerary, intent, pois, restaurants, target_node_id, user_profile, weights)

    kept_visits = [sv.visit for sv in full_scheduled if sv.visit.target_id != target_node_id]
    if schedule_route(kept_visits, depart_min=depart_min, budget_min=budget_min, commute_fn=commute_fn) is None:
        return []

    # ADR-0014 横向深审 P0：hard 恒定过滤（修法 1）+ 排除方案内已在场实体
    # （追加发现，见模块 docstring「追加」节）——两条都发生在 _degrade_tiers
    # 之前，只影响这份局部 candidates 列表，不碰 pois/restaurants 原始形参。
    candidates = _filter_hard_violations(kind, pois if kind == "poi" else restaurants, intent)
    in_itinerary_ids = {n.target_id for n in itinerary.nodes if n.target_kind != "home"}
    candidates = [e for e in candidates if e.id not in in_itinerary_ids]
    tiers = _degrade_tiers(kind, candidates, target_node_id, target_entity, adjustment=None)
    same_category_ids = {e.id for e in tiers[0]}
    all_candidates = tiers[-1]  # 无 adjustment 时 tiers[-1]（tier2）已是全量同 kind 候选

    visits_by_id = {e.id: _build_visit(kind, e, intent, weights) for e in all_candidates}
    ranked = sorted(
        all_candidates,
        key=lambda e: (0 if e.id in same_category_ids else 1, -visits_by_id[e.id].base_score),
    )

    results: list[AlternativeOption] = []
    for e in ranked:
        if len(results) >= k:
            break
        candidate_schedule = try_insert(
            kept_visits, visits_by_id[e.id], depart_min=depart_min, budget_min=budget_min, commute_fn=commute_fn
        )
        if candidate_schedule is None:
            continue
        results.append(_to_alternative_option(kind, e))
    return results


def _to_alternative_option(kind: str, entity: Entity) -> AlternativeOption:
    if kind == "poi":
        price = float(entity.price_range[0]) if getattr(entity, "price_range", None) else 0.0
        category = entity.type
    else:
        price = float(entity.avg_price)
        category = entity.cuisine
    return AlternativeOption(
        kind=kind,
        target_id=entity.id,
        name=entity.name,
        rating=entity.rating,
        distance_km=entity.distance_km,
        price=price,
        category=category,
    )


__all__ = [
    "SwapResult",
    "AlternativeOption",
    "resolve_node_swap",
    "feasible_alternatives",
]
