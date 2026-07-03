"""tests.test_exit_audit —— ADR-0014 决策 2（G-2）出口满足度审计单测。

覆盖 `agent.planning.critic.exit_audit.audit_constraint_relaxation`：
1. soft tag 未满足 + 出处 = user_stated/prior/inferred → 各自生成对应口径的
   CONSTRAINT_RELAXED advisory
2. soft tag 未满足 + 出处 = default / 无出处数据 → 不产生 advisory（不打扰）
3. hard tag 不在本审计职责范围内（即使未满足也不产生 advisory——那是
   check_dietary/check_physical 的 gate 职责）
4. soft tag 已满足 → 不产生 advisory
5. 三类字段（dietary/physical/experience）各自独立核验
"""

from __future__ import annotations

from agent.planning.critic.exit_audit import audit_constraint_relaxation
from schemas.advisory import AdvisoryCode
from schemas.intent import IntentExtraction
from schemas.itinerary import ActivityNode, Hop, Itinerary


def _make_intent(
    *,
    dietary_constraints: list[str] | None = None,
    physical_constraints: list[str] | None = None,
    experience_tags: list[str] | None = None,
    field_provenance: dict[str, str] | None = None,
) -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=[4, 6],
        distance_max_km=10.0,
        companions=[],
        physical_constraints=physical_constraints or [],
        dietary_constraints=dietary_constraints or [],
        experience_tags=experience_tags or [],
        social_context="家庭日常",
        raw_input="测试输入",
        parse_confidence=0.9,
        field_provenance=field_provenance,
    )


def _make_itinerary(*, poi_id: str = "P040", restaurant_id: str = "R001") -> Itinerary:
    """4 nodes / 3 hops 最小行程（复用 test_critics_v2.py 同款默认实体：
    P040=无障碍亲子博物馆·三代同堂友好馆，R001=轻语沙拉/tags=[低脂]）。
    """
    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home", start_time="14:00", duration_min=0, title="出发"),
        ActivityNode(node_id="n1", kind="主活动", target_kind="poi", target_id=poi_id, start_time="14:09", duration_min=165, title=poi_id),
        ActivityNode(node_id="n2", kind="用餐", target_kind="restaurant", target_id=restaurant_id, start_time="17:30", duration_min=60, title=restaurant_id),
        ActivityNode(node_id="n3", kind="终点", target_kind="home", target_id="home", start_time="18:37", duration_min=0, title="回家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="14:00", minutes=9, mode="taxi", path_type="real_route", buffer_min=0),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="16:54", minutes=5, mode="taxi", path_type="real_route", buffer_min=5),
        Hop(hop_id="h2", from_node_id="n2", to_node_id="n3", start_time="18:30", minutes=7, mode="taxi", path_type="real_route", buffer_min=0),
    ]
    return Itinerary(summary="测试方案", nodes=nodes, hops=hops, total_minutes=277)


def test_unmet_soft_dietary_user_stated_produces_advisory():
    """dietary soft tag（日料）未满足 + user_stated → 「你说的」口径告知。"""
    intent = _make_intent(
        dietary_constraints=["日料"],  # R001 tags=[低脂]，不含日料
        field_provenance={"dietary_constraints:日料": "user_stated"},
    )
    itinerary = _make_itinerary()

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert len(advisories) == 1
    assert advisories[0].code == AdvisoryCode.CONSTRAINT_RELAXED
    assert "你说的" in advisories[0].message
    assert "日料" in advisories[0].message


def test_unmet_soft_physical_prior_produces_advisory():
    """physical soft tag（适合青少年）未满足 + prior → 「你档案里的」口径告知。"""
    intent = _make_intent(
        physical_constraints=["适合青少年"],  # P040 tags 不含
        field_provenance={"physical_constraints:适合青少年": "prior"},
    )
    itinerary = _make_itinerary()

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert len(advisories) == 1
    assert "你档案里的" in advisories[0].message
    assert "适合青少年" in advisories[0].message


def test_unmet_soft_experience_inferred_produces_advisory():
    """experience tag（网红打卡，全 soft）未满足 + inferred → 「我猜你想要的」口径告知。"""
    intent = _make_intent(
        experience_tags=["网红打卡"],  # 既不在 P040 也不在 R001 的 tags 里
        field_provenance={"experience_tags:网红打卡": "inferred"},
    )
    itinerary = _make_itinerary()

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert len(advisories) == 1
    assert "我猜你想要的" in advisories[0].message
    assert "网红打卡" in advisories[0].message


def test_unmet_soft_default_provenance_does_not_disturb():
    """soft tag 未满足 + 出处 = default → 不产生 advisory（不打扰）。"""
    intent = _make_intent(
        dietary_constraints=["日料"],
        field_provenance={"dietary_constraints:日料": "default"},
    )
    itinerary = _make_itinerary()

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert advisories == []


def test_unmet_soft_no_provenance_data_does_not_disturb():
    """intent.field_provenance 整体为 None（旧 checkpoint）→ 不产生 advisory。"""
    intent = _make_intent(dietary_constraints=["日料"], field_provenance=None)
    itinerary = _make_itinerary()

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert advisories == []


def test_hard_tag_not_in_exit_audit_scope():
    """hard tag（不辣）未满足不产生 advisory——那是 check_dietary 的 gate 职责，
    不是出口满足度审计（soft-only）的职责。"""
    intent = _make_intent(
        dietary_constraints=["不辣"],  # R001 tags=[低脂]，不含不辣；且不辣是 hard
        field_provenance={"dietary_constraints:不辣": "user_stated"},
    )
    itinerary = _make_itinerary()

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert advisories == []


def test_satisfied_soft_tag_no_advisory():
    """soft tag 已被最终节点满足 → 不产生 advisory。"""
    intent = _make_intent(
        dietary_constraints=["低脂"],  # R001 tags 含低脂
        field_provenance={"dietary_constraints:低脂": "user_stated"},
    )
    itinerary = _make_itinerary()

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert advisories == []


def test_multiple_unmet_fields_each_independently_audited():
    """dietary + physical + experience 三类字段的未满足各自独立产出。"""
    intent = _make_intent(
        dietary_constraints=["日料"],
        physical_constraints=["适合青少年"],
        experience_tags=["网红打卡"],
        field_provenance={
            "dietary_constraints:日料": "user_stated",
            "physical_constraints:适合青少年": "prior",
            "experience_tags:网红打卡": "inferred",
        },
    )
    itinerary = _make_itinerary()

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert len(advisories) == 3
    messages = [a.message for a in advisories]
    assert any("日料" in m for m in messages)
    assert any("适合青少年" in m for m in messages)
    assert any("网红打卡" in m for m in messages)
