"""agent.llm_client —— LLM 客户端 wrapper（DeepSeek 主 / 通义备 / Stub）。

接口契约（Protocol）+ 三个实现（DeepSeek / Qwen / Stub）+ 工厂函数。
统一用 OpenAI 兼容 SDK，`base_url` 切 provider；上层不感知。

环境变量约定（详见 backend/.env.example）：
- LLM_PROVIDER=deepseek            # deepseek | qwen | stub
- DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
- QWEN_API_KEY     / QWEN_BASE_URL     / QWEN_MODEL
- LLM_TIMEOUT_S=30
- LLM_MAX_RETRIES=2

不负责：
- Prompt 设计（在 backend/agent/prompts/）
- Tool 注册（在 backend/tools/registry.py）
- 业务逻辑（在 backend/agent/{intent_parser,planner,executor}.py）

防御策略（参考 pitfalls.md）：
- P2-预埋：JSON 围栏剥离
- P3-跨项目：超时 30s + 重试 2 次（429 指数退避）
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Protocol, runtime_checkable

from openai import APIError, APIStatusError, APITimeoutError, OpenAI, RateLimitError


# ============================================================
# 数据结构（OpenAI 兼容）
# ============================================================

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class LLMMessage:
    role: Role
    content: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_openai(self) -> dict[str, Any]:
        """转为 OpenAI Chat Completions API 格式。"""
        msg: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            msg["content"] = self.content
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.name:
            msg["name"] = self.name
        return msg


@dataclass
class LLMChatResponse:
    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: Literal["stop", "tool_calls", "length", "error"] = "stop"
    raw: dict[str, Any] | None = None


# ============================================================
# Protocol
# ============================================================

@runtime_checkable
class LLMClient(Protocol):
    provider: str
    model: str

    def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
    ) -> LLMChatResponse: ...

    def stream_chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
    ) -> Iterator[str]: ...


@runtime_checkable
class FunctionCallingClient(LLMClient, Protocol):
    def chat_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.3,
        tool_choice: Literal["auto", "required", "none"] = "auto",
    ) -> LLMChatResponse: ...


# ============================================================
# 共享：JSON 围栏剥离 + 重试 helper
# ============================================================

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def strip_json_fence(text: str | None) -> str | None:
    """剥离 markdown 代码块围栏。

    pitfalls P2-预埋：中文 LLM 返回 ```json ... ``` 时直接 json.loads 会失败。
    本函数是裸字符串的兜底；首选仍是 response_format={"type":"json_object"}。
    """
    if text is None:
        return None
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _retry(
    func,
    *,
    max_retries: int,
    on_rate_limit_backoff_s: float = 2.0,
):
    """简单重试：APITimeout / APIError / RateLimit 都重试；429 指数退避。"""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except RateLimitError as e:
            last_exc = e
            if attempt < max_retries:
                time.sleep(on_rate_limit_backoff_s * (2**attempt))
                continue
            raise
        except (APITimeoutError, APIStatusError, APIError) as e:
            last_exc = e
            if attempt < max_retries:
                time.sleep(0.5)
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("retry 内部错误：循环未执行")


# ============================================================
# OpenAI 兼容客户端基类（DeepSeek / Qwen 共用）
# ============================================================

class _OpenAICompatibleClient:
    """OpenAI 兼容 SDK 的统一封装。DeepSeek / 通义都走这套。"""

    provider: str = "openai-compatible"
    model: str = ""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_s: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        if not api_key:
            raise ValueError(
                f"{self.provider} 缺少 API Key；请检查 .env（详见 backend/.env.example）"
            )
        self.model = model
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        # openai SDK 自带的 max_retries 走它的，外层 _retry 是兜底
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s)

    # ---- 内部 ----

    def _create_completion(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        stream: bool = False,
    ):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        return self._client.chat.completions.create(**kwargs)

    # ---- 公共接口 ----

    def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
    ) -> LLMChatResponse:
        def _do():
            return self._create_completion(
                messages,
                temperature=temperature,
                response_format=response_format,
                stream=False,
            )

        resp = _retry(_do, max_retries=self._max_retries)
        choice = resp.choices[0]
        content = choice.message.content
        # 即使 response_format=json_object，部分国产 LLM 仍会带围栏
        if response_format and response_format.get("type") == "json_object":
            content = strip_json_fence(content)
        return LLMChatResponse(
            content=content,
            tool_calls=[
                tc.model_dump() if hasattr(tc, "model_dump") else dict(tc)
                for tc in (choice.message.tool_calls or [])
            ],
            finish_reason=choice.finish_reason or "stop",
            raw=resp.model_dump() if hasattr(resp, "model_dump") else None,
        )

    def stream_chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
    ) -> Iterator[str]:
        # 流式不重试；简单返
        stream = self._create_completion(
            messages, temperature=temperature, stream=True
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            text = getattr(delta, "content", None)
            if text:
                yield text

    def chat_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.3,
        tool_choice: Literal["auto", "required", "none"] = "auto",
    ) -> LLMChatResponse:
        def _do():
            return self._create_completion(
                messages,
                temperature=temperature,
                tools=tools,
                tool_choice=tool_choice,
                stream=False,
            )

        resp = _retry(_do, max_retries=self._max_retries)
        choice = resp.choices[0]
        return LLMChatResponse(
            content=choice.message.content,
            tool_calls=[
                tc.model_dump() if hasattr(tc, "model_dump") else dict(tc)
                for tc in (choice.message.tool_calls or [])
            ],
            finish_reason=choice.finish_reason or "stop",
            raw=resp.model_dump() if hasattr(resp, "model_dump") else None,
        )


# ============================================================
# 具体 provider
# ============================================================

class DeepSeekClient(_OpenAICompatibleClient):
    provider = "deepseek"


class QwenClient(_OpenAICompatibleClient):
    provider = "qwen"


# ============================================================
# 工厂
# ============================================================

ProviderName = Literal["deepseek", "qwen", "stub"]


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def get_llm_client(
    provider: ProviderName | str | None = None,
) -> FunctionCallingClient:
    """全局获取 LLM 客户端。

    优先级：参数 > LLM_PROVIDER 环境变量 > 默认 "deepseek"
    """
    provider = (provider or os.getenv("LLM_PROVIDER") or "deepseek").lower()
    timeout_s = _env_float("LLM_TIMEOUT_S", 30.0)
    max_retries = _env_int("LLM_MAX_RETRIES", 2)

    if provider == "stub":
        # 延迟 import 防循环
        from .llm_client_stub import StubLLMClient

        return StubLLMClient()  # type: ignore[return-value]

    if provider == "deepseek":
        return DeepSeekClient(
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            timeout_s=timeout_s,
            max_retries=max_retries,
        )

    if provider == "qwen":
        return QwenClient(
            api_key=os.getenv("QWEN_API_KEY", ""),
            base_url=os.getenv(
                "QWEN_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            model=os.getenv("QWEN_MODEL", "qwen-plus"),
            timeout_s=timeout_s,
            max_retries=max_retries,
        )

    raise ValueError(
        f"未知 LLM_PROVIDER: {provider}（合法值：deepseek / qwen / stub）"
    )
