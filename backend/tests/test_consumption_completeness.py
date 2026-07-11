"""test_consumption_completeness —— ADR-0014 G-4 消费完备性 gate（SSE / Advisory /
IntentExtraction 字段 三轴）。

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

本文件做 G-4 全部三条轴（G-4a 的 SSE / Advisory 两轴 + G-4b 的字段轴）：

1. **SSE 事件轴**：`schemas.sse.SseEventType` 每个值，必须在
   `frontend/lib/store/event-handlers.ts` 的 `handleEvent` switch 里有对应
   `case`，或登记进 `SSE_NOT_CONSUMED_IN_SWITCH` 白名单（附理由）。
2. **AdvisoryCode 轴**：`schemas.advisory.AdvisoryCode` 每个码，必须在 backend
   里（`schemas/` 与 `tests/` 之外）至少有一处生产点，或登记进
   `ADVISORY_NOT_PRODUCED_OUTSIDE_SCHEMAS` 白名单（附理由）。
3. **IntentExtraction 字段轴（G-4b）**：`schemas.intent.IntentExtraction`
   的全部字段（含嵌套的 `Companion` 字段），必须在 backend 业务代码里
   （`schemas/`、`tests/` 之外）至少有一处真实读取，或登记进
   `INTENT_FIELD_NOT_CONSUMED_OUTSIDE_SCHEMAS` 白名单（附理由）。这条轴
   G-4a 阶段有意留白——彼时 G-1 正在同步改字段集，登记消费点会立刻过期；
   现在 G-0（砍 pace_profile/gender_mix）/ G-1（出处）/ G-3（预算）都落地、
   字段集定型，补齐这条轴不会刚补完就过期。
4. **反向轴（防幻觉）**：前端 switch 里的每个 case 字符串，必须能在后端
   `SseEventType` 枚举里找到——防止"前端写了个 case，但后端从来不会发这个
   事件"的死代码分支（长期维护会以为它在工作）。

【实现手法】

- SSE 轴：不跑前端 JS/TS 构建链路（成本高、非本 gate 目的），改用纯文本正则
  从 `event-handlers.ts` 里提取 `case "xxx":` 的字符串字面量集合——容忍单/双
  引号、行内 `{` 等格式微调，但不做真正的 TS AST 解析（过度工程，收益不成
  比例；文件本身是手写的固定 switch，正则足够稳）。
- Advisory 轴：同样用纯文本 grep，在每个候选码上查「枚举成员名」
  （`AdvisoryCode.XXX`）或「枚举值字符串字面量」（`"xxx"` / `'xxx'`）是否
  出现在 schemas/ 与 tests/ 之外的 backend 源码里——任一命中即视为"有生产点"。
- 字段轴：**不能照搬 Advisory 轴"字段名以任意带引号字符串出现即算数"的宽
  判据**——读码核实过程中撞见两类具体假阳性，逼着判据收紧：
  (a) `intent_parser_prompt.py`/`refiner_prompt.py` 这类 prompt 文档字符串
  里塞满给 LLM 看的 few-shot JSON 示例（如
  `'"ambiguous_fields":["budget_per_person"]'`），字段名会以"另一个字段的
  值"身份出现在字符串字面量里，和"代码真的读了这个字段"是两回事；
  (b) `start_time`、`social_context`、`distance_max_km` 等名字与
  `schemas/itinerary.py`（行程节点自己的到达时间）、`schemas/tools.py`
  （SearchXxxInput 查询参数，同名是因为字段直接照抄 intent 语义传过去）
  撞名，Companion 的 `role`/`age`/`count` 更是和 LLM message role、协作
  房间 role、`list.count()` 方法这类无关属性撞名——裸 `\\.字段名\\b` 会把
  "读了个同名但不相干的属性"误判成"读了 IntentExtraction 的这个字段"。
  因此改用「receiver 限定 + 访问形状限定」的判据，在 backend（schemas/、
  tests/、**scripts/** 之外）的源码里查以下四种形状之一：
    1. `intent.field` / `original.field` / `refined.field`——receiver 限定
       为读码核实到的全部 IntentExtraction 形参命名（`intent` 是压倒性
       约定；`original`/`refined` 是 `refiner._propagate_field_provenance`
       同时持有改前改后两份意图对象时的例外）。
    2. `intent_dict.get("field")`——`model_dump()` 转字典后走模板拼装的
       既定写法（`narrator_prompt.py`）。
    3. `getattr(intent/original/refined, "field", ...)`——防御性可选
       字段读取（旧 checkpoint 可能缺字段时的兜底），`budget_per_person`/
       `extra_services` 的真实消费点就是这个形状，纯属性访问判据会漏判。
    4. 字段名单独成行、加引号、以逗号或右括号收尾——"把字段名塞进一个具名
       集合、再对集合循环 `getattr(obj, 变量)`"这种动态分发写法在调用现场
       看不到字面字段名，只能从"字段名作为集合字面量元素单独占一行"这个
       写法特征去认（`start_weekday` 的 field_provenance 传播——见
       `agent/intent/{parser,refiner}.py` 的 `_SCALAR_PROVENANCE_FIELDS`——
       是当前唯一走这条路径的字段）；限定"独占一行"排除 few-shot JSON
       示例（那些永远塞成一整行紧凑字符串，不会把单个字段名单独占一行）。
  Companion 字段（role/age/count/is_birthday/is_special_role）额外收窄
  receiver 到 `c`（读码核实：全仓遍历 `companions` 的循环变量清一色叫
  `c`，无 `companion.`/`comp.` 写法），只认形状 1/3，不需要形状 2/4
  （Companion 从不整份转 dict 模板拼装，也没有动态字段名集合）。
  额外排除 `scripts/`（SSE/Advisory 两轴当初没排除，是因为凑巧没有码/
  事件的唯一证据落在这里；字段轴读码核实时撞见 `parse_confidence` 唯一
  命中恰好是 `scripts/verify_schema_hardening.py`——手动跑的诊断脚本，
  用 print bullet + 退出码断言"LLM 输出符合预期"，不被任何 API 路由或
  LangGraph 节点 import，不在生产代码路径上，本质和 `tests/` 一样是
  "验证代码"而非"业务消费代码"，故一并排除）。

【白名单纪律】

登记必须是 `dict[事件名/码/字段名 -> 一句话理由]`，不许用集合/列表静默塞
进去而不给理由；且白名单条目若已经被消费/生产了却还挂着，会被本文件另一条
测试判定为"白名单过期未清理"而报红——白名单不是一次性放生，是需要跟着代码
演进维护的显式登记表。
"""

