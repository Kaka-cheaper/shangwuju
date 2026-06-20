"""验 spec planning-quality-deep-review R6+R7 主动质疑 + state 一致性修复（Task 6）。

测试矩阵：
1. critic_summary 触发 LLM 主动质疑（fake LLM 返回含「宝贝可能会累」关键词）
2. template 兜底质疑（含 ≤6 岁孩 + duration_min > 90 时强制追加质疑短语）
3. 5 岁娃场景文案多样性（不同 social_context 不输出固定模板）
4. DONE payload 6 字段（final_strategy / plan_attempts / critic_attempt_count /
   fallback_hops_count / total_ms / has_itinerary）
5. refiner 重置 trace（critic_attempts / fallback_chain / alternatives /
   quality_issues 4 字段都被清空）

测试套路对齐 test_age_aware_critic.py 的 sys.modules 桥接：
- 复用 agent 命名空间 stub，避免 agent/__init__.py eager-import 老 schema 炸；
- 不依赖真实 LLM 连接（用 monkeypatch 替换 get_llm_client）。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any


def _install_agent_stub() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    agent_dir = backend_root / "agent"
    v2_dir = agent_dir / "v2"
    graph_dir = agent_dir / "graph"
    nodes_dir = graph_dir / "nodes"
    prompts_dir = agent_dir / "prompts"

    for mod_name, mod_path in [
        ("agent", agent_dir),
        ("agent.v2", v2_dir),
        ("agent.graph", graph_dir),
        ("agent.graph.nodes", nodes_dir),
        ("agent.prompts", prompts_dir),
    ]:
        if mod_name not in sys.modules or not hasattr(sys.modules[mod_name], "__path__"):
            stub = types.ModuleType(mod_name)
            stub.__path__ = [str(mod_path)]
            sys.modules[mod_name] = stub


_install_agent_stub()


from agent.intent.narrator import (  # noqa: E402
    _template_narration,
    generate_narration,
)
from agent.intent.prompts.narrator_prompt import (  # noqa: E402
    NARRATOR_SYSTEM_PROMPT,
    build_narrator_user_message,
)
from schemas.intent import Companion, IntentExtraction  # noqa: E402
from schemas.itinerary import (  # noqa: E402
    ActivityNode,
    Hop,
    Itinerary,
)
from schemas.decision_trace import (  # noqa: E402
    CriticAttempt,
    DecisionTrace,
    FallbackHop,
)


# ============================================================
# Fixtures
# ============================================================


def _make_intent(
    *,
    companions: list[Companion],
    social: str = "家庭日常",
) -> IntentExtraction:
    return IntentExtraction(
        raw_input="测试",
        social_context=social,
        companions=companions,
        duration_hours=[4, 6],
        distance_max_km=5.0,
        start_time="14:00",
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        parse_confidence=0.95,
    )


def _make_itinerary(
    *,
    poi_duration: int = 75,
    poi_title: str = "玩贝亲子博物馆",
    decision_trace: DecisionTrace | None = None,
) -> Itinerary:
    """构造满足 edge_v1 不变量的最小 Itinerary：home → POI → home。"""
    nodes = [
        ActivityNode(
            node_id="n_home_start",
            kind="起点",
            target_kind="home",
            target_id="home",
            start_time="14:00",
            duration_min=0,
            title="出发",
        ),
        ActivityNode(
            node_id="n_1",
            kind="主活动",
            target_kind="poi",
            target_id="P003",
            start_time="14:15",
            duration_min=poi_duration,
            title=poi_title,
        ),
        ActivityNode(
            node_id="n_home_end",
            kind="终点",
            target_kind="home",
            target_id="home",
            start_time=f"15:{poi_duration % 60:02d}",  # 占位即可，invariant 不验
            duration_min=0,
            title="回家",
        ),
    ]
    hops = [
        Hop(
            hop_id="h_0",
            from_node_id="n_home_start",
            to_node_id="n_1",
            start_time="14:00",
            minutes=15,
            mode="taxi",
            path_type="real_route",
        ),
        Hop(
            hop_id="h_1",
            from_node_id="n_1",
            to_node_id="n_home_end",
            start_time="15:00",
            minutes=12,
            mode="taxi",
            path_type="real_route",
        ),
    ]
    return Itinerary(
        summary="测试方案",
        nodes=nodes,
        hops=hops,
        total_minutes=poi_duration + 27,
        decision_trace=decision_trace,
    )


# ============================================================
# 1) critic_summary 触发 LLM 主动质疑
# ============================================================


def test_critic_summary_triggers_llm_active_query(monkeypatch) -> None:
    """LLM 路径：传入 critic_summary 时，prompt user message 含「主动质疑规则」触发指令；
    fake LLM 返回的文案被原样透传出去。"""
    from agent.intent import narrator as narrator_mod

    captured_messages: list[Any] = []

    class FakeResp:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeClient:
        provider = "deepseek"

        def chat(self, *, messages, temperature, **kwargs):  # noqa: ANN001
            captured_messages.append(messages)
            captured_messages.append(temperature)
            return FakeResp(
                "这是和老婆孩子下午 1.5 小时的安排——14:15 到玩贝亲子博物馆陪宝贝，"
                "考虑到 5 岁宝贝的注意力，主活动我已经控制好不会让宝贝累；"
                "哪里不合适跟我说一声。"
            )

    monkeypatch.setattr(narrator_mod, "get_llm_client", lambda *args, **kwargs: FakeClient())

    intent = _make_intent(companions=[Companion(role="孩子", age=5, count=1)])
    itin = _make_itinerary(poi_duration=75)

    text = generate_narration(
        intent=intent,
        itinerary=itin,
        stage="stream",
        use_llm=True,
        critic_summary="经过 1 次 critic 修正——第 1 次（age_duration_mismatch · 已修复）",
    )

    # 1. LLM 真被调用了
    assert captured_messages, "LLM client.chat 未被调用"
    # 2. system message 是 NARRATOR_SYSTEM_PROMPT，含「主动质疑规则」段
    msgs = captured_messages[0]
    assert any("主动质疑规则" in m.content for m in msgs if m.role == "system")
    # 3. user message 含 critic_summary 字段触发指令
    user_msg = next(m.content for m in msgs if m.role == "user")
    assert "critic 历史" in user_msg
    assert "age_duration_mismatch" in user_msg
    # 4. 温度从 0.7 降到 0.5（spec R6 硬条款）
    assert captured_messages[1] == 0.5
    # 5. LLM 输出原样返回（含质疑关键词）
    assert "宝贝" in text
    assert "累" in text or "注意力" in text


# ============================================================
# 2) template 兜底质疑：含 ≤6 岁孩 + duration > 90
# ============================================================


def test_template_fallback_active_query_for_young_kid_long_session() -> None:
    """LLM 失败时走模板兜底；含 ≤6 岁孩 + 任 node.duration_min > 90 强制加质疑短语。"""
    intent = _make_intent(companions=[Companion(role="孩子", age=5, count=1)])
    itin = _make_itinerary(poi_duration=120, poi_title="玩贝亲子博物馆")

    # use_llm=False 直接走模板
    text = generate_narration(
        intent=intent, itinerary=itin, stage="stream", use_llm=False
    )

    # 必须命中质疑关键词之一（spec R6）
    assert "累" in text or "注意力" in text or "中途休息" in text or "略长" in text, (
        f"模板未触发兜底质疑，文案：{text}"
    )
    # 必须含具体节点信息
    assert "玩贝亲子博物馆" in text
    assert "120" in text


def test_template_no_active_query_when_no_young_kid() -> None:
    """没有 ≤6 岁孩 → 模板不应硬加质疑（避免做作）。"""
    intent = _make_intent(
        companions=[Companion(role="老婆", age=30, count=1)],
        social="家庭日常",
    )
    itin = _make_itinerary(poi_duration=120)
    text = generate_narration(
        intent=intent, itinerary=itin, stage="stream", use_llm=False
    )
    # 不应出现"宝贝可能会累 / 中途休息"这种针对小孩的措辞
    assert "宝贝" not in text
    assert "累" not in text or "宝贝" not in text


def test_template_no_active_query_when_short_session() -> None:
    """≤6 岁孩 + 但所有节点 ≤90min → 不加质疑。"""
    intent = _make_intent(companions=[Companion(role="孩子", age=5, count=1)])
    itin = _make_itinerary(poi_duration=75)
    text = generate_narration(
        intent=intent, itinerary=itin, stage="stream", use_llm=False
    )
    # 75 < 90 → 不应触发"累"质疑
    assert "可能会累" not in text


# ============================================================
# 3) 5 岁娃场景文案多样性（不同 social_context 走不同口吻）
# ============================================================


def test_template_diversity_across_social_contexts() -> None:
    """同一 5 岁娃场景，social_context 不同 → opener 不同。"""
    kid = Companion(role="孩子", age=5, count=1)
    intent_family = _make_intent(companions=[kid], social="家庭日常")
    intent_solo = _make_intent(companions=[], social="独处放空")
    intent_biz = _make_intent(
        companions=[Companion(role="商务客户", age=40, count=1)],
        social="商务接待",
    )

    itin = _make_itinerary(poi_duration=75)

    text_family = _template_narration(intent_family, itin, "stream")
    text_solo = _template_narration(intent_solo, itin, "stream")
    text_biz = _template_narration(intent_biz, itin, "stream")

    # 三种 social_context 必须输出不同的 opener
    assert text_family != text_solo
    assert text_family != text_biz
    assert text_solo != text_biz

    # 关键 opener 词
    assert "家庭" in text_family or "孩子" in text_family
    assert "安静" in text_solo or "独处" in text_solo or "下午" in text_solo
    assert "接待" in text_biz or "商务" in text_biz


# ============================================================
# 4) DONE payload 6 字段
# ============================================================


def test_done_event_payload_has_six_summary_fields(monkeypatch) -> None:
    """sse_adapter run_graph_stream 的 DONE 事件 payload 必含 6 字段。"""
    from agent.graph import sse_adapter as sse_mod
    from schemas.sse import SseEventType

    # Fake 一个 compiled graph：astream 直接产出一个 narrate 节点的 update，
    # update 中带 itinerary（含 decision_trace.final_strategy="ils"）
    trace = DecisionTrace(
        blueprint_rationale="test",
        weights_explanation="重舒适 0.4",
        critic_attempts=[
            CriticAttempt(
                attempt_n=1,
                violation_codes=["age_duration_mismatch"],
                feedback_summary="5 岁娃 165min 超 75min 上限",
                resolved=True,
            ),
        ],
        fallback_chain=[
            FallbackHop(
                from_stage="llm_first",
                to_stage="ils",
                reason="LLM 三次未通过 critic",
            ),
        ],
        final_strategy="ils",
    )

    fake_itin = _make_itinerary(poi_duration=75, decision_trace=trace)

    class FakeGraph:
        async def astream(self, initial, config, stream_mode):  # noqa: ANN001
            # 一次性产出 narrate 节点更新
            yield {"narrate": {"narration": "ok", "itinerary": fake_itin}}
            # 一次性产出 planner 节点更新（写 plan_attempt=2）
            yield {"planner": {"plan_attempt": 2, "blueprint": None, "weights": None}}

    monkeypatch.setattr(sse_mod, "get_compiled_graph", lambda: FakeGraph())

    async def run() -> list[Any]:
        events = []
        async for ev in sse_mod.run_graph_stream(
            user_input="5 岁娃下午想出去玩",
            session_id="test_sess",
            user_id="demo_user",
        ):
            events.append(ev)
        return events

    events = asyncio.run(run())

    # 找最后一个 DONE 事件
    done_events = [e for e in events if e.type == SseEventType.DONE]
    assert len(done_events) == 1, f"期望 1 个 DONE 事件，实际 {len(done_events)}"

    done = done_events[0]
    payload = done.payload

    # 6 字段全在
    expected_keys = {
        "final_strategy",
        "plan_attempts",
        "critic_attempt_count",
        "fallback_hops_count",
        "total_ms",
        "has_itinerary",
    }
    assert expected_keys.issubset(payload.keys()), (
        f"DONE payload 缺字段：{expected_keys - set(payload.keys())}"
    )

    # 字段值正确
    assert payload["final_strategy"] == "ils"
    assert payload["plan_attempts"] == 2
    assert payload["critic_attempt_count"] == 1
    assert payload["fallback_hops_count"] == 1
    assert payload["has_itinerary"] is True
    assert isinstance(payload["total_ms"], int)
    assert payload["total_ms"] >= 0


# ============================================================
# 5) refiner 重置 trace 4 字段
# ============================================================


def test_refiner_node_resets_trace_fields(monkeypatch) -> None:
    """refiner_node 返回 dict 必含 critic_attempts/fallback_chain/alternatives/
    quality_issues 4 字段，且全部为空列表（重置）。"""
    from agent.graph.nodes import refiner as refiner_mod

    # Fake refine_intent：原样返回一个 RefinerOutput-like 对象
    class FakeRefinerOutput:
        def __init__(self, refined: IntentExtraction) -> None:
            self.refined_intent = refined

    refined = _make_intent(companions=[Companion(role="孩子", age=5, count=1)])
    monkeypatch.setattr(
        refiner_mod, "refine_intent",
        # **kwargs 吃掉 client / itinerary_summary 等关键字参数，免得节点新增入参时这个 fake 失配
        lambda original, feedback_text, **kwargs: FakeRefinerOutput(refined),
    )

    class FakeClient:
        provider = "deepseek"

    monkeypatch.setattr(refiner_mod, "get_llm_client", lambda *args, **kwargs: FakeClient())

    # 输入 state 模拟「上一轮已有 critic_attempts / fallback_chain」
    state: dict[str, Any] = {
        "intent": _make_intent(companions=[Companion(role="孩子", age=5, count=1)]),
        "user_input": "太久了",
        "critic_attempts": [{"attempt_n": 1, "violation_codes": ["x"]}],
        "fallback_chain": [{"from_stage": "llm_first", "to_stage": "ils"}],
        "alternatives": [{"target_id": "P099"}],
        "quality_issues": ["残留警告"],
    }

    out = refiner_mod.refiner_node(state)  # type: ignore[arg-type]

    # 4 字段全部被重置为空 list
    assert out["critic_attempts"] == []
    assert out["fallback_chain"] == []
    assert out["alternatives"] == []
    assert out["quality_issues"] == []
    # routes 字段已删除（state.py 同步删了死字段；refiner 不应再写）
    assert "routes" not in out
    # decision_trace 也被重置（让下一轮 assemble 重新计算）
    assert out.get("decision_trace") is None
    # 候选数据失效，让 execute 重新搜
    assert out["pois"] == []
    assert out["restaurants"] == []
    # 反馈合并的核心：refined_intent 透传
    assert out["intent"] is refined


# ============================================================
# 附加：build_narrator_user_message 带 critic_summary 时输出含触发指令
# ============================================================


def test_build_user_message_embeds_critic_summary() -> None:
    """单测 user message 拼接逻辑（独立于 LLM）。"""
    user_msg = build_narrator_user_message(
        intent_dict={"companions": [{"role": "孩子", "age": 5}], "social_context": "家庭日常"},
        itinerary_dict={"summary": "x", "total_minutes": 150, "nodes": [], "orders": []},
        stage_label="stream",
        critic_summary="经过 2 次修正",
        quality_warnings=["老人单段过长"],
    )
    assert "critic 历史" in user_msg
    assert "经过 2 次修正" in user_msg
    assert "质量提醒" in user_msg
    assert "老人单段过长" in user_msg
    # 没传两个字段时不应有这两段
    user_msg_clean = build_narrator_user_message(
        intent_dict={"companions": [], "social_context": "家庭日常"},
        itinerary_dict={"summary": "x", "total_minutes": 150, "nodes": [], "orders": []},
        stage_label="stream",
    )
    assert "critic 历史" not in user_msg_clean
    assert "质量提醒" not in user_msg_clean


# ============================================================
# 附加：NARRATOR_SYSTEM_PROMPT 含「主动质疑规则」+ ≥2 条 few-shot
# ============================================================


def test_system_prompt_contains_active_query_rules_and_examples() -> None:
    """spec R6：system prompt 必须含「主动质疑规则」段 + ≥2 条 few-shot。"""
    assert "主动质疑规则" in NARRATOR_SYSTEM_PROMPT
    # ≥ 2 条规则
    assert "规则 1" in NARRATOR_SYSTEM_PROMPT
    assert "规则 2" in NARRATOR_SYSTEM_PROMPT
    # ≥ 2 条 few-shot
    assert "示例 A" in NARRATOR_SYSTEM_PROMPT
    assert "示例 B" in NARRATOR_SYSTEM_PROMPT
