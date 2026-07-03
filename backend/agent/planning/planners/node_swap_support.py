"""agent.planning.planners.node_swap_support —— 单节点调整编排的跨层共享助手
（ADR-0013 F-4/F-5，落地状态「已知留痕」记的债的搬迁终点）。

【这是什么问题】

`resolve_node_swap`（`node_swap.py`，F-1 局部重解引擎）本身是纯函数，只管
"钉住其余节点、在腾出来的这一格里按降级序列重解"；但把它接成用户可点的
一环——定位目标节点、把 `NodeAdjustment` 翻成人话、把"具名备选"收窄成
"点这个就该换成这个"、判断确认后是否封禁换菜——这层编排逻辑，F-4（单人
`/chat/adjust` SSE 流，`api/_streams/graph_adjust.py`）和 F-5（房间 WS
`adjust`，`collab/room.py::RoomManager._resolve_and_broadcast_adjust`）两个
调用方各自都要一份完全相同的实现。

F-3/F-4 落地时图省事把这批 helper 写成了 `api/_streams/graph_adjust.py`
的模块私有函数（下划线前缀）；F-5 房间侧接线时要用同一批逻辑，图快从
`room.py` 函数体内 `from api._streams.graph_adjust import _xxx` 硬拉——这是
collab 层反向依赖 api 层私有实现细节，层次拧了（ADR-0013 落地状态"已知
留痕"明记此债："graph_adjust 私有 helper 被 room.py 跨模块 import，待抽
中立 seam"）。本模块就是这条 seam。

【安家处选型：为什么是 planners/ 而不是新开 agent/ 顶层模块】

约束：api 层与 collab 层都要能 import 它；它自己只向下依赖
`schemas`（`Itinerary`/`NodeAdjustment`/`Poi`/`Restaurant`），不得反向
import `api`/`collab`。两个候选安家处都能满足这条约束，选 `planners/`
的理由：

1. **概念紧邻**——本模块的产出几乎总是与 `resolve_node_swap` 联用（两个
   调用方拿这批 helper 整理完入参/展示完结果后，下一步几乎都是调
   `node_swap.resolve_node_swap`）。放在 `node_swap.py` 隔壁而不是散落在
   `agent/` 顶层，"这是 node_swap 的调用编排"读代码时一眼可辨。
2. **不新增目录层级**——本模块函数全部只处理 `Itinerary`/`NodeAdjustment`
   这类规划域对象，不是通用工具；`agent/` 顶层新开一个薄模块（如
   `agent/node_adjust.py`）会让"这是谁的编排"脱离 `planning` 语境，
   `planners/` 包内平级更贴合现有惯例（`route_builder.py`/`activity_pool.py`
   等同样是"给 `node_swap.py`/`ils_planner.py` 类调用方复用的构件"）。

【这是什么问题（本质边界）：既非 SSE 殊相，也非引擎本体】

这批函数的本质既不是"SSE 流殊相"（不碰 `emit`/`SseEvent`/HTTP 4xx，那些
留在 `api/_streams/graph_adjust.py`），也不是"局部重解引擎本身"（不碰
`repair_route`/降级序列/候选打分，那些在 `node_swap.py`）——是介于两者之间
的**单节点调整编排**：给定 itinerary + 调整意图，做节点定位/候选池整理/
文案翻译，产出可以直接喂给 `resolve_node_swap` 或直接展示给用户的中间
结果。判据（供未来新增 helper 时对照）：房间侧今天用到、或明天房间侧照理
也该用的，属于这层；`emit`/SSE 事件拼装/HTTP 4xx 殊相，留在各自调用方。

【公共 API vs 私有实现细节】

原 `api/_streams/graph_adjust.py` 里这批函数一律下划线私有——当初只有单一
调用方，"私有"准确反映"模块内部实现细节"。现在是两个平级调用方（api 层、
collab 层）共同消费的中立 seam，继续叫私有名字名不副实：跨模块公开导入的
函数不该顶着"不要在模块外使用"的下划线记号。本模块因此把这批函数改回不带
下划线的公共名字，收进 `__all__`——比照 `node_swap.py` 自己的既有分层：
`resolve_node_swap`/`feasible_alternatives` 是真正的跨模块公共 API，不加
下划线且收进 `__all__`；`_find_target_node`/`_entity_by_id` 仅供模块内部
复用，保留下划线且不导出。本模块同一纪律：`_DIMENSION_ZH`/`_AMBIENCE_ZH`
两张映射表仅供模块内 `synthesize_source_text`/`adjustment_descriptor` 各自
使用，继续私有；其余全部是两个调用方要导入的公共 API。

不负责：
- 局部重解算法本体（降级序列/候选打分/`repair_route` 调用）——`node_swap.py`。
- SSE 事件拼装 / HTTP 4xx 分层——`api/_streams/graph_adjust.py`。
- 房间归名文案（"按{nickname}的要求"）/单人文案（"按你的要求"）的顶层拼句——
  两个调用方的语气天然不同（房间必须点名是谁提的，单人含糊成"你"即可），
  各自的顶层 `_build_success_narration`（graph_adjust.py）/
  `_build_room_narration`（room.py）留在各自调用方，只共享它们都要用的
  `adjustment_descriptor`/`compose_narration_text` 这两块更细粒度的构件。
"""

