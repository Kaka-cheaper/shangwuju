"""agent.context.sources —— `SessionContextSource` 的两个底座实现
（ADR-0011 2026-07-03"底座无关"增补）。

`GraphStateSource` 吃单人主聊天路径的 `AgentState`（dict-like）；`RoomSource`
吃协作房间的 `collab.room.Room`（dataclass）。两者把各自原生的历史/台账/
画像形状翻译成 `SessionContextSource` 协议的统一方法签名，`pack_routing_
context` 之后完全不知道、也不需要知道调用方来自哪个底座——这正是"一个打包器
多个来源"的落地点（ADR-0011 决策 3 底座无关增补）。

两个类都是只读实现：不修改传入的 state/room，只读取字段——打包器全程是纯
函数链路的一环。`RoomSource` 是本次任务新增的文件，只读 `collab.room.Room`
的既有字段，不改 `room.py` 本身（并行纪律：该文件是并行批次的地盘）。

不负责：
- 谁在何时调用这两个类去接线（单人路径接线归 E-2-c 的路由脑子；房间侧接线
  调用点在 E-2-c 或 F 后续——本模块只交付实现 + 单测）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

from .types import TurnLogEntry

if TYPE_CHECKING:  # 仅供类型检查，不产生运行时 import（避免任何潜在循环 import 风险）
    from langchain_core.messages import BaseMessage

    from agent.graph.state import AgentState
    from collab.room import Room


_MESSAGE_ROLE_MAP: dict[str, str] = {"human": "user", "ai": "agent"}
"""LangChain `BaseMessage.type` → 打包器统一角色词汇。未登记的类型（如
"system" —— 当前 `messages` 通道从不写入，防御性保留）原样透传，不强行
归类（见 `agent/graph/nodes/router.py`/`narrate.py`：目前只写 HumanMessage
与 AIMessage 两种）。"""


def _normalize_message(msg: "BaseMessage") -> TurnLogEntry:
    msg_type = getattr(msg, "type", "") or ""
    role = _MESSAGE_ROLE_MAP.get(msg_type, msg_type or "unknown")
    return TurnLogEntry(role=role, text=str(getattr(msg, "content", "") or ""))


@dataclass(frozen=True)
class GraphStateSource:
    """`SessionContextSource` 的图状态实现 —— 单人主聊天路径。

    吃 `AgentState`（`TypedDict`，运行时就是普通 dict）；只用 `.get()` 读取，
    不假设字段必然存在（`EPISODE_SCOPED` 字段在规划事件边界之间可能是
    None/缺失，见 `agent/graph/state.py` 字段生命周期表）。
    """

    state: "AgentState"

    def turn_log(self) -> Sequence[tuple[str, str]]:
        messages = self.state.get("messages") or []
        return [_normalize_message(m) for m in messages]

    def plan_version_log(self) -> Sequence[Mapping[str, Any]]:
        return list(self.state.get("plan_version_log") or [])

    def current_itinerary_dict(self) -> Optional[Mapping[str, Any]]:
        itinerary = self.state.get("itinerary")
        if itinerary is None:
            return None
        return itinerary.model_dump() if hasattr(itinerary, "model_dump") else itinerary

    def scenario_id(self) -> Optional[str]:
        return self.state.get("scenario_id")

    def profile_fields(self) -> Optional[Mapping[str, Any]]:
        """从 `state.user_profile`（`GetUserProfileOutput`）取出 `UserProfile`
        字段。

        取值路径与 `agent/graph/nodes/assemble.py::_resolve_user_profile`
        同源（`.profile` 属性 / 测试场景直塞 `UserProfile` 两种形态兼容），
        但**不**像它那样在缺失时回落 `data.loader.load_user_profile()` 默认
        画像——打包器是纯函数、不做文件 I/O，缺失就诚实返回 None，由消费方
        （`render_text`/prompt）自行决定"没有画像数据"时怎么措辞，不偷偷塞
        一份看起来像真实数据的默认值进路由脑子的判断材料里。
        """
        raw = self.state.get("user_profile")
        if raw is None:
            return None
        profile = getattr(raw, "profile", None)
        if profile is None and hasattr(raw, "home_location"):
            profile = raw  # 测试场景可能直接塞 UserProfile 本身
        if profile is None:
            return None
        return profile.model_dump() if hasattr(profile, "model_dump") else profile

    def user_decision(self) -> Optional[str]:
        return self.state.get("user_decision")

    def demand_ledger_raw(self) -> Sequence[Mapping[str, Any]]:
        return list(self.state.get("demand_ledger") or [])

    def pending_clarification(self) -> Optional[Any]:
        """占位透传（ADR-0011 决策 3）——`pending_clarification` 状态字段的
        生产者是 E-3，尚未落地进 `AgentState`；`.get()` 在字段出生前恒返回
        None、出生后自动读到真值，协议方法签名本身不必再改一次。"""
        return self.state.get("pending_clarification")


@dataclass(frozen=True)
class RoomSource:
    """`SessionContextSource` 的协作房间实现（ADR-0011 决策 3 底座无关增补；
    与 ADR-0013 联动）。

    新文件实现——只读 `collab.room.Room` 的既有字段，不修改 `room.py`。字段
    名对照：对话史 = `chat_messages`，台账 = `demand_ledger`，方案 =
    `current_itinerary_dict`（均已读码核实，见类内各方法注释）。

    已知的房间侧空缺（诚实降级，不是遗漏——ADR-0011"一个打包器多个来源"
    本就允许不同来源材料丰富度不同，只要形状一致）：
    - `plan_version_log`：房间目前不追踪版本志（后续 wiring 待补）。
    - `scenario_id`/`profile_fields`：房间是多人协作，没有单一"这是谁的
      画像"概念（ADR-0013 决策 6 边界：持久成员画像不在本弧范围）。
    - `pending_clarification`：房间版按成员分身，E-3 设计时才落地形状
      （ADR-0011 决策 3 原文）。
    """

    room: "Room"

    def turn_log(self) -> Sequence[tuple[str, str]]:
        """`Room.chat_messages` 形状：`{"id", "role" ("user"/"agent"),
        "text", "createdAt"}`（见 `collab/room.py` 的 `chat_messages.append`
        调用点）——role 已经就是 "user"/"agent"，只做防御性归一（非 "agent"
        一律视为 "user"）。"""
        out: list[tuple[str, str]] = []
        for m in self.room.chat_messages:
            role = "agent" if m.get("role") == "agent" else "user"
            out.append((role, str(m.get("text") or "")))
        return out

    def plan_version_log(self) -> Sequence[Mapping[str, Any]]:
        return []

    def current_itinerary_dict(self) -> Optional[Mapping[str, Any]]:
        return self.room.current_itinerary_dict

    def scenario_id(self) -> Optional[str]:
        return None

    def profile_fields(self) -> Optional[Mapping[str, Any]]:
        return None

    def user_decision(self) -> Optional[str]:
        """`Room.confirmed`（布尔）→ 三态语义的诚实降级：True → "confirm"；
        False 时房间没有 "refine"/"cancel" 两态的独立追踪，返回 None（未
        确认，不等于"确认拒绝"——两者语义不同，房间目前只能表达"确认过"或
        "没有这回事"）。"""
        return "confirm" if self.room.confirmed else None

    def demand_ledger_raw(self) -> Sequence[Mapping[str, Any]]:
        return list(self.room.demand_ledger or [])

    def pending_clarification(self) -> Optional[Any]:
        return None


__all__ = ["GraphStateSource", "RoomSource"]
