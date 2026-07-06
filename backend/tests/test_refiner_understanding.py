"""tests.test_refiner_understanding —— 信任带修订5：refiner 反馈味 `understanding`。

背景（`路演PPT/信任带设计终稿.md` 文末"修订"节第 5 条）：intent 首轮解析已有
`understanding`（信任带①拍，见 test_intent_understanding_schema.py）；反馈轮
（refiner）此前完全不产这个字段，导致"改口重排"这条第二段小弧的①拍在信任带
里是空的——评委看不到"它在响应我这次说的什么"。本文件钉住反馈轮对称补齐：

1. schema 层：`RefinementOutput.refined_intent` 本就是 `IntentExtraction`，
   字段已存在（Optional 默认空串）——LLM 路径只要在 JSON 里给这个键就会被
   `IntentExtraction.model_validate` 原样收下，不需要额外映射代码。
2. LLM 路径：refiner prompt 需要教会 LLM 现生成"反馈味"一句
   （句式"用户说……，我理解成……"），few-shot 至少一条做示范。
3. **stub 兜（关键）**：`LLM_PROVIDER=stub` 下 refiner 实际走的是 `_rule_fallback`
   （`StubLLMClient.chat()` 返回的是扁平 `IntentExtraction` JSON，没有
   `refined_intent` 外层包装，校验必炸 → 重试仍炸 → 落规则兜底——见
   test_refiner.py::test_refine_intent_with_stub_falls_back_to_rule 钉住的既有
   行为，这里不改）。所以"反馈味"必须也在 `_rule_fallback` 里按关键词分类
   现算一句，`--stub` 冒烟才能看到反馈轮①拍真的有内容，而不是永远空字符串。
"""

from __future__ import annotations

import json

from agent.core.llm_client import LLMChatResponse
from agent.core.llm_client_stub import StubLLMClient
from agent.intent.refiner import _rule_fallback, refine_intent
from agent.intent.prompts.refiner_prompt import REFINER_FEW_SHOTS, REFINER_SYSTEM_PROMPT
from schemas.intent import Companion, IntentExtraction

_FORBIDDEN_WORDS = ("为您", "精心", "智能", "贴心", "一站式", "量身")


def _base_intent(**overrides) -> IntentExtraction:
    base = dict(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5,
        companions=[Companion(role="妻子", count=1), Companion(role="孩子", age=5, count=1)],
        physical_constraints=["亲子友好", "适合 5-10 岁"],
        dietary_constraints=["低脂", "健康轻食"],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="今天下午带老婆孩子",
        parse_confidence=0.92,
    )
    base.update(overrides)
    return IntentExtraction(**base)


# ============================================================
# 1. _rule_fallback（stub 兜底实际路径）：反馈味 understanding
# ============================================================


def test_rule_fallback_understanding_is_feedback_flavored():
    intent = _base_intent()
    out = _rule_fallback(intent, "太远了，希望近一点")
    u = out.refined_intent.understanding
    assert u != ""
    assert u.startswith("用户")
    assert "我理解成" in u
    assert len(u) <= 40
    for w in _FORBIDDEN_WORDS:
        assert w not in u


def test_rule_fallback_understanding_varies_with_feedback_category():
    """不同关键词分类应给出不同措辞（不是无脑复读同一句），至少距离类与预算类不同。"""
    intent = _base_intent()
    near = _rule_fallback(intent, "太远了，希望近一点").refined_intent.understanding
    cheaper = _rule_fallback(intent, "便宜点").refined_intent.understanding
    assert near != cheaper


def test_rule_fallback_empty_feedback_understanding_still_nonempty_and_styled():
    intent = _base_intent()
    out = _rule_fallback(intent, "")
    u = out.refined_intent.understanding
    assert u != ""
    assert "我理解成" in u
    assert len(u) <= 40


# ============================================================
# 2. refine_intent 端到端（stub provider）—— --stub 冒烟实际路径
# ============================================================


def test_refine_intent_with_stub_provider_has_non_empty_understanding():
    intent = _base_intent()
    out = refine_intent(intent, "太远了", client=StubLLMClient())
    assert out.refined_intent.understanding != ""
    assert out.refined_intent.understanding.startswith("用户")


# ============================================================
# 3. LLM 成功路径：payload 里给的 understanding 原样透传（无需额外映射代码）
# ============================================================


class _FixedRefineClient:
    def __init__(self, refined_payload: dict, changed_fields: list[str]):
        self._content = json.dumps(
            {
                "refined_intent": refined_payload,
                "changed_fields": changed_fields,
                "refiner_note": "已调整。",
            },
            ensure_ascii=False,
        )

    def chat(self, messages, *, temperature=0.2, response_format=None, max_tokens=None, extra_body=None):
        return LLMChatResponse(content=self._content, finish_reason="stop")


def test_llm_path_passes_through_understanding_unchanged():
    intent = _base_intent()
    refined_payload = intent.model_dump()
    refined_payload["distance_max_km"] = 3.0
    refined_payload["understanding"] = "用户说太远了，我理解成要拉近距离"

    client = _FixedRefineClient(refined_payload, ["距离上限：5km → 3km"])
    out = refine_intent(intent, "太远了", client=client)

    assert out.refined_intent.understanding == "用户说太远了，我理解成要拉近距离"


def test_llm_path_missing_understanding_defaults_to_empty_string():
    """LLM 没给这个键（旧行为/降级）→ Optional 默认空串，不破既有校验。"""
    intent = _base_intent()
    refined_payload = intent.model_dump()
    refined_payload["distance_max_km"] = 3.0
    refined_payload.pop("understanding", None)

    client = _FixedRefineClient(refined_payload, ["距离上限：5km → 3km"])
    out = refine_intent(intent, "太远了", client=client)

    assert out.refined_intent.understanding == ""


# ============================================================
# 4. prompt 契约：风格红线 + few-shot 示范
# ============================================================


def test_system_prompt_has_understanding_style_rules():
    # 禁词本身会在"禁词：为您/精心/……"这条规则行里被列出来（同 intent 版
    # prompt 的既有写法），所以这里只断言风格红线三要素存在，不对整份
    # system prompt 做"完全不含禁词"的检查（few-shot **输出内容**里不能出现
    # 禁词才是真正要守的契约，见下面 test_at_least_one_few_shot_demonstrates_understanding）。
    assert "understanding" in REFINER_SYSTEM_PROMPT
    assert "用户说" in REFINER_SYSTEM_PROMPT
    assert "我理解成" in REFINER_SYSTEM_PROMPT
    assert "禁词" in REFINER_SYSTEM_PROMPT


def test_at_least_one_few_shot_demonstrates_understanding():
    payloads = [json.loads(assistant) for _user, assistant in REFINER_FEW_SHOTS]
    demonstrated = [
        p["refined_intent"]["understanding"]
        for p in payloads
        if p["refined_intent"].get("understanding")
    ]
    assert demonstrated, "至少一条 few-shot 需要示范 understanding 反馈味输出"
    for u in demonstrated:
        assert len(u) <= 40
        for w in _FORBIDDEN_WORDS:
            assert w not in u
