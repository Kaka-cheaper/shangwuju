"""backend.api —— FastAPI router 子模块集合（main.py 拆分后的去 god-module 化）。

本目录每个文件对应一组业务相关端点（按评委可见性 + 业务域分组）：

- `scenarios.py`     演示场景集（GET /scenarios + 8 个青年/家庭向场景常量）
- `health.py`        liveness / readiness 探针（GET /health, /ready）
- `amap.py`          高德 JS API 安全代理（/_AMapService/*）
- `preferences.py`   persona / 偏好读取重置（/personas, /preferences/*）
- `legal.py`         用户协议、隐私政策（/legal/*）
- `oauth.py`         OAuth 接入位（/auth/*）
- `collab.py`        多人协作房间（/room/*, WS /ws/{room_id}）
- `chat.py`          对话核心 2 端点（/chat/turn, /chat/confirm）——V1 legacy
  /chat/stream 与 /chat/refine 已退役删除，反馈流并入 /chat/turn 统一路由
- `adjust.py`        单人节点调整（POST /chat/adjust，ADR-0013 F-4）
- `_session_store.py` SESSION_STORE 内存级会话快照 + user_id 解析 helper
- `_sse_helpers.py`   SSE 包装 + 兜底（safe_stream, now_ms 等）
- `_streams/`         SSE 流实现（graph_confirm / graph_adjust / memory +
  Request 模型 models.py），V1 的 stub/planner 流已随退役删除

main.py 通过 `app.include_router(...)` 接入；任何端点逻辑改动应在本目录文件内做，
**不要往 main.py 加新端点**——main.py 仅保留 app 实例化、middleware、include_router。
"""
