"""agent.tools —— LangGraph 工具适配层。

把 backend/tools/ 的 8 工具包装成「直接接受 IntentExtraction / 用户态」的 helper，
让 graph/nodes/* 不需要重复构造 SearchPoisInput / SearchRestaurantsInput。

不负责：
- Tool 实际逻辑（在 backend/tools/）
- LangChain @tool 装饰器（execute_finalize 节点直接用 invoke_tool）
"""

from .search_adapter import (
    get_user_profile_for_user,
    search_pois_for_intent,
    search_restaurants_for_intent,
)

__all__ = [
    "search_pois_for_intent",
    "search_restaurants_for_intent",
    "get_user_profile_for_user",
]
