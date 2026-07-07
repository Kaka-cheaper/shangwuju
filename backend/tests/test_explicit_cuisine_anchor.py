"""显式点名品类·硬锚 的回归测试（「我想吃个烧烤」根治批）。

治的两个真 bug（用真实数据 R033 老王烧烤系列 / R013 独处书房咖啡复现）：

3a —— 显式点名的品类被推断场景压掉：用户显式点名「烧烤」(preferred_poi_types)，但
   系统从「一个人」等信息合理地推断出「独处/安静」场景后，用这个**推断**的场景调性
   把**显式点名**的热闹烧烤丢掉、换成安静咖啡。根因跨三层：① 选点 _utility 对 cuisine
   命中显式锚**零效用信用** → ② 即便选中，check_social_context 把热闹烧烤对独处场景判
   HARD SOCIAL_CONTEXT_MISMATCH → ③ repair 拉黑换成场景匹配的安静店。修法：_utility
   给显式锚压倒性 bonus（选中它）+ check_social_context 对显式锚豁免（不被换掉）。
   显式请求压过推断的场景偏好——场景只该管用户**没**明说的那部分槽位。

3b —— 「为什么没安排上烧烤」被当接地问答：这句问的是**缺席**（未满足诉求），却被
   itinerary_qa 的 why_rationale 答复器抓个在场实体背它的评分/距离，答非所问。修法：
   否定辖域护栏（为什么/为啥/凭什么 + 没安排/没排上/没…）→ answer_itinerary_question
   返 None → 落穿到路由脑子按 feedback/解释处理。
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.core.itinerary_qa import answer_itinerary_question
from agent.core.llm_client_stub import StubLLMClient
from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
from agent.planning.blueprint.blueprint import (
    BlueprintNode,
    BlueprintTargetKind,
    PlanBlueprint,
)
from agent.planning.planners import ils_planner
from agent.planning.planners.ils_planner import plan_hybrid
from data.loader import load_pois, load_restaurants, load_user_profile
from schemas.intent import IntentExtraction
from schemas.tools import SearchPoisOutput, SearchRestaurantsOutput


def _patch_tool(monkeypatch, *, pois, restaurants):
    """mock 工具层返回固定候选，让 _query_pois/_query_restaurants 走完整真实路径。"""

    def fake(name, args):
        if name == "search_pois":
            output = SearchPoisOutput(success=True, candidates=list(pois)).model_dump()
        elif name == "search_restaurants":
            output = SearchRestaurantsOutput(success=True, candidates=list(restaurants)).model_dump()
        else:
            return SimpleNamespace(success=False, output=None, reason=None, duration_ms=1)
        return SimpleNamespace(success=True, output=output, reason=None, duration_ms=1)

    monkeypatch.setattr(ils_planner, "invoke_tool", fake)


def _intent_bbq() -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 6],
        distance_max_km=5.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=["独处舒缓"],
        social_context="独处放空",
        preferred_poi_types=["烧烤"],
        raw_input="我想吃个烧烤",
        parse_confidence=0.9,
    )


def test_explicit_cuisine_anchor_survives_inferred_scene(monkeypatch):
    """3a：候选池同时有可排的烧烤(R033)与场景匹配的安静咖啡(R013)时，显式点名的
    烧烤必须进方案——不被推断的独处/安静场景在选点或 critic 阶段换掉。"""
    rests = {r.id: r for r in load_restaurants()}
    pois = load_pois()
    bbq = rests["R033"]   # 烧烤·热闹，营业 11:30-23:00（下午饭点开着，排除营业时间混淆）
    cafe = rests["R013"]  # 晨与暮·独处书房咖啡（独处舒缓/安静，rating 4.6 略高、场景匹配）
    quiet_poi = next(
        (p for p in pois if any(k in (p.name + p.type + "".join(p.tags)) for k in ("书", "图书", "独处", "安静"))),
        pois[0],
    )

    # 工具按 rating 序返回 [cafe, bbq]；没有本次修复时 build_route 选安静咖啡、
    # 或 critic 把热闹烧烤判 social 硬违规换成咖啡。修复后烧烤靠 _utility 锚 bonus
    # 被选中、且 check_social_context 对显式锚豁免不被换掉。
    _patch_tool(monkeypatch, pois=[quiet_poi], restaurants=[cafe, bbq])

    result = plan_hybrid(_intent_bbq(), client=StubLLMClient())
    assert result.success, f"应成功建程；失败={result.failure_detail}"
    itin = result.itinerary
    nodes_dbg = [(n.kind, n.target_kind, n.target_id) for n in itin.nodes]
    rest_node = next((n for n in itin.nodes if n.target_kind == "restaurant"), None)
    assert rest_node is not None, f"应有餐厅节点；nodes={nodes_dbg}"
    assert rest_node.target_id == "R033", (
        f"显式点名『烧烤』应进方案（锚 bonus + social 豁免），实际选了 "
        f"{rest_node.target_id}（R013=安静咖啡）；nodes={nodes_dbg}"
    )


def _simple_itin():
    """确定性拼一个含 P001 主活动 + R001 用餐的合法行程（无烧烤），供 3b 用。"""
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(kind="主活动", target_kind=BlueprintTargetKind.POI, target_id="P001", duration_min=120),
            BlueprintNode(kind="用餐", target_kind=BlueprintTargetKind.RESTAURANT, target_id="R001", duration_min=60),
        ],
        preferred_start_time="14:00",
        rationale="3b-regression",
    )
    return assemble_from_blueprint(_intent_bbq(), bp, load_user_profile())


def test_why_not_bbq_is_not_grounded_qa():
    """3b：『为什么没安排上烧烤』问的是缺席（未满足诉求），不该被接地问答抓个在场
    实体背数据；answer_itinerary_question 应返 None（落穿到脑子）。"""
    itin = _simple_itin()
    ans = answer_itinerary_question("为什么没安排上烧烤", itin, intent=_intent_bbq())
    assert ans is None, f"『为什么没安排上烧烤』不该被当接地问答作答，实际答：{ans!r}"