from __future__ import annotations

import re
from pathlib import Path

from schemas.advisory import AdvisoryCode
from schemas.intent import Companion, IntentExtraction
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


# ============================================================
# 轴 3（G-4b）：IntentExtraction 字段 —— 每个字段（含 Companion 嵌套字段）
# 必须在 backend 业务代码（schemas/、tests/、scripts/ 之外）有真实读取，
# 或登记白名单
# ============================================================

# 读码核实（2026-07-03，G-4b，字段集在 G-0/G-1/G-3 定型后收官）：
# - pace_profile / Companion.gender_mix 已在 G-0 砍除，`schemas/intent.py`
#   不再声明这两个字段，不会出现在下面 `IntentExtraction.model_fields` /
#   `Companion.model_fields` 的遍历范围里，不需要在本轴再提一次"零消费"。
# - ambiguous_fields：G-3 才接上——`agent/intent/narrator.py` 读
#   `intent.ambiguous_fields`（narration 据此说"哪些没吃准"），`agent/intent/
#   parser.py` 也用它做 budget_per_person 定性表达的诚实信号判断。有真实
#   消费点，不登记白名单。
# - parse_confidence：唯一命中是 `scripts/verify_schema_hardening.py` 里
#   `payload.get("parse_confidence")`——但那是手动跑的诊断脚本（断言 LLM
#   自报置信度符合预期，退出码判断通过与否），不被任何 API 路由/LangGraph
#   节点 import，不在生产代码路径上，本轴排除 `scripts/`（见下方排除集与
#   模块 docstring【实现手法】的理由）。真正点名的消费方是字段 docstring
#   里"< 0.6 时 Agent 应 ask back"这条分支逻辑，属于 E-3 范围（ADR-0014
#   决策 4），尚未实现——登记白名单。
# - 其余 16 个 IntentExtraction 顶层字段 + 5 个 Companion 字段，逐一读码
#   确认过至少一处真实消费点（详见模块 docstring【实现手法】列出的四种
#   访问形状），不逐条在这里复述文件路径——判据函数本身就是这次读码的
#   可执行记录，白名单为空即表示"读码没再找出新缺口"。
INTENT_FIELD_NOT_CONSUMED_OUTSIDE_SCHEMAS: dict[str, str] = {
    "parse_confidence": (
        "backend（schemas/、tests/、scripts/ 之外）唯一命中在 "
        "scripts/verify_schema_hardening.py，是手动跑的诊断脚本对 LLM 输出"
        "的断言，非生产代码路径消费；字段 docstring 点名的真正消费方"
        "（parse_confidence < 0.6 时 Agent 触发 ask back）是 E-3 的既定"
        "范围（ADR-0014 决策 4），当前尚未实现。"
    ),
    "understanding": (
        "信任带（AI 思考流）①拍专用的第一人称旁白句（2026-07-06 新增），"
        "唯一消费方是前端——`emit_intent` 把整份 `intent.model_dump()` 推成 "
        "INTENT_PARSED，前端信任带组件直接渲染这句话。它是纯自然语言叙事"
        "（LLM 现生成的一句话），不是结构化约束，backend 规则/critic/planner "
        "没有、也不应该读它做任何决策分支——同 raw_input 的展示性质，但"
        "raw_input 恰好还被 `_apply_provenance_correction` 当子串扫描读取，"
        "understanding 没有对应的规则消费点，故显式登记而非假装有消费方。"
    ),
    "explicit_dining_requested": (
        "【临时登记，C5a 落地即删】四条不变式批 C4 惰性提交（安全熔断点）："
        "tristate 字段先落 schema、零消费者、行为零变化（None=现状逐字节"
        "一致是该 commit 的验收红线，见 test_explicit_dining_tristate_schema"
        ".py）。消费者在紧随其后的 C5a（dining_soft_anchored 三分支 / "
        "blueprint prompt 决策 3/10 / critic explicit_dining_missing 护栏 / "
        "refiner 缺键继承守卫）——C5a 合入后本条必须删除，让本测试重新逼问"
        "真实消费点。若 C5a 被熔断砍掉，本字段应随之从 schema 删除。"
    ),
}

