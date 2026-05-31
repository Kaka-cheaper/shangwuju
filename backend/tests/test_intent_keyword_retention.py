"""tests.test_intent_keyword_retention —— 意图抽取关键约束保留规则（spec planning-pipeline-consolidation 块B-1 / R3）。

背景（真 LLM 8 场景评测 Bug 3）：
- S2「撸串喝酒」→ 意图丢失「撸串/烧烤」约束，下游给火锅 + 凭空加真人 CS
- S8「一个人独处」→ experience_tags 混入「安静聊天」（一个人无人可聊，自相矛盾）

修复手段是 prompt 调优（概率性改善），行为验证靠 Task 9 真 LLM 实测；
本测试是**确定性 prompt 内容契约测试**（沿用 test_blueprint_prompt.py 的范式）：
断言 INTENT_PARSER_SYSTEM_PROMPT 已包含关键约束保留规则 + 独处反例规则。

【词典事实（schemas/tags.py）】
- DIETARY_TAGS 有「日料」「粤菜」，但**没有**「烧烤」「撸串」「夜宵」「火锅」
- EXPERIENCE_TAGS 有「安静聊天」「独处舒缓」「热闹」「社交」，但没有独立的「安静」
- preferred_poi_types 是自由文本 list[str]，是无词典对应品类的保留位
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


# ---- 过渡态桥（同 test_blueprint_prompt.py）----
def _install_agent_stub() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    agent_dir = backend_root / "agent"

    if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
        stub = types.ModuleType("agent")
        stub.__path__ = [str(agent_dir)]
        sys.modules["agent"] = stub


_install_agent_stub()

from agent.intent.prompts.intent_parser_prompt import (  # noqa: E402
    INTENT_PARSER_SYSTEM_PROMPT,
)


# ---- Test 1：餐饮品类关键词保留规则 ----------------------------


def test_prompt_has_cuisine_keyword_retention_rule() -> None:
    """prompt 必须有「明示餐饮品类须保留」规则，且点名无词典对应的品类（撸串/烧烤/夜宵）。"""
    text = INTENT_PARSER_SYSTEM_PROMPT
    assert "撸串" in text, "prompt 缺少『撸串』关键词保留示例"
    assert "烧烤" in text, "prompt 缺少『烧烤』关键词保留示例"
    # 无词典对应的品类应写入 preferred_poi_types 保留
    assert "preferred_poi_types" in text


def test_prompt_forbids_cuisine_substitution() -> None:
    """prompt 必须禁止把用户品类改写成无关品类（撸串≠火锅）。"""
    text = INTENT_PARSER_SYSTEM_PROMPT
    # 规则段应出现「不要改写/不得替换」类禁令，且与火锅形成对照
    assert "火锅" in text, "prompt 应以火锅作为『不要乱替换品类』的反例锚点"
    assert any(neg in text for neg in ["不要改写", "不得替换", "不要替换", "禁止改写"]), (
        "prompt 缺少『不要把用户品类改写成无关品类』的禁令"
    )


# ---- Test 2：禁止凭空添加活动 --------------------------------


def test_prompt_forbids_fabricating_activities() -> None:
    """prompt 必须禁止凭空添加用户没提的活动/品类（S2 真人 CS bug）。"""
    text = INTENT_PARSER_SYSTEM_PROMPT
    assert any(
        kw in text for kw in ["凭空", "不要添加用户没提", "禁止添加用户未提", "不得添加用户没"]
    ), "prompt 缺少『禁止凭空添加用户未提诉求』的约束"


# ---- Test 3：独处场景反例规则 --------------------------------


def test_prompt_has_solo_context_reverse_rule() -> None:
    """独处放空场景 experience_tags 禁止出现「安静聊天」，应改用「独处舒缓」。"""
    text = INTENT_PARSER_SYSTEM_PROMPT
    # 规则段必须同时点名「独处放空」与「安静聊天」并形成禁令
    assert "独处放空" in text
    assert "安静聊天" in text
    assert "独处舒缓" in text, "prompt 应给出独处场景的替代标签『独处舒缓』"
    # 定位规则段：在所有「独处放空」出现处中，找一处其邻近窗口同时含「安静聊天」+ 禁令词
    found = False
    idx = 0
    while True:
        idx = text.find("独处放空", idx)
        if idx == -1:
            break
        window = text[idx : idx + 160]
        if "安静聊天" in window and any(
            neg in window for neg in ["禁止", "不得", "不要", "不应"]
        ):
            found = True
            break
        idx += len("独处放空")
    assert found, "独处反例规则未与『安静聊天』禁令同段"
