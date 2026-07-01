"""tests.test_assemble_age_cap —— ADR-0009 C-2：年龄约束进组装器（方案 α）。

【背景（已代码核实，见 ADR-0009「背景·地基 B」）】

`_assemble_itinerary` 给 POI 节点定 `duration_min = main_activity_minutes`
（由 intent 按段派生、与选哪个 POI / 同行人年龄都无关），从不按年龄 cap。
带幼童/高龄时 critic 的 `check_age_aware_duration` 几乎必然触发；而 ILS
换 POI 修不动此码（时长与选哪个 POI 无关，见 ADR-0009 映射表：
AGE_DURATION_MISMATCH → 决策 2 后组装期已预防，万一触发→地板非 retry 可修）。

本文件钉死修复后的行为：

1. `age_caps.strictest_cap_for_companions`——组装器与 critic 共读的「多代际
   取最严」helper（新增纯函数，供组装器调用；critic 的 `check_age_aware_duration`
   保留自己原有内联实现不变，仅需口径一致）。
2. `_assemble_itinerary` 按 `intent.companions` 把 POI 停留时长夹到
   min(算出的时长, 最严 cap)——单人 / 多代际取最严 / 无年龄不夹 三种场景。
3. cap 是硬天花板：即便 chosen_time 补偿想把时长往长拉，也不得突破 cap
   （ADR-0009 决策 2 子决策：与总时长/对齐 chosen_time 冲突时优先年龄合规）。
4. 顶层生产入口 `plan_itinerary(intent)` 同样生效——这依赖修复
   `plan_itinerary` 调 `_assemble_itinerary` 时遗漏 `intent=`/`user_profile=`
   的插线 bug（旧代码不传，`_assemble_itinerary` 会静默造一个
   `companions=[]` 的占位 intent，年龄 cap 形同虚设）。

【范围决策（cap 加在哪个组装器，已评估并交主代理审）】

cap 加在 `_assemble_itinerary`（rule_planner.py，rule/ILS 两条路径的唯一
组装入口），**不**加在它内部调用的 `assemble_from_blueprint`（LLM 路径 +
本仓库 `test_planner_hybrid.py` 等多个 critic 测试共用的更底层拼装函数）。

实测证据：`test_planner_hybrid.py::_itinerary()` 默认 `_intent()` 自带一个
5 岁孩子（cap 75），其测试助手会刻意构造远超 75 分钟的 POI `duration_min`
以让餐厅节点精确落在指定 `dining_time`（如 17:30）上，供 critic 分支测试用。
若在 `assemble_from_blueprint` 里加 cap，会把这些精心构造的时长砍到 75，
餐厅节点的真实到达时刻随之偏移，导致该文件里一大批与年龄毫不相关的 critic
测试失败——这是与本 ADR 目标无关的连带破坏（collateral breakage），而非
「LLM 路径真实场景」的破坏。相反，`_assemble_itinerary` 是 rule/ILS 专属
入口，两条路径缺的正是「无法靠 backprompt 修复 AGE_DURATION_MISMATCH」的
repair loop（ILS 换 POI/餐厅/时段都不改时长），需要靠组装期预防；LLM 路径
仍走它既有的 critic+backprompt 闭环（LLM 能重新出一版更短时长的蓝图），
不需要在 `assemble_from_blueprint` 里再插一层 cap。
"""

from __future__ import annotations

from data.loader import load_pois, load_restaurants
from schemas.intent import Companion, IntentExtraction

from agent.planning.critic.age_caps import strictest_cap_for_companions
from agent.planning.planners.rule_planner import _assemble_itinerary, plan_itinerary


def _poi(poi_id: str = "P001"):
    return next(p for p in load_pois() if p.id == poi_id)


def _restaurant(rest_id: str = "R001"):
    return next(r for r in load_restaurants() if r.id == rest_id)


def _intent(companions: list[Companion], duration_hours=(3, 5)) -> IntentExtraction:
    return IntentExtraction(
        raw_input="测试",
        social_context="家庭日常",
        companions=companions,
        duration_hours=list(duration_hours),
        distance_max_km=5.0,
        start_time="14:00",
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        parse_confidence=0.9,
    )


def _poi_node(itin):
    return next(n for n in itin.nodes if n.target_kind == "poi")


