"""agent.llm_client_stub —— 给 W2/W3 测试用的 LLMClient 实现。

W3 前端开发时不需要真 LLM，可在 .env 里设 `LLM_PROVIDER=stub` 把整条链路跑通。
W2 单测也用本 stub，避免每次测试都调真 LLM。

**警告**：返回的内容是固定 fixture，仅覆盖家庭主场景。其他输入会返一段固定文案。
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Literal

from .llm_client import LLMChatResponse, LLMMessage


class StubLLMClient:
    """固定响应的 LLM 客户端，仅用于本地开发/单测。"""

    provider: str = "stub"
    model: str = "stub-model"

    def chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> LLMChatResponse:
        # extra_body（如 MiMo 的 thinking 开关）对 stub 无意义，接住即可——
        # 不接会导致真实调用路径（narrator.py 传 extra_body）在 stub 模式
        # 下抛 TypeError，破坏 LLM_PROVIDER=stub 的单测/演示兜底路径。
        del extra_body
        # 简单的家庭主场景兜底
        sample_intent = {
            "start_time": "today_afternoon",
            "duration_hours": [4, 6],
            "distance_max_km": 5,
            "companions": [
                {"role": "妻子", "count": 1},
                {"role": "孩子", "age": 5, "count": 1},
            ],
            "physical_constraints": ["亲子友好", "适合 5-10 岁"],
            "dietary_constraints": ["低脂", "健康轻食"],
            "experience_tags": [],
            "social_context": "家庭日常",
            "raw_input": _last_user(messages),
            "parse_confidence": 0.88,
            "ambiguous_fields": [],
            # ADR-0014 决策 1（G-1）：本 stub 的固定 JSON 被多处测试当作
            # "任意一次 client.chat() 的通用回复"复用（不止 parse_intent，
            # 也包括 itinerary_qa._abstain 等把 resp.content 直接当 reply_text
            # 使用、且有长度上限的调用点——见 RouterDecision.reply_text ≤400
            # 字）。刻意**不**在这里加 field_provenance 自报键：加长这段固定
            # JSON 会顶穿那些无关调用点的长度上限（已实测 test_e0a_graph_
            # confirm_writeback 因此炸——真实字段值不缺：parse_intent 的规则
            # 交叉校正（_apply_provenance_correction）会在 LLM 自报缺失时
            # 按 schema 默认值兜底补全，field_provenance 依然会被正确写入，
            # 不需要 stub 显式给出这个键。
        }
        return LLMChatResponse(
            content=json.dumps(sample_intent, ensure_ascii=False),
            finish_reason="stop",
        )

    def stream_chat(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        del extra_body  # 同 chat()：stub 接住不用
        text = "已为你规划好下午行程：森林儿童探索乐园 → 轻语沙拉..."
        for ch in text:
            yield ch

    def chat_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.3,
        tool_choice: Literal["auto", "required", "none"] = "auto",
    ) -> LLMChatResponse:
        # P2 阶段 A 同学接入真模型后再细化；当前返一个空 tool_calls 的 stop
        return LLMChatResponse(
            content="（stub）模拟 Agent 决策完成。",
            finish_reason="stop",
        )


def _last_user(messages: list[LLMMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user" and m.content:
            return m.content
    return ""
