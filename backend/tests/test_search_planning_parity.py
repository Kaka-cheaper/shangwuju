"""tests.test_search_planning_parity —— ADR-0014 决策 2（G-2）结构对齐测试。

【这是什么问题】

三条规划路径各自独立构造 `SearchPoisInput`/`SearchRestaurantsInput`：
- `agent/runtime/tools/search_adapter.py`（execute 阶段主路径）
- `agent/planning/planners/ils_planner.py`（ILS 兜底）
- `agent/planning/planners/rule_planner.py`（rule 地板）

历史上已经出现过"三处构造点漏传一处"的真实 bug（氛围词 experience_tags
主路径漏传、party_size 三处三种算法）——本 ADR 决策 2 又新增了一个字段
（`tag_provenance`），如果只改一处忘了另外两处，行为会在"某条路径能触发
soft tag 出处降级、另外两条恒退化成默认序"这种**只有跑到那条冷门路径才
会暴露**的方式静默分叉，人工 code review 很容易漏看（三处都在改，逐字
比对三份 diff 的心智负担高）。

本测试是这类"第四次漏传"的静态网：同一个 intent 喂三条构造点，内省各自
产出的 `SearchXxxInput`（不真的跑候选搜索/规划），断言三者在**共享约束
子集**上字段级相等。

【共享约束子集 vs 刻意分叉字段（不在本测试断言范围）】

- POI 查询共享子集：physical_constraints / experience_tags / social_context /
  distance_max_km / tag_provenance。
- 餐厅查询共享子集：dietary_constraints / experience_tags / social_context /
  distance_max_km / tag_provenance。
- **capacity_requirement 刻意不比较**：`search_adapter.
  search_restaurants_for_intent` 自算 self+companions 实付头数，
  `rule_planner`/`ils_planner` 直取 `intent.capacity_requirement`
  （LLM"≥4 人才填"规则字段）——两套算法故意不同源（保持"搜索期过滤"与
  "critic.check_capacity 判定"用同一字段），三处构造点自身的模块 docstring
  已详细记述这个决策，不是本测试要盯防的"漏传"。
- preferred_types / age_in_party / user_lat / user_lng / exclude_visited_ids /
  limit / require_private_room：各构造点服务不同的执行阶段（搜索期候选池
  大小、个性化排除名单等），天然按调用方上下文不同，非本测试范围。

【实现手法】

内省三处构造点各自"第一次（未降级）"构造的 `SearchXxxInput`：
- `search_adapter` 单次直调，天然只有一次构造。
- `rule_planner._query_pois`/`_query_restaurants` 走 `call` 回调注入捕获
  （第 1 级 = 未降级的原始约束构造，也是与另两处唯一可比的一次）。
- `ils_planner._query_pois`/`_query_restaurants` 单次直调，monkeypatch 模块
  内 `invoke_tool` 捕获实际构造出的 dict。

真实候选搜索仍照常执行（捕获后原样转发给真 `invoke_tool`），不 mock 掉
search 行为——本测试只加一层"顺手记一笔构造参数"的旁路，不改变三条路径
本身的真实运行结果。
"""

from __future__ import annotations

from schemas.intent import Companion, IntentExtraction
from tools.registry import invoke_tool as _real_invoke_tool


def _make_parity_intent() -> IntentExtraction:
    """同时含 hard+soft 混合 tag + 出处标注的 intent，供三条路径共同消费。"""
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=[4, 6],
        distance_max_km=8.0,
        companions=[Companion(role="外公", age=70, count=1)],
        physical_constraints=["适合老人", "亲子友好"],  # 1 hard + 1 soft
        dietary_constraints=["不辣", "日料"],  # 1 hard + 1 soft
        experience_tags=["安静聊天"],
        social_context="老人伴助",
        raw_input="带外公去安静的地方，不吃辣，想吃日料",
        parse_confidence=0.9,
        field_provenance={
            "physical_constraints:适合老人": "inferred",
            "physical_constraints:亲子友好": "prior",
            "dietary_constraints:不辣": "user_stated",
            "dietary_constraints:日料": "default",
            "experience_tags:安静聊天": "inferred",
        },
    )


# POI/餐厅查询的共享约束子集（见模块 docstring）
_POI_SHARED_FIELDS = (
    "physical_constraints",
    "experience_tags",
    "social_context",
    "distance_max_km",
    "tag_provenance",
)
_RESTAURANT_SHARED_FIELDS = (
    "dietary_constraints",
    "experience_tags",
    "social_context",
    "distance_max_km",
    "tag_provenance",
)


