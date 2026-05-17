"""tests.test_blueprint_llm —— LLM 蓝图生成器单元测试（mock LLM）。

验证：
- 给定 intent + 候选预览 → 调 LLM 出 PlanBlueprint
- LLM 返非法 JSON → 抛 BlueprintGenError
- LLM 返合法 JSON 但字段缺失 → 抛 BlueprintGenError
- 候选预览正确序列化（id/name/tags/distance/opening_hours/avg_price/rating）
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agent.blueprint import BlueprintTargetKind, PlanBlueprint
from agent.blueprint_llm import (
    BlueprintGenError,
    build_candidate_preview,
    generate_blueprint,
)
from data.loader import load_pois, load_restaurants
from schemas.intent import Companion, IntentExtraction


def _intent(duration: list[int] = [1, 1]) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=list(duration),
        distance_max_km=5,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="独处放空",
        raw_input="只有一个小时",
        parse_confidence=0.9,
    )


# ============================================================
# Mock LLM Client
# ============================================================

@dataclass
class _MockResp:
    content: str
    tool_calls: list = field(default_factory=list)
    finish_reason: str = "stop"
    raw: dict | None = None


class _MockClient:
    provider = "mock"
    model = "mock"

    def __init__(self, content: str):
        self._content = content
        self.last_messages: list = []

    def chat(self, messages, *, temperature=0.3, response_format=None):
        self.last_messages = messages
        return _MockResp(content=self._content)


# ============================================================
# build_candidate_preview
# ============================================================

def test_build_preview_truncates_large_lists():
    pois = load_pois()[:10]
    rests = load_restaurants()[:10]
    preview = build_candidate_preview(pois, rests, top_k=3)
    assert "pois" in preview
    assert "restaurants" in preview
    assert len(preview["pois"]) <= 3
    assert len(preview["restaurants"]) <= 3


def test_build_preview_includes_opening_hours():
    """蓝图 LLM 必须看到 opening_hours，才能正确决策时间段。"""
    rests = [load_restaurants()[0]]
    preview = build_candidate_preview([], rests, top_k=1)
    rest_view = preview["restaurants"][0]
    assert "opening_hours" in rest_view
    assert "tags" in rest_view
    assert "distance_km" in rest_view


def test_build_preview_includes_poi_age_range():
    """POI 预览含 age_range，让 LLM 判断亲子适配。"""
    pois = [p for p in load_pois() if p.age_range][:1]
    if not pois:
        pytest.skip("没有 POI 含 age_range")
    preview = build_candidate_preview(pois, [], top_k=1)
    poi_view = preview["pois"][0]
    assert "age_range" in poi_view


# ============================================================
# generate_blueprint
# ============================================================

def test_generate_blueprint_success():
    """LLM 返合法蓝图 JSON → 解析为 PlanBlueprint。"""
    client = _MockClient(
        content="""
{
  "stages": [
    {"kind": "出发", "start_time": "14:00", "duration_min": 15, "target_kind": "none"},
    {"kind": "用餐", "start_time": "14:15", "duration_min": 45, "target_kind": "restaurant", "target_id": "R001"},
    {"kind": "返回", "start_time": "15:00", "duration_min": 15, "target_kind": "none"}
  ],
  "rationale": "只有 1 小时 + 想吃饭 → 直接去最近餐厅"
}
"""
    )
    intent = _intent([1, 1])
    pois = load_pois()[:3]
    rests = load_restaurants()[:3]
    bp = generate_blueprint(intent, pois, rests, client=client)
    assert isinstance(bp, PlanBlueprint)
    assert len(bp.stages) == 3
    assert bp.stages[1].target_id == "R001"
    assert bp.stages[1].target_kind == BlueprintTargetKind.RESTAURANT


def test_generate_blueprint_strips_markdown_fence():
    """LLM 返围栏 ```json ... ``` 也能正确解析。"""
    client = _MockClient(
        content="""```json
{
  "stages": [{"kind": "出发", "start_time": "14:00", "duration_min": 15}],
  "rationale": "test"
}
```"""
    )
    bp = generate_blueprint(_intent(), [], [], client=client)
    assert len(bp.stages) == 1


def test_generate_blueprint_invalid_json_raises():
    client = _MockClient(content="not json at all")
    with pytest.raises(BlueprintGenError):
        generate_blueprint(_intent(), [], [], client=client)


def test_generate_blueprint_missing_stages_raises():
    client = _MockClient(content='{"rationale": "no stages"}')
    with pytest.raises(BlueprintGenError):
        generate_blueprint(_intent(), [], [], client=client)


def test_generate_blueprint_with_critic_feedback():
    """带 critic 反馈的二次调用应在 prompt 里附上违规信息。"""
    client = _MockClient(
        content='{"stages": [{"kind": "出发", "start_time": "14:00", "duration_min": 15}], "rationale": "ok"}'
    )
    bp = generate_blueprint(
        _intent(),
        [],
        [],
        client=client,
        critic_feedback=["段 a 与段 b 重叠", "用餐时段超出营业时间"],
    )
    assert isinstance(bp, PlanBlueprint)
    # 检查 critic_feedback 被嵌入 user message
    last_user_msg = next(
        m.content for m in client.last_messages if m.role == "user"
    )
    assert "重叠" in last_user_msg or "营业" in last_user_msg


def test_generate_blueprint_24h_scenario():
    """24h 营业餐厅场景：LLM 应能输出晚上 22:00 的蓝图。

    本测试仅验证 generate_blueprint 不会强行限制 start_time 范围；
    具体输出由 LLM prompt + critic 决定，本测试 mock LLM 直接产出 22:00。
    """
    client = _MockClient(
        content="""
{
  "stages": [
    {"kind": "出发", "start_time": "21:30", "duration_min": 30, "target_kind": "none"},
    {"kind": "夜宵", "start_time": "22:00", "duration_min": 60, "target_kind": "restaurant", "target_id": "R001", "note": "24h 营业"},
    {"kind": "返回", "start_time": "23:00", "duration_min": 30, "target_kind": "none"}
  ],
  "rationale": "用户想夜宵"
}
"""
    )
    intent = IntentExtraction(
        start_time="today_evening",
        duration_hours=[2, 2],
        distance_max_km=5,
        companions=[],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="独处放空",
        raw_input="今晚想吃个夜宵",
        parse_confidence=0.9,
    )
    bp = generate_blueprint(intent, [], load_restaurants()[:2], client=client)
    assert bp.stages[1].kind == "夜宵"
    assert bp.stages[1].start_time == "22:00"
