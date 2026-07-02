"""api._streams —— Chat 端点共用的 SSE 流式实现（spec code-modularization-refactor H1-final）。

V1 legacy turn/refine 路径（stub_stream / stub_refine / planner_stream / refine_real /
route / intent_rules）已随 /chat/stream + /chat/refine 退役删除；`stub_confirm.py`
（confirm 流专用 demo fixture）已随 ADR-0012 决策 5（E-0-c）删除——协作房间也统一
切到 `_graph_confirm`，`USE_LANGGRAPH` 开关连带退役。本 package 现仅容纳唯一一条
confirm 流与共享小件：

- `models.py`          ChatStreamRequest / ChatConfirmRequest 两个 Request 模型
- `memory.py`          confirm 侧 memory 累积 helper（_collect_itinerary_tags + _accumulate_memory_after_confirm）
- `graph_confirm.py`   _graph_confirm（唯一 confirm 流：真实 LangGraph finalize + 两种记忆副作用并列）

main.py 通过 api/chat.py 接 router；chat.py 仅定义 2 个端点 + 调本 package 的函数。
"""
