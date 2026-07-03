"""test_consumption_completeness —— ADR-0014 G-4 消费完备性 gate（SSE 与 Advisory 两轴）。

【这是什么问题】

全链路审查（ADR-0014 三轮拷问）挖出十个「器官长了血管没接」缺陷：后端某处产出了一个
字段/事件/码，却没有任何下游消费它（pace_profile / parse_confidence / ambiguous_
fields / gender_mix / relaxed_tags / 台账全局形态 / 3 个 SSE 事件 / decision_trace /
reject-modify / modifications）。根因不是某一次疏忽，而是**没有机制强制"每个生产者
有消费者"**——新增一个事件/码时，忘记接前端消费不会有任何信号，直到评委或用户在界面上
发现"这功能怎么没反应"才暴露。

本测试把 ADR-0012 决策 4 的生命周期完备性先例（见 test_state_lifecycle.py：
AgentState 每个字段必须在三个 frozenset 里登记，否则红）推广到消费侧：**每个
生产物必须有登记的消费者，或显式登记为"有意不消费"并附理由**——不许静默漏接。

本文件只做 G-4 前半，两条轴：

1. **SSE 事件轴**：`schemas.sse.SseEventType` 每个值，必须在
   `frontend/lib/store/event-handlers.ts` 的 `handleEvent` switch 里有对应
   `case`，或登记进 `SSE_NOT_CONSUMED_IN_SWITCH` 白名单（附理由）。
2. **AdvisoryCode 轴**：`schemas.advisory.AdvisoryCode` 每个码，必须在 backend
   里（`schemas/` 与 `tests/` 之外）至少有一处生产点，或登记进
   `ADVISORY_NOT_PRODUCED_OUTSIDE_SCHEMAS` 白名单（附理由）。
3. **反向轴（防幻觉）**：前端 switch 里的每个 case 字符串，必须能在后端
   `SseEventType` 枚举里找到——防止"前端写了个 case，但后端从来不会发这个
   事件"的死代码分支（长期维护会以为它在工作）。

留白（有意不做，非遗漏）：**IntentExtraction 字段轴**——G 系列并行改造中
`schemas/intent.py` 的字段集本身还在变（G-1 正在改 agent/intent/ 与
schemas/intent.py），此时登记字段消费点只会立刻过期。字段轴留到 G-3（字段集
稳定）之后单独补。本文件的 grep/断言全程不涉及 IntentExtraction 的任何字段名。

【实现手法】

- SSE 轴：不跑前端 JS/TS 构建链路（成本高、非本 gate 目的），改用纯文本正则
  从 `event-handlers.ts` 里提取 `case "xxx":` 的字符串字面量集合——容忍单/双
  引号、行内 `{` 等格式微调，但不做真正的 TS AST 解析（过度工程，收益不成
  比例；文件本身是手写的固定 switch，正则足够稳）。
- Advisory 轴：同样用纯文本 grep，在每个候选码上查「枚举成员名」
  （`AdvisoryCode.XXX`）或「枚举值字符串字面量」（`"xxx"` / `'xxx'`）是否
  出现在 schemas/ 与 tests/ 之外的 backend 源码里——任一命中即视为"有生产点"。

【白名单纪律】

登记必须是 `dict[事件名/码 -> 一句话理由]`，不许用集合/列表静默塞进去而不给
理由；且白名单条目若已经被消费/生产了却还挂着，会被本文件另一条测试判定为
"白名单过期未清理"而报红——白名单不是一次性放生，是需要跟着代码演进维护的
显式登记表。
"""

from __future__ import annotations

import re
from pathlib import Path

from schemas.advisory import AdvisoryCode
from schemas.sse import SseEventType

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
_EVENT_HANDLERS_TS = _REPO_ROOT / "frontend" / "lib" / "store" / "event-handlers.ts"

# ============================================================
# 轴 1：SSE 事件 —— SseEventType 每个值必须有前端 case 或登记白名单
# ============================================================

