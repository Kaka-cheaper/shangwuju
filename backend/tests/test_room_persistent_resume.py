"""test_room_persistent_resume —— 房间重排根治批（方案 d）验收：持久线程 + 注入续跑。

问题命名：状态注入续跑（LangGraph `aupdate_state(as_node=...)` + `astream(None)`，
官方 time-travel / human-in-the-loop 同款原语）。房间"反馈→重排"此前是降维实现：
图外调 `refine_intent` 合并反馈 → 把合并后文本塞进**全新一次性** graph session 跑
完整规划。五类损失：路由义务误判（真实 LLM 把合成文本判成非规划，整轮不出方案——
点火冒烟 H3 实锤）、合并精度、出处链断、诉求台账不延续、版本志断。根治方案 d
（spike：`backend/scripts/spike_room_resume.py`，实证终态 43 键与正常单人反馈轮
全等、核心事件逐字节等价、连续两次注入稳定）：房间维护稳定持久 graph 线程
`collab_{room_id}`，反馈轮注入"反馈已合并"状态后从 refiner 出边续跑，与单人反馈轮
走同一条管线。

本文件钉四类新现实（全部是本批的**有意行为变化**，不是回归）：
1. 前奏事件合成：续跑不再执行 router/refiner，其单人 SSE 事件（agent_thought/
   refinement_start/refinement_done/intent_parsed）由房间侧按同一 payload 形状
   合成补发——前端 dispatchPlanningEvent 靠它们清屏/更新意图面板，中途加入者靠
   planning_events_history 回放重建，缺一条链就断。
2. 持久线程落点：反馈轮真实落在 `collab_{room_id}` 线程的 checkpoint 里——intent
   是活对象（serde 无声类型擦除的哨兵断言）、messages/plan_version_log 跨轮累积。
3. 金标准对比（spike 核心断言固化）：单人反馈轮终态 vs 房间注入续跑终态，除
   session_id（线程身份本身必然不同）外全键相等；核心事件 payload 相等。
4. 中途取消坑自愈：注入后、续跑完成前 planning_task 被取消，线程留下"episode 已
   reset、方案未产出、next 非空"的中间态 checkpoint；下一条反馈按配方自愈，且
   refiner 仍拿得到"被拒的上一版"摘要素材（room.current_itinerary_dict 补喂）。

驱动手法：与 test_room_route_turn_dispatch.py 同款——RoomManager 直驱、不起真 WS
（成员 ws=None，broadcast 对离线成员静默跳过）；itinerary/intent 复用
tests/test_critics_v2.py 的 `_make_intent` / `_make_legal_itinerary`。
全程 LLM_PROVIDER=stub（conftest 强制），反馈文本取 Layer 1 强信号（"太远"/"太贵"），
分发判定不依赖 stub 脑子。
"""

from __future__ import annotations

import asyncio
import sys
import types
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

import pytest

# ============================================================
# 过渡态桥（与 test_critics_v2 / test_room_* 系列同款）
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from langchain_core.messages import BaseMessage  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent.routing.canonical_shortcut import DEMO_SCENARIOS  # noqa: E402
from collab import RoomManager  # noqa: E402
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary  # noqa: E402

PLANNING_INPUT = DEMO_SCENARIOS[1]["input"]  # 壳2 canonical 短路 → 确定性 planning
FEEDBACK_1 = "太远了，近一点"  # Layer 1 强信号（"太远"）→ 确定性 feedback


# ============================================================
# 共用构造/驱动 helper
# ============================================================


def _seed_room(owner_id: str, *, nickname: str = "发起人") -> tuple[RoomManager, "Room"]:  # noqa: F821
    """构造一个已有 intent + 合法 itinerary 的房间（不起 WS，成员 ws=None）。

    注意：这条路径的房间基线来自 dict 直塞（同 HTTP 建房带入 SESSION_STORE 快照），
    `collab_{room_id}` 线程此刻**没有任何 checkpoint**——反馈轮注入走的是"线程冷
    启动垫底"分支（spike 未测、本批生产代码补上的前提），本文件多数测试刻意用它。
    """
    manager = RoomManager()
    room = manager.create_room(owner_id=owner_id, nickname=nickname)
    room.current_intent_dict = _make_intent().model_dump()
    room.current_itinerary_dict = _make_legal_itinerary().model_dump()
    return manager, room


