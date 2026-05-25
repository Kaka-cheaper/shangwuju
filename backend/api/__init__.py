"""backend.api —— FastAPI router 子模块集合（main.py 拆分后的去 god-module 化）。

本目录每个文件对应一组业务相关端点（按评委可见性 + 业务域分组）：

- `scenarios.py`     演示场景集（GET /scenarios + 8 个青年/家庭向场景常量）
- `health.py`        liveness / readiness 探针（GET /health, /ready）
- `amap.py`          高德 JS API 安全代理（/_AMapService/*）
- `preferences.py`   persona / 偏好读取重置（/personas, /preferences/*）
- `legal.py`         用户协议、隐私政策（/legal/*）
- `oauth.py`         OAuth 接入位（/auth/*）
- `collab.py`        多人协作房间（/room/*, WS /ws/{room_id}）
- `chat.py`          对话核心 4 端点（/chat/stream, /chat/confirm, /chat/refine, /chat/turn）
- `_session_store.py` _SESSION_STORE 内存级会话快照 + user_id 解析 helper
- `_sse_helpers.py`   SSE 包装 + 兜底（_to_sse, _safe_stream, _delay, _now_ms）
- `_stub_streams.py`  纯本地 stub fixture（_stub_stream, _stub_confirm, _stub_refine, _stub_route）
- `_planner_streams.py` 真 planner 链路（_planner_stream, _refine_stream_real, _routed_stream_real）

main.py 通过 `app.include_router(...)` 接入；任何端点逻辑改动应在本目录文件内做，
**不要往 main.py 加新端点**——main.py 仅保留 app 实例化、middleware、include_router。
"""