# ============================================================
# 1. strictest_cap_for_companions（纯函数单测）
# ============================================================


def test_strictest_cap_single_toddler():
    assert strictest_cap_for_companions([Companion(role="孩子", age=3)]) == 45


def test_strictest_cap_single_preschooler():
    assert strictest_cap_for_companions([Companion(role="孩子", age=5)]) == 75


def test_strictest_cap_multi_gen_takes_min():
    """5 岁娃（cap 75）+ 80 岁老人（cap 60）→ 取最严 60（与 check_age_aware_duration 口径一致）。"""
    companions = [Companion(role="孩子", age=5), Companion(role="父母", age=80)]
    assert strictest_cap_for_companions(companions) == 60


def test_strictest_cap_no_age_returns_none():
    assert strictest_cap_for_companions([Companion(role="妻子")]) is None


def test_strictest_cap_empty_or_none_returns_none():
    assert strictest_cap_for_companions([]) is None
    assert strictest_cap_for_companions(None) is None


def test_strictest_cap_unbucketed_age_returns_none():
    """13-74 岁不落任何硬 cap 分桶。"""
    assert strictest_cap_for_companions([Companion(role="朋友", age=30)]) is None


# ============================================================
# 2. _assemble_itinerary：POI 时长夹到 cap（main-only，无 chosen_time 干扰）
# ============================================================


def test_assemble_caps_poi_duration_for_toddler():
    """3 岁娃（cap 45）：main_activity_minutes=120 应被砍到 45（现状不 cap → 先红）。"""
    intent = _intent([Companion(role="孩子", age=3)])
    itin = _assemble_itinerary(
        main_poi=_poi(),
        chosen_restaurant=None,
        chosen_time=None,
        home_to_poi=10,
        poi_to_rest=0,
        rest_to_home=10,
        party_size=1,
        backup_pois=[],
        depart_time="14:00",
        main_activity_minutes=120,
        dining_minutes=0,
        segments=frozenset({"主活动"}),
        intent=intent,
    )
    node = _poi_node(itin)
    assert node.duration_min == 45, f"3 岁娃 cap 应为 45，实际 {node.duration_min}"


def test_assemble_caps_poi_duration_for_preschooler():
    """5 岁娃（cap 75）：main_activity_minutes=120 应被砍到 75。"""
    intent = _intent([Companion(role="孩子", age=5)])
    itin = _assemble_itinerary(
        main_poi=_poi(),
        chosen_restaurant=None,
        chosen_time=None,
        home_to_poi=10,
        poi_to_rest=0,
        rest_to_home=10,
        party_size=1,
        backup_pois=[],
        depart_time="14:00",
        main_activity_minutes=120,
        dining_minutes=0,
        segments=frozenset({"主活动"}),
        intent=intent,
    )
    node = _poi_node(itin)
    assert node.duration_min == 75, f"5 岁娃 cap 应为 75，实际 {node.duration_min}"


def test_assemble_multi_gen_takes_strictest_cap():
    """5 岁娃（75）+ 80 岁老人（60）同行 → 组装器也取最严 60（与 critic 口径一致）。"""
    intent = _intent([Companion(role="孩子", age=5), Companion(role="父母", age=80)])
    itin = _assemble_itinerary(
        main_poi=_poi(),
        chosen_restaurant=None,
        chosen_time=None,
        home_to_poi=10,
        poi_to_rest=0,
        rest_to_home=10,
        party_size=2,
        backup_pois=[],
        depart_time="14:00",
        main_activity_minutes=120,
        dining_minutes=0,
        segments=frozenset({"主活动"}),
        intent=intent,
    )
    node = _poi_node(itin)
    assert node.duration_min == 60, f"多代际应取最严 60，实际 {node.duration_min}"


def test_assemble_no_age_cap_when_companions_have_no_age():
    """无年龄同行人（如「妻子」不填 age）→ 不夹时长，保留组装器原有行为（characterization）。"""
    intent = _intent([Companion(role="妻子")])
    itin = _assemble_itinerary(
        main_poi=_poi(),
        chosen_restaurant=None,
        chosen_time=None,
        home_to_poi=10,
        poi_to_rest=0,
        rest_to_home=10,
        party_size=1,
        backup_pois=[],
        depart_time="14:00",
        main_activity_minutes=200,
        dining_minutes=0,
        segments=frozenset({"主活动"}),
        intent=intent,
    )
    node = _poi_node(itin)
    assert node.duration_min == 200, (
        f"无年龄同行人不应被年龄 cap 误伤，实际 {node.duration_min}"
    )


