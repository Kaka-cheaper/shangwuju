"""tests.test_demand_ledger —— ADR-0013 F-2：诉求台账 schema + 顶替规则 + 消费选择器。

覆盖 `schemas.demand_ledger` 的 `LedgerEntry` / `record_demand` /
`active_adjustments` / `ledger_for_display`，以及两个底座的存储登记：

1. 顶替规则：同 member_id + 同 node_ref + 同 adjustment.dimension 的既有生效
   条目被标记 `SUPERSEDED`（原地保留，不删除），新条目原样追加。
2. 跨 member 矛盾共存：member_id 不同则不顶替，即便 node_ref+dimension 完全相同。
3. 维度/节点不同不顶替（顶替判定必须三键同时命中）。
4. 已失效条目（SUPERSEDED/SATISFIED）不会被"再顶替一次"。
5. `record_demand` 是纯函数：不修改入参列表/条目对象。
6. `active_adjustments` 切片正确：node_ref 匹配 + 全局条目，排除其它节点/失效条目。
7. `ledger_for_display` 投影完整（含状态与归名），且不过滤失效条目。
8. 生命周期完备性：`demand_ledger` 已登记 SESSION_SCOPED，
   `make_initial_state` 覆盖它、`reset_for_new_episode` 不覆盖它。
9. `collab.room.Room` 新增 `demand_ledger` 字段：每个实例独立空表（无共享
   可变默认值陷阱）。
"""

from __future__ import annotations

from schemas.demand_ledger import (
    LedgerEntry,
    LedgerEntryStatus,
    NodeRef,
    active_adjustments,
    ledger_for_display,
    mark_satisfied,
    record_demand,
)
from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension


# ============================================================
# fixture helpers
# ============================================================


def _adj(dimension: NodeAdjustmentDimension, value: str) -> NodeAdjustment:
    return NodeAdjustment(dimension=dimension, value=value)


def _entry(
    *,
    member_id: str | None = None,
    nickname: str | None = None,
    node_ref: NodeRef | None = None,
    dimension: NodeAdjustmentDimension = NodeAdjustmentDimension.PRICE,
    value: str = "cheaper",
    status: LedgerEntryStatus = LedgerEntryStatus.ACTIVE,
    source_text: str = "便宜点的吧",
) -> LedgerEntry:
    return LedgerEntry(
        member_id=member_id,
        nickname=nickname,
        node_ref=node_ref,
        adjustment=_adj(dimension, value),
        status=status,
        source_text=source_text,
    )


NODE_R1 = NodeRef(kind="restaurant", target_id="R1")
NODE_R2 = NodeRef(kind="restaurant", target_id="R2")


# ============================================================
# 1) 顶替规则
# ============================================================


def test_record_demand_supersedes_same_member_node_dimension():
    old = _entry(member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="cheaper")
    new = _entry(member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="pricier")

    new_ledger = record_demand([old], new)

    assert len(new_ledger) == 2
    assert new_ledger[0].status == LedgerEntryStatus.SUPERSEDED
    assert new_ledger[0].adjustment.value == "cheaper"  # 旧条目留痕，内容不变
    assert new_ledger[1] is new
    assert new_ledger[1].status == LedgerEntryStatus.ACTIVE


def test_record_demand_supersedes_global_entry_same_member_dimension():
    """node_ref=None（全局诉求）也走同一套顶替判定——全局 vs 全局照样顶替。"""
    old = _entry(member_id="u1", node_ref=None, dimension=NodeAdjustmentDimension.DIETARY, value="不辣")
    new = _entry(member_id="u1", node_ref=None, dimension=NodeAdjustmentDimension.DIETARY, value="无牛肉")

    new_ledger = record_demand([old], new)

    assert new_ledger[0].status == LedgerEntryStatus.SUPERSEDED
    assert new_ledger[1].status == LedgerEntryStatus.ACTIVE


# ============================================================
# 2) 跨 member 矛盾共存
# ============================================================


def test_record_demand_cross_member_coexists():
    a = _entry(member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="cheaper")
    b = _entry(member_id="u2", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="pricier")

    new_ledger = record_demand([a], b)

    assert new_ledger[0].status == LedgerEntryStatus.ACTIVE, "跨成员矛盾不顶替，旧条目仍生效"
    assert new_ledger[1].status == LedgerEntryStatus.ACTIVE


def test_record_demand_single_user_mode_member_id_none_still_supersedes():
    """单人模式：所有条目 member_id 恒为 None，顶替规则不需要特判即可工作。"""
    old = _entry(member_id=None, node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="cheaper")
    new = _entry(member_id=None, node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="pricier")

    new_ledger = record_demand([old], new)

    assert new_ledger[0].status == LedgerEntryStatus.SUPERSEDED
    assert new_ledger[1].status == LedgerEntryStatus.ACTIVE


