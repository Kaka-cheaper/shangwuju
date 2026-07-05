"""tests.test_narrator_precomputed_numbers —— 分界修缮批 任务 3：叙事数字代码算。

病灶（全后端 LLM/规则分界普查实锤，narrator_prompt.py）：LLM 简报喂原始
total_minutes 让 LLM 自己除（"5.7 小时"），few-shot 还示范「19:30 回家」但
itinerary_brief 剔除了 home 节点且不含 hops——回家时刻 LLM 只能编；模板路径
同一数字由代码算（narrator.py::_template_narration）。

修法：`build_narrator_user_message` 预格式化 `total_hours_display`（与模板路径
同一算法：total_minutes/60 保留 1 位小数）与 `return_home_time`（nodes 末尾
home 节点的 start_time）喂给 LLM；prompt 要求数字照抄不自算；few-shot 示范
输入里带这两个字符串。无 home 终点节点 → 不提供该字段 + prompt 要求不编造
回家时刻。
"""

from __future__ import annotations

from agent.intent.prompts.narrator_prompt import (
    NARRATOR_SYSTEM_PROMPT,
    build_narrator_user_message,
)


def _nodes(*, with_home_end: bool = True) -> list[dict]:
    nodes = [
        {"kind": "出发", "target_kind": "home", "start_time": "14:00",
         "duration_min": 0, "title": "家", "note": None},
        {"kind": "主活动", "target_kind": "poi", "start_time": "14:20",
         "duration_min": 120, "title": "悦读亲子绘本馆", "note": None},
        {"kind": "用餐", "target_kind": "restaurant", "start_time": "17:30",
         "duration_min": 60, "title": "鲸落·健康简餐", "note": "已为你预留 17:30（2 人）"},
    ]
    if with_home_end:
        nodes.append(
            {"kind": "返程", "target_kind": "home", "start_time": "19:30",
             "duration_min": 0, "title": "家", "note": None}
        )
    return nodes


def _msg(*, total_minutes: int = 342, with_home_end: bool = True) -> str:
    return build_narrator_user_message(
        intent_dict={},
        itinerary_dict={
            "summary": "x",
            "total_minutes": total_minutes,
            "nodes": _nodes(with_home_end=with_home_end),
            "orders": [],
        },
        stage_label="stream",
    )


def test_user_message_carries_precomputed_total_hours_display():
    """342 分钟 → 「5.7 小时」由代码算好喂给 LLM（与模板路径同一算法），
    不喂原始分钟数让 LLM 自己除。"""
    msg = _msg(total_minutes=342)
    assert "total_hours_display" in msg
    assert "5.7 小时" in msg
    assert '"total_minutes"' not in msg, "原始分钟数不该再进简报——喂了 LLM 就会自己算"


def test_user_message_carries_return_home_time_from_home_end_node():
    """回家时刻从 nodes 末尾 home 节点取（代码已有该值），LLM 照抄不编。"""
    msg = _msg()
    assert "return_home_time" in msg
    assert "19:30" in msg


def test_user_message_without_home_end_omits_return_home_time():
    """无 home 终点节点 → 不提供该字段（prompt 侧要求缺失时绝不编回家时刻）。"""
    msg = _msg(with_home_end=False)
    assert "return_home_time" not in msg


def test_system_prompt_requires_verbatim_numbers():
    """system prompt 必须声明两个预格式化字段 + 照抄纪律 + 缺失不编造。"""
    assert "total_hours_display" in NARRATOR_SYSTEM_PROMPT
    assert "return_home_time" in NARRATOR_SYSTEM_PROMPT
    assert "照抄" in NARRATOR_SYSTEM_PROMPT


def test_fewshot_inputs_demonstrate_display_strings():
    """few-shot 示范输入里带这两个字符串——示范「数字从输入照抄」而不是
    示范「LLM 自己算出 5.7 / 编出 19:30」。"""
    assert "total_hours_display=5.7 小时" in NARRATOR_SYSTEM_PROMPT
    assert "return_home_time=19:30" in NARRATOR_SYSTEM_PROMPT