from __future__ import annotations

from typing import Optional, Union

from schemas.domain import Poi, Restaurant
from schemas.itinerary import Itinerary
from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension

Entity = Union[Poi, Restaurant]


# ============================================================
# 确认后调整守门（c′批 任务二；L0 禁令 2「绝不默默让已下单方案与订单脱钩」）
# ============================================================
#
# 病灶：确认下单后（/chat/confirm 成功 → user_decision="confirm" 回写图状态，
# 见 api/_streams/graph_confirm.py::_writeback_graph_state），节点行的调整
# 按钮此前仍会直接调用 resolve_node_swap 换菜——换菜成功、itinerary_ready 照
# 常推送新方案，但 execute_finalize 阶段生成的 orders（预约/门票/加购）仍是
# 换菜前那版方案的，两者从此静默脱钩，用户毫无感知。
#
# 两侧各自在自己的调用点判断信号是否命中，命中后共同引用下面这条文案，避免
# 同一语义在两处各写一份措辞：
# - 单人：`api/_streams/graph_adjust.py::_graph_adjust` 读图状态
#   `user_decision == "confirm"`（`_graph_adjust` 与 `/chat/confirm` 共享
#   同一份 LangGraph checkpoint）。该字段是 ADR-0012 决策 4 的 EPISODE_SCOPED
#   字段——下一次真正的新规划事件（新 intent / refiner 合并反馈）会经
#   `reset_for_new_episode()` 清零，故本守门不需要额外的"何时解锁"逻辑：
#   用户只要说"重新规划"，图状态自然回到未确认态，调整重新放行。
# - 房间：`collab/room.py::RoomManager._resolve_and_broadcast_adjust` 读
#   `Room.confirmed`（房间没有图 checkpoint，用房间自己的确认信号，见该
#   字段 docstring）。
CONFIRMED_ADJUST_BLOCKED_MESSAGE = (
    "方案已经确认下单了，这时候直接换这一站会跟已经下的订单对不上——"
    "想调整的话跟我说「重新规划」，我再帮你出个新方案，确认前都能随便换。"
)


# ============================================================
# 诉求台账 source_text 兜底合成（label 缺省时用；见 AdjustActionAdjust docstring）
# ============================================================

_DIMENSION_ZH: dict[NodeAdjustmentDimension, str] = {
    NodeAdjustmentDimension.PRICE: "价格",
    NodeAdjustmentDimension.DISTANCE: "距离",
    NodeAdjustmentDimension.CUISINE_OR_TYPE: "类型",
    NodeAdjustmentDimension.DIETARY: "口味",
    NodeAdjustmentDimension.AMBIENCE: "氛围",
    NodeAdjustmentDimension.CROWD_FIT: "适配",
}


def synthesize_source_text(adjustment: NodeAdjustment) -> str:
    dim_zh = _DIMENSION_ZH.get(adjustment.dimension, "调整")
    return f"{dim_zh}：{adjustment.value}"


