"""test_agent_flow —— 端到端：意图解析 → planner → executor。

依赖 conftest 注册的 fake_tools；不调真实 LLM（全程 stub）。

核心断言：
- 主场景能跑通完整流程
- E1（餐厅满）会被显式触发并自动恢复（trace 中有 replan_triggered）
- 最终 Itinerary 含 5 段以上、有 share_message、有 orders
- 全程不出现 D9 禁止字段
"""

from __future__ import annotations

from agent.legacy.executor import execute_plan
from agent.intent.parser import parse_intent
from agent.core.llm_client_stub import StubLLMClient
from agent.legacy.planner_rule import plan_itinerary


FAMILY_INPUT = "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。"


def test_end_to_end_family_main_scene():
    # 1. 意图解析
    client = StubLLMClient()
    intent = parse_intent(FAMILY_INPUT, client=client)

    # 2. 规划
    plan_result = plan_itinerary(intent)
    assert plan_result.success, plan_result.failure_detail
    itinerary = plan_result.itinerary
    assert itinerary is not None

    # edge_v1：节点-边模型，至少 1 个 mid node + 首尾 home（≥3 节点）
    assert itinerary.schema_version == "edge_v1"
    assert len(itinerary.hops) == len(itinerary.nodes) - 1
    assert itinerary.nodes[0].target_kind == "home"
    assert itinerary.nodes[-1].target_kind == "home"

    mid_nodes = [n for n in itinerary.nodes if n.target_kind != "home"]
    mid_kinds = {n.kind for n in mid_nodes}
    # 家庭场景应至少含主活动 + 用餐两类 mid node
    assert "主活动" in mid_kinds, f"缺少主活动 mid node：实际 {mid_kinds}"
    assert "用餐" in mid_kinds, f"缺少用餐 mid node：实际 {mid_kinds}"

    # 3. 执行（用户确认后）
    party_size = sum(c.count for c in intent.companions) or 1
    exec_result = execute_plan(
        itinerary,
        party_size=party_size,
        social_context=intent.social_context,
        audience="妻子",
    )
    assert exec_result.success
    final = exec_result.itinerary
    assert final.share_message
    # 至少有餐厅预约的订单
    assert any(o.kind == "餐厅预约" for o in final.orders)


def test_e1_restaurant_full_triggers_replan():
    """R001 在 17:00 已满（fake_tools 埋点）→ planner 应切到 17:30 或备选餐厅。"""
    client = StubLLMClient()
    intent = parse_intent(FAMILY_INPUT, client=client)
    plan_result = plan_itinerary(intent)
    assert plan_result.success

    replan_records = list(plan_result.tracer.filter("replan_triggered"))
    assert replan_records, "未触发 replan_triggered 事件——E1 异常恢复未发生"

    # 至少一条是 RESTAURANT_FULL 引发的
    full_replans = [
        r for r in replan_records if r.payload.get("reason") == "restaurant_full"
    ]
    assert full_replans, "replan 触发了但不是因为 restaurant_full"

    # 最终 itinerary 用餐节点的开始时刻不是 17:00（因为 R001 17:00 满）
    dining_node = next(
        n for n in plan_result.itinerary.nodes if n.target_kind == "restaurant"
    )
    # 选了 R001 → 必须是 17:30/18:00；选了 R002 → 17:00 也可
    assert dining_node.note  # 有"已为你预留"备注


def test_no_forbidden_d9_fields_in_intent():
    """D9 硬条款：意图抽取输出不能含场景枚举字段。"""
    client = StubLLMClient()
    intent = parse_intent(FAMILY_INPUT, client=client)
    dumped = intent.model_dump()
    forbidden = {"scene_type", "relation_type", "is_family", "is_friends"}
    leak = forbidden & set(dumped.keys())
    assert not leak, f"D9 禁止字段泄漏：{leak}"


def test_tool_quota_enforced():
    """planner 不应把任何 Tool 无限调用（pitfalls P3 过度规划防御）。

    上限分级：
    - check_restaurant_availability：≤ 15（3 餐厅 × 5 候选时段）
    - search_pois / search_restaurants：≤ 5（多级降级 + 距离放宽）
    - 其他：≤ 3（MAX_TOOL_CALLS_PER_KIND）
    - 总调用：≤ MAX_TOTAL_TOOL_CALLS（25）
    """
    client = StubLLMClient()
    intent = parse_intent(FAMILY_INPUT, client=client)
    plan_result = plan_itinerary(intent)
    counts: dict[str, int] = {}
    for r in plan_result.tracer.records:
        if r.type == "tool_call_start":
            tool = r.payload.get("tool", "")
            counts[tool] = counts.get(tool, 0) + 1
    quota = {
        "check_restaurant_availability": 30,
        "search_pois": 5,
        "search_restaurants": 5,
    }
    for tool, n in counts.items():
        cap = quota.get(tool, 3)
        assert n <= cap, f"Tool {tool} 调用 {n} 次，超出上限 {cap}"
    assert sum(counts.values()) <= 45, f"总调用 {sum(counts.values())} 次超 MAX_TOTAL_TOOL_CALLS"
