"""tests/test_thought_humanizer.py —— 验证 humanize_thought 黑/灰/白名单规则。

测试目的：
- 黑名单（调试型 thought）→ 返 None（前端隐藏）
- 灰名单（关键词驱动）→ 返用户向重写文案
- 白名单（已是人话）→ 透传
- 空 / None → 返 None
- attach_user_text 不破坏调用方传的 user_text
"""

from __future__ import annotations

import pytest

from agent.thought_humanizer import attach_user_text, humanize_thought


# ============================================================
# 黑名单（仅供后端 trace，前端隐藏）
# ============================================================

@pytest.mark.parametrize(
    "text",
    [
        # 权重
        "出 plan 第 1 次（权重 舒适 0.45 / 时间 0.25 / 成本 0.10 / 连贯 0.20）",
        # 蓝图原文（第三人称 + ID 泄露）
        "蓝图 5 段：用户计划下午出行 3-5 小时，包含妻子（减肥）和 5 岁孩子。选择主活动为 P017",
        "LLM 蓝图（第 1 次）：5 段，总时长 250 分钟",
        "LLM 蓝图生成第 2 次失败：rate_limit",
        "蓝图 Critic：passed=False，违规 2 条（硬 1）",
        # fallback 路径
        "LLM 客户端不可用（缺 API Key），已切回规则规划",
        "LLM 客户端为 stub 模式，无主观决策能力，已切回规则规划",
        "切换重排策略：ils_fallback",
        "ILS 算法兜底重排中……",
        "ILS 迭代 #3：发现更优解",
        # critic 违规原文（已通过 replan_triggered 单独事件展示）
        "[CRITICAL] CapacityCritic: 餐厅 R001 17:00 已满",
        "[HARD] DurationCritic: 总时长超限",
        # 段决策诊断
        "段决策：实际跨度 132 分钟，候选段集合 = ['出发', '主活动']",
    ],
)
def test_blacklist_returns_none(text: str) -> None:
    assert humanize_thought(text) is None


# ============================================================
# 灰名单（关键词驱动重写）
# ============================================================

def test_understanding_passthrough() -> None:
    out = humanize_thought("正在理解你的需求……")
    assert out == "正在理解你的需求……"


def test_planning_start_hidden() -> None:
    """「好的，让我帮你规划一下」是冗余废话 → 隐藏。"""
    assert humanize_thought("好的，让我帮你规划一下。") is None
    assert humanize_thought("好的, 让我帮你规划一下。") is None


def test_candidates_ready_extracts_count() -> None:
    """候选准备就绪：从原文抽取数量给用户看。"""
    out = humanize_thought("候选准备就绪：POI 6 个 / 餐厅 12 个，交给 LLM 出蓝图")
    assert out is not None
    assert "6 个" in out and "12" in out


def test_blueprint_assembled_friendly() -> None:
    out = humanize_thought("蓝图通过 critic，已组装 itinerary（5 段，250 分钟）")
    assert out == "方案搭好了，正在做最后检查……"


def test_critic_passed_friendly() -> None:
    out = humanize_thought("方案验证通过（0 条提示）。")
    assert out == "检查通过，方案准备好了"


def test_refine_start_extracts_feedback() -> None:
    """收到反馈：抽出引号内容给用户看。"""
    out = humanize_thought("开始根据你的反馈调整：「太远了，希望 3 公里以内」")
    assert out is not None
    assert "太远了" in out


# ============================================================
# 白名单（透传）
# ============================================================

def test_natural_language_passthrough() -> None:
    text = "你今天看起来有点累，要不要找个轻松一点的安排？"
    assert humanize_thought(text) == text


def test_long_text_truncated() -> None:
    text = "啊" * 300
    out = humanize_thought(text)
    assert out is not None
    assert out.endswith("……")
    assert len(out) <= 200


# ============================================================
# 边界
# ============================================================

def test_empty_returns_none() -> None:
    assert humanize_thought("") is None
    assert humanize_thought("   ") is None


# ============================================================
# attach_user_text 行为
# ============================================================

def test_attach_injects_user_text() -> None:
    payload = {"text": "正在理解你的需求……"}
    out = attach_user_text(payload)
    assert out["user_text"] == "正在理解你的需求……"
    assert out["text"] == "正在理解你的需求……"  # 原文保留


def test_attach_blacklist_sets_none() -> None:
    payload = {"text": "出 plan 第 1 次（权重 舒适 0.45）"}
    out = attach_user_text(payload)
    assert out["user_text"] is None
    assert "权重" in out["text"]  # 原文保留


def test_attach_respects_existing_user_text() -> None:
    """调用方显式传 user_text 时不覆盖。"""
    payload = {"text": "出 plan 第 1 次", "user_text": "自定义文案"}
    out = attach_user_text(payload)
    assert out["user_text"] == "自定义文案"


def test_attach_skip_payload_without_text() -> None:
    payload = {"reason": "x"}
    out = attach_user_text(payload)
    assert "user_text" not in out
