"""itinerary edge_v1 schema 单元测试。

覆盖 5 条 model_validator 不变量 + OrderRecord.target_kind 字段。
本文件是 itinerary-edge-model-refactor Task 1 的验收依据。
"""

import pytest
from pydantic import ValidationError

from schemas.itinerary import (
    ActivityNode,
    Hop,
    Itinerary,
    OrderRecord,
)


# ============================================================
# 测试夹具：一个最小合法 itinerary（2 节点 1 hop，home→home 同地）
# ============================================================


def _home_node(node_id: str, start_time: str = "14:00") -> ActivityNode:
    """构造一个合法的 home 节点。"""
    return ActivityNode(
        node_id=node_id,
        kind="出发" if node_id.endswith("start") else "返回",
        target_kind="home",
        target_id="home",
        start_time=start_time,
        duration_min=0,
        title="家",
    )


def _poi_node(node_id: str, start_time: str = "14:30", duration: int = 60) -> ActivityNode:
    """构造一个合法的 POI 节点。"""
    return ActivityNode(
        node_id=node_id,
        kind="主活动",
        target_kind="poi",
        target_id="P001",
        start_time=start_time,
        duration_min=duration,
        title="森林儿童探索乐园",
    )


def _hop(hop_id: str, from_id: str, to_id: str, start: str = "14:00", minutes: int = 20) -> Hop:
    """构造一个合法的 hop。"""
    return Hop(
        hop_id=hop_id,
        from_node_id=from_id,
        to_node_id=to_id,
        start_time=start,
        minutes=minutes,
        mode="taxi",
        path_type="real_route",
    )


# ============================================================
# 1. 合法路径
# ============================================================


def test_minimal_legal_itinerary_two_nodes_one_hop():
    """最小合法 itinerary：home → home（直接返回，1 hop 占位）。"""
    itin = Itinerary(
        summary="测试最小行程",
        nodes=[
            _home_node("n_home_start", "14:00"),
            _home_node("n_home_end", "14:30"),
        ],
        hops=[_hop("h_0", "n_home_start", "n_home_end", "14:00", 30)],
        total_minutes=30,
    )
    assert itin.schema_version == "edge_v1"
    assert len(itin.nodes) == 2
    assert len(itin.hops) == 1
    assert itin.schedule == []  # 默认空，由下游 builder 填充


def test_legal_itinerary_three_nodes_two_hops():
    """常见路径：home → POI → home。"""
    itin = Itinerary(
        summary="家庭半日方案",
        nodes=[
            _home_node("n0", "14:00"),
            _poi_node("n1", "14:30", duration=120),
            _home_node("n2", "17:00"),
        ],
        hops=[
            _hop("h0", "n0", "n1", "14:00", 30),
            _hop("h1", "n1", "n2", "16:30", 30),
        ],
        total_minutes=180,
    )
    assert len(itin.nodes) == 3
    assert len(itin.hops) == 2


# ============================================================
# 2. 不变量 1：hops 长度 = nodes - 1
# ============================================================


def test_invariant_hops_length_mismatch_too_few():
    """hops 比 nodes-1 少 → ValidationError。"""
    with pytest.raises(ValidationError) as exc:
        Itinerary(
            summary="x",
            nodes=[
                _home_node("n0", "14:00"),
                _poi_node("n1"),
                _home_node("n2", "17:00"),
            ],
            hops=[_hop("h0", "n0", "n1")],  # 只有 1 个，应该 2 个
            total_minutes=180,
        )
    assert "hops 长度" in str(exc.value)


def test_invariant_hops_length_mismatch_too_many():
    """hops 比 nodes-1 多 → ValidationError。"""
    with pytest.raises(ValidationError) as exc:
        Itinerary(
            summary="x",
            nodes=[
                _home_node("n0", "14:00"),
                _home_node("n1", "14:30"),
            ],
            hops=[
                _hop("h0", "n0", "n1"),
                _hop("h1", "n0", "n1"),
            ],
            total_minutes=30,
        )
    assert "hops 长度" in str(exc.value)


# ============================================================
# 3. 不变量 2/3：首尾必为 home
# ============================================================


def test_invariant_first_node_must_be_home():
    """首节点不是 home → ValidationError。"""
    with pytest.raises(ValidationError) as exc:
        Itinerary(
            summary="x",
            nodes=[
                _poi_node("n0", "14:00", duration=30),  # 首节点 poi 而非 home
                _home_node("n1", "15:00"),
            ],
            hops=[_hop("h0", "n0", "n1")],
            total_minutes=60,
        )
    assert "nodes[0] 必须是 home" in str(exc.value)


