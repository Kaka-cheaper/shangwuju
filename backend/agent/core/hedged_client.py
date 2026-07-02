"""HedgedLLMClient —— 多 provider hedged request 兜底（spec speed-constraints 优化方案 2）。

设计动机：
    LLM API 服务尾延迟严重（实测见过单次调用从 5s 抖到 197s）。Hackathon 评委演示
    是一锤子买卖，30s 卡死直接 game over。

实现方式：
    HedgedLLMClient 包装两个 LLMClient（primary + backup），主备双发：
    1. 主请求立即发出
    2. 主请求等待 hedge_after_s 秒后仍未返回 → 启动备援请求
    3. 谁先返回用谁的；另一个被取消（线程池 future）

    "hedged request" 来自 Google Tail at Scale（Dean & Barroso 2013）的尾延迟治理范式。

行为契约：
    - 与 LLMClient Protocol 完全一致（chat / stream_chat / chat_with_tools）
    - 单家正常时（5s）：~5s（备援不启动），与单 provider 等价
    - 主家抖动（30-60s）：备援 4-7s 拿到结果，避免长尾
    - 双家都坏：仍然失败（同单 provider）

注意：
    - stream_chat 不 hedge（流式响应特性，难以可靠 race）→ 直接走主路径
    - chat_with_tools 不 hedge（function calling 较少调用 + schema 复杂）→ 直接走主路径
    - 仅 chat（非流式 JSON 响应类）走 hedged → 覆盖 intent 解析 / 蓝图生成 / 文案生成等
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import (
    FIRST_COMPLETED,
    ThreadPoolExecutor,
    wait,
)
from typing import Any, Iterator

from .llm_client import (
    FunctionCallingClient,
    LLMChatResponse,
    LLMClient,
    LLMMessage,
)

logger = logging.getLogger(__name__)


class HedgedLLMClient:
    """主备双发 LLM 客户端；主 hedge_after_s 秒不返回则启动备援。

    Protocol 兼容：实现 LLMClient + chat_with_tools（FunctionCallingClient 子集）。
    """

    def __init__(
        self,
        *,
        primary: LLMClient,
        backup: LLMClient,
        hedge_after_s: float = 3.0,
    ) -> None:
        self._primary = primary
        self._backup = backup
        self._hedge_after_s = max(0.5, float(hedge_after_s))
        # 复用单一线程池，避免每次 chat 新建线程
        self._executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="hedged-llm"
        )
        # 兼容 LLMClient Protocol 字段
        self.provider = f"{primary.provider}+{backup.provider}"
        self.model = f"{primary.model}/{backup.model}"

    # ============================================================
    # Hedged chat（核心）
    # ============================================================

    def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMChatResponse:
        """主备双发 chat；主 hedge_after_s 秒不返回则启动备援。"""
        kwargs = {
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format,
            "max_tokens": max_tokens,
            "extra_body": extra_body,
        }

        primary_future = self._executor.submit(self._primary.chat, **kwargs)

        # 等主路径 hedge_after_s 秒
        done, _ = wait({primary_future}, timeout=self._hedge_after_s)
        if done:
            # 主在 hedge 窗口内返回了
            try:
                return primary_future.result()
            except Exception as e:  # noqa: BLE001
                # 主失败 → 兜底跑备援（同步等结果）
                logger.warning(
                    "[hedged] 主 LLM 调用失败 %s，启动备援 %s",
                    type(e).__name__,
                    self._backup.provider,
                )
                return self._backup.chat(**kwargs)

        # 主仍未完成 → 启动备援
        logger.info(
            "[hedged] 主 LLM 调用超 %.1fs 未返回，启动备援 %s",
            self._hedge_after_s,
            self._backup.provider,
        )
        backup_future = self._executor.submit(self._backup.chat, **kwargs)
        # 谁先返回用谁的；超时按主备 timeout 自然失败
        done, pending = wait(
            {primary_future, backup_future},
            return_when=FIRST_COMPLETED,
        )
        if not done:
            # 不应该发生（wait 不带 timeout）；防御性
            raise RuntimeError("hedged: 主备都未返回")

        winner = next(iter(done))
        try:
            result = winner.result()
            # 取消未完成的另一路（释放连接）
            for fut in pending:
                fut.cancel()
            return result
        except Exception as e:  # noqa: BLE001
            # 先返回的那个失败 → 等另一个
            logger.warning(
                "[hedged] 先返回的 LLM 失败 %s，等待另一路",
                type(e).__name__,
            )
            for fut in pending:
                try:
                    return fut.result()
                except Exception as e2:  # noqa: BLE001
                    logger.warning(
                        "[hedged] 另一路也失败 %s，整体失败",
                        type(e2).__name__,
                    )
                    raise e2 from e
            raise

    # ============================================================
    # 流式 / Function Calling 不 hedge（性价比低 + 实现复杂）
    # ============================================================

    def stream_chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        """流式不 hedge（流式响应难以可靠 race），直接走主路径。"""
        return self._primary.stream_chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=extra_body,
        )

    def chat_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.3,
        tool_choice: str = "auto",
    ) -> LLMChatResponse:
        """Function calling 不 hedge（schema 复杂；少量调用），直接走主路径。"""
        return self._primary.chat_with_tools(
            messages,
            tools,
            temperature=temperature,
            tool_choice=tool_choice,  # type: ignore[arg-type]
        )

    def __del__(self):
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001
            pass


# ============================================================
# 工厂：从 env 构造 HedgedLLMClient（如配了 backup）
# ============================================================


def maybe_build_hedged_client(primary: FunctionCallingClient) -> FunctionCallingClient:
    """如果 env 配了 LLM_API_KEY_BACKUP 等，把 primary 包成 HedgedLLMClient；否则原样返回。

    env 配置（任一缺即退化为单 provider）：
        LLM_API_KEY_BACKUP    备援 provider 的 API key
        LLM_BASE_URL_BACKUP   备援 provider 的 base_url
        LLM_MODEL_BACKUP      备援 provider 的 model
        LLM_HEDGE_AFTER_S     主路径多少秒不返回启动备援（默认 3.0）

    安全：任何构造异常 → 回退原 primary（不破基础链路）
    """
    backup_key = (os.getenv("LLM_API_KEY_BACKUP") or "").strip()
    backup_url = (os.getenv("LLM_BASE_URL_BACKUP") or "").strip()
    backup_model = (os.getenv("LLM_MODEL_BACKUP") or "").strip()
    if not (backup_key and backup_url and backup_model):
        return primary

    try:
        from .llm_client import OpenAICompatibleClient, _infer_provider_from_url

        backup = OpenAICompatibleClient(
            api_key=backup_key,
            base_url=backup_url,
            model=backup_model,
            provider=_infer_provider_from_url(backup_url),
            timeout_s=float(os.getenv("LLM_TIMEOUT_S") or 30.0),
            max_retries=int(os.getenv("LLM_MAX_RETRIES") or 2),
        )
        hedge_after = float(os.getenv("LLM_HEDGE_AFTER_S") or 3.0)
        logger.info(
            "[hedged] 启用主备双发：primary=%s backup=%s hedge_after=%.1fs",
            primary.provider,
            backup.provider,
            hedge_after,
        )
        return HedgedLLMClient(  # type: ignore[return-value]
            primary=primary,
            backup=backup,
            hedge_after_s=hedge_after,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[hedged] 构造 HedgedLLMClient 失败 %s，回退单 provider",
            type(e).__name__,
        )
        return primary
