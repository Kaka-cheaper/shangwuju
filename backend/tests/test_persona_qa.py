"""test_persona_qa —— 用户画像问答（我是谁 / 我的画像 / 你了解我）。

修复 bug：选了画像后问"我的用户画像是什么"被 itinerary QA 误捕获 → 弃答"没有您的信息"。
现在用系统 persona + 累积偏好 grounded 作答，且优先于 itinerary QA。
conftest 已把 SHANGWUJU_MOCK_DIR 指向仓库 mock_data，persona（u_dad/demo_user=新手爸爸）可用。

【判据变更理由（记忆身份读写分离批，ADR-0015 身份边界补充决策，2026-07-05）】
answer_persona_question 从单键（user_id）改双键：**模板按 user_id（共享只读，
label / persona 默认 tag），累积按 session_id（会话私有）**。不传 session 时只答
模板——多访客并发下，A 会话确认攒下的偏好绝不出现在 B 会话的画像回答里。
"""

from __future__ import annotations

import pytest

from agent.core.persona_qa import (
    answer_persona_question,
    build_persona_decision,
    looks_like_persona_question,
)
from data.memory_store import record_accepted, reset_all_memory


@pytest.fixture(autouse=True)
def _clean_memory():
    reset_all_memory()
    yield
    reset_all_memory()


# ---- 识别（含其与邻居的边界）----

def test_persona_question_recognition():
    assert looks_like_persona_question("我是谁？我的用户画像是什么")
    assert looks_like_persona_question("你了解我什么")
    assert looks_like_persona_question("我的偏好是什么")


def test_persona_excludes_neighbors():
    assert not looks_like_persona_question("你是谁"), "问 AI 身份 → chitchat（原 meta 已塌缩），不是 persona"
    assert not looks_like_persona_question("我喜欢热闹"), "陈述偏好 → 提约束，不是问画像"
    assert not looks_like_persona_question("这家贵吗"), "方案提问 → itinerary QA"


# ---- grounded 作答：模板侧（persona label + persona 默认 tag） ----

def test_answer_uses_persona_template_data():
    """不带会话累积时的答案 = 模板：label + persona 自带 tag（如 亲子友好）。"""
    a = answer_persona_question("demo_user")
    assert "新手爸爸" in a       # persona.label（模板，按 user_id）
    assert "亲子友好" in a       # persona 默认 tag 进 top_priors（模板侧）


# ---- 累积侧：按 session 键控，会话私有 ----

def test_answer_includes_session_accumulated_prefs():
    """本会话确认攒下的偏好（权重压过模板）应出现在带 session 的回答里。"""
    for _ in range(5):
        record_accepted("sess_pq_a", tags=["商务体面"])
    a = answer_persona_question("demo_user", "sess_pq_a")
    assert "商务体面" in a, "会话内累积应体现在同会话的画像回答里（A 方案保留的核心能力）"


def test_answer_does_not_leak_other_sessions_prefs():
    """别的会话攒的偏好绝不出现——会话即身份，跨访客不串味。"""
    for _ in range(5):
        record_accepted("sess_pq_a", tags=["商务体面"])
    b = answer_persona_question("demo_user", "sess_pq_b")
    assert "商务体面" not in b, "B 会话读到了 A 会话的累积偏好——身份串味"
    # 不传 session（如无会话上下文的调用方）同样只答模板
    none_ = answer_persona_question("demo_user")
    assert "商务体面" not in none_


def test_build_persona_decision():
    d = build_persona_decision("我的用户画像是什么", "demo_user", None)
    assert d is not None and d.input_kind.value == "chitchat"
    assert d.rationale == "persona_question"
    assert "新手爸爸" in d.reply_text
    assert build_persona_decision("这家贵吗", "demo_user", None) is None


# ---- router 集成：画像问题不再弃答，用画像答（且不调 LLM）----

def test_router_persona_question_answered():
    from agent.graph.nodes import router as router_mod
    from agent.graph.state import make_initial_state

    st = make_initial_state(user_input="我是谁？我的用户画像是什么", session_id="s")
    st["itinerary"] = {
        "nodes": [
            {"target_kind": "home", "target_id": "home"},
            {"target_kind": "poi", "target_id": "P027"},
        ]
    }
    out = router_mod.router_node(st)
    assert out["route_kind"] == "chitchat"
    # 用画像数据回答，而不是「方案里没有这条信息」的弃答
    assert "新手爸爸" in out["router_decision"].reply_text


def test_router_passes_session_key_for_accumulation():
    """router 节点必须把图状态里的 session_id 传给画像问答（累积键）。"""
    from agent.graph.nodes import router as router_mod
    from agent.graph.state import make_initial_state

    for _ in range(5):
        record_accepted("sess_router_pq", tags=["商务体面"])

    st = make_initial_state(user_input="我的偏好是什么", session_id="sess_router_pq")
    out = router_mod.router_node(st)
    assert out["route_kind"] == "chitchat"
    assert "商务体面" in out["router_decision"].reply_text, (
        "同会话累积的偏好应经 router → persona_qa 链路答出（session 键没接通）"
    )
