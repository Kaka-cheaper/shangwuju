"""schemas.demand_ledger —— 诉求台账跨层契约 + 单一写入/消费 helper（ADR-0013 决策 3 / F-2）。

【这是什么问题】

ADR-0013 决策 3 把「诉求台账」定为会话上下文家族第三器官（轮次日志=证据、
版本志=方案史、台账=诉求索引）：记名的、不压扁的「谁 · 要了什么 · 针对哪个
节点 · 状态」历史。与 `agent.planning.planners.node_swap.resolve_node_swap`
的 `ledger_slice` 消费接口（该模块头部 docstring "ledger_slice 消费接口"节，
F-1 拍板）对齐——核心可满足载荷复用 `schemas.node_adjustment.NodeAdjustment`，
本模块只加"谁 · 针对哪个节点 · 生效状态 · 指回来源"这层外层信封，不新造
第二套"调哪个维度、调成什么样"的平行结构（ADR 原文"台账塞进轮次日志硬承载"
被拒后的正解：第三器官 + 复用核心载荷）。

【两个底座、一个 helper】

单人会话（`agent.graph.state.AgentState.demand_ledger: list[dict]`，
SESSION_SCOPED）与协作房间（`collab.room.Room.demand_ledger`，生命周期随房间）
各自的存储位是 dict 序列化形状（跨进程/跨 checkpoint 的 JSON 友好形状，与
`schemas.advisory.Advisory` 走 `model_dump()` 存进 `AgentState.advisories`
同一先例）；本模块的三个函数（`record_demand` / `active_adjustments` /
`ledger_for_display`）都工作在**类型化** `LedgerEntry` 层——业务规则（顶替/
切片/投影）用类型化对象表达最清楚、最好测；dict ⟷ `LedgerEntry` 的序列化/
反序列化边界留给调用方（图节点 / 房间处理器），那是"谁在何时调用"的接线
决策，属于 F-4/F-5 范围，本步不做（同 `resolve_node_swap` 头部"本模块不关心
信封字段"的分层纪律）。

【状态机（生效 / 被顶替 / 已满足）与顶替规则】

见 `LedgerEntryStatus` 与 `record_demand`/`mark_satisfied` 各自 docstring；顶替判定 key 是
`(member_id, node_ref, adjustment.dimension)` 三元组——同这三者的既有生效
条目被标记 `SUPERSEDED`（原地保留，不删除，"旧条目留痕"是台账"不压扁历史"
的核心承诺）；`member_id` 不同则不顶替，共存并暴露交 LLM 调解（ADR 原文
"跨成员矛盾共存"）。单人模式下所有条目 `member_id` 恒为 `None`——`None ==
None` 天然满足"同一 member"，顶替规则不需要为单人模式特判。

不负责：
- 谁在何时调用 `record_demand` / `mark_satisfied`（图节点 / 房间处理器接线，
  F-4/F-5——本模块只提供状态翻转的机制，"什么时候判定为已满足"的业务判据
  （如 F-1 `degrade_tier` 命中哪一级）由消费方决定，见 `mark_satisfied`
  docstring）。
- "全局语义诉求同步揉进 intent"的消费时机（F-4 拍板，ADR-0013 决策 3 提及
  但明确留给消费方决定"什么时候揉"）。
- 前端台账面板的渲染（`ledger_for_display` 只产出投影 dict，UI 是 F-4 的事）。
- TTL / 房间销毁时机（F-5）。
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension


class LedgerEntryStatus(str, Enum):
    """诉求条目状态机（ADR-0013 决策 3：生效 / 被顶替 / 已满足）。

    英文枚举值 + 中文行内注释，对齐 `schemas.advisory.AdvisoryCode` /
    `schemas.node_adjustment.NodeAdjustmentDimension` 既有命名风格（内部值
    English snake_case，用户可见文案另在展示层——`ledger_for_display`——生成）。
    """

    ACTIVE = "active"  # 生效：当前仍是有效诉求，未被顶替也未被满足
    SUPERSEDED = "superseded"  # 被顶替：同 member+node_ref+dimension 的更新诉求已顶替它；原地保留，不删除
    SATISFIED = "satisfied"  # 已满足：诉求已被规划结果满足（判定/回写时机不在本模块——由消费方决定，见 F-4）


class NodeRef(BaseModel):
    """指向方案里一个具体节点的引用——`kind` + `target_id`，与 `schemas.pin.PinSpec`/
    `Visit.kind`/`ActivityNode.target_kind`同一套「实体引用」词汇（poi 用 Poi.id，
    restaurant 用 Restaurant.id）。

    `LedgerEntry.node_ref` 为 `None` 表示这是一条**全局语义诉求**（忌口/预算类）
    ——不指向任何具体节点，ADR-0013 决策 3 原文"全局语义诉求两边都进（台账 +
    intent），节点级只进台账"。

    与 `PinSpec` 同形不同义，不合并：`PinSpec` 表达"必须包含这个实体"（点名
    必去），`NodeRef` 表达"这条诉求是针对方案里已存在的这一个节点位置"——
    语义轴不同，合并成一个类型会让两条独立演化的契约互相牵连（同 `pin.py`
    "不同概念不共享类型，即便形状相同"的既有分层理由）。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["poi", "restaurant"]
    target_id: str = Field(..., min_length=1, description="对应 Poi.id / Restaurant.id")


