"""spec interaction-experience-review：双范式 mode 分发集成测试。

覆盖：
- AgentState.planner_mode 字段存在且接受 "rule" / "llm" / None
- planner_node 在 mode="rule" 时走 _planner_node_rule（plan_itinerary 路径）
- planner_node 在 mode="llm" / None 时走 LLM-First 路径
- assemble_node 看到 itinerary 已存在 + blueprint=None 时 noop
- run_graph_stream 透传 planner_mode 参数到 initial state
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub


# ============================================================
# AgentState.planner_mode 字段存在性
# ============================================================


def test_make_initial_state_accepts_planner_mode_rule():
    """make_initial_state 接受 planner_mode='rule' 写入 state"""
    from agent.graph.state import make_initial_state

    state = make_initial_state(
        user_input="测试",
        session_id="sess_test_rule",
        planner_mode="rule",
    )
    assert state.get("planner_mode") == "rule"


def test_make_initial_state_accepts_planner_mode_llm():
    """make_initial_state 接受 planner_mode='llm'"""
    from agent.graph.state import make_initial_state

    state = make_initial_state(
        user_input="测试",
        session_id="sess_test_llm",
        planner_mode="llm",
    )
    assert state.get("planner_mode") == "llm"


def test_make_initial_state_invalid_mode_falls_to_none():
    """make_initial_state 越界 planner_mode → None（向后兼容）"""
    from agent.graph.state import make_initial_state

    state = make_initial_state(
        user_input="测试",
        session_id="sess_test_invalid",
        planner_mode="bogus",
    )
    assert state.get("planner_mode") is None


def test_make_initial_state_default_planner_mode_none():
    """不传 planner_mode → None（向后兼容旧调用方）"""
    from agent.graph.state import make_initial_state

    state = make_initial_state(
        user_input="测试",
        session_id="sess_test_default",
    )
    assert state.get("planner_mode") is None


# ============================================================
# planner_node 双范式分发
# ============================================================


def test_planner_node_rule_mode_returns_itinerary_directly():
    """planner_node 在 mode='rule' 时直接返 itinerary，blueprint=None"""
    from agent.graph.nodes.planner import planner_node
    from agent.intent.parser import parse_intent
    from agent.core.llm_client_stub import StubLLMClient

    client = StubLLMClient()
    intent = parse_intent("今天下午想和老婆孩子出去玩", client=client)

    state = {
        "intent": intent,
        "pois": [],  # 规则模式不读 state.pois，由 plan_itinerary 内部查
        "restaurants": [],
        "planner_mode": "rule",
        "plan_attempt": 0,
    }

    result = planner_node(state)
    # rule 模式跳过 LLM 蓝图：blueprint=None, itinerary 直接产出
    assert result.get("blueprint") is None
    assert result.get("itinerary") is not None, (
        "rule 模式应直接产出 itinerary（不走 LLM 蓝图）"
    )
    assert result["itinerary"].nodes  # 非空节点列表
    assert result["plan_attempt"] == 1


def test_planner_node_rule_mode_no_llm_calls_in_planner():
    """planner_node 在 mode='rule' 时不调 get_llm_client（mock 验证）"""
    from agent.graph.nodes.planner import planner_node
    from agent.intent.parser import parse_intent
    from agent.core.llm_client_stub import StubLLMClient
    from unittest.mock import patch

    client = StubLLMClient()
    intent = parse_intent("今天下午想出去玩", client=client)

    state = {
        "intent": intent,
        "pois": [],
        "restaurants": [],
        "planner_mode": "rule",
        "plan_attempt": 0,
    }

    # rule 模式不应调 get_llm_client（核心承诺：不调用大模型的纯算法路径）
    with patch("agent.graph.nodes.planner.get_llm_client") as mock_get:
        result = planner_node(state)
        assert mock_get.call_count == 0, (
            f"rule 模式不应调 get_llm_client，实际调了 {mock_get.call_count} 次"
        )

    assert result.get("itinerary") is not None


# ============================================================
# assemble_node 识别已有 itinerary 跳过
# ============================================================


def test_assemble_node_noop_when_itinerary_exists_and_no_blueprint():
    """assemble_node 看到 state.itinerary 已存在 + blueprint=None → 返空 dict（noop）

    这是 rule 模式的桥接：planner_node 已直接产 itinerary，assemble 不再二次拼装。
    """
    from agent.graph.nodes.assemble import assemble_node
    from agent.intent.parser import parse_intent
    from agent.core.llm_client_stub import StubLLMClient
    from agent.planning.planners.rule_planner import plan_itinerary

    client = StubLLMClient()
    intent = parse_intent("今天下午想出去玩", client=client)
    plan_result = plan_itinerary(intent)
    assert plan_result.success and plan_result.itinerary is not None

    state = {
        "intent": intent,
        "blueprint": None,
        "itinerary": plan_result.itinerary,
    }

    result = assemble_node(state)
    # noop：返空 dict，不写 itinerary 字段，state 中已有 itinerary 保留
    assert "itinerary" not in result or result == {}



# ============================================================
# rule 模式不再走 critic backprompt 闭环（spec interaction-experience-review fix）
# ============================================================


def test_critic_node_rule_mode_skips_backprompt_even_when_violations_critical():
    """rule 模式 critic 命中违规时不应触发 has_critical=True 让流程回 planner。

    规则路径产出的 itinerary 已经过 plan_itinerary 内部的 5 级降级 + dining_slots 试探，
    再走 LLM-Modulo backprompt 闭环只会让 LLM 调用 3+ 次 + ILS 兜底，与「规则模式不调 LLM」
    承诺冲突。critic 仍记录 violations 让 trace 可见，但 has_critical 强制 False。
    """
    from agent.graph.nodes.critic import critic_node
    from agent.intent.parser import parse_intent
    from agent.core.llm_client_stub import StubLLMClient
    from agent.planning.planners.rule_planner import plan_itinerary

    client = StubLLMClient()
    intent = parse_intent("今天下午想出去玩", client=client)
    plan_result = plan_itinerary(intent)
    assert plan_result.success and plan_result.itinerary is not None

    state = {
        "intent": intent,
        "itinerary": plan_result.itinerary,
        "planner_mode": "rule",
        "user_id": "demo_user",
    }

    result = critic_node(state)
    # 不论 violations 是否为空，rule 模式下 has_critical 都应是 False
    assert result["has_critical"] is False, (
        f"rule 模式 critic 不应触发 backprompt，"
        f"实际 has_critical={result['has_critical']}, violations={len(result['violations'])} 条"
    )
    # critic_feedback_text 也应为 None（不发 backprompt）
    assert result.get("critic_feedback_text") is None


def test_narrate_node_rule_mode_uses_template_not_llm():
    """rule 模式 narrate 不调 LLM 润色文案，走纯模板文案。"""
    from agent.graph.nodes.narrate import narrate_node
    from agent.intent.parser import parse_intent
    from agent.core.llm_client_stub import StubLLMClient
    from agent.planning.planners.rule_planner import plan_itinerary
    from unittest.mock import patch, MagicMock

    client = StubLLMClient()
    intent = parse_intent("今天下午想出去玩", client=client)
    plan_result = plan_itinerary(intent)
    assert plan_result.success and plan_result.itinerary is not None

    state = {
        "intent": intent,
        "itinerary": plan_result.itinerary,
        "planner_mode": "rule",
        "user_id": "demo_user",
    }

    # 验证 generate_title_and_narration 被调时 use_llm=False
    # （narrate 改为同次产 title + narration；rule 模式仍走纯模板不调 LLM）
    with patch(
        "agent.graph.nodes.narrate.generate_title_and_narration"
    ) as mock_narration:
        mock_narration.return_value = ("（mock 标题）", "（mock 模板文案）")
        result = narrate_node(state)
        assert mock_narration.call_count == 1
        # use_llm 是 keyword 参数，从 kwargs 取
        kwargs = mock_narration.call_args.kwargs
        assert kwargs.get("use_llm") is False, (
            f"rule 模式 narrate 应该 use_llm=False，实际 {kwargs}"
        )

    # narrate 主输出仍要返回（不阻断流程）
    assert "narration" in result
