"""tests.test_refiner_duration_consistency —— refiner 时长字段一致性回归测试。

bug 现象（截图复现）：用户说「我只有一个小时」，refiner 的 changed_fields 显示
「时长：[4,6] → [1,1]」，但 refined_intent.duration_hours 仍是 [4,6]。
导致 planner 用旧时长拼时间轴，主活动仍是 2 小时（应为 30 分钟）。

根因：
1. _rule_fallback 关键词只覆盖「时间紧/快一点」→ [2,3]、「时间多」→ [5,7]，
   不识别带具体数字的时长（"我只有 1 小时"/"两小时"）
2. LLM 路径下 LLM 在 changed_fields 文本里复读了用户说的"1 小时"，
   但 refined_intent.duration_hours 字段未真改

修复（双层防御）：
- 兜底层：_rule_fallback 用正则 _extract_duration_from_feedback 抽数字
- LLM 层：成功解析后跑 _enforce_duration_consistency，把字段强制对齐反馈
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agent.refiner import refine_intent, _rule_fallback, _extract_duration_from_feedback
from schemas.intent import Companion, IntentExtraction


# ============================================================
# 共享 fixture
# ============================================================

def _intent(duration: list[int] | None = None) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=list(duration or [4, 6]),
        distance_max_km=5.0,
        companions=[Companion(role="女朋友", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=["看展", "安静聊天"],
        social_context="情侣亲密",
        raw_input="周日下午带女朋友去看展",
        parse_confidence=0.9,
    )


# ============================================================
# 维度 1：_extract_duration_from_feedback 工具函数
# ============================================================

@pytest.mark.parametrize(
    "feedback,expected",
    [
        ("我只有一个小时", [1, 1]),
        ("我只有 1 小时", [1, 1]),
        ("一小时", [1, 1]),
        ("我有两小时", [2, 2]),
        ("就 2 小时吧", [2, 2]),
        ("半小时差不多", None),  # 不强求支持半小时
        ("3 个小时", [3, 3]),
        ("再给我 2-3 小时", [2, 3]),
        ("1 到 2 个小时", [1, 2]),
        ("不想被时间限制", None),
        ("时间紧", None),  # 没具体数字 → 让关键词分支处理
    ],
)
def test_extract_duration_from_feedback(feedback: str, expected: list[int] | None):
    result = _extract_duration_from_feedback(feedback)
    if expected is None:
        assert result is None, f"{feedback!r} 应返 None，实际 {result}"
    else:
        assert result == tuple(expected), f"{feedback!r} 期待 {expected}，实际 {result}"


# ============================================================
# 维度 2：_rule_fallback 识别精确数字时长（截图复现）
# ============================================================

def test_rule_fallback_one_hour_changes_duration_to_one_one():
    """截图复现：feedback="我只有一个小时" → refined.duration_hours=[1,1]，
    且 changed_fields 含「时长：[4,6] → [1,1]」。"""
    original = _intent(duration=[4, 6])
    out = _rule_fallback(original, "我只有一个小时")

    assert list(out.refined_intent.duration_hours) == [1, 1], (
        f"refined.duration_hours 应为 [1,1]，实际 {out.refined_intent.duration_hours}"
    )
    assert any("时长" in c and "[1, 1]" in c for c in out.changed_fields), (
        f"changed_fields 应含时长变更：{out.changed_fields}"
    )


def test_rule_fallback_two_hours_changes_duration_to_two_two():
    original = _intent(duration=[4, 6])
    out = _rule_fallback(original, "就两小时吧")
    assert list(out.refined_intent.duration_hours) == [2, 2]


def test_rule_fallback_keyword_time_tight_still_works():
    """旧关键词"时间紧"路径不能因为新逻辑被破坏。"""
    original = _intent(duration=[4, 6])
    out = _rule_fallback(original, "时间紧")
    assert list(out.refined_intent.duration_hours) == [2, 3]


def test_rule_fallback_no_time_keyword_keeps_duration():
    """反馈不涉及时长 → duration_hours 不动。"""
    original = _intent(duration=[4, 6])
    out = _rule_fallback(original, "太远了")
    assert list(out.refined_intent.duration_hours) == [4, 6]


# ============================================================
# 维度 3：LLM 路径输出不一致时强制对齐（截图根因）
# ============================================================

@dataclass
class _MockLLMResp:
    content: str
    tool_calls: list = field(default_factory=list)
    finish_reason: str = "stop"
    raw: dict | None = None


class _InconsistentLLMClient:
    """模拟截图中的 LLM 行为：changed_fields 说改了时长，refined_intent 字段没改。"""

    provider = "mock"
    model = "mock"

    def chat(self, messages, *, temperature=0.3, response_format=None):
        # 返回与截图一致的不一致响应：
        # changed_fields 含「时长：[4,6] → [1,1]」，但 refined_intent.duration_hours 仍是 [4,6]
        return _MockLLMResp(
            content=(
                '{"refined_intent": {"start_time": "today_afternoon", "start_weekday": null, '
                '"duration_hours": [4, 6], "distance_max_km": 5.0, '
                '"companions": [{"role": "女朋友", "age": null, "count": 1, '
                '"gender_mix": null, "is_birthday": false, "is_special_role": false}], '
                '"physical_constraints": [], "dietary_constraints": [], '
                '"experience_tags": ["看展", "安静聊天"], "social_context": "情侣亲密", '
                '"capacity_requirement": null, "extra_services": [], '
                '"preferred_poi_types": [], "raw_input": "周日下午带女朋友去看展", '
                '"parse_confidence": 0.9, "ambiguous_fields": []}, '
                '"changed_fields": ["时长：[4, 6] → [1, 1] 小时"], '
                '"refiner_note": "已把可用时长调到 1 小时"}'
            )
        )


class _ConsistentLLMClient:
    """模拟正常的 LLM：changed_fields 与 refined_intent 字段一致。"""

    provider = "mock"
    model = "mock"

    def chat(self, messages, *, temperature=0.3, response_format=None):
        return _MockLLMResp(
            content=(
                '{"refined_intent": {"start_time": "today_afternoon", "start_weekday": null, '
                '"duration_hours": [3, 4], "distance_max_km": 5.0, '
                '"companions": [{"role": "女朋友", "age": null, "count": 1, '
                '"gender_mix": null, "is_birthday": false, "is_special_role": false}], '
                '"physical_constraints": [], "dietary_constraints": [], '
                '"experience_tags": ["看展", "安静聊天"], "social_context": "情侣亲密", '
                '"capacity_requirement": null, "extra_services": [], '
                '"preferred_poi_types": [], "raw_input": "周日下午带女朋友去看展", '
                '"parse_confidence": 0.9, "ambiguous_fields": []}, '
                '"changed_fields": ["时长：[4, 6] → [3, 4] 小时"], '
                '"refiner_note": "已把可用时长调到 3-4 小时"}'
            )
        )


def test_refine_intent_enforces_duration_consistency():
    """LLM 输出不一致时，refiner 应按 feedback 真实数字强制覆盖 refined_intent.duration_hours。"""
    original = _intent(duration=[4, 6])
    out = refine_intent(original, "我只有一个小时", client=_InconsistentLLMClient())

    assert list(out.refined_intent.duration_hours) == [1, 1], (
        f"LLM 不一致时 refiner 必须强制对齐字段；实际 duration_hours={out.refined_intent.duration_hours}"
    )


def test_refine_intent_consistent_response_unchanged():
    """LLM 输出一致（feedback 没具体数字时）→ refiner 不动。"""
    original = _intent(duration=[4, 6])
    out = refine_intent(original, "时间多一点", client=_ConsistentLLMClient())

    # 不应被后校验破坏
    assert list(out.refined_intent.duration_hours) == [3, 4]


# ============================================================
# 维度 4：端到端——refine_intent 默认 client（auth 缺失）走兜底也能正确
# ============================================================

def test_refine_intent_no_client_falls_back_correctly(monkeypatch):
    """无 LLM 客户端可用 → 走 _rule_fallback；结果应识别精确小时数。"""

    def _no_client(*args, **kwargs):
        raise ValueError("no LLM_API_KEY")

    monkeypatch.setattr("agent.refiner.get_llm_client", _no_client, raising=False)

    original = _intent(duration=[4, 6])
    out = refine_intent(original, "我只有一个小时")
    assert list(out.refined_intent.duration_hours) == [1, 1]
