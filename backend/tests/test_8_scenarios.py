"""test_8_scenarios —— 8 个开放场景端到端集成测试（D9 + 演示场景集 §四）。

为什么不用 stub LLM 跑意图解析：
- StubLLMClient 只对家庭主场景返家庭 fixture，其他输入也固定返家庭 → 不能验证 8 场景多样性
- 题目核心在「Tool/Agent 对场景类型无感」（D9）：本测试**直接构造 IntentExtraction 喂 planner**，
  从而验证「同一套 planner + 同一套 mock 数据 + 不同约束 → 输出场景调性匹配的方案」
- 真 LLM 抽取的鲁棒性放在 A4 真链路验证（需要 DEEPSEEK_API_KEY，不在本测试范围）

每个场景断言：
- planner 端到端跑通 success=True，输出 Itinerary ≥5 段
- 主活动 POI / 用餐餐厅的 suitable_for 含场景对应 social_context（场景调性匹配）
- E1 / E2 异常分支：S1 显式触发 E1（家庭路径中 R001 17:00 满）；E2 暂未实现（buy_ticket 不在 MVP-1）

E2 测试用 xfail（C 同学未实现 buy_ticket Tool，按 W1 任务清单边界这是 C 后续的活）。

Tool 注册由 conftest.py 自动 bootstrap；mock 数据走仓库根 mock_data/。
"""

from __future__ import annotations

import pytest

from agent.legacy.executor import execute_plan
from agent.legacy.planner_rule import plan_itinerary
from schemas.intent import Companion, IntentExtraction
from schemas.itinerary import Itinerary


# ============================================================
# 8 场景输入（直接构造 IntentExtraction，绕过 stub LLM）
# 严格按 docs/01-requirements/演示场景集.md §三 期待抽取
# ============================================================

INTENTS: dict[str, IntentExtraction] = {
    "S1": IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[
            Companion(role="妻子", count=1),
            Companion(role="孩子", age=5, count=1),
        ],
        physical_constraints=["亲子友好", "适合 5-10 岁"],
        dietary_constraints=["低脂", "健康轻食"],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥。",
        parse_confidence=0.92,
    ),
    "S2": IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[
            Companion(role="朋友", count=4, gender_mix="2男2女"),
        ],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=["社交", "拍照友好"],
        social_context="朋友热闹",
        capacity_requirement=4,
        raw_input="今天下午想和朋友出去玩几小时，4 个人 2 男 2 女，别离家太远。",
        parse_confidence=0.88,
    ),
    "S3": IntentExtraction(
        start_time="sunday_afternoon",
        start_weekday="sunday",
        duration_hours=[4, 6],
        distance_max_km=5,
        companions=[Companion(role="女朋友", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=["看展", "安静聊天"],
        social_context="情侣亲密",
        preferred_poi_types=["展览", "美术馆"],
        raw_input="周日下午带着女朋友去看个展，顺便找个安静能聊天的地方吃饭。",
        parse_confidence=0.85,
    ),
    "S4": IntentExtraction(
        start_time="sunday_afternoon",
        start_weekday="sunday",
        duration_hours=[3, 5],
        distance_max_km=3,
        companions=[
            Companion(role="外公", count=1, is_special_role=True),
            Companion(role="外婆", count=1, is_special_role=True),
        ],
        physical_constraints=["适合老人", "无台阶", "可休息"],
        dietary_constraints=["软烂"],
        experience_tags=[],
        social_context="老人伴助",
        raw_input="周日下午想带外公外婆出去走走，别走太远他们腿不好。",
        parse_confidence=0.88,
    ),
    "S5": IntentExtraction(
        start_time="weekend_afternoon",
        duration_hours=[3, 4],
        distance_max_km=5,
        companions=[Companion(role="闺蜜", count=1)],
        physical_constraints=[],
        dietary_constraints=["下午茶", "甜品"],
        experience_tags=["网红打卡", "拍照友好"],
        social_context="闺蜜聊天",
        raw_input="周末下午约了闺蜜想找个网红的地方拍拍照吃个下午茶。",
        parse_confidence=0.86,
    ),
    "S6": IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[Companion(role="商务客户", count=1, is_special_role=True)],
        physical_constraints=[],
        dietary_constraints=["高人均", "有包间"],
        experience_tags=["商务体面", "礼仪感"],
        social_context="商务接待",
        raw_input="下午临时被叫去接个外地客户，对方是商务人士，帮我安排下。",
        parse_confidence=0.82,
    ),
    "S7": IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[2, 4],
        distance_max_km=5,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=["独处舒缓"],
        social_context="独处放空",
        raw_input="这周加班加得想吐，下午想一个人安安静静待几个小时再回家。",
        parse_confidence=0.80,
    ),
    "S8": IntentExtraction(
        start_time="sunday_lunch",
        start_weekday="sunday",
        duration_hours=[3, 4],
        distance_max_km=5,
        companions=[
            Companion(role="母亲", count=1, is_birthday=True, is_special_role=True),
            Companion(role="全家", count=6),
        ],
        physical_constraints=["适合老人"],
        dietary_constraints=["粤菜"],
        experience_tags=["礼仪感"],
        social_context="纪念日仪式感",
        capacity_requirement=6,
        extra_services=["蛋糕"],
        raw_input="周日是我妈生日，全家 6 个人想一起出去吃顿好的，她想吃粤菜。",
        parse_confidence=0.84,
    ),
}


