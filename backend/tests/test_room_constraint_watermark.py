"""test_room_constraint_watermark —— 约束池水位线切片（改口根治批 · 任务 1）。

【病灶（治的是这个）】房间成员四轮对话"不想要密室→（软化被判闲聊）→换个密室
主题→还是换回密室"，密室始终回不来。链条第①环：`_merge_constraints_text`
把约束池最近 5 条**全量拼接**当"这次输入"喂 refiner——旧否定每轮以"最新输入"
身份重播，refiner prompt 的"最新输入最高优先级"被结构性抹掉，用户的改口
（换回密室）在文本里被三条旧否定淹没。

【问题命名 + 成熟做法】约束池 `room.constraints` 是 append-only 事件日志，
每次重排是它的一个消费者——这是消息队列 consumer-offset 的经典形状。修法：
Room 上记一条整数水位线（`constraints_consumed_watermark` = 已消化条目数），
每轮只把水位线之后的**新增条目全取**（同一轮多人发言不丢）作为本次 feedback；
历史语义靠链式意图继承（单人架构既定状态模型：意图=唯一状态，增量按序只合并
一次——上一轮的否定已经合并进意图，不需要也不允许作为"新话"重播）。
ack 语义取 at-least-once：水位线在 refiner 合并提交（aupdate_state）后才推进，
中途取消宁可极端时机下同一条反馈被合并两次（对意图合并近似幂等），也绝不
静默丢失成员的话。

【本文件钉住】
1. 第二次重排喂给 refiner 的文本只含新增条目、不含已消化的旧否定
   （写作时刻 RED：现状全量拼接，第二轮文本含第一轮旧话）。
2. 同一轮多个新增条目全取 + 「{昵称}说：」归名前缀语义原样保留。
3. 水位线生命周期：合并提交后推进到切片终点。
4. planning 义务重开一局（`_trigger_fresh_plan`）快进水位线：新 episode 整体
   替换意图，旧 episode 挂账未消化的反馈随旧方案作废（与 locked_targets.clear()
   同一条 episode 边界纪律）。
5. 防御边界：切片为空时不做幻影重排（喂空串会触发 refiner"反馈为空→轻量调整"
   的距离-1km 幻影变更）——现场核对结论：该边界结构上到不了（唯一触发方
   `add_constraint` 的 feedback 分支先入池再触发；判成 chitchat 的输入根本不走
   `_trigger_replan`），本测试直接驱动 `_run_planning` 制造它，钉防御行为。

驱动手法：与 test_room_persistent_resume.py 同款——RoomManager 直驱、不起真 WS
（成员 ws=None，broadcast 对离线成员静默跳过）；反馈文本取 Layer 1 强信号
（"太远"/"太贵"），分发判定不依赖 stub 脑子；观测点=实例级包裹
`manager._replan_with_refiner` 捕获实际喂给注入链的 feedback 文本（同
smoke_final_llm.py H3 对 `_run_planning` 的实例级包裹手法）。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any

# ============================================================
# 过渡态桥（与 test_critics_v2 / test_room_* 系列同款）
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.routing.canonical_shortcut import DEMO_SCENARIOS  # noqa: E402
from collab import RoomManager  # noqa: E402
from collab.room import Constraint, Member  # noqa: E402
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary  # noqa: E402

PLANNING_INPUT = DEMO_SCENARIOS[1]["input"]  # 壳2 canonical 短路 → 确定性 planning
FEEDBACK_1 = "太远了，近一点"  # Layer 1 强信号（"太远"）→ 确定性 feedback
FEEDBACK_2 = "太贵了"          # Layer 1 强信号（"太贵"）→ 确定性 feedback


# ============================================================
# 共用构造/驱动 helper
# ============================================================


def _seed_room(owner_id: str) -> tuple[RoomManager, "Room"]:  # noqa: F821
    manager = RoomManager()
    room = manager.create_room(owner_id=owner_id, nickname="发起人")
    room.current_intent_dict = _make_intent().model_dump()
    room.current_itinerary_dict = _make_legal_itinerary().model_dump()
    return manager, room


def _spy_replan(manager: RoomManager) -> list[str]:
    """实例级包裹 `_replan_with_refiner`：捕获实际喂给注入链的 feedback 文本，
    然后照常执行原方法（水位线推进等真实逻辑全跑）。"""
    captured: list[str] = []
    orig = manager._replan_with_refiner

    async def spy(room: Any, feedback: str, **kwargs: Any) -> None:
        captured.append(feedback)
        return await orig(room, feedback, **kwargs)

    manager._replan_with_refiner = spy  # type: ignore[method-assign]
    return captured


async def _add_and_drain(manager: RoomManager, room: Any, user_id: str, text: str) -> None:
    """同 test_room_persistent_resume.py 同名 helper：触发 + 等待后台规划任务。"""
    await manager.add_constraint(room, user_id, text)
    if room.planning_task is not None:
        await room.planning_task


# ============================================================
# 1. 主根治：第二轮切片不含已消化的旧否定（写作时刻 RED）
# ============================================================


def test_second_replan_feeds_only_new_constraints():
    """两轮反馈：第二轮喂给 refiner 的文本只含第二轮新增条目。

    现状（RED 依据）：`_merge_constraints_text` 全量拼接最近 5 条，第二轮文本
    是「发起人说：太远了，近一点；发起人说：太贵了」——第一轮旧话以新话身份
    重播。根治后第二轮应恰为「【最新·最高优先】发起人说：太贵了」（问题①
    目标态：单条切片也带显式优先级标签，见 _merge_constraints_text）。
    """
    owner = "owner_wm_slice"
    manager, room = _seed_room(owner)
    captured = _spy_replan(manager)

    async def scenario() -> None:
        await _add_and_drain(manager, room, owner, FEEDBACK_1)
        await _add_and_drain(manager, room, owner, FEEDBACK_2)

    asyncio.run(scenario())

    assert len(captured) == 2, f"两轮反馈应各触发一次注入链，实际={captured}"
    assert captured[0] == f"【最新·最高优先】发起人说：{FEEDBACK_1}"
    assert captured[1] == f"【最新·最高优先】发起人说：{FEEDBACK_2}", (
        "第二轮切片只应含水位线之后的新增条目（链式意图继承：第一轮已合并进"
        f"意图，不得重播），实际={captured[1]!r}"
    )
    assert "太远" not in captured[1], f"旧否定重播未根治：{captured[1]!r}"


# ============================================================
# 2. 同轮多条全取 + 归名前缀保留
# ============================================================


def test_all_new_entries_in_one_round_taken_with_attribution():
    """水位线之后的新增条目**全取**（同一轮多人发言不丢），归名前缀原样保留。

    场景构造：成员 B 的条目已入池但尚未被任何重排消化（对应真实时序：上一轮
    重排被新约束打断取消，条目在池里挂账），随后 owner 的强信号反馈触发重排
    ——切片应含两条。问题①目标态：**倒序**（最新/owner 的话排最前，对应
    "最后一个人的话第一优先级"）+ 显式优先级标签，而非按入池时间顺序拼接。
    """
    owner = "owner_wm_multi"
    manager, room = _seed_room(owner)
    room.members["u_b"] = Member(user_id="u_b", nickname="小北", role="participant")
    room.constraints.append(Constraint(user_id="u_b", text="想安静一点的", source="text"))
    captured = _spy_replan(manager)

    asyncio.run(_add_and_drain(manager, room, owner, FEEDBACK_1))

    assert captured == [
        f"【最新·最高优先】发起人说：{FEEDBACK_1}；【其次】小北说：想安静一点的"
    ], (
        f"切片应全取新增条目、倒序 + 显式优先级标签，实际={captured}"
    )


# ============================================================
# 3. 水位线生命周期：合并提交后推进到切片终点
# ============================================================


def test_watermark_advances_to_slice_end_after_round():
    owner = "owner_wm_advance"
    manager, room = _seed_room(owner)

    assert room.constraints_consumed_watermark == 0, "建房水位线应为 0"

    asyncio.run(_add_and_drain(manager, room, owner, FEEDBACK_1))

    assert len(room.constraints) == 1
    assert room.constraints_consumed_watermark == 1, (
        "重排完成后水位线应推进到本轮切片终点（该条已消化）"
    )


# ============================================================
# 4. planning 义务重开一局：水位线快进，挂账反馈不跨 episode 重播
# ============================================================


def test_fresh_plan_fast_forwards_watermark_and_discards_stale_pending():
    """新 episode（canonical planning 输入 → `_trigger_fresh_plan`）整体替换意图：
    旧 episode 挂账未消化的反馈随旧方案作废（水位线快进），下一轮反馈的切片
    不得把它重播进全新方案。"""
    owner = "owner_wm_fresh"
    manager, room = _seed_room(owner)
    # 挂账条目：入池但未被消化（水位线仍 0）
    room.constraints.append(
        Constraint(user_id=owner, text="旧episode挂账的否定", source="text")
    )
    captured = _spy_replan(manager)

    async def scenario() -> None:
        await _add_and_drain(manager, room, owner, PLANNING_INPUT)   # 重开一局
        await _add_and_drain(manager, room, owner, FEEDBACK_1)       # 新 episode 首轮反馈

    asyncio.run(scenario())

    assert captured == [f"【最新·最高优先】发起人说：{FEEDBACK_1}"], (
        f"旧 episode 挂账条目不得跨 episode 重播进新方案的反馈切片，实际={captured}"
    )
    assert room.constraints_consumed_watermark == len(room.constraints), (
        "fresh plan 后接一轮反馈，水位线应收敛到池长"
    )


# ============================================================
# 5. 防御边界：空切片不做幻影重排
# ============================================================


def test_empty_slice_skips_phantom_replan():
    """切片为空（池里条目全已消化）却触发了 `_run_planning`：不得喂空串给
    refiner（那会触发"反馈为空→轻量调整"的距离-1km 幻影变更），应空转收尾
    （广播 done，前端 spinner 不挂死）。现场核对：该边界结构上到不了——
    本测试直接驱动 `_run_planning` 制造它，钉防御行为本身。"""
    owner = "owner_wm_empty"
    manager, room = _seed_room(owner)
    room.constraints.append(Constraint(user_id=owner, text="已消化的旧话", source="text"))
    room.constraints_consumed_watermark = 1  # 全部已消化 → 切片为空
    captured = _spy_replan(manager)

    asyncio.run(manager._run_planning(room))

    assert captured == [], f"空切片不得进注入链（幻影轻量调整），实际={captured}"
    types_ = [
        e["type"].value if hasattr(e["type"], "value") else e["type"]
        for e in room.planning_events_history
    ]
    assert types_ == ["done"], f"空切片应空转收尾（done 必达），实际={types_}"


# ============================================================
# 6. 协作房间约束流合并 A1：指名换店留痕不得污染未来的反馈合并
# ============================================================


def test_alternative_swap_note_is_watermarked_and_never_replayed_into_refiner_feedback():
    """`RoomManager._resolve_and_broadcast_adjust` 的 `AdjustActionAlternative`
    成功分支直接向 `room.constraints` append 一条 `source="alternative_swap"`
    的留痕记录（绕开 `add_constraint()`/`route_turn`，不烧 LLM、不触发重排——
    见该分支实现注释）。这条记录必须被水位线立即"预先消化"：它只是"记一笔"
    展示用，不是真实用户反馈，`_merge_constraints_text` 未来任何一次真实
    重排切片都绝不能把它当成"用户刚说的话"重播进 refiner——那会让 refiner
    看到一句"换成了「XX」"却误当成本轮新增的自由文本诉求。

    与本文件 `test_watermark_advances_to_slice_end_after_round` 同一族安全
    保证，验证角度不同：那条测的是"重排完成后"水位线状态，本条直接验证
    "指名换店发生后、下一次真实反馈重排"这条端到端路径的合并文本里确实
    不含换店记录。
    """
    from api._streams.models import AdjustActionAlternative

    owner = "owner_wm_swap_note"
    manager, room = _seed_room(owner)
    ws_stub = type("_Ws", (), {"send_json": staticmethod(lambda *_a, **_k: None)})()

    async def scenario() -> list[str]:
        # member.ws 留 None（同 test_room_persistent_resume.py 既有先例，
        # broadcast 对离线成员静默跳过，不需要真实 WS）。
        await manager.join(room, owner, "发起人", None)
        await manager.adjust(
            room, owner, "R001", AdjustActionAlternative(target_id="R017"),
        )
        assert len(room.constraints) == 1, "指名换店应先落一条约束流记录"
        assert room.constraints[0].source == "alternative_swap"
        assert room.constraints_consumed_watermark == 1, "记录后水位线应立即覆盖它"

        captured = _spy_replan(manager)
        # 换店之后，任何人再触发一次真实反馈——这是水位线要保护的场景：
        # 切片必须只含这条新反馈，不能把上面那条换店记录也重播进去。
        await _add_and_drain(manager, room, owner, FEEDBACK_1)
        return captured

    captured = asyncio.run(scenario())

    assert captured == [f"【最新·最高优先】发起人说：{FEEDBACK_1}"], (
        "指名换店记录不得混进下一次反馈重排的合并文本，"
        f"实际={captured}"
    )
    assert "换成了" not in captured[0], f"换店记录字样泄露进 refiner 反馈文本：{captured[0]!r}"
    _ = ws_stub  # 仅占位说明此处不需要真实 WS 断言，join 的 ws 形参传 None 即可
