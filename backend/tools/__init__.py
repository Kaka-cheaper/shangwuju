"""tools —— 7 个 Tool 的实现 + Function Calling 注册表。

每个 Tool 一个文件，模块顶部 docstring 必须说明：
- 这个 Tool 做什么（一句话）
- 输入输出 schema 引用（schemas.tools.XxxInput / XxxOutput）
- 失败分支与对应的 FailureReason
- 是否埋了失败案例（埋哪几条 mock id）

注册表 TOOL_REGISTRY 是 LLM Function Calling 的唯一入口——Agent 编排层
通过它拿到「所有 Tool 的 OpenAI 兼容 spec + 调用函数」，不直接 import 单个 Tool。

不负责：
- LLM 调用（在 agent/llm_client.py）
- Mock 数据加载（在 data/loader.py）
- 规划决策（不允许 Tool 互相调用）

注册副作用：
- 导入本模块即把 7 个 Tool 注册进 TOOL_REGISTRY；
- 顺序无关，但保留下面 import 顺序便于人工 grep。
"""

from .registry import TOOL_REGISTRY, ToolSpec, invoke_tool, register_tool, all_specs

# 触发副作用：每个 Tool 模块顶部 @register_tool 把自己加进 TOOL_REGISTRY
from . import (  # noqa: F401  ── side-effect import
    search_pois,
    search_restaurants,
    check_restaurant_availability,
    estimate_route_time,
    reserve_restaurant,
    buy_ticket,
    generate_share_message,
    get_user_profile,
)

__all__ = [
    "TOOL_REGISTRY",
    "ToolSpec",
    "invoke_tool",
    "register_tool",
    "all_specs",
]