# ============================================================
# 3) 维度/节点不同不顶替
# ============================================================


def test_record_demand_different_dimension_no_supersede():
    old = _entry(member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="cheaper")
    new = _entry(member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.DISTANCE, value="closer")

    new_ledger = record_demand([old], new)

    assert new_ledger[0].status == LedgerEntryStatus.ACTIVE
    assert new_ledger[1].status == LedgerEntryStatus.ACTIVE


def test_record_demand_different_node_no_supersede():
    old = _entry(member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="cheaper")
    new = _entry(member_id="u1", node_ref=NODE_R2, dimension=NodeAdjustmentDimension.PRICE, value="pricier")

    new_ledger = record_demand([old], new)

    assert new_ledger[0].status == LedgerEntryStatus.ACTIVE
    assert new_ledger[1].status == LedgerEntryStatus.ACTIVE


def test_record_demand_global_vs_node_scoped_no_supersede():
    """全局条目（node_ref=None）与节点条目（node_ref=NODE_R1）即便同 member+dimension
    也不是同一 node_ref，不应互相顶替。"""
    old_global = _entry(member_id="u1", node_ref=None, dimension=NodeAdjustmentDimension.PRICE, value="cheaper")
    new_node = _entry(member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="pricier")

    new_ledger = record_demand([old_global], new_node)

    assert new_ledger[0].status == LedgerEntryStatus.ACTIVE


# ============================================================
# 4) 已失效条目不会被"再顶替一次"
# ============================================================


def test_record_demand_does_not_touch_already_inactive_entries():
    superseded = _entry(
        member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE,
        value="cheaper", status=LedgerEntryStatus.SUPERSEDED,
    )
    satisfied = _entry(
        member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE,
        value="pricier", status=LedgerEntryStatus.SATISFIED,
    )
    new = _entry(member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="cheaper")

    new_ledger = record_demand([superseded, satisfied], new)

    assert new_ledger[0].status == LedgerEntryStatus.SUPERSEDED
    assert new_ledger[1].status == LedgerEntryStatus.SATISFIED
    assert new_ledger[2].status == LedgerEntryStatus.ACTIVE


# ============================================================
# 5) 纯函数：不修改入参
# ============================================================


def test_record_demand_is_pure_does_not_mutate_input():
    old = _entry(member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="cheaper")
    ledger = [old]
    new = _entry(member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="pricier")

    result = record_demand(ledger, new)

    assert len(ledger) == 1, "入参列表本身不应被追加"
    assert old.status == LedgerEntryStatus.ACTIVE, "入参条目对象本身不应被就地修改"
    assert result[0] is not old, "顶替产出的是新对象（model_copy），不是原对象"


# ============================================================
# 6) active_adjustments 切片
# ============================================================


def test_active_adjustments_matches_node_and_global_excludes_others_and_inactive():
    node_entry = _entry(member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="cheaper")
    global_entry = _entry(member_id="u1", node_ref=None, dimension=NodeAdjustmentDimension.DIETARY, value="不辣")
    other_node_entry = _entry(member_id="u1", node_ref=NODE_R2, dimension=NodeAdjustmentDimension.AMBIENCE, value="安静聊天")
    superseded_entry = _entry(
        member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.DISTANCE,
        value="closer", status=LedgerEntryStatus.SUPERSEDED,
    )
    ledger = [node_entry, global_entry, other_node_entry, superseded_entry]

    result = active_adjustments(ledger, node_ref=NODE_R1)

    assert result == [node_entry.adjustment, global_entry.adjustment]


def test_active_adjustments_without_node_ref_returns_only_global():
    node_entry = _entry(member_id="u1", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="cheaper")
    global_entry = _entry(member_id="u1", node_ref=None, dimension=NodeAdjustmentDimension.DIETARY, value="不辣")
    ledger = [node_entry, global_entry]

    result = active_adjustments(ledger)

    assert result == [global_entry.adjustment]


def test_active_adjustments_empty_ledger_returns_empty():
    assert active_adjustments([]) == []
    assert active_adjustments([], node_ref=NODE_R1) == []


# ============================================================
# 7) ledger_for_display 投影
# ============================================================