# 读码核实（2026-07-03，接线批）：CRITIC_VIOLATIONS / CRITIC_FIX_ATTEMPT /
# PLAN_FALLBACK 三个事件此前挂在这里——后端一直在发（emit_critic / emit_planner /
# emit_replan_router / emit_ils_replan），前端一直静默丢弃，路演看板叙事待拍板。
# 现已拍板：接成「系统自愈过程可视化」，event-handlers.ts 的 handleEvent switch
# 补了这三个 case（落 store.criticReport，ThoughtPanel「质检与自愈」小节渲染），
# 三条白名单登记随之摘除——这正是本 gate 的设计意图：接线后白名单条目过期即报红，
# 逼着登记跟着代码演进维护，不是一次性放生。
SSE_NOT_CONSUMED_IN_SWITCH: dict[str, str] = {}

# `case "done":` 在 event-handlers.ts 里确实存在（是空分支，注释里写明
# "onDone 在 streamSse 调用方处理"），会被下面的正则当作已消费——这与「done
# 的真正消费逻辑在 streamSse 的 onDone 回调而不在 switch 内部」这一实现细节
# 不矛盾：本 gate 只关心"有没有接住"，不关心接住的地点是 switch 分支本身还是
# 分支之外的兄弟回调；正则天然兼容这点，这里留白注释仅作说明。

_CASE_PATTERN = re.compile(r'case\s+["\']([a-zA-Z0-9_]+)["\']\s*:')


def _frontend_switch_cases() -> set[str]:
    text = _EVENT_HANDLERS_TS.read_text(encoding="utf-8")
    cases = set(_CASE_PATTERN.findall(text))
    assert cases, (
        f"从 {_EVENT_HANDLERS_TS} 提取到 0 个 case——正则可能与文件实际格式脱节，"
        "先确认文件路径/格式没有大改，而不是本 gate 全绿一场空"
    )
    return cases


def test_every_sse_event_type_is_consumed_or_whitelisted():
    """SseEventType 每个值：前端有 case，或进白名单附理由——不许静默漏接。"""
    cases = _frontend_switch_cases()
    missing = [
        ev.value
        for ev in SseEventType
        if ev.value not in cases and ev.value not in SSE_NOT_CONSUMED_IN_SWITCH
    ]
    assert not missing, (
        f"以下 SSE 事件后端会发出，但前端 event-handlers.ts 的 handleEvent switch "
        f"里没有 case，也没有登记进 SSE_NOT_CONSUMED_IN_SWITCH 白名单："
        f"{missing}（ADR-0014 G-4：产出必须有登记的消费者，或显式登记为"
        f"「有意不消费」并附理由）"
    )


def test_sse_whitelist_entries_are_not_stale():
    """白名单条目若已经被前端消费，必须移除登记——否则白名单本身在说谎。"""
    cases = _frontend_switch_cases()
    stale = set(SSE_NOT_CONSUMED_IN_SWITCH) & cases
    assert not stale, (
        f"以下事件已经在 event-handlers.ts 里有 case 了，SSE_NOT_CONSUMED_IN_SWITCH "
        f"白名单登记已过期，应删除对应条目：{stale}"
    )


def test_sse_whitelist_entries_have_nonempty_reason():
    """白名单每条必须有非空理由——不许用空字符串糊弄"显式登记"这条纪律。"""
    empty_reason = [ev for ev, reason in SSE_NOT_CONSUMED_IN_SWITCH.items() if not reason.strip()]
    assert not empty_reason, f"以下白名单事件缺少（非空）理由：{empty_reason}"


def test_frontend_switch_has_no_case_for_nonexistent_sse_event():
    """反向轴（防幻觉）：前端 case 字符串必须对应真实存在的后端 SseEventType。

    只覆盖 event-handlers.ts 的 SSE switch；collab 侧的 handleWsMessage（WS 通道，
    非 SSE）不在本轴范围——已读码核实 collab-store 的 "confirmed" case 属于该
    通道，故不在这里出现，不需要特判。若未来在本 SSE switch 里也长出同类"前端
    有、后端 SSE 从未发过"的 case，本测试会直接报红，无需额外白名单（这条轴本身
    就是在防止"需要白名单才能过"的幻觉分支产生）。
    """
    cases = _frontend_switch_cases()
    valid_values = {ev.value for ev in SseEventType}
    extra = cases - valid_values
    assert not extra, (
        f"event-handlers.ts 的 switch 里有以下 case，但后端 SseEventType 枚举"
        f"里根本不存在这个值（前端在处理一个后端从不会发的事件，多半是改名/"
        f"下线时漏删的死分支）：{extra}"
    )


