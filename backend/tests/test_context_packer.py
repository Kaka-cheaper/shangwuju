"""tests.test_context_packer —— ADR-0011 决策 3 E-2-b 验收：会话上下文打包器
纯函数单测。

覆盖 `agent.context`（types/sources/packer）四组承诺：

1. **裁剪（保险丝）**：轮数上限（40 轮，保留最近的）+ token 预算上限（8K，
   系数 1.5 token/字保守估算），两者都从最老的非首轮轮次开始丢。
2. **钉锚永不裁**：首轮原始需求 / 方案版本志全量 / pending_clarification /
   台账生效条目——即便轮次日志被裁光也原样在场（ADR 原文）。
3. **确定性**：同一份来源材料两次打包产出完全相等（含 render_text 字节级
   一致）——纯函数、无时间戳/随机性。
4. **两来源等价形状**：`GraphStateSource`（AgentState dict）与 `RoomSource`
   （collab.room.Room）喂等价材料时产出同形 `RoutingContext`（角色词汇统一、
   台账切片一致）；房间侧已知空缺（版本志/画像/待澄清）诚实降级为空。

另钉台账切片语义：只取 ACTIVE（SUPERSEDED/SATISFIED 不进）；
`ledger_active_global` 只含全局条目（`NodeAdjustment` 类型化），
`ledger_active_named` 含全部生效条目（节点级点击也在——refiner 切片的
核心素材）。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# agent 命名空间桥接（与 test_narrator_full_nodes 等同款）
if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from agent.context import (  # noqa: E402
    DEFAULT_MAX_TURNS,
    GraphStateSource,
    RoomSource,
    pack_routing_context,
    render_demand_recap,
)
from collab.room import Room  # noqa: E402

# ============================================================
# fixture helpers
# ============================================================

_FIRST_INPUT = "今天下午带老婆孩子出去玩"
_FEEDBACK_INPUT = "太远了"


def _ledger_dicts() -> list[dict]:
    """一条节点级点击（ACTIVE）+ 一条全局诉求（ACTIVE）+ 一条被顶替（SUPERSEDED）。"""
    return [
        {
            "member_id": None,
            "nickname": None,
            "node_ref": {"kind": "restaurant", "target_id": "R001"},
            "adjustment": {"dimension": "price", "value": "cheaper"},
            "status": "active",
            "source_text": "更便宜的",
            "created_at": 100.0,
        },
        {
            "member_id": None,
            "nickname": None,
            "node_ref": None,
            "adjustment": {"dimension": "dietary", "value": "不辣"},
            "status": "active",
            "source_text": "别太辣",
            "created_at": 101.0,
        },
        {
            "member_id": None,
            "nickname": None,
            "node_ref": {"kind": "restaurant", "target_id": "R001"},
            "adjustment": {"dimension": "price", "value": "pricier"},
            "status": "superseded",
            "source_text": "高档一点",
            "created_at": 99.0,
        },
    ]


def _version_log() -> list[dict]:
    return [
        {"version_n": 1, "summary": f"v1: 按『{_FIRST_INPUT}』出方案", "trigger": "first", "timestamp": 1},
        {"version_n": 2, "summary": f"v2: 应『{_FEEDBACK_INPUT}』调整", "trigger": "feedback", "timestamp": 2},
    ]


def _itinerary_dict() -> dict:
    return {
        "nodes": [
            {"target_kind": "home", "kind": "出发", "title": "家", "start_time": "14:00"},
            {"target_kind": "poi", "kind": "主活动", "title": "森林儿童探索乐园", "start_time": "14:15"},
            {"target_kind": "restaurant", "kind": "用餐", "title": "鲸落·健康简餐", "start_time": "17:30"},
            {"target_kind": "home", "kind": "回家", "title": "家", "start_time": "19:30"},
        ]
    }


def _graph_state(*, n_extra_turns: int = 0, big_turn_chars: int = 0) -> dict:
    messages = [HumanMessage(content=_FIRST_INPUT), AIMessage(content="给你排好了~")]
    for i in range(n_extra_turns):
        text = ("字" * big_turn_chars) if big_turn_chars else f"闲聊第{i}句"
        messages.append(HumanMessage(content=text) if i % 2 == 0 else AIMessage(content=text))
    messages.append(HumanMessage(content=_FEEDBACK_INPUT))
    return {
        "messages": messages,
        "plan_version_log": _version_log(),
        "itinerary": _itinerary_dict(),
        "scenario_id": "S1",
        "demand_ledger": _ledger_dicts(),
        "user_decision": None,
    }


def _room_equivalent() -> Room:
    """喂与 _graph_state() 等价对话/台账/方案材料的 Room（版本志/画像房间侧无）。"""
    room = Room(room_id="r_test", owner_id="u1")
    room.chat_messages = [
        {"id": "m1", "role": "user", "text": _FIRST_INPUT, "createdAt": 1},
        {"id": "m2", "role": "agent", "text": "给你排好了~", "createdAt": 2},
        {"id": "m3", "role": "user", "text": _FEEDBACK_INPUT, "createdAt": 3},
    ]
    room.current_itinerary_dict = _itinerary_dict()
    room.demand_ledger = _ledger_dicts()
    return room


# ============================================================
# 1) 裁剪：轮数上限 + token 预算上限
# ============================================================


def test_turn_count_cap_keeps_first_and_most_recent():
    state = _graph_state(n_extra_turns=100)  # 共 103 轮，远超 40
    ctx = pack_routing_context(GraphStateSource(state))

    assert len(ctx.turn_log) == DEFAULT_MAX_TURNS
    assert ctx.dropped_turn_count == 103 - DEFAULT_MAX_TURNS
    # 钉锚：首轮永在第 0 位
    assert ctx.turn_log[0].text == _FIRST_INPUT
    # 裁的是最老的非首轮：最新一轮（反馈）必须还在末尾
    assert ctx.turn_log[-1].text == _FEEDBACK_INPUT
    # 被丢弃的是紧跟首轮之后的最老几条
    kept_texts = {t.text for t in ctx.turn_log}
    assert "给你排好了~" not in kept_texts, "最老的陪聊轮应最先被裁"


def test_token_budget_cap_drops_oldest_big_turns():
    # 6 条超大轮（每条 3000 字 ≈ 4500 token），远超 8K 预算但不超 40 轮计数
    state = _graph_state(n_extra_turns=6, big_turn_chars=3000)
    ctx = pack_routing_context(GraphStateSource(state))

    assert ctx.dropped_turn_count > 0, "token 预算应触发裁剪"
    assert ctx.turn_log[0].text == _FIRST_INPUT, "首轮钉锚不参与 token 预算裁剪"
    assert ctx.turn_log[-1].text == _FEEDBACK_INPUT, "最新一轮优先保留"


def test_no_trim_when_within_budget():
    state = _graph_state(n_extra_turns=4)
    ctx = pack_routing_context(GraphStateSource(state))
    assert ctx.dropped_turn_count == 0
    assert len(ctx.turn_log) == 7  # 2 + 4 + 1


# ============================================================
# 2) 钉锚永不裁
# ============================================================


def test_pinned_material_survives_heavy_trimming():
    state = _graph_state(n_extra_turns=100, big_turn_chars=2000)
    ctx = pack_routing_context(GraphStateSource(state))

    # 版本志全量在场（2 条一条不少）
    assert len(ctx.plan_version_log) == 2
    assert ctx.plan_version_log[1]["summary"] == f"v2: 应『{_FEEDBACK_INPUT}』调整"
    # 台账生效切片在场（2 条 ACTIVE；SUPERSEDED 不进）
    assert len(ctx.ledger_active_named) == 2
    # 首轮原始需求在场
    assert ctx.turn_log[0].text == _FIRST_INPUT
    # 方案摘要/画像不受裁剪影响
    assert len(ctx.plan_summary) == 2
    assert ctx.profile.scenario_id == "S1"


def test_extreme_reserved_overflow_degrades_to_first_turn_only():
    """钉锚材料自身超预算的极端边界：日志退化到只剩首轮，不崩、不丢锚。"""
    state = _graph_state(n_extra_turns=4)
    state["plan_version_log"] = [
        {"version_n": 1, "summary": "长" * 9000, "trigger": "first", "timestamp": 1}
    ]
    ctx = pack_routing_context(GraphStateSource(state))
    assert ctx.turn_log[0].text == _FIRST_INPUT
    assert len(ctx.turn_log) == 1, "预算被钉锚吃光时非首轮轮次全部让位"
    assert len(ctx.plan_version_log) == 1, "版本志本身永不被裁"


# ============================================================
# 3) 确定性
# ============================================================


def test_pack_is_deterministic():
    state = _graph_state(n_extra_turns=10)
    ctx1 = pack_routing_context(GraphStateSource(state))
    ctx2 = pack_routing_context(GraphStateSource(state))
    assert ctx1 == ctx2
    assert ctx1.render_text() == ctx2.render_text()
    assert render_demand_recap(ctx1) == render_demand_recap(ctx2)


def test_render_text_sections_and_content():
    ctx = pack_routing_context(GraphStateSource(_graph_state()))
    text = ctx.render_text()
    # 固定分节结构（空段也有占位，消费方按结构定位）
    for section in (
        "【首轮原始需求】", "【会话轮次】", "【方案版本志】", "【当前方案摘要】",
        "【画像】", "【待澄清】", "【待确认态】", "【台账生效条目】",
    ):
        assert section in text, f"render_text 缺分节 {section}"
    assert _FIRST_INPUT in text
    assert f"v2: 应『{_FEEDBACK_INPUT}』调整" in text
    assert "主活动·森林儿童探索乐园（14:15）" in text
    assert "场景=S1" in text
    # 台账人话短句（方向词翻中文 + 原话引用）
    assert "更便宜（源：『更便宜的』）" in text
    assert "饮食要求「不辣」（源：『别太辣』）" in text
    # SUPERSEDED 条目不出现
    assert "高档一点" not in text


def test_render_text_empty_state_placeholders():
    ctx = pack_routing_context(GraphStateSource({}))
    text = ctx.render_text()
    assert "（无）" in text
    assert "（暂无方案版本）" in text
    assert "（暂无方案）" in text
    assert "（无画像数据）" in text
    assert "（暂无生效诉求）" in text


# ============================================================
# 4) 台账切片语义
# ============================================================


def test_ledger_slices_active_only_and_global_vs_named():
    ctx = pack_routing_context(GraphStateSource(_graph_state()))
    # global 切片：只有全局条目（node_ref=None），类型化 NodeAdjustment
    assert len(ctx.ledger_active_global) == 1
    assert ctx.ledger_active_global[0].dimension.value == "dietary"
    assert ctx.ledger_active_global[0].value == "不辣"
    # named 切片：全部 ACTIVE（节点级点击 + 全局），SUPERSEDED 不进
    assert len(ctx.ledger_active_named) == 2
    statuses = {e["status"] for e in ctx.ledger_active_named}
    assert statuses == {"active"}
    node_refs = [e["node_ref"] for e in ctx.ledger_active_named]
    assert {"kind": "restaurant", "target_id": "R001"} in node_refs, "节点级点击必须在 named 切片里"


def test_render_demand_recap_contains_versions_and_active_demands():
    ctx = pack_routing_context(GraphStateSource(_graph_state()))
    recap = render_demand_recap(ctx)
    assert "此前的方案版本变化：" in recap
    assert f"v2: 应『{_FEEDBACK_INPUT}』调整" in recap
    assert "此前已记录且仍生效的诉求" in recap
    assert "更便宜（源：『更便宜的』）" in recap
    assert "高档一点" not in recap, "SUPERSEDED 条目不进 recap"


def test_render_demand_recap_empty_returns_empty_string():
    ctx = pack_routing_context(GraphStateSource({"messages": [HumanMessage(content="你好")]}))
    assert render_demand_recap(ctx) == ""


def test_malformed_ledger_entry_skipped_not_crash():
    state = _graph_state()
    state["demand_ledger"] = [{"garbage": True}, *_ledger_dicts()]
    ctx = pack_routing_context(GraphStateSource(state))
    assert len(ctx.ledger_active_named) == 2, "脏条目跳过，其余照常"


# ============================================================
# 5) 两来源等价形状
# ============================================================


def test_two_sources_equivalent_shape():
    graph_ctx = pack_routing_context(GraphStateSource(_graph_state()))
    room_ctx = pack_routing_context(RoomSource(_room_equivalent()))

    # 轮次日志：角色词汇统一为 user/agent，文本一致
    assert [(t.role, t.text) for t in room_ctx.turn_log] == [
        (t.role, t.text) for t in graph_ctx.turn_log
    ]
    # 台账切片：两底座产出完全一致的切片
    assert room_ctx.ledger_active_global == graph_ctx.ledger_active_global
    assert room_ctx.ledger_active_named == graph_ctx.ledger_active_named
    # 方案摘要一致（同一份 itinerary dict）
    assert room_ctx.plan_summary == graph_ctx.plan_summary
    # 房间侧已知空缺：诚实降级为空，不发明数据
    assert room_ctx.plan_version_log == ()
    assert room_ctx.profile.is_empty()
    assert room_ctx.pending_clarification is None
    # 两来源的 render_text 都能产出（结构相同，材料丰富度不同）
    assert "【台账生效条目】" in room_ctx.render_text()


def test_room_source_confirmed_maps_to_user_decision():
    room = _room_equivalent()
    assert pack_routing_context(RoomSource(room)).user_decision is None
    room.confirmed = True
    assert pack_routing_context(RoomSource(room)).user_decision == "confirm"


def test_room_source_reads_only_does_not_mutate_room():
    room = _room_equivalent()
    before_chat = [dict(m) for m in room.chat_messages]
    before_ledger = [dict(e) for e in room.demand_ledger]
    pack_routing_context(RoomSource(room))
    assert room.chat_messages == before_chat
    assert room.demand_ledger == before_ledger


# ============================================================
# 6) pending_clarification 占位透传（E-3 生产者出生前恒 None；出生后自动透传）
# ============================================================


def test_pending_clarification_passthrough():
    state = _graph_state()
    assert pack_routing_context(GraphStateSource(state)).pending_clarification is None
    state["pending_clarification"] = {"question": "想去哪个方向？", "options": ["东", "西"]}
    ctx = pack_routing_context(GraphStateSource(state))
    assert ctx.pending_clarification == {"question": "想去哪个方向？", "options": ["东", "西"]}
    assert "想去哪个方向？" in ctx.render_text()