# 在 Advisory 轴排除集基础上追加 scripts/——理由见模块 docstring【实现手法】
# 字段轴段落："scripts/verify_*.py" 是手动诊断脚本，不在生产代码路径上，
# 本质与 tests/ 同类。不直接修改 `_EXCLUDED_DIR_NAMES`（Advisory/SSE 两轴
# 复用它），避免字段轴的判断影响到已经绿的另外两轴。
_INTENT_FIELD_EXCLUDED_DIR_NAMES = _EXCLUDED_DIR_NAMES | {"scripts"}


def _backend_py_files_for_intent_field_axis() -> list[Path]:
    """同 `_backend_py_files_outside_schemas_and_tests`，额外排除 `scripts/`。"""
    files = []
    for path in _BACKEND_ROOT.rglob("*.py"):
        rel_parts = path.relative_to(_BACKEND_ROOT).parts
        if any(part in _INTENT_FIELD_EXCLUDED_DIR_NAMES for part in rel_parts[:-1]):
            continue
        files.append(path)
    assert files, f"{_BACKEND_ROOT} 下扫描到 0 个候选源文件（字段轴排除集）——排除规则可能过严"
    return files


# IntentExtraction 顶层字段的 receiver 白名单——读码核实到的全部形参命名：
# `intent` 是压倒性约定；`original`/`refined` 是
# `agent/intent/refiner.py::_propagate_field_provenance` 同时持有"改前/
# 改后"两份意图对象时的例外（不能都叫 intent）。
_INTENT_RECEIVER_NAMES = ("intent", "original", "refined")


