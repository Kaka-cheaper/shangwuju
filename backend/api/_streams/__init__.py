"""api._streams —— Chat 端点共用的 SSE 流式实现（spec code-modularization-refactor H1-final）。

V1 legacy turn/refine 路径（stub_stream / stub_refine / planner_stream / refine_real /
route / intent_rules）已随 /chat/stream + /chat/refine 退役删除；本 package 现仅容纳
confirm 流与共享小件：

- `models.py`          ChatStreamRequest / ChatConfirmRequest 两个 Request 模型
- `memory.py`          confirm 侧 memory 累积 helper（_collect_itinerary_tags + _accumulate_memory_after_confirm）
- `stub_confirm.py`    _stub_confirm（confirm 流 demo fixture）
- `graph_confirm.py`   _graph_confirm（confirm 流真实 LangGraph finalize）

main.py 通过 api/chat.py 接 router；chat.py 仅定义 2 个端点 + 调本 package 的函数。
"""
