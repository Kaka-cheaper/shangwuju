"""agent.v2.model_factory —— Pydantic AI Model 创建工厂。

替代旧 `agent/llm_client.py` 200+ 行的手写 OpenAI SDK wrapper。

设计要点：
- 任何 OpenAI Chat Completions 兼容服务都能直接接入：DeepSeek / Qwen / OpenAI / 智谱 / Moonshot / 本地 Ollama / vLLM / LM Studio
- .env 配置完全沿用旧 llm_client：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
- 旧名（DEEPSEEK_API_KEY / QWEN_API_KEY）继续兼容，user 不需要改 .env

LLM_PROVIDER=stub 由调用方判断（旧路径走 fixture，v2 默认不支持 stub——上层用 feature flag 切换）。
"""

from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlparse

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider


def _resolve_creds(provider_hint: Optional[str] = None) -> tuple[str, str, str, str]:
    """与 agent/llm_client._resolve_creds 兼容的解析逻辑。

    优先级：
    1. LLM_API_KEY + LLM_BASE_URL + LLM_MODEL（推荐）
    2. 按 provider_hint 兜底（deepseek / qwen 旧名）
    3. OpenAI 默认值（base_url / model）

    Returns:
        (api_key, base_url, model, display_provider)
    """
    api_key = (os.getenv("LLM_API_KEY") or "").strip()
    base_url = (os.getenv("LLM_BASE_URL") or "").strip()
    model = (os.getenv("LLM_MODEL") or "").strip()
    provider = (provider_hint or os.getenv("LLM_PROVIDER") or "").strip().lower()

    if provider == "deepseek":
        api_key = api_key or (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        base_url = (
            base_url
            or (os.getenv("DEEPSEEK_BASE_URL") or "").strip()
            or "https://api.deepseek.com/v1"
        )
        model = model or (os.getenv("DEEPSEEK_MODEL") or "").strip() or "deepseek-chat"
    elif provider == "qwen":
        api_key = api_key or (os.getenv("QWEN_API_KEY") or "").strip()
        base_url = (
            base_url
            or (os.getenv("QWEN_BASE_URL") or "").strip()
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        model = model or (os.getenv("QWEN_MODEL") or "").strip() or "qwen-plus"
    else:
        # 通用：缺值给 OpenAI 默认（让用户至少看到「key 没填」类报错而非 base_url 空）
        if not base_url:
            base_url = "https://api.openai.com/v1"
        if not model:
            model = "gpt-4o-mini"

    display = provider or _infer_provider(base_url)
    return api_key, base_url, model, display


def _infer_provider(base_url: str) -> str:
    if not base_url:
        return "openai-compatible"
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return "openai-compatible"
    if "deepseek" in host:
        return "deepseek"
    if "dashscope" in host or "aliyun" in host:
        return "qwen"
    if "openai" in host:
        return "openai"
    if "moonshot" in host:
        return "moonshot"
    if "bigmodel" in host:
        return "zhipu"
    if host in ("localhost", "127.0.0.1", "0.0.0.0"):
        return "local"
    return "openai-compatible"


def create_model(*, use_test: bool = False) -> Model:
    """创建 Pydantic AI Model 实例。

    Args:
        use_test: True 时返回 TestModel（单测/stub 模式用，不调外部 API）

    Returns:
        Pydantic AI 框架的 Model 实例，可直接喂给 Agent(model=...)
    """
    if use_test:
        return TestModel()

    explicit_provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if explicit_provider == "stub":
        # stub 模式：用 TestModel 让 v2 路径也能跑（不调真 LLM）
        return TestModel()

    api_key, base_url, model_name, _display = _resolve_creds(
        explicit_provider or None,
    )
    if not api_key:
        raise RuntimeError(
            "v2 model_factory 缺少 LLM_API_KEY；"
            "请在 backend/.env 设 LLM_API_KEY + LLM_BASE_URL + LLM_MODEL，"
            "或设 LLM_PROVIDER=stub 走 TestModel 兜底"
        )

    provider = OpenAIProvider(api_key=api_key, base_url=base_url)
    return OpenAIChatModel(model_name, provider=provider)


def display_provider() -> str:
    """给 /health 端点显示当前 provider 名（兼容旧逻辑）。"""
    explicit = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if explicit == "stub":
        return "stub"
    _, base_url, _, display = _resolve_creds(explicit or None)
    return display


__all__ = ["create_model", "display_provider"]