def test_ledger_for_display_projection_shape_and_includes_inactive():
    node_entry = _entry(
        member_id="u1", nickname="小明", node_ref=NODE_R1,
        dimension=NodeAdjustmentDimension.PRICE, value="cheaper",
    )
    superseded_entry = _entry(
        member_id="u1", nickname="小明", node_ref=NODE_R1,
        dimension=NodeAdjustmentDimension.PRICE, value="pricier",
        status=LedgerEntryStatus.SUPERSEDED,
    )
    global_entry = _entry(member_id=None, nickname=None, node_ref=None, dimension=NodeAdjustmentDimension.DIETARY, value="不辣")

    projected = ledger_for_display([node_entry, superseded_entry, global_entry])

    assert len(projected) == 3, "展示投影不过滤失效条目"

    first = projected[0]
    assert first["member_id"] == "u1"
    assert first["nickname"] == "小明"
    assert first["node_ref"] == {"kind": "restaurant", "target_id": "R1", "title": None}
    assert first["dimension"] == "price"
    assert first["value"] == "cheaper"
    assert first["status"] == "active"
    assert "source_text" in first
    assert "created_at" in first

    assert projected[1]["status"] == "superseded"

    last = projected[2]
    assert last["node_ref"] is None, "全局诉求投影里 node_ref 应为 None"


# ============================================================
# 8) LedgerEntry 默认状态
# ============================================================


def test_ledger_entry_defaults_to_active_status():
    entry = _entry()
    assert entry.status == LedgerEntryStatus.ACTIVE


def test_node_ref_equality_by_value():
    """NodeRef 是 pydantic BaseModel，按字段值比较相等——顶替判定依赖这一点。"""
    assert NodeRef(kind="restaurant", target_id="R1") == NodeRef(kind="restaurant", target_id="R1")
    assert NodeRef(kind="restaurant", target_id="R1") != NodeRef(kind="restaurant", target_id="R2")


# ============================================================
# 9) NodeRef.title 店名快照（UI 修复批·台账店名快照）
# ============================================================


def test_node_ref_title_defaults_to_none_for_backward_compat():
    """本字段新增前落盘的旧条目反序列化时没有这个 key——必须有默认值，
    不能让老数据在 `LedgerEntry.model_validate` 这一步就炸（extra="forbid"
    只禁多余字段，不禁缺失有默认值的字段，这里验证默认值确实是 None）。"""
    ref = NodeRef(kind="restaurant", target_id="R1")
    assert ref.title is None


def test_node_ref_title_snapshot_survives_in_record_demand_and_display():
    """记账时刻把店名快照存进 NodeRef.title，顶替/展示投影全程原样携带——
    这是台账"不压扁历史"承诺在"节点被换菜后旧条目还认得出店名"这件事上的
    根治（此前展示层事后反查 itinerary.nodes，节点被换菜后旧 id 查不到，
    退化成裸 id，如 WJP062）。"""
    ref_with_title = NodeRef(kind="restaurant", target_id="R1", title="老地方火锅")
    entry = _entry(member_id="u1", node_ref=ref_with_title, dimension=NodeAdjustmentDimension.PRICE, value="cheaper")

    ledger = record_demand([], entry)
    assert ledger[0].node_ref.title == "老地方火锅"

    projected = ledger_for_display(ledger)
    assert projected[0]["node_ref"]["title"] == "老地方火锅"

    # 顶替判定不受 title 影响——三元组仍是 (member_id, node_ref, dimension)，
    # node_ref 相等性按 NodeRef 全部字段值比较（pydantic BaseModel 默认行为），
    # 因此顶替新条目必须携带同样的 title 才会被判定为"同一节点引用"顶替；
    # 这是既有设计的自然推论，不是本次新增行为，这里显式验证不留暗坑。
    same_ref_new_value = NodeRef(kind="restaurant", target_id="R1", title="老地方火锅")
    newer = _entry(member_id="u1", node_ref=same_ref_new_value, dimension=NodeAdjustmentDimension.PRICE, value="pricier")
    ledger2 = record_demand(ledger, newer)
    assert ledger2[0].status == LedgerEntryStatus.SUPERSEDED
    assert ledger2[1].node_ref.title == "老地方火锅"
    assert NodeRef(kind="restaurant", target_id="R1") != NodeRef(kind="poi", target_id="R1")


# ============================================================
# 8b) mark_satisfied（ADR-0013 F-4：换菜成功回写）
# ============================================================


def test_mark_satisfied_flips_exact_matching_active_entry():
    entry = _entry(member_id=None, node_ref=NODE_R1, dimension=NodeAdjustmentDimension.DIETARY, value="不辣")
    ledger = [entry]

    result = mark_satisfied(
        ledger, member_id=None, node_ref=NODE_R1, dimension=NodeAdjustmentDimension.DIETARY
    )

    assert result[0].status == LedgerEntryStatus.SATISFIED
    assert result[0] is not entry, "纯函数：应返回新对象，不改写入参"
    assert entry.status == LedgerEntryStatus.ACTIVE, "入参条目本身不应被就地修改"


