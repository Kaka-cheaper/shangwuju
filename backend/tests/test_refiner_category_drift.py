"""tests.test_refiner_category_drift —— 烧烤根治批 L1：反馈轮「词典外品类」漂移根治。

【病灶回顾】品类有两条通道：`dietary_constraints`（封闭 Literal 词典，只有
日料/粤菜等）和 `preferred_poi_types`（自由文本，anchor-escape 靠它触发召回，
见 test_explicit_cuisine_anchor.py）。intent_parser 首轮教了"词典外品类
（烧烤/撸串/夜宵/火锅/川菜/KTV/桌游/密室/真人 CS/攀岩）必须原样写进
preferred_poi_types"，但 refiner_prompt.py（反馈轮）此前从没提过这条规则——
"商务茶叙方案 + 反馈『吃个烧烤』"这类场景下，烧烤会被 LLM 试图塞进
dietary_constraints（词典没有，校验失败/被丢弃）或干脆不落地任何字段，
preferred_poi_types 保持空 → anchor-escape 收不到信号 → 换菜失败。

【本文件验收的两条修复】
1. 共享规则（方案2，`agent.intent.refiner._repair_dictionary_drift`）：反馈轮
   产出后做中立后处理——反馈原话命中 `category_vocab.all_canonical_terms()`
   且不在 DIETARY_TAGS、且未出现在 preferred_poi_types → 自动补齐。两条产出
   路径（LLM 成功 / `_rule_fallback` 兜底）都要经过它。
2. 幂等：parser 首轮已经正确填过的词不重复添加（防止共享规则在无反馈变化
   场景下把 preferred_poi_types 意外拉长）。
"""

from __future__ import annotations

import json

from agent.core.llm_client import LLMChatResponse
from agent.intent.refiner import _repair_dictionary_drift, _rule_fallback, refine_intent
from schemas.intent import Companion, IntentExtraction


def _business_tea_intent(**overrides) -> IntentExtraction:
    """商务茶叙方案：无 preferred_poi_types，dietary 走商务客户机械触发规则。"""
    base = dict(
        start_time="today_afternoon",
        duration_hours=[2, 3],
        distance_max_km=5,
        companions=[Companion(role="客户", count=2, is_special_role=True)],
        physical_constraints=[],
        dietary_constraints=["高人均", "有包间"],
        experience_tags=["商务体面", "礼仪感"],
        social_context="商务接待",
        capacity_requirement=2,
        raw_input="下午陪客户喝茶聊聊",
        parse_confidence=0.85,
        preferred_poi_types=[],
    )
    base.update(overrides)
    return IntentExtraction(**base)


class _FixedRefineClient:
    """假 LLM client：模拟"LLM 没学会新规则，把烧烤漏在忘记填 preferred_poi_types
    也没塞进 dietary_constraints"的最坏情况——用来断言中立后处理能兜底补齐，
    不完全依赖 prompt 教得动 LLM。"""

    def __init__(self, refined_payload: dict, changed_fields: list[str]):
        self._content = json.dumps(
            {
                "refined_intent": refined_payload,
                "changed_fields": changed_fields,
                "refiner_note": "已按反馈调整。",
            },
            ensure_ascii=False,
        )

    def chat(self, messages, *, temperature=0.2, response_format=None, max_tokens=None, extra_body=None):
        return LLMChatResponse(content=self._content, finish_reason="stop")


# ============================================================
# 1. 单元测试：_repair_dictionary_drift 本身
# ============================================================


def test_repair_adds_bbq_to_preferred_poi_types_when_llm_missed_it():
    intent = _business_tea_intent()
    repaired = _repair_dictionary_drift(intent, "不喝茶了，吃个烧烤吧")
    assert "烧烤" in repaired.preferred_poi_types


def test_repair_is_idempotent_when_parser_already_filled_it():
    """parser 首轮已正确填过 → 不重复添加（幂等）。"""
    intent = _business_tea_intent(preferred_poi_types=["烧烤"])
    repaired = _repair_dictionary_drift(intent, "吃个烧烤")
    assert repaired.preferred_poi_types == ["烧烤"]


