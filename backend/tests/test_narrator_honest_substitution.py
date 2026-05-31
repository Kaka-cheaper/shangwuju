"""tests.test_narrator_honest_substitution —— 诚实告知未满足品类（用户观察的 bug）。

背景：用户明示「撸串/烧烤」但匹配餐厅都超 5km 被距离过滤，最终方案排了火锅。
旧行为：narrator 当正常方案暖语气包装，不告知用户品类没满足（不诚实）。
新行为：检测「用户指定品类 vs 最终行程餐厅 cuisine」未命中 → 诚实告知
       「附近没找到 X，帮你换了 Y」。

本测试验证纯检测函数 detect_unmet_cuisine_preference 的行为（确定性），
narrator 文案的暖语气包装靠 prompt（概率性，不在此断言）。
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

from agent.intent.narrator import detect_unmet_cuisine_preference  # noqa: E402


def test_unmet_when_preferred_cuisine_absent_from_itinerary() -> None:
    """用户要烧烤，行程餐厅全是火锅 → 报「烧烤」未满足。"""
    unmet = detect_unmet_cuisine_preference(
        preferred_poi_types=["烧烤", "啤酒"],
        itinerary_restaurant_cuisines=["火锅"],
    )
    assert "烧烤" in unmet, "烧烤未出现在行程餐厅中，应判未满足"
    # 「啤酒」不是 mock 真实菜系，不应误报为未满足品类
    assert "啤酒" not in unmet


def test_satisfied_when_preferred_cuisine_present() -> None:
    """用户要烧烤，行程里确实有烧烤 → 不报未满足。"""
    unmet = detect_unmet_cuisine_preference(
        preferred_poi_types=["烧烤"],
        itinerary_restaurant_cuisines=["烧烤"],
    )
    assert unmet == [], "烧烤已满足，不应报未满足"


def test_no_preference_returns_empty() -> None:
    """用户没明示品类 → 永不报未满足。"""
    assert detect_unmet_cuisine_preference([], ["火锅"]) == []


def test_substring_match_counts_as_satisfied() -> None:
    """双向 substring：preferred=['串'] 命中 cuisine='串串' → 满足。"""
    unmet = detect_unmet_cuisine_preference(
        preferred_poi_types=["串"],
        itinerary_restaurant_cuisines=["串串"],
    )
    assert unmet == []


def test_non_cuisine_words_filtered_out() -> None:
    """非菜系词（KTV/展览/啤酒）不参与餐厅品类满足判定（避免误报）。"""
    # KTV/展览是 POI 活动类，不是餐厅菜系；行程无对应餐厅也不该报"餐厅未满足"
    unmet = detect_unmet_cuisine_preference(
        preferred_poi_types=["KTV", "展览"],
        itinerary_restaurant_cuisines=["火锅"],
    )
    assert unmet == [], "非菜系词不应触发餐厅品类未满足告知"


# ---- prompt 接线测试 ----------------------------------------

from agent.intent.prompts.narrator_prompt import (  # noqa: E402
    NARRATOR_SYSTEM_PROMPT,
    build_narrator_user_message,
)


def test_system_prompt_has_honest_disclosure_rules() -> None:
    """narrator system prompt 必须含「诚实告知规则」段 + 至少 1 条 few-shot。"""
    assert "诚实告知规则" in NARRATOR_SYSTEM_PROMPT
    assert "未满足的品类诉求" in NARRATOR_SYSTEM_PROMPT
    # 坦白 + 替代 两步必须在 prompt 里
    assert "先坦白" in NARRATOR_SYSTEM_PROMPT
    assert "替代" in NARRATOR_SYSTEM_PROMPT


def test_user_message_embeds_unmet_cuisines() -> None:
    """传 unmet_cuisines 时 user message 含诚实告知触发指令；不传则无。"""
    msg = build_narrator_user_message(
        intent_dict={"companions": [], "social_context": "朋友热闹"},
        itinerary_dict={"summary": "x", "total_minutes": 180, "nodes": [], "orders": []},
        stage_label="stream",
        unmet_cuisines=["烧烤"],
    )
    assert "未满足的品类诉求" in msg
    assert "烧烤" in msg
    assert "诚实告知规则" in msg

    msg_clean = build_narrator_user_message(
        intent_dict={"companions": [], "social_context": "朋友热闹"},
        itinerary_dict={"summary": "x", "total_minutes": 180, "nodes": [], "orders": []},
        stage_label="stream",
    )
    assert "未满足的品类诉求" not in msg_clean


def test_template_narration_includes_honest_disclosure() -> None:
    """模板兜底路径（规则模式/LLM失败）也要诚实告知未满足品类。"""
    from agent.intent.narrator import _template_narration
    from schemas.intent import IntentExtraction
    from schemas.itinerary import ActivityNode, Hop, Itinerary

    intent = IntentExtraction(
        start_time="today_evening",
        duration_hours=[2, 4],
        distance_max_km=5,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=["热闹"],
        social_context="朋友热闹",
        preferred_poi_types=["烧烤"],
        raw_input="撸串",
        parse_confidence=0.8,
    )
    itin = Itinerary(
        schema_version="edge_v1",
        summary="测试",
        nodes=[
            ActivityNode(node_id="n_home_s", kind="出发", target_kind="home", target_id="home", start_time="18:00", duration_min=0, title="家"),
            ActivityNode(node_id="n_1", kind="用餐", target_kind="restaurant", target_id="R034", start_time="18:10", duration_min=90, title="火锅店"),
            ActivityNode(node_id="n_home_e", kind="回家", target_kind="home", target_id="home", start_time="20:00", duration_min=0, title="家"),
        ],
        hops=[
            Hop(hop_id="h_0", from_node_id="n_home_s", to_node_id="n_1", start_time="18:00", minutes=10, mode="taxi", path_type="estimated"),
            Hop(hop_id="h_1", from_node_id="n_1", to_node_id="n_home_e", start_time="19:40", minutes=10, mode="taxi", path_type="estimated"),
        ],
        total_minutes=120,
    )
    text = _template_narration(intent, itin, "stream", None, ["烧烤"])
    assert "烧烤" in text and "没找到" in text, f"模板未诚实告知未满足品类：{text}"

