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

from agent.core.itinerary_qa import answer_itinerary_question
from agent.core.llm_client_stub import StubLLMClient
from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
from agent.planning.blueprint.blueprint import (
    BlueprintNode,
    BlueprintTargetKind,
    PlanBlueprint,
)
from agent.planning.planners.ils_planner import plan_hybrid
from agent.runtime.tools.search_adapter import search_restaurants_for_intent
from data.loader import load_user_profile
from schemas.intent import IntentExtraction


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


def _bbq_ids(rests) -> list[str]:
    return [r.id for r in rests if "烧烤" in (r.cuisine or "")]


def test_explicit_cuisine_anchor_recalled_and_planned():
    """3a 真跑（**不 mock invoke_tool**，走真 search_restaurants + 真 mock 数据）：
    显式点名『烧烤』+ 独处放空推断场景 → L1 anchor-escape 让热闹烧烤真召回（不被
    推断场景在工具层硬删）→ plan_hybrid 方案含烧烤（L3 锚 bonus 选中 + L4 豁免不被换）。

    这是"把搜索工具层 mock 成池子里已有烧烤"那个方法论错误的修正：病灶正在工具
    那两行硬过滤，测试必须真穿过它。"""
    intent = _intent_bbq()
    rests, _ = search_restaurants_for_intent(intent)
    bbq = _bbq_ids(rests)
    assert bbq, (
        "L1 anchor-escape 应让显式点名的烧烤真召回；"
        f"实际召回={[(r.id, r.cuisine) for r in rests]}"
    )

    result = plan_hybrid(intent, client=StubLLMClient())
    assert result.success, f"应成功建程；失败={result.failure_detail}"
    itin = result.itinerary
    rest_ids = {n.target_id for n in itin.nodes if n.target_kind == "restaurant"}
    nodes_dbg = [(n.kind, n.target_kind, n.target_id) for n in itin.nodes]
    assert rest_ids & set(bbq), (
        f"方案应含烧烤节点（锚 bonus + social 豁免）；餐厅节点={rest_ids}，"
        f"召回烧烤={bbq}；nodes={nodes_dbg}"
    )


def test_case_b_no_explicit_desire_keeps_scene_filter():
    """case(b) 反断言（守不放松）：无显式诉求（preferred=[]）时，独处放空推断场景
    仍硬过滤热闹烧烤——证明 L1 只放松了**显式锚**、没砸 case(b) 的场景硬闸。"""
    intent = _intent_bbq().model_copy(update={"preferred_poi_types": []})
    rests, _ = search_restaurants_for_intent(intent)
    assert not _bbq_ids(rests), (
        "case(b) 无显式诉求不应召回热闹烧烤（场景硬闸保留）；"
        f"实际召回={[(r.id, r.cuisine) for r in rests]}"
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
