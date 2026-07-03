"""agent.context.packer —— 会话上下文打包器主函数（ADR-0011 决策 3）。

`pack_routing_context` 是本 ADR 决策 3 的核心交付：每轮一次、确定性地把
`SessionContextSource` 提供的原始材料整理成 `RoutingContext`。纯函数——不碰
LLM、不做任何 I/O、不含随机性/"当前时间"，给定同一份来源材料必然产出同样
的 `RoutingContext`（可单测的核心承诺）。

【裁剪算法（保险丝，ADR 原文"约最近 40 轮/8K token，溢出丢最老闲聊轮，
钉锚永不丢"）】
钉锚集 = 首轮原始需求（`turn_log` 的第 0 条）+ 方案版本志全量 +
`pending_clarification` + 台账生效切片 + 当前方案摘要 + 画像——这些材料要么
体量天然很小（单条摘要/画像几十字节），要么被 ADR 原文显式点名"永不丢"，
一律不参与裁剪。真正的裁剪目标只有"首轮之后的轮次日志"——它是随会话时长
唯一无界增长的部分，也是 ADR 决策 3 唯一提到"溢出丢最老"的对象。裁剪先套用
轮数上限（近 40 轮内，含首轮），再套用 token 预算（8K，扣掉钉锚材料已占用的
份额），两者都从"最老的非首轮轮次"开始丢，首轮本身永不参与、永远保留。

【token 估算系数怎么选的（报告要求的拍板点）】
调研 tiktoken cl100k_base（GPT 系工具链常见基准）对中文文本的公开实测：常见
汉字按 BPE 落地约 1 token/字，但 cl100k_base 的 10 万 token 词表要覆盖全部
语言、CJK 统一表意文字本身逾 9.7 万字，大量字符被切成 2-3 个 sub-token——
"1 token/字"是下限而非典型值。本系数只服务"保险丝"用途——ADR 原文的诉求是
"给个数量级兜边界，防止无界增长喂爆 LLM 上下文"，不是精确计费。在"防止喂爆"
这个目标下，宁可**高估** token 数、让保险丝**更早跳闸**（裁得多一点，也比
裁得不够、真的喂爆 LLM 上下文安全）——故取 1.5（每字 1.5 token）作保守
系数，而非贴着 1.0 的下限估计。header/分节标题等格式化开销（"【会话轮次】"
之类）不计入预算——量级上是几百字符、相对 8000 token 预算可忽略，计入只会
让实现复杂化而不改变保险丝的实际防护效果。
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from schemas.demand_ledger import LedgerEntry, active_adjustments, ledger_for_display
from schemas.node_adjustment import NodeAdjustment

from .types import (
    PlanSummaryLine,
    ProfileSnapshot,
    RoutingContext,
    SessionContextSource,
    TurnLogEntry,
    ledger_value_clause,
)

DEFAULT_MAX_TURNS = 40
"""轮次日志的计数上限（含首轮，ADR-0011 决策 3 原文"约最近 40 轮"）。"""

DEFAULT_MAX_TOKENS = 8000
"""打包上下文的 token 预算上限（ADR-0011 决策 3 原文"8K token"）。"""

_ESTIMATED_TOKENS_PER_CHAR = 1.5
"""字符 → token 的保守估算系数，见模块 docstring「token 估算系数怎么选的」。"""

# 画像"关键字段"拍板（报告要求的拍板点）：从 `UserProfile` 全量字段里只挑这
# 3 个。排除 `home_location`（经纬度/地址，对"路由脑子判这句话该怎么处理"
# 这类推理无信息量，反而像 PII，不适合喂进 LLM 上下文）；排除 `recent_trips`/
# `social_context_history`（历史行程数组本就是"会话之外"的长期记忆，体量可达
# 十条，与本打包器"这一个会话现在发生了什么"的定位不符，且与 `plan_version_
# log`"本会话方案演化史"的职责重叠，硬塞进来是同一叙事说两遍）。
_PROFILE_KEY_FIELDS = ("dietary_preference", "transport_preference", "default_budget")


def _estimate_tokens(char_len: int) -> int:
    return math.ceil(char_len * _ESTIMATED_TOKENS_PER_CHAR)


def _plan_summary_lines(itinerary_dict: Mapping[str, Any] | None) -> tuple[PlanSummaryLine, ...]:
    """当前方案摘要：每个非 home 节点一行（kind + 名称 + 时刻），从 itinerary
    现算，不缓存（与 `agent.intent.narrator._node_to_phrase` 同一"跳过 home"
    纪律）。"""
    if not itinerary_dict:
        return ()
    nodes = itinerary_dict.get("nodes") or []
    lines: list[PlanSummaryLine] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        if node.get("target_kind") == "home":
            continue
        title = str(node.get("title") or "").strip()
        if not title:
            continue
        lines.append(
            PlanSummaryLine(
                kind=str(node.get("kind") or "").strip(),
                name=title,
                time=str(node.get("start_time") or "").strip(),
            )
        )
    return tuple(lines)


def _profile_snapshot(
    scenario_id: str | None, profile_fields: Mapping[str, Any] | None
) -> ProfileSnapshot:
    pf = profile_fields or {}
    return ProfileSnapshot(
        scenario_id=scenario_id,
        dietary_preference=pf.get("dietary_preference"),
        transport_preference=pf.get("transport_preference"),
        default_budget=pf.get("default_budget"),
    )


def _ledger_slices(
    ledger_raw: Sequence[Mapping[str, Any]],
) -> tuple[tuple[NodeAdjustment, ...], tuple[Mapping[str, Any], ...]]:
    entries: list[LedgerEntry] = []
    for raw in ledger_raw:
        try:
            entries.append(LedgerEntry.model_validate(raw))
        except Exception:  # noqa: BLE001 —— 脏数据不让打包器崩，跳过该条
            continue
    # node_ref=None → `active_adjustments` 只返回"全局生效诉求"（见该函数
    # docstring："不传 node_ref 等价于没有具体节点上下文，此时只返回全局生效
    # 诉求"）。
    global_active = tuple(active_adjustments(entries))
    named_active = tuple(
        e for e in ledger_for_display(entries) if e.get("status") == "active"
    )
    return global_active, named_active


def _trim_turn_log(
    turns: Sequence[TurnLogEntry],
    *,
    max_turns: int,
    max_tokens: int,
    reserved_tokens: int,
) -> tuple[tuple[TurnLogEntry, ...], int]:
    """裁剪轮次日志。返回 (保留的轮次, 丢弃的轮次计数)。

    钉锚：`turns[0]`（首轮原始需求）永不裁剪。裁剪目标只是 `turns[1:]`——先按
    `max_turns` 做计数上限（保留最近的），再按 `max_tokens - reserved_tokens`
    做 token 预算（仍从最老的开始丢）。`reserved_tokens` 是钉锚材料（版本志/
    画像/台账/方案摘要等）已经现算出的预估体量，从总预算里先扣掉——它们不占用
    轮次日志的份额，也不会被裁掉。

    边界：若 `reserved_tokens` 本身已经逼近甚至超过 `max_tokens`（会话规模
    "千级 token"的假设被打破的极端情况），`turns[1:]` 会被大量甚至全部裁掉，
    只剩首轮——这是有定义的降级（打包器仍能产出，不抛异常），不是"没考虑到"
    的漏洞。
    """
    if not turns:
        return (), 0

    pinned_first = turns[0]
    rest = list(turns[1:])

    count_budget = max(0, max_turns - 1)
    dropped_by_count = max(0, len(rest) - count_budget)
    if dropped_by_count:
        rest = rest[dropped_by_count:]

    token_budget = max_tokens - reserved_tokens - _estimate_tokens(len(pinned_first.text))
    token_budget = max(token_budget, 0)

    kept_from_newest: list[TurnLogEntry] = []
    running = 0
    for entry in reversed(rest):
        cost = _estimate_tokens(len(entry.text))
        if running + cost > token_budget:
            break
        running += cost
        kept_from_newest.append(entry)
    kept_rest = list(reversed(kept_from_newest))
    dropped_by_token = len(rest) - len(kept_rest)

    return (pinned_first, *kept_rest), dropped_by_count + dropped_by_token


def _reserved_char_len(
    plan_version_log: Sequence[Mapping[str, Any]],
    plan_summary: Sequence[PlanSummaryLine],
    profile: ProfileSnapshot,
    ledger_active_named: Sequence[Mapping[str, Any]],
    pending_clarification: Any,
) -> int:
    """估算"钉锚材料"（除首轮外的其余五件套）大致会占用的字符数，供
    `_trim_turn_log` 从总预算里预扣。粗算即可（见模块 docstring 系数选择
    段——本来就是保守估算的保险丝，不追求与 `render_text` 的字节数严格
    对齐）。"""
    total = 0
    for entry in plan_version_log:
        total += len(str(entry.get("summary", "")))
    for line in plan_summary:
        total += len(line.kind) + len(line.name) + len(line.time)
    total += len(str(profile.scenario_id or ""))
    total += len(str(profile.dietary_preference or ""))
    total += len(str(profile.transport_preference or ""))
    total += len(str(profile.default_budget or ""))
    for entry in ledger_active_named:
        total += len(str(entry.get("source_text", ""))) + len(str(entry.get("value", "")))
    total += len(str(pending_clarification or ""))
    return total


def pack_routing_context(
    source: SessionContextSource,
    *,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> RoutingContext:
    """打包一份 `RoutingContext`（ADR-0011 决策 3 主入口）。

    纯函数：只读 `source` 暴露的只读方法，不做 LLM 调用/文件 I/O/当前时间
    戳；给定同一份来源材料快照，必然产出同样的 `RoutingContext`（`source`
    包装的底层 dict/Room 若在两次调用之间被外部改写，产出自然不同——这是
    "源变了"而不是"函数不纯"，纯函数承诺的是"给定同样的读取结果，产出同样
    的 RoutingContext"）。
    """
    raw_turns = [TurnLogEntry(role=role, text=text) for role, text in source.turn_log()]
    plan_version_log = tuple(dict(entry) for entry in source.plan_version_log())
    plan_summary = _plan_summary_lines(source.current_itinerary_dict())
    profile = _profile_snapshot(source.scenario_id(), source.profile_fields())
    pending_clarification = source.pending_clarification()
    user_decision = source.user_decision()
    ledger_active_global, ledger_active_named = _ledger_slices(source.demand_ledger_raw())

    reserved_tokens = _estimate_tokens(
        _reserved_char_len(
            plan_version_log, plan_summary, profile, ledger_active_named, pending_clarification
        )
    )
    turn_log, dropped = _trim_turn_log(
        raw_turns, max_turns=max_turns, max_tokens=max_tokens, reserved_tokens=reserved_tokens
    )

    return RoutingContext(
        turn_log=turn_log,
        dropped_turn_count=dropped,
        plan_version_log=plan_version_log,
        plan_summary=plan_summary,
        profile=profile,
        pending_clarification=pending_clarification,
        user_decision=user_decision,
        ledger_active_global=ledger_active_global,
        ledger_active_named=ledger_active_named,
    )


def render_demand_recap(ctx: RoutingContext) -> str:
    """「版本志 + 台账生效条目」切片的确定性文本（ADR-0011 决策 3 refiner 切片
    消费口径）—— `refiner_node` 消费，不是 `RoutingContext.render_text()` 的
    全量转发。

    只取这两件，因为 refiner 只需要"用户已经确认过/点击过的诉求史"去闭合
    "点击的诉求全量重排不认账"这个窗口，不需要完整会话上下文（轮次日志/
    画像那是路由脑子的工作；refiner 已经有独立的 `itinerary_summary` 承担
    "上一版方案讲了什么"，见 `agent/intent/refiner.py::summarize_itinerary`）。

    两段都为空时返回空串——调用方（`refiner_node`）据此传 `None` 给
    `refine_intent`，不给 prompt 添加一个空标题的段落（同 `narrator_prompt.py`
    `extras` 的"空则不出现"纪律）。
    """
    lines: list[str] = []
    if ctx.plan_version_log:
        lines.append("此前的方案版本变化：")
        lines.extend(
            f"- {entry.get('summary')}" for entry in ctx.plan_version_log if entry.get("summary")
        )
    if ctx.ledger_active_named:
        lines.append("此前已记录且仍生效的诉求（含点击调整）：")
        lines.extend(f"- {ledger_value_clause(entry)}" for entry in ctx.ledger_active_named)
    return "\n".join(lines)


__all__ = [
    "DEFAULT_MAX_TURNS",
    "DEFAULT_MAX_TOKENS",
    "pack_routing_context",
    "render_demand_recap",
]