def _intent_field_has_consumption_point(field: str, files: list[Path]) -> bool:
    """IntentExtraction 顶层字段判据——四种形状之一即算"有真实读取"。

    形状取舍见模块 docstring【实现手法】字段轴段落；核心是不用裸
    `\\.field\\b`（会被 schemas/itinerary.py 的同名 start_time、
    schemas/tools.py 的同名 social_context/distance_max_km 等撞名），改用
    「receiver 限定 + 访问形状限定」。
    """
    receiver_alt = "|".join(_INTENT_RECEIVER_NAMES)
    dot_pattern = re.compile(rf"\b(?:{receiver_alt})\.{re.escape(field)}\b")
    # `model_dump()` 转字典后走模板拼装的既定写法（narrator_prompt.py）。
    dict_get_pattern = re.compile(rf'intent_dict\.get\(\s*["\']{re.escape(field)}["\']')
    # 防御性可选字段读取（getattr 带默认值）——budget_per_person/
    # extra_services 的真实消费点就是这个形状，纯属性访问判据会漏判。
    getattr_pattern = re.compile(
        rf'getattr\(\s*(?:{receiver_alt})\s*,\s*["\']{re.escape(field)}["\']'
    )
    # 字段名单独成行、加引号、以逗号或右括号收尾——动态分发写法
    # （`for field in _SCALAR_PROVENANCE_FIELDS: getattr(obj, field)`）在
    # 调用现场看不到字面字段名，只能从"集合字面量里字段名单独占一行"这个
    # 写法特征去认（start_weekday 的 field_provenance 传播是当前唯一
    # 走这条路径的字段）。限定"独占一行"排除 few-shot JSON 示例——那些
    # 永远塞成一整行紧凑字符串，不会把单个字段名单独占一行。
    own_line_pattern = re.compile(rf'^\s*["\']{re.escape(field)}["\']\s*,?\s*$', re.MULTILINE)
    for path in files:
        text = path.read_text(encoding="utf-8")
        if (
            dot_pattern.search(text)
            or dict_get_pattern.search(text)
            or getattr_pattern.search(text)
            or own_line_pattern.search(text)
        ):
            return True
    return False


# Companion 嵌套字段的 receiver 白名单——读码核实：全仓遍历 `companions` 的
# 循环变量清一色叫 `c`（无 `companion.`/`comp.` 写法）。收窄到 `c` 是因为
# role/age/count 这几个名字太通用：不收窄会把 LLM message 的 `.role`、
# 协作房间的 `.role`、`list.count()` 方法都误判成"读了 Companion 的字段"。
_COMPANION_RECEIVER_NAMES = ("c",)


def _companion_field_has_consumption_point(field: str, files: list[Path]) -> bool:
    """Companion 嵌套字段判据——只认形状 1（属性访问）与形状 3（getattr）。

    不需要形状 2/4：Companion 从不整份转 dict 做模板拼装，也没有"字段名塞
    进具名集合再动态 getattr"的写法。
    """
    receiver_alt = "|".join(_COMPANION_RECEIVER_NAMES)
    dot_pattern = re.compile(rf"\b(?:{receiver_alt})\.{re.escape(field)}\b")
    getattr_pattern = re.compile(
        rf'getattr\(\s*(?:{receiver_alt})\s*,\s*["\']{re.escape(field)}["\']'
    )
    for path in files:
        text = path.read_text(encoding="utf-8")
        if dot_pattern.search(text) or getattr_pattern.search(text):
            return True
    return False


