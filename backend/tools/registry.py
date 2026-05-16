"""tools.registry —— Tool 注册表与 OpenAI Function Calling spec 自动生成。

职责：
- 提供 `@register_tool(name, input_model, output_model)` 装饰器，把 Tool 函数注册到 `TOOL_REGISTRY`。
- 自动从 Pydantic v2 模型生成 OpenAI Function Calling 兼容的 spec
  （`{"type":"function","function":{"name":..., "description":..., "parameters": <json schema>}}`）
- 提供 `invoke_tool(name, raw_args)` 给 Agent 调用——**Agent 不直接 import 单个 Tool**。

设计纪律（AGENTS.md §3.4 / §4.1）：
- Tool 函数签名统一为 `(input: XxxInput) -> XxxOutput`
- Tool 函数**不能**互相 import（一个 Tool 不调另一个 Tool）
- Tool 失败用 `success=false + reason: FailureReason`，不抛业务异常给上层
- Tool 描述（description）必须中文 + 写明失败分支，给 LLM 看

接口契约对外固化（A 同学 owner，但 W1/W2 都依赖）：
1. ToolSpec 数据结构
2. TOOL_REGISTRY 全局变量名
3. invoke_tool 函数签名
4. 自动 spec 生成的 JSON 形态（与 OpenAI Function Calling 保持一致）
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Type, TypeVar

from pydantic import BaseModel, ValidationError

from schemas.errors import FailureReason


TIn = TypeVar("TIn", bound=BaseModel)
TOut = TypeVar("TOut", bound=BaseModel)


@dataclass
class ToolSpec(Generic[TIn, TOut]):
    """单个 Tool 的注册项。"""

    name: str
    description: str
    input_model: Type[TIn]
    output_model: Type[TOut]
    func: Callable[[TIn], TOut]

    def to_openai_spec(self) -> dict[str, Any]:
        """生成 OpenAI Function Calling 兼容 spec。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


# 全局注册表：name -> ToolSpec
TOOL_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(
    name: str,
    description: str,
    input_model: Type[TIn],
    output_model: Type[TOut],
):
    """装饰器：把 Tool 函数注册到 TOOL_REGISTRY。

    用法：
        @register_tool(
            name="search_pois",
            description="按距离/标签查询活动地点候选；命中 0 条返回 empty_candidates",
            input_model=SearchPoisInput,
            output_model=SearchPoisOutput,
        )
        def search_pois(input: SearchPoisInput) -> SearchPoisOutput:
            ...
    """

    def decorator(func: Callable[[TIn], TOut]) -> Callable[[TIn], TOut]:
        if name in TOOL_REGISTRY:
            raise ValueError(f"Tool 重复注册: {name}")
        TOOL_REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            input_model=input_model,
            output_model=output_model,
            func=func,
        )
        return func

    return decorator


@dataclass
class ToolInvocationResult:
    """Agent 调用 Tool 的结果包装。

    无论成功失败都不抛异常，由 Agent 层判断后续路径。
    duration_ms 给 SSE TOOL_CALL_END 事件用。
    """

    tool: str
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    reason: FailureReason | None = None
    error_detail: str | None = None
    duration_ms: int = 0


def invoke_tool(name: str, raw_args: dict[str, Any]) -> ToolInvocationResult:
    """Agent 调用 Tool 的统一入口。

    职责：
    - 按注册表找 ToolSpec
    - 用 input_model 校验 raw_args（LLM 可能漂移字段——校验失败回 INVALID_INPUT）
    - 调用 Tool 函数
    - 用 output_model 校验输出（防 Tool 实现漂移）
    - 计时

    LLM 漂移字段时 Agent 应捕获 INVALID_INPUT 并把错误回灌给 LLM 重试
    （对应 pitfalls P2-预埋 LLM Function Calling 参数 hallucination）。
    """
    started = time.perf_counter()

    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return ToolInvocationResult(
            tool=name,
            success=False,
            reason=FailureReason.NOT_FOUND,
            error_detail=f"Tool 未注册: {name}",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    try:
        parsed = spec.input_model.model_validate(raw_args)
    except ValidationError as e:
        return ToolInvocationResult(
            tool=name,
            success=False,
            reason=FailureReason.INVALID_INPUT,
            error_detail=str(e),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    try:
        out = spec.func(parsed)
    except Exception as e:  # noqa: BLE001
        return ToolInvocationResult(
            tool=name,
            success=False,
            reason=FailureReason.UPSTREAM_FAILURE,
            error_detail=f"{type(e).__name__}: {e}",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    # 二次校验：Tool 实现可能返非法字段
    try:
        validated = spec.output_model.model_validate(out.model_dump())
    except ValidationError as e:
        return ToolInvocationResult(
            tool=name,
            success=False,
            reason=FailureReason.UPSTREAM_FAILURE,
            error_detail=f"Tool 输出未过 schema: {e}",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    return ToolInvocationResult(
        tool=name,
        success=validated.success,
        output=validated.model_dump(),
        reason=validated.reason,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


def all_specs() -> list[dict[str, Any]]:
    """给 Agent 拿全部 Tool 的 OpenAI spec 用。"""
    return [s.to_openai_spec() for s in TOOL_REGISTRY.values()]
