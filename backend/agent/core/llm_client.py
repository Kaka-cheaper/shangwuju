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
from functools import lru_cache
from typing import Any, Iterator, Literal, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse

from openai import APIError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

import httpx


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
# 共享 extra_body 常量
# ============================================================

MIMO_THINKING_DISABLED_EXTRA_BODY: dict = {"thinking": {"type": "disabled"}}
"""关闭 MiMo「深度思考」模式的 extra_body（MiMo 官方文档 mimo.mi.com/docs
"Deep Thinking Mode"；OpenAI SDK 无对应字段，须走 `.chat(extra_body=...)`
透传——`OpenAICompatibleClient._create_completion` 已把 extra_body 原样并入
请求体，见该方法实现）。

原本只有 `agent/intent/narrator.py` 私有定义（narrator 曾踩过"思考 token 吃光
max_tokens 预算，正文被截成空字符串"的静默失败，才加的双保险之一），本批
（真因修复批 item 5）搬到这里供 `agent/planning/blueprint/blueprint_llm.py`
共享——蓝图生成同样是"只要结构化 JSON 输出，不要思考过程"的场景，同样的
风险（思考 token 挤占蓝图 JSON 的输出预算）理应带同一份保险，不必等它在
生产上真出事才补。对不识别这个字段的 provider（非 MiMo）是无害的多余字段，
OpenAI 兼容服务通常忽略未知字段，不需要按 provider 分支处理。
"""


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
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMChatResponse: ...

    def stream_chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
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
        # 优化方案 1：HTTP/2 + 共享连接池
        # - http2=True：单 TCP 连接多路复用 LLM 调用，节省 TLS 握手 ~200ms × N
        # - max_keepalive_connections：长连接池大小（覆盖单次规划 4-5 次串行 LLM 调用）
        # - keepalive_expiry：连接保活时间（30s 足够覆盖一次完整规划链路）
        # 失败兜底：h2 包未装时 httpx 自动降级到 HTTP/1.1
        try:
            http_client = httpx.Client(
                http2=True,
                timeout=timeout_s,
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                ),
            )
        except ImportError:
            # h2 包未装时降级 HTTP/1.1（不破基础功能）
            http_client = httpx.Client(
                timeout=timeout_s,
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                    keepalive_expiry=30.0,
                ),
            )
        # openai SDK 自带的 max_retries 走它的，外层 _retry 是兜底
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_s,
            http_client=http_client,
        )

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
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
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
        if max_tokens is not None and max_tokens > 0:
            kwargs["max_tokens"] = max_tokens
        if extra_body:
            # OpenAI Python SDK 的标准挂点：extra_body 里的键会原样并入请求体，
            # 用来透传 provider 特有、SDK 未建模的字段（如 MiMo 的
            # `thinking: {"type": "disabled"}` 关闭深度思考——见 narrator.py
            # 调用点注释 + docs.md "Deep Thinking Mode"）。任何 OpenAI 兼容
            # provider 通用，不 provider-specific 写死在这里。
            kwargs["extra_body"] = extra_body
        return self._client.chat.completions.create(**kwargs)

    # ---- 公共接口 ----

    def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMChatResponse:
        def _do():
            return self._create_completion(
                messages,
                temperature=temperature,
                response_format=response_format,
                stream=False,
                max_tokens=max_tokens,
                extra_body=extra_body,
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
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        # 流式不重试；简单返
        stream = self._create_completion(
            messages,
            temperature=temperature,
            stream=True,
            max_tokens=max_tokens,
            extra_body=extra_body,
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
    *,
    task: str | None = None,
) -> FunctionCallingClient:
    """全局获取 LLM 客户端（单例缓存版 + 任务级模型路由）。

    优化方案 1（单例化）+ 优化方案 3.4（任务级模型路由），详见各方法 docstring。

    Args:
        provider: 显式 provider 名（与 LLM_PROVIDER env 同义；None=按 env 推断）
        task:     可选任务标签；非空时按 LLM_MODEL_<TASK> env 覆写 model
                  - "intent"     → LLM_MODEL_INTENT（缺省回退主 model）
                  - "narration"  → LLM_MODEL_NARRATION
                  - "router"     → LLM_MODEL_ROUTER
                  让简单任务用小模型（qwen-turbo / gpt-4o-mini）省 3-5s
    """
    explicit = (provider or os.getenv("LLM_PROVIDER") or "").strip().lower()
    return _get_llm_client_cached(explicit, task or "")


@lru_cache(maxsize=32)
def _get_llm_client_cached(provider: str, task: str) -> FunctionCallingClient:
    """实际构造 + 缓存 LLMClient。被 get_llm_client 调用，按 (provider, task) 缓存。

    优化方案 1：(provider, task) 复用同一个 OpenAICompatibleClient → httpx 连接池复用
    优化方案 3.4：task 非空时按 LLM_MODEL_<TASK> env 覆写 model（小模型路由）
    """
    if provider == "stub":
        from .llm_client_stub import StubLLMClient

        return StubLLMClient()  # type: ignore[return-value]

    api_key, base_url, model, display_provider = _resolve_creds(provider or None)

    # 优化方案 3.4：按任务覆写 model（缺省回退主 model）
    if task:
        task_model = (os.getenv(f"LLM_MODEL_{task.upper()}") or "").strip()
        if task_model:
            model = task_model
            display_provider = f"{display_provider}/{task}"

    timeout_s = _env_float("LLM_TIMEOUT_S", 30.0)
    max_retries = _env_int("LLM_MAX_RETRIES", 2)

    primary = OpenAICompatibleClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider=display_provider,
        timeout_s=timeout_s,
        max_retries=max_retries,
    )

    # 优化方案 2：如配了 LLM_API_KEY_BACKUP 等 env，包成 HedgedLLMClient（主备双发）
    # env 未配 → 原样返回 primary（单 provider 行为不变，零成本兜底）
    from .hedged_client import maybe_build_hedged_client

    return maybe_build_hedged_client(primary)


def reset_llm_client_cache() -> None:
    """清空 LLM 客户端缓存。供单测在 mock env 后重新构造时调用。"""
    _get_llm_client_cached.cache_clear()