def test_every_intent_extraction_field_is_consumed_or_whitelisted():
    """IntentExtraction 顶层字段：backend 业务代码有真实读取，或进白名单附理由。"""
    files = _backend_py_files_for_intent_field_axis()
    missing = [
        field
        for field in IntentExtraction.model_fields
        if field not in INTENT_FIELD_NOT_CONSUMED_OUTSIDE_SCHEMAS
        and not _intent_field_has_consumption_point(field, files)
    ]
    assert not missing, (
        f"以下 IntentExtraction 字段在 backend（schemas/、tests/、scripts/ 之外）"
        f"找不到任何真实读取（既没有 intent/original/refined 的属性访问，也没有"
        f"intent_dict.get()/getattr() 形式，也不是动态字段集合里单独成行的元素），"
        f"也没有登记进 INTENT_FIELD_NOT_CONSUMED_OUTSIDE_SCHEMAS 白名单："
        f"{missing}（ADR-0014 G-4b：字段在 schema 里声明却无人读取，多半是"
        f"抽取了但没接下游，或字段集演进后忘了删声明）"
    )


def test_every_companion_field_is_consumed_or_whitelisted():
    """Companion 嵌套字段：backend 业务代码有真实读取（receiver 限定为 c），或进白名单。"""
    files = _backend_py_files_for_intent_field_axis()
    missing = [
        field
        for field in Companion.model_fields
        if field not in INTENT_FIELD_NOT_CONSUMED_OUTSIDE_SCHEMAS
        and not _companion_field_has_consumption_point(field, files)
    ]
    assert not missing, (
        f"以下 Companion 字段在 backend（schemas/、tests/、scripts/ 之外）找不到"
        f"任何真实读取（既没有 `c.字段名` 属性访问，也没有 `getattr(c, \"字段名\", ...)`），"
        f"也没有登记进 INTENT_FIELD_NOT_CONSUMED_OUTSIDE_SCHEMAS 白名单：{missing}"
        f"（ADR-0014 G-4b：Companion 抽取了这个字段但没有任何规划/叙事逻辑读取它）"
    )


def test_intent_field_whitelist_entries_are_not_stale():
    """白名单条目若已经有检测到的消费点，必须移除登记——否则白名单本身在说谎。"""
    files = _backend_py_files_for_intent_field_axis()
    stale = [
        field
        for field in INTENT_FIELD_NOT_CONSUMED_OUTSIDE_SCHEMAS
        if (
            field in IntentExtraction.model_fields
            and _intent_field_has_consumption_point(field, files)
        )
        or (field in Companion.model_fields and _companion_field_has_consumption_point(field, files))
    ]
    assert not stale, (
        f"以下字段已经在 backend 业务代码里检测到真实读取了，"
        f"INTENT_FIELD_NOT_CONSUMED_OUTSIDE_SCHEMAS 白名单登记已过期，"
        f"应删除对应条目：{stale}"
    )


def test_intent_field_whitelist_entries_have_nonempty_reason():
    """白名单每条必须有非空理由——不许用空字符串糊弄"显式登记"这条纪律。"""
    empty_reason = [
        field
        for field, reason in INTENT_FIELD_NOT_CONSUMED_OUTSIDE_SCHEMAS.items()
        if not reason.strip()
    ]
    assert not empty_reason, f"以下白名单字段缺少（非空）理由：{empty_reason}"


def test_intent_field_whitelist_keys_reference_real_fields():
    """反向轴（防幻觉）：白名单登记的字段名必须真实存在于 schema 里。

    防止"字段改名/删除后白名单条目忘了同步改名"——比如 pace_profile /
    Companion.gender_mix 在 G-0 已经从 schema 里砍掉，如果白名单还挂着这两个
    名字，字面上会"通过"（因为遍历 model_fields 根本不会碰到它们），但那是
    在悄悄放行一条名不副实的登记，不是真的守住"每条白名单都对应一个当前
    仍然存在、仍然没被消费的字段"这条语义。
    """
    valid_names = set(IntentExtraction.model_fields) | set(Companion.model_fields)
    extra = set(INTENT_FIELD_NOT_CONSUMED_OUTSIDE_SCHEMAS) - valid_names
    assert not extra, (
        f"INTENT_FIELD_NOT_CONSUMED_OUTSIDE_SCHEMAS 里以下登记名，在当前 "
        f"IntentExtraction/Companion 字段集里根本不存在（多半是字段改名/"
        f"下线后白名单忘了同步清理）：{extra}"
    )
