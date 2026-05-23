"""tests.test_lookup_hop —— 边解析三级降级单测。

覆盖矩阵（design.md / Requirement 4 R4.1-R4.5）：

```
| Test | 触发条件                          | 期望返回                                |
|------|----------------------------------|-----------------------------------------|
| L1   | from_id == to_id                 | (0, "virtual", "in_place")              |
| L2   | routes.json 命中 transport_pref  | (real_min, transport_pref, "real_route")|
| L3   | routes 不命中但双端坐标存在       | (>0, "haversine_estimated", "estimated")|
| L4   | 双端坐标缺失（未知 id）            | (15, transport_pref, "estimated")       |
| L5   | 一致性：同输入 3 次同输出         | 严格相等                                |
| L6   | transport_pref 字段在 routes 为空 | 走 haversine（不静默换交通方式）         |
```

L5 是 design.md 强制约束：assemble 与 critic 共用本函数，对同一 (from, to) 输入
**永远返回相同结果**，否则 critic 会反复挑刺触发死循环。
"""

from __future__ import annotations

import sys
import types

import pytest

# 过渡态桥（删除时机：Wave 5 Task 9 完成后）：
# Task 1 已删除 ItineraryStage，但 agent/__init__.py 仍在 from .planner import ...，
# planner.py 还引用 ItineraryStage —— 整个 agent 包暂时无法 import。
# lookup_hop.py 自身只依赖 schemas + data.loader，不依赖 agent 兄弟模块，
# 此处把 agent 注册为空命名空间包，让 from-import 跳过 __init__.py 副作用。
if "agent" not in sys.modules or not hasattr(sys.modules["agent"], "__path__"):
    import importlib.util as _ilu
    from pathlib import Path as _Path

    _agent_dir = _Path(__file__).resolve().parent.parent / "agent"
    _stub = types.ModuleType("agent")
    _stub.__path__ = [str(_agent_dir)]  # 让 Python 把它当包，子模块仍按文件解析
    sys.modules["agent"] = _stub

from agent import lookup_hop as lookup_hop_mod  # noqa: E402
from agent.lookup_hop import FALLBACK_MIN, lookup_hop  # noqa: E402
from data.loader import load_user_profile  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_lookup_cache():
    """每个测试前后清空模块级 lru_cache，避免上一个测试的 monkeypatch 残留。"""
    lookup_hop_mod.reset_cache()
    yield
    lookup_hop_mod.reset_cache()


@pytest.fixture
def profile():
    """demo_user 的画像，含 home_location lat/lng 与 transport_preference。"""
    return load_user_profile()


# ============================================================
# L1：from == to → in_place
# ============================================================


def test_L1_same_id_returns_in_place(profile):
    """1 级降级：from_id == to_id → (0, "virtual", "in_place")。"""
    minutes, mode, path_type = lookup_hop("P001", "P001", "taxi", profile)
    assert minutes == 0
    assert mode == "virtual"
    assert path_type == "in_place"


def test_L1_home_to_home_returns_in_place(profile):
    """home → home 也是同地复用，必走 1 级。"""
    minutes, mode, path_type = lookup_hop("home", "home", "walking", profile)
    assert (minutes, mode, path_type) == (0, "virtual", "in_place")


# ============================================================
# L2：routes.json 命中
# ============================================================


def test_L2_routes_hit_returns_real_route(profile):
    """2 级降级：routes.json 含 home → P001（taxi 13min）→ 命中 real_route。"""
    minutes, mode, path_type = lookup_hop("home", "P001", "taxi", profile)
    # routes.json 第一条：{"from_location":"home","to_location":"P001","taxi_minutes":13}
    assert minutes == 13
    assert mode == "taxi"
    assert path_type == "real_route"


def test_L2_routes_hit_walking(profile):
    """同一边换 walking，应返 walking_minutes=50。"""
    minutes, mode, path_type = lookup_hop("home", "P001", "walking", profile)
    assert minutes == 50
    assert mode == "walking"
    assert path_type == "real_route"


# ============================================================
# L3：haversine 估算
# ============================================================


