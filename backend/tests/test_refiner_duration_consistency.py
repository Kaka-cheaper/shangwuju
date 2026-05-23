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
        ("半小时差不多", [0, 1]),  # spec R8：扩展支持半小时 → (0, 1)
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



# ============================================================
# 维度 5：截图 bug 完整端到端复现（pitfalls P1+P2-2026-05-17）
# ============================================================

def test_screenshot_bug_one_hour_feedback_caps_total_minutes():
    """截图复现：S5 闺蜜下午茶 + 反馈"只有一个小时"，rule mode 应削段+裁时长。

    历史 bug：
    - 截图旧行为：4.7h 总时长，5 段
    - 修复后期望：≤ 1.5h 总时长，3 段（裁掉用餐段）

    这是综合性回归测试，覆盖：
    - refiner 真 LLM 路径输出 [1,1] 一致性（_enforce_duration_consistency）
    - refiner 把反馈拼到 raw_input
    - planner 入口 _enforce_intent_duration_from_raw 兜底
    - segment_decider 决定削段
    - _resolve_time_window 接受 segments 不再 30min 下限拉爆
    - 二次裁段在 duration ≤ 2h 时启用
    """
    from agent.planner import plan_itinerary

    intent = IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[1, 1],  # 已经被 refiner 改对（用 _rule_fallback 模拟）
        distance_max_km=5,
        companions=[Companion(role="闺蜜", count=1)],
        physical_constraints=[],
        dietary_constraints=["下午茶", "甜品"],
        experience_tags=["网红打卡", "拍照友好"],
        social_context="闺蜜聊天",
        raw_input="周末下午约了闺蜜想找个网红的地方拍拍照吃个下午茶。（反馈：只有一个小时）",
        parse_confidence=0.9,
    )
    result = plan_itinerary(intent)

    assert result.success
    itin = result.itinerary
    assert itin is not None

    # 截图 bug 修复：总时长 ≤ 1.5h（容忍路程+对齐到 30min）
    assert itin.total_minutes <= 90, (
        f"反馈 1h 后总时长应 ≤ 90min，实际 {itin.total_minutes}"
    )

    # edge_v1：mid 节点应 ≤ 2（不会硬塞 5 段），且首尾 home 由 assemble 自动补
    mid_nodes = [n for n in itin.nodes if n.target_kind != "home"]
    assert len(mid_nodes) <= 2, (
        f"反馈 1h 后中间节点应 ≤ 2，实际 {len(mid_nodes)}"
    )

    # 必有 home 起讫（assemble 不变量已校验，此处冗余兜底）
    assert itin.nodes[0].target_kind == "home"
    assert itin.nodes[-1].target_kind == "home"


def test_two_hour_feedback_caps_total_within_2_5_hours():
    """反馈"2 小时" + 闺蜜下午茶：受 mock 餐厅时段约束，可能裁掉用餐段；
    但总时长必须严格 ≤ 2.5h（不能像截图 4.7h 那样）。"""
    from agent.planner import plan_itinerary

    intent = IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[2, 2],
        distance_max_km=5,
        companions=[Companion(role="闺蜜", count=1)],
        physical_constraints=[],
        dietary_constraints=["下午茶", "甜品"],
        experience_tags=["网红打卡"],
        social_context="闺蜜聊天",
        raw_input="周末下午约了闺蜜（反馈：就两小时吧）",
        parse_confidence=0.9,
    )
    result = plan_itinerary(intent)
    assert result.success
    itin = result.itinerary

    # 2h 反馈下总时长应严格 ≤ 2.5h（容忍 30min 路程+对齐）
    assert itin.total_minutes <= 150, (
        f"2h 反馈下总时长应 ≤ 150min，实际 {itin.total_minutes}"
    )

    # edge_v1：必有 home 起讫，主活动 / 用餐 mid node 至少一个
    assert itin.nodes[0].target_kind == "home"
    assert itin.nodes[-1].target_kind == "home"
    mid_nodes = [n for n in itin.nodes if n.target_kind != "home"]
    mid_kinds = {n.kind for n in mid_nodes}
    assert "主活动" in mid_kinds or "用餐" in mid_kinds, (
        f"应至少含主活动或用餐 mid node：实际 {mid_kinds}"
    )


def test_long_duration_unaffected_by_dining_cut():
    """4h 场景仍应 5 段，不被二次裁段误触发。"""
    from agent.planner import plan_itinerary

    intent = IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[Companion(role="妻子", count=1), Companion(role="孩子", age=5, count=1)],
        physical_constraints=["亲子友好", "适合 5-10 岁"],
        dietary_constraints=["低脂", "健康轻食"],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="今天下午带老婆孩子",
        parse_confidence=0.9,
    )
    result = plan_itinerary(intent)
    assert result.success
    itin = result.itinerary
    # edge_v1：4h 场景应有完整 mid nodes（主活动 + 用餐 共 2 个），
    # 加上首尾 home 共 4 个节点，3 条 hop
    mid_nodes = [n for n in itin.nodes if n.target_kind != "home"]
    assert len(mid_nodes) == 2, (
        f"4h 家庭场景应得 2 个中间节点（主活动+用餐），实际 {len(mid_nodes)}"
    )
    mid_kinds = {n.kind for n in mid_nodes}
    assert mid_kinds == {"主活动", "用餐"}, f"实际 mid_kinds={mid_kinds}"
    # 首尾 home + 2 mid = 4 nodes / 3 hops
    assert len(itin.nodes) == 4
    assert len(itin.hops) == 3
