"""test_refiner_inherit_missing_keys_real_llm —— C1 守卫的真 LLM 冒烟探针。

forge-intent-loss（round1-4.md）四轮全程标记的最弱点：所有"忘写/撤回服从率"
数字（8 成收益、null 主信道 70-85% 接住率）都是直觉估计，零真实 LLM 采样，
"只有跑真 LLM（甚至就是本项目要接的真 LLM 冒烟）才能测"（round3.md R3 最弱点
第 1 条）。本文件是那个"必须新增的哨兵"——不做就等于整个修复的收益是未验假设。

覆盖任务书要求的三个断言（同 test_refiner_real_llm.py 的方向性宽松校验风格，
不 mock LLM，真打 API）：
(a) 反馈轮 LLM 忘写 user_stated 的 preferred_poi_types/duration_hours →
    守卫继承回来（不因整体替换而丢失）
(b) 显式撤回"不要预算了" → LLM 输出 null → 守卫放行删除（不被误继承回旧值）
(c) "没提" → 不误删（局部反馈不触及的字段应原样保留）

方法论说明：无法在真 LLM 场景下人为强制"LLM 一定忘写某个键"（那是 LLM 的
自由输出，不受测试控制）——这正是本探针存在的意义：**观察真实 LLM 在这些
场景下的实际行为**，用宽松的端到端断言钉住"用户可感知的结果层面没有退化"，
而不是钉死 LLM 具体怎么输出 JSON（stub/mock 测的是"守卫代码逻辑对不对"，
本文件测的是"配上真 LLM 的实际输出分布后，系统级契约还成不成立"）。

运行条件同 test_refiner_real_llm.py：backend/.env 配好真 LLM，缺则整类 skip。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _load_backend_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_backend_dotenv()

from agent.core.llm_client import reset_llm_client_cache  # noqa: E402
from agent.intent.refiner import refine_intent  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402


def _llm_configured() -> bool:
    return bool(
        os.getenv("LLM_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("QWEN_API_KEY")
    )


pytestmark = pytest.mark.skipif(
    not _llm_configured(),
    reason="未配置真实 LLM（backend/.env 无 LLM_API_KEY），跳过真测",
)


@pytest.fixture(autouse=True)
def _force_real_llm(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "")
    reset_llm_client_cache()
    yield
    reset_llm_client_cache()


def _base_intent_with_poi_and_budget() -> IntentExtraction:
    """上一轮已经明说了品类偏好 + 预算——两个 C 类白名单字段都有非默认
    user_stated 值，构成"这轮反馈只字未提，LLM 整体替换时最容易忘写"的
    真实测试条件。"""
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[2, 3],
        distance_max_km=8.0,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="独处放空",
        raw_input="自己出去转转，想吃个烧烤，预算给到150",
        parse_confidence=0.88,
        preferred_poi_types=["烧烤"],
        budget_per_person=150.0,
        field_provenance={
            "distance_max_km": "user_stated",
        },
    )


class TestRefinerInheritMissingKeysRealLLM:
    """真打 LLM。用宽松方向性断言观察守卫在真实输出分布下的系统级效果。"""

    def test_a_local_feedback_does_not_silently_drop_user_stated_poi_and_budget(self):
        """(a) 局部反馈（"太远了，近一点"）只针对距离，理论上不该动
        preferred_poi_types/budget_per_person——但 refiner 是整体替换架构，
        LLM 有真实概率在这类局部反馈里"顺手"漏写这两个字段。守卫应确保
        无论 LLM 是否忘写，最终结果里这两个 user_stated 字段都不会静默丢失
        （这正是本次修复要根治的"意图丢失"问题本身）。
        """
        original = _base_intent_with_poi_and_budget()
        out = refine_intent(original, "太远了，近一点")

        assert out.refined_intent.distance_max_km < original.distance_max_km, (
            f"局部反馈应生效：距离应缩小，实际={out.refined_intent.distance_max_km}"
        )
        assert "烧烤" in (out.refined_intent.preferred_poi_types or []), (
            f"局部反馈没提品类，preferred_poi_types 不该丢失，"
            f"实际={out.refined_intent.preferred_poi_types}；"
            f"note={out.refiner_note!r}"
        )
        assert out.refined_intent.budget_per_person == 150.0, (
            f"局部反馈没提预算，budget_per_person 不该丢失，"
            f"实际={out.refined_intent.budget_per_person}；"
            f"note={out.refiner_note!r}"
        )

    def test_b_explicit_budget_withdrawal_actually_clears_the_field(self):
        """(b) 用户明确说"预算不设限了" → 最终 budget_per_person 应变成
        None（撤回真正生效），不能因为守卫的继承逻辑而被顶回旧值 150——
        这是 null-on-removal 撤回信道 + 守卫门控协同工作的端到端验证。
        """
        original = _base_intent_with_poi_and_budget()
        out = refine_intent(original, "预算不设限了，好吃的就行，别卡预算")

        assert out.refined_intent.budget_per_person is None, (
            f"显式撤回预算限制后应变为 None，实际={out.refined_intent.budget_per_person}；"
            f"note={out.refiner_note!r}；changed_fields={out.changed_fields!r}"
        )

    def test_b_explicit_poi_withdrawal_actually_clears_the_field(self):
        """(b) 用户明确说"不吃烧烤了" → 最终 preferred_poi_types 不应再含
        "烧烤"（撤回真正生效），不能被守卫继承逻辑顶回旧值。"""
        original = _base_intent_with_poi_and_budget()
        out = refine_intent(original, "不吃烧烤了，随便逛逛就行")

        assert "烧烤" not in (out.refined_intent.preferred_poi_types or []), (
            f"显式撤回品类后不该再含'烧烤'，实际={out.refined_intent.preferred_poi_types}；"
            f"note={out.refiner_note!r}；changed_fields={out.changed_fields!r}"
        )

    def test_c_unrelated_feedback_does_not_over_delete_untouched_fields(self):
        """(c) 反馈只改时长，完全没提品类/预算 → 不应被误判成撤回而清空
        （对照 (b)：没有否定语境时，键缺失应该走继承，不是走撤回）。"""
        original = _base_intent_with_poi_and_budget()
        out = refine_intent(original, "我只有一个小时")

        assert list(out.refined_intent.duration_hours) == [1, 1], (
            f"反馈明说具体小时数，应对齐，实际={out.refined_intent.duration_hours}"
        )
        assert "烧烤" in (out.refined_intent.preferred_poi_types or []), (
            f"反馈没提品类，不该被误判撤回而清空，"
            f"实际={out.refined_intent.preferred_poi_types}；"
            f"note={out.refiner_note!r}"
        )
        assert out.refined_intent.budget_per_person == 150.0, (
            f"反馈没提预算，不该被误判撤回而清空，"
            f"实际={out.refined_intent.budget_per_person}；"
            f"note={out.refiner_note!r}"
        )
