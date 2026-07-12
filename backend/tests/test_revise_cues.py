"""test_revise_cues —— 「明说要改方案」祈使词判定（对话轮路由规则层重构，2026-07-12）。

`looks_like_explicit_revise` 原定义 + 测试都在 `soft_constraint_sniffer.py` /
`test_soft_constraint_sniffer.py`——软约束嗅探器的路由角色删除后，这个判据
被抽到中立模块 `agent.core.revise_cues`（供 `dialogue_acts.py` 与
`itinerary_qa.py` 平权引用，不再让其中一个模块名义上"拥有"另一个的依赖），
测试随之搬到本文件。判据本身逻辑未变。
"""

from __future__ import annotations

from agent.core.revise_cues import looks_like_explicit_revise


def test_explicit_revise_detects_imperative():
    assert looks_like_explicit_revise("帮我换成适合老人的")
    assert looks_like_explicit_revise("这版去掉椰林餐厅")
    assert not looks_like_explicit_revise("我妈膝盖不好，走不远")
    assert not looks_like_explicit_revise("你好呀")


def test_explicit_revise_empty_input():
    assert not looks_like_explicit_revise("")
    assert not looks_like_explicit_revise(None)  # type: ignore[arg-type]