async def _add_constraint_and_drain(manager: RoomManager, room, user_id: str, text: str) -> None:
    """同 test_room_route_turn_dispatch.py 同名 helper：触发 + 等待后台规划任务
    必须在同一次 asyncio.run 里完成（fire-and-forget task 不能跨事件循环 await）。"""
    await manager.add_constraint(room, user_id, text)
    if room.planning_task is not None:
        await room.planning_task


def _event_types(room) -> list[str]:
    return [
        e["type"].value if hasattr(e["type"], "value") else e["type"]
        for e in room.planning_events_history
    ]


def _room_config(room) -> dict[str, Any]:
    return {"configurable": {"thread_id": f"collab_{room.room_id}"}}


# ============================================================
# 归一化 + diff（金标准对比用；移植自 spike_room_resume.py，剔除时间戳等易变字段）
# ============================================================

_DROP_KEYS = {"timestamp", "timestamp_ms", "created_at", "total_ms", "duration_ms"}


def _norm(obj: Any) -> Any:
    if isinstance(obj, BaseMessage):
        return {"__msg__": obj.type, "content": obj.content}
    if isinstance(obj, BaseModel):
        return _norm(obj.model_dump(mode="python"))
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): _norm(v) for k, v in obj.items() if str(k) not in _DROP_KEYS}
    if isinstance(obj, (list, tuple)):
        return [_norm(x) for x in obj]
    if isinstance(obj, float):
        return round(obj, 6)
    return obj


def _diff_paths(a: Any, b: Any, path: str = "", out: list[str] | None = None, limit: int = 60) -> list[str]:
    """左=GOLD，右=房间。产出差异路径列表（断言失败时供人读）。"""
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                out.append(f"{path}.{k}: 仅房间侧有 = {repr(b[k])[:100]}")
            elif k not in b:
                out.append(f"{path}.{k}: 仅单人侧有 = {repr(a[k])[:100]}")
            else:
                _diff_paths(a[k], b[k], f"{path}.{k}", out, limit)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: 列表长度 {len(a)} vs {len(b)}")
        for i in range(min(len(a), len(b))):
            _diff_paths(a[i], b[i], f"{path}[{i}]", out, limit)
        return out
    if a != b:
        out.append(f"{path}: {repr(a)[:100]} != {repr(b)[:100]}")
    return out


# ============================================================
# 1. 前奏事件合成：形状与单人 SSE 契约逐字段同形，随后真跑规划
# ============================================================


def test_feedback_round_emits_prelude_then_real_planning_stream():
    """反馈轮事件流 = 4 条合成前奏 + 续跑真实规划流（stub 下 ILS 兜底真出方案）。

    【判据变更理由（房间重排根治批）】旧实现下反馈轮第二段进全新 graph session，
    stub 的 router 对合成文本必落保守地板 → chitchat_reply 收尾、不产方案；根治后
    续跑不经过 router，规划管线（workers→planner→ILS 兜底）真实执行到底。
    """
    owner_id = "owner_resume_prelude_test"
    manager, room = _seed_room(owner_id)
    old_distance = room.current_intent_dict["distance_max_km"]

    asyncio.run(_add_constraint_and_drain(manager, room, owner_id, "太远了"))

    types_ = _event_types(room)
    assert types_[:4] == [
        "agent_thought",
        "refinement_start",
        "refinement_done",
        "intent_parsed",
    ], f"反馈轮必须以 4 条合成前奏开头（前端清屏/意图面板/回放链都等它们），实际={types_}"
    assert "itinerary_ready" in types_, f"续跑应真出方案（不再经 router 降级），实际={types_}"
    assert types_[-1] == "done", f"续跑流应以 done 收尾，实际={types_}"
    assert "chitchat_reply" not in types_, "旧降级路径（新 session 路由判非规划）不应再出现"

    events = room.planning_events_history
    # 前奏 payload 与单人 SSE 逐字段同形（emit_router feedback 分支 + emit_refiner）
    assert events[0]["payload"] == {"text": "收到反馈，正在调整……"}
    assert events[1]["payload"] == {"feedback_text": "发起人说：太远了"}, (
        "refinement_start 携带喂给 refiner 的反馈文本（房间侧=归名合并后的约束池文本）"
    )
    done_payload = events[2]["payload"]
    assert set(done_payload.keys()) == {"refined_intent", "changed_fields", "refiner_note"}, (
        "refinement_done payload 契约=RefinementOutput.model_dump() 三键（api_contract §分支B）"
    )
    assert done_payload["refined_intent"] == room.current_intent_dict
    assert done_payload["changed_fields"], "『太远』经 stub 规则兜底必产出距离收紧的变更条目"
    assert done_payload["refiner_note"], "refiner_note 缺省时应兜 emit_refiner 同款默认文案"
    assert events[3]["payload"] == done_payload["refined_intent"], (
        "intent_parsed 重推新意图（前端 IntentSummary 靠它刷新）"
    )

    # 意图投影真的收紧了 + 方案真的落地了
    assert room.current_intent_dict["distance_max_km"] < old_distance
    assert room.current_itinerary_dict is not None
    assert room.constraints and room.constraints[0].text == "太远了", "约束池归档语义不变"


