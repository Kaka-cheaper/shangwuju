"""agent.llm_client —— LLM 客户端 wrapper 的接口契约。

本文件**只定接口**，不含真正的 DeepSeek/通义实现。
P2 真正实现时，A 同学填充 `_DeepSeekClient` 等具体类，并保持公共接口签名不变。

两个核心 protocol：
- `LLMClient`：基础对话接口（chat / stream_chat）
- `FunctionCallingClient`：带 Tool spec 的 Function Calling 接口

全局获取入口：`get_llm_client(provider="deepseek")`——其他模块都从这里拿，
不直接 new 具体客户端。

环境变量约定（.env 内容；P3 前端透传）：
- LLM_PROVIDER=deepseek            # deepseek | qwen
- DEEPSEEK_API_KEY=sk-xxx
- DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
- DEEPSEEK_MODEL=deepseek-chat
- QWEN_API_KEY=sk-xxx               # 备用
- QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
- QWEN_MODEL=qwen-plus
- LLM_TIMEOUT_S=30
- LLM_MAX_RETRIES=2

不负责：
- Prompt 设计（在 backend/agent/prompts/）
- Tool 注册（在 backend/tools/registry.py）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator, Literal, Protocol, runtime_checkable


# ============================================================
# 数据结构
# ============================================================

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class LLMMessage:
    """单条消息。OpenAI 兼容格式。"""

    role: Role
    content: str | None = None
    # 助手回复中的 Tool 调用（OpenAI tool_calls 字段）
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # role="tool" 时填，对应到 tool_calls[i].id
    tool_call_id: str | None = None
    # role="tool" 时填工具名
    name: str | None = None


@dataclass
class LLMChatResponse:
    """LLM 一次回复的解析结果。"""

    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: Literal["stop", "tool_calls", "length", "error"] = "stop"
    raw: dict[str, Any] | None = None


# ============================================================
# Protocol（接口）
# ============================================================

@runtime_checkable
class LLMClient(Protocol):
    """基础 LLM 客户端。"""

    provider: str
    model: str

    def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
    ) -> LLMChatResponse:
        """同步对话；不带 Tool。"""
        ...

    def stream_chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
    ) -> Iterator[str]:
        """流式对话（仅文本 token）；用于打字效果。"""
        ...


@runtime_checkable
class FunctionCallingClient(LLMClient, Protocol):
    """带 Function Calling 的客户端。"""

    def chat_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.3,
        tool_choice: Literal["auto", "required", "none"] = "auto",
    ) -> LLMChatResponse:
        """带 Tool spec 的对话。tools 直接传 TOOL_REGISTRY.all_specs() 结果。"""
        ...


# ============================================================
# 工厂
# ============================================================

ProviderName = Literal["deepseek", "qwen", "stub"]


def get_llm_client(provider: ProviderName | str = "deepseek") -> FunctionCallingClient:
    """全局获取 LLM 客户端。

    P2 时 A 同学实现：
    - "deepseek" → DeepSeekClient（默认）
    - "qwen"     → QwenClient
    - "stub"     → 返回固定响应，给 W2/W3 单测使用

    当前是 placeholder，import 时不报错；调用时显式提示未实现。
    """
    raise NotImplementedError(
        f"LLM 客户端未实现（provider={provider}）；"
        "P2 阶段由 A 同学填充 backend/agent/llm_client.py。"
        "在此之前，W2/W3 测试请用 backend.agent.llm_client_stub.StubLLMClient。"
    )
