"""planner_mode —— 双范式 planner 切换契约。

两种规划范式：
- "rule"  ：规则化 ReAct（默认；MVP-1/2 主路径，Demo 安全网）
- "llm"   ：LLM Function Calling 自主决策（评分项 2 加分点）

切换通道（优先级从高到低）：
1. HTTP 请求 header `X-Planner-Mode: rule|llm`（前端切换器写）
2. 环境变量 `PLANNER_MODE=rule|llm`（后端启动时读）
3. 默认值 `rule`（缺省）

不负责：
- 具体规划逻辑（rule 在 planner.py / llm 在 llm_planner.py，均 A 块）
- 入口分发（在 planner.py 的 plan_itinerary_with_mode，A 块）
- HTTP 解析（在 main.py，B 块）
"""

from __future__ import annotations

import os
from typing import Literal


PlannerMode = Literal["rule", "llm"]

DEFAULT_MODE: PlannerMode = "rule"

_VALID: tuple[PlannerMode, ...] = ("rule", "llm")


def normalize_mode(value: str | None) -> PlannerMode:
    """把任意字符串归一为合法 PlannerMode；非法值回 DEFAULT_MODE。"""
    if not value:
        return DEFAULT_MODE
    v = value.strip().lower()
    if v in _VALID:
        return v  # type: ignore[return-value]
    return DEFAULT_MODE


def resolve_planner_mode(
    *,
    header_value: str | None = None,
    env_value: str | None = None,
) -> PlannerMode:
    """按优先级解析 mode：header > env > default。

    HTTP 端点（B）调用：
        mode = resolve_planner_mode(
            header_value=request.headers.get("X-Planner-Mode"),
            env_value=os.getenv("PLANNER_MODE"),
        )
    """
    for raw in (header_value, env_value):
        if raw is None or raw == "":
            continue
        normalized = normalize_mode(raw)
        # 仅当 header / env 显式提供合法值才返回；非法时跳过
        if normalize_mode(raw) != DEFAULT_MODE or raw.strip().lower() == DEFAULT_MODE:
            return normalized
    return DEFAULT_MODE


def current_env_mode() -> PlannerMode:
    """读 PLANNER_MODE 环境变量；缺省回 default。"""
    return normalize_mode(os.getenv("PLANNER_MODE"))