# ============================================================
# 节点定位 + 换菜说明文案（自包含中文句子，同 Advisory.message 纪律）
# ============================================================


def target_kind(itinerary: Itinerary, node_id: str) -> Optional[str]:
    for node in itinerary.nodes:
        if node.target_kind != "home" and node.target_id == node_id:
            return node.target_kind
    return None


def node_title(itinerary: Itinerary, target_id: str) -> str:
    for node in itinerary.nodes:
        if node.target_id == target_id:
            return node.title
    return target_id


def find_entity(kind: str, target_id: str, pois: list[Poi], restaurants: list[Restaurant]) -> Optional[Entity]:
    pool: list[Entity] = pois if kind == "poi" else restaurants
    return next((e for e in pool if e.id == target_id), None)


_AMBIENCE_ZH = {"安静聊天": "更安静", "热闹": "更热闹"}


def adjustment_descriptor(adjustment: NodeAdjustment) -> str:
    """把定向调整翻成一小句人话，拼进换菜说明（"更安静"这类风格短语）。"""
    dim, value = adjustment.dimension, adjustment.value
    if dim == NodeAdjustmentDimension.PRICE:
        return "更便宜" if value == "cheaper" else "贵一点但应该更值"
    if dim == NodeAdjustmentDimension.DISTANCE:
        return "更近" if value == "closer" else "稍远一点但更合适"
    if dim == NodeAdjustmentDimension.CUISINE_OR_TYPE:
        return f"换成{value}口味"
    if dim == NodeAdjustmentDimension.AMBIENCE:
        return _AMBIENCE_ZH.get(value, f"更{value}")
    # DIETARY / CROWD_FIT：value 本身就是可读短语（"不辣" / "亲子友好"）
    return value


def compose_narration_text(base: str, advisories: list[dict]) -> str:
    """advisory message 并入 text（同 narrate.py 一贯把 D-7 告知拼进暖语气正文的纪律）。"""
    extra = "".join(a["message"] for a in advisories if a.get("message"))
    return f"{base}{extra}" if extra else base


# ============================================================
# 「具名备选」候选池收窄
# ============================================================


def narrow_pool_to_single_alternative(
    itinerary: Itinerary,
    pois: list[Poi],
    restaurants: list[Restaurant],
    kind: str,
    chosen: Entity,
) -> tuple[list[Poi], list[Restaurant]]:
    """候选池收窄手法（业务规则：具名备选"点这个就该换成这个"）。

    `resolve_node_swap` 的降级序列本质是"在候选池里挑最优"，若原样传入
    全量召回池，用户点的这个具名备选可能被同池里评分更高的另一候选顶替，
    违背"点这个就该换成这个"的字面承诺。收窄传入的候选池到"当前已选中的
    全部实体（覆盖 `resolve_node_swap` 前置条件 2）∪ 这一个被点中的备选"，
    让降级序列的候选集合里只有它一个"新"选项——`route_builder.repair_route`
    自身按已在场实体排除"已经在场"的候选，故已选中实体混进候选池不会被
    误选为新增候选，最终真正竞争"这一格"的就只剩这一个被点中的备选。
    """
    kept_poi_ids = {n.target_id for n in itinerary.nodes if n.target_kind == "poi"}
    kept_rest_ids = {n.target_id for n in itinerary.nodes if n.target_kind == "restaurant"}
    call_pois = [p for p in pois if p.id in kept_poi_ids]
    call_rests = [r for r in restaurants if r.id in kept_rest_ids]
    if kind == "poi" and chosen.id not in kept_poi_ids:
        call_pois.append(chosen)  # type: ignore[arg-type]
    elif kind == "restaurant" and chosen.id not in kept_rest_ids:
        call_rests.append(chosen)  # type: ignore[arg-type]
    return call_pois, call_rests


__all__ = [
    "CONFIRMED_ADJUST_BLOCKED_MESSAGE",
    "synthesize_source_text",
    "target_kind",
    "node_title",
    "find_entity",
    "adjustment_descriptor",
    "compose_narration_text",
    "narrow_pool_to_single_alternative",
]