def test_repair_does_not_touch_dietary_vocab_words():
    """反馈提到词典内有对应词的菜系（如"日料"）→ 不该被本函数插手
    （那条路径走 dietary_constraints，是 LLM/规则兜底的正常职责，不是本函数
    该管的"词典外品类"漂移）。"""
    intent = _business_tea_intent()
    repaired = _repair_dictionary_drift(intent, "想吃日料")
    assert "日料" not in repaired.preferred_poi_types


def test_repair_noop_on_empty_feedback():
    intent = _business_tea_intent()
    repaired = _repair_dictionary_drift(intent, "")
    assert repaired.preferred_poi_types == []


def test_repair_handles_multiple_category_words_in_one_feedback():
    intent = _business_tea_intent()
    repaired = _repair_dictionary_drift(intent, "不想喝茶了，撸串或者烧烤都行")
    assert "撸串" in repaired.preferred_poi_types
    assert "烧烤" in repaired.preferred_poi_types


# ============================================================
# 2. _rule_fallback 路径（LLM 不可用时的降级路径）
# ============================================================


def test_rule_fallback_adds_bbq_to_preferred_poi_types():
    intent = _business_tea_intent()
    out = _rule_fallback(intent, "不喝茶了，吃个烧烤吧")
    assert "烧烤" in out.refined_intent.preferred_poi_types
    assert any("烧烤" in c for c in out.changed_fields), (
        f"changed_fields 应体现品类调整，实际={out.changed_fields}"
    )


def test_rule_fallback_idempotent_when_already_present():
    intent = _business_tea_intent(preferred_poi_types=["烧烤"])
    out = _rule_fallback(intent, "还是想吃烧烤")
    assert out.refined_intent.preferred_poi_types == ["烧烤"]


# ============================================================
# 3. refine_intent 端到端（LLM 路径，模拟 LLM 未学会新规则的最坏情况）
# ============================================================


def test_llm_path_repairs_missing_preferred_poi_types():
    """LLM 假装"没学会"新规则：payload 里 preferred_poi_types 仍是空——
    中立后处理应该在 refine_intent 端到端流程里把烧烤补回来。"""
    intent = _business_tea_intent()
    refined_payload = intent.model_dump()
    refined_payload["understanding"] = "用户说不喝茶了想吃烧烤，我理解成主活动换成烧烤"
    # 故意不改 preferred_poi_types，模拟 LLM 漏教场景
    assert refined_payload["preferred_poi_types"] == []

    client = _FixedRefineClient(refined_payload, [])
    out = refine_intent(intent, "不喝茶了，吃个烧烤吧", client=client)

    assert "烧烤" in out.refined_intent.preferred_poi_types, (
        f"中立后处理应补齐 preferred_poi_types，实际={out.refined_intent.preferred_poi_types}"
    )


def test_llm_path_idempotent_when_llm_already_did_it_right():
    """LLM 已经学会了新规则、正确填了 preferred_poi_types → 后处理不重复添加、
    不产生副作用。"""
    intent = _business_tea_intent()
    refined_payload = intent.model_dump()
    refined_payload["preferred_poi_types"] = ["烧烤"]
    refined_payload["understanding"] = "用户说想吃烧烤，我理解成主活动换成烧烤"

    client = _FixedRefineClient(refined_payload, ["加品类：烧烤"])
    out = refine_intent(intent, "吃个烧烤", client=client)

    assert out.refined_intent.preferred_poi_types == ["烧烤"]


def test_first_round_parser_behavior_unaffected():
    """共享规则只挂在 refiner 路径上，不触碰 intent_parser 首轮产出——首轮的
    IntentExtraction 若直接拿去跑 _repair_dictionary_drift（防御性验证：即使
    误用也是幂等补齐，不会因为已经填对而重复添加或产生副作用）。"""
    already_correct = _business_tea_intent(preferred_poi_types=["烧烤"], raw_input="我想吃个烧烤")
    repaired = _repair_dictionary_drift(already_correct, "我想吃个烧烤")
    assert repaired.preferred_poi_types == ["烧烤"]
    assert repaired == already_correct.model_copy(update={"preferred_poi_types": ["烧烤"]})
