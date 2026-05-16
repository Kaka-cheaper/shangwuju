"""test_refiner —— refiner 单测（含 LLM stub + 规则化兜底两条路径）。

策略：
- LLM stub 返非法 JSON / 不返 refined_intent → 触发 _rule_fallback
- _rule_fallback 是关键测试对象（保证 LLM 失败时 Demo 不翻车）
- 主断言：raw_input 始终保留 + 字段调整方向正确
"""

from __future__ import annotations

from agent.refiner import _rule_fallback, refine_intent
from agent.llm_client_stub import StubLLMClient
from schemas.intent import Companion, IntentExtraction


def _base_intent() -> IntentExtraction:
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
# Case 1: "太远了" → distance_max_km 缩小
# ============================================================

def test_rule_fallback_too_far_shrinks_distance():
    intent = _base_intent()
    out = _rule_fallback(intent, "太远了，希望近一点")
    assert out.refined_intent.distance_max_km < intent.distance_max_km
    assert out.refined_intent.distance_max_km >= 2.0
    assert any("距离" in cf for cf in out.changed_fields)
    # raw_input 必须保留
    assert out.refined_intent.raw_input == intent.raw_input


# ============================================================
# Case 2: "便宜点" → 去掉高人均，加健康轻食
# ============================================================

def test_rule_fallback_cheaper_drops_premium_tags():
    intent = _base_intent().model_copy(
        update={
            "dietary_constraints": ["高人均", "有包间"],
            "experience_tags": ["商务体面"],
            "social_context": "商务接待",
        }
    )
    out = _rule_fallback(intent, "便宜点")
    assert "高人均" not in out.refined_intent.dietary_constraints
    assert "健康轻食" in out.refined_intent.dietary_constraints
    assert "商务体面" not in out.refined_intent.experience_tags
    assert any("高人均" in cf or "健康" in cf for cf in out.changed_fields)


# ============================================================
# Case 3: 反馈为空 → 距离 -1km 兜底（让候选打散）
# ============================================================

def test_rule_fallback_empty_feedback_does_minor_tweak():
    intent = _base_intent()
    out = _rule_fallback(intent, "")
    # 至少做了一处调整
    assert out.changed_fields
    # raw_input 不漂移
    assert out.refined_intent.raw_input == intent.raw_input
    # 仍合法 IntentExtraction（D9 禁止字段不出现）
    forbidden = {"scene_type", "relation_type", "is_family", "is_friends"}
    leak = forbidden & set(out.refined_intent.model_dump().keys())
    assert not leak


# ============================================================
# Case 4: end-to-end refine_intent（stub LLM 不返 refined → 走 fallback）
# ============================================================

def test_refine_intent_with_stub_falls_back_to_rule():
    """stub LLM chat() 返家庭主场景 IntentExtraction JSON，没有 refined_intent / changed_fields
    包装层。Pydantic 校验会失败 → 重试 1 次 → 仍失败 → 走 _rule_fallback。"""
    intent = _base_intent()
    out = refine_intent(intent, "太远了", client=StubLLMClient())
    # 兜底必须返合法 RefinementOutput
    assert out.refined_intent.raw_input == intent.raw_input
    assert out.refined_intent.distance_max_km < intent.distance_max_km


# ============================================================
# Case 5: D9 硬条款不被绕过
# ============================================================

def test_refined_intent_no_d9_forbidden_fields():
    intent = _base_intent()
    for feedback in ["太远了", "便宜点", "时间紧", "时间多", ""]:
        out = _rule_fallback(intent, feedback)
        forbidden = {"scene_type", "relation_type", "is_family", "is_friends"}
        leak = forbidden & set(out.refined_intent.model_dump().keys())
        assert not leak, f"反馈 '{feedback}' 漏出 D9 禁止字段：{leak}"
