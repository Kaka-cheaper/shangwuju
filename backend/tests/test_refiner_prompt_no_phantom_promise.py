"""test_refiner_prompt_no_phantom_promise —— 剪空头支票（改口根治批 · 任务 3）。

【病灶（治的是这个）】refiner_prompt 的 C 规则（"只是想换一个备选"）教 LLM 往
`ambiguous_fields` 写「上次推荐的 X 不行」并宣称「planner 后续会避开」——读码
核实（tests/test_consumption_completeness.py 轴 3 的读码记录）：该字段的真实
程序消费者只有两个——narrator 读它说"哪些没吃准"、parser 用它做
budget_per_person 定性表达的诚实信号；**没有任何 planner 路径按它排除实体**。
"planner 会避开"是空头支票：对用户（refiner_note 承诺避开）和对下游（把自由
句子塞进本应放"待澄清字段名"的列表，污染 narrator 的"没吃准"叙事）同时撒谎。

【判据变更（本文件钉住的新契约）】
1. system prompt 不得再出现"planner 会避开 / 后续避开"类承诺；
2. few-shot 输出的 ambiguous_fields 不得含"避开/不行"类自由句子——该字段回归
   本职（真正需要向用户澄清的字段名，如 "budget_per_person"，few-shot 6 的用法）；
3. few-shot 的 refiner_note / changed_fields 不得承诺"避开某家"（系统没有按店名
   排除的机制；换备选的诚实说法是"重新配一版"——反馈原话仍经 raw_input 拼接
   到达下游 LLM，避开可以自然涌现，但不能被承诺）。

C 类反馈的语义本身（字段基本不动）原样保留——变的只是"不再写无人消费的备注、
不再许诺做不到的事"。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.intent.prompts.refiner_prompt import (  # noqa: E402
    REFINER_FEW_SHOTS,
    REFINER_SYSTEM_PROMPT,
)


def _few_shot_payloads() -> list[dict]:
    return [json.loads(assistant) for _user, assistant in REFINER_FEW_SHOTS]


def test_system_prompt_has_no_planner_avoidance_promise():
    """"planner 会避开"类承诺无程序消费者=假话，prompt 不得再教。"""
    for phrase in ("planner 后续会避开", "让 planner 后续避开", "planner 会避开"):
        assert phrase not in REFINER_SYSTEM_PROMPT, f"空头支票短语仍在 prompt 里：{phrase!r}"


def test_system_prompt_does_not_teach_avoidance_notes_into_ambiguous_fields():
    """C 规则不得再教"在 ambiguous_fields 记『上次推荐的 X 不行』"。"""
    assert "在 ambiguous_fields 记下" not in REFINER_SYSTEM_PROMPT
    assert "在 ambiguous_fields 加备注" not in REFINER_SYSTEM_PROMPT


def test_few_shot_ambiguous_fields_keep_native_semantics():
    """few-shot 输出的 ambiguous_fields 只放字段名信号，不放"避开某家"自由句。"""
    for payload in _few_shot_payloads():
        fields = payload["refined_intent"].get("ambiguous_fields", [])
        for f in fields:
            assert "不行" not in f and "避开" not in f and "换一家" not in f, (
                f"ambiguous_fields 出现避开类自由句（本职是待澄清字段名）：{f!r}"
            )


def test_few_shot_notes_do_not_promise_avoidance():
    """refiner_note / changed_fields 不得承诺"避开 X"（做不到的事不许说）。"""
    for payload in _few_shot_payloads():
        note = payload.get("refiner_note", "")
        assert "避开" not in note, f"refiner_note 仍在承诺避开：{note!r}"
        for cf in payload.get("changed_fields", []):
            assert "避开" not in cf, f"changed_fields 仍在宣称避开：{cf!r}"


def test_budget_clarify_signal_usage_survives():
    """本职用法不许被误伤：few-shot 6 的 ambiguous_fields=["budget_per_person"]
    （字段名澄清信号，narrator/parser 的真实消费形态）必须仍然存在。"""
    assert any(
        "budget_per_person" in payload["refined_intent"].get("ambiguous_fields", [])
        for payload in _few_shot_payloads()
    ), "ambiguous_fields 的本职示范（字段名信号）不应随空头支票一起被剪掉"


# ============================================================
# B2 · A1：refiner understanding 护栏（C 类不许承诺 swap 结果）
# ============================================================
#
# 【病灶】refiner 的 understanding 字段在方案重新跑之前生成——此刻还不知道
# 最后到底换没换、换成了谁。若它写"把原来的点换掉了/已经换成 X"这类结果性
# 断言，而实际重排后 0 处变化（node_swap.py 明写"系统没有按店名排除的机制"，
# C 类反馈走全局重排大概率选出同一批候选——真实 bug 场景），这句 understanding
# 本身就是一句空话/假话，与 refiner_note/changed_fields 此前"不承诺避开某家"
# 的整改是同一根因的另一处症状，理应同一批治掉。

_RESULT_CLAIM_PHRASES: tuple[str, ...] = (
    "换掉了", "已经换成", "已经换掉", "帮你换了", "已换成", "换成了",
)


def test_system_prompt_forbids_result_claims_in_understanding():
    """system prompt 必须明确禁止 understanding 写结果性断言（"换掉了"类）。"""
    assert "结果性断言" in REFINER_SYSTEM_PROMPT, (
        "system prompt 应新增 understanding 的 C 类专属红线说明（结果性断言）"
    )
    assert any(p in REFINER_SYSTEM_PROMPT for p in _RESULT_CLAIM_PHRASES), (
        "system prompt 应点名举例禁止的结果性断言短语，供 LLM 对照"
    )


def test_few_shot_understanding_never_claims_swap_already_happened():
    """全部 few-shot 的 understanding 字段都不得出现"已经换/换掉了"类结果性
    断言——即使是 C 类（换备选）示范，也只能预告"打算重新配一版"，不能宣布
    "已经换了"这个此刻并不存在、也不保证的结果。"""
    for payload in _few_shot_payloads():
        understanding = payload["refined_intent"].get("understanding", "")
        for phrase in _RESULT_CLAIM_PHRASES:
            assert phrase not in understanding, (
                f"understanding 仍在承诺 swap 结果：{understanding!r}（命中短语 {phrase!r}）"
            )


def test_few_shot_c_class_understanding_predicts_intent_not_outcome():
    """C 类换备选（few-shot 5）的 understanding 应是"预告打算怎么处理"的句式
    （如"重新配一版备选"），不是宣布已完成的动作。"""
    payloads = _few_shot_payloads()
    c_class = payloads[4]["refined_intent"]["understanding"]
    assert "备选" in c_class or "重新" in c_class, (
        f"C 类 understanding 应预告处理方式而非宣布结果：{c_class!r}"
    )
