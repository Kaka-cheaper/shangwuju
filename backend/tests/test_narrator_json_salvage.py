"""test_narrator_json_salvage —— 叙事气泡显示原始 JSON 的 bug 根治。

真机实锤（Image 15）：反馈轮叙事复述用户原话「"吃饭前想去个KTV"」时，那个
未转义的双引号提前闭合了 JSON 字符串 → `json.loads` 炸 → 旧代码把整段
`{"title":...,"narration":...,"node_chips":...}` 原样当 narration 抖给用户。

根治三层：①源头 response_format=json_object（本文件不覆盖 LLM 调用，见
narrator._call_llm_narrator）②抢救 `_salvage_narration` 从坏 JSON 抠出 narration
③硬兜底：抠不出且内容像 JSON → 返回空串（调用方 `llm_narration or 模板` 自动
回落干净模板），**绝不 dump 原始 JSON**。本文件钉②③。
"""

from __future__ import annotations

from agent.intent.narrator import _parse_title_narration, _salvage_narration


# ---- ② 抢救：坏 JSON（narration 嵌未转义引号）→ 救回那句正常叙事 ----

def test_salvage_narration_with_unescaped_inner_quotes():
    """真机 bug 的最小复现：narration 值里有 ASCII 双引号（复述用户原话），
    json.loads 必炸。抢救应抠出完整 narration（含内层引号），不吐 JSON。"""
    raw = (
        '{"title": "和兄弟的3.7h", '
        '"narration": "和兄弟们的夜场。这版是照你上次说的"吃饭前想去个KTV"调的，先唱再吃。", '
        '"node_chips": [{"target_id": "P1", "dimension": "cheaper"}]}'
    )
    title, narration, _chips = _parse_title_narration(raw)
    # 救回了真正的叙事内容
    assert "夜场" in narration and "吃饭前想去个KTV" in narration
    # 绝不把 JSON 结构抖给用户
    assert "node_chips" not in narration
    assert '"narration"' not in narration
    assert not narration.lstrip().startswith("{")


def test_salvage_when_narration_is_last_field():
    """narration 是最后一个字段（无 node_chips）时也能抢救。"""
    raw = '{"title": "标题", "narration": "他说"太远了"，我给拉近了。"}'
    _t, narration, _c = _parse_title_narration(raw)
    assert "太远了" in narration and "拉近" in narration
    assert not narration.lstrip().startswith("{")


def test_salvage_narration_unit():
    """_salvage_narration 单元：抠不出返 None（交给硬兜底）。"""
    assert _salvage_narration('{"foo": "bar"}') is None
    assert _salvage_narration('完全不是 JSON') is None
    got = _salvage_narration('{"narration": "一句话", "node_chips": []}')
    assert got == "一句话"


# ---- ③ 硬兜底：坏 JSON 抠不出 → 空串（绝不 dump 原文），调用方回落模板 ----

def test_broken_json_without_narration_returns_empty_not_raw():
    """坏 JSON 里根本没有可救的 narration，且内容明显是 JSON 对象 →
    返回空串（调用方 `llm_narration or 模板` 自动兜底），不 dump 原始 JSON。"""
    raw = '{"title": "标题", "garbage_field": 一堆非法内容 without quotes'
    _t, narration, _c = _parse_title_narration(raw)
    assert narration == "", f"坏 JSON 应返回空串走模板，不该是原文：{narration!r}"


# ---- 回归：正常 JSON / 纯文本两条路径不受影响 ----

def test_valid_json_still_parses():
    raw = '{"title": "好标题", "narration": "正常的一句叙事", "node_chips": []}'
    title, narration, _c = _parse_title_narration(raw)
    assert title == "好标题"
    assert narration == "正常的一句叙事"


def test_plain_text_still_used_as_narration():
    """模型没走 JSON、直接给一段话（非 { 开头）→ 保留旧行为，用原文当叙事。"""
    raw = "和兄弟们的夜场，玩得尽兴，注意安全。"
    _t, narration, _c = _parse_title_narration(raw)
    assert narration == raw
