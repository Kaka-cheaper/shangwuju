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


# ---- 3. 弃答（abstention）：字段没对上 → 坦白没对上 + 经验标注 ----

def test_abstain_when_field_absent():
    """分界修缮批 任务 5：措辞判据变更——弃答不再断言「没有记录」。

    旧判据钉的是「没有记录」字面；但那句是假负面断言：字段 cue 词表
    （itinerary_qa._FIELD_CUES）未命中 ≠ 数据缺失——数据可能明明有（如停车
    信息哪天进了 mock），只是识别没接上。确定域的事实断言不许编造，负面断言
    也一样。新判据：坦白「没对上」（识别层面的诚实）+ 经验标注语义保留，
    **绝不**出现「没有记录」这类宣称数据缺失的字面。
    """
    a = answer_itinerary_question("有地方停车吗", _itin(), client=None)
    assert a and "没对上" in a
    assert "没有记录" not in a, "识别未命中不等于数据缺失，不许对用户下假负面断言"


def test_abstain_output_clamped_to_reply_text_limit():
    """弃答文案钳制——LLM 无视「不超过 60 字」约束长篇大论时，输出必须压进
    RouterDecision.reply_text 的 max_length=400，否则 router 层构造 decision 时
    ValidationError → 整轮以 stream_error 收场（I 类元对话探针 I3 在 stub 下实锤：
    stub 固定 JSON 顶穿 400 上限把弃答轮整个炸掉；真实模式 prompt 约束 60 字
    通常不炸，但此前代码无任何保险）。"""
    from types import SimpleNamespace

    class _LongWinded:
        def chat(self, messages, **kwargs):  # noqa: ANN003 —— 只需鸭子型 .content
            return SimpleNamespace(content="这附近停车其实要看具体时段和商场政策，" * 40)

    a = answer_itinerary_question("有地方停车吗", _itin(), client=_LongWinded())
    assert a is not None and len(a) <= 400, f"弃答输出未钳制：len={len(a) if a else 0}"


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


# ---- 6. route_turn 集成：提问 → chitchat 回答（不重规划，不触达脑子）----
# ADR-0011 E-2-c：QA 判定从旧 Layer 3（跑在 Layer 2 LLM 分类之后）前移到脑子
# 调用之前（见 route_turn.py Layer 1.8），本用例改为钉住"脑子不会被调用"。


def test_router_l3_question_becomes_chitchat_answer(monkeypatch):
    from agent.graph.nodes import router as router_mod
    from agent.graph.state import make_initial_state

    def _brain_should_not_run(*a, **k):
        raise AssertionError("提问应在规则层被 QA 接住，不该触达脑子")

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(router_mod, "classify_turn", _brain_should_not_run)
    st = make_initial_state(user_input="这家餐厅贵不贵", session_id="s1")
    st["itinerary"] = _itin()

    out = router_mod.router_node(st)
    assert out["route_kind"] == "chitchat", f"提问应被回答而非重规划，实际 {out['route_kind']}"
    assert "人均" in out["router_decision"].reply_text


# ============================================================
# 7. 三个方案级接地答复器（点火前小修批 任务 3；K7/K9/K10 实锤）
# ============================================================
# 治「数据在≠答得出」：为什么推荐/还有别的选/数据是真的吗 三类问句的材料
# （实体字段+意图命中关系、narrate 预计算的 node_actions、产品事实边界）本来
# 就在现场，但字段词典只有实体数据字段，这三类问句全部落弃答。
# 三纪律：线索词与既有字段同表（单一真相源）/材料拿不到落既有弃答/模板接地
# 零 LLM。client=None 断言即钉死「不调 LLM」：若走了弃答分支会带「没对上」字样。


def _intent_dict() -> dict:
    # 只放答复器消费的字段；dict 形态同时覆盖房间路径（room.current_intent_dict）
    return {
        "dietary_constraints": ["低脂"],
        "experience_tags": [],
        "physical_constraints": [],
        "preferred_poi_types": [],
        "social_context": "家庭日常",
        "distance_max_km": 5.0,
        "budget_per_person": 100.0,
    }


# ---- 7a. 为什么推荐这家 / 为什么这么排（K7）----


def test_answer_why_recommend_grounded_no_llm():
    a = answer_itinerary_question(
        "为什么推荐这家餐厅？", _itin(), client=None, intent=_intent_dict()
    )
    assert a is not None and "没对上" not in a, f"应走接地答复器而非弃答：{a!r}"
    # 实体字段与意图的命中关系组句：评分 + 命中你要的「低脂」（R001 tags=低脂）
    assert "评分" in a
    assert "低脂" in a


def test_answer_why_order_grounded_with_timeline():
    a = answer_itinerary_question(
        "为什么你把餐厅放在活动后面？", _itin(), client=None, intent=_intent_dict()
    )
    assert a is not None and "没对上" not in a
    # 顺序类问法应带方案时间轴接地（节点 title 可见）
    assert "轻语沙拉" in a or "森林儿童探索乐园" in a


def test_answer_why_without_intent_still_grounded():
    # intent 拿不到（如旧会话）→ 退化为纯实体字段组句，仍不弃答、不调 LLM
    a = answer_itinerary_question("为什么推荐这家餐厅？", _itin(), client=None)
    assert a is not None and "没对上" not in a and "评分" in a


# ---- 7b. 还有别的选吗（K9）----


