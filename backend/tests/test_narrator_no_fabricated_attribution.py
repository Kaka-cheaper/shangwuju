"""test_narrator_no_fabricated_attribution —— 叙事不编因（改口根治批 · 任务 4）。

【病灶（治的是这个）】narrator_prompt 的【未满足的品类诉求·这版没安排】块此前
教 LLM 两件编因果的事：
1. 括注"（往往是照用户最新的反馈/约束做的取舍）"——把一个**猜测**当背景事实
   喂给 LLM；
2. "若本条消息还带了【上版回顾】，把没安排的原因与那条反馈自然衔接（如『这版
   按你说的累了调的，就先没排X』）"——明令 LLM 把 recap（真：这版因该反馈触发）
   升格成某个没安排项的**因果解释**（编：引擎没说 X 是因为那条反馈被滤掉的）。

房间四轮"密室回不来"实测里，正是这条指令让叙事把"没排上密室"归因成"按之前
反馈"——引擎从未给过这个原因，纯属编造。

【判据（本文件钉住的新契约）】没有真实原因传入时**只陈述不归因**：
- user message 的"这版没安排"块不得再含"衔接原因"的指令与猜测性括注；
- 该块必须显式指示"不要编原因"；
- 【上版回顾】块保留（recap 是版本志的真实触发记录），但加护栏：回顾只说明
  这版因何触发，不得扩展成没安排项的原因解释；
- system prompt 规则 5 的"（常见于用户新反馈收紧了约束）"猜测括注同步剪除。

与文案批已落地的 unmet 两组分叉（tests/test_narrator_unmet_reason_fork.py 的
9 条断言：分类器 / 模板措辞 / prompt 接线）完全兼容——那批治的是"把取舍说成
找不到"（措辞互串），本批治的是"给取舍编一个原因"（无中生因），同一条诚实
纪律的两半。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.intent.prompts.narrator_prompt import (  # noqa: E402
    NARRATOR_SYSTEM_PROMPT,
    build_narrator_user_message,
)


def _msg(**kwargs) -> str:
    base = dict(
        intent_dict={"companions": [], "social_context": "朋友热闹"},
        itinerary_dict={"summary": "x", "total_minutes": 180, "nodes": [], "orders": []},
        stage_label="stream",
    )
    base.update(kwargs)
    return build_narrator_user_message(**base)


def test_not_scheduled_block_no_causal_link_instruction():
    """recap 与"没安排"同时在场时，不得再教 LLM 把两者衔接成因果。"""
    msg = _msg(
        unmet_not_scheduled=["密室"],
        plan_recap="这版是照你『有点累了』的反馈调过的",
    )
    assert "把没安排的原因与" not in msg, "编因指令仍在（把原因与反馈衔接）"
    assert "就先没排" not in msg, "编因示范句仍在（『这版按你说的累了调的，就先没排X』）"


def test_not_scheduled_block_no_guessed_cause_parenthetical():
    msg = _msg(unmet_not_scheduled=["密室"])
    assert "往往是照" not in msg, "猜测性括注仍在（往往是照用户最新的反馈/约束做的取舍）"


def test_not_scheduled_block_instructs_state_not_attribute():
    """新契约正向面：块内必须显式教"只陈述不归因/不要编原因"。"""
    msg = _msg(unmet_not_scheduled=["密室"])
    assert "只陈述、不归因" in msg or "只陈述不归因" in msg, (
        "『这版没安排』块应显式指示只陈述不归因"
    )
    assert "不要编" in msg, "『这版没安排』块应显式禁止编原因"


def test_not_scheduled_fork_contract_preserved():
    """与 unmet 两组分叉兼容：块标题与"不许说找不到"禁令原样保留
    （test_narrator_unmet_reason_fork.py 的接线断言依赖它们）。"""
    msg = _msg(unmet_not_scheduled=["KTV"])
    assert "这版没安排" in msg and "KTV" in msg
    assert "绝不能说「附近没找到" in msg


def test_recap_block_guarded_against_reason_expansion():
    """【上版回顾】保留（真实触发记录），但必须带"不当原因解释"护栏。"""
    msg = _msg(plan_recap="这版是照你『太远了』的反馈调过的")
    assert "【上版回顾】" in msg, "recap 块本身应保留（版本志真实记录）"
    assert "不要" in msg and "原因" in msg, "recap 块应有护栏：回顾不得扩展成取舍的原因解释"


def test_system_prompt_rule5_no_guessed_cause():
    assert "常见于用户新反馈收紧了约束" not in NARRATOR_SYSTEM_PROMPT, (
        "system prompt 规则 5 的猜测性括注应剪除（原因引擎未给出，不替它编）"
    )
    assert "这版没安排" in NARRATOR_SYSTEM_PROMPT, (
        "原因分叉规则本体应保留（fork 批契约）"
    )
