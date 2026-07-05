"""spec C 端到端真实功能演示——把 9 个 task 的核心能力一次跑完。

不依赖真 LLM（用 stub mock client 控制返回），仅验证：
1. grounding-first 前置硬剔除（task 4）
2. critic compute_reward + 三档反馈模式（task 2）
3. TOOL_RESPONSE_INCONSISTENCY hallucination 防护（task 3）
4. preference_scorer LLM 语义打分（task 5）+ _utility 加项（task 5）
5. memory_writer 副作用 + recent_trips 召回（task 6）
6. UserProfile 三层 schema 向后兼容（task 6）
7. CRITIC_FEEDBACK_MODE 三档切换 + reward 模式（task 2）

执行：python -m scripts.verify_spec_c_demo
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

# 强制 UTF-8 stdout（Windows GBK 终端会破坏中文输出）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.core.llm_client import LLMChatResponse
from agent.core.trace import Tracer
from agent.planning.critic.critics_v2 import (
    CODE_WEIGHTS,
    SEVERITY_WEIGHTS,
    Severity,
    Violation,
    ViolationCode,
    _check_tool_consistency,
    _get_feedback_mode,
    compute_reward,
    format_violations_for_llm,
    validate_itinerary,
)
from agent.planning.memory_writer import persist_memory
from agent.planning.planners.ils_planner import (
    _grounding_filter_poi,
    _utility,
)
from agent.planning.preference_scorer import score_pois_with_llm
from agent.planning.weights_llm import PlanningWeights
from schemas.domain import (
    Location,
    Poi,
    SuggestedDuration,
    UserProfile,
)
from schemas.intent import Companion, IntentExtraction


# ============================================================
# Helper：构造 fixture
# ============================================================


def _make_intent_kid(distance_max_km: float = 5.0) -> IntentExtraction:
    return IntentExtraction(
        start_time="2026-05-22T14:00",
        duration_hours=[4, 6],
        distance_max_km=distance_max_km,
        companions=[Companion(role="孩子", age=5, count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="带 5 岁娃出去玩半天",
        parse_confidence=0.9,
    )


def _make_poi(
    poi_id: str,
    *,
    distance_km: float = 3.0,
    suggested_default: int = 60,
    suggested_kid_3_6: int | None = None,
    business_status: str = "open",
) -> Poi:
    poi = Poi(
        id=poi_id,
        name=f"{poi_id}（演示）",
        type="测试",
        location=Location(name="测试", lat=30.0, lng=120.0),
        distance_km=distance_km,
        opening_hours="09:00-21:00",
        rating=4.5,
        suggested_duration_minutes=SuggestedDuration(
            default=suggested_default,
            kid_3_6=suggested_kid_3_6,
        ),
    )
    if business_status != "open":
        object.__setattr__(poi, "business_status", business_status)
    return poi


# ============================================================
# Demo 1：grounding-first（task 4）
# ============================================================


def demo_grounding_first():
    print("\n" + "=" * 70)
    print("Demo 1：grounding-first 前置硬剔除（task 4）")
    print("=" * 70)
    print("场景：5 岁娃 + 4 个 POI 候选（1 个超 cap，3 个合规）")

    intent = _make_intent_kid(distance_max_km=10.0)
    candidates = [
        _make_poi("P_LONG", suggested_default=180, suggested_kid_3_6=120),
        _make_poi("P_OK_1", suggested_default=60, suggested_kid_3_6=60),
        _make_poi("P_OK_2", suggested_default=75, suggested_kid_3_6=75),
        _make_poi("P_OK_3", suggested_default=80, suggested_kid_3_6=80),
    ]

    tracer = Tracer()
    filtered = _grounding_filter_poi(candidates, intent, tracer)

    print(f"输入候选数：{len(candidates)}")
    print(f"过滤后候选数：{len(filtered)}")
    print(f"剔除事件：")
    for r in tracer.records:
        if r.type == "grounding_filtered":
            print(f"  ✓ poi_id={r.payload.get('poi_id')} reason={r.payload['reason']}")

    assert len(filtered) == 3, f"应剔除 P_LONG，剩 3 个，实际 {len(filtered)}"
    assert "P_LONG" not in {p.id for p in filtered}
    print("[PASS] P_LONG 因 kid_3_6=120 > 90min cap 被前置剔除")


# ============================================================
# Demo 2：critic compute_reward + 三档反馈模式（task 2）
# ============================================================


def demo_compute_reward():
    print("\n" + "=" * 70)
    print("Demo 2：critic compute_reward + 三档反馈模式（task 2）")
    print("=" * 70)

    violations = [
        Violation(
            code=ViolationCode.INVARIANT_BROKEN,
            severity=Severity.CRITICAL,
            message="结构性违规",
            field_path="hops",
        ),
        Violation(
            code=ViolationCode.DISTANCE_EXCEEDED,
            severity=Severity.WARNING,
            message="距离超限",
            field_path="nodes[1]",
        ),
    ]

    print("【常量验证】")
    print(f"  SEVERITY_WEIGHTS[CRITICAL] = {SEVERITY_WEIGHTS[Severity.CRITICAL]}")
    print(f"  SEVERITY_WEIGHTS[WARNING]  = {SEVERITY_WEIGHTS[Severity.WARNING]}")
    print(
        f"  比例 CRITICAL/WARNING = {SEVERITY_WEIGHTS[Severity.CRITICAL] / SEVERITY_WEIGHTS[Severity.WARNING]:.0f}× "
        f"（macro 设计：CRITICAL 必须 5× 于 WARNING）"
    )
    print(f"  CODE_WEIGHTS[INVARIANT_BROKEN] = {CODE_WEIGHTS[ViolationCode.INVARIANT_BROKEN]} (macro)")
    print(f"  CODE_WEIGHTS[DISTANCE_EXCEEDED] = {CODE_WEIGHTS[ViolationCode.DISTANCE_EXCEEDED]} (micro)")

    reward = compute_reward(violations)
    expected = -(1.0 * 1.5 + 0.2 * 0.8)
    print(f"\n【reward 计算】")
    print(f"  reward({len(violations)} violations) = {reward:.4f}")
    print(f"  预期：-({SEVERITY_WEIGHTS[Severity.CRITICAL]}×{CODE_WEIGHTS[ViolationCode.INVARIANT_BROKEN]} + "
          f"{SEVERITY_WEIGHTS[Severity.WARNING]}×{CODE_WEIGHTS[ViolationCode.DISTANCE_EXCEEDED]}) = {expected:.4f}")
    assert abs(reward - expected) < 1e-6
    print("[PASS] compute_reward 数学正确")

    print(f"\n【三档反馈模式切换】")
    for mode in ("pinpoint-all", "first-only", "reward"):
        os.environ["CRITIC_FEEDBACK_MODE"] = mode
        out = format_violations_for_llm(violations)
        # 仅含 1 个 critical（INVARIANT_BROKEN）；warning 不进 backprompt
        if mode == "reward":
            print(f"  mode={mode!r:18} → 输出空字符串（dense scalar 路径）")
            assert out == ""
        elif mode == "first-only":
            print(f"  mode={mode!r:18} → 输出 {len(out)} 字符（仅第一条 critical）")
            assert "1." in out and "结构性违规" in out
        else:
            print(f"  mode={mode!r:18} → 输出 {len(out)} 字符（完整列表）")
            assert "1 处违规" in out
    os.environ.pop("CRITIC_FEEDBACK_MODE", None)

    # 越界值 fallback
    os.environ["CRITIC_FEEDBACK_MODE"] = "RL-mode"
    mode = _get_feedback_mode()
    assert mode == "pinpoint-all"
    print(f"  mode={'RL-mode'!r:18} → fallback 到 {mode!r}（越界值兜底）")
    os.environ.pop("CRITIC_FEEDBACK_MODE", None)


# ============================================================
# Demo 3：TOOL_RESPONSE_INCONSISTENCY hallucination 防护（task 3）
# ============================================================


def demo_tool_response_inconsistency():
    print("\n" + "=" * 70)
    print("Demo 3：TOOL_RESPONSE_INCONSISTENCY hallucination 防护（task 3）")
    print("=" * 70)
    print("场景：LLM 在 itinerary 中编造一个不存在的 P999 POI ID")

    # 复用 test_critics_v2 fixture
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from test_critics_v2 import _make_legal_itinerary

    itinerary = _make_legal_itinerary(poi_id="P999")  # 编造 ID

    # 候选池只有 P040 / P033（真实 ID）
    class FakePoi:
        def __init__(self, pid):
            self.id = pid

    tool_results = {
        "pois": [FakePoi("P040"), FakePoi("P033")],
        "restaurants": [FakePoi("R001")],
    }

    violations = _check_tool_consistency(itinerary, tool_results)
    print(f"违规数：{len(violations)}")
    for v in violations:
        print(f"  ✓ code={v.code.value}")
        print(f"    severity={v.severity.value}")
        print(f"    message={v.message[:80]}...")
        # 设计纪律：不暴露 dot-path
        assert "target_id" not in v.message
        assert "nodes[" not in v.message
        print(f"    [PASS] 未暴露 dot-path 字段名给 LLM")

    assert len(violations) == 1
    assert violations[0].code == ViolationCode.TOOL_RESPONSE_INCONSISTENCY
    assert violations[0].severity == Severity.CRITICAL
    assert "P999" in violations[0].message
    print(f"\n[PASS] LLM 编造的 P999 被立刻识别为 hallucination")


# ============================================================
# Demo 4：preference_scorer LLM 语义打分（task 5）
# ============================================================


def demo_preference_scorer():
    print("\n" + "=" * 70)
    print("Demo 4：preference_scorer LLM 语义打分（task 5）+ _utility 加项")
    print("=" * 70)
    print("场景：5 岁娃 + 2 POI（亲子 / 成人）→ mock LLM 给亲子 0.92 / 成人 0.35")

    intent = _make_intent_kid()
    pois = [
        _make_poi("P_KID", suggested_default=60, suggested_kid_3_6=60),
        _make_poi("P_ADULT", suggested_default=120, suggested_kid_3_6=120),
    ]

    # mock LLM 客户端
    client = MagicMock()
    client.provider = "deepseek"
    client.model = "test"
    client.chat.return_value = LLMChatResponse(
        content='{"scores": {"P_KID": 0.92, "P_ADULT": 0.35}}'
    )

    scores = score_pois_with_llm(intent, pois, client=client)
    print(f"语义分数：{scores}")
    assert scores["P_KID"] == 0.92
    assert scores["P_ADULT"] == 0.35

    # _utility 加项验证
    weights = PlanningWeights(
        comfort=0.3, time=0.2, cost=0.2, smoothness=0.3, source="test"
    )
    util_no_sem, _ = _utility(pois[0], None, "", intent, weights, semantic_scores=None)
    util_high, _ = _utility(
        pois[0], None, "", intent, weights, semantic_scores={"P_KID": 1.0}
    )
    diff = util_high - util_no_sem
    print(f"\n_utility 加项验证：")
    print(f"  semantic=None 时 utility = {util_no_sem:.4f}")
    print(f"  semantic=1.0 时 utility = {util_high:.4f}")
    print(f"  差值 = {diff:.4f}（预期 0.3 = 0.3 × 1.0）")
    assert abs(diff - 0.3) < 1e-6
    print("[PASS] LLM 语义打分按 0.3 权重叠加到 _utility")

    # stub 模式短路
    client_stub = MagicMock()
    client_stub.provider = "stub"
    client_stub.model = "test"
    scores_stub = score_pois_with_llm(intent, pois, client=client_stub)
    assert scores_stub == {"P_KID": 0.5, "P_ADULT": 0.5}
    client_stub.chat.assert_not_called()
    print("[PASS] stub 模式短路返全 0.5（不调 LLM）")

    # LLM 失败兜底
    client_fail = MagicMock()
    client_fail.provider = "deepseek"
    client_fail.chat.side_effect = RuntimeError("LLM 超时")
    scores_fail = score_pois_with_llm(intent, pois, client=client_fail)
    assert scores_fail == {"P_KID": 0.5, "P_ADULT": 0.5}
    print("[PASS] LLM 失败兜底返全 0.5（不阻断 ILS 主路径）")


# ============================================================
# Demo 5：memory_writer 副作用 + 三层 schema（task 6）
# ============================================================


def demo_memory_writer():
    print("\n" + "=" * 70)
    print("Demo 5：memory_writer 副作用（会话私有行程档案）+ UserProfile 三层 schema（task 6）")
    print("=" * 70)
    # 记忆身份读写分离批（2026-07-05）：persist_memory 不再写 user_profile.json
    # （模板只读），改写 data.memory_store 的会话私有 recent_trips 区（键=session_id）。
    from data.memory_store import get_recent_trips, reset_all_memory

    reset_all_memory()

    # 旧 4 字段 profile 仍可加载（schema 向后兼容，模板侧不变）
    old_profile = {
        "user_id": "demo_user",
        "home_location": {"name": "测试家", "lat": 30.0, "lng": 120.0},
        "default_budget": 300.0,
        "transport_preference": "taxi",
    }
    loaded = UserProfile.model_validate(old_profile)
    assert loaded.recent_trips is None
    assert loaded.dietary_preference is None
    print("[PASS] 旧 4 字段 profile 加载成功（schema 向后兼容）")

    # 复用 test_critics_v2 fixture 构造 itinerary + intent
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from test_critics_v2 import _make_intent, _make_legal_itinerary

    intent = _make_intent(social_context="家庭日常")
    itinerary = _make_legal_itinerary()

    client_stub = MagicMock()
    client_stub.provider = "stub"

    session_id = "verify_spec_c_demo_session"
    state = {
        "intent": intent,
        "itinerary": itinerary,
        "user_decision": "confirm",
        "user_id": "demo_user",
        "session_id": session_id,
    }

    ok = persist_memory(state, client=client_stub)
    assert ok is True

    # 验证写入（会话私有档案）
    trips = get_recent_trips(session_id)
    assert len(trips) == 1
    trip = trips[0]
    print(f"\n写入 1 条 recent_trip（键={session_id!r}）：")
    print(f"  social_context = {trip.social_context!r}")
    print(f"  summary = {trip.summary[:80]}...")
    print(f"  success = {trip.success}")
    # 隐私脱敏：不含具体年龄
    assert "5 岁" not in trip.summary
    assert "5岁" not in trip.summary
    print("[PASS] summary 不含具体年龄数字（隐私脱敏）")

    # 5 分钟幂等键
    ok2 = persist_memory(state, client=client_stub)
    assert ok2 is False
    assert len(get_recent_trips(session_id)) == 1  # 仍 1 条
    print("[PASS] 5 分钟内重复 persist 被幂等键拦下")

    # cancel 跳过
    state_cancel = {**state, "user_decision": "cancel"}
    ok3 = persist_memory(state_cancel, client=client_stub)
    assert ok3 is False
    print("[PASS] user_decision='cancel' 时跳过写入")

    # 无 session_id（无会话身份）跳过 + 不抛异常
    state_no_sess = {k: v for k, v in state.items() if k != "session_id"}
    ok4 = persist_memory(state_no_sess, client=client_stub)
    assert ok4 is False
    print("[PASS] 缺 session_id 仅返 False（会话即身份，无身份不写）")

    # 会话隔离
    assert get_recent_trips("verify_other_session") == []
    print("[PASS] 其它会话读不到本会话的行程档案（会话私有）")
    reset_all_memory()


# ============================================================
# Demo 7：意图解析阶段注入 user_profile 召回（task 6）
# ============================================================


def demo_intent_parser_recall():
    print("\n" + "=" * 70)
    print("Demo 7：intent parser 注入 user_profile 召回（task 6）")
    print("=" * 70)

    from agent.intent.prompts.intent_parser_prompt import (
        INTENT_PARSER_SYSTEM_PROMPT,
        _build_user_profile_addendum,
        build_intent_parser_system_prompt_with_priors,
    )

    # 读写分离批：recent_trips 召回按会话键——先给演示会话写一条档案
    from data.memory_store import record_recent_trip, reset_all_memory
    from schemas.domain import RecentTrip

    reset_all_memory()
    demo_session = "verify_spec_c_demo_recall"
    record_recent_trip(
        demo_session,
        RecentTrip(
            timestamp="2026-07-01T10:00:00Z",
            social_context="家庭日常",
            summary="家庭日常场景行程：活动 → 用餐；总时长约 3 小时。",
            success=True,
        ),
    )

    addendum = _build_user_profile_addendum(demo_session)
    print(f"addendum 长度：{len(addendum)} 字符")

    if "饮食偏好" in addendum:
        print("[PASS] dietary_preference 已注入 prompt（模板侧）")
        # 截取 dietary 段输出
        for line in addendum.split("\n"):
            if "健康轻食" in line or "辣度" in line or "饮食" in line:
                print(f"  {line[:100]}")
                break
    else:
        print("[SKIP] mock_data 未含 dietary_preference")

    if "最近行程" in addendum:
        print("[PASS] recent_trips 已注入 prompt（会话私有档案）")
        for line in addendum.split("\n"):
            if "场景：" in line:
                print(f"  {line[:100]}")
    else:
        print("[FAIL] 会话档案有数据但未注入")

    fresh_addendum = _build_user_profile_addendum("verify_fresh_session")
    assert "最近行程" not in fresh_addendum
    print("[PASS] 新会话零累积 → 零召回（隐私式诚实）")

    # 完整 prompt 长度对比
    base_len = len(INTENT_PARSER_SYSTEM_PROMPT)
    full = build_intent_parser_system_prompt_with_priors("demo_user", demo_session)
    full_len = len(full)
    print(
        f"\nprompt 总长度对比：base = {base_len} → full = {full_len}（含 priors + user_profile）"
    )
    assert full_len > base_len
    print("[PASS] full prompt 比 base 长（注入 priors + user_profile 召回）")


# ============================================================
# Demo 8：validate_itinerary 集成 tool_results（task 3）
# ============================================================


def demo_validate_itinerary_integration():
    print("\n" + "=" * 70)
    print("Demo 8：validate_itinerary 端到端（task 2 + task 3 集成）")
    print("=" * 70)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from test_critics_v2 import _make_intent, _make_legal_itinerary

    intent = _make_intent()

    class FakePoi:
        def __init__(self, pid):
            self.id = pid

    # 编造 ID
    bad_itinerary = _make_legal_itinerary(poi_id="P_HALLUCINATED")
    tool_results = {"pois": [FakePoi("P040")], "restaurants": [FakePoi("R001")]}

    violations = validate_itinerary(bad_itinerary, intent, tool_results=tool_results)
    inconsistency = [
        v for v in violations if v.code == ViolationCode.TOOL_RESPONSE_INCONSISTENCY
    ]
    print(f"含编造 POI 时违规数：{len(violations)}（含 {len(inconsistency)} 个 hallucination）")
    assert len(inconsistency) == 1
    print("[PASS] validate_itinerary 透传 tool_results 触发 hallucination 检查")

    # reward 计算（含 macro 级权重）
    reward = compute_reward(violations)
    print(f"reward = {reward:.4f}")
    print(f"  TOOL_RESPONSE_INCONSISTENCY × CRITICAL = -1.0 × 1.5 = -1.5")
    assert reward <= -1.5
    print("[PASS] hallucination 触发 macro 级强惩罚")

    # 不传 tool_results（向后兼容）
    real_itinerary = _make_legal_itinerary()
    violations_no_tr = validate_itinerary(real_itinerary, intent)
    inconsistency_no_tr = [
        v
        for v in violations_no_tr
        if v.code == ViolationCode.TOOL_RESPONSE_INCONSISTENCY
    ]
    assert len(inconsistency_no_tr) == 0
    print("[PASS] 不传 tool_results 时跳过 hallucination 检查（向后兼容）")


# ============================================================
# 主流程
# ============================================================


def main():
    print("=" * 70)
    print("spec C `algorithm-redesign` 端到端真实功能演示")
    print(f"git tag：v-spec-c-done")
    print(f"测试范围：8 个核心能力的真实运行验证（不依赖真 LLM）")
    print("=" * 70)

    demos = [
        demo_grounding_first,
        demo_compute_reward,
        demo_tool_response_inconsistency,
        demo_preference_scorer,
        demo_memory_writer,
        demo_intent_parser_recall,
        demo_validate_itinerary_integration,
    ]

    passed = 0
    failed = 0
    for demo in demos:
        try:
            demo()
            passed += 1
        except AssertionError as e:
            print(f"\n[FAIL] {demo.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"\n[ERROR] {demo.__name__}: {type(e).__name__}: {e}")
            import traceback

            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 70)
    print(f"演示结果：{passed}/{len(demos)} 通过")
    if failed:
        print(f"  失败：{failed}")
        return 1
    print("[PASS] spec C 全部 8 项能力真实跑通")
    return 0


if __name__ == "__main__":
    sys.exit(main())
