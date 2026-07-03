"""tests.test_intent_parser_provenance —— ADR-0014 决策 1（G-1）parser 出处测试。

覆盖首轮出处「LLM 自报 + 规则交叉校正」（`agent.intent.parser._apply_provenance_
correction`）：
1. 无 user_id（无先验注入）→ 自报原样通过，不误伤。
2. 先验值命中 + 原话未提 → 机械回标 prior，覆盖 LLM 自报（"冲突时规则赢"）。
3. 值虽等于先验值，但原话确实提到 → 不强制纠正，保留自报。
4. distance_max_km 标量字段同样适用先验强制纠正；social_context 自
   ADR-0014 横向深审 P1 起豁免这条强纠正（该字段本就"几乎总是 inferred"，
   字面等值不是"抄了先验"的可靠信号，见下方对应测试）。
5. 自报缺失时按 schema 默认值兜底（default / user_stated）。

用固定 JSON 假 client（不复用全局 StubLLMClient——那份 fixture 内容固定，
无法按测试场景定制自报值），u_dad persona（mock_data/personas.json）提供
确定性先验：default_tags.physical 含"亲子友好"、suitable_for_priority=
["家庭日常"]、default_distance_max_km=5.0。
"""

from __future__ import annotations

import json
import os

import pytest

from agent.core.llm_client import LLMChatResponse
from agent.intent.parser import parse_intent
from agent.routing.canonical_shortcut import DEMO_SCENARIOS
from data.memory_store import reset_all_memory


@pytest.fixture(autouse=True)
def _isolate_memory():
    # u_dad 的 top_priors 会被 memory 累积污染——每个测试前后清空，同
    # test_persona_memory.py 的隔离手法。
    reset_all_memory()
    os.environ.pop("SHANGWUJU_MEMORY_DIR", None)
    yield
    reset_all_memory()


class _FixedJsonClient:
    """固定返回给定 payload 的假 LLM 客户端，模拟"LLM 自报出处"的输出。"""

    def __init__(self, payload: dict):
        self._content = json.dumps(payload, ensure_ascii=False)

    def chat(self, messages, *, temperature=0.1, response_format=None, max_tokens=None, extra_body=None):
        return LLMChatResponse(content=self._content, finish_reason="stop")


def _full_payload(raw_input: str, **overrides) -> dict:
    base = {
        "start_time": "today_afternoon",
        "start_weekday": None,
        "duration_hours": [3, 5],
        "distance_max_km": 5.0,
        "companions": [],
        "physical_constraints": [],
        "dietary_constraints": [],
        "experience_tags": [],
        "social_context": "家庭日常",
        "capacity_requirement": None,
        "extra_services": [],
        "preferred_poi_types": [],
        "budget_per_person": None,
        "raw_input": raw_input,
        "parse_confidence": 0.8,
        "ambiguous_fields": [],
        "field_provenance": {},
    }
    base.update(overrides)
    return base


# ============================================================
# 1. 无 user_id → 无先验可注入，自报原样通过
# ============================================================


def test_no_user_id_self_report_passes_through_unchanged():
    raw = "带孩子去亲子馆"
    payload = _full_payload(
        raw,
        physical_constraints=["亲子友好"],
        field_provenance={"physical_constraints:亲子友好": "user_stated"},
    )
    intent = parse_intent(raw, client=_FixedJsonClient(payload))
    assert intent.field_provenance["physical_constraints:亲子友好"] == "user_stated"


# ============================================================
# 2. 先验命中 + 原话未提 → 机械回标 prior（冲突时规则赢）
# ============================================================


def test_prior_tag_mismarked_user_stated_gets_corrected_to_prior():
    """u_dad persona 默认带"亲子友好"标签；用户这句话完全没提亲子相关内容，
    LLM 却把它错标成 user_stated——规则应强制纠正为 prior。"""
    raw = "今天下午想出去转转"
    payload = _full_payload(
        raw,
        physical_constraints=["亲子友好"],
        field_provenance={"physical_constraints:亲子友好": "user_stated"},
    )
    intent = parse_intent(raw, client=_FixedJsonClient(payload), user_id="u_dad")
    assert intent.field_provenance["physical_constraints:亲子友好"] == "prior"


