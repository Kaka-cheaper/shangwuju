"""test_refiner_inherit_missing_keys —— C1 通用「键缺失继承」守卫单测。

forge-intent-loss（round1-4.md）收敛结论的落地验收：
`agent.intent.refiner._inherit_missing_keys` 收编 explicit_dining_requested
专属补丁、推广到 9 个白名单字段（B 类 duration_hours/distance_max_km/
social_context + C 类 start_weekday/capacity_requirement/extra_services/
preferred_poi_types/explicit_dining_requested/budget_per_person），按
`field_provenance` 出处门控 + 矛盾检测决定是否继承。

覆盖维度：
1. 核心契约（任务书 (a)(b)(c) 三件必须）：
   (a) 反馈轮 LLM 忘写 user_stated 字段 → 守卫继承回来
   (b) 显式撤回（null-on-removal）→ LLM 输出 null/[] → 守卫放行，撤回生效
   (c) "没提" ≠ 撤回 → 键缺失但矛盾检测未命中 → 正常继承，不误删
2. 9 个白名单字段逐个覆盖（不只测 explicit_dining_requested 一个代表）
3. 出处门控：user_stated 倾向继承；inferred/prior/default 放行不继承
4. R4 修正：_NO_PRIOR_CHANNEL_FIELDS（无先验注入通道字段）在 provenance
   记录缺失时按 user_stated 处理，而非无条件拒绝继承——这是本次实现过程中
   发现的真实 bug（test_explicit_dining_trigger.py 的 _intent() fixture 不带
   field_provenance，暴露"重构后 explicit_dining_requested 继承失效"的
   回归），归入本文件钉死，防再退化。
5. 矛盾检测：poi_types/extra_services 命中"值 + 否定词"→ 不继承
6. 排序不冲突：与 _enforce_duration_consistency / _repair_dictionary_drift
   的既有回归用例一起跑，确认守卫插入点不打架（这两块已有独立测试文件覆盖，
   本文件只做端到端穿透确认，不重复造轮子）。
7. A 类必传字段（companions/三类 tag）不受守卫影响——键缺失走 Pydantic
   校验失败的既有路径，不在白名单内。
"""

from __future__ import annotations

import json
import types

from agent.core.llm_client import LLMChatResponse
from agent.intent.refiner import _inherit_missing_keys, refine_intent
from schemas.intent import Companion, IntentExtraction


# ============================================================
# 共享 fixture
# ============================================================


def _intent(**overrides) -> IntentExtraction:
    """默认场景：独处放空，9 个白名单字段全部有非默认值 + user_stated 出处。

    用意：让"忘写"这个动作在每个字段上都构成"从 user_stated 值被顶回 schema
    默认值"的真实静默丢失，测试才有区分度（若字段本来就是默认值，忘写和
    默认落回看起来一样，测不出继承有没有生效）。
    """
    base = dict(
        start_time="today_afternoon",
        start_weekday="saturday",
        duration_hours=[2, 3],
        distance_max_km=4.0,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=["独处舒缓"],
        social_context="独处放空",
        capacity_requirement=2,
        extra_services=["蛋糕"],
        preferred_poi_types=["烧烤"],
        explicit_dining_requested=True,
        budget_per_person=150.0,
        raw_input="自己去转转，想吃个烧烤，预算150，需要蛋糕",
        parse_confidence=0.88,
        field_provenance={
            "start_weekday": "user_stated",
            "duration_hours": "user_stated",
            "distance_max_km": "user_stated",
            "social_context": "user_stated",
            "capacity_requirement": "user_stated",
            "extra_services:蛋糕": "user_stated",
        },
    )
    base.update(overrides)
    return IntentExtraction(**base)


class _FixedJsonClient:
    """chat() 返回固定 JSON 的假客户端（模拟 refiner LLM 输出形状）。"""

    provider = "fake"

    def __init__(self, payload: dict):
        self._content = json.dumps(payload, ensure_ascii=False)

    def chat(self, *args, **kwargs):
        return types.SimpleNamespace(content=self._content)