def _node_actions() -> dict:
    # 形状 = narrate._build_node_actions 产出：{target_id: {chips, alternatives}}
    return {
        "R001": {
            "chips": [],
            "alternatives": [
                {
                    "kind": "restaurant",
                    "target_id": "R010",
                    "name": "山葵家精致料理",
                    "rating": 4.6,
                    "distance_km": 1.8,
                    "price": 128.0,
                    "category": "日料",
                }
            ],
        }
    }


def test_answer_alternatives_from_precomputed_node_actions():
    a = answer_itinerary_question(
        "还有别的选吗？",
        _itin(),
        client=None,
        node_actions_provider=lambda: _node_actions(),
    )
    assert a is not None and "没对上" not in a, f"应报预计算备选而非弃答：{a!r}"
    assert "山葵家精致料理" in a, "必须报出备选名"
    assert "换成" in a, "必须带一句引导（前端按钮字面是「换成◯◯」）"


def test_alternatives_unreachable_falls_to_existing_abstain():
    # 漏配纪律：拿不到 node_actions（provider 缺省 / 空 / 抛异常）→ 落既有弃答
    a1 = answer_itinerary_question("还有别的选吗？", _itin(), client=None)
    assert a1 is not None and "没对上" in a1
    a2 = answer_itinerary_question(
        "还有别的选吗？", _itin(), client=None, node_actions_provider=lambda: {}
    )
    assert a2 is not None and "没对上" in a2

    def _boom() -> dict:
        raise RuntimeError("snapshot 组装失败")

    a3 = answer_itinerary_question(
        "还有别的选吗？", _itin(), client=None, node_actions_provider=_boom
    )
    assert a3 is not None and "没对上" in a3


# ---- 7c. 你这数据是真的吗（K10）----

# 与 K10 探针的 _FAKE_REALITY_CLAIM_PHRASES 同一判据（演示原型绝不可宣称
# 真实库存/实时数据/真实预订成功）——诚实边界话术自己先过一遍红线。
_FAKE_REALITY_PHRASES = (
    "真实库存", "实时排队", "实时数据", "已接入真实",
    "已经订好", "预订成功", "已预订", "已下单成功", "真实预订",
)


def test_answer_data_trust_honest_boundary():
    a = answer_itinerary_question(
        "你这个数据是真的吗？是不是随便编的？", _itin(), client=None
    )
    assert a is not None and "没对上" not in a
    assert "演示" in a, "必须诚实说明演示数据边界"
    for phrase in _FAKE_REALITY_PHRASES:
        assert phrase not in a, f"诚实边界话术不得含虚假现实声称短语：{phrase!r}"


# ---- 7d. 词表纪律与出口钳制 ----


def test_new_cues_do_not_shadow_existing_fields():
    # 既有具体字段仍优先：问价钱还是价钱答复器
    a = answer_itinerary_question("这家贵不贵", _itin(), client=None)
    assert a and "人均" in a


def test_why_answer_clamped_to_reply_text_limit():
    # 组句出口必须压进 RouterDecision.reply_text 的 max_length=400（同弃答钳制先例）
    d = build_question_decision(
        "为什么推荐这家餐厅？", _itin(), client=None, intent=_intent_dict()
    )
    assert d is not None and len(d.reply_text) <= 400


def test_alternatives_answer_clamped():
    many = {
        f"R{i:03d}": {
            "chips": [],
            "alternatives": [
                {"kind": "restaurant", "target_id": f"RX{i}{j}", "name": "超长备选名" * 10,
                 "rating": 4.0, "distance_km": 1.0, "price": 80.0, "category": "测试"}
                for j in range(3)
            ],
        }
        for i in range(8)
    }
    d = build_question_decision(
        "还有别的选吗？", _itin(), client=None, node_actions_provider=lambda: many
    )
    assert d is not None and len(d.reply_text) <= 400


# ---- 7e. route_turn / router_node 集成：图状态的 intent 与 node_actions 可达 ----


def test_router_threads_intent_and_node_actions_to_qa(monkeypatch):
    from agent.graph.nodes import router as router_mod
    from agent.graph.state import make_initial_state

    def _brain_should_not_run(*a, **k):
        raise AssertionError("方案级问句应在 Layer 1.8 被接地答复器接住，不该触达脑子")

    monkeypatch.setattr(router_mod, "get_llm_client", lambda *a, **k: object())
    monkeypatch.setattr(router_mod, "classify_turn", _brain_should_not_run)

    st = make_initial_state(user_input="还有别的选吗？", session_id="s_qa_alt")
    st["itinerary"] = _itin()
    st["node_actions"] = _node_actions()

    out = router_mod.router_node(st)
    assert out["route_kind"] == "chitchat"
    assert "山葵家精致料理" in out["router_decision"].reply_text

    st2 = make_initial_state(user_input="为什么推荐这家餐厅？", session_id="s_qa_why")
    st2["itinerary"] = _itin()
    from schemas.intent import IntentExtraction

    st2["intent"] = IntentExtraction(
        start_time="today_afternoon",
        companions=[],
        physical_constraints=[],
        dietary_constraints=["低脂"],
        experience_tags=[],
        raw_input="低脂一点",
        parse_confidence=0.9,
    )
    out2 = router_mod.router_node(st2)
    assert out2["route_kind"] == "chitchat"
    assert "低脂" in out2["router_decision"].reply_text
