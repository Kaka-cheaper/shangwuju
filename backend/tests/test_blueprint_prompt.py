"""tests.test_blueprint_prompt —— blueprint_prompt 重写后回归测试（edge_v1）。

验证 design.md §LLM Prompt 重写要点：
1. BLUEPRINT_SYSTEM_PROMPT 长度 ≤ 2200 字符（hard cap，spec R3 提到 2200）
2. 不含旧概念（commute_matrix / buffer 5min / 下一段 start_time 公式 / 5 段模板）
3. 含 edge_v1 关键约束（不要输出 start_time / target_id 必须在候选预览存在 /
   不要输出 hop / nodes / preferred_start_time）
4. 强调灵活性：单段 / 反序 / 同地复用都允许
5. build_user_message 输出包含 intent / candidates / critic_feedback（如果传了）
6. spec planning-quality-deep-review R3：含按 companion age 分级时长表 + 候选预览消费规则

【sys.modules 桥接】
agent/__init__.py 当前仍 eager-import 旧 ItineraryStage（Wave 5 Task 9 修），
所以 import agent.prompts 走包路径会炸。这里复用 Task 3/4/5/7 的 stub 套路：
把 agent / agent.prompts 注册为空命名空间包，让 from-import 直接命中模块文件。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest


# ---- 过渡态桥（删除时机：Wave 5 Task 9 完成后）----
def _install_agent_stub() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    agent_dir = backend_root / "agent"
    prompts_dir = agent_dir / "prompts"

    if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
        stub = types.ModuleType("agent")
        stub.__path__ = [str(agent_dir)]
        sys.modules["agent"] = stub

    if (
        "agent.prompts" not in sys.modules
        or not hasattr(sys.modules["agent.prompts"], "__path__")
    ):
        prompts_stub = types.ModuleType("agent.prompts")
        prompts_stub.__path__ = [str(prompts_dir)]
        sys.modules["agent.prompts"] = prompts_stub


_install_agent_stub()

from agent.prompts.blueprint_prompt import (  # noqa: E402
    BLUEPRINT_SYSTEM_PROMPT,
    build_user_message,
)


# ---- Test 1：长度 hard cap ----------------------------------


def test_system_prompt_length_under_hard_cap() -> None:
    """渲染后 prompt 总字符数 ≤ 2200（spec planning-quality-deep-review R3 提升 cap）。

    历史：1500 → 2200（spec R3 加按 age 分级表 + 候选预览消费规则后必要扩容）。
    旧版 ~3500 → 新版 < 2200 仍是巨大压缩，仅放宽必要业务规则空间。
    """
    length = len(BLUEPRINT_SYSTEM_PROMPT)
    assert length <= 2200, (
        f"BLUEPRINT_SYSTEM_PROMPT 长度 {length} 超过 hard cap 2200，"
        f"需精简（spec R3 cap 2200，进一步放宽需修订 spec）"
    )


# ---- Test 1.1：spec R3 关键词覆盖 ----------------------------


@pytest.mark.parametrize(
    "keyword",
    [
        "suggested_duration",  # 候选预览消费规则
        "typical_dining",  # 餐厅时长字段
        "5 岁",  # 学龄前样例（与范例 75min 配对）
        "75min",  # 学龄前 cap
        "学龄前",  # age tier 桶
        "建议范围",  # critic_feedback 收敛措辞
    ],
)
def test_system_prompt_contains_spec_r3_keywords(keyword: str) -> None:
    """spec R3 要求 prompt 含按 age 分级时长表 + 候选预览消费规则关键词。"""
    assert keyword in BLUEPRINT_SYSTEM_PROMPT, (
        f"prompt 缺少 spec R3 关键词 '{keyword}'（违反 spec R3 验收 §5）"
    )


def test_system_prompt_example_uses_short_duration() -> None:
    """范例 JSON 必须是 75min（学龄前合规），不能是历史 165min（5 岁娃 2.5h 反例锚定）。"""
    assert "duration_min\": 165" not in BLUEPRINT_SYSTEM_PROMPT
    assert "duration_min\": 75" in BLUEPRINT_SYSTEM_PROMPT


# ---- Test 2：旧概念已删除 ----------------------------------


@pytest.mark.parametrize(
    "forbidden",
    [
        "commute_matrix",  # 旧 prompt 让 LLM 查表代入算 start_time
        "下一段",  # 旧公式「下一段.start_time = 上一段.end_time + ...」
        "查表代入",  # 旧 prompt 措辞
        "5min 缓冲",  # 旧 prompt 把 5min buffer 暴露给 LLM
        "5 分钟缓冲",
        "5 分钟 buffer",
        "段间通勤",  # 旧 critic 命名暴露给 LLM
    ],
)
def test_system_prompt_no_legacy_concepts(forbidden: str) -> None:
    """edge_v1 prompt 不应再提及通勤算式 / buffer 数值 / 旧字段。"""
    assert forbidden not in BLUEPRINT_SYSTEM_PROMPT, (
        f"prompt 仍包含旧概念关键词 '{forbidden}'，违反 edge_v1 设计"
    )


def test_system_prompt_no_hardcoded_segment_count() -> None:
    """prompt 不应硬编码「5 段 / 6 段」之类段数模板（节点数由场景决定）。"""
    # 「5 段」「6 段」这种硬编码模板被 design.md §灵活性指南明令禁止
    for forbidden in ["5 段", "6 段", "五段", "六段"]:
        # 允许「不要硬凑 5 段 / 6 段模板」这种**反向告诫**性表述
        # 通过检查上下文：周围若有「不要」「禁」「硬凑」则放过
        idx = 0
        while True:
            idx = BLUEPRINT_SYSTEM_PROMPT.find(forbidden, idx)
            if idx == -1:
                break
            # 取前后 20 字看是否在反向告诫语境
            window = BLUEPRINT_SYSTEM_PROMPT[max(0, idx - 20) : idx + len(forbidden) + 20]
            assert any(neg in window for neg in ["不要", "禁", "硬凑", "别"]), (
                f"prompt 含硬编码段数 '{forbidden}'（上下文：'{window}'）"
            )
            idx += len(forbidden)


# ---- Test 3：edge_v1 关键约束齐全 -----------------------------


@pytest.mark.parametrize(
    "required",
    [
        "nodes",  # 输出顶层字段
        "preferred_start_time",  # 输出顶层字段
        "rationale",  # 输出顶层字段
        "duration_min",  # 节点字段
        "target_id",  # 节点字段
        "target_kind",  # 节点字段
        "候选预览",  # 必须强调 target_id 取自候选预览（中文措辞）
        "不要输出 home",  # 显式禁 home 节点
        "不要输出 hop",  # 显式禁 hop
        "不要输出 start_time",  # 显式禁 start_time / end_time
        "不要输出 stages",  # 显式禁旧字段
        "opening_hours",  # 营业时间约束
    ],
)
def test_system_prompt_contains_required_constraints(required: str) -> None:
    """edge_v1 prompt 必须显式约束这些关键词，让解析层有理由触发 BlueprintGenError。"""
    assert required in BLUEPRINT_SYSTEM_PROMPT, (
        f"prompt 缺少必要约束关键词 '{required}'（违反 design.md §Components.Component 3）"
    )


def test_system_prompt_target_id_must_exist_in_candidates() -> None:
    """prompt 必须强调 target_id 在候选预览里存在的硬性约束。"""
    text = BLUEPRINT_SYSTEM_PROMPT
    assert "target_id" in text and "候选预览" in text, "缺少候选预览引用"
    # 二者应在同一段（约束语境）里出现
    assert (
        "target_id 必须在候选预览里存在" in text
        or ("target_id" in text and "候选预览里存在" in text)
    ), "prompt 应明确「target_id 必须在候选预览里存在」"


# ---- Test 4：灵活性条款齐全 ---------------------------------


@pytest.mark.parametrize(
    "flexibility",
    [
        "单段允许",
        "反序允许",
        "同地复用允许",
    ],
)
def test_system_prompt_flexibility_clauses(flexibility: str) -> None:
    """edge_v1 强调单段 / 反序 / 同地复用都允许（解开旧 5 段模板的束缚）。"""
    assert flexibility in BLUEPRINT_SYSTEM_PROMPT, (
        f"prompt 缺少灵活性条款 '{flexibility}'"
    )


def test_system_prompt_allows_24h_and_late_night() -> None:
    """prompt 应允许 24h / 夜宵 / 早茶等非下午时段（不被「下午局」名字绑架）。"""
    text = BLUEPRINT_SYSTEM_PROMPT
    assert "24h" in text or "24 小时" in text, "应允许 24h 餐厅"
    assert "夜宵" in text or "晚场" in text, "应允许夜宵 / 晚场"


# ---- Test 5：build_user_message 行为 -------------------------


def test_build_user_message_includes_intent_and_candidates() -> None:
    intent_json = '{"raw_input":"今天下午想看展再吃饭","duration_hours":[3,4]}'
    candidates_json = '{"pois":[{"id":"P040"}],"restaurants":[{"id":"R024"}]}'
    msg = build_user_message(intent_json, candidates_json)

    assert "IntentExtraction" in msg
    assert intent_json in msg
    assert "候选预览" in msg
    assert candidates_json in msg
    # 没传 critic_feedback 时不应出现违规段
    assert "上次蓝图违规" not in msg


def test_build_user_message_injects_critic_feedback() -> None:
    intent_json = '{"raw_input":"x"}'
    candidates_json = '{"pois":[]}'
    feedback = [
        "hop[0] minutes=5 < 实际可达 13 分钟",
        "总时长 280 超出用户期望 [180,240]",
    ]
    msg = build_user_message(intent_json, candidates_json, critic_feedback=feedback)

    assert "上次蓝图违规" in msg
    assert "你必须规避" in msg
    for fb in feedback:
        assert fb in msg, f"critic_feedback '{fb}' 未注入 user message"


def test_build_user_message_empty_feedback_treated_as_no_feedback() -> None:
    msg = build_user_message("{}", "{}", critic_feedback=[])
    assert "上次蓝图违规" not in msg


def test_build_user_message_tail_instructs_strict_json() -> None:
    """user message 末尾应提醒只输出三字段（防 LLM 多输出 stages 漂移）。"""
    msg = build_user_message("{}", "{}")
    assert "nodes" in msg
    assert "preferred_start_time" in msg
    assert "rationale" in msg


# ---- Test 6：导出符号契约 ------------------------------------


def test_module_exports_contract() -> None:
    """blueprint_prompt 至少导出 BLUEPRINT_SYSTEM_PROMPT 与 build_user_message。"""
    import agent.prompts.blueprint_prompt as mod

    assert hasattr(mod, "BLUEPRINT_SYSTEM_PROMPT")
    assert hasattr(mod, "build_user_message")
    assert isinstance(mod.BLUEPRINT_SYSTEM_PROMPT, str)
    assert callable(mod.build_user_message)
