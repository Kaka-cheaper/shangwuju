"""agent.llm_client —— OpenAI 兼容 LLM 客户端 wrapper（任意 base_url 通用）。

设计原则（解耦）：
- 任何 OpenAI Chat Completions 兼容服务都能直接接入：DeepSeek / 通义 / OpenAI / 智谱 GLM / 月之暗面 / 本地 Ollama / vLLM / LM Studio …
- 上层（intent_parser / planner / refiner）调 `get_llm_client()`，**不感知**具体模型供应商
- 客户端实现只有一个 `OpenAICompatibleClient`；不再为每个模型写空壳子类

环境变量约定（详见 backend/.env.example）：
- 主接口（推荐）：
    LLM_API_KEY    任意 OpenAI 兼容服务的 API key
    LLM_BASE_URL   任意 OpenAI 兼容 endpoint，如：
                     https://api.deepseek.com/v1
                     https://dashscope.aliyuncs.com/compatible-mode/v1
                     https://api.openai.com/v1
                     http://localhost:11434/v1               （Ollama）
                     http://localhost:1234/v1                （LM Studio）
    LLM_MODEL      模型名，如 deepseek-chat / qwen-plus / gpt-4o-mini / glm-4-plus / llama3.2
- 全局：
    LLM_PROVIDER       provider 显式名（仅作展示用；缺省按 base_url 推断）
    LLM_TIMEOUT_S      默认 30
    LLM_MAX_RETRIES    默认 2
- 特殊值：
    LLM_PROVIDER=stub  → 加载 StubLLMClient（开发/单测，无 API 调用）
- 向后兼容（旧 .env）：
    DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
    QWEN_API_KEY     / QWEN_BASE_URL     / QWEN_MODEL
    若 LLM_API_KEY 缺失，按 LLM_PROVIDER 回退到上面这些旧名（不强制 user 改 .env）

不负责：
- Prompt 设计（在 backend/agent/prompts/）
- Tool 注册（在 backend/tools/registry.py）
- 业务逻辑（在 backend/agent/{intent_parser,planner,refiner}.py）

防御策略（参考 pitfalls.md）：
- P2-预埋：JSON 围栏剥离
- P3-跨项目：超时 30s + 重试 2 次（429 指数退避）
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse

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
# 通用 OpenAI 兼容客户端（任意 base_url 直接用）
# ============================================================

class OpenAICompatibleClient:
    """OpenAI Chat Completions 兼容服务的统一封装。

    任何遵循 OpenAI Chat Completions API（/v1/chat/completions）的服务都能直接接入：
    传入 api_key + base_url + model 三件套即可。

    `provider` 字段仅作展示用（LLM_PROVIDER env 或从 base_url 推断），不参与协议判断。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        provider: str = "openai-compatible",
        timeout_s: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        if not api_key:
            raise ValueError(
                f"LLM 客户端缺少 API Key（provider={provider}, base_url={base_url}）；"
                "请在 .env 设 LLM_API_KEY（详见 backend/.env.example）"
            )
        if not base_url:
            raise ValueError(
                "LLM 客户端缺少 base_url；请在 .env 设 LLM_BASE_URL"
            )
        if not model:
            raise ValueError("LLM 客户端缺少 model；请在 .env 设 LLM_MODEL")

        self.provider = provider
        self.model = model
        self.base_url = base_url
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
# 向后兼容别名（DeepSeek/Qwen 名字保留，行为完全一致）
# ============================================================

class DeepSeekClient(OpenAICompatibleClient):
    """向后兼容别名：与 OpenAICompatibleClient 行为一致。

    保留是为了：
    1. 老代码 `from agent.core.llm_client import DeepSeekClient` 不破
    2. 测试或日志想标识 provider="deepseek"
    """


class QwenClient(OpenAICompatibleClient):
    """向后兼容别名。"""


# ============================================================
# 工厂：解析 .env → 构造客户端
# ============================================================


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


def _infer_provider_from_url(base_url: str) -> str:
    """从 base_url 自动推断 provider 展示名（仅展示用，不参与协议判断）。"""
    if not base_url:
        return "openai-compatible"
    try:
        host = urlparse(base_url).hostname or ""
    except Exception:  # noqa: BLE001
        return "openai-compatible"
    host = host.lower()
    table = {
        "api.deepseek.com": "deepseek",
        "dashscope.aliyuncs.com": "qwen",
        "api.openai.com": "openai",
        "open.bigmodel.cn": "zhipu",
        "api.moonshot.cn": "moonshot",
        "api.minimax.chat": "minimax",
        "api.anthropic.com": "anthropic-compat",
    }
    for needle, name in table.items():
        if needle in host:
            return name
    if host in ("localhost", "127.0.0.1", "0.0.0.0"):
        return "local"
    return "openai-compatible"


def _resolve_creds(provider_hint: Optional[str]) -> tuple[str, str, str, str]:
    """按优先级解析 (api_key, base_url, model, provider 展示名)。

    优先级：
    1. 主接口：LLM_API_KEY + LLM_BASE_URL + LLM_MODEL（推荐）
    2. 兼容旧名（按 provider_hint）：
        deepseek → DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
        qwen     → QWEN_API_KEY     / QWEN_BASE_URL     / QWEN_MODEL
    3. 主接口缺 base_url 时按 provider_hint 兜底默认值（deepseek/qwen 各有默认 endpoint）
    """
    api_key = os.getenv("LLM_API_KEY", "").strip()
    base_url = os.getenv("LLM_BASE_URL", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    provider = (provider_hint or os.getenv("LLM_PROVIDER", "")).strip().lower()

    # 旧兼容：deepseek
    if provider == "deepseek":
        api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "").strip()
        base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", "").strip() or "https://api.deepseek.com/v1"
        model = model or os.getenv("DEEPSEEK_MODEL", "").strip() or "deepseek-chat"
    # 旧兼容：qwen
    elif provider == "qwen":
        api_key = api_key or os.getenv("QWEN_API_KEY", "").strip()
        base_url = (
            base_url
            or os.getenv("QWEN_BASE_URL", "").strip()
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        model = model or os.getenv("QWEN_MODEL", "").strip() or "qwen-plus"
    # 通用：base_url 缺省时给一个 OpenAI 默认值，避免不友好的报错
    else:
        if not base_url:
            base_url = "https://api.openai.com/v1"
        if not model:
            model = "gpt-4o-mini"

    # 推断展示名（如果 user 没显式给 LLM_PROVIDER）
    display_provider = provider or _infer_provider_from_url(base_url)
    return api_key, base_url, model, display_provider


def get_llm_client(
    provider: str | None = None,
) -> FunctionCallingClient:
    """全局获取 LLM 客户端。

    解析逻辑：
    - provider="stub"（参数或 LLM_PROVIDER）→ 返回 StubLLMClient（开发/单测）
    - 其他情况 → 用 _resolve_creds() 拿到 (api_key, base_url, model, display_provider)
                  → 构造 OpenAICompatibleClient
    任何 OpenAI 兼容 endpoint 都行，不限于 deepseek/qwen。
    """
    explicit = (provider or os.getenv("LLM_PROVIDER") or "").strip().lower()

    if explicit == "stub":
        from .llm_client_stub import StubLLMClient

        return StubLLMClient()  # type: ignore[return-value]

    api_key, base_url, model, display_provider = _resolve_creds(explicit or None)
    timeout_s = _env_float("LLM_TIMEOUT_S", 30.0)
    max_retries = _env_int("LLM_MAX_RETRIES", 2)

    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider=display_provider,
        timeout_s=timeout_s,
        max_retries=max_retries,
    )
