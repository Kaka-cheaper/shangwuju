"""collab —— 多人实时协作模块。

提供：
- Room / RoomManager：房间生命周期管理
- WebSocket Hub：连接管理 + 广播
- 规划中断桥接：新约束到达 → cancel 当前规划 → 合并约束 → 重新规划

不负责：
- 规划算法（复用 agent/planner 或 agent/graph）
- Tool 实现（在 tools/）
- HTTP 端点注册（在 main.py）
"""

from .room import Room, RoomManager, get_room_manager

__all__ = ["Room", "RoomManager", "get_room_manager"]