# ============================================================
# 2. 持久线程落点：checkpoint 真实存在、intent 是活对象、状态形状正确
# ============================================================


def test_feedback_round_lands_state_on_persistent_room_thread():
    """反馈轮从此落在稳定线程 `collab_{room_id}` 上（旧实现：一次性
    `collab_{room_id}_{ts}` session，checkpoint 随线程 id 蒸发）。

    intent 类型断言是 serde 红线的哨兵：spike 实证白名单外/dict 化注入的失败模式
    是**无声类型擦除**（读回静默变 dict，零告警），活的 IntentExtraction 读回必须
    仍是 IntentExtraction。
    """
    owner_id = "owner_resume_thread_test"
    manager, room = _seed_room(owner_id)

    async def scenario():
        await _add_constraint_and_drain(manager, room, owner_id, "太远了")

        from agent.graph.build import get_compiled_graph

        graph = get_compiled_graph()
        snap = await graph.aget_state(_room_config(room))
        return dict(snap.values), tuple(snap.next)

    vals, next_nodes = asyncio.run(scenario())

    assert vals, "collab_{room_id} 线程上应有本轮反馈的 checkpoint"
    assert next_nodes == (), "续跑应完整走到 END，不留半截 next"
    assert type(vals.get("intent")).__name__ == "IntentExtraction", (
        f"intent 必须是活对象（serde 无声类型擦除哨兵），实际={type(vals.get('intent'))}"
    )
    assert vals.get("route_kind") == "feedback"
    assert vals.get("user_input") == "发起人说：太远了"
    assert vals.get("itinerary") is not None, "续跑终态应有方案"

    msgs = vals.get("messages") or []
    assert any(
        m.type == "human" and "太远了" in str(m.content) for m in msgs
    ), f"messages 通道应含本轮反馈的 HumanMessage，实际={[(m.type, str(m.content)[:20]) for m in msgs]}"

    pvl = vals.get("plan_version_log") or []
    assert [e.get("trigger") for e in pvl] == ["feedback"], (
        f"版本志应记下这轮反馈（trigger=feedback），实际={pvl}"
    )


def test_consecutive_feedback_rounds_accumulate_version_log_and_messages():
    """连续注入稳定（spike Q5 固化）：planning 开局 + 两轮反馈都走同一持久线程，
    版本志 [first, feedback, feedback] 连续编号、messages 跨轮累积——这正是旧实现
    五类损失里"诉求台账不延续/版本志断"的反面钉法。"""
    owner_id = "owner_resume_seq_test"
    manager = RoomManager()
    room = manager.create_room(owner_id=owner_id, nickname="发起人")

    async def scenario():
        await _add_constraint_and_drain(manager, room, owner_id, PLANNING_INPUT)
        assert room.current_itinerary_dict is not None, "前置：canonical 开局应出方案"
        await _add_constraint_and_drain(manager, room, owner_id, "太远了")
        await _add_constraint_and_drain(manager, room, owner_id, "太贵了")

        from agent.graph.build import get_compiled_graph

        graph = get_compiled_graph()
        snap = await graph.aget_state(_room_config(room))
        return dict(snap.values)

    vals = asyncio.run(scenario())

    pvl = vals.get("plan_version_log") or []
    assert [e.get("trigger") for e in pvl] == ["first", "feedback", "feedback"], (
        f"版本志应跨三轮连续累积，实际={[(e.get('version_n'), e.get('trigger')) for e in pvl]}"
    )
    assert [e.get("version_n") for e in pvl] == [1, 2, 3]

    human_texts = [str(m.content) for m in (vals.get("messages") or []) if m.type == "human"]
    assert len(human_texts) == 3, f"三轮输入应各留一条 HumanMessage，实际={human_texts}"
    assert any("太远了" in t for t in human_texts)
    assert any("太贵了" in t for t in human_texts)
    assert room.current_itinerary_dict is not None