def _full_refined_dict(intent: IntentExtraction, **field_overrides) -> dict:
    """从一份合法 intent 起底，产出"LLM 完整回填"的 refined_intent dict——
    调用方按需 pop 掉某些键模拟"忘写"，或用 field_overrides 模拟"显式改写"。
    """
    data = intent.model_dump()
    data.pop("field_provenance", None)  # provenance 由守卫之后的传播规则重算，不是 LLM 该管的
    data["understanding"] = "用户说了一句话，我理解成先按这个调整"
    data.update(field_overrides)
    return data


def _refine_with_payload(original: IntentExtraction, feedback: str, refined_data: dict, changed=None):
    client = _FixedJsonClient(
        {
            "refined_intent": refined_data,
            "changed_fields": changed or [],
            "refiner_note": "已按反馈调整。",
        }
    )
    return refine_intent(original, feedback, client=client)


# ============================================================
# 1. 核心契约 (a)：忘写 user_stated 字段 → 守卫继承回来
# ============================================================


def test_a_forgotten_user_stated_field_is_inherited_preferred_poi_types():
    """(a) 反馈轮 LLM 忘写 preferred_poi_types（本没有先验通道，值本身即
    user_stated）→ 守卫从 original 继承回 ["烧烤"]，不因忘写而丢失。"""
    original = _intent()
    refined = _full_refined_dict(original)
    del refined["preferred_poi_types"]  # 模拟"忘写"（键缺失，非显式 []）

    out = _refine_with_payload(original, "换个安静点的地方", refined)
    assert out.refined_intent.preferred_poi_types == ["烧烤"], (
        f"忘写应被继承，实际={out.refined_intent.preferred_poi_types}"
    )


def test_a_forgotten_user_stated_field_is_inherited_duration_hours():
    """(a) B 类字段：duration_hours 忘写（user_stated 出处）→ 继承 [2,3]，
    不静默落回 schema 默认 [4,6]（R4 抓出的 B 类静默丢失面）。"""
    original = _intent()
    refined = _full_refined_dict(original)
    del refined["duration_hours"]

    out = _refine_with_payload(original, "换个安静点的地方", refined)
    assert list(out.refined_intent.duration_hours) == [2, 3], (
        f"忘写应继承 [2,3]，不该落回默认 [4,6]，实际={out.refined_intent.duration_hours}"
    )


def test_a_forgotten_user_stated_field_is_inherited_social_context():
    """(a) B 类字段：social_context 忘写 → 继承"独处放空"，不静默落回默认
    "家庭日常"（R3 抓出的 social_context 例外）。"""
    original = _intent()
    refined = _full_refined_dict(original)
    del refined["social_context"]

    out = _refine_with_payload(original, "换个安静点的地方", refined)
    assert out.refined_intent.social_context == "独处放空", (
        f"忘写应继承，不该落回默认'家庭日常'，实际={out.refined_intent.social_context}"
    )


def test_a_forgotten_user_stated_field_is_inherited_budget_per_person():
    """(a) C 类字段：budget_per_person 忘写 → 继承 150，不静默落回 None。"""
    original = _intent()
    refined = _full_refined_dict(original)
    del refined["budget_per_person"]

    out = _refine_with_payload(original, "换个安静点的地方", refined)
    assert out.refined_intent.budget_per_person == 150.0


def test_a_forgotten_user_stated_field_is_inherited_explicit_dining_requested():
    """(a) explicit_dining_requested（原专属补丁被收编的字段）：忘写 → 继承
    True，不静默落回 None（S5 反馈轮二次丢饭根因的回归钉）。"""
    original = _intent()
    refined = _full_refined_dict(original)
    del refined["explicit_dining_requested"]

    out = _refine_with_payload(original, "换个安静点的地方", refined)
    assert out.refined_intent.explicit_dining_requested is True


# ============================================================
# 2. 核心契约 (b)：显式撤回（null-on-removal）→ 守卫放行，撤回生效
# ============================================================