class LedgerEntry(BaseModel):
    """诉求台账的一条记录——外层信封包住 `NodeAdjustment` 核心载荷（ADR-0013
    决策 3 明令不造平行结构，见模块 docstring）。

    字段：
    - `member_id` / `nickname`：谁提的诉求；单人模式两者皆 `None`（单人会话
      没有"成员"概念，但依然记账——ADR-0013 决策 3 用户拍板"单人也记账，
      懂人的系统要在重规划时主动提及"）。
    - `node_ref`：针对哪个节点；`None` = 全局语义诉求（见 `NodeRef` docstring）。
    - `adjustment`：核心可满足载荷，复用 `NodeAdjustment`——与 F-1
      `ledger_slice`/按钮点击/点踩换菜同一形状、同一真相源。
    - `status`：见 `LedgerEntryStatus`；新记录默认 `ACTIVE`。
    - `source_text`：原话指针的**当前实现**——E-2 轮次日志出生前，本字段先存
      一小段用户原话短串（不是全量转写）；轮次日志落地后，本字段的职责会
      **降级为指向轮次日志的指针**（如 turn_id），届时字段语义变化，但本步
      不提前猜指针的具体形状（同 `schemas/pin.py`"不猜未来形状，等真正需要
      时再设计"的既有纪律）。
    - `created_at`：记账时刻（epoch 秒）；供展示排序/审计用，`record_demand`
      的顶替判定不依赖它——写入顺序（ledger 列表原有顺序 + 新条目追加在
      末尾）已经天然定序，不需要额外靠时间戳比较新旧。
    """

    model_config = ConfigDict(extra="forbid")

    member_id: Optional[str] = None
    nickname: Optional[str] = None
    node_ref: Optional[NodeRef] = None
    adjustment: NodeAdjustment
    status: LedgerEntryStatus = LedgerEntryStatus.ACTIVE
    source_text: str = Field(
        ...,
        min_length=1,
        description="用户原话短串（E-2 轮次日志出生前的占位实现，见类 docstring）",
    )
    created_at: float = Field(default_factory=time.time)


# ============================================================
# 单一写入 helper（两底座共用）
# ============================================================


def record_demand(ledger: Sequence[LedgerEntry], entry: LedgerEntry) -> list[LedgerEntry]:
    """写入一条新诉求，实现顶替规则（ADR-0013 决策 3）——两底座（单人 state /
    房间 Room）共用的唯一写入口，不重复实现第二份顶替逻辑。

    顶替规则：`ledger` 中与 `entry` **同 `member_id`、同 `node_ref`、同
    `adjustment.dimension`** 的既有 `ACTIVE` 条目被标记为 `SUPERSEDED`（原地
    保留，不删除——"旧条目留痕"是台账"谁要了什么"不压扁历史的核心承诺）；
    `entry` 本身原样追加在新列表末尾。

    跨 member 矛盾**不**顶替：`member_id` 不同时，即便 `node_ref` + `dimension`
    完全相同也视为两条独立、共存的诉求（ADR 原文"跨成员矛盾共存并暴露交 LLM
    调解"，不是本函数要解决的调解逻辑——本函数只负责"顶不顶替"这一步判定）。
    单人模式下所有条目 `member_id` 恒为 `None`，`None == None` 天然满足
    "同一 member"，顶替规则在单人模式下不需要特判即可正确工作。

    已是 `SUPERSEDED`/`SATISFIED` 的既有条目不会被本函数动到（只有当前
    `ACTIVE` 的条目才会被顶替——已经不生效的条目没有"再顶替一次"的意义）。

    纯函数：不修改入参 `ledger` 或其中任何条目对象，返回全新列表（用
    `model_copy` 生成状态翻转后的新对象，原对象保持不变）。
    """
    supersede_key = (entry.member_id, entry.node_ref, entry.adjustment.dimension)
    new_ledger: list[LedgerEntry] = []
    for existing in ledger:
        existing_key = (existing.member_id, existing.node_ref, existing.adjustment.dimension)
        if existing.status == LedgerEntryStatus.ACTIVE and existing_key == supersede_key:
            new_ledger.append(
                existing.model_copy(update={"status": LedgerEntryStatus.SUPERSEDED})
            )
        else:
            new_ledger.append(existing)
    new_ledger.append(entry)
    return new_ledger