def test_invariant_last_node_must_be_home():
    """尾节点不是 home → ValidationError。"""
    with pytest.raises(ValidationError) as exc:
        Itinerary(
            summary="x",
            nodes=[
                _home_node("n0", "14:00"),
                _poi_node("n1", "14:30", duration=30),  # 尾节点 poi 而非 home
            ],
            hops=[_hop("h0", "n0", "n1")],
            total_minutes=60,
        )
    assert "nodes[-1] 必须是 home" in str(exc.value)


# ============================================================
# 4. 不变量 4：home 节点 duration_min = 0
# ============================================================


def test_invariant_home_node_duration_must_be_zero_first():
    """首节点 home 但 duration_min > 0 → ValidationError。"""
    bad_home = ActivityNode(
        node_id="n0",
        kind="出发",
        target_kind="home",
        target_id="home",
        start_time="14:00",
        duration_min=15,  # ← 应为 0
        title="家",
    )
    with pytest.raises(ValidationError) as exc:
        Itinerary(
            summary="x",
            nodes=[bad_home, _home_node("n1", "14:30")],
            hops=[_hop("h0", "n0", "n1")],
            total_minutes=30,
        )
    assert "duration_min 必须为 0" in str(exc.value)


def test_invariant_home_node_duration_must_be_zero_last():
    """尾节点 home 但 duration_min > 0 → ValidationError。"""
    bad_home = ActivityNode(
        node_id="n1",
        kind="返回",
        target_kind="home",
        target_id="home",
        start_time="14:30",
        duration_min=10,  # ← 应为 0
        title="家",
    )
    with pytest.raises(ValidationError) as exc:
        Itinerary(
            summary="x",
            nodes=[_home_node("n0", "14:00"), bad_home],
            hops=[_hop("h0", "n0", "n1")],
            total_minutes=30,
        )
    assert "duration_min 必须为 0" in str(exc.value)


# ============================================================
# 5. 不变量 5：home 节点 target_id 固定 "home"
# ============================================================


def test_invariant_home_target_id_must_be_literal_home():
    """home 节点 target_id 不是 "home" → ValidationError。"""
    bad_home = ActivityNode(
        node_id="n0",
        kind="出发",
        target_kind="home",
        target_id="user_001_home",  # ← 应为 "home"
        start_time="14:00",
        duration_min=0,
        title="家",
    )
    with pytest.raises(ValidationError) as exc:
        Itinerary(
            summary="x",
            nodes=[bad_home, _home_node("n1", "14:30")],
            hops=[_hop("h0", "n0", "n1")],
            total_minutes=30,
        )
    assert 'target_id 必须为 "home"' in str(exc.value)


# ============================================================
# 6. OrderRecord.target_kind 字段
# ============================================================


def test_order_record_with_target_kind_restaurant():
    """OrderRecord 带 target_kind="restaurant" 校验通过。"""
    order = OrderRecord(
        order_id="R20260507_001",
        kind="餐厅预约",
        target_kind="restaurant",
        target_id="R001",
        target_name="老头儿油爆虾",
        detail="17:00 三人位",
    )
    assert order.target_kind == "restaurant"


def test_order_record_with_target_kind_poi():
    """OrderRecord 带 target_kind="poi" 校验通过。"""
    order = OrderRecord(
        order_id="P20260507_001",
        kind="门票",
        target_kind="poi",
        target_id="P001",
        target_name="森林儿童探索乐园",
        detail="3 张成人票",
    )
    assert order.target_kind == "poi"


def test_order_record_target_kind_rejects_home():
    """OrderRecord.target_kind 不接受 "home"（订单必须挂在真实业务实体上）。"""
    with pytest.raises(ValidationError):
        OrderRecord(
            order_id="X001",
            kind="餐厅预约",
            target_kind="home",  # type: ignore[arg-type]
            target_id="home",
            target_name="家",
            detail="x",
        )


def test_order_record_target_kind_required():
    """OrderRecord 必须显式提供 target_kind（无默认值）。"""
    with pytest.raises(ValidationError):
        OrderRecord(  # type: ignore[call-arg]
            order_id="R001",
            kind="餐厅预约",
            target_id="R001",
            target_name="x",
            detail="x",
        )