def test_b_explicit_null_budget_withdrawal_is_honored():
    """(b) "不要预算了" → LLM 输出显式 null（键存在，值 null）→ 守卫不干预，
    撤回生效为 None，不被误继承回 150。"""
    original = _intent()
    refined = _full_refined_dict(original, budget_per_person=None)

    out = _refine_with_payload(original, "不要预算限制了，随便吃", refined)
    assert out.refined_intent.budget_per_person is None, (
        f"显式撤回应生效为 None，实际={out.refined_intent.budget_per_person}"
    )


def test_b_explicit_empty_list_poi_types_withdrawal_is_honored():
    """(b) "不吃烧烤了" → LLM 输出显式 []（键存在，值空列表）→ 守卫不干预，
    撤回生效为 []，不被误继承回 ["烧烤"]。"""
    original = _intent()
    refined = _full_refined_dict(original, preferred_poi_types=[])

    out = _refine_with_payload(original, "不吃烧烤了，随便逛逛", refined)
    assert out.refined_intent.preferred_poi_types == [], (
        f"显式撤回应生效为 []，实际={out.refined_intent.preferred_poi_types}"
    )


def test_b_explicit_false_dining_withdrawal_is_honored():
    """(b) explicit_dining_requested 撤回（既有专属行为，收编后必须等价）：
    显式 false → 撤回生效，不被误继承回 True。"""
    original = _intent()
    refined = _full_refined_dict(original, explicit_dining_requested=False)

    out = _refine_with_payload(original, "算了不吃了，直接回家", refined)
    assert out.refined_intent.explicit_dining_requested is False


# ============================================================
# 3. 核心契约 (c)："没提" ≠ 撤回 → 键缺失但矛盾检测未命中 → 正常继承
# ============================================================


def test_c_unmentioned_field_inherits_not_mistaken_for_withdrawal():
    """(c) 反馈只字未提 preferred_poi_types/budget_per_person，LLM 忘写两个键
    （常见的"整体替换漏字段"）→ 守卫继承旧值，不误判成撤回清空。"""
    original = _intent()
    refined = _full_refined_dict(original)
    del refined["preferred_poi_types"]
    del refined["budget_per_person"]

    out = _refine_with_payload(original, "太远了，近一点", refined)
    assert out.refined_intent.preferred_poi_types == ["烧烤"], "没提该字段应继承，不应清空"
    assert out.refined_intent.budget_per_person == 150.0, "没提该字段应继承，不应清空"


def test_c_negation_word_present_but_value_not_mentioned_still_inherits():
    """(c) 反馈里出现否定词，但否定的不是 preferred_poi_types 里的值本身
    （"不要走太远"里的"不要"跟"烧烤"无关）→ 矛盾检测要求"值本身出现在反馈
    里"，值不在反馈里就不该被误判撤回，继续正常继承。"""
    original = _intent()
    refined = _full_refined_dict(original)
    del refined["preferred_poi_types"]

    out = _refine_with_payload(original, "不要走太远，近一点", refined)
    assert out.refined_intent.preferred_poi_types == ["烧烤"], (
        "否定词命中但值本身不在反馈里，不该被误判撤回"
    )


# ============================================================
# 4. 出处门控：user_stated 继承；inferred/prior/default 放行不继承
# ============================================================


def test_inferred_provenance_field_not_force_inherited():
    """social_context 出处若非 user_stated（如 inferred），忘写键时不强行
    继承——本轮 LLM 的判断应该被尊重，不被一个连用户自己都没说过的旧推断值
    钉死。"""
    original = _intent(
        field_provenance={
            **(_intent().field_provenance or {}),
            "social_context": "inferred",
        }
    )
    refined = _full_refined_dict(original)
    del refined["social_context"]  # LLM 没输出 → 落回 schema 默认"家庭日常"

    out = _refine_with_payload(original, "随便换个新地方", refined)
    assert out.refined_intent.social_context == "家庭日常", (
        f"inferred 出处不该被强行继承，应放行落回 schema 默认，"
        f"实际={out.refined_intent.social_context}"
    )


