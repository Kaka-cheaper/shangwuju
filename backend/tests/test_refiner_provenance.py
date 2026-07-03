"""tests.test_refiner_provenance —— ADR-0014 决策 1（G-1）refiner 出处传播测试。

覆盖反馈轮"纯规则，不要 LLM 自报"的四条传播规则
（`agent.intent.refiner._propagate_field_provenance`）：
1. 改动的字段/新元素 → user_stated
2. 未动的字段/元素 → 继承原出处
3. 撤回的元素 → 出处键同步清理
4. 重申先验已有值 → 升级 user_stated

覆盖两条产出路径：`_rule_fallback`（直调，同 test_refiner.py 既有风格）+
`refine_intent` 走 LLM 成功路径（假 client）——确认两条路径都跑同一套传播
规则（"_rule_fallback 路径同样维护"，ADR-0014 决策 1），且 LLM 自己在
refined_intent.field_provenance 里自报的任何值都会被规则整体覆盖（"不要
LLM 自报"）。
"""

from __future__ import annotations

import json

from agent.core.llm_client import LLMChatResponse
from agent.intent.refiner import _rule_fallback, refine_intent
from schemas.intent import Companion, IntentExtraction


def _intent(**overrides) -> IntentExtraction:
    base = dict(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[Companion(role="妻子", count=1)],
        physical_constraints=["亲子友好"],
        dietary_constraints=["高人均", "有包间"],
        experience_tags=["商务体面"],
        social_context="家庭日常",
        raw_input="今天下午带老婆孩子",
        parse_confidence=0.9,
        field_provenance={
            "distance_max_km": "prior",
            "physical_constraints:亲子友好": "prior",
            "dietary_constraints:高人均": "prior",
            "dietary_constraints:有包间": "prior",
            "experience_tags:商务体面": "prior",
        },
    )
    base.update(overrides)
    return IntentExtraction(**base)


# ============================================================
# 1. 改动 → user_stated（"太远了"缩距离）
# ============================================================


def test_changed_field_becomes_user_stated():
    intent = _intent()
    out = _rule_fallback(intent, "太远了，希望近一点")
    assert out.refined_intent.distance_max_km != intent.distance_max_km
    assert out.refined_intent.field_provenance["distance_max_km"] == "user_stated"


# ============================================================
# 2. 未动 → 继承原出处
# ============================================================


def test_untouched_field_inherits_original_provenance():
    intent = _intent()
    out = _rule_fallback(intent, "太远了，希望近一点")
    # physical_constraints 未被"太远了"分支触及，原出处 prior 应保留
    assert out.refined_intent.field_provenance.get("physical_constraints:亲子友好") == "prior"


# ============================================================
# 3. 撤回 → 出处键同步清理
# ============================================================


def test_withdrawn_element_provenance_key_removed():
    intent = _intent()
    out = _rule_fallback(intent, "便宜点")
    assert "高人均" not in out.refined_intent.dietary_constraints
    assert "dietary_constraints:高人均" not in out.refined_intent.field_provenance


# ============================================================
# 4. 重申先验已有值 → 升级 user_stated
# ============================================================


def test_reasserted_value_upgrades_to_user_stated():
    intent = _intent()
    # "便宜点"分支不动"有包间"（只去高人均、加健康轻食）；反馈原话重申"有包间"
    out = _rule_fallback(intent, "便宜点，但有包间就行")
    assert "有包间" in out.refined_intent.dietary_constraints
    assert out.refined_intent.field_provenance.get("dietary_constraints:有包间") == "user_stated"


def test_non_reasserted_prior_value_stays_prior():
    """对照组：反馈没提"有包间"字面词，应仍继承 prior（防止误判"只要变了就升级"）。"""
    intent = _intent()
    out = _rule_fallback(intent, "便宜点")
    assert "有包间" in out.refined_intent.dietary_constraints
    assert out.refined_intent.field_provenance.get("dietary_constraints:有包间") == "prior"


# ============================================================
# 5. _rule_fallback 路径同样维护："太久了" 缩 duration_hours 时标 user_stated
# ============================================================


def test_session_too_long_duration_shrink_marked_user_stated():
    intent = _intent(duration_hours=[4, 8])
    out = _rule_fallback(intent, "这段太久了，孩子扛不住")
    assert out.refined_intent.duration_hours[1] < 8
    assert out.refined_intent.field_provenance["duration_hours"] == "user_stated"


# ============================================================
# 6. LLM 成功路径也跑同一套传播规则（结构 diff，不采信 LLM 自报）
# ============================================================


class _FixedRefineClient:
    def __init__(self, refined_payload: dict, changed_fields: list[str]):
        self._content = json.dumps(
            {
                "refined_intent": refined_payload,
                "changed_fields": changed_fields,
                "refiner_note": "已调整。",
            },
            ensure_ascii=False,
        )

    def chat(self, messages, *, temperature=0.2, response_format=None, max_tokens=None, extra_body=None):
        return LLMChatResponse(content=self._content, finish_reason="stop")


def test_llm_path_also_propagates_provenance_by_diff_not_llm_self_report():
    intent = _intent()
    refined_payload = intent.model_dump()
    refined_payload["distance_max_km"] = 3.0  # 唯一改动
    # LLM 自己胡乱自报 field_provenance——必须被规则传播整体覆盖，不采信
    refined_payload["field_provenance"] = {"distance_max_km": "prior"}

    client = _FixedRefineClient(refined_payload, ["距离上限：5km → 3km"])
    out = refine_intent(intent, "太远了", client=client)

    # 不管 LLM 自己标了什么，规则说"变了就是 user_stated"
    assert out.refined_intent.field_provenance["distance_max_km"] == "user_stated"
    # 未变字段仍然继承原出处（不是被 LLM 自报的 {"distance_max_km":"prior"} 那份污染）
    assert out.refined_intent.field_provenance.get("physical_constraints:亲子友好") == "prior"
