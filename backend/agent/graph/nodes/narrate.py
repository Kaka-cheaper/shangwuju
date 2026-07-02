"""nodes.narrate —— 暖语气文案节点。

复用 backend/agent/narrator.py 的 generate_narration。

输入：state["intent"] / state["itinerary"] / state["critic_attempts"]
输出：
- state["narration"] = str
- state["itinerary"] = 新 Itinerary（不可变更新；trace.final_strategy 会被更新）

【spec planning-quality-deep-review R6+R7（Task 6 + Agent H P1-H6）】
- 用 itinerary.model_copy(update={"decision_trace": ...}) + 内嵌 trace.model_copy
  替代原地 mutate（旧实现直接修改 itinerary.decision_trace.final_strategy 等
  字段，副作用泄漏到上游 state，违背 LangGraph "节点返回 diff 不改输入" 原则）
- 把 state.critic_attempts 拼接成 critic_summary 字符串，喂给 narrator 触发
  「主动质疑规则」（R6 核心：让评委看到"AI 主动质疑方案"）

【ADR-0013 F-3：节点调整按钮 + 具名备选（node_actions）】
narrate 是"节点交互三元素"里"具名备选"与"定向调整按钮"两者共同的生产时机
（决策 5："narrate 的既有 LLM 调用搭车产出"）：
- chips：`generate_title_and_narration` 第三项返回值（同次 LLM JSON 增列 /
  按 kind 模板兜底，见 `agent.intent.narrator`）。
- alternatives：现场调 `agent.planning.planners.node_swap.feasible_
  alternatives`（k=2），与 F-1 点击换菜同一条候选池/预验证真相源。
- 两者由 `_build_node_actions` 组装成 `{node_id: {chips, alternatives}}`，
  原样放进返回 diff 的 `node_actions` 字段，供 `emit_narrate` 挂
  `ITINERARY_READY` payload 的兄弟字段（不进 `Itinerary` schema 本体）。
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from agent.graph.state import AgentState
from agent.core.llm_client import get_llm_client
from agent.intent.narrator import generate_title_and_narration
from agent.planning.critic.critics_v2 import Severity, ViolationCode
from agent.planning.planners.node_swap import feasible_alternatives
from schemas.advisory import AdvisoryCode
from schemas.node_chip import NodeChip

logger = logging.getLogger(__name__)

# ADR-0013 F-3：具名备选数量（与调整按钮 narrate 同一时机产出，同一真相源
# ——feasible_alternatives 就是 F-1 点击换菜实际会用的候选池预验证结果，
# 展示与点击不割裂）。k=2 是任务书拍定的展示密度（右侧栏两个备选，不喧宾夺主）。
_NODE_ALTERNATIVES_K = 2


def _build_critic_summary(critic_attempts: list[Any]) -> str:
    """把 state.critic_attempts 拼成一段 narrator 可消费的中文摘要。

    输出示例：
        "经过 2 次 critic 修正：第 1 次（age_duration_mismatch×2 / 已修复）→
         节点[1] 时长 165min 超 75min 上限；第 2 次（已通过）"

    空列表 → 返回空串（narrator 不触发主动质疑）。
    """
    if not critic_attempts:
        return ""

    parts: list[str] = []
    for raw in critic_attempts:
        if hasattr(raw, "model_dump"):
            d = raw.model_dump()
        elif isinstance(raw, dict):
            d = raw
        else:
            continue
        n = d.get("attempt_n", "?")
        codes = d.get("violation_codes") or []
        feedback = (d.get("feedback_summary") or "").strip()
        resolved = d.get("resolved", False)
        head = f"第 {n} 次"
        if codes:
            head += "（" + " / ".join(codes[:3])
            head += " · 已修复" if resolved else " · 未消化"
            head += "）"
        else:
            head += "（已通过）" if resolved else ""
        # 截 critic 反馈摘要（避免塞过长的 prompt）
        if feedback:
            head += f"：{feedback[:80]}"
        parts.append(head)
    return "经过 " + str(len(parts)) + " 次 critic 修正——" + "；".join(parts)


def _extract_quality_warnings(state: AgentState) -> list[str]:
    """把 state.quality_issues 转成 list[str] 喂给 narrator。

    quality_issues 由上游节点写入（`intent_node` 的词典外社交意图检测——见
    `agent/graph/nodes/intent.py` R1）；`intent_node` / `refiner_node` 两条新
    规划事件入口都在事件开始时经 `agent.graph.state.reset_for_new_episode()`
    清零，防上一事件残留漏进新一轮（ADR-0012 决策 4）。
    """
    issues = state.get("quality_issues") or []
    out: list[str] = []
    for it in issues:
        if isinstance(it, str) and it.strip():
            out.append(it.strip())
        elif isinstance(it, dict) and it.get("message"):
            out.append(str(it["message"]).strip())
    return out


def _extract_advisories(state: AgentState) -> list[dict]:
    """把 state.advisories（`Advisory.model_dump()` 列表，D-7）原样取出。

    由 `ils_replan_node` 写入（hybrid 成功时），`intent_node` / `refiner_node`
    两条新规划事件入口都经 `reset_for_new_episode()` 重置（ADR-0012 决策 4）。
    本函数只做防御性过滤（非法条目跳过，宁缺毋崩），不改写内容——`narrate_node`
    既用它拼 narrator 文案，也原样回填进返回 diff 供 `emit_narrate` 取用
    （见 `agent/graph/_emit_handlers.py`）。
    """
    advisories = state.get("advisories") or []
    return [a for a in advisories if isinstance(a, dict) and a.get("message")]


# 主 LLM 蓝图路径 SOFT 违规 → advisory code 映射（补 D-7 的路径缺口，见
# `_extract_soft_violation_advisories` docstring）。
#
# 只有 DURATION_OUT_OF_RANGE 有天然对应的 AdvisoryCode：`SHORTER_THAN_REQUESTED`
# ——这条映射不是本改动新造的，是照抄 `ils_planner._build_success_advisories`
# 已有的同一条映射（该函数原文："复用 check_duration 已经写好的用户向文案…
# 同一纪律，不重写第二份措辞"）。两条路径（ILS / 主 LLM 蓝图）用同一个 code
# 描述同一种情况，是两路都产出时能靠 message 去重合并成一句话的前提。
#
# DISTANCE_EXCEEDED / SOCIAL_CONTEXT_MISMATCH（POOR 档）目前没有对应
# AdvisoryCode——`schemas/advisory.py` 的 5 个码是 D-7 立项时按 ils_planner
# 已知的告知场景枚举的，未预留这两个 critic 维度。候选方案 a）扩
# `AdvisoryCode` 枚举；b）直接把 `ViolationCode` 字符串值当 code 用。选 b：
# 1) 范围纪律——本改动只许动 narrate.py，扩枚举要改 schemas/advisory.py；
# 2) 核实过 `agent/graph/_emit_handlers.py:emit_narrate` 对 advisory dict 的
#    "code" 字段只做原样透传（`"code": a.get("code")`），不做任何枚举校验/
#    白名单检查；前端消费同一形状的 `messages[].code` 目前也只是文本渲染，
#    未见枚举强校验（若未来前端要枚举校验，再补 AdvisoryCode 扩员，成本仍是
#    加成员而非破坏性变更）——选 b 侵入面最小，且不引入"胖枚举"
#    （为一次性 3 码新增就扩一个跨层共享的 public API）。
_SOFT_VIOLATION_CODE_TO_ADVISORY_CODE: dict[str, str] = {
    ViolationCode.DURATION_OUT_OF_RANGE.value: AdvisoryCode.SHORTER_THAN_REQUESTED.value,
}


def _violation_field(v: Any, key: str) -> Any:
    """兼容 `state.violations` 条目的两种可能形态取字段。

    `critic_node`（`agent/graph/nodes/critic.py`）当前实际写入的是
    `validate_itinerary` 直接返回的 `list[Violation]`（Pydantic 对象，未
    `.model_dump()`）。这里仍防御性兼容 dict 形态（未来实现变化 / 测试直接
    喂 dict），两态取同一份逻辑处理，不重复写两套。
    """
    if isinstance(v, dict):
        return v.get(key)
    return getattr(v, key, None)


def _enum_value(x: Any) -> Any:
    """str Enum 取裸值；已经是字符串（dict 形态）原样返回。"""
    return getattr(x, "value", x)


def _extract_soft_violation_advisories(state: AgentState) -> list[dict]:
    """把主 LLM 蓝图路径 critic 放行后残留的 SOFT 违规转成 advisory dict。

    【这是什么问题】ADR-0010 决策 11「绝不默默忽略」的 advisory 通道（D-7）
    目前只在 ILS 兜底路径（`ils_planner._build_success_advisories` →
    `ils_replan_node` 写 `state.advisories`）兑现。主 LLM 蓝图路径
    （`planner_node` → `assemble_node` → `critic_node` → `narrate_node`）
    critic 放行（无 HARD 违规，`route_after_critic` 直接走 narrate）后，
    SOFT 违规只写进 `state.violations` 供 trace / SSE `critic_violations`
    展示——narrate 从不读它，"方案比你要的短了些"这句话在主路径永远说不出口。
    本函数补上这段路径缺口。

    【不挑 code，全部转】覆盖 `state.violations` 里所有 `severity == SOFT`
    的条目（决策 11 原文「任何没完全如你所愿都要告知」，不是只挑时长）。
    当前产 SOFT 的 3 个 check（`agent/planning/critic/_rules/checks.py`）：
    `check_duration`（时长不足，`DURATION_OUT_OF_RANGE`）、`check_distance`
    （`DISTANCE_EXCEEDED`）、`check_social_context`（POOR 档，
    `SOCIAL_CONTEXT_MISMATCH`）。`Violation.message` 本就是自包含中文人话
    （同 D-7 纪律），直接复用，不重写文案。

    【为什么读 state.violations 不会带出"已作废方案"的旧告知】
    `violations` 是 EPISODE_SCOPED 字段且无自定义 reducer（默认整体覆盖，
    见 `agent/graph/state.py`）；`critic_node` 每次跑都整份重写它。当
    itinerary 因 replan 换掉时（`ils_replan_node` 成功分支），该节点显式把
    `violations` 重置为 `[]`（见 `agent/graph/nodes/replan.py`）——所以本函数
    读到的 SOFT 违规，要么是本轮 `critic_node` 对**当前** itinerary 的真实
    评估结果，要么是 `[]`，不存在"itinerary 已被 ILS 换掉、violations 却还
    是旧方案的评语"这种错配。
    """
    violations = state.get("violations") or []
    out: list[dict] = []
    for v in violations:
        severity = _enum_value(_violation_field(v, "severity"))
        if severity != Severity.SOFT.value:
            continue
        message = _violation_field(v, "message")
        if not message:
            continue
        code = str(_enum_value(_violation_field(v, "code")))
        advisory_code = _SOFT_VIOLATION_CODE_TO_ADVISORY_CODE.get(code, code)
        out.append({"code": advisory_code, "message": str(message)})
    return out


def _merge_advisories(existing: list[dict], additional: list[dict]) -> list[dict]:
    """按 message 去重合并两路 advisory（D-7 ILS 路径 + 本次新增的主路径转换）。

    同一条 `check_duration` SOFT message 理论上可能同时出现在两路（ILS 路径的
    `_build_success_advisories` 与本文件的 `_extract_soft_violation_advisories`
    都复用同一句 `Violation.message`），必须只保留一份——`existing` 在前保序
    保留（先出现者留），`additional` 里同 message 的条目跳过。
    """
    seen = {a.get("message") for a in existing if isinstance(a, dict)}
    merged = list(existing)
    for a in additional:
        msg = a.get("message")
        if msg in seen:
            continue
        seen.add(msg)
        merged.append(a)
    return merged


def _dedupe_unmet_desires_against_advisories(
    unmet_desires: list[str], advisory_messages: list[str]
) -> list[str]:
    """去重：advisory 消息里已经提过的名词，不在 unmet_desires 里重复说一遍。

    简单包含判重（ADR-0010 D-7 决策 G 原话），不做语义匹配——advisory 与
    unmet_desires 服务不同检测机制（前者来自 planner 的 pin/预算/时长判定，
    后者来自 narrate 自身的品类/POI 诉求检测），两者命中同一诉求词的概率低，
    简单 substring 包含足以避免"同一件事说两遍"这个具体风险，不值得引入更复杂
    的语义去重。
    """
    if not advisory_messages:
        return unmet_desires
    combined = "".join(advisory_messages)
    return [d for d in unmet_desires if d not in combined]


def _detect_unmet_cuisines(intent: Any, itinerary: Any) -> list[str]:
    """检测用户明示餐饮品类是否未排进最终行程（诚实告知用）。

    取 intent.preferred_poi_types + 行程中 target_kind=restaurant 节点的 cuisine
    （靠 target_id 查 mock），交给 narrator.detect_unmet_cuisine_preference 判定。
    任何异常返空（降级为不告知，宁缺毋误报）。
    """
    try:
        from agent.intent.narrator import detect_unmet_cuisine_preference
        from data.loader import load_restaurants

        prefs = list(getattr(intent, "preferred_poi_types", []) or [])
        if not prefs:
            return []
        rest_by_id = {r.id: r for r in load_restaurants()}
        cuisines: list[str] = []
        for n in itinerary.nodes:
            if getattr(n, "target_kind", None) != "restaurant":
                continue
            rid = getattr(n, "target_id", None)
            r = rest_by_id.get(rid)
            if r and r.cuisine:
                cuisines.append(r.cuisine)
        return detect_unmet_cuisine_preference(prefs, cuisines)
    except Exception:  # noqa: BLE001
        return []


def _detect_unmet_poi(intent: Any, itinerary: Any) -> list[str]:
    """检测用户明示活动诉求（看展/KTV/密室等）是否未排进最终行程的 POI（诚实告知用）。

    取 intent.preferred_poi_types + 行程中 target_kind=poi 节点的 type/name/tags
    （靠 target_id 查 mock pois），交给 narrator.detect_unmet_poi_preference 判定。
    与 _detect_unmet_cuisines 对称（cuisine 走餐厅 cuisine 字段，本函数走 POI 维度）。
    任何异常返空（降级为不告知，宁缺毋误报）。
    """
    try:
        from agent.intent.narrator import detect_unmet_poi_preference
        from data.loader import load_pois

        prefs = list(getattr(intent, "preferred_poi_types", []) or [])
        if not prefs:
            return []
        poi_by_id = {p.id: p for p in load_pois()}
        poi_types: list[str] = []
        poi_names: list[str] = []
        poi_tags: list[str] = []
        for n in itinerary.nodes:
            if getattr(n, "target_kind", None) != "poi":
                continue
            pid = getattr(n, "target_id", None)
            p = poi_by_id.get(pid)
            if p:
                poi_types.append(p.type or "")
                poi_names.append(p.name or "")
                poi_tags.extend(list(p.tags or []))
        return detect_unmet_poi_preference(prefs, poi_types, poi_names, poi_tags)
    except Exception:  # noqa: BLE001
        return []


def _build_node_actions(
    itinerary: Any,
    intent: Any,
    pois: list[Any],
    restaurants: list[Any],
    node_chips: list[NodeChip],
) -> dict[str, dict[str, Any]]:
    """组装 ADR-0013 F-3 的 node_actions：`{node_id: {"chips": [...],
    "alternatives": [...]}}`——挂 `ITINERARY_READY` payload 的兄弟字段（见
    `agent.graph._emit_handlers.emit_narrate`），不进 `Itinerary` schema 本体。

    chips 来自 `generate_title_and_narration` 第三项返回值（LLM 搭车产出或
    模板兜底，narrate_node 调用处已经决出）；alternatives 现场调
    `node_swap.feasible_alternatives`（k=`_NODE_ALTERNATIVES_K`）——与 F-1
    "点击换菜"实际消费的候选池/降级序列/`try_insert` 预验证同一条真相源，
    不是另算一套"看起来差不多"的展示用备选（ADR-0013 决策 4 原文："必须由
    引擎预验证可行——试插通过才展示，拒绝拿未验证的 alternatives 充数"）。

    单节点异常兜底：`feasible_alternatives` 对某个节点抛异常（候选池覆盖
    缺口等调用方契约问题，见该函数 docstring「前置条件」）不应连累其它节点
    的按钮/备选一起消失——按节点独立捕获，该节点的 alternatives 降级为空
    （宁缺毋崩，与 `agent.graph._resilience.drain_on_error` 同一纪律，但这里
    是函数内部的节点级隔离，比图级兜底更细粒度）。

    没有 chips 也没有 alternatives 的节点不进返回字典（emit_narrate 那层
    "无内容不加字段" 的同一纪律在这里先做一次节点粒度的体现）。
    """
    chips_by_node: dict[str, list[NodeChip]] = {}
    for chip in node_chips:
        chips_by_node.setdefault(chip.node_id, []).append(chip)

    result: dict[str, dict[str, Any]] = {}
    for node in itinerary.nodes:
        if node.target_kind == "home":
            continue
        node_id = node.target_id
        chips = chips_by_node.get(node_id, [])

        alternatives: list[dict[str, Any]] = []
        try:
            options = feasible_alternatives(
                itinerary, intent, pois, restaurants,
                target_node_id=node_id, k=_NODE_ALTERNATIVES_K,
            )
            alternatives = [asdict(opt) for opt in options]
        except Exception:  # noqa: BLE001
            logger.warning(
                "[narrate] feasible_alternatives 对节点 %s 失败，该节点降级为空备选",
                node_id, exc_info=True,
            )

        if not chips and not alternatives:
            continue
        result[node_id] = {
            "chips": [c.model_dump() for c in chips],
            "alternatives": alternatives,
        }
    return result


def narrate_node(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    itinerary = state.get("itinerary")

    if intent is None or itinerary is None:
        return {"narration": None}

    client = get_llm_client()
    # spec interaction-experience-review：规则模式下 narrate **不调 LLM 润色**，
    # 走纯模板文案——保持「规则模式 = 不调用大模型的纯算法路径」承诺一致
    mode = state.get("planner_mode")
    use_llm = (
        mode != "rule"
        and client is not None
        and getattr(client, "provider", None) != "stub"
    )

    # spec R6：把 state.critic_attempts 拼成摘要喂给 narrator
    critic_summary = _build_critic_summary(state.get("critic_attempts") or [])
    quality_warnings = _extract_quality_warnings(state)

    # D-7：state.advisories（ILS 兜底路径 planner「绝不默默忽略」的结构化告知）
    # + 本次新增：主 LLM 蓝图路径 critic 放行后残留的 SOFT 违规转换（见
    # `_extract_soft_violation_advisories` docstring）→ 合并去重（同 message
    # 不重复）→ narrator 的完整句子列表。
    advisories = _merge_advisories(
        _extract_advisories(state),
        _extract_soft_violation_advisories(state),
    )
    advisory_messages = [a["message"] for a in advisories]

    # 诚实告知（用户观察的 bug）：用户明示诉求但因超距/无候选/重排仍未选上而未排进行程
    # → 检测未满足诉求，让 narrator 诚实说明"附近没找到 X，帮你换了替代品"。
    # cuisine 版走餐厅菜系维度，poi 版走活动场所维度（spec narration-and-intent-fidelity R4）；
    # 两者合并成统一的未满足诉求列表喂给 narrator（去重保序）。
    unmet_cuisines = _detect_unmet_cuisines(intent, itinerary)
    unmet_pois = _detect_unmet_poi(intent, itinerary)
    unmet_desires: list[str] = []
    for d in [*unmet_cuisines, *unmet_pois]:
        if d and d not in unmet_desires:
            unmet_desires.append(d)
    # D-7 决策 G：advisory 已经提过的名词不在 unmet_desires 里重复说。
    unmet_desires = _dedupe_unmet_desires_against_advisories(unmet_desires, advisory_messages)

    # ADR-0013 F-3：execute 阶段召回的候选池（EPISODE_SCOPED，与本次 itinerary
    # 出自同一批候选，覆盖前置条件天然满足——见 node_swap 模块 docstring「前置
    # 条件」2）；同时喂给 generate_title_and_narration（node_chips 的 LLM 上下文/
    # 模板兜底）与 _build_node_actions（feasible_alternatives 的候选池）。
    pois = state.get("pois") or []
    restaurants = state.get("restaurants") or []

    # 同次产出：title（小红书风格大标题，写回 itinerary.summary）+ narration（开场白）
    # + node_chips（ADR-0013 F-3：节点定向调整按钮，LLM 搭车或模板兜底）。
    # title 必须覆盖所有主要站点（旧 bug：只取停留最久的单站）。
    title, text, node_chips = generate_title_and_narration(
        intent=intent,
        itinerary=itinerary,
        stage="stream",
        use_llm=use_llm,
        critic_summary=critic_summary,
        quality_warnings=quality_warnings,
        unmet_cuisines=unmet_desires,
        advisories=advisory_messages,
        pois=pois,
        restaurants=restaurants,
    )

    # spec R7（Agent H P1-H6）：用 model_copy 不可变更新 itinerary，避免原地 mutate
    update_fields: dict[str, Any] = {}

    # 把同次产出的小红书风格 title 写回 summary（行程卡片大标题）。
    # title 永远非空（LLM 解析失败也有规则兜底），覆盖所有主要站点。
    if title and title.strip() and title.strip() != (itinerary.summary or "").strip():
        update_fields["summary"] = title.strip()

    if itinerary.decision_trace is not None:
        old_trace = itinerary.decision_trace
        chain = old_trace.fallback_chain
        if chain:
            last_to = chain[-1].to_stage
            mapping = {
                "give_up": "give_up",
                "ils": "ils",
                "rule": "rule",
                "llm_backprompt": "llm_backprompt",
            }
            final_strategy = mapping.get(last_to, "llm_first")
        else:
            final_strategy = "llm_first"

        # 把上一条 critic_attempt（如果存在且未 resolved）标 resolved
        # ——能走到 narrate 说明 critic 已经放行，最后一次 attempt 的反馈被消化了
        new_critic_attempts = list(old_trace.critic_attempts)
        if new_critic_attempts:
            last = new_critic_attempts[-1]
            if not last.resolved:
                new_critic_attempts[-1] = last.model_copy(update={"resolved": True})

        new_trace = old_trace.model_copy(
            update={
                "final_strategy": final_strategy,
                "critic_attempts": new_critic_attempts,
            }
        )
        update_fields["decision_trace"] = new_trace

    # 工具前移（spec dialogue-act-routing）：规划最后一步把「确认动作清单」算好挂上，
    # confirm 时直接 replay、不再读 intent。narrate 是 LangGraph 规划的最后必经节点。
    from agent.graph.nodes.execute_finalize import build_confirm_actions

    update_fields["pending_actions"] = build_confirm_actions(itinerary, intent)
    new_itinerary = itinerary.model_copy(update=update_fields)

    # spec algorithm-redesign R5：narrate 主逻辑末尾的副作用——回写 user_profile.json
    # 路径 B（design.md §Component 4 决策点 4）：不动 graph 拓扑（spec B 锁的编排冻结纪律）
    #
    # 【pitfalls 2026-05-25 修正】
    # 旧实现：narrate（方案就绪后）就触发 persist_memory——产品语义错误
    # 用户反馈：「已记住此次场景偏好应该是我确认预约后才记住」——方案没确认就持久化偏好
    # 是不是合适的语义？显然不是。
    #
    # 新实现：persist_memory 副作用迁到 execute_finalize_node（confirm 路径）；narrate 节点
    # 不再触发 memory 写入。这与 memory_writer.py 的「user_decision != 'confirm' 时
    # success=False」语义对齐，让「记住」与「下单」两个动作绑同一触发点。
    #
    # D-7：advisories 原样透传进返回 diff（不是本节点新算的，只是让 emit_narrate
    # 能直接从 diff 里拿到，不必依赖 EmitContext.last_state 的时序假设——见
    # agent/graph/_emit_handlers.py:emit_narrate）。
    #
    # ADR-0013 F-3：node_actions 同一纪律——本节点算好（chips 来自同次 LLM/
    # 模板，alternatives 现场调 feasible_alternatives），原样透传进返回 diff，
    # emit_narrate 只管组装 SSE payload、不重新计算业务逻辑。用 new_itinerary
    # （而非入参 itinerary）算，保证 node_id 集合与最终推给前端的方案完全一致
    # （两者节点集合当前恒等——narrate 只改 summary/decision_trace/pending_
    # actions，不改 nodes——但显式用 new_itinerary 是"不假设两者恒等"的防御）。
    node_actions = _build_node_actions(new_itinerary, intent, pois, restaurants, node_chips)

    result: dict[str, Any] = {
        "narration": text,
        "itinerary": new_itinerary,
        "advisories": advisories,
        "node_actions": node_actions,
    }
    return result
