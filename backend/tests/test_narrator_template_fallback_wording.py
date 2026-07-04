"""tests.test_narrator_template_fallback_wording —— 叙事兜底模板的命名与空格
（真 LLM 点火 B9/G3/G4/H1 四次实锤，文案修缮批必修 2）。

实锤原文（叙事 LLM 失败时的规则兜底）：

    和2 位兄弟的 3.9 小时——18:00 从家出发，18:08 去私房包房，
    20:16 去旗舰店，21:54 打车回家。都给你搞定了，可以放心出门了。

两处瑕疵：
1. "和2 位"——`_format_companions` 拼 "和"+"2 位兄弟" 时空格插错位
   （该函数 docstring 自己写的期望就是「和 4 个朋友」，代码没对齐）。
2. "去私房包房"/"去旗舰店"——`_node_to_phrase` 把店名按「 · 」劈开只取后半
   截，但 mock 目录里「 · 」是**全名的一部分**（"麦霸欢唱 KTV · 旗舰店"
   "8 号台球俱乐部 · 私房包房"，102 个实体里 67 个如此），截半读着像断句。

修法（终审拍板）：模板拼接取全名 + 空格修正，纯字符串级改动。演示日网络抖、
叙事 LLM 一挂，台上看到的就是这个模板——兜底文案必须体面。
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

from agent.intent.narrator import (  # noqa: E402
    _format_companions,
    _node_to_phrase,
    _template_narration,
)
from schemas.intent import IntentExtraction  # noqa: E402
from schemas.itinerary import ActivityNode, Hop, Itinerary  # noqa: E402


# ============================================================
# 1. 同行人短语空格：「和 2 位兄弟」不是「和2 位兄弟」
# ============================================================


def test_companions_phrase_space_between_he_and_count() -> None:
    assert _format_companions([{"role": "兄弟", "count": 2}]) == "和 2 位兄弟"


def test_companions_phrase_space_for_friends_count() -> None:
    assert _format_companions([{"role": "朋友", "count": 4}]) == "和 4 个朋友"


def test_companions_phrase_without_count_prefix_unchanged() -> None:
    """不以数字开头的短语不受影响（"和老婆"不该变成"和 老婆"）。"""
    assert _format_companions([{"role": "妻子", "count": 1}]) == "和老婆"


# ============================================================
# 2. 节点短语取全名：「 · 」是店名的一部分，不是可截断的分隔符
# ============================================================


def test_poi_phrase_keeps_full_interpunct_name() -> None:
    node = {
        "target_kind": "poi",
        "kind": "主活动",
        "title": "8 号台球俱乐部 · 私房包房",
        "start_time": "18:08",
        "note": "",
    }
    phrase = _node_to_phrase(node, 1, 4)
    assert "8 号台球俱乐部 · 私房包房" in phrase, f"应取全名：{phrase}"
    assert phrase != "18:08 去私房包房", "不应只剩「·」后半截"


def test_restaurant_phrase_keeps_full_interpunct_name() -> None:
    node = {
        "target_kind": "restaurant",
        "kind": "用餐",
        "title": "聚味居 · 朋友聚餐热闹堂",
        "start_time": "18:30",
        "note": "",
    }
    phrase = _node_to_phrase(node, 1, 4)
    assert "聚味居 · 朋友聚餐热闹堂" in phrase, f"应取全名：{phrase}"


# ============================================================
# 3. B9 实锤端到端回归：兜底模板整句体面
# ============================================================


def _b9_like_itinerary() -> Itinerary:
    nodes = [
        ActivityNode(node_id="n0", kind="出发", target_kind="home", target_id="home", start_time="18:00", duration_min=0, title="家"),
        ActivityNode(node_id="n1", kind="主活动", target_kind="poi", target_id="P1", start_time="18:08", duration_min=120, title="8 号台球俱乐部 · 私房包房"),
        ActivityNode(node_id="n2", kind="主活动", target_kind="poi", target_id="P2", start_time="20:16", duration_min=90, title="麦霸欢唱 KTV · 旗舰店"),
        ActivityNode(node_id="n3", kind="回家", target_kind="home", target_id="home", start_time="21:54", duration_min=0, title="家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="18:00", minutes=8, mode="taxi", path_type="estimated"),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="20:08", minutes=8, mode="taxi", path_type="estimated"),
        Hop(hop_id="h2", from_node_id="n2", to_node_id="n3", start_time="21:46", minutes=8, mode="taxi", path_type="estimated"),
    ]
    return Itinerary(schema_version="edge_v1", summary="兄弟局", nodes=nodes, hops=hops, total_minutes=234)


def test_template_fallback_b9_regression() -> None:
    intent = IntentExtraction(
        start_time="today_evening",
        duration_hours=[3, 4],
        distance_max_km=5.0,
        companions=[{"role": "兄弟", "count": 2}],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="朋友热闹",
        raw_input="晚上和两个兄弟出去玩",
        parse_confidence=0.9,
    )
    text = _template_narration(intent, _b9_like_itinerary(), "confirm")
    assert "和 2 位兄弟" in text, f"空格应修正：{text}"
    assert "和2 位" not in text, f"空格错位应消失：{text}"
    assert "8 号台球俱乐部 · 私房包房" in text, f"店名应取全名：{text}"
    assert "麦霸欢唱 KTV · 旗舰店" in text, f"店名应取全名：{text}"
    assert "去旗舰店" not in text and "去私房包房" not in text, f"截半店名应消失：{text}"


# ============================================================
# 4. 兜底质疑短句同样取全名（同一函数族、同一 bug）
# ============================================================


def test_challenge_clause_keeps_full_interpunct_name() -> None:
    intent = IntentExtraction(
        start_time="today_afternoon",
        duration_hours=[3, 4],
        distance_max_km=5.0,
        companions=[{"role": "孩子", "age": 5, "count": 1}],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="带娃出去玩",
        parse_confidence=0.9,
    )
    nodes = [
        ActivityNode(node_id="n0", kind="出发", target_kind="home", target_id="home", start_time="14:00", duration_min=0, title="家"),
        ActivityNode(node_id="n1", kind="主活动", target_kind="poi", target_id="P1", start_time="14:10", duration_min=120, title="无障碍亲子博物馆 · 三代同堂友好馆"),
        ActivityNode(node_id="n2", kind="回家", target_kind="home", target_id="home", start_time="16:20", duration_min=0, title="家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="14:00", minutes=10, mode="taxi", path_type="estimated"),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="16:10", minutes=10, mode="taxi", path_type="estimated"),
    ]
    itin = Itinerary(schema_version="edge_v1", summary="亲子", nodes=nodes, hops=hops, total_minutes=140)
    text = _template_narration(intent, itin, "stream")
    assert "提醒一下" in text, f"应触发兜底质疑：{text}"
    assert "无障碍亲子博物馆 · 三代同堂友好馆" in text, f"质疑短句应取全名：{text}"