def test_L3_haversine_when_routes_miss(profile):
    """3 级降级：routes 没有 P001 → home（仅有正向边），双端坐标存在 → haversine。"""
    minutes, mode, path_type = lookup_hop("P001", "home", "taxi", profile)
    # 必须落在 haversine 分支
    assert mode == "haversine_estimated"
    assert path_type == "estimated"
    # 估算分钟应为正整数（haversine 距离 × 1.3 / 25kmh × 60，最小 1 分钟）
    assert minutes >= 1
    # 直线距离上限：home (30.275, 120.075) ↔ P001 (30.285, 120.083) ≈ 1.3km
    # 路网折算 1.3 × 25kmh ≈ 4 分钟级别，断言 < 30min 防止公式错误
    assert minutes < 30


def test_L3_haversine_walking_slower_than_taxi(profile):
    """同一对坐标，walking 估算分钟 > taxi 估算分钟（速度更慢）。"""
    walk_min, _, _ = lookup_hop("P002", "home", "walking", profile)
    taxi_min, _, _ = lookup_hop("P002", "home", "taxi", profile)
    assert walk_min > taxi_min


# ============================================================
# L4：保守兜底
# ============================================================


def test_L4_fallback_when_unknown_ids(profile):
    """4 级降级：未知 id（GHOST_X / GHOST_Y），routes 不命中 + 坐标解不出 → 保守 15min。"""
    minutes, mode, path_type = lookup_hop("GHOST_X", "GHOST_Y", "taxi", profile)
    assert minutes == FALLBACK_MIN
    assert mode == "taxi"
    assert path_type == "estimated"


def test_L4_fallback_preserves_transport_pref(profile):
    """兜底时 mode 仍按 transport_pref 返回（不强制 taxi）。"""
    _, mode, _ = lookup_hop("GHOST_X", "GHOST_Y", "bus", profile)
    assert mode == "bus"


# ============================================================
# L5：一致性 —— 同输入 N 次同输出（critic + assemble 共用要求）
# ============================================================


@pytest.mark.parametrize(
    "from_id,to_id,pref",
    [
        ("P001", "P001", "taxi"),       # 1 级
        ("home", "P001", "taxi"),       # 2 级
        ("P001", "home", "taxi"),       # 3 级
        ("GHOST_X", "GHOST_Y", "bus"),  # 4 级
    ],
)
def test_L5_consistency_same_input_same_output(profile, from_id, to_id, pref):
    """对同一 (from, to, pref) 输入连调 3 次，3 次结果完全相同。

    这是 design.md 的强制约束：assemble 与 critic 共用本函数，
    若同输入产出不同则 critic 反复挑刺触发死循环。
    """
    r1 = lookup_hop(from_id, to_id, pref, profile)
    r2 = lookup_hop(from_id, to_id, pref, profile)
    r3 = lookup_hop(from_id, to_id, pref, profile)
    assert r1 == r2 == r3, f"非确定性输出！{from_id}→{to_id}/{pref}: {r1} {r2} {r3}"


# ============================================================
# L6：transport_pref 字段在 routes 为空 → 不静默回退
# ============================================================


def test_L6_routes_hit_but_pref_field_none_falls_to_haversine(
    profile, monkeypatch
):
    """边在 routes 命中，但当前 transport_pref 对应字段为 None → 走 haversine。

    避免「routes 有 walking_minutes 但 pref=bus 时静默用 walking」的语义漂移。
    用 monkeypatch 把 `_route_index` 整体替换为返回人造索引的函数，绕过 lru_cache。
    """
    # 构造一条人造边：home → P001 仅有 taxi_minutes，walking/bus 字段为 None
    fake_index = {
        ("home", "P001"): {"walking": None, "taxi": 13, "bus": None},
    }
    # 直接替换 _route_index 函数本身（lookup_hop 内 routes = _route_index() 会调到这个 fake）
    monkeypatch.setattr(lookup_hop_mod, "_route_index", lambda: fake_index)

    # 子断言 1：walking 字段为 None → 应降级到 haversine（home / P001 双端坐标都在）
    minutes_w, mode_w, path_w = lookup_hop("home", "P001", "walking", profile)
    assert mode_w == "haversine_estimated"
    assert path_w == "estimated"
    assert minutes_w >= 1

    # 子断言 2：taxi 字段有值 → 应命中 real_route 13min（同一 fake_index，不需重置缓存）
    minutes_t, mode_t, path_t = lookup_hop("home", "P001", "taxi", profile)
    assert (minutes_t, mode_t, path_t) == (13, "taxi", "real_route")

    # 子断言 3：bus 字段为 None → 也应降级到 haversine
    minutes_b, mode_b, path_b = lookup_hop("home", "P001", "bus", profile)
    assert mode_b == "haversine_estimated"
