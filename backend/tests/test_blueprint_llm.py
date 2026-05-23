"""tests.test_blueprint_llm —— LLM 蓝图生成器单元测试（mock LLM, edge_v1）。

验证：
- 给定 intent + 候选预览 → 调 LLM 出 PlanBlueprint（仅 nodes/preferred_start_time/rationale）
- LLM 返非法 JSON / 空内容 → 抛 BlueprintGenError
- LLM 返合法 JSON 但 nodes 缺失或空 → 抛 BlueprintGenError
- LLM 返旧 `stages` 字段（schema 漂移）→ 抛 BlueprintGenError（解析层显式挡住）
- LLM 返 nodes 内部含旧 `start_time` / `end_time` / `commute_minutes` → 抛 BlueprintGenError
- LLM 返围栏 ```json ... ``` → 围栏剥离后正常解析
- 候选预览**不**含 commute_matrix（edge_v1：assemble 自己算 hop）
- critic_feedback 注入到 user message（重试逻辑保留）
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field

import pytest

# 过渡态桥（删除时机：Wave 5 Task 9 完成后）：
# Task 1 已删除 ItineraryStage，但 agent/__init__.py 仍 eager-import 旧 planner，
# 整个 agent 包暂时无法直接 import。blueprint_llm.py 自身只依赖
# blueprint / llm_client / prompts 兄弟模块，此处把 agent 注册为空命名空间包，
# 让 from-import 跳过 __init__.py 副作用。
if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    from pathlib import Path as _Path

    _agent_dir = _Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.planning.blueprint.blueprint import BlueprintTargetKind, PlanBlueprint  # noqa: E402
from agent.planning.blueprint.blueprint_llm import (  # noqa: E402
    BlueprintGenError,
    build_candidate_preview,
    generate_blueprint,
)
from data.loader import load_pois, load_restaurants  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402


# ============================================================
# Fixtures
# ============================================================


def _intent(duration: list[int] | None = None) -> IntentExtraction:
    return IntentExtraction(
        start_time="today_afternoon",
        duration_hours=duration or [1, 1],
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
    """最小可用 mock：实现 chat()，记录最后一次 messages 供断言。"""

    provider = "mock"
    model = "mock"

    def __init__(self, content: str):
        self._content = content
        self.last_messages: list = []
        self.call_count: int = 0

    def chat(self, messages, *, temperature=0.3, response_format=None):
        self.last_messages = messages
        self.call_count += 1
        return _MockResp(content=self._content)


class _RaisingClient:
    """模拟 LLM API 失败，让 generate_blueprint 走 llm_chat_failed 分支。"""

    provider = "mock"
    model = "mock"

    def chat(self, messages, *, temperature=0.3, response_format=None):
        raise RuntimeError("simulated upstream failure")


# ============================================================
# build_candidate_preview（edge_v1：不含 commute_matrix）
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
    """蓝图 LLM 必须看到 opening_hours，才能正确决策时段。"""
    rests = [load_restaurants()[0]]
    preview = build_candidate_preview([], rests, top_k=1)
    rest_view = preview["restaurants"][0]
    assert "opening_hours" in rest_view
    assert "tags" in rest_view
    assert "distance_km" in rest_view


def test_build_preview_does_not_include_commute_matrix():
    """edge_v1 关键回归：preview **不**含 commute_matrix（assemble 自己算 hop）。"""
    pois = load_pois()[:3]
    rests = load_restaurants()[:3]
    preview = build_candidate_preview(
        pois, rests, top_k=3, transport_preference="taxi"
    )
    assert "commute_matrix" not in preview, (
        "edge_v1 preview 不应再含 commute_matrix；"
        "assemble_from_blueprint 通过 lookup_hop 自己算 hop"
    )
    # 但 transport_preference 仍要透传给 LLM 作为元信息
    assert preview.get("transport_preference") == "taxi"


def test_build_preview_includes_review_excerpts():
    """UGC 引用逻辑保留：每条 POI / 餐厅应带 review_excerpts 字段（哪怕为空 list）。"""
    pois = load_pois()[:1]
    rests = load_restaurants()[:1]
    preview = build_candidate_preview(pois, rests, top_k=1)
    if preview["pois"]:
        assert "review_excerpts" in preview["pois"][0]
    if preview["restaurants"]:
        assert "review_excerpts" in preview["restaurants"][0]


# ============================================================
# generate_blueprint —— 合法路径
# ============================================================


def test_generate_blueprint_success():
    """LLM 返合法 edge_v1 JSON（nodes 数组）→ 解析为 PlanBlueprint。"""
    client = _MockClient(
        content="""
{
  "nodes": [
    {"kind": "用餐", "target_kind": "restaurant", "target_id": "R001", "duration_min": 60},
    {"kind": "主活动", "target_kind": "poi", "target_id": "P001", "duration_min": 120}
  ],
  "preferred_start_time": "14:00",
  "rationale": "edge_v1 测试用例：先吃饭后活动"
}
"""
    )
    intent = _intent([3, 4])
    pois = load_pois()[:3]
    rests = load_restaurants()[:3]
    bp = generate_blueprint(intent, pois, rests, client=client)
    assert isinstance(bp, PlanBlueprint)
    assert len(bp.nodes) == 2
    assert bp.nodes[0].target_id == "R001"
    assert bp.nodes[0].target_kind == BlueprintTargetKind.RESTAURANT
    assert bp.nodes[1].target_id == "P001"
    assert bp.nodes[1].target_kind == BlueprintTargetKind.POI
    assert bp.preferred_start_time == "14:00"
    assert "edge_v1" in bp.rationale


def test_generate_blueprint_strips_markdown_fence():
    """LLM 返围栏 ```json ... ``` 也能正确解析。"""
    client = _MockClient(
        content="""```json
{
  "nodes": [
    {"kind": "主活动", "target_kind": "poi", "target_id": "P001", "duration_min": 90}
  ],
  "preferred_start_time": "15:00",
  "rationale": "fence test"
}
```"""
    )
    bp = generate_blueprint(_intent([2, 2]), load_pois()[:2], [], client=client)
    assert len(bp.nodes) == 1
    assert bp.preferred_start_time == "15:00"


def test_generate_blueprint_with_critic_feedback_injected():
    """带 critic 反馈的二次调用应在 prompt 里附上违规信息（重试链路保留）。"""
    client = _MockClient(
        content=(
            '{"nodes": ['
            '{"kind": "用餐", "target_kind": "restaurant", '
            '"target_id": "R001", "duration_min": 45}'
            '], "preferred_start_time": "12:30", "rationale": "ok"}'
        )
    )
    bp = generate_blueprint(
        _intent(),
        [],
        load_restaurants()[:2],
        client=client,
        critic_feedback=["节点时序重叠", "用餐时段超出营业时间"],
    )
    assert isinstance(bp, PlanBlueprint)
    # critic_feedback 必须真的进了 user message
    last_user_msg = next(
        m.content for m in client.last_messages if m.role == "user"
    )
    assert "重叠" in last_user_msg or "营业" in last_user_msg
    assert client.call_count == 1, "本次调用应只发一轮 LLM"


# ============================================================
# generate_blueprint —— 错误路径
# ============================================================


def test_generate_blueprint_invalid_json_raises():
    client = _MockClient(content="not json at all")
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), [], [], client=client)
    assert exc.value.reason == "json_decode_failed"


def test_generate_blueprint_empty_response_raises():
    client = _MockClient(content="")
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), [], [], client=client)
    assert exc.value.reason == "empty_response"


def test_generate_blueprint_non_object_raises():
    """LLM 返 JSON array 而非 object → 拒绝。"""
    client = _MockClient(content='[{"nodes": []}]')
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), [], [], client=client)
    assert exc.value.reason == "not_a_json_object"


def test_generate_blueprint_legacy_stages_field_rejected():
    """edge_v1 关键回归：LLM 退回旧 stages 输出 → 解析层显式拒绝。"""
    client = _MockClient(
        content="""
{
  "stages": [
    {"kind": "出发", "start_time": "14:00", "duration_min": 15, "target_kind": "none"},
    {"kind": "用餐", "start_time": "14:15", "duration_min": 60, "target_kind": "restaurant", "target_id": "R001"}
  ],
  "rationale": "旧 schema 输出"
}
"""
    )
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), [], load_restaurants()[:2], client=client)
    assert exc.value.reason == "legacy_stages_field"
    # detail 应给出清楚的诊断让 LLM 在 backprompt 里知道怎么改
    assert "stages" in (exc.value.detail or "")
    assert "nodes" in (exc.value.detail or "")


def test_generate_blueprint_legacy_node_start_time_rejected():
    """LLM 输出 nodes 但每个 node 仍带 start_time（半迁移状态）→ 拒绝。"""
    client = _MockClient(
        content="""
{
  "nodes": [
    {"kind": "用餐", "target_kind": "restaurant", "target_id": "R001",
     "duration_min": 60, "start_time": "14:15"}
  ],
  "preferred_start_time": "14:00",
  "rationale": "node 含 start_time"
}
"""
    )
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), [], load_restaurants()[:2], client=client)
    assert exc.value.reason == "legacy_node_field"
    assert "start_time" in (exc.value.detail or "")


def test_generate_blueprint_legacy_node_end_time_rejected():
    """LLM 输出 nodes 但 node 含 end_time → 拒绝。"""
    client = _MockClient(
        content="""
{
  "nodes": [
    {"kind": "主活动", "target_kind": "poi", "target_id": "P001",
     "duration_min": 90, "end_time": "16:30"}
  ],
  "preferred_start_time": "15:00",
  "rationale": "node 含 end_time"
}
"""
    )
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), load_pois()[:2], [], client=client)
    assert exc.value.reason == "legacy_node_field"
    assert "end_time" in (exc.value.detail or "")


def test_generate_blueprint_legacy_node_commute_minutes_rejected():
    """LLM 输出 nodes 但 node 含 commute_minutes → 拒绝（hop 由系统算）。"""
    client = _MockClient(
        content="""
{
  "nodes": [
    {"kind": "主活动", "target_kind": "poi", "target_id": "P001",
     "duration_min": 90, "commute_minutes": 12}
  ],
  "preferred_start_time": "14:00",
  "rationale": "node 含 commute_minutes"
}
"""
    )
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), load_pois()[:2], [], client=client)
    assert exc.value.reason == "legacy_node_field"
    assert "commute_minutes" in (exc.value.detail or "")


def test_generate_blueprint_missing_nodes_field_rejected():
    """LLM 没出 nodes 字段 → 抛 nodes_missing_or_empty。"""
    client = _MockClient(content='{"rationale": "I forgot nodes"}')
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), [], [], client=client)
    assert exc.value.reason == "nodes_missing_or_empty"


def test_generate_blueprint_empty_nodes_rejected():
    """LLM 出 nodes=[] → 抛 nodes_missing_or_empty。"""
    client = _MockClient(content='{"nodes": [], "rationale": "empty"}')
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), [], [], client=client)
    assert exc.value.reason == "nodes_missing_or_empty"


def test_generate_blueprint_node_not_dict_rejected():
    """nodes 含非 dict 项 → 拒绝。"""
    client = _MockClient(
        content='{"nodes": ["not a dict"], "preferred_start_time": "14:00", "rationale": ""}'
    )
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), [], [], client=client)
    assert exc.value.reason == "node_not_dict"


def test_generate_blueprint_pydantic_validation_failure():
    """nodes 字段类型对但内容违反 BlueprintNode 约束（如 target_kind 非法值）→ 走 Pydantic 兜底。"""
    client = _MockClient(
        content="""
{
  "nodes": [
    {"kind": "主活动", "target_kind": "home", "target_id": "P001", "duration_min": 90}
  ],
  "preferred_start_time": "14:00",
  "rationale": "target_kind=home 在 BlueprintNode 不允许"
}
"""
    )
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), load_pois()[:2], [], client=client)
    assert exc.value.reason == "blueprint_validation_failed"


def test_generate_blueprint_extra_field_rejected_by_pydantic():
    """node 含未知字段（非旧 schema 但 extra='forbid'）→ Pydantic 兜底。"""
    client = _MockClient(
        content="""
{
  "nodes": [
    {"kind": "主活动", "target_kind": "poi", "target_id": "P001",
     "duration_min": 90, "unknown_field": "x"}
  ],
  "preferred_start_time": "14:00",
  "rationale": "extra forbid"
}
"""
    )
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), load_pois()[:2], [], client=client)
    assert exc.value.reason == "blueprint_validation_failed"


def test_generate_blueprint_llm_chat_failure_wrapped():
    """LLM 客户端抛异常 → 包成 BlueprintGenError(reason='llm_chat_failed')。"""
    with pytest.raises(BlueprintGenError) as exc:
        generate_blueprint(_intent(), [], [], client=_RaisingClient())
    assert exc.value.reason == "llm_chat_failed"
    assert "RuntimeError" in (exc.value.detail or "")