# ============================================================
# 主路径：8 场景全部跑通且输出场景调性匹配
# ============================================================

@pytest.mark.parametrize("scenario_id", list(INTENTS.keys()))
def test_scenario_end_to_end(scenario_id: str):
    """每个场景能跑通完整 planner 流程，输出 ≥5 段且场景调性匹配。"""
    intent = INTENTS[scenario_id]

    plan_result = plan_itinerary(intent)
    assert plan_result.success, (
        f"场景 {scenario_id} planner 失败：{plan_result.failure_detail}"
    )

    itinerary = plan_result.itinerary
    assert isinstance(itinerary, Itinerary)

    # edge_v1 不变量自检（assemble 已强校验，此处冗余兜底）
    assert itinerary.schema_version == "edge_v1"
    assert len(itinerary.hops) == len(itinerary.nodes) - 1

    # Phase 0.10（pitfalls P1-2026-05-17）→ edge_v1（Wave 7 Task 14）：
    # 中间节点按 decide_nodes 决定，不再硬要 5 段；首尾 home 由 assemble 自动补
    from agent.planning.blueprint.node_decider import decide_nodes
    expected_kinds = decide_nodes(intent)
    mid_nodes = [n for n in itinerary.nodes if n.target_kind != "home"]
    mid_kinds = [n.kind for n in mid_nodes]
    assert len(mid_nodes) >= len(expected_kinds), (
        f"场景 {scenario_id} 中间节点数不足：实际 {len(mid_nodes)}，"
        f"按 intent 应有 {len(expected_kinds)} 个（{expected_kinds}）"
    )
    for required in expected_kinds:
        assert required in mid_kinds, (
            f"场景 {scenario_id} 缺少中间节点 kind：{required}（实际 mid_kinds={mid_kinds}）"
        )

    # 总时长按 intent 与节点数判：节点越多下限越高
    # 2 mid nodes（含用餐）→ ≥120；1 mid node（独处仅去 POI）→ ≥60
    min_floor = 60 if len(expected_kinds) <= 1 else 120
    assert min_floor <= itinerary.total_minutes <= 600, (
        f"场景 {scenario_id} 总时长越界：{itinerary.total_minutes} 分钟"
        f"（按 {len(expected_kinds)} 中间节点下限 {min_floor}）"
    )


def test_d9_no_scene_type_in_intent_dump():
    """D9 硬条款反向校验：所有场景的意图 dump 都不含枚举型场景字段。"""
    forbidden = {"scene_type", "relation_type", "is_family", "is_friends"}
    for scenario_id, intent in INTENTS.items():
        dumped = set(intent.model_dump().keys())
        leak = forbidden & dumped
        assert not leak, f"场景 {scenario_id} 出现 D9 禁止字段：{leak}"


# ============================================================
# 主路径：场景调性匹配（POI/餐厅 suitable_for 含对应 social_context）
# ============================================================