# ============================================================
# 轴 2：AdvisoryCode —— 每个码必须在 backend（schemas/、tests/ 之外）有生产点
# ============================================================

# 读码核实（2026-07-03）：8 个码全部在 agent/planning/planners/{ils_planner.py,
# node_swap.py} 里有真实赋值生产点（ils_planner.plan_hybrid 产 5 个：
# NO_MATCHING_CANDIDATES / PINNED_UNSATISFIABLE / PINNED_DROPPED_IN_REPAIR /
# SHORTER_THAN_REQUESTED / OVER_BUDGET；node_swap 产 3 个 ADR-0013 F-1 换菜码：
# SWAP_DEGRADED / SWAP_KEPT_NODE_UNFIT / SWAP_NO_ALTERNATIVE_FOUND）。当前无需
# 登记任何白名单条目；字典留空是为了让这条轴的"登记机制"本身可测——如果未来
# 真出现一个只在 schemas/ 定义、backend 里没人生产的码，这里应该照 SSE 轴同样的
# 格式补一条 dict[码 -> 理由]，而不是把断言删掉。
ADVISORY_NOT_PRODUCED_OUTSIDE_SCHEMAS: dict[str, str] = {}

# 排除 schemas/、tests/（任务范围要求）与非源码目录（.venv 等第三方包、缓存）——
# 后者不排除会导致 rglob 扫描几万个无关文件，纯粹的性能与噪声问题，不是语义
# 排除。
_EXCLUDED_DIR_NAMES = {
    "schemas",
    "tests",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".git",
    "node_modules",
}


def _backend_py_files_outside_schemas_and_tests() -> list[Path]:
    files = []
    for path in _BACKEND_ROOT.rglob("*.py"):
        rel_parts = path.relative_to(_BACKEND_ROOT).parts
        if any(part in _EXCLUDED_DIR_NAMES for part in rel_parts[:-1]):
            continue
        files.append(path)
    assert files, f"{_BACKEND_ROOT} 下扫描到 0 个候选源文件——排除规则可能过严"
    return files


def _advisory_code_has_production_point(member: AdvisoryCode, files: list[Path]) -> bool:
    member_ref = f"AdvisoryCode.{member.name}"
    value_literals = (f'"{member.value}"', f"'{member.value}'")
    for path in files:
        text = path.read_text(encoding="utf-8")
        if member_ref in text or any(lit in text for lit in value_literals):
            return True
    return False


def test_every_advisory_code_has_a_production_point_or_whitelisted():
    """AdvisoryCode 每个码：backend（schemas/、tests/ 之外）有生产点，或进白名单。"""
    files = _backend_py_files_outside_schemas_and_tests()
    missing = [
        member.value
        for member in AdvisoryCode
        if member.value not in ADVISORY_NOT_PRODUCED_OUTSIDE_SCHEMAS
        and not _advisory_code_has_production_point(member, files)
    ]
    assert not missing, (
        f"以下 AdvisoryCode 在 backend（schemas/、tests/ 之外）找不到任何生产点"
        f"（既没有 `AdvisoryCode.成员名` 的赋值引用，也没有码值字符串字面量），"
        f"也没有登记进 ADVISORY_NOT_PRODUCED_OUTSIDE_SCHEMAS 白名单："
        f"{missing}（ADR-0014 G-4：只在 schemas/ 里声明却无人生产的码，多半是"
        f"设计时预留但从未接线，或已下线但忘了删声明）"
    )


def test_advisory_whitelist_entries_have_nonempty_reason():
    """白名单每条必须有非空理由（当前字典为空，此测试为机制本身留证）。"""
    empty_reason = [
        code for code, reason in ADVISORY_NOT_PRODUCED_OUTSIDE_SCHEMAS.items() if not reason.strip()
    ]
    assert not empty_reason, f"以下白名单 AdvisoryCode 缺少（非空）理由：{empty_reason}"