# ============================================================
# 3. 值虽等于先验值，但原话确实提到 → 不强制纠正
# ============================================================


def test_prior_tag_not_forced_when_raw_input_literally_mentions_it():
    raw = "今天下午想带孩子去亲子友好的地方"
    payload = _full_payload(
        raw,
        physical_constraints=["亲子友好"],
        field_provenance={"physical_constraints:亲子友好": "user_stated"},
    )
    intent = parse_intent(raw, client=_FixedJsonClient(payload), user_id="u_dad")
    assert intent.field_provenance["physical_constraints:亲子友好"] == "user_stated"


# ============================================================
# 4. 标量字段（distance_max_km / social_context）同样适用先验强制纠正
# ============================================================


def test_distance_equal_to_prior_default_and_unmentioned_forced_to_prior():
    raw = "今天下午想出去转转"  # 完全没提距离
    payload = _full_payload(
        raw,
        distance_max_km=5.0,  # u_dad persona 默认距离恰好是 5.0
        field_provenance={"distance_max_km": "user_stated"},
    )
    intent = parse_intent(raw, client=_FixedJsonClient(payload), user_id="u_dad")
    assert intent.field_provenance["distance_max_km"] == "prior"


def test_distance_not_forced_when_raw_input_mentions_distance():
    raw = "今天下午想出去转转，5公里以内就行"
    payload = _full_payload(
        raw,
        distance_max_km=5.0,
        field_provenance={"distance_max_km": "user_stated"},
    )
    intent = parse_intent(raw, client=_FixedJsonClient(payload), user_id="u_dad")
    assert intent.field_provenance["distance_max_km"] == "user_stated"


def test_social_context_self_reported_inferred_and_equal_to_persona_default_stays_inferred():
    """ADR-0014 横向深审 P1：`social_context` 已从 forced_prior 校正中豁免——
    LLM 自报 `inferred`（"我猜你想要…"旗舰话术依赖的出处），即使推断值恰好
    等于 persona 先验首选（u_dad 的 `suitable_for_priority[0]` == "家庭日常"），
    也不该被机械纠正成 `prior`。`social_context` 本就"几乎总是 inferred"
    （从整句话综合推断场景，原话不会逐字出现"家庭日常"四个字），字面等值
    不是"抄了先验"的可靠信号——豁免前这里会被强纠正成 `prior`，压制旗舰
    话术；豁免后原样保留 LLM 自报。"""
    raw = "今天下午想出去转转"
    payload = _full_payload(
        raw,
        social_context="家庭日常",  # u_dad persona 的 suitable_for_priority[0]
        field_provenance={"social_context": "inferred"},
    )
    intent = parse_intent(raw, client=_FixedJsonClient(payload), user_id="u_dad")
    assert intent.field_provenance["social_context"] == "inferred"


def test_social_context_self_reported_user_stated_and_equal_to_persona_default_stays_user_stated():
    """同上，覆盖自报 `user_stated` 的情形——`social_context` 豁免后无论
    LLM 自报什么出处，只要原话没有可疑的自相矛盾，一律信任自报，不再有任何
    "字面等于先验值就强纠正"的分支（同 distance_max_km 的既有分支相区分，
    该分支保留不动，见下方测试 4）。"""
    raw = "今天下午想出去转转"
    payload = _full_payload(
        raw,
        social_context="家庭日常",
        field_provenance={"social_context": "user_stated"},
    )
    intent = parse_intent(raw, client=_FixedJsonClient(payload), user_id="u_dad")
    assert intent.field_provenance["social_context"] == "user_stated"


# ============================================================
# 5. 自报缺失时按 schema 默认值兜底
# ============================================================


