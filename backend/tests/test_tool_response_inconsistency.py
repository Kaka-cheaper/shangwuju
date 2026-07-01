"""spec algorithm-redesign R2：TOOL_RESPONSE_INCONSISTENCY hallucination 防护单测。

测试覆盖：
- 编造 POI ID 触发 TOOL_RESPONSE_INCONSISTENCY
- 编造 Restaurant ID 触发
- 真实 ID 不触发
- tool_results=None 时跳过（向后兼容）
- target_kind="home" 不检查
- 多个幻觉 ID 全部捕获
- 候选池为空时跳过（stub mode 防误报）
- target_kind=poi 节点 + 餐厅候选池有但 POI 候选池空 → 不报（设计纪律）

不消费真 LLM；不依赖 mock_data；纯单元测试。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# 复用 test_critics_v2 的过渡态桥
if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


from agent.planning.critic.critics_v2 import (  # noqa: E402
    Severity,
    ViolationCode,
    _check_tool_consistency,
    validate_itinerary,
)
from tests.test_critics_v2 import (  # noqa: E402
    _make_intent,
    _make_legal_itinerary,
)


# ============================================================
# Mock 候选 POI / Restaurant（最小有效对象）
# ============================================================


class _FakePoi:
    """只需 .id 字段；_check_tool_consistency 用 getattr 取 id"""

    def __init__(self, poi_id: str):
        self.id = poi_id


class _FakeRestaurant:
    def __init__(self, rest_id: str):
        self.id = rest_id


# ============================================================
# 测试 1：编造 POI ID 触发违规
# ============================================================


def test_hallucinated_poi_id_triggers_violation():
    """itinerary 含 P999（编造），候选池只有 P040 → CRITICAL TOOL_RESPONSE_INCONSISTENCY"""
    itinerary = _make_legal_itinerary(poi_id="P999")  # 编造
    tool_results = {
        "pois": [_FakePoi("P040"), _FakePoi("P033")],  # 真实候选
        "restaurants": [_FakeRestaurant("R001")],
    }
    violations = _check_tool_consistency(itinerary, tool_results)
    assert len(violations) == 1
    v = violations[0]
    assert v.code == ViolationCode.TOOL_RESPONSE_INCONSISTENCY
    assert v.severity == Severity.HARD
    assert "P999" in v.message
    assert "候选池" in v.message
    assert "编造" in v.message or "重新规划" in v.message
    # 设计纪律：不暴露 dot-path 字段名给 LLM
    assert "target_id" not in v.message
    assert "nodes[" not in v.message


def test_hallucinated_restaurant_id_triggers_violation():
    """itinerary 含 R999（编造），候选池只有 R001"""
    itinerary = _make_legal_itinerary(restaurant_id="R999")
    tool_results = {
        "pois": [_FakePoi("P040")],
        "restaurants": [_FakeRestaurant("R001"), _FakeRestaurant("R024")],
    }
    violations = _check_tool_consistency(itinerary, tool_results)
    assert len(violations) == 1
    v = violations[0]
    assert v.code == ViolationCode.TOOL_RESPONSE_INCONSISTENCY
    assert "R999" in v.message
    assert "餐厅" in v.message


def test_real_ids_do_not_trigger():
    """itinerary 全部 target_id 都在候选池里 → 0 violation"""
    itinerary = _make_legal_itinerary(poi_id="P040", restaurant_id="R001")
    tool_results = {
        "pois": [_FakePoi("P040"), _FakePoi("P033")],
        "restaurants": [_FakeRestaurant("R001"), _FakeRestaurant("R024")],
    }
    violations = _check_tool_consistency(itinerary, tool_results)
    assert violations == []


def test_tool_results_none_skips_check():
    """tool_results=None → 直接返空（向后兼容旧调用）"""
    itinerary = _make_legal_itinerary(poi_id="P999")  # 即使是编造的
    violations = _check_tool_consistency(itinerary, None)
    assert violations == []


def test_home_nodes_not_checked():
    """target_kind=home 节点跳过——home 不来自工具，不可能 hallucination

    legal itinerary 首尾两个 home 节点 target_id="home"，候选池里没有 "home"
    但这是合法的，不应触发违规。
    """
    itinerary = _make_legal_itinerary(poi_id="P040", restaurant_id="R001")
    tool_results = {
        "pois": [_FakePoi("P040")],
        "restaurants": [_FakeRestaurant("R001")],
    }
    violations = _check_tool_consistency(itinerary, tool_results)
    # home 节点 target_id="home" 不在候选池但应被跳过；POI/Restaurant 真实 → 0
    assert violations == []


def test_multiple_hallucinated_ids_all_caught():
    """同一行程内 POI + Restaurant 都是编造 → 两条违规"""
    itinerary = _make_legal_itinerary(poi_id="P999", restaurant_id="R888")
    tool_results = {
        "pois": [_FakePoi("P040")],
        "restaurants": [_FakeRestaurant("R001")],
    }
    violations = _check_tool_consistency(itinerary, tool_results)
    assert len(violations) == 2
    codes = [v.code for v in violations]
    assert all(c == ViolationCode.TOOL_RESPONSE_INCONSISTENCY for c in codes)
    msgs = " ".join(v.message for v in violations)
    assert "P999" in msgs and "R888" in msgs


def test_empty_candidate_pools_skip_check():
    """候选池为空（stub mode / 候选耗尽场景） → 跳过避免误报"""
    itinerary = _make_legal_itinerary(poi_id="P999", restaurant_id="R888")
    # 两个候选池都为空 → stub 或退化场景
    tool_results = {"pois": [], "restaurants": []}
    violations = _check_tool_consistency(itinerary, tool_results)
    assert violations == []


def test_partial_pool_only_one_kind_checked():
    """POI 候选池为空但餐厅候选池有 → 餐厅节点检查，POI 节点跳过

    场景：execute 阶段并行查 POI 失败但餐厅成功 → 不报 POI hallucination
    避免误判（POI 候选为空不代表 LLM 在编造）
    """
    itinerary = _make_legal_itinerary(poi_id="P999", restaurant_id="R001")
    tool_results = {
        "pois": [],  # POI 候选池为空
        "restaurants": [_FakeRestaurant("R001")],  # 餐厅候选池有
    }
    violations = _check_tool_consistency(itinerary, tool_results)
    # POI 候选为空 → P999 跳过；R001 真实 → 不报；总 0 条
    assert violations == []


# ============================================================
# 测试：与 validate_itinerary 集成
# ============================================================


def test_validate_itinerary_with_tool_results_includes_inconsistency_check():
    """validate_itinerary 透传 tool_results 参数 → critic 链路完整跑通"""
    intent = _make_intent()
    itinerary = _make_legal_itinerary(poi_id="P999")
    tool_results = {
        "pois": [_FakePoi("P040")],
        "restaurants": [_FakeRestaurant("R001")],
    }
    violations = validate_itinerary(itinerary, intent, tool_results=tool_results)
    inconsistency = [
        v for v in violations if v.code == ViolationCode.TOOL_RESPONSE_INCONSISTENCY
    ]
    assert len(inconsistency) == 1


def test_validate_itinerary_without_tool_results_backward_compatible():
    """不传 tool_results → 不破已有 spec A/B 的 470+ 项测试

    向后兼容硬约束：spec C task 3 引入 tool_results 参数后，
    所有不传此参数的旧调用必须依然返一致结果（不含 TOOL_RESPONSE_INCONSISTENCY）。
    """
    intent = _make_intent()
    itinerary = _make_legal_itinerary(poi_id="P040")
    violations = validate_itinerary(itinerary, intent)  # 不传 tool_results
    inconsistency = [
        v for v in violations if v.code == ViolationCode.TOOL_RESPONSE_INCONSISTENCY
    ]
    assert inconsistency == []
