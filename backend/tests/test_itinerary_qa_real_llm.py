"""test_itinerary_qa_real_llm —— 弃答（abstention）的 LLM 路径真测。

字段命中走模板（确定性测试已覆盖）；查不到字段时才调 LLM 凭经验作答，且必须标注是经验。
这条只有真打 LLM 才测得出。运行条件同 test_refiner_real_llm：缺真 LLM 配置则整类 skip。
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

from agent.core.itinerary_qa import answer_itinerary_question  # noqa: E402
from agent.core.llm_client import get_llm_client, reset_llm_client_cache  # noqa: E402


def _llm_configured() -> bool:
    return bool(os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("QWEN_API_KEY"))


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


def _itin() -> dict:
    return {
        "nodes": [
            {"target_kind": "home", "target_id": "home"},
            {"target_kind": "poi", "target_id": "P001"},
            {"target_kind": "restaurant", "target_id": "R001"},
        ]
    }


class TestItineraryQaAbstentionRealLLM:
    def test_abstain_is_honest_and_grounded(self):
        """查不到的字段（停车）→ LLM 既坦诚没数据、又不假装查到了。"""
        ans = answer_itinerary_question("有地方停车吗", _itin(), client=get_llm_client())
        assert ans, "应有回答"
        # 坦诚：明说没有 / 没查到 / 不确定 之类（abstention，不编造确切答案）
        assert any(k in ans for k in ("没有", "没查到", "没找到", "不确定", "未", "没记录")), (
            f"弃答应坦诚说明数据缺失，实际：{ans!r}"
        )