def test_mark_satisfied_does_not_touch_other_dimension_on_same_node():
    """同节点上另一维度的独立生效诉求不应被这次满足回写误伤——不能因为满足了
    「不辣」就顺手把「更便宜」也标成已满足（那是它没提出过的诉求）。"""
    dietary_entry = _entry(member_id=None, node_ref=NODE_R1, dimension=NodeAdjustmentDimension.DIETARY, value="不辣")
    price_entry = _entry(member_id=None, node_ref=NODE_R1, dimension=NodeAdjustmentDimension.PRICE, value="cheaper")
    ledger = [dietary_entry, price_entry]

    result = mark_satisfied(
        ledger, member_id=None, node_ref=NODE_R1, dimension=NodeAdjustmentDimension.DIETARY
    )

    assert result[0].status == LedgerEntryStatus.SATISFIED
    assert result[1].status == LedgerEntryStatus.ACTIVE, "不同维度的诉求不受影响"


def test_mark_satisfied_ignores_other_node_and_other_member():
    entry_other_node = _entry(member_id=None, node_ref=NODE_R2, dimension=NodeAdjustmentDimension.DIETARY, value="不辣")
    entry_other_member = _entry(member_id="u9", node_ref=NODE_R1, dimension=NodeAdjustmentDimension.DIETARY, value="不辣")
    ledger = [entry_other_node, entry_other_member]

    result = mark_satisfied(
        ledger, member_id=None, node_ref=NODE_R1, dimension=NodeAdjustmentDimension.DIETARY
    )

    assert result[0].status == LedgerEntryStatus.ACTIVE
    assert result[1].status == LedgerEntryStatus.ACTIVE


def test_mark_satisfied_does_not_touch_already_inactive_entries():
    superseded = _entry(
        member_id=None, node_ref=NODE_R1, dimension=NodeAdjustmentDimension.DIETARY,
        value="不辣", status=LedgerEntryStatus.SUPERSEDED,
    )
    ledger = [superseded]

    result = mark_satisfied(
        ledger, member_id=None, node_ref=NODE_R1, dimension=NodeAdjustmentDimension.DIETARY
    )

    assert result[0].status == LedgerEntryStatus.SUPERSEDED, "已失效条目不应被'复活'成已满足"


# ============================================================
# 9) 生命周期完备性（AgentState.demand_ledger，ADR-0013 决策 3）
# ============================================================


def test_demand_ledger_registered_session_scoped_and_wired_correctly():
    from agent.graph.state import (
        EPISODE_SCOPED,
        SESSION_SCOPED,
        TURN_SCOPED,
        make_initial_state,
        reset_for_new_episode,
    )

    assert "demand_ledger" in SESSION_SCOPED
    assert "demand_ledger" not in EPISODE_SCOPED
    assert "demand_ledger" not in TURN_SCOPED

    # make_initial_state 写 []——经 _merge_demand_ledger 归并器是 no-op(深审修正:
    # 存活性结构保障,不再靠"调用方记得透传旧值"的形参口子)
    state = make_initial_state(user_input="test", session_id="s-ledger")
    assert state.get("demand_ledger") == []

    # 归并器语义(跨轮存活的机制本体):空更新保留旧值,非空整体替换
    from agent.graph.state import _merge_demand_ledger

    prior = [{"member_id": "u1", "status": "active"}]
    assert _merge_demand_ledger(prior, []) == prior, "每轮初始化的 [] 不得清空存档台账"
    newer = [
        {"member_id": "u1", "status": "superseded"},
        {"member_id": "u2", "status": "active"},
    ]
    assert _merge_demand_ledger(prior, newer) == newer, "record_demand 全量新列表应整体替换"
    assert _merge_demand_ledger(None, []) == []  # 首轮无旧值

    # reset_for_new_episode 不应触碰它（这正是它跨事件存活的另一半机制）
    assert "demand_ledger" not in reset_for_new_episode()


# ============================================================
# 10) Room.demand_ledger 存储位（ADR-0013 F-2 房间底座）
# ============================================================


def test_room_demand_ledger_field_defaults_independent_empty_list():
    from collab.room import Room

    room_a = Room(room_id="r1", owner_id="o1")
    room_b = Room(room_id="r2", owner_id="o2")

    assert room_a.demand_ledger == []
    room_a.demand_ledger.append({"member_id": "u1"})

    assert room_b.demand_ledger == [], "default_factory 必须给每个实例独立列表，不能共享同一个默认列表对象"
