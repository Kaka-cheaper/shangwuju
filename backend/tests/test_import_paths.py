"""测试 spec agent-directory-restructure R2：新 import 路径可用 + 旧路径不可用。

防回归：任何后续 PR 把 backend.agent.intent_parser 等老路径恢复，都会被本测试拦下。
"""

from __future__ import annotations

import importlib

import pytest


# ============================================================
# 新路径必须可 import（spec R2.4 验收）
# ============================================================


def test_core_imports() -> None:
    """agent/core/ 5 个模块。"""
    from agent.core.llm_client import LLMClient  # noqa: F401
    from agent.core.feedback_detector import looks_like_feedback  # noqa: F401
    from agent.core.trace import Tracer  # noqa: F401
    from agent.core import llm_client_stub  # noqa: F401
    from agent.core import observability_init  # noqa: F401


def test_intent_imports() -> None:
    """agent/intent/ 含 parser / refiner / router / narrator + prompts。"""
    from agent.intent.parser import parse_intent  # noqa: F401
    from agent.intent.refiner import refine_intent  # noqa: F401
    from agent.intent.router import classify_input  # noqa: F401
    from agent.intent.narrator import generate_narration  # noqa: F401
    from agent.intent.prompts.intent_parser_prompt import (  # noqa: F401
        INTENT_PARSER_SYSTEM_PROMPT,
    )
    from agent.intent.prompts.refiner_prompt import REFINER_SYSTEM_PROMPT  # noqa: F401
    from agent.intent.prompts.router_prompt import (  # noqa: F401
        ROUTER_SYSTEM_PROMPT,
    )
    from agent.intent.prompts.narrator_prompt import (  # noqa: F401
        NARRATOR_SYSTEM_PROMPT,
    )


def test_planning_imports() -> None:
    """agent/planning/ 含 blueprint / critic / commute / weights_llm。"""
    from agent.planning.blueprint.blueprint import PlanBlueprint  # noqa: F401
    from agent.planning.blueprint.blueprint_llm import generate_blueprint  # noqa: F401
    from agent.planning.blueprint.assemble_blueprint import (  # noqa: F401
        assemble_from_blueprint,
    )
    from agent.planning.blueprint.node_decider import decide_nodes  # noqa: F401
    from agent.planning.blueprint.prompts.blueprint_prompt import (  # noqa: F401
        BLUEPRINT_SYSTEM_PROMPT,
    )
    from agent.planning.critic.critics_v2 import (  # noqa: F401
        ViolationCode,
        validate_itinerary,
    )
    from agent.planning.critic import social_compat  # noqa: F401
    from agent.planning.commute.lookup_hop import lookup_hop  # noqa: F401
    from agent.planning.weights_llm import PlanningWeights  # noqa: F401


def test_runtime_imports() -> None:
    """agent/runtime/ 含 Pydantic AI 框架模块。"""
    from agent.runtime import react_agent  # noqa: F401
    from agent.runtime import output_types  # noqa: F401
    from agent.runtime import orchestrator  # noqa: F401
    from agent.runtime import conversation  # noqa: F401
    from agent.runtime import tool_provider  # noqa: F401
    from agent.runtime import deps  # noqa: F401
    from agent.runtime import model_factory  # noqa: F401
    from agent.runtime import observability  # noqa: F401
    from agent.runtime.tools import search_adapter  # noqa: F401


def test_legacy_imports() -> None:
    """agent/legacy/ 7 个冻结模块全部可 import。"""
    from agent.legacy.planner_rule import plan_itinerary  # noqa: F401
    from agent.legacy.ils_planner import plan_hybrid  # noqa: F401
    from agent.legacy.llm_first_planner import plan_llm_first  # noqa: F401
    from agent.legacy.llm_planner import plan_itinerary_llm  # noqa: F401
    from agent.legacy.ils_score_critic import run_critics  # noqa: F401
    from agent.legacy.executor import execute_plan  # noqa: F401
    from agent.legacy.segment_decider import decide_segments  # noqa: F401


# ============================================================
# 旧路径必须 ImportError（防止回退）
# ============================================================


@pytest.mark.parametrize(
    "old_path",
    [
        "agent.intent_parser",
        "agent.refiner",
        "agent.router",
        "agent.narrator",
        "agent.blueprint",
        "agent.blueprint_llm",
        "agent.assemble_blueprint",
        "agent.node_decider",
        "agent.lookup_hop",
        "agent.weights_llm",
        "agent.llm_client",
        "agent.llm_client_stub",
        "agent.feedback_detector",
        "agent.trace",
        "agent.observability_init",
        "agent.planner",
        "agent.planner_hybrid",
        "agent.planner_llm_first",
        "agent.llm_planner",
        "agent.critics",
        "agent.executor",
        "agent.segment_decider",
        "agent.v2.react_agent",
        "agent.v2.critics_v2",
        "agent.v2.social_compat",
        "agent.v2.observability",
        "agent.tools.search_adapter",
        "agent.prompts.system_prompt",
        "agent.prompts.refiner_prompt",
        "agent.prompts.router_prompt",
        "agent.prompts.narrator_prompt",
        "agent.prompts.blueprint_prompt",
        "agent.prompts.llm_planner_prompt",
    ],
)
def test_old_paths_no_longer_importable(old_path: str) -> None:
    """spec R2.2 验收：所有旧路径必须 ImportError。"""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(old_path)


# ============================================================
# weights_llm.py 含 FROZEN 标记（spec R3.5）
# ============================================================


def test_weights_llm_has_frozen_marker() -> None:
    """planning/weights_llm.py 顶部应含 # FROZEN 标记（虽不在 legacy/，但仅 ILS 路径消费）。"""
    from pathlib import Path

    weights_path = (
        Path(__file__).resolve().parent.parent
        / "agent"
        / "planning"
        / "weights_llm.py"
    )
    head = weights_path.read_text(encoding="utf-8").splitlines()[:20]
    assert any("# FROZEN" in line for line in head), (
        "planning/weights_llm.py 应在文件头加 # FROZEN: 仅 ILS 路径"
    )
