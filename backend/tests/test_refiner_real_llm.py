"""test_refiner_real_llm —— refiner 真实 LLM 集成测试（不 mock）。

验证 spec session-no-new-request 落地：会话内"换场景"的输入交 refiner 后，
LLM 应**覆盖冲突字段**（同行/场景/tag），而非死守"最小修改"——这条靠新加的
B/C few-shot 才生效，必须用真 LLM 才测得出来（stub/规则兜底测不出）。

运行条件：
- backend/.env 配好真 LLM（LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）；缺则整类 skip。
- 会真打 API（约 1-3s/用例），断言对 LLM 输出做"方向性"宽松校验，不卡字面。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _load_backend_dotenv() -> None:
    """把 backend/.env 读进 os.environ（pytest 不会自动加载）。已存在的键不覆盖。"""
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
    """顶掉 conftest 的 stub 默认，让本类真打 LLM。

    tests/conftest.py 有个 autouse fixture 会 `os.environ.setdefault("LLM_PROVIDER","stub")`，
    目的是让确定性测试一律走 StubLLMClient、不误调真 endpoint。但本文件就是要测真 LLM，
    必须把 LLM_PROVIDER 顶成空（→ get_llm_client 走 LLM_API_KEY/BASE_URL/MODEL 的真接口），
    并清掉 lru_cache 里可能缓存的 stub 客户端。monkeypatch 会在用例结束后自动还原。
    """
    monkeypatch.setenv("LLM_PROVIDER", "")
    reset_llm_client_cache()
    yield
    reset_llm_client_cache()


def _base_intent() -> IntentExtraction:
    return IntentExtraction(
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
        raw_input="今天下午带老婆孩子",
        parse_confidence=0.92,
    )


def _roles(out) -> str:
    return "".join(c.role for c in out.refined_intent.companions)


class TestRefinerRealLLM:
    """真打 LLM。断言做方向性宽松校验，避免被 LLM 措辞差异卡死。"""

    def test_scenario_shift_overrides_companions(self):
        """换场景（不带孩子、陪爸妈）→ 同行被覆盖成父母、不再是孩子。"""
        out = refine_intent(
            _base_intent(),
            "不带孩子了，改成陪我爸妈吃个饭，要安静点",
            itinerary_summary=(
                "上一版:家庭半日方案\n"
                "- 14:30 滨江公园·散步 90min\n  ↳ 步行 12min\n"
                "- 15:40 椰林餐厅·用餐 60min"
            ),
        )
        roles = _roles(out)
        assert any(k in roles for k in ("爸", "妈", "父", "母")), (
            f"换场景后同行应改成父母，实际 roles={roles!r}；note={out.refiner_note!r}"
        )
        assert not any(k in roles for k in ("孩子", "娃")), (
            f"明说『不带孩子』，同行不该还有孩子，实际 roles={roles!r}"
        )
        assert out.changed_fields, "换场景应产出 changed_fields（覆盖了字段）"

    def test_local_feedback_minimal_change(self):
        """局部反馈（太远）→ 只缩距离，不动同行（仍是老婆孩子）。"""
        out = refine_intent(_base_intent(), "太远了，3 公里以内吧")
        assert out.refined_intent.distance_max_km <= 3.0, (
            f"『3 公里以内』后 distance_max_km 应 ≤3，实际 {out.refined_intent.distance_max_km}"
        )
        roles = _roles(out)
        assert "孩子" in roles or "娃" in roles, (
            f"局部反馈不该动同行，孩子应还在，实际 roles={roles!r}"
        )
