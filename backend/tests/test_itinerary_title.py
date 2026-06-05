"""tests.test_itinerary_title —— 行程卡片小红书风格大标题（itinerary.summary）。

背景（用户观察的 bug）：旧标题「半日方案 · 单个POI（约X小时）」只取停留最久的单站，
漏掉其它站（如烧烤）。改造后 title 必须**信息全 = 含所有主要站点**，且口语化、
无「半日方案·」前缀、无「（约X小时）」括号。

三层兜底全部断言「信息全」：
- title_builder（纯函数）
- _template_title / generate_title_and_narration（规则兜底）
- assemble_from_blueprint._build_summary（最底层 summary）
- LLM JSON 同次产 title + 解析失败兜底
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


def _install_agent_stub() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    agent_dir = backend_root / "agent"
    if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
        stub = types.ModuleType("agent")
        stub.__path__ = [str(agent_dir)]
        sys.modules["agent"] = stub


_install_agent_stub()

from agent.intent import narrator as narrator_mod  # noqa: E402
from agent.intent.narrator import (  # noqa: E402
    generate_title_and_narration,
    build_template_title,
)
from agent.intent.title_builder import (  # noqa: E402
    build_xiaohongshu_title,
    companions_to_title_phrase,
    node_to_title_phrase,
)
from agent.planning.blueprint.assemble_blueprint import assemble_from_blueprint  # noqa: E402
from agent.planning.blueprint.blueprint import (  # noqa: E402
    BlueprintNode,
    BlueprintTargetKind,
    PlanBlueprint,
)
from data.loader import load_user_profile  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402
from schemas.itinerary import ActivityNode, Hop, Itinerary  # noqa: E402


# ============================================================
# Fixtures
# ============================================================


def _intent(*, companions=None, social="朋友热闹") -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[4, 5],
        distance_max_km=8,
        companions=companions or [Companion(role="室友", count=4)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context=social,
        raw_input="和室友撸串唱K",
        parse_confidence=0.9,
    )


def _bbq_ktv_itinerary() -> Itinerary:
    """烧烤(用餐) + KTV(活动) 两站行程（信息全断言用：两类站点都要出现在标题）。"""
    nodes = [
        ActivityNode(node_id="n0", kind="起点", target_kind="home", target_id="home", start_time="17:30", duration_min=0, title="家"),
        ActivityNode(node_id="n1", kind="用餐", target_kind="restaurant", target_id="R031", start_time="17:57", duration_min=120, title="炭烤大叔 · 路边烧烤"),
        ActivityNode(node_id="n2", kind="主活动", target_kind="poi", target_id="P026", start_time="20:28", duration_min=120, title="麦霸欢唱 KTV · 旗舰店"),
        ActivityNode(node_id="n3", kind="终点", target_kind="home", target_id="home", start_time="22:36", duration_min=0, title="家"),
    ]
    hops = [
        Hop(hop_id=f"h{i}", from_node_id=f"n{i}", to_node_id=f"n{i + 1}", start_time="17:30", minutes=10, mode="taxi", path_type="estimated")
        for i in range(3)
    ]
    return Itinerary(summary="占位 summary", nodes=nodes, hops=hops, total_minutes=270)


# ============================================================
# 1) title_builder 纯函数
# ============================================================


def test_node_to_title_phrase_keyword_uses_full_title() -> None:
    """KTV 关键词在「· 旗舰店」前缀里——必须用完整 title 匹配，不能被 split 丢掉。"""
    assert node_to_title_phrase(title="麦霸欢唱 KTV · 旗舰店", kind="主活动", target_kind="poi") == "唱K"
    assert node_to_title_phrase(title="炭烤大叔 · 路边烧烤", kind="用餐", target_kind="restaurant") == "撸串"
    # home 跳过
    assert node_to_title_phrase(title="家", kind="起点", target_kind="home") is None


def test_build_title_covers_all_stations() -> None:
    """标题必须串联所有站点（+ 连接），含同行 + 时长，无方案前缀 / 无括号。"""
    title = build_xiaohongshu_title(
        station_phrases=["撸串", "唱K"], companions_phrase="和室友", total_hours=4.5
    )
    assert "撸串" in title and "唱K" in title  # 信息全：两站都在
    assert "+" in title
    assert "室友" in title
    assert "4.5" in title
    assert "半日方案" not in title and "（约" not in title and "(约" not in title


def test_build_title_dedup_and_solo() -> None:
    """重复站点去重；一个人 → 独自。"""
    assert build_xiaohongshu_title(station_phrases=["撸串", "撸串"], companions_phrase="一个人", total_hours=3) == "独自撸串，3小时"


def test_companions_to_title_phrase_concise() -> None:
    """标题同行短语简洁（不带「位/个」），家庭组合连写。"""
    assert companions_to_title_phrase([{"role": "室友", "count": 4}]) == "和室友"
    assert companions_to_title_phrase([{"role": "妻子", "count": 1}, {"role": "孩子", "age": 5, "count": 1}]) == "和老婆孩子"
    assert companions_to_title_phrase([]) == ""


# ============================================================
# 2) 规则兜底（_template_title / generate_title_and_narration use_llm=False）
# ============================================================


def test_template_title_includes_both_stations() -> None:
    """规则兜底标题：烧烤 + KTV 两站都出现（核心 bug：旧实现只出单站）。"""
    title = build_template_title(_intent(), _bbq_ktv_itinerary())
    assert "撸串" in title, f"漏掉烧烤站：{title}"
    assert "唱K" in title, f"漏掉 KTV 站：{title}"
    assert "半日方案" not in title


def test_generate_title_and_narration_rule_mode() -> None:
    """use_llm=False：title 走规则兜底（信息全），narration 仍是暖语气开场白。"""
    title, narration = generate_title_and_narration(
        intent=_intent(), itinerary=_bbq_ktv_itinerary(), use_llm=False
    )
    assert "撸串" in title and "唱K" in title
    assert narration  # 非空
    assert "半日方案" not in title


# ============================================================
# 3) LLM 同次产 title + 解析兜底
# ============================================================


def test_llm_json_produces_title_and_narration(monkeypatch) -> None:
    """LLM 返回 JSON {title, narration} → 同一次调用拿到两者。"""

    class Resp:
        def __init__(self, c):
            self.content = c

    class JsonClient:
        provider = "deepseek"

        def chat(self, *, messages, temperature, **kw):
            return Resp(
                '{"title": "室友夜局｜撸串配K歌", '
                '"narration": "和室友的夜局——先撸串再唱K。哪里不合适跟我说一声。"}'
            )

    monkeypatch.setattr(narrator_mod, "get_llm_client", lambda *a, **k: JsonClient())
    title, narration = generate_title_and_narration(
        intent=_intent(), itinerary=_bbq_ktv_itinerary(), use_llm=True
    )
    assert title == "室友夜局｜撸串配K歌"
    assert "撸串" in narration and "唱K" in narration


def test_llm_plaintext_falls_back_title_keeps_narration(monkeypatch) -> None:
    """LLM 返回纯文本（非 JSON）→ title 兜底规则版（信息全），narration 用整段原文。"""

    class Resp:
        def __init__(self, c):
            self.content = c

    class PlainClient:
        provider = "deepseek"

        def chat(self, *, messages, temperature, **kw):
            return Resp("和室友下午撸串唱K，玩得很开心。哪里不合适跟我说一声。")

    monkeypatch.setattr(narrator_mod, "get_llm_client", lambda *a, **k: PlainClient())
    title, narration = generate_title_and_narration(
        intent=_intent(), itinerary=_bbq_ktv_itinerary(), use_llm=True
    )
    # 解析不出 title → 规则兜底，仍信息全
    assert "撸串" in title and "唱K" in title
    # narration 用 LLM 原文
    assert narration == "和室友下午撸串唱K，玩得很开心。哪里不合适跟我说一声。"


def test_llm_dirty_title_sanitized(monkeypatch) -> None:
    """LLM 给的 title 含禁用「半日方案·」前缀 +「（约X小时）」→ 必须被清理。"""

    class Resp:
        def __init__(self, c):
            self.content = c

    class DirtyClient:
        provider = "deepseek"

        def chat(self, *, messages, temperature, **kw):
            return Resp(
                '{"title": "半日方案 · 室友撸串+唱K（约 5 小时）", '
                '"narration": "narration 内容。哪里不合适跟我说一声。"}'
            )

    monkeypatch.setattr(narrator_mod, "get_llm_client", lambda *a, **k: DirtyClient())
    title, _ = generate_title_and_narration(
        intent=_intent(), itinerary=_bbq_ktv_itinerary(), use_llm=True
    )
    assert "半日方案" not in title
    assert "约 5 小时" not in title and "（约" not in title
    assert "撸串" in title and "唱K" in title


# ============================================================
# 4) 最底层 summary（assemble_from_blueprint）
# ============================================================


def test_assemble_summary_covers_all_stations() -> None:
    """最底层 summary 必须串联所有主要站点（烧烤 + KTV），无方案前缀 / 无括号。"""
    up = load_user_profile()
    bp = PlanBlueprint(
        nodes=[
            BlueprintNode(kind="用餐", target_kind=BlueprintTargetKind.RESTAURANT, target_id="R031", duration_min=120, note="4 人"),
            BlueprintNode(kind="主活动", target_kind=BlueprintTargetKind.POI, target_id="P026", duration_min=120, note=None),
        ],
        preferred_start_time="17:30",
        rationale="test",
    )
    itin = assemble_from_blueprint(_intent(), bp, up)
    assert "撸串" in itin.summary, f"最底层 summary 漏烧烤：{itin.summary}"
    assert "唱K" in itin.summary, f"最底层 summary 漏 KTV：{itin.summary}"
    assert "半日方案" not in itin.summary
    assert "（约" not in itin.summary and "(约" not in itin.summary
