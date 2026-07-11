"""tests.test_explicit_dining_tristate_schema —— tristate 字段惰性提交特征化（C4）。

【钉住的红线】`IntentExtraction.explicit_dining_requested: Optional[bool] = None`
落 schema 的这个 commit 是**安全熔断点**：零消费者、行为零变化——
「None = 现状逐字节一致」是验收红线。本文件的特征化断言在消费者接入
（C5a）之后必须**继续全绿**：None 态走既有推断触发，现状行为分毫不变；
C5a 只对 True/False 两个新态开新分支。

【测试矩阵】

```
| Test | 场景                             | 验证重点                          |
|------|----------------------------------|-----------------------------------|
| T1   | 缺省构造                         | 默认 None                         |
| T2   | 旧形状 JSON（无该键）反序列化     | 兼容且值为 None（旧 checkpoint 免迁移）|
| T3   | _build_fallback_intent           | None（解析失败无从声称显式意愿，锁死为有意选择）|
| T4   | None 态 dining_soft_anchored 特征化 | 商务恒锚 / 无 dietary 不锚 / 跨窗+dietary 锚——现状三行为 |
| T5   | 三态都能构造与序列化              | True/False/None 往返无损           |
```
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    _agent_dir = Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]
    sys.modules["agent"] = _stub

from agent.graph.nodes.intent import _build_fallback_intent  # noqa: E402
from agent.planning.planners.route_builder import dining_soft_anchored  # noqa: E402
from schemas.intent import Companion, IntentExtraction  # noqa: E402


def _base_kwargs(**overrides) -> dict:
    kw = dict(
        start_time="today_afternoon",
        duration_hours=[3, 5],
        distance_max_km=5.0,
        companions=[Companion(role="自己", count=1)],
        physical_constraints=[],
        dietary_constraints=[],
        experience_tags=[],
        social_context="家庭日常",
        raw_input="下午出去转转",
        parse_confidence=0.9,
    )
    kw.update(overrides)
    return kw


# ============================================================
# T1/T2：默认值与旧数据兼容
# ============================================================


def test_T1_default_is_none():
    intent = IntentExtraction(**_base_kwargs())
    assert intent.explicit_dining_requested is None


def test_T2_old_shape_json_without_key_deserializes_to_none():
    """旧 checkpoint / 旧 LLM 输出（无该键）必须原样可用——Optional 默认 None
    的免迁移承诺。"""
    old_shape = {
        "start_time": "today_afternoon",
        "duration_hours": [3, 5],
        "distance_max_km": 5.0,
        "companions": [],
        "physical_constraints": [],
        "dietary_constraints": [],
        "experience_tags": [],
        "social_context": "家庭日常",
        "raw_input": "老数据",
        "parse_confidence": 0.8,
    }
    intent = IntentExtraction.model_validate(old_shape)
    assert intent.explicit_dining_requested is None


def test_T3_fallback_intent_field_is_none():
    """解析彻底失败的兜底意图：无从声称用户明说过要/不要吃饭 → None
    （走推断触发，现状行为）。这是有意选择不是疏漏——若为 False 会错误
    抑制商务/跨窗推断触发，若为 True 会硬塞一顿饭。"""
    intent = _build_fallback_intent("乱码输入")
    assert intent.explicit_dining_requested is None


# ============================================================
# T4：None 态 = 现状行为特征化（C5a 接消费者后必须继续全绿）
# ============================================================


def test_T4a_none_business_context_still_soft_anchors():
    """现状：商务接待场景无条件软锚饭（1.28 记录的既存行为——False 态在
    C5a 会修复「明说不用排饭仍硬塞商务餐」的缺陷，None 态保持现状）。"""
    intent = IntentExtraction(
        **_base_kwargs(social_context="商务接待", raw_input="接待客户")
    )
    assert intent.explicit_dining_requested is None
    assert dining_soft_anchored(intent) is True


def test_T4b_none_family_no_dietary_not_anchored():
    """现状：普通家庭场景、无 dietary 信号 → 不软锚（饭走涌现）。"""
    intent = IntentExtraction(**_base_kwargs())
    assert dining_soft_anchored(intent) is False


def test_T4c_none_dietary_plus_dinner_window_anchored():
    """现状：14:00 出发 + 5h 上限（窗至 19:00 踩进晚饭窗）+ dietary 非空
    → 软锚（ADR-0010 决策 10 放宽后规则②）。"""
    intent = IntentExtraction(
        **_base_kwargs(
            start_time="2026-07-11T14:00",
            duration_hours=[3, 5],
            dietary_constraints=["不辣"],
            raw_input="下午带老人出门，不吃辣",
        )
    )
    assert dining_soft_anchored(intent, depart_min=14 * 60) is True


# ============================================================
# T5：三态构造与序列化往返
# ============================================================


def test_T5_all_three_states_roundtrip():
    for value in (None, True, False):
        intent = IntentExtraction(
            **_base_kwargs(), explicit_dining_requested=value
        )
        assert intent.explicit_dining_requested is value
        dumped = intent.model_dump()
        assert dumped["explicit_dining_requested"] is value
        restored = IntentExtraction.model_validate(dumped)
        assert restored.explicit_dining_requested is value
