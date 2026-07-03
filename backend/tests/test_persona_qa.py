"""test_persona_qa —— 用户画像问答（我是谁 / 我的画像 / 你了解我）。

修复 bug：选了画像后问"我的用户画像是什么"被 itinerary QA 误捕获 → 弃答"没有您的信息"。
现在用系统 persona + 累积偏好 grounded 作答，且优先于 itinerary QA。
conftest 已把 SHANGWUJU_MOCK_DIR 指向仓库 mock_data，persona（u_dad/demo_user=新手爸爸）可用。
"""

from __future__ import annotations

from agent.core.persona_qa import (
    answer_persona_question,
    build_persona_decision,
    looks_like_persona_question,
)


# ---- 识别（含其与邻居的边界）----

def test_persona_question_recognition():
    assert looks_like_persona_question("我是谁？我的用户画像是什么")
    assert looks_like_persona_question("你了解我什么")
    assert looks_like_persona_question("我的偏好是什么")


def test_persona_excludes_neighbors():
    assert not looks_like_persona_question("你是谁"), "问 AI 身份 → chitchat（原 meta 已塌缩），不是 persona"
    assert not looks_like_persona_question("我喜欢热闹"), "陈述偏好 → 提约束，不是问画像"
    assert not looks_like_persona_question("这家贵吗"), "方案提问 → itinerary QA"


# ---- grounded 作答：用真实 persona 数据 ----

def test_answer_uses_persona_data():
    a = answer_persona_question("demo_user")
    assert "新手爸爸" in a       # persona.label
    assert "亲子友好" in a       # 累积偏好（top_priors）


def test_build_persona_decision():
    d = build_persona_decision("我的用户画像是什么", "demo_user")
    assert d is not None and d.input_kind.value == "chitchat"
    assert d.rationale == "persona_question"
    assert "新手爸爸" in d.reply_text
    assert build_persona_decision("这家贵吗", "demo_user") is None


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
