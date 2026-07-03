"""壳2 canonical 字面短路回归测试（ADR-0011 决策 2 / E-1）。

原文件名同（原测的是已删除的规划信号表 fast path——四张词表 + 通用启发式
`_looks_like_new_planning`，ADR-0011 背景 3 判定"表面形式无穷、误吞面大"予以
整体退役）。本文件位置保留，内容整体换成新机制的回归测试：

- canonical 文本命中（PRIMARY_CTAS / /scenarios 8 场景 input）→ 直接 planning，
  且**不调 classify_input**（证明是壳2 字面短路，不是走到 LLM 分类才判出来的）；
- 近似但不完全相同的文本 → 不命中壳2，照常交给 LLM 分类（Layer 2）判定；
- 有方案时命中 canonical 文本仍直接 planning（归并已删，见 ADR-0011 决策 2：
  会话中期命中 canonical 等价于"重新规划一个"，不该被强行掰成 feedback）；
- 地板澄清 chips（fallback_decision 有方案分支发的三个 chip）命中 → 各自对应路由。
"""

from __future__ import annotations

import pytest

from agent.graph.nodes import router as router_mod
from agent.graph.state import make_initial_state
from agent.intent.prompts.router_prompt import PRIMARY_CTAS
from agent.routing.canonical_shortcut import DEMO_SCENARIOS


def _classify_should_not_run(*args, **kwargs):
    raise AssertionError("壳2 canonical 短路命中不应再调脑子")


# ============================================================
# ① PRIMARY_CTAS send 文本命中 → planning，不调 LLM
# ============================================================


@pytest.mark.parametrize("send", [c["send"] for c in PRIMARY_CTAS])
def test_primary_cta_literal_routes_to_planning_without_llm(monkeypatch, send):
    monkeypatch.setattr(router_mod, "classify_turn", _classify_should_not_run)

    out = router_mod.router_node(
        make_initial_state(user_input=send, session_id="canonical-cta")
    )

    assert out["route_kind"] == "planning"
    assert out["router_decision"].input_kind.value == "planning"


# ============================================================
# ③ /scenarios 8 个演示场景 input 文本命中 → planning，不调 LLM
# （断网/stub 演示下"任意输入→引导气泡→点场景 chip→正常规划"的规划可达通道）
# ============================================================


@pytest.mark.parametrize("scenario", DEMO_SCENARIOS, ids=lambda s: s["id"])
def test_demo_scenario_literal_routes_to_planning_without_llm(monkeypatch, scenario):
    monkeypatch.setattr(router_mod, "classify_turn", _classify_should_not_run)

    out = router_mod.router_node(
        make_initial_state(user_input=scenario["input"], session_id=f"canonical-{scenario['id']}")
    )

    assert out["route_kind"] == "planning"


@pytest.mark.parametrize("scenario_idx", [0, 1])
def test_demo_scenario_literal_routes_to_planning_even_with_itinerary(monkeypatch, scenario_idx):
    """会话中期命中 canonical 文本仍直接 planning——两重性质各用一个场景钉死:

    - S2(idx=1,"人均 50 左右就行"):不含任何强信号词,证「归并已删,
      canonical 中期可达」;
    - S1(idx=0,"预算别太贵"含保留词「贵」):证**壳2 先于 Layer 1**(深审修正)
      ——系统发出的精确全串确定性高于启发式强信号;修正前这条会被 Layer 1
      吞成 feedback,演示后果=有方案时点场景卡不开新局反而去改旧方案。
    """
    monkeypatch.setattr(router_mod, "classify_turn", _classify_should_not_run)

    st = make_initial_state(
        user_input=DEMO_SCENARIOS[scenario_idx]["input"],
        session_id=f"canonical-mid-session-{scenario_idx}",
    )
    st["itinerary"] = {"summary": "上一轮方案"}
    out = router_mod.router_node(st)

    assert out["route_kind"] == "planning", (
        "命中 canonical 文本不该被吞成 feedback(归并已删,且壳2 先于 Layer 1)"
    )


# ============================================================
# 近似但不精确匹配 → 不命中壳2，照常交给 Layer 2 LLM 分类
# ============================================================


@pytest.mark.parametrize(
    "near_miss",
    [
        # 场景 S3 input 掐头去尾/改一字，都不是精确字面匹配
        "今天下午想和老婆孩子出去玩几个小时，别离家太远，孩子 5 岁，老婆最近在减肥",  # 缺尾部句号
        PRIMARY_CTAS[0]["send"] + "！",  # 多一个字符
        "帮我规划一下下午",  # 普通规划意图，但不是任何 canonical 文本
    ],
)
def test_near_miss_text_does_not_shortcut(monkeypatch, near_miss):
    """壳2 是 FP≈0 精确字面匹配，近似文本一律不命中，落回脑子判定。"""
    from agent.routing.brain import RouteJudgment

    def _classify_chitchat(*args, **kwargs):
        return RouteJudgment(
            label="chitchat",
            confidence=0.9,
            reply_text="没听太懂，要不要试试下面的场景？",
            tone="warm",
            cta_chips=[],
            rationale="test-near-miss",
        )

    monkeypatch.setattr(router_mod, "classify_turn", _classify_chitchat)

    out = router_mod.router_node(
        make_initial_state(user_input=near_miss, session_id="near-miss")
    )

    assert out["route_kind"] == "chitchat", (
        f"{near_miss!r} 不是精确 canonical 文本，应交给脑子判定，不应被壳2短路"
    )


# ============================================================
# ② 地板澄清 chips（fallback_decision 有方案分支）命中 → 各自对应路由
# ============================================================


def test_floor_clarify_adjust_routes_to_feedback():
    """「调整一下方案」命中 → feedback（不调 LLM，has_itinerary 时才短路）。"""
    st = make_initial_state(user_input="调整一下方案", session_id="floor-adjust")
    st["itinerary"] = {"summary": "上一轮方案"}
    out = router_mod.router_node(st)
    assert out["route_kind"] == "feedback"


def test_floor_clarify_replan_routes_to_planning():
    """「重新规划一个」命中 → planning（不调 LLM）。"""
    st = make_initial_state(user_input="重新规划一个", session_id="floor-replan")
    st["itinerary"] = {"summary": "上一轮方案"}
    out = router_mod.router_node(st)
    assert out["route_kind"] == "planning"


def test_floor_clarify_keep_routes_to_confirm():
    """「就这样挺好」命中 → confirm（ADR-0011 决策 1：确认独立出口，纯认可，
    不改方案、不重规划；原断言"chitchat"随 7→6 塌缩改名）。"""
    st = make_initial_state(user_input="就这样挺好", session_id="floor-keep")
    st["itinerary"] = {"summary": "上一轮方案"}
    out = router_mod.router_node(st)
    assert out["route_kind"] == "confirm"


def test_floor_clarify_chips_do_not_shortcut_without_itinerary(monkeypatch):
    """无方案时这三句字面出现属巧合，不强行短路（防御性校验，见 canonical_shortcut 设计取舍）。"""
    from agent.routing.brain import RouteJudgment

    def _classify_clarify(*args, **kwargs):
        return RouteJudgment(
            label="clarify",
            confidence=0.7,
            reply_text="?",
            tone="warm",
            cta_chips=[],
            rationale="test",
        )

    monkeypatch.setattr(router_mod, "classify_turn", _classify_clarify)
    st = make_initial_state(user_input="调整一下方案", session_id="floor-adjust-no-itin")
    out = router_mod.router_node(st)
    assert out["route_kind"] == "clarify", "无方案时不应被壳2的地板 chip 短路"
