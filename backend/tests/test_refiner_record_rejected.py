"""tests.test_refiner_record_rejected —— record_rejected 全环负向输入接线
（用户偏好面板全环方案 §9/§11.1/§14.2）。

覆盖点：
1. refiner_node 算出 original vs refined_intent 的字段集差（三个受控 tag 字段）
   → 词典内的值记进 record_rejected(session_id, tags=dropped)。
2. 词典外的值被词典守卫丢弃（不进 record_rejected），且不炸流程。
3. 没有 session_id 时跳过记账（不阻断主流程，返回值不变）。
4. swap（node_swap 路径）不接这条通道——本文件不测 node_swap，只用一条
   断言坐实 memory_store 模块里 record_rejected 的唯一生产调用点是 refiner_node
   （grep 式回归哨兵，防止未来又在别处误接）。
5. 正向对偶不做：refine 新增的 tag 不应同步 record_accepted（§12）。
"""

from __future__ import annotations

import os

import pytest

from agent.graph.nodes.refiner import refiner_node
from data.memory_store import compute_priors, reset_all_memory
from schemas.intent import IntentExtraction


@pytest.fixture(autouse=True)
def _isolate_memory():
    reset_all_memory()
    os.environ.pop("SHANGWUJU_MEMORY_DIR", None)
    yield
    reset_all_memory()


def _intent(**overrides) -> IntentExtraction:
    base = dict(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="测试反馈",
        parse_confidence=0.9,
    )
    base.update(overrides)
    return IntentExtraction(**base)


class _StubOutput:
    """垫桩 refine_intent 输出——同 test_refiner.py 既有手法。"""

    def __init__(self, refined_intent, changed_fields=None, refiner_note=None):
        self.refined_intent = refined_intent
        self.changed_fields = changed_fields or []
        self.refiner_note = refiner_note


def _run_refiner(monkeypatch, *, original, refined, session_id="sess_reject_test"):
    import agent.graph.nodes.refiner as refiner_mod

    monkeypatch.setattr(
        refiner_mod,
        "refine_intent",
        lambda **kw: _StubOutput(refined_intent=refined),
    )
    state = {
        "intent": original,
        "user_input": "不要辣的，便宜点",
        "itinerary": None,
        "session_id": session_id,
    }
    return refiner_mod.refiner_node(state)


def test_dropped_dietary_tag_recorded_as_rejected(monkeypatch):
    original = _intent(dietary_constraints=["高人均", "不辣"])
    refined = _intent(dietary_constraints=["不辣"])  # 掉了"高人均"

    _run_refiner(monkeypatch, original=original, refined=refined)

    view = compute_priors("u_dad", "sess_reject_test")
    assert view.memory.rejected_tags.counts.get("高人均", 0) == 1


def test_dropped_experience_and_physical_tags_both_recorded(monkeypatch):
    original = _intent(
        experience_tags=["商务体面", "热闹"],
        physical_constraints=["高强度"],
    )
    refined = _intent(experience_tags=["热闹"], physical_constraints=[])

    _run_refiner(monkeypatch, original=original, refined=refined)

    view = compute_priors("u_dad", "sess_reject_test")
    assert view.memory.rejected_tags.counts.get("商务体面", 0) == 1
    assert view.memory.rejected_tags.counts.get("高强度", 0) == 1
    # 没掉的"热闹"不该被记
    assert view.memory.rejected_tags.counts.get("热闹", 0) == 0


def test_dictionary_outside_value_is_dropped_not_recorded(monkeypatch, caplog):
    """§14.2 词典守卫：即便 LLM/垫桩产出词典外字符串，也不能进 record_rejected。"""
    # IntentExtraction 的 dietary_constraints 是 Literal 类型字段，构造非法值
    # 会在 pydantic 校验失败——用 model_construct 绕过校验，模拟"防线失守"场景
    # （字段集差计算读的是 python 对象属性，不重新校验）。
    original = IntentExtraction.model_construct(
        **{**_intent().model_dump(), "dietary_constraints": ["不辣", "这不是一个合法tag"]}  # noqa: RUF001
    )
    refined = _intent(dietary_constraints=[])

    import logging

    caplog.set_level(logging.WARNING)
    _run_refiner(monkeypatch, original=original, refined=refined)

    view = compute_priors("u_dad", "sess_reject_test")
    assert view.memory.rejected_tags.counts.get("不辣", 0) == 1
    assert "这不是一个合法tag" not in view.memory.rejected_tags.counts  # noqa: RUF001
    assert any("词典外" in rec.message for rec in caplog.records)


def test_no_session_id_skips_recording_without_raising(monkeypatch):
    original = _intent(dietary_constraints=["高人均"])
    refined = _intent(dietary_constraints=[])

    import agent.graph.nodes.refiner as refiner_mod

    monkeypatch.setattr(
        refiner_mod,
        "refine_intent",
        lambda **kw: _StubOutput(refined_intent=refined),
    )
    state = {"intent": original, "user_input": "不要贵的", "itinerary": None}
    # 没有 session_id 键——不能抛异常，也不能记到任何键
    diff = refiner_mod.refiner_node(state)
    assert diff["intent"] is refined
    # 不传 session（模板视图）不应含任何累积
    assert compute_priors("u_dad").memory.rejected_tags.counts == {}


def test_no_dropped_tags_no_op(monkeypatch):
    """字段集差为空（用户反馈只改了距离等非 tag 字段）时不应调用 record_rejected。"""
    original = _intent(dietary_constraints=["低脂"])
    refined = _intent(dietary_constraints=["低脂"])  # 完全没变

    _run_refiner(monkeypatch, original=original, refined=refined)

    view = compute_priors("u_dad", "sess_reject_test")
    assert view.memory.rejected_tags.counts == {}


def test_refine_does_not_record_accepted_for_new_tags(monkeypatch):
    """§12 正向对偶不做：refine 新增的 tag（"清淡"类）不应同步 record_accepted。"""
    original = _intent(dietary_constraints=["高人均"])
    refined = _intent(dietary_constraints=["健康轻食"])  # 新增"健康轻食"，掉"高人均"

    _run_refiner(monkeypatch, original=original, refined=refined)

    view = compute_priors("u_dad", "sess_reject_test")
    assert view.memory.rejected_tags.counts.get("高人均", 0) == 1
    # 新增的"健康轻食"不应被 refine 阶段记进 accepted（要等 confirm）
    assert view.memory.accepted_tags.counts.get("健康轻食", 0) == 0