def test_prior_provenance_field_not_force_inherited():
    """distance_max_km 出处若为 prior（先验注入），忘写键时同样不强行继承。"""
    original = _intent(
        field_provenance={
            **(_intent().field_provenance or {}),
            "distance_max_km": "prior",
        }
    )
    refined = _full_refined_dict(original)
    del refined["distance_max_km"]

    out = _refine_with_payload(original, "随便换个新地方", refined)
    assert out.refined_intent.distance_max_km == 5.0, (
        f"prior 出处不该被强行继承，应放行落回 schema 默认 5.0，"
        f"实际={out.refined_intent.distance_max_km}"
    )


def test_default_provenance_field_not_force_inherited():
    """capacity_requirement 出处若为 default（纯 schema 默认，不是任何人说的），
    忘写键时不强行继承。"""
    original = _intent(
        field_provenance={
            **(_intent().field_provenance or {}),
            "capacity_requirement": "default",
        }
    )
    refined = _full_refined_dict(original)
    del refined["capacity_requirement"]

    out = _refine_with_payload(original, "随便换个新地方", refined)
    assert out.refined_intent.capacity_requirement is None


# ============================================================
# 5. R4 修正回归钉：无先验通道字段（_NO_PRIOR_CHANNEL_FIELDS）在 provenance
#    记录缺失时仍要继承（不能因为查不到 provenance 就拒绝）
# ============================================================


def test_no_prior_channel_field_inherits_even_without_provenance_record():
    """explicit_dining_requested 根本不在 field_provenance 覆盖范围内
    （schema docstring 明文排除），生产环境永远查不到它的 provenance 记录。
    若门控要求"必须是 user_stated 才继承"、又查不到记录，会让这个字段的
    继承守卫形同虚设——这是实现过程中发现的真实回归（对照
    tests/test_explicit_dining_trigger.py 的 _intent() fixture 不带
    field_provenance），本用例钉死"无记录时仍按 user_stated 处理"这条修正。
    """
    original = _intent(field_provenance=None)  # 完全没有 provenance 记录
    refined = _full_refined_dict(original)
    del refined["explicit_dining_requested"]

    out = _refine_with_payload(original, "换个更近的", refined)
    assert out.refined_intent.explicit_dining_requested is True, (
        "无先验通道字段（explicit_dining_requested）在 provenance 记录缺失时"
        "仍应按 user_stated 处理并继承，不能因查不到记录就拒绝——这是"
        "R4 门控修正要钉死的行为。"
    )


def test_no_prior_channel_field_preferred_poi_types_inherits_without_provenance():
    """同上，preferred_poi_types 同样不在 field_provenance 覆盖范围内。"""
    original = _intent(field_provenance=None)
    refined = _full_refined_dict(original)
    del refined["preferred_poi_types"]

    out = _refine_with_payload(original, "换个更近的", refined)
    assert out.refined_intent.preferred_poi_types == ["烧烤"]


def test_prior_channel_field_does_not_inherit_without_provenance_record():
    """对照组：duration_hours **有**先验注入通道（B 类字段），无 provenance
    记录时应按"未知，不强行继承"处理——不能和无先验通道字段一视同仁，否则
    R2/T2 的"inferred/prior 放行"门控会被无记录场景绕过。"""
    original = _intent(field_provenance=None)
    refined = _full_refined_dict(original)
    del refined["duration_hours"]

    out = _refine_with_payload(original, "换个更近的", refined)
    assert list(out.refined_intent.duration_hours) == [4, 6], (
        f"有先验通道字段无记录时不该强行继承，应落回 schema 默认 [4,6]，"
        f"实际={out.refined_intent.duration_hours}"
    )


# ============================================================
# 6. 矛盾检测：poi_types/extra_services 命中"值+否定词"→ 不继承
# ============================================================