def _capture_first_call(captured: dict):
    """rule_planner 的 `call` 回调注入：只记第一次（未降级）构造，原样转发。"""

    def _call(tool_name, args):
        if tool_name not in captured:
            captured[tool_name] = args
        return _real_invoke_tool(tool_name, args)

    return _call


def _collect_search_adapter_inputs(intent: IntentExtraction, monkeypatch) -> dict:
    import agent.runtime.tools.search_adapter as search_adapter_mod

    captured: dict = {}

    def _spy_invoke_tool(name, args):
        if name not in captured:
            captured[name] = args
        return _real_invoke_tool(name, args)

    monkeypatch.setattr(search_adapter_mod, "invoke_tool", _spy_invoke_tool)
    search_adapter_mod.search_pois_for_intent(intent)
    search_adapter_mod.search_restaurants_for_intent(intent)
    return captured


def _collect_rule_planner_inputs(intent: IntentExtraction) -> dict:
    import agent.planning.planners.rule_planner as rule_planner_mod
    from agent.core.trace import Tracer

    captured: dict = {}
    tracer = Tracer()
    rule_planner_mod._query_pois(intent, _capture_first_call(captured), tracer)
    rule_planner_mod._query_restaurants(intent, _capture_first_call(captured), tracer)
    return captured


def _collect_ils_planner_inputs(intent: IntentExtraction, monkeypatch) -> dict:
    import agent.planning.planners.ils_planner as ils_planner_mod
    from agent.core.trace import Tracer

    captured: dict = {}

    def _spy_invoke_tool(name, args):
        if name not in captured:
            captured[name] = args
        return _real_invoke_tool(name, args)

    monkeypatch.setattr(ils_planner_mod, "invoke_tool", _spy_invoke_tool)
    tracer = Tracer()
    ils_planner_mod._query_pois(intent, tracer)
    ils_planner_mod._query_restaurants(intent, tracer)
    return captured


def test_search_pois_input_shared_fields_match_across_three_paths(monkeypatch):
    """三条路径的 search_pois 构造在共享子集上字段级相等。"""
    intent = _make_parity_intent()

    adapter_args = _collect_search_adapter_inputs(intent, monkeypatch)
    rule_args = _collect_rule_planner_inputs(intent)
    ils_args = _collect_ils_planner_inputs(intent, monkeypatch)

    sources = {
        "search_adapter": adapter_args["search_pois"],
        "rule_planner": rule_args["search_pois"],
        "ils_planner": ils_args["search_pois"],
    }

    for field in _POI_SHARED_FIELDS:
        values = {name: args.get(field) for name, args in sources.items()}
        distinct = {repr(v) for v in values.values()}
        assert len(distinct) == 1, (
            f"search_pois 构造字段「{field}」三条路径不一致（第四次漏传？）："
            f"{values}"
        )


def test_search_restaurants_input_shared_fields_match_across_three_paths(monkeypatch):
    """三条路径的 search_restaurants 构造在共享子集上字段级相等。"""
    intent = _make_parity_intent()

    adapter_args = _collect_search_adapter_inputs(intent, monkeypatch)
    rule_args = _collect_rule_planner_inputs(intent)
    ils_args = _collect_ils_planner_inputs(intent, monkeypatch)

    sources = {
        "search_adapter": adapter_args["search_restaurants"],
        "rule_planner": rule_args["search_restaurants"],
        "ils_planner": ils_args["search_restaurants"],
    }

    for field in _RESTAURANT_SHARED_FIELDS:
        values = {name: args.get(field) for name, args in sources.items()}
        distinct = {repr(v) for v in values.values()}
        assert len(distinct) == 1, (
            f"search_restaurants 构造字段「{field}」三条路径不一致（第四次漏传？）："
            f"{values}"
        )


def test_tag_provenance_actually_populated_not_all_none(monkeypatch):
    """反幻觉：确保三处确实把 tag_provenance 传了值，而不是"三处都恰好一致地
    没传"（那样上面两个相等性测试会因为"三个 None 也算相等"而失去意义）。
    """
    intent = _make_parity_intent()
    rule_args = _collect_rule_planner_inputs(intent)

    assert rule_args["search_pois"].get("tag_provenance"), (
        "search_pois 的 tag_provenance 不应为空——intent 已标注"
        "physical_constraints 出处，三处构造点都应该摘取到非空子集"
    )
    assert rule_args["search_restaurants"].get("tag_provenance"), (
        "search_restaurants 的 tag_provenance 不应为空——同上，dietary_constraints"
    )
