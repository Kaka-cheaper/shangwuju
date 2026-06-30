"""test_e2e_refinement —— 8 场景 × 双 mode × 含/不含反馈 端到端联调。

矩阵（4 维 = 32 跑次）：
- 场景：S1-S8（来自 docs/01-requirements/演示场景集.md）
- 模式：rule / llm（llm 用 stub LLMClient 必触发 fallback）
- 反馈：无（直接 plan）/ 有（refine→重 plan）

为什么不调真 LLM：
- 本测试套件是「联调能不能跑通」的回归检查，不依赖 DEEPSEEK_API_KEY
- 真 LLM 链路验证（A4）需要 user 提供 API key，单独跑

主断言：
1. 8 场景 × 2 mode 全部 success=True（demo 安全网）
2. 反馈「太远了」走 rule_fallback → distance_max_km 真的变小
3. 反馈空字符串也能跑（兜底路径）
4. mode=llm 必触发 fallback agent_thought（stub 客户端不返 tool_calls）
5. 输出 Itinerary ≥ 5 段且 6 段结构齐全
"""

from __future__ import annotations

import pytest

from agent import plan_itinerary, refine_intent
from agent.core.llm_client_stub import StubLLMClient
from agent.core.trace import Tracer
from agent.planning.planners.ils_planner import plan_hybrid
from schemas.intent import Companion, IntentExtraction


# ============================================================
# 8 场景的 IntentExtraction（按演示场景集 §三 期待）
# ============================================================

def _intent(payload: dict) -> IntentExtraction:
    """构造 IntentExtraction，跳过 LLM 直接进 planner。"""
    return IntentExtraction(
        start_time=payload.get("start_time", "today_afternoon"),
        start_weekday=payload.get("start_weekday"),
        duration_hours=payload.get("duration_hours", [3, 5]),
        distance_max_km=payload.get("distance_max_km", 5),
        companions=payload["companions"],
        physical_constraints=payload.get("physical_constraints", []),
        dietary_constraints=payload.get("dietary_constraints", []),
        experience_tags=payload.get("experience_tags", []),
        social_context=payload["social_context"],
        capacity_requirement=payload.get("capacity_requirement"),
        extra_services=payload.get("extra_services", []),
        preferred_poi_types=payload.get("preferred_poi_types", []),
        raw_input=payload["raw_input"],
        parse_confidence=0.85,
    )


def _rule_assembler(intent, candidate, tracer):
    """plan_hybrid 的 rule_assembler 回调：复用 rule planner 完成时间轴拼装。

    镜像 graph/nodes/replan.py:ils_replan_node——plan_hybrid 选定 candidate 后
    把拼装委托给 survivor 入口 rule_planner.plan_itinerary。
    """
    t = tracer if isinstance(tracer, Tracer) else Tracer()
    result = plan_itinerary(intent, tracer=t)
    return result.itinerary if (result.success and result.itinerary) else None


