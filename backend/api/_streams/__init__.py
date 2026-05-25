"""api._streams —— Chat 端点共用的 SSE 流式实现（spec code-modularization-refactor H1-final）。

main.py 不再有 stream / refine / planner / confirm 内部实现；本 package 容纳：

- `models.py`          ChatStreamRequest / ChatConfirmRequest 两个 Request 模型
- `memory.py`          3 个 memory 累积 helper（confirm/refine 路径调用）
- `stub_stream.py`     _stub_stream（demo 主路径 fixture）
- `stub_confirm.py`    _stub_confirm（confirm 流 demo fixture）
- `stub_refine.py`     _stub_refine + _refine_stream + _extract_distance_km
- `planner_stream.py`  _planner_stream + _intent_via_llm + _record_to_sse + tracer 转换
- `refine_real.py`     _refine_stream_real（真 planner 路径下的 refine）
- `route.py`           _stub_route + _make_chitchat_event + _routed_stream_*

main.py 通过 api/chat.py 接 router；chat.py 仅定义 4 个端点 + 调本 package 的函数。
"""
