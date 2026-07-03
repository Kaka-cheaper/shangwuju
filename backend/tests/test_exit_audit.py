"""tests.test_exit_audit —— ADR-0014 决策 2（G-2）出口满足度审计单测。

覆盖 `agent.planning.critic.exit_audit.audit_constraint_relaxation`：
1. soft tag 未满足 + 出处 = user_stated/prior/inferred → 各自生成对应口径的
   CONSTRAINT_RELAXED advisory
2. soft tag 未满足 + 出处 = default / 无出处数据 → 不产生 advisory（不打扰）
3. hard tag 不在本审计职责范围内（即使未满足也不产生 advisory——那是
   check_dietary/check_physical 的 gate 职责）
4. soft tag 已满足 → 不产生 advisory
5. 三类字段（dietary/physical/experience）各自独立核验

ADR-0014 横向深审 P2 补丁（逐节点判定）新增覆盖：
6. 多 restaurant / 多 poi 节点场景——部分节点满足、部分不满足 → advisory
   点名哪些站没对上，已满足的站不出现在文案里（与 check_dietary/
   check_physical 的逐节点 ALL-match 同一粒度，不再被"并集里凑得出这个
   tag 就算满足"的旧逻辑误判）
7. 全部同类节点都满足 → 不告知
8. 同一 tag 在多个节点都未满足 → 合并成一条 advisory，站名都点出来（去重
   键拍板：按 tag 合并，不按站拆分）
9. 该类型节点为空（如整趟没有餐厅）→ 不臆造"全部未满足"（与
   check_dietary/check_physical 遍历 0 个节点时零违规的空集短路行为一致）
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


def _make_multi_node_itinerary(
    mid_specs: list[tuple[str, str, str, str]],
) -> Itinerary:
    """任意个中间节点的行程（ADR-0014 横向深审 P2：多餐厅/多 POI 场景）。

    `mid_specs`: `[(kind, target_kind, target_id, title), ...]`——exit_audit
    只读 target_kind/target_id/title，时间戳/时长/hop 细节本模块不校验
    （Pydantic 层唯一强制的不变量是首尾 home + hops 长度，见 schemas.
    itinerary.Itinerary._check_invariants），故用等长占位时间即可。
    """
    nodes = [
        ActivityNode(
            node_id="n0", kind="起点", target_kind="home", target_id="home",
            start_time="09:00", duration_min=0, title="出发",
        )
    ]
    t = 9 * 60 + 10
    for i, (kind, target_kind, target_id, title) in enumerate(mid_specs, start=1):
        nodes.append(
            ActivityNode(
                node_id=f"n{i}", kind=kind, target_kind=target_kind,
                target_id=target_id, start_time=f"{t // 60:02d}:{t % 60:02d}",
                duration_min=60, title=title,
            )
        )
        t += 70
    nodes.append(
        ActivityNode(
            node_id=f"n{len(mid_specs) + 1}", kind="终点", target_kind="home",
            target_id="home", start_time=f"{t // 60:02d}:{t % 60:02d}",
            duration_min=0, title="回家",
        )
    )
    hops = [
        Hop(
            hop_id=f"h{i}", from_node_id=nodes[i].node_id,
            to_node_id=nodes[i + 1].node_id, start_time=nodes[i].start_time,
            minutes=5, mode="taxi", path_type="real_route", buffer_min=0,
        )
        for i in range(len(nodes) - 1)
    ]
    return Itinerary(summary="测试方案", nodes=nodes, hops=hops, total_minutes=t)


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


# ============================================================
# ADR-0014 横向深审 P2：逐节点判定（多餐厅 / 多 POI 场景）
# ============================================================
#
# 旧实现把同类节点 tags 取并集判满足——两顿饭一顿日料就会被误判"日料已满足"。
# 以下测试钉住"每个该类型节点独立核验"的新语义：先在旧实现上会失败（并集
# 里能凑出 R050 的「日料」，误判满足 → advisories == []，与断言的"应产生 1
# 条并点名 R001"矛盾），新实现应变绿。


def test_dietary_partial_satisfaction_multi_restaurant_names_unmet_station():
    """两顿饭一顿日料（R050）一顿不是（R001）→ 只点名没对上的那一站。

    R050=东京拉面·tags 含日料；R001=轻语沙拉·tags 不含日料。旧实现取两家
    tags 并集，日料在并集里出现就判满足——这正是深审揪出的 bug。新实现应
    产出 1 条 advisory，点名 R001 对应的站，不点名 R050。
    """
    intent = _make_intent(
        dietary_constraints=["日料"],
        field_provenance={"dietary_constraints:日料": "user_stated"},
    )
    itinerary = _make_multi_node_itinerary(
        [
            ("用餐", "restaurant", "R050", "东京拉面"),
            ("用餐", "restaurant", "R001", "轻语沙拉"),
        ]
    )

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert len(advisories) == 1
    message = advisories[0].message
    assert "日料" in message
    assert "轻语沙拉" in message  # 没对上的那一站被点名
    assert "东京拉面" not in message  # 已满足的站不出现


def test_dietary_full_satisfaction_multi_restaurant_no_advisory():
    """两家餐厅都各自满足「日料」→ 不告知（与单餐厅场景语义一致）。"""
    intent = _make_intent(
        dietary_constraints=["日料"],
        field_provenance={"dietary_constraints:日料": "user_stated"},
    )
    itinerary = _make_multi_node_itinerary(
        [
            ("用餐", "restaurant", "R050", "东京拉面"),
            ("用餐", "restaurant", "R008", "金樽日料会所"),
        ]
    )

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert advisories == []


def test_physical_partial_satisfaction_multi_poi_names_unmet_station():
    """两个 POI 一个低强度（P010）一个不是（P032）→ 只点名没对上的那一站。"""
    intent = _make_intent(
        physical_constraints=["低强度"],
        field_provenance={"physical_constraints:低强度": "prior"},
    )
    itinerary = _make_multi_node_itinerary(
        [
            ("主活动", "poi", "P010", "舒愈SPA"),
            ("主活动", "poi", "P032", "硬核燃力健身工坊"),
        ]
    )

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert len(advisories) == 1
    message = advisories[0].message
    assert "低强度" in message
    assert "硬核燃力健身工坊" in message
    assert "舒愈SPA" not in message


def test_physical_full_satisfaction_multi_poi_no_advisory():
    """两个 POI 都各自满足「低强度」→ 不告知。"""
    intent = _make_intent(
        physical_constraints=["低强度"],
        field_provenance={"physical_constraints:低强度": "prior"},
    )
    itinerary = _make_multi_node_itinerary(
        [
            ("主活动", "poi", "P010", "舒愈SPA"),
            ("主活动", "poi", "P003", "城市儿童博物馆"),
        ]
    )

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert advisories == []


def test_same_tag_unmet_at_multiple_stations_merges_into_one_advisory():
    """同一 tag 在两个餐厅都未满足 → 合并成 1 条 advisory，两站都点名。

    去重键拍板（任务报告详述取舍）：按 tag 合并而非按站拆分——避免告知
    条数随节点数线性增长，挤占 narrator 侧 ≤2 条限额本该露出的其它内容。
    """
    intent = _make_intent(
        dietary_constraints=["日料"],
        field_provenance={"dietary_constraints:日料": "user_stated"},
    )
    itinerary = _make_multi_node_itinerary(
        [
            ("用餐", "restaurant", "R001", "轻语沙拉"),
            ("用餐", "restaurant", "R003", "蔬田主义"),
        ]
    )

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert len(advisories) == 1  # 合并成一条，不是两条
    message = advisories[0].message
    assert "轻语沙拉" in message
    assert "蔬田主义" in message


def test_dietary_no_restaurant_node_does_not_fabricate_unmet():
    """整趟行程没有任何 restaurant 节点 → 不告知（与 check_dietary 遍历 0
    个匹配节点时零违规的空集短路行为一致，不臆造"全部未满足"）。

    ADR-0010 决策 9：多活动 TOPTW 允许"多 POI 无饭"的合法涌现组成。
    """
    intent = _make_intent(
        dietary_constraints=["日料"],
        field_provenance={"dietary_constraints:日料": "user_stated"},
    )
    itinerary = _make_multi_node_itinerary(
        [("主活动", "poi", "P040", "无障碍亲子博物馆")]
    )

    advisories = audit_constraint_relaxation(itinerary, intent)
    assert advisories == []