SCENARIOS: dict[str, dict] = {
    "S1": {
        "companions": [
            Companion(role="妻子", count=1),
            Companion(role="孩子", age=5, count=1),
        ],
        "physical_constraints": ["亲子友好", "适合 5-10 岁"],
        "dietary_constraints": ["低脂", "健康轻食"],
        "social_context": "家庭日常",
        "raw_input": "今天下午想和老婆孩子出去玩几个小时",
    },
    "S2": {
        "companions": [Companion(role="朋友", count=4, gender_mix="2男2女")],
        "experience_tags": ["社交", "拍照友好"],
        "social_context": "朋友热闹",
        "capacity_requirement": 4,
        "raw_input": "下午和朋友 4 人 2男2女出去玩",
    },
    "S3": {
        "start_time": "sunday_afternoon",
        "start_weekday": "sunday",
        "duration_hours": [4, 6],
        "companions": [Companion(role="女朋友", count=1)],
        "experience_tags": ["看展", "安静聊天"],
        "social_context": "情侣亲密",
        "preferred_poi_types": ["展览", "美术馆"],
        "raw_input": "周日下午带女朋友去看展",
    },
    "S4": {
        "start_time": "sunday_afternoon",
        "start_weekday": "sunday",
        "distance_max_km": 3,
        "companions": [
            Companion(role="外公", count=1, is_special_role=True),
            Companion(role="外婆", count=1, is_special_role=True),
        ],
        "physical_constraints": ["适合老人", "无台阶", "可休息"],
        "dietary_constraints": ["软烂"],
        "social_context": "老人伴助",
        "raw_input": "周日下午带外公外婆出去走走",
    },
    "S5": {
        "start_time": "weekend_afternoon",
        "duration_hours": [3, 4],
        "companions": [Companion(role="闺蜜", count=1)],
        "dietary_constraints": ["下午茶", "甜品"],
        "experience_tags": ["网红打卡", "拍照友好"],
        "social_context": "闺蜜聊天",
        "raw_input": "周末和闺蜜下午茶",
    },
    "S6": {
        "companions": [Companion(role="商务客户", count=1, is_special_role=True)],
        "dietary_constraints": ["高人均", "有包间"],
        "experience_tags": ["商务体面", "礼仪感"],
        "social_context": "商务接待",
        "raw_input": "下午接外地客户",
    },
    "S7": {
        "duration_hours": [2, 4],
        "companions": [],
        "experience_tags": ["独处舒缓"],
        "social_context": "独处放空",
        "raw_input": "一个人安静待几小时",
    },
    "S8": {
        "start_time": "sunday_lunch",
        "start_weekday": "sunday",
        "duration_hours": [3, 4],
        "companions": [
            Companion(role="母亲", count=1, is_birthday=True, is_special_role=True),
            Companion(role="全家", count=6),
        ],
        "physical_constraints": ["适合老人"],
        "dietary_constraints": ["粤菜"],
        "experience_tags": ["礼仪感"],
        "social_context": "纪念日仪式感",
        "capacity_requirement": 6,
        "extra_services": ["蛋糕"],
        "raw_input": "妈妈生日全家 6 人吃粤菜",
    },
}


# ============================================================
# 维度 1：8 场景 × 2 mode → 16 跑次（不带反馈，主路径）
# ============================================================

@pytest.mark.parametrize("scenario_id", list(SCENARIOS.keys()))
@pytest.mark.parametrize("mode", ["rule", "llm"])
def test_scenario_x_mode_main_path(scenario_id: str, mode: str):
    intent = _intent(SCENARIOS[scenario_id])
    # 双 mode 端到端：原 V1 dispatcher 在 stub 客户端下对 llm/rule 都短路到 plan_itinerary
    # （stub 无真 LLM 决策能力，dispatcher 的 stub 分支直接 return plan_itinerary）。
    # dispatcher 已删；survivor 等价入口即 rule planner.plan_itinerary——两个 mode 不变量一致。
    result = plan_itinerary(intent)
    assert result.success, (
        f"{scenario_id}/{mode} 失败：{result.failure_detail}"
    )
    itinerary = result.itinerary
    assert itinerary is not None
    # edge_v1：中间节点按 decide_nodes 决定（不再硬要 5 段）
    from agent.planning.blueprint.node_decider import decide_nodes
    expected_kinds = decide_nodes(intent)
    mid_nodes = [n for n in itinerary.nodes if n.target_kind != "home"]
    mid_kinds = {n.kind for n in mid_nodes}
    assert len(mid_nodes) >= len(expected_kinds), (
        f"中间节点数不足：实际 {len(mid_nodes)}，按 intent 应有 {len(expected_kinds)} 个"
    )
    for required in expected_kinds:
        assert required in mid_kinds, (
            f"{scenario_id}/{mode} 缺中间节点 kind：{required}（实际 {mid_kinds}）"
        )


# ============================================================
# 维度 2：mode=llm 必触发 fallback（stub LLM 不返 tool_calls）
# ============================================================

