"""tests.test_search_adapter_restaurant_query —— 约束消费面 bug 修复回归测试。

钉住 `agent/runtime/tools/search_adapter.py::search_restaurants_for_intent`
（execute 阶段主路径，state["restaurants"] 的唯一来源）两个已实锤 bug：

1. 氛围词（experience_tags）没传进 SearchRestaurantsInput——ILS/rule 两条
   兜底路径都传，唯独主路径漏传，导致主路径的餐厅候选对氛围词完全无感。
2. capacity_requirement 只在 party_size 精确等于 2/4/6/8 才传，其余人数
   （尤其 3/5/7 这类聚餐常见人数）直接传 None，桌型过滤在主路径整体失效；
   而 `tools/search_restaurants.py::_capacity_ok` 本就按 ≤2/≤4/≤6/其余 分档，
   任意整数都能正确分档，不需要预先对齐到桌型档位。

跑真实 Tool + 真 mock_data（conftest.py 默认策略，不走 fake_tools），验证的是
"这个 bug 在真实召回链路上确实存在/确实被修复"，而不是纯函数级别的行为。

social_context 选取说明：IntentExtraction.social_context 非 Optional（默认
"家庭日常"），search_restaurants 内部对它做硬过滤（`social_context not in
r.suitable_for` 即剔除），每个测试都要选一个「与要验证的候选真实重叠」的
context，否则会被这个无关维度误伤成假阳性/假阴性——各测试的选取理由见各自
docstring。
"""

from __future__ import annotations

from agent.runtime.tools.search_adapter import search_restaurants_for_intent
from schemas.intent import Companion, IntentExtraction


def _intent(
    *,
    companions: list[Companion],
    social_context: str,
    experience_tags: list[str] | None = None,
) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5.0,
        companions=companions,
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=experience_tags or [],
        social_context=social_context,
        raw_input="测试",
        parse_confidence=0.9,
    )


# ============================================================
# bug 1：氛围词（experience_tags）在主路径失效
# ============================================================


def test_experience_tags_filter_restaurants_in_execute_path():
    """intent.experience_tags=['安静聊天'] 时，execute 阶段召回的餐厅必须全部
    命中该氛围词（tools/search_restaurants.py 的 has_any_tag 宽松命中）。

    social_context 选「情侣亲密」：mock 数据里 R006/R014/R018/R044 四家餐厅
    同时满足"带安静聊天 tag" + "suitable_for 含情侣亲密"，而 R004/R011/R016/
    R029/R032/R039/R041/R042/R043/R045 等一批不带该 tag 的餐厅也 suitable_for
    情侣亲密——这批"混进来的候选"正是修复前 bug 的可观测证据。
    修复前：SearchRestaurantsInput 没传 experience_tags → 完全不过滤，
    上面这批不带该 tag 的高分餐厅会混进结果，本断言应失败。
    """
    intent = _intent(companions=[], social_context="情侣亲密", experience_tags=["安静聊天"])
    rests, _relaxed = search_restaurants_for_intent(intent, limit=20)

    assert rests, "「安静聊天」+「情侣亲密」在 mock 数据里应有命中候选，不应召回为空"
    missing = [r.id for r in rests if "安静聊天" not in (r.tags or [])]
    assert not missing, (
        f"execute 阶段召回的餐厅应全部命中 experience_tags=['安静聊天']，"
        f"实际混入未命中候选：{missing}"
    )


# ============================================================
# bug 2：party_size 非精确 2/4/6/8 时桌型过滤整体失效
# ============================================================


def test_capacity_filter_applies_for_five_person_party():
    """5 人聚餐（1 自己 + 4 朋友）：execute 阶段召回的餐厅必须都有 ≥6 人桌型
    （_capacity_ok：party<=6 需要 cap.six）。

    social_context 用「朋友热闹」（贴合聚餐场景）：mock 数据里 R047/R050 是
    "朋友热闹"高分餐厅但只有 4 人桌（six/eight 均 False）——修复前
    party_size=5 不在 (2,4,6,8) 精确匹配里 → capacity_requirement=None →
    桌型过滤整体不生效，R047/R050 会混进候选，本断言应失败，这是 bug 2 的
    直接回归证据。
    """
    intent = _intent(companions=[Companion(role="朋友", count=4)], social_context="朋友热闹")
    rests, _relaxed = search_restaurants_for_intent(intent, limit=20)

    assert rests, "5 人局「朋友热闹」在 mock 数据里应有满足桌型的候选，不应召回为空"
    # 与 tools/search_restaurants.py::_capacity_ok 的分档逐字对齐：party<=6 只认
    # cap.six（不像 critic._rules.checks.check_capacity 那样把 private_room 也
    # 算数——两处"能不能坐下"的判定口径本就不同，此处按 Tool 层真实口径断言）。
    bad = [r.id for r in rests if not r.capacity.six]
    assert not bad, (
        f"5 人局（party_size=5）execute 阶段召回应只含 ≥6 人桌的餐厅，"
        f"实际混入桌型不够的候选：{bad}"
    )


def test_capacity_filter_applies_for_three_person_party():
    """3 人局（1 自己 + 2 朋友）：party_size=3 同样不在旧版 (2,4,6,8) 精确匹配
    集合里，是 bug 2 覆盖的另一个非精确人数。_capacity_ok 对 party<=4 要求
    cap.four。

    social_context 用「独处放空」：mock 数据里该 context 下只有 R009 是 4 人桌
    以下（four=False，只有 2 人桌），其余候选（R013/R021/R022/R047/R049/R050）
    都有 four=True——R009 是唯一能钉住"3 人不再被误判成不过滤"的候选。
    """
    intent = _intent(companions=[Companion(role="朋友", count=2)], social_context="独处放空")
    rests, _relaxed = search_restaurants_for_intent(intent, limit=20)

    assert rests, "3 人局「独处放空」在 mock 数据里应有候选，不应召回为空"
    bad = [r.id for r in rests if not r.capacity.four]
    assert not bad, (
        f"3 人局（party_size=3）execute 阶段召回应只含有 4 人桌的餐厅，"
        f"实际混入桌型不够的候选（预期只有 R009 会被误混入)：{bad}"
    )


def test_capacity_filter_scales_to_seven_person_party_needing_eight_seat():
    """7 人局（1 自己 + 6 朋友）：_capacity_ok 对 party>6 要求 cap.eight，比
    5/6 人局门槛更严。同一 social_context「朋友热闹」下，R005/R015/R032/R037/
    R039/R040/R046/R049/R051 这批 six=True 但 eight=False 的高分餐厅，修复前
    （party_size=7 不在精确匹配集合里）会连同 R047/R050（四人桌）一起混进来；
    修复后应只剩 R030/R031/R033/R034/R035/R036/R048 这批真正的 8 人桌/包间餐厅。
    """
    intent = _intent(companions=[Companion(role="朋友", count=6)], social_context="朋友热闹")
    rests, _relaxed = search_restaurants_for_intent(intent, limit=20)

    assert rests, "7 人局「朋友热闹」在 mock 数据里应有 8 人桌候选，不应召回为空"
    bad = [r.id for r in rests if not r.capacity.eight]
    assert not bad, (
        f"7 人局（party_size=7）execute 阶段召回应只含 8 人桌餐厅，"
        f"实际混入桌型不够的候选：{bad}"
    )