# 因 mock 数据 suitable_for 标注与场景的对应关系
SCENE_TO_CONTEXT = {
    "S1": "家庭日常",
    "S2": "朋友热闹",
    "S3": "情侣亲密",
    "S4": "老人伴助",
    "S5": "闺蜜聊天",
    "S6": "商务接待",
    "S7": "独处放空",
    "S8": "纪念日仪式感",
}


@pytest.mark.parametrize("scenario_id", list(INTENTS.keys()))
def test_scenario_tone_match(scenario_id: str):
    """主活动 POI 与用餐餐厅的 suitable_for 应包含场景的 social_context（调性匹配）。

    这是 D9 的核心验证：同一套 Tool / Agent，跨场景全靠 suitable_for tag 命中——
    不是 Tool 内部的 if-else 分支。
    """
    from data.loader import load_pois, load_restaurants

    intent = INTENTS[scenario_id]
    context = SCENE_TO_CONTEXT[scenario_id]
    plan_result = plan_itinerary(intent)
    assert plan_result.success
    itinerary = plan_result.itinerary

    # 主活动 POI（edge_v1：通过 target_kind="poi" 找 mid node）
    main_node = next(
        (n for n in itinerary.nodes if n.target_kind == "poi"), None
    )
    if main_node and main_node.target_id:
        poi = next((p for p in load_pois() if p.id == main_node.target_id), None)
        assert poi is not None
        assert context in poi.suitable_for, (
            f"场景 {scenario_id} 主活动 POI {poi.id} 不适配 {context}：suitable_for={poi.suitable_for}"
        )

    # 用餐餐厅（仅当 decide_nodes 决定有用餐节点时检查；S7 独处放空可能无用餐）
    dining_node = next(
        (n for n in itinerary.nodes if n.target_kind == "restaurant"), None
    )
    if dining_node and dining_node.target_id:
        rest = next((r for r in load_restaurants() if r.id == dining_node.target_id), None)
        assert rest is not None
        assert context in rest.suitable_for, (
            f"场景 {scenario_id} 餐厅 {rest.id} 不适配 {context}：suitable_for={rest.suitable_for}"
        )


# ============================================================
# 异常分支：E1 显式触发与恢复
# ============================================================

def test_e1_restaurant_full_recovery_in_family_scene():
    """S1 家庭场景 mock 中 R001 17:00 已满 → planner 应至少触发 1 次 replan_triggered
    且 reason=restaurant_full，最终方案能跑通。"""
    intent = INTENTS["S1"]
    plan_result = plan_itinerary(intent)
    assert plan_result.success

    replans = [
        r
        for r in plan_result.tracer.records
        if r.type == "replan_triggered"
        and r.payload.get("reason") == "restaurant_full"
    ]
    assert replans, (
        "S1 家庭场景未触发 E1（restaurant_full）—— mock R001 17:00 埋点失效或 planner 路径异常"
    )


def test_executor_reservation_filled_after_plan():
    """S1 主流程：planner → execute_plan，应生成餐厅预约订单 + 转发文案。"""
    intent = INTENTS["S1"]
    plan_result = plan_itinerary(intent)
    assert plan_result.success
    party_size = sum(c.count for c in intent.companions) or 1
    exec_result = execute_plan(
        plan_result.itinerary,
        party_size=party_size,
        social_context=intent.social_context,
        audience="妻子",
    )
    assert exec_result.success
    final = exec_result.itinerary
    assert any(o.kind == "餐厅预约" for o in final.orders)
    assert final.share_message


# ============================================================
# 异常分支：E2 门票售罄
# ============================================================

def test_e2_ticket_sold_out_recovery():
    """E2 门票售罄异常分支：buy_ticket(P_SOLD) → success=false + reason=ticket_sold_out。

    依赖 C 同学的 buy_ticket Tool 实现 + mock 中 P_SOLD 售罄埋点（capacity.available_slots=0）。
    """
    from tools.registry import TOOL_REGISTRY

    assert "buy_ticket" in TOOL_REGISTRY, "buy_ticket Tool 未注册"

    from schemas.tools import BuyTicketInput
    from tools.registry import invoke_tool

    result = invoke_tool("buy_ticket", BuyTicketInput(poi_id="P_SOLD", quantity=2).model_dump())
    assert not result.success
    assert result.reason and result.reason.value == "ticket_sold_out", (
        f"P_SOLD 应触发 ticket_sold_out，实际 reason={result.reason}"
    )
