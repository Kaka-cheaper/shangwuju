"""test_confirm_actions —— 工具前移：规划期生成确认动作清单 + confirm 期 replay。

核心验证（spec dialogue-act-routing 工具前移）：
- build_confirm_actions 在规划期把 confirm 要调的工具 + 参数算全（人数/加购/餐厅）。
- execute_finalize_node **有 pending_actions + intent=None 时仍能 replay 出订单**——
  这就是 ReAct 断点的修复证据：动作规划期锁死，confirm 不再依赖 intent。
"""

from __future__ import annotations

from agent.graph.nodes.execute_finalize import (
    build_confirm_actions,
    execute_finalize_node,
)
from schemas.intent import Companion, IntentExtraction
from schemas.itinerary import ActivityNode, Hop, Itinerary


def _itin() -> Itinerary:
    nodes = [
        ActivityNode(node_id="n0", kind="出发", target_kind="home", target_id="home",
                     start_time="14:00", duration_min=0, title="家"),
        ActivityNode(node_id="n1", kind="用餐", target_kind="restaurant", target_id="R001",
                     start_time="17:30", duration_min=60, title="轻语沙拉", note="17:30 三人位"),
        ActivityNode(node_id="n2", kind="返程", target_kind="home", target_id="home",
                     start_time="19:00", duration_min=0, title="家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="14:00",
            minutes=10, mode="taxi", path_type="estimated"),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="18:30",
            minutes=10, mode="taxi", path_type="estimated"),
    ]
    return Itinerary(summary="测试方案", nodes=nodes, hops=hops, total_minutes=180)


def _intent(extra: list[str] | None = None) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon", duration_hours=[3, 5], distance_max_km=5,
        companions=[Companion(role="妻子", count=1), Companion(role="孩子", age=5, count=1)],
        physical_constraints=[], dietary_constraints=[], experience_tags=[],
        social_context="家庭日常", raw_input="x", parse_confidence=0.9,
        extra_services=extra or [],
    )


# ---- build_confirm_actions：规划期算全 ----

def test_build_actions_reserve_and_share():
    actions = build_confirm_actions(_itin(), _intent())
    tools = [a.tool for a in actions]
    assert "reserve_restaurant" in tools
    assert "generate_share_message" in tools
    reserve = next(a for a in actions if a.tool == "reserve_restaurant")
    assert reserve.args["restaurant_id"] == "R001"
    assert reserve.args["party_size"] == 3  # 妻子1 + 孩子1 + 本人1


def test_build_actions_includes_extra_service():
    actions = build_confirm_actions(_itin(), _intent(extra=["蛋糕"]))
    assert any(a.tool == "order_extra_service" for a in actions)


def test_build_actions_intent_none_degrades():
    # intent 缺省：餐厅 / 文案照出，无加购，人数取默认
    actions = build_confirm_actions(_itin(), None)
    tools = [a.tool for a in actions]
    assert "reserve_restaurant" in tools and "generate_share_message" in tools
    assert not any(a.tool == "order_extra_service" for a in actions)


# ---- execute_finalize replay：断点修复核心 ----

def test_execute_finalize_replays_without_intent():
    """itinerary 有 pending_actions + intent=None → 仍 replay 出订单（ReAct 断点修复）。"""
    itin = _itin()
    itin = itin.model_copy(
        update={"pending_actions": build_confirm_actions(itin, _intent(extra=["蛋糕"]))}
    )
    out = execute_finalize_node({"itinerary": itin, "intent": None})
    assert out, "intent=None 不该再返回空（旧逻辑会 return {}）"
    tools_called = [r["tool"] for r in out["execution_tool_results"]]
    # 加购被锁进规划期清单，intent 空也照做——这就是工具前移的价值
    assert "reserve_restaurant" in tools_called
    assert "order_extra_service" in tools_called
    assert any(o.kind == "餐厅预约" for o in out["orders"])


def test_execute_finalize_no_pending_falls_back_to_build():
    """没 pending_actions（旧方案）+ 有 intent → 现算 build 再 replay（向后兼容）。"""
    out = execute_finalize_node({"itinerary": _itin(), "intent": _intent()})
    assert out
    assert any(o.kind == "餐厅预约" for o in out["orders"])


def test_execute_finalize_no_itinerary_returns_empty():
    assert execute_finalize_node({"itinerary": None, "intent": None}) == {}