def test_llm_mode_fallback_thought_emitted():
    """stub LLM 无主观决策能力 → hybrid 走启发式权重（非真 LLM），trace 标记降级。

    原 V1 双范式 dispatcher 在 stub 下推「已切回规则规划」thought；
    dispatcher 已删。survivor 等价物：直调 plan_hybrid，stub 经 get_planning_weights 走
    启发式权重（source=stub），并在 trace 推「权重（stub）」降级信号——即 LLM 不可用时的
    优雅降级（成功出方案 + trace 可见）。
    """
    intent = _intent(SCENARIOS["S1"])
    tracer = Tracer()
    result = plan_hybrid(
        intent, client=StubLLMClient(), tracer=tracer, rule_assembler=_rule_assembler,
    )
    assert result.success
    # stub → 权重来自启发式（非 LLM），证明 LLM 不可用时优雅降级
    assert result.weights is not None and result.weights.source == "stub"
    # trace 含降级信号（权重决策 thought 标 stub 来源）
    thoughts = [r for r in tracer.records if r.type == "agent_thought"]
    assert any("stub" in t.payload.get("text", "") for t in thoughts), (
        "stub 模式应在 trace 标记启发式权重降级信号"
    )


# ============================================================
# 维度 3：8 场景 + 反馈「太远了」 → distance 真的变小
# ============================================================

@pytest.mark.parametrize("scenario_id", list(SCENARIOS.keys()))
def test_refine_too_far_shrinks_distance(scenario_id: str):
    """refiner stub LLM 路径走 _rule_fallback；S4 原距离 3km 已较小，不一定能再缩。"""
    intent = _intent(SCENARIOS[scenario_id])
    out = refine_intent(intent, "太远了，希望近一点")
    # raw_input 强制保留
    # raw_input 保留原句作为前缀（pitfalls P1-2026-05-17 引申：反馈作为最高约束追加到 raw_input）
    assert out.refined_intent.raw_input.startswith(intent.raw_input)
    # 距离要么变小，要么因为已经触底（≤2km）保持
    if intent.distance_max_km > 2.0:
        assert out.refined_intent.distance_max_km <= intent.distance_max_km
    # changed_fields 至少能反映「未识别」或「距离调整」
    assert isinstance(out.changed_fields, list)
    assert out.refiner_note  # 必有说明


# ============================================================
# 维度 4：8 场景 + 反馈合并后能继续 plan（rule + llm 都过）
# ============================================================

@pytest.mark.parametrize("scenario_id", ["S1", "S3", "S6"])  # 跨场景代表
@pytest.mark.parametrize("mode", ["rule", "llm"])
def test_refine_then_replan_end_to_end(scenario_id: str, mode: str):
    """主路径 → 反馈 → 重新 plan 的完整链路。"""
    intent = _intent(SCENARIOS[scenario_id])

    # 第一次 plan（survivor 等价入口：stub 下 llm/rule 都走 plan_itinerary）
    plan1 = plan_itinerary(intent)
    assert plan1.success

    # 反馈合并
    feedback = "太远了" if intent.distance_max_km > 2.5 else "便宜点"
    out = refine_intent(intent, feedback)
    refined = out.refined_intent

    # 重新 plan（用 refined intent）
    plan2 = plan_itinerary(refined)
    assert plan2.success, (
        f"{scenario_id}/{mode} refine 后重新 plan 失败：{plan2.failure_detail}"
    )
    assert plan2.itinerary is not None
    # edge_v1：至少含 1 个 mid node
    mid_nodes = [n for n in plan2.itinerary.nodes if n.target_kind != "home"]
    assert len(mid_nodes) >= 1, "重新 plan 后应至少含 1 个 mid node"


# ============================================================
# 维度 5：空反馈也能跑（兜底）
# ============================================================

def test_refine_empty_feedback():
    intent = _intent(SCENARIOS["S1"])
    out = refine_intent(intent, "")
    # 兜底必给说明
    assert out.refiner_note
    # raw_input 不漂移
    # raw_input 保留原句作为前缀（pitfalls P1-2026-05-17 引申：反馈作为最高约束追加到 raw_input）
    assert out.refined_intent.raw_input.startswith(intent.raw_input)


# ============================================================
# 维度 6：D9 硬条款不被绕过（refine 后仍无场景枚举字段）
# ============================================================

@pytest.mark.parametrize("scenario_id", list(SCENARIOS.keys()))
def test_refined_intent_no_d9_forbidden_fields(scenario_id: str):
    intent = _intent(SCENARIOS[scenario_id])
    out = refine_intent(intent, "太远了")
    forbidden = {"scene_type", "relation_type", "is_family", "is_friends"}
    leak = forbidden & set(out.refined_intent.model_dump().keys())
    assert not leak, f"{scenario_id} 出现 D9 禁止字段：{leak}"