def test_missing_self_report_backfills_default_for_schema_default_value():
    raw = "随便看看"
    payload = _full_payload(raw, field_provenance={})  # 完全没自报
    intent = parse_intent(raw, client=_FixedJsonClient(payload))
    # distance_max_km 停在 schema 默认值 5.0，且无自报、无 user_id 先验 → default
    assert intent.field_provenance["distance_max_km"] == "default"


def test_missing_self_report_backfills_user_stated_for_non_default_value():
    raw = "3公里以内"
    payload = _full_payload(raw, distance_max_km=3.0, field_provenance={})
    intent = parse_intent(raw, client=_FixedJsonClient(payload))
    assert intent.field_provenance["distance_max_km"] == "user_stated"


# ============================================================
# 6. ADR-0014 决策 3（G-3）：预算一等字段——定量/定性分轨
# ============================================================
#
# 用固定 JSON 假 client 模拟"LLM 已经按 intent_parser_prompt.py【预算抽取
# 规则】正确抽取"的输出（同本文件其它测试的手法：不测 LLM 的自然语言理解
# 能力，只测 parse_intent 的规则交叉校正 + provenance 管道对 budget_per_person
# 的处理是否正确接线）。canonical 原句取自 `DEMO_SCENARIOS`（单一真相源，
# 不在测试里另起一份重复文案）。


def test_s2_canonical_quantitative_budget_becomes_user_stated():
    """S2"今晚和兄弟出来撸串喝点酒，人均 50 左右就行"——原话明说数字 →
    budget_per_person=50 且出处 user_stated（G-3 完整解决 S2，见 ADR 诚实声明）。
    """
    raw = DEMO_SCENARIOS[1]["input"]
    assert raw == "今晚和兄弟出来撸串喝点酒，人均 50 左右就行"
    payload = _full_payload(
        raw,
        budget_per_person=50,
        field_provenance={"budget_per_person": "user_stated"},
    )
    intent = parse_intent(raw, client=_FixedJsonClient(payload))
    assert intent.budget_per_person == 50
    assert intent.field_provenance["budget_per_person"] == "user_stated"


def test_s1_canonical_qualitative_budget_stays_none_and_is_heard():
    """S1"周五晚上和室友 4 个人想去 K 歌，预算别太贵"——定性表达，系统不编造
    数字：budget_per_person 应为 None（不硬映射），"budget_per_person" 键
    不应出现在 field_provenance 里（没有值就没有出处可言，见 parser 通用
    None-guard：`_apply_provenance_correction` 循环对 None 值直接 continue）。
    "被听见"体现在 ambiguous_fields 自报（G-3：S1 只解决"被听见"，不解决精确
    匹配——见 ADR 诚实声明），narration 层的消费见
    `tests/test_narrator_provenance_disclosure.py` 的 budget_ambiguous 测试。
    """
    raw = DEMO_SCENARIOS[0]["input"]
    assert raw == "周五晚上和室友 4 个人想去 K 歌，预算别太贵"
    payload = _full_payload(
        raw,
        budget_per_person=None,
        ambiguous_fields=["budget_per_person"],
        field_provenance={},
    )
    intent = parse_intent(raw, client=_FixedJsonClient(payload))
    assert intent.budget_per_person is None
    assert "budget_per_person" not in intent.field_provenance
    assert "budget_per_person" in intent.ambiguous_fields


def test_budget_missing_self_report_backfills_user_stated():
    """自报缺失时（LLM 没给 field_provenance 键，只给了数字）→ 按通用兜底
    规则落 user_stated（budget_per_person 没有"自然默认值"可比对，任何非
    None 值都走 else 分支得到 user_stated，同 distance_max_km 非默认值场景）。
    """
    raw = "人均 80 就行"
    payload = _full_payload(raw, budget_per_person=80, field_provenance={})
    intent = parse_intent(raw, client=_FixedJsonClient(payload))
    assert intent.field_provenance["budget_per_person"] == "user_stated"
