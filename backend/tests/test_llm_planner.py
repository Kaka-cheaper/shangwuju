"""test_llm_planner —— LLM 自主规划测试（用 stub 客户端 + 双 mode 一致性）。

策略：
- StubLLMClient.chat_with_tools 返 finish_reason=stop 且无 tool_calls
  → llm_planner state 不齐 → 自动 fallback 到 plan_itinerary（rule 范式）
- 验证 fallback 后 PlannerResult 与直接调 plan_itinerary 输出等价（行程段数 / 主活动 / 餐厅）

关键测试目标：
1. fallback 链路必然成功（Demo 安全网）
2. plan_itinerary_with_mode 三种入参（rule/llm/非法）都能跑出方案
3. 8 场景在两种 mode 下都不崩
"""

from __future__ import annotations

import pytest

from agent import plan_itinerary, plan_itinerary_with_mode
from agent.llm_client_stub import StubLLMClient
from agent.llm_planner import plan_itinerary_llm
from schemas.intent import Companion, IntentExtraction


def _family_intent() -> IntentExtraction:
    return IntentExtraction(
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
        raw_input="今天下午带老婆孩子",
        parse_confidence=0.92,
    )


# ============================================================
# Case 1: stub LLM 必须 fallback 成功（不返 tool_calls）
# ============================================================

def test_llm_planner_fallback_to_rule_with_stub():
    """StubLLMClient.chat_with_tools 不返 tool_calls，state 不齐 → 必走 fallback。"""
    intent = _family_intent()
    result = plan_itinerary_llm(intent, client=StubLLMClient())
    assert result.success
    assert result.itinerary is not None
    # Phase 0.10：家庭场景应得 5 段（segment_decider 默认）
    from agent.segment_decider import decide_segments
    expected = decide_segments(intent)
    assert len(result.itinerary.stages) >= len(expected)

    # 必须留下 fallback 提示事件
    thoughts = [r for r in result.tracer.records if r.type == "agent_thought"]
    fallback_thoughts = [t for t in thoughts if "规则" in t.payload.get("text", "")]
    assert fallback_thoughts, "fallback 应推 agent_thought 提示"


# ============================================================
# Case 2: rule mode vs llm mode 在主场景下输出一致（fallback 后等价）
# ============================================================

def test_rule_vs_llm_mode_same_main_poi_and_restaurant():
    """rule mode 与 llm mode（fallback 后）应当选同样的主 POI 和用餐餐厅。"""
    intent = _family_intent()
    rule_result = plan_itinerary(intent)
    llm_result = plan_itinerary_with_mode(intent, "llm", llm_client=StubLLMClient())

    assert rule_result.success and llm_result.success

    rule_main = next((s for s in rule_result.itinerary.stages if s.kind == "主活动"), None)
    llm_main = next((s for s in llm_result.itinerary.stages if s.kind == "主活动"), None)
    assert rule_main and llm_main
    assert rule_main.poi_id == llm_main.poi_id

    rule_dining = next((s for s in rule_result.itinerary.stages if s.kind == "用餐"), None)
    llm_dining = next((s for s in llm_result.itinerary.stages if s.kind == "用餐"), None)
    assert rule_dining and llm_dining
    assert rule_dining.restaurant_id == llm_dining.restaurant_id


# ============================================================
# Case 3: plan_itinerary_with_mode 入参兜底
# ============================================================

@pytest.mark.parametrize("mode", ["rule", "llm", "hack", "", None])
def test_plan_itinerary_with_mode_param_robust(mode):
    """任意 mode 入参都不应崩；非法值回 rule。"""
    intent = _family_intent()
    result = plan_itinerary_with_mode(intent, mode, llm_client=StubLLMClient())
    assert result.success
    assert result.itinerary is not None


# ============================================================
# Case 4: 8 场景在 llm mode 下都能产出（fallback 也算）
# ============================================================

# 6 个跨场景代表（避免与 test_8_scenarios.py 完全重复，只覆盖 D9 多样性）
_SCENE_PAYLOADS = [
    # 朋友
    {"distance_max_km": 5, "companions": [Companion(role="朋友", count=4, gender_mix="2男2女")],
     "experience_tags": ["社交", "拍照友好"], "social_context": "朋友热闹",
     "capacity_requirement": 4, "raw_input": "和朋友 4 人"},
    # 情侣
    {"distance_max_km": 5, "companions": [Companion(role="女朋友", count=1)],
     "experience_tags": ["看展", "安静聊天"], "social_context": "情侣亲密",
     "preferred_poi_types": ["展览", "美术馆"], "raw_input": "和女友看展"},
    # 老人
    {"distance_max_km": 3, "companions": [Companion(role="外公", count=1, is_special_role=True),
                                          Companion(role="外婆", count=1, is_special_role=True)],
     "physical_constraints": ["适合老人", "无台阶"], "dietary_constraints": ["软烂"],
     "social_context": "老人伴助", "raw_input": "带外公外婆"},
    # 商务
    {"distance_max_km": 5, "companions": [Companion(role="商务客户", count=1, is_special_role=True)],
     "dietary_constraints": ["高人均", "有包间"], "experience_tags": ["商务体面", "礼仪感"],
     "social_context": "商务接待", "raw_input": "接客户"},
    # 闺蜜
    {"distance_max_km": 5, "companions": [Companion(role="闺蜜", count=1)],
     "dietary_constraints": ["下午茶", "甜品"], "experience_tags": ["网红打卡", "拍照友好"],
     "social_context": "闺蜜聊天", "raw_input": "和闺蜜下午茶"},
    # 独处
    {"distance_max_km": 5, "companions": [], "experience_tags": ["独处舒缓"],
     "social_context": "独处放空", "raw_input": "一个人"},
]


@pytest.mark.parametrize("payload", _SCENE_PAYLOADS)
def test_llm_mode_handles_all_scenes_via_fallback(payload):
    intent = IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        physical_constraints=payload.get("physical_constraints", []),
        dietary_constraints=payload.get("dietary_constraints", []),
        experience_tags=payload.get("experience_tags", []),
        social_context=payload["social_context"],
        distance_max_km=payload["distance_max_km"],
        companions=payload["companions"],
        capacity_requirement=payload.get("capacity_requirement"),
        preferred_poi_types=payload.get("preferred_poi_types", []),
        raw_input=payload["raw_input"],
        parse_confidence=0.85,
    )
    result = plan_itinerary_with_mode(intent, "llm", llm_client=StubLLMClient())
    assert result.success, f"场景 {payload['social_context']} 失败：{result.failure_detail}"
    assert result.itinerary is not None
    # Phase 0.10：段数按 segment_decider 决定（独处放空可能 3 段）
    from agent.segment_decider import decide_segments
    expected = decide_segments(intent)
    assert len(result.itinerary.stages) >= len(expected)
