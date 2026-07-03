"""agent.context.types —— 会话上下文打包器(RoutingContext)的类型定义(ADR-0011 决策 3)。

【这是什么】
"会话上下文打包器"是路由节点的一等组件——每轮一次、确定性地把散落在
state/Room 里的会话材料（轮次日志/方案版本志/当前方案/画像/待澄清/台账）
拼成一份结构化的 `RoutingContext`，供路由脑子（E-2-c）/refiner/narration
三方共同消费，杜绝"各节点自己拼上下文，三份拼法三种漂移"（ADR-0011 决策 3
原文）。本模块只定义类型（值对象 + 协议），打包算法在 `packer.py`。

【底座无关（ADR-0011 2026-07-03 增补）】
打包器吃抽象的"会话上下文来源"协议 `SessionContextSource`——单人主聊天与
协作房间各自实现一份（`agent.context.sources.GraphStateSource` / `RoomSource`），
`pack_routing_context`（packer.py）本身对底座一无所知，一个打包器多个来源，
不为房间另建第二个打包器。

【与 agent/graph/_emit_context.py::EmitContext 的区别，避免望文生义】
两者名字都带"context"，但服务完全不同的关切——`EmitContext` 是 SSE 事件流
"这次调用推过哪些事件"的记账器（emit 层内部状态，字段级/事件级）；本模块的
`RoutingContext` 是"这个会话到目前为止发生了什么"的结构化只读快照（路由/
refiner/narration 的输入材料，会话级）。两者不共享任何字段，也不应被合并。

不负责：
- LLM 调用（纯函数打包，见 packer.py）。
- pending_clarification 的生产（占位透传，生产者是 E-3，见字段注释）。
- 房间侧实际接线调用点（读 Room 的调用发生在 E-2-c / F 后续，见 sources.py）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, NamedTuple, Optional, Protocol, Sequence, runtime_checkable

from schemas.node_adjustment import NodeAdjustment


# ============================================================
# 值对象
# ============================================================


class TurnLogEntry(NamedTuple):
    """会话轮次日志的一条 —— (角色, 文本) 二元组（ADR-0011 决策 3 原文）。

    role 统一为 "user" / "agent"：两个来源各自把自己的原生角色词汇
    （LangChain `BaseMessage.type` 的 "human"/"ai"，或 `Room.chat_messages`
    的 "user"/"agent"）翻译成这一份统一词汇——打包器与 `render_text` 不关心
    来源的原生命名，这正是"底座无关"的落地方式之一（翻译发生在 sources.py，
    不发生在这里）。写入时已消毒（见 `agent/graph/nodes/router.py` 的消毒
    纪律注释），打包层不二次处理。
    """

    role: str
    text: str


class PlanSummaryLine(NamedTuple):
    """当前方案摘要的一行 —— kind（节点性质中文标签）+ name（标题）+ time（时刻）。

    从 `Itinerary`（或其 dict 形式）的 `nodes` 现算（每次 pack 都重新算，不
    缓存），跳过首尾 home 虚拟节点（与 `agent.intent.narrator._node_to_phrase`
    同一纪律）。
    """

    kind: str
    name: str
    time: str


@dataclass(frozen=True)
class ProfileSnapshot:
    """画像要素快照 —— scenario_id + `UserProfile` 挑出的 3 个关键字段。

    字段挑选的拍板理由见 `packer.py` 的 `_PROFILE_KEY_FIELDS` 注释（排除
    home_location / recent_trips / social_context_history）。
    """

    scenario_id: Optional[str] = None
    dietary_preference: Optional[str] = None
    transport_preference: Optional[str] = None
    default_budget: Optional[float] = None

    def is_empty(self) -> bool:
        return not any(
            (
                self.scenario_id,
                self.dietary_preference,
                self.transport_preference,
                self.default_budget,
            )
        )


@dataclass(frozen=True)
class RoutingContext:
    """会话上下文打包器的产出 —— 路由脑子（E-2-c）/refiner/narration 三方共同
    消费的结构化快照（ADR-0011 决策 3"六件套"）。

    字段与 ADR 原文六件套的对照：
    1. `turn_log`（+ `dropped_turn_count` 元数据）—— 消毒轮次日志
    2. `plan_version_log` —— 方案版本志（全量，钉锚，永不裁剪）
    3. `plan_summary` —— 当前方案摘要（每节点一行）
    4. `profile` —— 画像要素
    5. `pending_clarification` —— 占位透传，生产者是 E-3，现恒 None
    6. `user_decision` + `ledger_active_global` + `ledger_active_named` ——
       待确认态 + 台账生效切片（前者是给程序消费的 `NodeAdjustment` 列表，
       只含全局诉求——即 `node_ref is None` 的生效条目；后者是
       `ledger_for_display` 投影、含节点归属与记名，给人话文案/prompt 用）

    不可变（`frozen=True`）——打包器是纯函数链路的一环，产出不应被消费方
    就地篡改（同 `schemas.demand_ledger` 系列函数"返回全新列表"的纪律）。
    """

    turn_log: tuple[TurnLogEntry, ...]
    dropped_turn_count: int
    plan_version_log: tuple[Mapping[str, Any], ...]
    plan_summary: tuple[PlanSummaryLine, ...]
    profile: ProfileSnapshot
    pending_clarification: Optional[Any]
    user_decision: Optional[str]
    ledger_active_global: tuple[NodeAdjustment, ...]
    ledger_active_named: tuple[Mapping[str, Any], ...]

    def render_text(self) -> str:
        """确定性纯文本序列化 —— 路由脑子（E-2-c）prompt 的输入材料。

        纯函数（不含任何时间戳/随机性 —— `created_at` 等字段照原样打印，不用
        `now()` 重新盖时间戳）。段落固定顺序、固定标题；空数据的段落打印
        "（无/暂无……）" 占位而不是整段消失 —— 消费方（LLM prompt）靠固定
        结构定位信息，段落忽隐忽现会增加解析难度（同 `narrator_prompt.py`
        `extras` 拼接先例的反面教训：那里空则不出现是因为那是"触发型"指令，
        这里是"结构性"快照，两种场景不同一律套用同一规则）。
        """
        lines: list[str] = []

        first_turn = self.turn_log[0] if self.turn_log else None
        lines.append("【首轮原始需求】")
        lines.append(first_turn.text if first_turn else "（无）")
        lines.append("")

        turn_count = len(self.turn_log)
        trim_note = (
            f"，已丢弃 {self.dropped_turn_count} 轮更老的对话"
            if self.dropped_turn_count
            else ""
        )
        lines.append(f"【会话轮次】(共 {turn_count} 轮{trim_note})")
        if self.turn_log:
            lines.extend(f"{entry.role}: {entry.text}" for entry in self.turn_log)
        else:
            lines.append("（无）")
        lines.append("")

        lines.append(f"【方案版本志】(共 {len(self.plan_version_log)} 版)")
        if self.plan_version_log:
            lines.extend(f"- {v.get('summary', '')}" for v in self.plan_version_log)
        else:
            lines.append("（暂无方案版本）")
        lines.append("")

        lines.append("【当前方案摘要】")
        if self.plan_summary:
            for line in self.plan_summary:
                time_part = f"（{line.time}）" if line.time else ""
                lines.append(f"- {line.kind}·{line.name}{time_part}")
        else:
            lines.append("（暂无方案）")
        lines.append("")

        lines.append("【画像】")
        lines.append(self._render_profile())
        lines.append("")

        lines.append("【待澄清】")
        lines.append(str(self.pending_clarification) if self.pending_clarification else "（无）")
        lines.append("")

        lines.append("【待确认态】")
        lines.append(_USER_DECISION_LABEL.get(self.user_decision, "（未决定）"))
        lines.append("")

        lines.append(f"【台账生效条目】(共 {len(self.ledger_active_named)} 条)")
        if self.ledger_active_named:
            lines.extend(f"- {_format_ledger_entry(entry)}" for entry in self.ledger_active_named)
        else:
            lines.append("（暂无生效诉求）")

        return "\n".join(lines)

    def _render_profile(self) -> str:
        if self.profile.is_empty():
            return "（无画像数据）"
        parts: list[str] = []
        if self.profile.scenario_id:
            parts.append(f"场景={self.profile.scenario_id}")
        if self.profile.dietary_preference:
            parts.append(f"饮食偏好：{self.profile.dietary_preference}")
        if self.profile.transport_preference:
            parts.append(f"出行偏好：{self.profile.transport_preference}")
        if self.profile.default_budget:
            parts.append(f"默认预算：{self.profile.default_budget:g} 元")
        return "；".join(parts)


_USER_DECISION_LABEL: dict[Optional[str], str] = {
    "confirm": "已确认下单",
    "refine": "已选择继续调整",
    "cancel": "已取消",
}


_DIMENSION_VALUE_PHRASES: dict[tuple[str, str], str] = {
    ("price", "cheaper"): "更便宜",
    ("price", "pricier"): "更高档",
    ("distance", "closer"): "更近",
    ("distance", "farther"): "更远",
}
"""方向词维度（PRICE/DISTANCE）的人话短语——与 `agent.intent.narrator` 模板
chips 的按钮文案（"更便宜的"/"更近的"）同一措辞族，不发明第三套词。"""

_DIMENSION_LABEL: dict[str, str] = {
    "ambience": "氛围调整为",
    "dietary": "饮食要求",
    "crowd_fit": "人群适配",
    "cuisine_or_type": "换成",
}
"""目标值维度的前缀标签（value 本身已是中文词典词/自由文本，直接拼）。"""


def ledger_value_clause(entry: Mapping[str, Any]) -> str:
    """诉求条目的"调哪个维度、调成什么样 + 原话引用"人话短句（不含记名/节点
    归属）。

    公用 helper —— `render_text` 的 `_format_ledger_entry`（本模块内）与
    `packer.render_demand_recap`（refiner 切片，见该函数 docstring）共用同一
    份措辞，避免两处各写一套文案而后续漂移。人话纪律（任务书原文）：方向词
    （cheaper/closer）翻成中文短语——与 `agent.intent.narrator` 模板 chips
    的按钮文案同一措辞族；目标值维度用中文前缀 + 原值（value 本身已是词典
    中文词）；词典外的未知组合退化为 "dimension→value" 原样打印（不崩、
    不瞎猜——未来枚举扩员时这里最多退化成半人话，不会漏条目）。
    """
    dimension = str(entry.get("dimension", "?"))
    value = str(entry.get("value", "?"))
    phrase = _DIMENSION_VALUE_PHRASES.get((dimension, value))
    if phrase is None:
        label = _DIMENSION_LABEL.get(dimension)
        phrase = f"{label}「{value}」" if label else f"{dimension}→{value}"
    source_text = entry.get("source_text") or ""
    quote = f"（源：『{source_text}』）" if source_text else ""
    return f"{phrase}{quote}"


def _format_ledger_entry(entry: Mapping[str, Any]) -> str:
    who = entry.get("nickname") or entry.get("member_id") or "匿名"
    node_ref = entry.get("node_ref")
    where = "全局" if node_ref is None else f"节点 {node_ref.get('target_id', '?')}"
    return f"[{who}] {where} · {ledger_value_clause(entry)}"


# ============================================================
# 来源协议（底座无关铁律，ADR-0011 2026-07-03 增补）
# ============================================================


@runtime_checkable
class SessionContextSource(Protocol):
    """会话上下文来源协议 —— `pack_routing_context` 唯一认识的输入接口。

    两个当前实现见 `agent.context.sources`（`GraphStateSource` / `RoomSource`）；
    未来若有第三个底座，只需再实现一份本协议，`pack_routing_context` /
    `RoutingContext` / `render_text` 都不必改一行 —— 这正是 ADR-0011"一个
    打包器多个来源"的落地点。

    结构化子类型（`@runtime_checkable` + `Protocol`，与
    `agent.core.llm_client.LLMClient` 同一先例）—— 只要具备同名方法就自动
    满足协议，不需要显式继承。
    """

    def turn_log(self) -> Sequence[tuple[str, str]]:
        """全量会话轮次，按发生顺序，(role, text) 二元组；role 统一为
        "user"/"agent"。实现方负责把自己原生的历史形状翻译成这个统一形状
        （写入时已消毒，本协议的实现不做二次消毒）。"""
        ...

    def plan_version_log(self) -> Sequence[Mapping[str, Any]]:
        """方案版本志全量，条目形状 {version_n, summary, trigger, timestamp}
        （见 `agent/graph/nodes/finalize_plan.py::_version_log_entry`）。永不
        裁剪（打包器钉锚集之一），没有就返回空列表。"""
        ...

    def current_itinerary_dict(self) -> Optional[Mapping[str, Any]]:
        """当前方案（`Itinerary` 的 dict 形式，`model_dump()` 或原生已是
        dict）；没有方案时返回 None。"""
        ...

    def scenario_id(self) -> Optional[str]:
        """画像素材之一：当前场景 id。没有对应概念时返回 None。"""
        ...

    def profile_fields(self) -> Optional[Mapping[str, Any]]:
        """画像素材之二：`UserProfile` 的 dict 形式（打包器只挑
        `dietary_preference`/`transport_preference`/`default_budget` 三项，
        见 `packer.py` 的 `_PROFILE_KEY_FIELDS` 拍板注释）。没有画像数据时
        返回 None。"""
        ...

    def user_decision(self) -> Optional[str]:
        """待确认态："confirm"/"refine"/"cancel" 之一，或 None（未决定/无此
        概念）。"""
        ...

    def demand_ledger_raw(self) -> Sequence[Mapping[str, Any]]:
        """诉求台账全量（`schemas.demand_ledger.LedgerEntry.model_dump()`
        形状的 dict 列表，含各状态——ACTIVE/SUPERSEDED/SATISFIED 都要，打包器
        自己按状态过滤，不要求来源预过滤）。"""
        ...

    def pending_clarification(self) -> Optional[Any]:
        """占位透传（ADR-0011 决策 3）：生产者是 E-3，当前两个实现恒返回
        None。"""
        ...


__all__ = [
    "TurnLogEntry",
    "PlanSummaryLine",
    "ProfileSnapshot",
    "RoutingContext",
    "SessionContextSource",
    "ledger_value_clause",
]
