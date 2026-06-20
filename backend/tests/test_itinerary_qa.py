"""test_itinerary_qa —— 对已有方案提问的接地问答（grounded QA + 弃答）。

用 mock_data 里真实的 P001 / R001 构造一份方案，验证：字段命中→按数据答、查不到→诚实弃答、
疑问式改请求→不当提问、L3 集成把提问路由成 chitchat 回答（而非重规划）。
conftest 已把 SHANGWUJU_MOCK_DIR 指向仓库 mock_data，load_pois/load_restaurants 直接可用。
"""

from __future__ import annotations

from agent.core.itinerary_qa import (
    answer_itinerary_question,
    build_question_decision,
    looks_like_question,
)


def _itin() -> dict:
    # P001 森林儿童探索乐园（distance 4.2, 门票 80-120, 09:00-18:00）
    # R001 轻语沙拉·西溪店（avg_price 75, distance 2.1, 17:30 可订, 招牌 牛油果藜麦碗…）
    return {
        "nodes": [
            {"target_kind": "home", "target_id": "home", "title": "家"},
            {"target_kind": "poi", "target_id": "P001", "title": "森林儿童探索乐园"},
            {"target_kind": "restaurant", "target_id": "R001", "title": "轻语沙拉"},
        ]
    }


# ---- 1. 提问识别 ----

def test_looks_like_question():
    assert looks_like_question("这家贵不贵")
    assert looks_like_question("这个公园远吗")
    assert looks_like_question("几点关门")
    assert not looks_like_question("我妈膝盖不好")
    assert not looks_like_question("你好呀")


# ---- 2. 字段命中 → 按数据回答（grounded）----

def test_answer_price_from_data():
    a = answer_itinerary_question("这家餐厅贵不贵", _itin())
    assert a and "人均" in a and "75" in a


def test_answer_distance_from_data():
    a = answer_itinerary_question("这个公园远吗", _itin())
    assert a and "公里" in a and ("4.2" in a or "2.1" in a)


def test_answer_hours_from_data():
    a = answer_itinerary_question("几点关门", _itin())
    assert a and "09:00" in a


def test_answer_queue_from_data():
    a = answer_itinerary_question("要等位吗", _itin())
    assert a and "17:30" in a


def test_answer_signature_from_data():
    a = answer_itinerary_question("有什么招牌菜", _itin())
    assert a and "牛油果" in a


def test_answer_elderly_honest_when_no_tag():
    # P001/R001 都没有适老标注 → 诚实说没有明确标注（grounding：不编）
    a = answer_itinerary_question("适合老人吗", _itin())
    assert a and "没有明确" in a


# ---- 3. 弃答（abstention）：字段查不到 → 诚实说没记录 ----

def test_abstain_when_field_absent():
    a = answer_itinerary_question("有地方停车吗", _itin(), client=None)
    assert a and "没有记录" in a


# ---- 4. 疑问式改请求 / 非提问 → None（交回兜底，不当 QA）----

def test_change_request_not_treated_as_question():
    assert answer_itinerary_question("能不能近一点", _itin()) is None
    assert answer_itinerary_question("帮我换成适老的吗", _itin()) is None


def test_non_question_returns_none():
    assert answer_itinerary_question("我妈膝盖不好", _itin()) is None


def test_no_itinerary_returns_none():
    assert answer_itinerary_question("这家贵不贵", {"nodes": []}) is None


# ---- 5. build_question_decision → chitchat 出口 ----

def test_build_question_decision_is_chitchat():
    d = build_question_decision("这家餐厅贵不贵", _itin())
    assert d is not None
    assert d.input_kind.value == "chitchat"
    assert "人均" in d.reply_text


def test_build_question_decision_none_for_constraint():
    assert build_question_decision("我妈膝盖不好", _itin()) is None


# ---- 6. router L3 集成：ambiguous + 提问 → chitchat 回答（不重规划）----

def test_router_l3_question_becomes_chitchat_answer(monkeypatch):
    from agent.graph.nodes import router as router_mod
    from agent.graph.state import make_initial_state
    from schemas.router import InputKind, RouterDecision

    def _ambiguous(*a, **k):
        return RouterDecision(
            input_kind=InputKind("ambiguous"), confidence=0.7,
            reply_text="?", tone="warm", cta_chips=[], rationale="t",
        )

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(router_mod, "classify_input", _ambiguous)
    st = make_initial_state(user_input="这家餐厅贵不贵", session_id="s1")
    st["itinerary"] = _itin()

    out = router_mod.router_node(st)
    assert out["route_kind"] == "chitchat", f"提问应被回答而非重规划，实际 {out['route_kind']}"
    assert "人均" in out["router_decision"].reply_text