# ============================================================
# 3. 金标准对比（spike 核心断言固化）：单人反馈轮 ≡ 房间注入续跑
# ============================================================


def test_room_injected_resume_matches_single_user_feedback_turn():
    """防漂移之锚：同一 planning 输入 + 同一反馈原话，单人生产入口（run_graph_stream，
    router→refiner 图内执行）与房间注入续跑（aupdate_state+astream(None)）的**图终态**
    除 session_id（线程身份必然不同）外必须全键相等；核心事件 payload 相等。

    对比在 `_replan_with_refiner(room, 反馈原话)` 这一层做——房间真实入口
    `add_constraint` 会给约束文本加"{昵称}说："归名前缀（多人场景的有意语义，
    test_feedback_round_emits_prelude_then_real_planning_stream 已单独钉住），
    喂不同文本对比终态没有意义；本测试锚的是"注入续跑配方 ≡ 单人反馈轮"这一层。
    owner 取 "demo_user" 使 user_id 与单人入口默认值一致。
    """
    gold_session = f"gold_room_parity_{uuid.uuid4().hex[:8]}"

    async def scenario():
        from agent.graph.build import get_compiled_graph
        from agent.graph.sse_adapter import run_graph_stream

        graph = get_compiled_graph()

        # ---- GOLD：单人生产入口两轮 ----
        async for _ in run_graph_stream(user_input=PLANNING_INPUT, session_id=gold_session):
            pass
        gold_t2 = []
        async for ev in run_graph_stream(user_input=FEEDBACK_1, session_id=gold_session):
            gold_t2.append(ev)
        gold_snap = await graph.aget_state({"configurable": {"thread_id": gold_session}})

        # ---- 房间：turn1 canonical 开局（同一生产入口，落持久线程）----
        manager = RoomManager()
        room = manager.create_room(owner_id="demo_user", nickname="发起人")
        await _add_constraint_and_drain(manager, room, "demo_user", PLANNING_INPUT)
        n_before = len(room.planning_events_history)

        # ---- 房间：turn2 注入 + 续跑（喂与 GOLD 同一反馈原话）----
        await manager._replan_with_refiner(room, FEEDBACK_1)
        room_snap = await graph.aget_state(_room_config(room))
        room_t2_events = room.planning_events_history[n_before:]
        return gold_t2, dict(gold_snap.values), room_t2_events, dict(room_snap.values)

    gold_t2, gold_vals, room_t2_events, room_vals = asyncio.run(scenario())

    # ---- 终态全键对比（session_id 是线程身份本身，必然不同，显式豁免）----
    gold_vals.pop("session_id", None)
    room_vals.pop("session_id", None)
    diff = _diff_paths(_norm(gold_vals), _norm(room_vals))
    assert diff == [], "单人反馈轮 vs 房间注入续跑 终态漂移：\n" + "\n".join(diff)

    # ---- 核心事件 payload 对比（合成前奏 + 续跑事件 vs 单人 SSE）----
    def _first_payload_room(etype: str) -> Any:
        for e in room_t2_events:
            t = e["type"].value if hasattr(e["type"], "value") else e["type"]
            if t == etype:
                return e["payload"]
        return None

    def _first_payload_gold(etype: str) -> Any:
        for e in gold_t2:
            if e.type.value == etype:
                return e.payload
        return None

    for etype in ("refinement_start", "refinement_done", "intent_parsed", "itinerary_ready", "agent_narration"):
        g, r = _first_payload_gold(etype), _first_payload_room(etype)
        assert g is not None, f"GOLD 反馈轮缺事件 {etype}（金标准前提被破坏）"
        assert r is not None, f"房间反馈轮缺事件 {etype}（前奏合成/续跑透传断链）"
        d = _diff_paths(_norm(g), _norm(r), path=etype)
        assert d == [], f"{etype} payload 漂移：\n" + "\n".join(d)