# ============================================================
# 3. cap 是硬天花板：chosen_time 补偿不得突破 cap（ADR-0009 决策 2 子决策）
# ============================================================


def test_age_cap_overrides_chosen_time_compensation():
    """幼童 + 很晚的 chosen_time：补偿算术想把 POI 时长拉到 335 分钟
    （30 基础 + 305 补偿，见推导：depart 14:00→natural_arrive 14:55，
    chosen 20:00 超出 305min），但 cap（3 岁→45）必须赢——不得因为要凑准点
    而突破年龄合规。
    """
    intent = _intent([Companion(role="孩子", age=3)])
    itin = _assemble_itinerary(
        main_poi=_poi(),
        chosen_restaurant=_restaurant(),
        chosen_time="20:00",  # 远晚于自然到达时刻，补偿会想大幅拉长 POI 停留
        home_to_poi=10,
        poi_to_rest=10,
        rest_to_home=10,
        party_size=1,
        backup_pois=[],
        depart_time="14:00",
        main_activity_minutes=30,  # 远低于 cap，验证补偿而非「本来就短」把它撑大
        dining_minutes=60,
        segments=frozenset({"出发", "主活动", "转场", "用餐", "返回"}),
        intent=intent,
    )
    node = _poi_node(itin)
    assert node.duration_min == 45, (
        f"cap 应赢过 chosen_time 补偿，实际 {node.duration_min}"
        "（若 >45 说明补偿撑破了年龄天花板）"
    )


def test_chosen_time_compensation_still_works_without_age_cap():
    """characterization：同上场景但同行人无年龄 → 补偿逻辑应保持原行为不受影响，
    POI 时长确实被拉到 335（30 基础 + 305 补偿），证明 cap 逻辑没误伤原有补偿。
    """
    intent = _intent([Companion(role="妻子")])
    itin = _assemble_itinerary(
        main_poi=_poi(),
        chosen_restaurant=_restaurant(),
        chosen_time="20:00",
        home_to_poi=10,
        poi_to_rest=10,
        rest_to_home=10,
        party_size=1,
        backup_pois=[],
        depart_time="14:00",
        main_activity_minutes=30,
        dining_minutes=60,
        segments=frozenset({"出发", "主活动", "转场", "用餐", "返回"}),
        intent=intent,
    )
    node = _poi_node(itin)
    assert node.duration_min == 335, (
        "无年龄约束时，chosen_time 补偿应仍能把 POI 时长精确拉到 335"
        f"（实际 {node.duration_min}，说明补偿逻辑本身未被破坏）"
    )


# ============================================================
# 4. 顶层入口 plan_itinerary(intent)：真实生产路径也要生效
#    （钉住 plan_itinerary 调 _assemble_itinerary 时遗漏 intent=/user_profile= 的插线 bug）
# ============================================================


def test_plan_itinerary_end_to_end_caps_poi_for_toddler():
    """3 岁娃、时长充足的家庭场景：走完整 plan_itinerary 生产入口，
    组装出的 POI 节点也必须 ≤ 45min——不能只在直调 _assemble_itinerary 时生效。
    """
    intent = IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[
            Companion(role="妈妈", count=1),
            Companion(role="孩子", age=3, count=1),
        ],
        physical_constraints=["亲子友好"],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="今天下午带 3 岁孩子出去玩",
        parse_confidence=0.9,
    )
    result = plan_itinerary(intent)
    assert result.success, f"应成功：{result.failure_detail}"
    itin = result.itinerary
    assert itin is not None
    poi_nodes = [n for n in itin.nodes if n.target_kind == "poi"]
    assert poi_nodes, "该场景应含至少一个 POI 节点"
    for node in poi_nodes:
        assert node.duration_min <= 45, (
            f"3 岁娃同行，POI 节点 {node.node_id} 停留 {node.duration_min}min "
            "超出 45min 年龄 cap（端到端 plan_itinerary 入口未生效）"
        )


