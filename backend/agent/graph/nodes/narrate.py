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
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import AgentState
from agent.core.llm_client import get_llm_client
from agent.intent.narrator import generate_title_and_narration


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

    # D-7：state.advisories（planner「绝不默默忽略」的结构化告知）→ narrator 的
    # 完整句子列表。
    advisories = _extract_advisories(state)
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

    # 同次产出：title（小红书风格大标题，写回 itinerary.summary）+ narration（开场白）
    # title 必须覆盖所有主要站点（旧 bug：只取停留最久的单站）。
    title, text = generate_title_and_narration(
        intent=intent,
        itinerary=itinerary,
        stage="stream",
        use_llm=use_llm,
        critic_summary=critic_summary,
        quality_warnings=quality_warnings,
        unmet_cuisines=unmet_desires,
        advisories=advisory_messages,
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
    result: dict[str, Any] = {
        "narration": text,
        "itinerary": new_itinerary,
        "advisories": advisories,
    }
    return result
