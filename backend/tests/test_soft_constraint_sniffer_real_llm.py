"""test_soft_constraint_sniffer_real_llm —— 软约束嗅探 LLM 版真测（不 mock）。

规则版只认有限关键词；隐晦表达（"我家那位上了年纪"）靠 LLM 兜底——这条必须真打
LLM 才测得出。运行条件同 test_refiner_real_llm：backend/.env 配好真 LLM，缺则整类 skip。
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

from agent.core.llm_client import get_llm_client, reset_llm_client_cache  # noqa: E402
from agent.core.soft_constraint_sniffer import (  # noqa: E402
    sniff_rule,
    sniff_soft_constraints,
)


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
    """顶掉 conftest 的 stub 默认（见 test_refiner_real_llm 同名 fixture）。"""
    monkeypatch.setenv("LLM_PROVIDER", "")
    reset_llm_client_cache()
    yield
    reset_llm_client_cache()


class TestSoftConstraintSnifferRealLLM:
    """真打 LLM。断言做方向性宽松校验。"""

    def test_llm_catches_implicit_elderly(self):
        """规则漏掉的隐晦表达（"上了年纪"非规则词）→ LLM 抽出适老 tag。"""
        text = "我家那位上了年纪，走两步路就喊腿酸"
        assert sniff_rule(text) == [], "前提：这句不该被规则命中，才能验证 LLM 兜底"
        hits = sniff_soft_constraints(text, client=get_llm_client())
        tags = {t for c in hits for t in c.tags}
        assert tags & {"适合老人", "可休息", "低强度", "无障碍", "无台阶"}, (
            f"LLM 应从『上了年纪、走两步腿酸』推出适老/低强度类 tag，实际 {tags}"
        )

    def test_llm_ignores_pure_emotion(self):
        """纯情绪、无可执行约束 → LLM 不该硬凑 tag。"""
        hits = sniff_soft_constraints("唉，今天心情有点低落", client=get_llm_client())
        tags = {t for c in hits for t in c.tags}
        assert not tags, f"纯情绪不该抽出硬约束，实际 {tags}"
