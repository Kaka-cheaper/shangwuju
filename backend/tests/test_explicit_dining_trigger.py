"""tests.test_explicit_dining_trigger —— tristate 触发+护栏（C5a，I3 显式诉求零丢失）。

【钉住的行为】`explicit_dining_requested` 三态接入消费者：
- `dining_soft_anchored`：True 恒锚 / False 恒不锚（抑制商务/纪念日/跨窗+dietary
  全部推断触发——顺带修复"商务场景明说不用排饭仍硬塞商务餐"的既存缺陷）/
  None 走既有推断（现状特征化在 test_explicit_dining_tristate_schema.py T4）。
- `node_decider.decide_nodes`：True 强制用餐节点在场（含独处/极短时长）/
  False 抑制一切用餐触发。
- critic `check_explicit_dining_presence`：True+无餐厅节点 → HARD（驱动补饭
  backprompt），带可行性护栏（窄池 tool_results 有餐厅才判，池空/无快照降级
  留给 C6 advisory）+ slot-hint 范式（message 带池内候选店名）。
- refiner 缺键继承守卫：LLM 输出缺键 → 从 original 继承（拦"忘写"）；显式
  false → 放行（"改口"是合法语义）。含房间路径镜像用例（多成员拼接文本
  走同一 refine_intent 管线）。
- prompt 镜像钉：blueprint/parser/refiner 三个 prompt 都含三态规则文本
  （防"两条路径行为不一致"的镜像漂移）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.planning.blueprint.node_decider import (  # noqa: E402
    KIND_DINING,
    KIND_MAIN,
    decide_nodes,
)
from agent.planning.critic._rules.checks import (  # noqa: E402
    check_explicit_dining_presence,
)
from agent.planning.critic._rules.types import Severity, ViolationCode  # noqa: E402
from agent.planning.critic.context import CriticContext  # noqa: E402
from agent.planning.planners.route_builder import dining_soft_anchored  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402


def _intent(**overrides) -> IntentExtraction:
    kw = dict(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5.0,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="下午出去转转",
        parse_confidence=0.9,
    )
    kw.update(overrides)
    return IntentExtraction(**kw)


# ============================================================
# 1. dining_soft_anchored：显式态双向早退
# ============================================================


def test_soft_anchor_true_overrides_all_inference():
    """True 恒锚：普通家庭场景、无 dietary、窗不跨饭点——推断条件全不满足，
    显式要吃饭仍软锚。"""
    intent = _intent(explicit_dining_requested=True)
    assert dining_soft_anchored(intent) is True


def test_soft_anchor_false_suppresses_business_inference():
    """False 抑制商务推断（既存缺陷修复钉）：商务接待此前无条件软锚饭，
    用户明说"不用安排吃的"必须压过它。"""
    intent = _intent(
        social_context="商务接待",
        explicit_dining_requested=False,
        raw_input="接客户，不用安排吃的，就谈事",
    )
    assert dining_soft_anchored(intent) is False


def test_soft_anchor_false_suppresses_dietary_window_inference():
    """False 同样抑制跨窗+dietary 推断触发。"""
    intent = _intent(
        start_time="2026-07-11T14:00",
        dietary_constraints=["不辣"],
        explicit_dining_requested=False,
    )
    assert dining_soft_anchored(intent, depart_min=14 * 60) is False


# ============================================================
# 2. node_decider：tristate
# ============================================================


def test_decide_nodes_false_suppresses_all_dining():
    """False → 恒 [主活动]：商务/纪念日/dietary/中长时长的用餐触发全部抑制。"""
    for kwargs in (
        {"social_context": "商务接待"},
        {"social_context": "纪念日仪式感"},
        {"dietary_constraints": ["不辣"]},
        {"duration_hours": [4, 6]},
        {"social_context": "商务接待", "duration_hours": [1, 1]},  # 极短商务
    ):
        intent = _intent(explicit_dining_requested=False, **kwargs)
        assert decide_nodes(intent) == [KIND_MAIN], f"False 未抑制：{kwargs}"


def test_decide_nodes_true_forces_dining_presence():
    """True → 用餐节点强制在场，即使时长很短/独处放空。"""
    # 极短：用餐即主体
    short = _intent(duration_hours=[1, 1], explicit_dining_requested=True)
    assert decide_nodes(short) == [KIND_DINING]
    # 短+独处（原规则例外"不强行吃饭"被显式要求压过）
    solo = _intent(
        duration_hours=[2, 2],
        social_context="独处放空",
        explicit_dining_requested=True,
    )
    assert KIND_DINING in decide_nodes(solo)
    # 中长+独处
    solo_long = _intent(
        duration_hours=[4, 5],
        social_context="独处放空",
        explicit_dining_requested=True,
    )
    assert KIND_DINING in decide_nodes(solo_long)


def test_decide_nodes_none_keeps_current_behavior():
    """None 走既有推断——独处放空无 dietary 仍是纯主活动（现状特征化）。"""
    solo = _intent(duration_hours=[4, 5], social_context="独处放空")
    assert decide_nodes(solo) == [KIND_MAIN]


# ============================================================
# 3. critic check_explicit_dining_presence：护栏+slot-hint
# ============================================================


def _poi_only_itinerary():
    from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
    from agent.planning.blueprint.blueprint import (
        BlueprintNode,
        BlueprintTargetKind,
        PlanBlueprint,
    )
    from data.loader import load_user_profile

    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="主活动",
                target_kind=BlueprintTargetKind.POI,
                target_id="P040",
                duration_min=90,
            ),
        ],
        preferred_start_time="14:00",
        rationale="无餐厅方案",
    )
    return assemble_from_blueprint(_intent(), bp, load_user_profile())


def _restaurant_pool():
    from data.loader import load_restaurants

    return list(load_restaurants())[:3]


def test_critic_hard_when_true_no_restaurant_and_pool_available():
    """True + 无餐厅节点 + 窄池有货 → HARD，message 带池内候选店名
    （slot-hint 范式：不让 LLM 盲猜补哪家）。"""
    itin = _poi_only_itinerary()
    pool = _restaurant_pool()
    ctx = CriticContext(
        intent=_intent(explicit_dining_requested=True),
        tool_results={"pois": [], "restaurants": pool},
    )
    out = check_explicit_dining_presence(itin, ctx=ctx)
    assert len(out) == 1
    v = out[0]
    assert v.code == ViolationCode.EXPLICIT_DINING_MISSING
    assert v.severity == Severity.HARD
    # slot-hint：至少两家池内店名出现在 message 里
    named = [r.name for r in pool if r.name in v.message]
    assert len(named) >= 2, f"message 未带足候选店名：{v.message}"


def test_critic_guard_no_hard_when_pool_empty_or_absent():
    """可行性护栏：窄池空 / 无搜索快照（ILS 路径）→ 不判 HARD
    （修复闭环冲着补不进的目标空转只会拖垮方案；诚实义务由 C6 advisory 承接）。"""
    itin = _poi_only_itinerary()
    intent = _intent(explicit_dining_requested=True)

    ctx_empty = CriticContext(intent=intent, tool_results={"pois": [], "restaurants": []})
    assert check_explicit_dining_presence(itin, ctx=ctx_empty) == []

    ctx_none = CriticContext(intent=intent, tool_results=None)
    assert check_explicit_dining_presence(itin, ctx=ctx_none) == []


def test_critic_silent_when_restaurant_present_or_not_requested():
    """有餐厅节点 → 静默；None/False → 静默（None=现状零变化红线）。"""
    from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint
    from agent.planning.blueprint.blueprint import (
        BlueprintNode,
        BlueprintTargetKind,
        PlanBlueprint,
    )
    from data.loader import load_user_profile

    pool = _restaurant_pool()

    # 有餐厅节点：True 也静默
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(
                kind="用餐",
                target_kind=BlueprintTargetKind.RESTAURANT,
                target_id="R001",
                duration_min=60,
            ),
        ],
        preferred_start_time="12:00",
        rationale="有餐厅方案",
    )
    with_rest = assemble_from_blueprint(_intent(), bp, load_user_profile())
    ctx = CriticContext(
        intent=_intent(explicit_dining_requested=True),
        tool_results={"pois": [], "restaurants": pool},
    )
    assert check_explicit_dining_presence(with_rest, ctx=ctx) == []

    # None / False：无餐厅也静默
    poi_only = _poi_only_itinerary()
    for value in (None, False):
        ctx2 = CriticContext(
            intent=_intent(explicit_dining_requested=value),
            tool_results={"pois": [], "restaurants": pool},
        )
        assert check_explicit_dining_presence(poi_only, ctx=ctx2) == [], f"value={value}"


def test_critic_registered_in_registry_stage1_hard():
    """新 check 已注册进显式注册表（Stage 1 hard，gate 修复）。"""
    from agent.planning.critic.validate import REGISTRY

    specs = [s for s in REGISTRY if s.code == ViolationCode.EXPLICIT_DINING_MISSING]
    assert len(specs) == 1
    assert specs[0].stage == 1
    assert specs[0].tier == "hard"


# ============================================================
# 4. refiner 缺键继承守卫（两道防线的代码侧）
# ============================================================


class _FixedJsonClient:
    """chat() 返回固定 JSON 的假客户端（模拟 refiner LLM 输出形状）。"""

    provider = "fake"

    def __init__(self, payload: str):
        self._payload = payload

    def chat(self, *args, **kwargs):
        return types.SimpleNamespace(content=self._payload)


def _refined_payload(*, include_dining_key: bool, dining_value="null") -> str:
    dining_part = (
        f'"explicit_dining_requested":{dining_value},' if include_dining_key else ""
    )
    return (
        '{"refined_intent":{"start_time":"today_afternoon","start_weekday":null,'
        '"duration_hours":[3,5],"distance_max_km":3.0,'
        '"companions":[{"role":"自己","age":null,"count":1,'
        '"is_birthday":false,"is_special_role":false}],'
        '"physical_constraints":[],"dietary_constraints":[],'
        '"experience_tags":[],"social_context":"家庭日常",'
        '"capacity_requirement":null,"extra_services":[],"preferred_poi_types":[],'
        f"{dining_part}"
        '"raw_input":"占位（会被守卫覆盖）","parse_confidence":0.9,'
        '"ambiguous_fields":[],'
        '"understanding":"用户说太远了，我理解成要拉近距离"},'
        '"changed_fields":["距离上限：5km → 3km"],'
        '"refiner_note":"已收紧范围。"}'
    )


def test_refiner_missing_key_inherits_original_true():
    """缺键继承（拦"忘写"）：original=True，LLM 输出没带该键 → 继承 True，
    显式诉求不因 LLM 忘写而静默翻转（S5 反馈轮二次丢饭根因的钉子）。"""
    from agent.intent.refiner import refine_intent

    original = _intent(explicit_dining_requested=True, raw_input="看展顺便吃饭")
    client = _FixedJsonClient(_refined_payload(include_dining_key=False))
    out = refine_intent(original, "换个更近的展", client=client)
    assert out.refined_intent.explicit_dining_requested is True


def test_refiner_missing_key_inherits_original_false_and_none():
    """缺键继承对三态一致：original=False/None 同样原样继承。"""
    from agent.intent.refiner import refine_intent

    for value in (False, None):
        original = _intent(explicit_dining_requested=value)
        client = _FixedJsonClient(_refined_payload(include_dining_key=False))
        out = refine_intent(original, "换个更近的", client=client)
        assert out.refined_intent.explicit_dining_requested is value, f"value={value}"


def test_refiner_explicit_false_is_legal_retraction():
    """显式 false 放行（不拦"改口"）：original=True，LLM 显式输出 false
    （用户说"算了不吃了"）→ 撤回生效，守卫不覆盖。"""
    from agent.intent.refiner import refine_intent

    original = _intent(explicit_dining_requested=True, raw_input="看展顺便吃饭")
    client = _FixedJsonClient(
        _refined_payload(include_dining_key=True, dining_value="false")
    )
    out = refine_intent(original, "算了不吃了，看完展直接回家", client=client)
    assert out.refined_intent.explicit_dining_requested is False


def test_refiner_rule_fallback_inherits_naturally():
    """规则兜底路径：model_copy(update=...) 天然继承字段（无需守卫）。"""
    from agent.intent.refiner import _rule_fallback

    original = _intent(explicit_dining_requested=True)
    out = _rule_fallback(original, "太远了")
    assert out.refined_intent.explicit_dining_requested is True


def test_refiner_guard_room_path_mirror():
    """房间路径镜像（1.6-(4)）：多成员约束合并是文本级（nickname 前缀拼接），
    走同一 refine_intent 管线——房间形状的反馈文本下缺键守卫同样生效。"""
    from agent.intent.refiner import refine_intent

    original = _intent(explicit_dining_requested=True, raw_input="看展顺便吃饭")
    room_feedback = "小明：换个更近一点的展\n小红：+1，太远了"
    client = _FixedJsonClient(_refined_payload(include_dining_key=False))
    out = refine_intent(original, room_feedback, client=client)
    assert out.refined_intent.explicit_dining_requested is True


# ============================================================
# 5. prompt 镜像钉（防触发集两处实现漂移）
# ============================================================


def test_prompt_mirrors_contain_tristate_rule():
    """blueprint（决策 3/10）/ parser（抽取规则）/ refiner（保持与撤回）三处
    prompt 都必须含三态规则文本——`dining_soft_anchored` 与 blueprint prompt
    是同一条规则的两处独立实现，镜像不得漂移。"""
    from agent.intent.prompts.intent_parser_prompt import (
        INTENT_PARSER_FEW_SHOTS,
        INTENT_PARSER_SYSTEM_PROMPT,
    )
    from agent.intent.prompts.refiner_prompt import (
        REFINER_FEW_SHOTS,
        REFINER_SYSTEM_PROMPT,
    )
    from agent.planning.blueprint.prompts.blueprint_prompt import (
        BLUEPRINT_SYSTEM_PROMPT,
    )

    assert "explicit_dining_requested" in BLUEPRINT_SYSTEM_PROMPT
    assert "explicit_dining_requested" in INTENT_PARSER_SYSTEM_PROMPT
    assert "explicit_dining_requested" in REFINER_SYSTEM_PROMPT

    # parser few-shots：true / false 两个方向都有示范
    joined = "\n".join(out for _, out in INTENT_PARSER_FEW_SHOTS)
    assert '"explicit_dining_requested":true' in joined
    assert '"explicit_dining_requested":false' in joined

    # refiner few-shots：全部示例的 refined_intent 都显式带该键（第一道防线
    # ——不在示范里的字段 LLM 大概率省略）+ 保持/撤回两个语义方向都有
    for i, (_, out) in enumerate(REFINER_FEW_SHOTS):
        assert "explicit_dining_requested" in out, f"few-shot #{i + 1} 缺该字段"
    joined_refiner = "\n".join(out for _, out in REFINER_FEW_SHOTS)
    assert '"explicit_dining_requested":true' in joined_refiner
    assert '"explicit_dining_requested":false' in joined_refiner