def mark_satisfied(
    ledger: Sequence[LedgerEntry],
    *,
    member_id: Optional[str],
    node_ref: Optional[NodeRef],
    dimension: NodeAdjustmentDimension,
) -> list[LedgerEntry]:
    """把"刚被满足"的 ACTIVE 诉求标记为 `SATISFIED`（ADR-0013 F-4 消费口径，
    F-5 房间可直接复用——同 `record_demand`"两底座共用一份写入逻辑"的分层
    纪律，不为房间侧另写第二份状态翻转代码）。

    【这是什么问题】F-1 `resolve_node_swap` 返回的 `degrade_tier`（1/2/3）标出
    了这次换菜对"用户点的这个定向调整"满足到什么程度：tier 1/2 命中时新实体
    确实满足 `adjustment` 谓词（`node_swap._adjustment_satisfied` 已验证过），
    tier 3 只是"近似"（谓词不保证成立，见 `SWAP_DEGRADED` advisory）。谁在什么
    时候把这个判定结果回写进台账状态机——本模块 docstring 早已声明"不负责，
    由消费方决定"；F-4 拍板：消费方（`api/_streams/graph_adjust.py`）只在
    `degrade_tier in (1, 2)` 时调用本函数，tier 3/None（点踩，无方向）不标满足
    （近似满足/无方向换不构成"诉求被满足"的证据）。

    定位键与 `record_demand` 的顶替判定同一个三元组
    `(member_id, node_ref, adjustment.dimension)`——精确匹配"这一条刚被这次
    操作满足的诉求"，不是笼统把该 node_ref 上所有生效诉求都标满足（同节点上
    可能还挂着其它维度的独立诉求，比如先前记的"更便宜"依旧生效，不会因为这次
    满足的是"不辣"而被一并标满足——那会是误报"帮你满足了你根本没这次提的
    诉求"）。纯函数：不修改入参，返回全新列表（同 `record_demand` 的
    `model_copy` 纪律）。
    """
    key = (member_id, node_ref, dimension)
    return [
        entry.model_copy(update={"status": LedgerEntryStatus.SATISFIED})
        if (
            entry.status == LedgerEntryStatus.ACTIVE
            and (entry.member_id, entry.node_ref, entry.adjustment.dimension) == key
        )
        else entry
        for entry in ledger
    ]


# ============================================================
# 消费选择器
# ============================================================


def active_adjustments(
    ledger: Sequence[LedgerEntry], node_ref: Optional[NodeRef] = None
) -> list[NodeAdjustment]:
    """给 F-1 引擎的 `ledger_slice` 喂料：某个节点在场的生效诉求 + 全局生效诉求。

    匹配规则（"node_ref 匹配 + 全局条目"，ADR-0013 F-1 `node_swap.py` 头部
    "ledger_slice 消费接口"节要求的语义）：条目的 `node_ref` 与传入的 `node_ref`
    完全一致，**或**条目本身是全局诉求（`node_ref is None`）——全局诉求（忌口/
    预算类）无论在解哪个节点都应纳入考虑。只看 `status == ACTIVE`——`SUPERSEDED`/
    `SATISFIED` 条目不再是"当下真正该考虑的诉求"（F-1 假定传入的切片已经是
    去重后生效的那些，见 `node_swap.py` 头部"本模块假定传入的就是当下真正该
    考虑的那些"）。

    不传 `node_ref`（默认 `None`）等价于"没有具体节点上下文"，此时只返回全局
    生效诉求——`entry.node_ref is None` 这一条件本身在 `node_ref=None` 时与
    `entry.node_ref == node_ref` 重合，不需要为"未传节点"单独分支。
    """
    return [
        entry.adjustment
        for entry in ledger
        if entry.status == LedgerEntryStatus.ACTIVE
        and (entry.node_ref is None or entry.node_ref == node_ref)
    ]


def ledger_for_display(ledger: Sequence[LedgerEntry]) -> list[dict[str, Any]]:
    """给前端台账面板的展示投影（F-4 消费）——含状态与归名，JSON 友好 dict 形状。

    与 `active_adjustments` 的区别：本函数不过滤状态（`SUPERSEDED`/`SATISFIED`
    条目也要显示——台账面板的价值正是"看得见协商拉锯过程"，不是只看当下生效值；
    过滤/高亮由前端按 `status` 字段自行决定），也不局限于某个节点（返回全量，
    节点归属通过 `node_ref` 字段暴露给前端自行分组/过滤）。
    """
    return [
        {
            "member_id": entry.member_id,
            "nickname": entry.nickname,
            "node_ref": entry.node_ref.model_dump() if entry.node_ref is not None else None,
            "dimension": entry.adjustment.dimension.value,
            "value": entry.adjustment.value,
            "status": entry.status.value,
            "source_text": entry.source_text,
            "created_at": entry.created_at,
        }
        for entry in ledger
    ]


__all__ = [
    "LedgerEntryStatus",
    "NodeRef",
    "LedgerEntry",
    "record_demand",
    "mark_satisfied",
    "active_adjustments",
    "ledger_for_display",
]