def test_plan_itinerary_end_to_end_caps_poi_for_preschooler():
    """5 岁娃场景：POI 节点应 ≤ 75min。"""
    intent = IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[
            Companion(role="妻子", count=1),
            Companion(role="孩子", age=5, count=1),
        ],
        physical_constraints=["亲子友好", "适合 5-10 岁"],
        dietary_constraints=["低脂", "健康轻食"],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="今天下午想和老婆孩子出去玩几个小时，孩子 5 岁。",
        parse_confidence=0.92,
    )
    result = plan_itinerary(intent)
    assert result.success, f"应成功：{result.failure_detail}"
    itin = result.itinerary
    assert itin is not None
    poi_nodes = [n for n in itin.nodes if n.target_kind == "poi"]
    assert poi_nodes, "该场景应含至少一个 POI 节点"
    for node in poi_nodes:
        assert node.duration_min <= 75, (
            f"5 岁娃同行，POI 节点 {node.node_id} 停留 {node.duration_min}min "
            "超出 75min 年龄 cap（端到端 plan_itinerary 入口未生效）"
        )


# ============================================================
# 5. 乙（ADR-0009 决策 2）：cap 砍短 POI 后，餐厅仍落在 chosen_time
#    —— 餐前留空闲/等待，排定时刻与 note/reservation 自洽
#    （补 C-2 漏掉的自洽层：原实现让餐厅提前到自然到达时刻，note 却仍写 chosen_time）
# ============================================================


def test_capped_poi_keeps_restaurant_at_chosen_time():
    """3 岁娃（cap 45）+ chosen_time 17:00：POI 补偿想拉长以让餐厅落 17:00，但 cap
    砍到 45 → 自然到达提前。乙：餐厅节点 start_time 仍须是 17:00（餐前留空闲补齐），
    与 note「已为你预留 17:00」自洽。现状（无乙）餐厅会提前到自然到达 → 先红。
    """
    intent = _intent([Companion(role="孩子", age=3)])
    itin = _assemble_itinerary(
        main_poi=_poi(),
        chosen_restaurant=_restaurant(),
        chosen_time="17:00",
        home_to_poi=10,
        poi_to_rest=10,
        rest_to_home=10,
        party_size=1,
        backup_pois=[],
        depart_time="14:00",
        main_activity_minutes=30,
        dining_minutes=90,
        segments=frozenset({"出发", "主活动", "转场", "用餐", "返回"}),
        intent=intent,
    )
    poi = _poi_node(itin)
    rest = next(n for n in itin.nodes if n.target_kind == "restaurant")
    assert poi.duration_min == 45, f"POI 应被 cap 到 45，实际 {poi.duration_min}"
    assert rest.start_time == "17:00", (
        f"cap 砍短 POI 后餐厅仍应落 chosen_time 17:00（餐前留空闲），实际 {rest.start_time}"
        "（若更早说明 note 承诺 17:00 与排定时刻矛盾——正是 C-2 引入、乙 修复的 bug）"
    )
    assert rest.note and rest.start_time in rest.note, (
        f"餐厅排定时刻应与 note 承诺一致；start={rest.start_time} note={rest.note!r}"
    )


def test_plan_itinerary_toddler_restaurant_time_coherent_with_note():
    """端到端自洽（乙）：3 岁娃走完整 plan_itinerary，任一餐厅节点的 start_time
    必须与它 note 里承诺的预留时刻一致——绝不能「排 15:56 却说预留 17:00」。
    """
    intent = IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[
            Companion(role="妈妈", count=1),
            Companion(role="孩子", age=3, count=1),
        ],
        physical_constraints=["亲子友好"],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="今天下午带 3 岁孩子出去玩",
        parse_confidence=0.9,
    )
    result = plan_itinerary(intent)
    assert result.success, f"应成功：{result.failure_detail}"
    rest_nodes = [n for n in result.itinerary.nodes if n.target_kind == "restaurant"]
    for rn in rest_nodes:
        if rn.note:  # note 形如「已为你预留 HH:MM（N 人）」
            assert rn.start_time in rn.note, (
                f"餐厅排定 {rn.start_time} 与 note 承诺不一致（自洽被破）：note={rn.note!r}"
            )