# ============================================================
# 4. 中途取消坑：注入后取消续跑 → 中间态 checkpoint → 下一条反馈自愈
# ============================================================


def test_cancel_mid_resume_then_second_feedback_self_heals(monkeypatch):
    """持久线程的新坑（spike 未测，本批必须处理）：新约束到来时房间
    `planning_task.cancel()`，若打断点在"注入已完成、续跑未产出"之间，线程留下
    "episode 已 reset（itinerary=None）、next 非空"的中间态 checkpoint。下一次注入
    按配方自愈（aupdate_state 覆盖 + 续跑走完），但那轮 refiner 从图状态读到的
    itinerary 是 None——丢失"被拒的上一版"摘要素材。生产代码用
    `room.current_itinerary_dict` 补喂；本测试钉住"取消后再反馈：系统自愈 + refiner
    拿得到旧方案摘要"两件事。"""
    owner_id = "owner_cancel_pit_test"
    manager, room = _seed_room(owner_id)

    async def scenario():
        import agent.graph.sse_adapter as sse_adapter
        from agent.graph.build import get_compiled_graph

        graph = get_compiled_graph()
        real_resume = sse_adapter.run_graph_resume_stream
        entered = asyncio.Event()

        async def hanging_resume(**kwargs):
            # 注入（aupdate_state）已在调用本函数之前完成——挂死在这里即制造
            # "注入后、续跑前被取消"的确定性窗口，不靠 sleep 赛跑。
            entered.set()
            await asyncio.sleep(30)
            yield  # pragma: no cover —— 永远到不了

        monkeypatch.setattr(sse_adapter, "run_graph_resume_stream", hanging_resume)
        await manager.add_constraint(room, owner_id, "太远了")
        await asyncio.wait_for(entered.wait(), timeout=10)

        # 钉住"坑"的存在：episode 已 reset、方案未产出、next 非空
        snap_mid = await graph.aget_state(_room_config(room))
        assert tuple(snap_mid.next), "注入后线程应停在续跑前（next 非空）——坑的前半"
        assert snap_mid.values.get("itinerary") is None, "episode 已 reset——坑的后半"
        assert room.current_itinerary_dict is not None, "房间投影仍握着被拒的上一版（补喂素材来源）"

        # 第二条反馈：恢复真续跑 + spy 住 refine_intent 观察补喂
        monkeypatch.setattr(sse_adapter, "run_graph_resume_stream", real_resume)

        import agent.graph.nodes.refiner as refiner_mod

        captured: dict[str, Any] = {}
        real_refine = refiner_mod.refine_intent

        def spy_refine(*args, **kwargs):
            captured.update(kwargs)
            return real_refine(*args, **kwargs)

        monkeypatch.setattr(refiner_mod, "refine_intent", spy_refine)

        await _add_constraint_and_drain(manager, room, owner_id, "太贵了")

        final_snap = await graph.aget_state(_room_config(room))
        return captured, dict(final_snap.values), tuple(final_snap.next)

    captured, final_vals, final_next = asyncio.run(scenario())

    summary = captured.get("itinerary_summary")
    assert summary, (
        "取消坑修复的核心：图状态 itinerary 为 None 时，refiner 必须仍拿到旧方案摘要"
        "（room.current_itinerary_dict 补喂），否则丢失『被拒的上一版』判断素材"
    )
    assert "P040" in summary or "R001" in summary, (
        f"摘要应来自被拒的上一版方案（P040/R001 fixture），实际={summary!r}"
    )

    # 系统自愈：第二轮完整走完
    types_ = _event_types(room)
    assert types_[:4] == ["agent_thought", "refinement_start", "refinement_done", "intent_parsed"]
    assert "itinerary_ready" in types_ and types_[-1] == "done", f"自愈轮应完整收尾，实际={types_}"
    assert final_vals.get("itinerary") is not None, "自愈后图终态应有方案"
    assert final_next == (), "自愈后线程应走到 END，不留半截 next"
    assert room.current_itinerary_dict is not None
