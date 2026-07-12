"""test_party_size_floor —— 房间人数地板注入图 intent 节点的回归。

规则（用户定稿，2026-07-12）：协作房间里，用户没明说人数时，party_size 默认按
房间在场人数算；明说的更大值不被拉低；单人路径（floor=0）逐字零影响。

注入机制：房间规划把 `party_size_floor` 穿进图 State，intent 节点抽完意图后
`capacity_requirement = max(原值, floor)`——一处生效全链路（搜餐容量过滤 /
execute_finalize 预约头数 / critic 校验同源读它）。本测试锚这一处的正确性
（房间编排层的 ≥2 门控 / 明说基线 / 进人 +1 由全后端套件的 room 用例覆盖）。
"""

from __future__ import annotations

import pytest

from agent.graph.nodes import intent as intent_mod
from schemas.intent import IntentExtraction


def _vague_intent(capacity=None) -> IntentExtraction:
    """一个"没明说人数"的意图（companions 空）——最容易被地板兜底的场景。"""
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[2, 3],
        distance_max_km=5.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="朋友热闹",
        raw_input="找个地方逛逛",
        parse_confidence=0.9,
        capacity_requirement=capacity,
    )


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    # parse_intent 被 stub，client 不会被真正使用；仍 stub get_llm_client 免依赖 .env。
    monkeypatch.setattr(intent_mod, "get_llm_client", lambda: None)


def test_floor_lifts_capacity_when_user_vague(monkeypatch):
    """没明说人数 + floor=3（3 人房）→ capacity_requirement 抬到 3。"""
    monkeypatch.setattr(intent_mod, "parse_intent", lambda *a, **k: _vague_intent())
    out = intent_mod.intent_node({"user_input": "找个地方逛逛", "party_size_floor": 3})
    assert out["intent"].capacity_requirement == 3


def test_floor_zero_is_noop_single_user(monkeypatch):
    """floor=0（单人路径/1 人房）→ 不动，capacity 保持原样（None）。"""
    monkeypatch.setattr(intent_mod, "parse_intent", lambda *a, **k: _vague_intent())
    out = intent_mod.intent_node({"user_input": "找个地方逛逛", "party_size_floor": 0})
    assert out["intent"].capacity_requirement is None


def test_floor_missing_is_noop(monkeypatch):
    """state 无 party_size_floor 键（单人图路径根本不传）→ 零影响。"""
    monkeypatch.setattr(intent_mod, "parse_intent", lambda *a, **k: _vague_intent())
    out = intent_mod.intent_node({"user_input": "找个地方逛逛"})
    assert out["intent"].capacity_requirement is None


def test_explicit_larger_not_lowered(monkeypatch):
    """用户明说更大（capacity=6）+ floor=3 → 取 max，不被地板拉低到 3。"""
    monkeypatch.setattr(intent_mod, "parse_intent", lambda *a, **k: _vague_intent(capacity=6))
    out = intent_mod.intent_node({"user_input": "六个人", "party_size_floor": 3})
    assert out["intent"].capacity_requirement == 6
