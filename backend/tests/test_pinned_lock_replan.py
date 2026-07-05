"""tests.test_pinned_lock_replan —— 赞锁定根治批：locked_stages 真接重排引擎。

【病灶】房间成员点赞写 `Room.locked_stages` 并广播锁定归名，但该集合此前
**纯展示**——没有任何下游消费者按它门控重排（room.py 旧 docstring 自认）。
用户的自然预期"重排时保住这一段"是空头支票。承接机制（`plan_hybrid(pinned=
...)` 的 PinSpec 解析/保护/被牺牲必产 advisory，ADR-0010 D-7）早已 TDD 落地，
只差生产接线。

【本批选型：形态 (i)「pinned 在场」做成 critic 硬判据 + 蓝图用户消息先验 +
修复阶梯自然接管 + 房间出口兜底归名】——完整论证见任务报告；本文件钉住整条
数据流的每一跳：

  房间(update_vote 登记 locked_targets)
    → 注入(_replan_with_refiner values 带 pinned_targets，serde 安全 plain dict)
    → 蓝图 LLM(用户消息「必须保留」段，planner_node 透传)
    → critic(check_pinned_presence，Stage 1 HARD → backprompt 阶梯)
    → ILS(ils_replan_node 构造 PinSpec 透传 plan_hybrid，原生保护+advisory)
    → rule 地板/give_up(pinned 缺席补产 PINNED_UNSATISFIABLE advisory)
    → 房间出口检查(锁没保住 → 归名告知广播 + locked_stages 重投影)

单人路径零变化保证：pinned_targets 缺省（无生产者）= 现状行为，本文件对
「无 pin 时不产任何新违规/新段落」逐层有对照断言。

驱动手法：与 test_room_persistent_resume.py 同款——RoomManager 直驱、不起真
WS；itinerary/intent 复用 tests/test_critics_v2 的 `_make_intent`/
`_make_legal_itinerary`（P040 poi / R001 餐厅 fixture）。全程 LLM_PROVIDER=stub。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any

import pytest

# ============================================================
# 过渡态桥（与 test_room_* 系列同款）
# ============================================================

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from collab import RoomManager  # noqa: E402
from tests.test_critics_v2 import _make_intent, _make_legal_itinerary  # noqa: E402


# ============================================================
# 共用 fixture
# ============================================================


def _pin(kind: str = "poi", target_id: str = "P999", name: str = "梦幻美术馆") -> dict:
    return {"kind": kind, "target_id": target_id, "name": name}


def _seed_room(owner_id: str) -> tuple[RoomManager, Any]:
    manager = RoomManager()
    room = manager.create_room(owner_id=owner_id, nickname="发起人")
    room.current_intent_dict = _make_intent().model_dump()
    room.current_itinerary_dict = _make_legal_itinerary().model_dump()
    return manager, room


class _FakeEvent:
    """`run_graph_resume_stream` 产物的最小替身（room 只消费 .type.value /
    .payload / .model_dump()）。"""

    def __init__(self, etype: str, payload: Any) -> None:
        self.type = types.SimpleNamespace(value=etype)
        self.payload = payload

    def model_dump(self) -> dict[str, Any]:
        return {"type": self.type.value, "seq": 0, "payload": self.payload, "timestamp_ms": 0}


def _mid_nodes(itin_dict: dict) -> list[dict]:
    return [n for n in itin_dict["nodes"] if n.get("target_kind") != "home"]


# ============================================================
# 1. critic 硬判据：check_pinned_presence
# ============================================================


def test_validate_itinerary_missing_pin_is_hard_violation():
    """锁定实体缺席 → PINNED_ENTITY_MISSING HARD 违规，message 含实体名、
    不外露内部 id（消息纪律）。"""
    from agent.planning.critic._rules.types import Severity, ViolationCode
    from agent.planning.critic.critics_v2 import validate_itinerary

    itin = _make_legal_itinerary()  # 含 P040 + R001，不含 P999
    violations = validate_itinerary(
        itin, _make_intent(), pinned=[_pin(target_id="P999", name="梦幻美术馆")]
    )
    hits = [v for v in violations if v.code == ViolationCode.PINNED_ENTITY_MISSING]
    assert len(hits) == 1, f"锁定实体缺席应产出恰好一条违规，实际={[v.code for v in violations]}"
    v = hits[0]
    assert v.severity == Severity.HARD, "pinned 在场是硬判据——必须驱动 backprompt/修复阶梯"
    assert "梦幻美术馆" in v.message, "message 必须点名（自包含人话纪律）"
    assert "P999" not in v.message, "内部 id 不外露（Violation.message 既有纪律）"


def test_validate_itinerary_present_pin_no_violation():
    from agent.planning.critic._rules.types import ViolationCode
    from agent.planning.critic.critics_v2 import validate_itinerary

    itin = _make_legal_itinerary()
    violations = validate_itinerary(
        itin, _make_intent(), pinned=[_pin(target_id="P040", name="童趣海洋亲子馆")]
    )
    assert not [v for v in violations if v.code == ViolationCode.PINNED_ENTITY_MISSING]


def test_validate_itinerary_without_pinned_unchanged():
    """单人路径零变化：不传 pinned（缺省）→ 绝不产出 pinned 违规。"""
    from agent.planning.critic._rules.types import ViolationCode
    from agent.planning.critic.critics_v2 import validate_itinerary

    violations = validate_itinerary(_make_legal_itinerary(), _make_intent())
    assert not [v for v in violations if v.code == ViolationCode.PINNED_ENTITY_MISSING]


def test_critic_node_reads_pinned_targets_from_state():
    """critic_node 把 state.pinned_targets 喂进 validate_itinerary：缺席 →
    has_critical=True 且 backprompt 文本点名；无 pin → 合法方案照常通过。"""
    from agent.graph.nodes.critic import critic_node

    base_state = {
        "intent": _make_intent(),
        "itinerary": _make_legal_itinerary(),
        "pois": [],
        "restaurants": [],
    }

    clean = critic_node(dict(base_state))
    assert clean["has_critical"] is False, "前置：无 pin 时该 fixture 方案必须通过"

    out = critic_node({**base_state, "pinned_targets": [_pin(name="梦幻美术馆")]})
    assert out["has_critical"] is True
    assert "梦幻美术馆" in (out["critic_feedback_text"] or ""), (
        "backprompt 文本必须点名锁定实体，蓝图 LLM 才知道『必须保留谁』"
    )


# ============================================================
# 2. state 生命周期：pinned_targets 归 EPISODE_SCOPED
# ============================================================


def test_pinned_targets_registered_episode_scoped():
    from agent.graph.state import EPISODE_SCOPED, reset_for_new_episode

    assert "pinned_targets" in EPISODE_SCOPED, (
        "pinned 绑定『这一次重排事件』：反馈轮由房间重新注入、新需求由 intent_node 清零"
    )
    assert reset_for_new_episode()["pinned_targets"] == []


# ============================================================
# 3. 蓝图用户消息先验（第一次就告诉 LLM，不白烧一轮 backprompt）
# ============================================================


def test_build_user_message_renders_pinned_section():
    from agent.planning.blueprint.prompts.blueprint_prompt import build_user_message

    msg = build_user_message(
        intent_json="{}",
        candidates_json="{}",
        pinned=[_pin(target_id="P040", name="童趣海洋亲子馆")],
    )
    assert "必须保留" in msg
    assert "童趣海洋亲子馆" in msg
    assert "P040" in msg, "蓝图 LLM 按 target_id 引用候选，id 必须给到（候选 JSON 一侧，非系统提示）"


def test_build_user_message_without_pinned_unchanged():
    from agent.planning.blueprint.prompts.blueprint_prompt import build_user_message

    msg = build_user_message(intent_json="{}", candidates_json="{}")
    assert "必须保留" not in msg, "无 pin 时用户消息零变化（单人路径现状）"


def test_planner_node_passes_pinned_to_generate_blueprint(monkeypatch):
    import agent.graph.nodes.planner as planner_mod
    from data.loader import load_pois, load_restaurants

    captured: dict[str, Any] = {}

    def fake_generate_blueprint(intent, pois, restaurants, **kwargs):
        captured.update(kwargs)
        return None  # blueprint=None → replan_router 兜底路径，本测试不关心

    monkeypatch.setattr(planner_mod, "generate_blueprint", fake_generate_blueprint)

    pinned = [_pin(target_id="P040", name="童趣海洋亲子馆")]
    planner_mod.planner_node(
        {
            "intent": _make_intent(),
            "pois": load_pois()[:3],
            "restaurants": load_restaurants()[:3],
            "pinned_targets": pinned,
        }
    )
    assert captured.get("pinned") == pinned, (
        f"planner_node 必须把 state.pinned_targets 透传给 generate_blueprint，实际={captured}"
    )


# ============================================================
# 4. ILS 阶梯：PinSpec 透传 + rule 地板/give_up 的诚实告知
# ============================================================


def test_ils_replan_passes_pinspecs_to_plan_hybrid(monkeypatch):
    import agent.planning.planners.ils_planner as ils_mod
    from agent.graph.nodes.replan import ils_replan_node
    from schemas.pin import PinSpec

    captured: dict[str, Any] = {}

    def fake_plan_hybrid(intent, **kwargs):
        captured.update(kwargs)
        return ils_mod.HybridResult(success=True, itinerary=_make_legal_itinerary())

    monkeypatch.setattr(ils_mod, "plan_hybrid", fake_plan_hybrid)

    out = ils_replan_node(
        {
            "intent": _make_intent(),
            "pinned_targets": [
                _pin(target_id="P040", name="童趣海洋亲子馆"),
                _pin(kind="restaurant", target_id="R001", name="轻语沙拉"),
                {"kind": "home", "target_id": "home", "name": "怪值防御"},  # 非法 kind 应被跳过
            ],
        }
    )
    assert captured.get("pinned") == [
        PinSpec(kind="poi", target_id="P040"),
        PinSpec(kind="restaurant", target_id="R001"),
    ], f"ils_replan_node 必须把 pinned_targets 构造成 PinSpec 传入 plan_hybrid，实际={captured.get('pinned')}"
    assert out.get("itinerary") is not None


def test_ils_replan_without_pins_passes_none(monkeypatch):
    """单人路径零变化：无 pinned_targets → plan_hybrid(pinned=None)（缺省语义）。"""
    import agent.planning.planners.ils_planner as ils_mod
    from agent.graph.nodes.replan import ils_replan_node

    captured: dict[str, Any] = {}

    def fake_plan_hybrid(intent, **kwargs):
        captured.update(kwargs)
        return ils_mod.HybridResult(success=True, itinerary=_make_legal_itinerary())

    monkeypatch.setattr(ils_mod, "plan_hybrid", fake_plan_hybrid)
    ils_replan_node({"intent": _make_intent()})
    assert captured.get("pinned") is None


def test_ils_replan_rule_floor_missing_pin_produces_advisory(monkeypatch):
    """ILS 失败落 rule 地板（rule planner 不支持 pinned）：锁定实体缺席时必须
    补产 PINNED_UNSATISFIABLE advisory——绝不静默丢锁（L0）。"""
    import agent.planning.planners.ils_planner as ils_mod
    import agent.planning.planners.rule_planner as rule_mod
    from agent.graph.nodes.replan import ils_replan_node

    def failing_plan_hybrid(intent, **kwargs):
        return ils_mod.HybridResult(success=False)

    monkeypatch.setattr(ils_mod, "plan_hybrid", failing_plan_hybrid)
    monkeypatch.setattr(
        rule_mod,
        "plan_itinerary",
        lambda intent, tracer=None: types.SimpleNamespace(
            success=True, itinerary=_make_legal_itinerary(), failure_reason=None
        ),
    )

    out = ils_replan_node(
        {
            "intent": _make_intent(),
            "pinned_targets": [_pin(target_id="P999", name="梦幻美术馆")],
        }
    )
    advisories = out.get("advisories") or []
    hit = [a for a in advisories if a.get("code") == "pinned_unsatisfiable"]
    assert hit, f"rule 地板丢锁必须产 advisory，实际 advisories={advisories}"
    assert "梦幻美术馆" in hit[0]["message"]

    # 对照：pin 在场 → 不产 pinned advisory
    out2 = ils_replan_node(
        {
            "intent": _make_intent(),
            "pinned_targets": [_pin(target_id="P040", name="童趣海洋亲子馆")],
        }
    )
    assert not [a for a in (out2.get("advisories") or []) if a.get("code") == "pinned_unsatisfiable"]


def test_ils_replan_give_up_missing_pin_produces_advisory(monkeypatch):
    """ILS + rule 全灭（give_up，保留 state 里上一轮不完美方案）：锁定实体缺席
    同样必须补产 advisory。"""
    import agent.planning.planners.ils_planner as ils_mod
    import agent.planning.planners.rule_planner as rule_mod
    from agent.graph.nodes.replan import ils_replan_node
    from schemas.errors import FailureReason

    monkeypatch.setattr(
        ils_mod, "plan_hybrid", lambda intent, **kw: ils_mod.HybridResult(success=False)
    )
    monkeypatch.setattr(
        rule_mod,
        "plan_itinerary",
        lambda intent, tracer=None: types.SimpleNamespace(
            success=False, itinerary=None, failure_reason=FailureReason.EMPTY_CANDIDATES
        ),
    )

    out = ils_replan_node(
        {
            "intent": _make_intent(),
            "itinerary": _make_legal_itinerary(),  # 上一轮 backprompt 留下的方案（缺 pin）
            "pinned_targets": [_pin(target_id="P999", name="梦幻美术馆")],
        }
    )
    advisories = out.get("advisories") or []
    assert [a for a in advisories if a.get("code") == "pinned_unsatisfiable"], (
        f"give_up 分支丢锁也不许静默，实际={advisories}"
    )


# ============================================================
# 5. 房间侧：锁登记（归名）→ 注入 → 出口检查 → 重投影
# ============================================================


def test_update_vote_like_registers_locked_target_with_attribution():
    async def scenario():
        manager, room = _seed_room("owner_lock_reg_test")
        await manager.update_vote(room, "owner_lock_reg_test", 0, "like")

        assert "P040" in room.locked_targets, f"点赞应登记实体级锁，实际={room.locked_targets}"
        entry = room.locked_targets["P040"]
        assert entry["kind"] == "poi"
        assert entry["lockers"] == ["owner_lock_reg_test"], "锁必须归名（谁赞的）"
        assert room.locked_stages == {0}, "展示投影语义保持"

        # 第二人赞同一段 → lockers 追加，不重复
        room.members["member_b"] = type(room.members["owner_lock_reg_test"])(
            user_id="member_b", nickname="阿B", role="participant"
        )
        await manager.update_vote(room, "member_b", 0, "like")
        assert room.locked_targets["P040"]["lockers"] == ["owner_lock_reg_test", "member_b"]

    asyncio.run(scenario())


def test_update_vote_dislike_unregisters_locked_target():
    async def scenario():
        manager, room = _seed_room("owner_lock_unreg_test")
        await manager.update_vote(room, "owner_lock_unreg_test", 1, "like")
        assert "R001" in room.locked_targets

        await manager.update_vote(room, "owner_lock_unreg_test", 1, "dislike")
        assert "R001" not in room.locked_targets, "踩解锁本段——实体登记同步移除"
        assert 1 not in room.locked_stages

    asyncio.run(scenario())


def test_replan_injects_pinned_targets_and_guarantees_lock_or_honest_loss():
    """端到端（stub 全链路）：赞锁定 P040 → 反馈重排 → 图状态里 pinned_targets
    注入成功（serde 安全 plain dict）；且 L0 不变量成立——锁定实体要么在新方案
    里，要么房间广播了归名的『保不住』告知，绝无静默丢锁。"""
    owner_id = "owner_lock_inject_test"
    manager, room = _seed_room(owner_id)

    async def scenario():
        await manager.update_vote(room, owner_id, 0, "like")
        await manager.add_constraint(room, owner_id, "太贵了")
        if room.planning_task is not None:
            await room.planning_task

        from agent.graph.build import get_compiled_graph

        graph = get_compiled_graph()
        snap = await graph.aget_state(
            {"configurable": {"thread_id": f"collab_{room.room_id}"}}
        )
        return dict(snap.values)

    vals = asyncio.run(scenario())

    injected = vals.get("pinned_targets")
    assert injected and injected[0]["target_id"] == "P040", (
        f"图状态应带上锁定清单（plain dict，serde 白名单外零风险），实际={injected}"
    )
    assert isinstance(injected[0], dict) and set(injected[0]) == {"kind", "target_id", "name"}

    final_ids = [n["target_id"] for n in _mid_nodes(room.current_itinerary_dict)]
    if "P040" not in final_ids:
        narrations = [
            e for e in room.planning_events_history
            if (e["type"] if isinstance(e["type"], str) else e["type"].value) == "agent_narration"
            and "锁定" in str(e.get("payload", {}).get("text", ""))
        ]
        assert narrations, (
            "锁定实体没保住时必须有归名告知广播（L0 绝不默默忽略），"
            f"最终节点={final_ids}，事件={[e['type'] for e in room.planning_events_history]}"
        )


def test_room_exit_check_broadcasts_attributed_loss_and_reprojects(monkeypatch):
    """出口检查（合成续跑流，确定性）：新方案丢了锁定实体 → 广播含归名昵称 +
    实体名的告知；locked_targets/locked_stages 同步收敛（不再指向不存在的实体）。"""
    owner_id = "owner_lock_exit_test"
    manager, room = _seed_room(owner_id)

    async def scenario():
        await manager.update_vote(room, owner_id, 0, "like")  # 锁 P040（阿A=发起人）
        assert "P040" in room.locked_targets

        import agent.graph.sse_adapter as sse_adapter
        from schemas.advisory import AdvisoryCode

        lost_plan = _make_legal_itinerary(poi_id="P100").model_dump()  # P040 被换掉

        async def fake_resume(**kwargs):
            yield _FakeEvent("itinerary_ready", lost_plan)
            # 引擎侧 advisory 经房间广播路径是 model_dump()（python mode）——
            # code 是活的 AdvisoryCode 枚举实例，不是 plain str（与 build.py
            # serde 白名单注释记载的 LedgerEntry 同款现象）；出口检查的原因
            # 匹配必须按 .value 归一化，这里刻意喂枚举钉住。
            yield _FakeEvent(
                "agent_narration",
                {
                    "text": "方案说明",
                    "stage": "stream",
                    "messages": [
                        {
                            "kind": "advisory",
                            "code": AdvisoryCode.PINNED_UNSATISFIABLE,
                            "text": "点赞锁定的「P040」这轮实在没能排进方案……",
                        }
                    ],
                },
            )
            yield _FakeEvent("done", {})

        monkeypatch.setattr(sse_adapter, "run_graph_resume_stream", fake_resume)
        await manager._replan_with_refiner(room, "太贵了")

    asyncio.run(scenario())

    texts = [
        str(e.get("payload", {}).get("text", ""))
        for e in room.planning_events_history
        if (e["type"] if isinstance(e["type"], str) else e["type"].value) == "agent_narration"
    ]
    loss_msgs = [t for t in texts if "锁定" in t and "发起人" in t]
    assert loss_msgs, f"丢锁必须归名告知（『发起人锁定的…没保住』），实际叙述={texts}"
    assert "时间和路线里实在塞不进" in loss_msgs[0], (
        f"归名告知应转述引擎 advisory 的原因（code 按 .value 归一化匹配，枚举/字符串都认），实际={loss_msgs[0]}"
    )

    assert "P040" not in room.locked_targets, "锁应随实体消失而收敛（不再指向不存在的段）"
    assert room.locked_stages == set(), f"展示投影应同步清空，实际={room.locked_stages}"


def test_room_exit_check_remaps_surviving_lock_silently(monkeypatch):
    """出口检查（合成续跑流）：锁定实体保住了但换了位置 → 不产告知，
    locked_stages 重投影到新下标（下一轮反馈的翻译才不会锁错人）。"""
    owner_id = "owner_lock_remap_test"
    manager, room = _seed_room(owner_id)

    async def scenario():
        await manager.update_vote(room, owner_id, 0, "like")  # 锁 P040（原 stage 0）

        import agent.graph.sse_adapter as sse_adapter

        base = _make_legal_itinerary().model_dump()
        mids = _mid_nodes(base)
        # 反序：R001 在前、P040 在后 → P040 新下标 = 1
        reordered = {**base, "nodes": [base["nodes"][0], mids[1], mids[0], base["nodes"][-1]]}

        async def fake_resume(**kwargs):
            yield _FakeEvent("itinerary_ready", reordered)
            yield _FakeEvent("done", {})

        monkeypatch.setattr(sse_adapter, "run_graph_resume_stream", fake_resume)
        await manager._replan_with_refiner(room, "太贵了")

    asyncio.run(scenario())

    texts = [
        str(e.get("payload", {}).get("text", ""))
        for e in room.planning_events_history
        if (e["type"] if isinstance(e["type"], str) else e["type"].value) == "agent_narration"
    ]
    assert not [t for t in texts if "锁定" in t and "没保住" in t], "锁保住了不该谎报丢失"
    assert "P040" in room.locked_targets
    assert room.locked_stages == {1}, f"投影应重排到新下标，实际={room.locked_stages}"


def test_fresh_plan_clears_locks():
    """planning 义务重开一局：旧方案的锁随旧方案作废（陈旧下标指向新方案是
    翻译错人的正确性隐患，不是保守选择）。"""
    owner_id = "owner_lock_fresh_test"
    manager, room = _seed_room(owner_id)

    async def scenario():
        await manager.update_vote(room, owner_id, 0, "like")
        assert room.locked_targets

        async def noop_fresh(room_, user_input):
            return None

        manager._plan_fresh = noop_fresh  # 不真跑规划，只验证触发时清锁
        await manager._trigger_fresh_plan(room, trigger_user=owner_id, user_input="重新规划一个")
        if room.planning_task is not None:
            await room.planning_task

    asyncio.run(scenario())
    assert room.locked_targets == {}
    assert room.locked_stages == set()


def test_adjust_success_drops_lock_of_swapped_entity(monkeypatch):
    """联动：锁定的实体被成员显式换菜换走（人对人的公开动作，非引擎静默行为）
    → 实体级锁随实体消失收敛，投影同步。

    换菜引擎本体（resolve_node_swap）打桩返回确定性成功结果——本测试钉的是
    房间侧"换菜成功后锁收敛"这一跳，不复测引擎语义（引擎另有并行批在改硬
    约束判据，真跑会把本测试绑上它的中间态）。
    """
    owner_id = "owner_lock_adjust_test"
    manager, room = _seed_room(owner_id)

    async def scenario():
        import agent.planning.planners.node_swap as node_swap_mod
        from api._streams.models import AdjustActionDislike

        new_itin = _make_legal_itinerary(poi_id="P100")  # P040 被换成 P100

        def fake_resolve(*args, **kwargs):
            return types.SimpleNamespace(
                success=True,
                new_itinerary=new_itin,
                swapped_to="P100",
                degrade_tier=1,
                advisories=[],
            )

        monkeypatch.setattr(node_swap_mod, "resolve_node_swap", fake_resolve)

        await manager.update_vote(room, owner_id, 0, "like")
        assert "P040" in room.locked_targets
        await manager.adjust(room, owner_id, "P040", AdjustActionDislike())

    asyncio.run(scenario())

    final_ids = [n["target_id"] for n in _mid_nodes(room.current_itinerary_dict)]
    assert "P040" not in final_ids, "前置：换菜应真的把 P040 换掉"
    assert "P040" not in room.locked_targets
    assert 0 not in room.locked_stages
