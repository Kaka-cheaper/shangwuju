"""spike_room_resume —— 方案 d（LangGraph 原生状态注入 + 续跑）可行性实验。

【spike，不进生产、不提交】验证外部评审推荐的方案 d：对房间维护的持久 graph
会话，用 `graph.aupdate_state(config, values, as_node="refiner")` 把"反馈已合并"
的状态写进去，再 `graph.astream(None, config)` 从 refiner 出边续跑（不重新执行
refiner，避免 refine_intent 双重合并）。

实验设计（金标准 vs 复刻）：
- GOLD    会话：turn1 规划（run_graph_stream 生产入口）→ turn2 反馈（同一生产
          入口，Layer 1 强信号确定性路由到 refiner）→ 抓事件序列 + 终态快照。
          这是"正常单人反馈轮"的金标准。
- VAL     会话：turn1 同 GOLD；turn2 用本脚本的镜像驱动器（drive，与
          sse_adapter.run_graph_stream 逐行同构、只是额外记录节点顺序）跑同一
          反馈。用途有二：① 验证镜像驱动器与生产入口产出一致（否则 REPLAY 的
          对比不可信）；② 验证同输入跨会话确定性（诊断方法学的前提）。
- REPLAY  会话：turn1 同 GOLD；turn2 不走图入口——aupdate_state(as_node=
          "refiner") 注入"refiner 已跑完"的 values，astream(None) 续跑。
- 若 GOLD ≡ VAL（确定性成立），则 GOLD vs REPLAY 的 diff 就是"方案 d 与正常
  反馈轮的真实行为差异"，不掺镜像驱动器 / 非确定性的噪声。

另有三个独立探针：
- serde 正向探针：注入的 IntentExtraction（在 build.py serde 白名单内）能否
  经 InMemorySaver 序列化往返仍是活对象。
- serde 反向探针：注入白名单外的 Pydantic 类型（RefinementOutput）会发生什么。
- dict-intent 探针：把 intent 以 model_dump() dict 注入（而非活对象）续跑，
  下游行为如何劣化——回答"需不需要先 model_dump"（预期答案：不能 dump）。

以及 Q5：REPLAY 会话上第二次注入+续跑（连续两条反馈），验证版本志 /
诉求台账（demand_ledger，SESSION_SCOPED 带 merge）/ messages 的累积。

运行：LLM_PROVIDER=stub（本脚本强制设置），零真实 LLM 调用。
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
import warnings
from enum import Enum
from pathlib import Path
from typing import Any

# ============================================================
# 环境：路径 / stub LLM / mock 数据副本（绝不写仓库真目录）
# ============================================================

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

os.environ["LLM_PROVIDER"] = "stub"

_scratch = os.environ.get("SPIKE_SCRATCH") or tempfile.mkdtemp(prefix="spike_room_resume_")
Path(_scratch).mkdir(parents=True, exist_ok=True)
_mock_copy = Path(_scratch) / "mock_data_copy"
if not _mock_copy.exists():
    shutil.copytree(REPO_ROOT / "mock_data", _mock_copy)
os.environ["SHANGWUJU_MOCK_DIR"] = str(_mock_copy)

# stdout 强制 UTF-8（Windows 控制台 cp936 防乱码/防写崩）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ============================================================
# 警告捕获（serde 未注册类型告警 / Blocked deserialization 都走 warnings）
# ============================================================

CAPTURED_WARNINGS: list[str] = []


def _capture_warning(message, category, filename, lineno, file=None, line=None):  # noqa: ANN001
    CAPTURED_WARNINGS.append(f"{category.__name__}: {message} @ {Path(str(filename)).name}:{lineno}")


warnings.showwarning = _capture_warning
warnings.simplefilter("always")

# ============================================================
# 真实业务 import（与 tests/conftest.py 同款：先注册真 Tool）
# ============================================================

import tools as _real_tools  # noqa: E402,F401 —— 副作用注册 TOOL_REGISTRY

from langchain_core.messages import BaseMessage, HumanMessage  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent.graph._emit_context import EmitContext, now_ms  # noqa: E402
from agent.graph import _emit_handlers as EH  # noqa: E402
from agent.graph.build import get_compiled_graph  # noqa: E402
from agent.graph.nodes.refiner import refiner_node  # noqa: E402
from agent.graph.sse_adapter import run_graph_stream  # noqa: E402
from agent.graph.state import make_initial_state  # noqa: E402
from agent.routing.canonical_shortcut import DEMO_SCENARIOS  # noqa: E402
from schemas.intent import IntentExtraction  # noqa: E402
from schemas.sse import SseEvent, SseEventType  # noqa: E402

# ============================================================
# 常量
# ============================================================

PLANNING_INPUT = DEMO_SCENARIOS[1]["input"]  # 壳2 canonical 短路 → 确定性 planning
FEEDBACK_1 = "太远了，近一点"  # Layer 1 强信号（"太远"）→ 确定性 feedback，不依赖 stub 脑子
FEEDBACK_2 = "太贵了，便宜点"  # 第二条反馈（Q5 连续注入），同为强信号（"太贵"）

GOLD = "spike_gold"
VAL = "spike_validate"
REPLAY = "spike_replay"
DICT_PROBE = "spike_dict_probe"
SERDE_PROBE = "spike_serde_probe"

_FANOUT = {"search_pois_worker", "search_restaurants_worker", "get_user_profile_worker"}

REPORT_LINES: list[str] = []


def rprint(*args: Any) -> None:
    line = " ".join(str(a) for a in args)
    REPORT_LINES.append(line)
    print(line)


# ============================================================
# 归一化 + diff（对比用；剔除时间戳等易变字段，保留业务字段）
# ============================================================

_DROP_KEYS = {"timestamp", "timestamp_ms", "created_at", "total_ms", "duration_ms"}


def norm(obj: Any) -> Any:
    if isinstance(obj, BaseMessage):
        return {"__msg__": obj.type, "content": obj.content}
    if isinstance(obj, BaseModel):
        return norm(obj.model_dump(mode="python"))
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): norm(v) for k, v in obj.items() if str(k) not in _DROP_KEYS}
    if isinstance(obj, (list, tuple)):
        return [norm(x) for x in obj]
    if isinstance(obj, float):
        return round(obj, 6)
    return obj


def _trunc(v: Any, n: int = 110) -> str:
    s = repr(v)
    return s if len(s) <= n else s[: n] + "…"


def diff_paths(a: Any, b: Any, path: str = "", out: list[str] | None = None, limit: int = 120) -> list[str]:
    """左=GOLD，右=对照。产出差异路径列表（供人读）。"""
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            if k not in a:
                out.append(f"{path}.{k}: 仅右侧有 = {_trunc(b[k])}")
            elif k not in b:
                out.append(f"{path}.{k}: 仅左侧有 = {_trunc(a[k])}")
            else:
                diff_paths(a[k], b[k], f"{path}.{k}", out, limit)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: 列表长度 {len(a)} vs {len(b)}")
        for i in range(min(len(a), len(b))):
            diff_paths(a[i], b[i], f"{path}[{i}]", out, limit)
        return out
    if a != b:
        out.append(f"{path}: {_trunc(a)} != {_trunc(b)}")
    return out


def norm_events(events: list[SseEvent]) -> list[tuple[str, Any]]:
    return [(e.type.value, norm(e.payload)) for e in events]


def event_types(events: list[SseEvent]) -> list[str]:
    return [e.type.value for e in events]


# ============================================================
# 镜像驱动器（与 sse_adapter.run_graph_stream 的 dispatch 逐行同构；
# 差异仅：① 允许 initial=None（续跑）② 额外记录节点顺序 ③ 返回而非 yield）
# ============================================================


async def drive(graph: Any, initial: Any, config: dict, user_input: str):
    ctx = EmitContext()
    events: list[SseEvent] = [
        ctx.emit(SseEventType.AGENT_THOUGHT, {"text": "正在理解你的需求……"})
    ]
    node_order: list[str] = []
    error: str | None = None
    try:
        async for chunk in graph.astream(initial, config=config, stream_mode="updates"):
            for node_name, node_diff in chunk.items():
                if node_diff is None:
                    continue
                node_order.append(node_name)
                if node_name == "router":
                    evs = EH.emit_router(ctx, node_diff, user_input)
                elif node_name == "intent":
                    evs = EH.emit_intent(ctx, node_diff)
                elif node_name == "refiner":
                    evs = EH.emit_refiner(ctx, node_diff)
                elif node_name in _FANOUT:
                    evs = EH.emit_fanout_worker(ctx, node_name, node_diff)
                elif node_name == "planner":
                    evs = EH.emit_planner(ctx, node_diff)
                elif node_name == "critic":
                    evs = EH.emit_critic(ctx, node_diff)
                elif node_name == "replan_router":
                    evs = EH.emit_replan_router(ctx, node_diff)
                elif node_name == "ils_replan":
                    evs = EH.emit_ils_replan(ctx, node_diff)
                elif node_name == "assemble":
                    evs = EH.emit_assemble(ctx, node_diff)
                elif node_name == "finalize_plan":
                    evs = EH.emit_finalize_plan(ctx, node_diff)
                elif node_name == "narrate":
                    evs = EH.emit_narrate(ctx, node_diff)
                else:
                    evs = []
                events.extend(evs)
                ctx.update_accum_from_diff(node_diff)
    except Exception as e:  # noqa: BLE001
        import traceback

        error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
        events.append(
            ctx.emit(
                SseEventType.STREAM_ERROR,
                {"reason": "graph_execution_failed", "detail": str(error)[:500]},
            )
        )

    # DONE payload —— 与 run_graph_stream 末尾同构
    final_strategy = "llm_first"
    has_itinerary = ctx.final_itinerary is not None
    if ctx.final_itinerary is not None:
        trace = getattr(ctx.final_itinerary, "decision_trace", None)
        if trace is not None:
            final_strategy = getattr(trace, "final_strategy", "llm_first") or "llm_first"
            trace_chain = getattr(trace, "fallback_chain", None)
            if trace_chain:
                ctx.last_fallback_chain = list(trace_chain)
            trace_attempts = getattr(trace, "critic_attempts", None)
            if trace_attempts:
                ctx.last_critic_attempts = list(trace_attempts)
    events.append(
        ctx.emit(
            SseEventType.DONE,
            {
                "final_strategy": final_strategy,
                "plan_attempts": ctx.last_plan_attempt,
                "critic_attempt_count": len(ctx.last_critic_attempts),
                "fallback_hops_count": len(ctx.last_fallback_chain),
                "total_ms": now_ms() - ctx.start_ms,
                "has_itinerary": has_itinerary,
            },
        )
    )
    return events, node_order, error


async def run_stream_collect(user_input: str, session_id: str) -> list[SseEvent]:
    evs: list[SseEvent] = []
    async for ev in run_graph_stream(
        user_input=user_input, session_id=session_id, user_id="demo_user"
    ):
        evs.append(ev)
    return evs


# ============================================================
# 注入配方（方案 d 的核心待验证物）
# ============================================================


def build_injection_values(snapshot_values: dict, feedback_text: str) -> dict[str, Any]:
    """复刻"router 判 feedback + refiner 跑完"两个节点合起来对 state 的全部写入。

    - refiner 部分：直接调真实 refiner_node（喂 checkpoint values + 本轮
      user_input/messages 叠层），拿到与图内执行完全一致的 diff（reset_for_new_
      episode 全集 + refined intent + refinement_changed_fields/note）。
    - router 部分：route_kind="feedback" / router_decision=None（Layer 1 强信号
      路径 decision 本就是 None）/ messages 追加 HumanMessage（会话日志）。
    - make_initial_state 部分：user_input=反馈原话（finalize_plan 的版本志
      snippet 读它）。chitchat_* 三键不写（房间会话不会有图内 chitchat 轮写过
      它们；若有陈旧值，反馈链路也无读者——终态 diff 会验证这一判断）。
    """
    base = dict(snapshot_values)
    base["user_input"] = feedback_text
    base["messages"] = list(base.get("messages") or []) + [HumanMessage(content=feedback_text)]
    refiner_diff = refiner_node(base)
    return {
        **refiner_diff,
        "user_input": feedback_text,
        "route_kind": "feedback",
        "router_decision": None,
        "messages": [HumanMessage(content=feedback_text)],
    }


async def inject_and_resume(graph: Any, config: dict, feedback_text: str, *, mutate_values=None):
    snap = await graph.aget_state(config)
    values = build_injection_values(dict(snap.values), feedback_text)
    if mutate_values is not None:
        values = mutate_values(values)
    await graph.aupdate_state(config, values, as_node="refiner")
    after = await graph.aget_state(config)
    next_nodes = tuple(after.next)
    injected_intent = after.values.get("intent")
    events, node_order, error = await drive(graph, None, config, feedback_text)
    return {
        "values_keys": sorted(values.keys()),
        "next_after_update": next_nodes,
        "intent_type_after_roundtrip": type(injected_intent).__name__,
        "events": events,
        "node_order": node_order,
        "error": error,
    }


# ============================================================
# 主实验
# ============================================================


async def main() -> None:
    import importlib.metadata as md

    graph = get_compiled_graph()

    rprint("=" * 78)
    rprint("spike_room_resume —— 方案 d（aupdate_state as_node='refiner' + astream(None)）")
    rprint(
        f"langgraph={md.version('langgraph')} "
        f"langgraph-checkpoint={md.version('langgraph-checkpoint')} "
        f"LLM_PROVIDER={os.environ['LLM_PROVIDER']}"
    )
    rprint(f"planning 输入: {PLANNING_INPUT!r}")
    rprint(f"feedback-1: {FEEDBACK_1!r}   feedback-2: {FEEDBACK_2!r}")
    rprint("=" * 78)

    # ---------- GOLD：生产入口两轮 ----------
    gold_t1 = await run_stream_collect(PLANNING_INPUT, GOLD)
    gold_t2 = await run_stream_collect(FEEDBACK_1, GOLD)
    gold_snap = await graph.aget_state({"configurable": {"thread_id": GOLD}})

    # ---------- VAL：turn1 生产入口，turn2 镜像驱动器（验证驱动器 + 确定性） ----------
    await run_stream_collect(PLANNING_INPUT, VAL)
    val_cfg = {"configurable": {"thread_id": VAL}}
    val_initial = make_initial_state(
        user_input=FEEDBACK_1, session_id=VAL, user_id="demo_user"
    )
    val_events, val_nodes, val_err = await drive(graph, val_initial, val_cfg, FEEDBACK_1)
    val_snap = await graph.aget_state(val_cfg)

    # ---------- REPLAY：turn1 生产入口，turn2 注入 + 续跑 ----------
    await run_stream_collect(PLANNING_INPUT, REPLAY)
    replay_cfg = {"configurable": {"thread_id": REPLAY}}
    r1 = await inject_and_resume(graph, replay_cfg, FEEDBACK_1)
    replay_snap = await graph.aget_state(replay_cfg)

    # ---------- 报告：Q0 方法学前提（驱动器可信 + 确定性） ----------
    rprint("\n【Q0 方法学前提】")
    drv_ok = event_types(val_events) == event_types(gold_t2)
    rprint(f"- 镜像驱动器事件类型序列 == 生产 run_graph_stream: {drv_ok}")
    if not drv_ok:
        rprint(f"  gold: {event_types(gold_t2)}")
        rprint(f"  val : {event_types(val_events)}")
    drv_payload_diff = diff_paths(norm_events(gold_t2), norm_events(val_events))
    rprint(f"- 镜像驱动器事件 payload diff 条数: {len(drv_payload_diff)}")
    for d in drv_payload_diff[:8]:
        rprint(f"    {d}")
    det_diff = diff_paths(norm(dict(gold_snap.values)), norm(dict(val_snap.values)))
    rprint(f"- 跨会话确定性（GOLD vs VAL 终态 state diff 条数）: {len(det_diff)}")
    for d in det_diff[:12]:
        rprint(f"    {d}")
    if val_err:
        rprint(f"- VAL 驱动器异常: {val_err}")
    rprint(f"- 正常反馈轮节点执行顺序（VAL 实测）: {val_nodes}")

    # ---------- Q1：续跑语义 ----------
    rprint("\n【Q1 续跑语义】")
    rprint(f"- aupdate_state(as_node='refiner') 后 snapshot.next = {r1['next_after_update']}")
    rprint(f"- astream(None) 实际执行节点顺序: {r1['node_order']}")
    rprint(f"- refiner 是否被重新执行: {'refiner' in r1['node_order']}")
    rprint(f"- router 是否被执行: {'router' in r1['node_order']}")
    if r1["error"]:
        rprint(f"- 续跑异常: {r1['error']}")
    # checkpoint 元数据（注入产生的 source='update' 检查点证据）
    try:
        hist = []
        async for s in graph.aget_state_history(replay_cfg):
            hist.append((s.metadata.get("source"), s.metadata.get("step"), tuple(s.next)))
            if len(hist) >= 6:
                break
        rprint(f"- 最近 6 个 checkpoint (source, step, next)（新→旧）: {hist}")
    except Exception as e:  # noqa: BLE001
        rprint(f"- checkpoint history 读取失败（不影响主结论）: {type(e).__name__}: {e}")

    # ---------- Q2：终态 state diff（金标准 vs 复刻） ----------
    rprint("\n【Q2 终态 state 对比（GOLD vs REPLAY，按顶层键）】")
    rprint(f"- 注入 values 的键清单: {r1['values_keys']}")
    g_vals, r_vals = dict(gold_snap.values), dict(replay_snap.values)
    all_keys = sorted(set(g_vals) | set(r_vals))
    equal_keys, diff_keys = [], {}
    for k in all_keys:
        d = diff_paths(norm(g_vals.get(k)), norm(r_vals.get(k)), path=k)
        if d:
            diff_keys[k] = d
        else:
            equal_keys.append(k)
    rprint(f"- 完全一致的键（{len(equal_keys)}）: {equal_keys}")
    rprint(f"- 有差异的键（{len(diff_keys)}）:")
    for k, d in diff_keys.items():
        rprint(f"  * {k}: {len(d)} 处")
        for line in d[:10]:
            rprint(f"      {line}")

    # ---------- Q4：事件序列对比 ----------
    rprint("\n【Q4 SSE 事件序列对比（反馈轮）】")
    rprint(f"- GOLD  ({len(gold_t2)}): {event_types(gold_t2)}")
    rprint(f"- REPLAY({len(r1['events'])}): {event_types(r1['events'])}")
    from collections import Counter

    cg, cr = Counter(event_types(gold_t2)), Counter(event_types(r1["events"]))
    missing = {t: cg[t] - cr.get(t, 0) for t in cg if cg[t] > cr.get(t, 0)}
    extra = {t: cr[t] - cg.get(t, 0) for t in cr if cr[t] > cg.get(t, 0)}
    rprint(f"- REPLAY 缺少的事件（类型: 缺少条数）: {missing}")
    rprint(f"- REPLAY 多出的事件（类型: 多出条数）: {extra}")
    # 共有结构事件的 payload 对比（itinerary_ready / agent_narration / done）
    for etype in ("itinerary_ready", "agent_narration", "done"):
        ge = [e for e in gold_t2 if e.type.value == etype]
        re_ = [e for e in r1["events"] if e.type.value == etype]
        if ge and re_:
            d = diff_paths(norm(ge[0].payload), norm(re_[0].payload), path=etype)
            rprint(f"- {etype} payload diff 条数: {len(d)}")
            for line in d[:6]:
                rprint(f"      {line}")

    # ---------- Q3：serde ----------
    rprint("\n【Q3 serde（自定义 msgpack 白名单）】")
    rprint(
        f"- 注入活 IntentExtraction 经 checkpointer 往返后的类型: "
        f"{r1['intent_type_after_roundtrip']}（期望 IntentExtraction）"
    )
    final_intent = replay_snap.values.get("intent")
    rprint(f"- 续跑后终态 intent 类型: {type(final_intent).__name__}")

    # 反向探针：白名单外的 Pydantic 类型
    rprint("- 反向探针：注入白名单外类型（schemas.refine.RefinementOutput）……")
    await run_stream_collect(PLANNING_INPUT, SERDE_PROBE)
    probe_cfg = {"configurable": {"thread_id": SERDE_PROBE}}
    from schemas.refine import RefinementOutput

    probe_snap = await graph.aget_state(probe_cfg)
    probe_obj = RefinementOutput(
        refined_intent=probe_snap.values["intent"], changed_fields=[], refiner_note=None
    )
    n_warn_before = len(CAPTURED_WARNINGS)
    try:
        await graph.aupdate_state(
            probe_cfg, {"memory_status": {"probe": probe_obj}}, as_node="narrate"
        )
        back = await graph.aget_state(probe_cfg)
        got = (back.values.get("memory_status") or {}).get("probe")
        rprint(f"    aupdate_state 未抛异常；读回类型: {type(got).__name__}，值: {_trunc(got)}")
    except Exception as e:  # noqa: BLE001
        rprint(f"    抛异常: {type(e).__name__}: {str(e)[:200]}")
    new_warns = CAPTURED_WARNINGS[n_warn_before:]
    rprint(f"    期间新增 warnings（{len(new_warns)}）: {new_warns[:4]}")

    # ---------- dict-intent 探针（"要不要先 model_dump"的实证） ----------
    rprint("\n【Q3-附 dict-intent 探针：注入 model_dump() 后的 intent（而非活对象）】")
    await run_stream_collect(PLANNING_INPUT, DICT_PROBE)
    dict_cfg = {"configurable": {"thread_id": DICT_PROBE}}

    def _dump_intent(values: dict) -> dict:
        v = dict(values)
        v["intent"] = v["intent"].model_dump()
        return v

    rd = await inject_and_resume(graph, dict_cfg, FEEDBACK_1, mutate_values=_dump_intent)
    rprint(f"- 续跑节点顺序: {rd['node_order']}")
    rprint(f"- 事件类型序列: {event_types(rd['events'])}")
    rprint(f"- 异常: {rd['error']}")
    done_ev = [e for e in rd["events"] if e.type.value == "done"]
    if done_ev:
        rprint(f"- done payload: {norm(done_ev[0].payload)}")
    err_ev = [e for e in rd["events"] if e.type.value == "stream_error"]
    if err_ev:
        rprint(f"- stream_error payload: {norm(err_ev[0].payload)}")
    dict_snap = await graph.aget_state(dict_cfg)
    d_itin = dict_snap.values.get("itinerary")
    rprint(f"- 终态 itinerary: {'有' if d_itin is not None else '无'}；intent 类型: {type(dict_snap.values.get('intent')).__name__}")
    if d_itin is not None:
        gold_itin = gold_snap.values.get("itinerary")
        d = diff_paths(norm(gold_itin), norm(d_itin), path="itinerary")
        rprint(f"- 与 GOLD 终态 itinerary 的 diff 条数: {len(d)}（>0 即静默劣化证据）")
        for line in d[:6]:
            rprint(f"      {line}")

    # ---------- Q5：连续第二次注入 + 续跑（台账/版本志/messages 累积） ----------
    rprint("\n【Q5 连续两条反馈（REPLAY 会话第二次注入）】")
    # 先在两次注入之间种一条真实台账条目（房间换菜会写它；验证 SESSION_SCOPED
    # merge 函数在注入+续跑下不被冲掉）
    from schemas.demand_ledger import LedgerEntry, NodeRef
    from schemas.node_adjustment import NodeAdjustment, NodeAdjustmentDimension

    seed_entry = LedgerEntry(
        member_id="u_alice",
        nickname="小A",
        node_ref=NodeRef(kind="restaurant", target_id="r_spike_probe"),
        adjustment=NodeAdjustment(dimension=NodeAdjustmentDimension.PRICE, value="cheaper"),
        source_text="换个便宜点的",
    ).model_dump()
    await graph.aupdate_state(replay_cfg, {"demand_ledger": [seed_entry]}, as_node="narrate")

    r2 = await inject_and_resume(graph, replay_cfg, FEEDBACK_2)
    replay_snap2 = await graph.aget_state(replay_cfg)
    rprint(f"- 第二次注入后 next: {r2['next_after_update']}")
    rprint(f"- 第二次续跑节点顺序: {r2['node_order']}（refiner 在内: {'refiner' in r2['node_order']}）")
    if r2["error"]:
        rprint(f"- 第二次续跑异常: {r2['error']}")
    pvl = replay_snap2.values.get("plan_version_log") or []
    rprint(f"- plan_version_log（{len(pvl)} 条，期望 3 条 trigger=[first, feedback, feedback]）:")
    for e in pvl:
        rprint(f"    v{e.get('version_n')} trigger={e.get('trigger')} summary={e.get('summary')!r}")
    msgs = replay_snap2.values.get("messages") or []
    rprint(f"- messages 通道（{len(msgs)} 条）: {[(m.type, str(m.content)[:30]) for m in msgs]}")
    ledger = replay_snap2.values.get("demand_ledger") or []
    survived = any(d.get("member_id") == "u_alice" for d in ledger)
    rprint(f"- demand_ledger 种入条目在第二次注入+续跑后仍存活: {survived}（共 {len(ledger)} 条）")
    fi = replay_snap2.values.get("intent")
    rprint(f"- 第二轮反馈后 intent.raw_input: {getattr(fi, 'raw_input', None)!r}")
    rprint(f"- 第二轮反馈后 intent.distance_max_km: {getattr(fi, 'distance_max_km', None)}")

    # GOLD 侧同样跑第二条反馈（生产入口），对比双反馈终态
    gold_t3 = await run_stream_collect(FEEDBACK_2, GOLD)
    gold_snap3 = await graph.aget_state({"configurable": {"thread_id": GOLD}})
    g3, r3 = dict(gold_snap3.values), dict(replay_snap2.values)
    diff2 = {}
    for k in sorted(set(g3) | set(r3)):
        d = diff_paths(norm(g3.get(k)), norm(r3.get(k)), path=k)
        if d:
            diff2[k] = d
    rprint(f"- 双反馈后 GOLD vs REPLAY 有差异的键: {list(diff2.keys())}")
    for k, d in diff2.items():
        for line in d[:6]:
            rprint(f"      {line}")
    rprint(f"  （注：REPLAY 侧多了人工种入的台账条目，demand_ledger 差异属预期）")
    rprint(f"- GOLD 第二条反馈事件类型: {event_types(gold_t3)}")

    # ---------- 警告汇总 ----------
    rprint("\n【全程捕获的 warnings】")
    serde_warns = [w for w in CAPTURED_WARNINGS if "eserializ" in w or "msgpack" in w or "lock" in w]
    rprint(f"- 总数 {len(CAPTURED_WARNINGS)}；serde 相关 {len(serde_warns)}:")
    for w in serde_warns[:15]:
        rprint(f"    {w}")

    # ---------- 落盘 ----------
    report_path = Path(_scratch) / "spike_room_resume_report.txt"
    report_path.write_text("\n".join(REPORT_LINES), encoding="utf-8")
    print(f"\n[report saved] {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