def test_contradiction_detected_prevents_inheritance_poi_types():
    """反馈原话同时含"烧烤"和否定词"不吃" → 矛盾检测命中，即使 LLM 忘写键
    也不强行继承（该撤回的不能被继承守卫续上）。"""
    original = _intent()
    refined = _full_refined_dict(original)
    del refined["preferred_poi_types"]  # LLM 没显式清空，但反馈原话已经否定了

    out = _refine_with_payload(original, "不吃烧烤了，随便逛逛就行", refined)
    assert out.refined_intent.preferred_poi_types == [], (
        f"矛盾检测命中应阻止继承，实际={out.refined_intent.preferred_poi_types}"
    )


def test_contradiction_detected_prevents_inheritance_extra_services():
    """extra_services 同样受矛盾检测保护："不要蛋糕了" + 忘写键 → 不继承。"""
    original = _intent()
    refined = _full_refined_dict(original)
    del refined["extra_services"]

    out = _refine_with_payload(original, "不要蛋糕了，简单点就行", refined)
    assert out.refined_intent.extra_services == []


# ============================================================
# 7. A 类必传字段不受守卫影响（companions/三类 tag 键缺失=既有 ValidationError 路径）
# ============================================================


def test_required_field_missing_key_falls_back_not_silently_inherited_by_guard():
    """companions 是 A 类必传字段，不在白名单内——键缺失会触发 Pydantic
    ValidationError，走现有的错误回灌重试/_rule_fallback 兜底路径，不是本
    守卫的职责范围。用真实"LLM 一直漏传 companions"场景验证：最终仍能拿到
    合法结果（走 _rule_fallback，从 original 恢复），而不是被守卫静默接管。
    """
    original = _intent()
    refined = _full_refined_dict(original)
    del refined["companions"]  # 必传字段缺失 → 校验失败

    client = _FixedJsonClient(
        {
            "refined_intent": refined,
            "changed_fields": [],
            "refiner_note": "已调整。",
        }
    )
    out = refine_intent(original, "换个安静点的地方", client=client, max_retries=0)
    # 最终必须拿到合法结果（走 _rule_fallback，companions 从 original 恢复）
    assert out.refined_intent.companions[0].role == "自己"


# ============================================================
# 8. 排序穿透确认：守卫 + _enforce_duration_consistency 不打架
# ============================================================


def test_guard_then_duration_consistency_explicit_number_wins():
    """LLM 忘写 duration_hours 键（守卫会继承旧值 [2,3]），但反馈原话明说
    "只有 1 小时"——_enforce_duration_consistency 应在守卫继承之后再次介入，
    把结果强制对齐到反馈的真实数字 [1,1]，而不是停留在守卫继承的 [2,3]。
    这确认两个机制的执行顺序正确：守卫先兜底"没丢"，数字一致性校验再兜底
    "反馈的精确数字优先"，二者不冲突。"""
    original = _intent()
    refined = _full_refined_dict(original)
    del refined["duration_hours"]  # 忘写：守卫会先继承回 [2,3]

    out = _refine_with_payload(original, "我只有一个小时", refined)
    assert list(out.refined_intent.duration_hours) == [1, 1], (
        f"_enforce_duration_consistency 应把守卫继承的 [2,3] 再次覆盖为反馈"
        f"明说的 [1,1]，实际={out.refined_intent.duration_hours}"
    )


def test_guard_then_dictionary_drift_repair_stack_additively():
    """LLM 忘写 preferred_poi_types 键（守卫继承回 ["烧烤"]），且反馈原话
    提到新的词典外品类"撸串"——_repair_dictionary_drift 应在守卫继承之后
    再补上"撸串"，两者叠加而非互相覆盖（守卫管"旧的别丢"，drift 管"新的该
    加"，正交共存，forge round2 T1 已论证）。"""
    original = _intent()
    refined = _full_refined_dict(original)
    del refined["preferred_poi_types"]  # 忘写：守卫会先继承回 ["烧烤"]

    out = _refine_with_payload(original, "撸串也行", refined)
    assert "烧烤" in out.refined_intent.preferred_poi_types, "守卫继承的旧值不该被 drift 冲掉"
    assert "撸串" in out.refined_intent.preferred_poi_types, "drift 应补上反馈里的新品类"
