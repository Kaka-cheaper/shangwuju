"""tests.test_narrator_unmet_reason_fork —— 未满足品类的原因分叉（文案修缮批
建议修 2，真 LLM 点火 C2 实锤）。

实锤（C2 二轮，用户反馈"有点累了"后）：

    你想要唱K，但附近没找到合适的KTV，先帮你安排了鼎鼎鸳鸯火锅顶上……
    上次你说有点累了，这版就只安排了吃……

同一条叙事对"KTV 没了"给了两个解释：第一个（"附近没找到"）是**误导**——
第一轮方案里就排着"麦霸欢唱 KTV"，附近明明有；KTV 是被"累了"催生的新约束
（低强度/可休息）在重排里滤掉的。第二个（recap）才是真相。

信号在现场（调查结论）：narrate 构建未满足列表时，全量目录 + 实体
distance_km + intent.distance_max_km 都拿得到——"附近到底有没有这类去处"
可以确定性判定，不需要跨模块新链路。修法：

- `split_unmet_by_nearby_availability`（本文件断言）：把未满足诉求分成
  「附近确实没有」（可以说"附近没找到"）与「附近有但这版没安排」（只能说
  "这次没安排上"，不许把方案取舍说成找不到）两组。
- 模板路径 / prompt 各自按组分叉措辞（本文件断言模板与 prompt 接线）。

分类失败的兜底方向：归入"这版没安排"组——"这次没安排上 X"无论哪种原因都为
真；"附近没找到 X"只在验证过缺货时才为真。宁可少断言，不可说假话。
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
    _template_narration,
    split_unmet_by_nearby_availability,
)
from agent.intent.prompts.narrator_prompt import (  # noqa: E402
    NARRATOR_SYSTEM_PROMPT,
    build_narrator_user_message,
)
from schemas.domain import Location, Poi, PoiCapacity, Restaurant, RestaurantCapacity  # noqa: E402
from schemas.intent import IntentExtraction  # noqa: E402
from schemas.itinerary import ActivityNode, Hop, Itinerary  # noqa: E402


def _poi(name: str, poi_type: str, dist: float) -> Poi:
    return Poi(
        id=f"P_{name[:4]}",
        name=name,
        type=poi_type,
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=dist,
        opening_hours="10:00-23:00",
        rating=4.5,
        age_range=None,
        price_range=None,
        tags=[],
        suitable_for=[],
        suggested_duration_minutes=90,
        capacity=PoiCapacity(daily_quota=100, available_slots=50),
    )


def _rest(name: str, cuisine: str, dist: float) -> Restaurant:
    return Restaurant(
        id=f"R_{name[:4]}",
        name=name,
        cuisine=cuisine,
        location=Location(name="测试地", lat=None, lng=None),
        distance_km=dist,
        opening_hours="11:00-23:00",
        avg_price=100.0,
        rating=4.3,
        typical_dining_min=60,
        capacity=RestaurantCapacity(),
        tags=[],
        suitable_for=[],
    )


def _intent(**overrides) -> IntentExtraction:
    base = dict(
        start_time="today_evening",
        duration_hours=[2, 3],
        distance_max_km=5.0,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="朋友热闹",
        raw_input="想去 K 歌",
        parse_confidence=0.9,
    )
    base.update(overrides)
    return IntentExtraction(**base)


def _make_itinerary() -> Itinerary:
    nodes = [
        ActivityNode(node_id="n0", kind="出发", target_kind="home", target_id="home", start_time="18:00", duration_min=0, title="家"),
        ActivityNode(node_id="n1", kind="用餐", target_kind="restaurant", target_id="R1", start_time="18:30", duration_min=90, title="鼎鼎鸳鸯火锅"),
        ActivityNode(node_id="n2", kind="回家", target_kind="home", target_id="home", start_time="20:10", duration_min=0, title="家"),
    ]
    hops = [
        Hop(hop_id="h0", from_node_id="n0", to_node_id="n1", start_time="18:00", minutes=10, mode="taxi", path_type="estimated"),
        Hop(hop_id="h1", from_node_id="n1", to_node_id="n2", start_time="20:00", minutes=10, mode="taxi", path_type="estimated"),
    ]
    return Itinerary(schema_version="edge_v1", summary="吃饭", nodes=nodes, hops=hops, total_minutes=140)


# ============================================================
# 1. 分类器：附近有 → "这版没安排"组；附近确实没有/超距 → "附近没找到"组
# ============================================================


def test_ktv_nearby_classified_as_not_scheduled() -> None:
    """C2 同款：附近就有 KTV（第一轮还排进过方案）→ 不许说"附近没找到"。"""
    pois = [_poi("麦霸欢唱 KTV · 旗舰店", "KTV", dist=3.0)]
    not_found, not_scheduled = split_unmet_by_nearby_availability(
        ["KTV"], _intent(), pois, []
    )
    assert not_scheduled == ["KTV"]
    assert not_found == []


def test_missing_cuisine_classified_as_not_found() -> None:
    """本地压根没有该品类（韩式烤肉）→ "附近没找到"成立。"""
    restaurants = [_rest("鼎鼎鸳鸯火锅", "火锅", dist=2.0)]
    not_found, not_scheduled = split_unmet_by_nearby_availability(
        ["韩式烤肉"], _intent(), [], restaurants
    )
    assert not_found == ["韩式烤肉"]
    assert not_scheduled == []


def test_beyond_distance_classified_as_not_found() -> None:
    """该品类存在但全部超出用户距离半径 → "附近没找到"仍然诚实成立。"""
    pois = [_poi("麦霸欢唱 KTV · 旗舰店", "KTV", dist=12.0)]
    not_found, not_scheduled = split_unmet_by_nearby_availability(
        ["KTV"], _intent(distance_max_km=5.0), pois, []
    )
    assert not_found == ["KTV"]
    assert not_scheduled == []


def test_nearby_cuisine_classified_as_not_scheduled() -> None:
    """餐饮品类同规则：附近有烧烤但没排进 → "这版没安排"组。"""
    restaurants = [_rest("夜烤场·精致烧烤", "烧烤", dist=3.0)]
    not_found, not_scheduled = split_unmet_by_nearby_availability(
        ["烧烤"], _intent(), [], restaurants
    )
    assert not_scheduled == ["烧烤"]
    assert not_found == []


# ============================================================
# 2. 模板路径分叉措辞
# ============================================================


def test_template_not_scheduled_never_claims_not_found() -> None:
    intent = _intent(preferred_poi_types=["KTV"])
    text = _template_narration(
        intent, _make_itinerary(), "stream", unmet_not_scheduled=["KTV"]
    )
    assert "KTV" in text and "没安排上" in text, f"应坦白这次没安排上：{text}"
    assert "没找到" not in text, f"附近明明有，不许说没找到：{text}"


def test_template_both_groups_render_both_clauses() -> None:
    intent = _intent(preferred_poi_types=["韩式烤肉", "KTV"])
    text = _template_narration(
        intent,
        _make_itinerary(),
        "stream",
        None,
        ["韩式烤肉"],
        unmet_not_scheduled=["KTV"],
    )
    assert "韩式烤肉" in text and "没找到" in text
    assert "KTV" in text and "没安排上" in text


# ============================================================
# 3. LLM 路径 prompt 接线
# ============================================================


def test_user_message_forks_not_scheduled_block() -> None:
    msg = build_narrator_user_message(
        intent_dict={"companions": [], "social_context": "朋友热闹"},
        itinerary_dict={"summary": "x", "total_minutes": 180, "nodes": [], "orders": []},
        stage_label="stream",
        unmet_not_scheduled=["KTV"],
    )
    assert "这版没安排" in msg and "KTV" in msg
    assert "附近没有匹配的餐厅" not in msg, "不该给 LLM 喂『附近没有』的假原因"


def test_user_message_not_found_block_unchanged() -> None:
    msg = build_narrator_user_message(
        intent_dict={"companions": [], "social_context": "朋友热闹"},
        itinerary_dict={"summary": "x", "total_minutes": 180, "nodes": [], "orders": []},
        stage_label="stream",
        unmet_cuisines=["韩式烤肉"],
    )
    assert "未满足的品类诉求" in msg and "韩式烤肉" in msg


def test_system_prompt_has_reason_fork_rule() -> None:
    assert "这版没安排" in NARRATOR_SYSTEM_PROMPT, (
        "诚实告知规则应含原因分叉：附近没找到 ≠ 这版没安排，措辞不许互串"
    )
