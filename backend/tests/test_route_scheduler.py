"""tests.test_route_scheduler —— ADR-0010 D-2：窗感知调度器。

覆盖 `agent.planning.planners.route_scheduler`（纯函数，无 I/O/LLM）：
1. 单活动宽窗：排在自然到达，无 slack。
2. 餐厅晚饭窗：自然到达早于窗起点 → 排在窗起点（前面的空隙即 slack）。
3. 餐厅 snap：自然到达落在窗内但不在槽网格上 → 向上取整到最近的半点槽。
4. 顺序被窗逼出来：两活动各自窗互斥 → 枚举只留一个可行顺序。
5. 不可行四态：预算塞不下 / 窗全错过 / windows 为空 / 跨午夜 → None。
6. 总时长含回家通勤 ≤ budget（边界值两侧各一次）。
7. 多窗餐厅（午/晚）按自然到达落进可达的那个窗。
8. 顺序选择策略（可行顺序间选总 slack 最小、其次结束最早）——本模块自行拍板
   的业务决策，需要行为测试钉住。
9. try_insert：对「既有集合 + 新活动」重跑调度，等价于对合并后列表整体调度。

`Visit.entity` 字段本测试一律传 `None`——`route_scheduler` 不读 `entity`
（那是下游 D-4 assemble 阶段才用得到的真实字段，如坐标/名字），本文件只测
调度时间轴代数，不重复 D-1（`test_activity_pool.py`）已经覆盖的实体构造。
"""

from __future__ import annotations

from typing import Callable, Optional

import pytest

from agent.planning.planners import route_scheduler as rs
from agent.planning.planners.activity_pool import TimeWindow, Visit


# ============================================================
# 共享 fixture helpers
# ============================================================


def _visit(
    *,
    kind: str = "poi",
    target_id: str = "P1",
    duration_min: int = 60,
    windows: list[TimeWindow],
    category: str = "poi",
) -> Visit:
    return Visit(
        kind=kind,
        target_id=target_id,
        duration_min=duration_min,
        windows=windows,
        base_score=0.5,
        category=category,
        cost=0.0,
        entity=None,  # type: ignore[arg-type]  -- 本模块不读 entity，见文件头注释
    )


def _commute_table(
    table: dict[tuple[str, str], int], *, default: int = 10
) -> Callable[[str, str], int]:
    """由 dict 构造注入用的 commute_fn；未登记的 pair 落 default（同地 0 分钟）。"""

    def fn(from_id: str, to_id: str) -> int:
        if from_id == to_id:
            return 0
        return table.get((from_id, to_id), default)

    return fn


WIDE_WINDOW = [TimeWindow(0, 23 * 60 + 59)]


# ============================================================
# 1. 单活动宽窗
# ============================================================


def test_single_wide_window_visit_starts_at_natural_arrival():
    v = _visit(target_id="P1", duration_min=60, windows=WIDE_WINDOW)
    commute = _commute_table({("home", "P1"): 20, ("P1", "home"): 20})

    sched = rs.schedule_route(
        [v], depart_min=600, budget_min=300, commute_fn=commute
    )

    assert sched is not None
    assert len(sched.scheduled) == 1
    sv = sched.scheduled[0]
    assert sv.start_min == 620  # 10:00 + 20min 通勤，首跳无 buffer
    assert sv.natural_arrival_min == 620
    assert sv.slack_min == 0
    assert sched.return_arrival_min == 620 + 60 + 20  # 活动结束 + 回家通勤


# ============================================================
# 2. 餐厅晚饭窗：自然到达早于窗起点
# ============================================================


def test_restaurant_dinner_window_waits_until_window_start():
    # 自然到达 15:56（depart 15:30 + 通勤 26min），窗 [17:00, 20:00]
    v = _visit(
        kind="restaurant",
        target_id="R1",
        duration_min=90,
        windows=[TimeWindow(17 * 60, 20 * 60)],
        category="粤菜",
    )
    commute = _commute_table({("home", "R1"): 26, ("R1", "home"): 10})

    sched = rs.schedule_route(
        [v], depart_min=15 * 60 + 30, budget_min=600, commute_fn=commute
    )

    assert sched is not None
    sv = sched.scheduled[0]
    assert sv.natural_arrival_min == 15 * 60 + 56
    assert sv.start_min == 17 * 60  # 窗起点，恰好在槽上
    assert sv.slack_min == 17 * 60 - (15 * 60 + 56)  # 前面的空隙是 slack


# ============================================================
# 3. 餐厅 snap：自然到达落在窗内但不在槽网格上
# ============================================================


def test_restaurant_start_snaps_up_to_reservation_grid():
    # 自然到达 17:10（不在 :00/:30 网格上），窗 [17:00, 20:00] 足够宽
    v = _visit(
        kind="restaurant",
        target_id="R1",
        duration_min=60,
        windows=[TimeWindow(17 * 60, 20 * 60)],
        category="粤菜",
    )
    commute = _commute_table({("home", "R1"): 10, ("R1", "home"): 10})

    sched = rs.schedule_route(
        [v], depart_min=17 * 60, budget_min=300, commute_fn=commute
    )

    assert sched is not None
    sv = sched.scheduled[0]
    assert sv.natural_arrival_min == 17 * 60 + 10
    assert sv.start_min == 17 * 60 + 30  # 向上取整到最近槽刻，不是 17:10


def test_poi_start_does_not_snap_to_grid():
    """对照组：POI 不受槽网格约束，自然到达即排定（哪怕不在半点上）。"""
    v = _visit(kind="poi", target_id="P1", duration_min=30, windows=WIDE_WINDOW)
    commute = _commute_table({("home", "P1"): 13, ("P1", "home"): 10})

    sched = rs.schedule_route(
        [v], depart_min=9 * 60, budget_min=300, commute_fn=commute
    )

    assert sched is not None
    assert sched.scheduled[0].start_min == 9 * 60 + 13  # 不 snap


# ============================================================
# 4. 顺序被窗逼出来
# ============================================================


def test_order_forced_by_mutually_exclusive_windows():
    # POI 只在上午到傍晚开（宽窗，8:00-18:00），餐厅只有一个极窄的晚窗（19:00-19:30）。
    # 若餐厅排第一，POI 排第二会因 19:xx 之后 POI 早已打烊而不可行；
    # 唯一可行顺序是 POI 先、餐厅后。
    poi = _visit(
        kind="poi",
        target_id="P1",
        duration_min=90,
        windows=[TimeWindow(8 * 60, 18 * 60)],
    )
    rest = _visit(
        kind="restaurant",
        target_id="R1",
        duration_min=30,
        windows=[TimeWindow(19 * 60, 19 * 60 + 30)],
        category="粤菜",
    )
    commute = _commute_table(
        {
            ("home", "P1"): 10,
            ("home", "R1"): 10,
            ("P1", "R1"): 15,
            ("R1", "P1"): 15,
            ("P1", "home"): 10,
            ("R1", "home"): 10,
        }
    )

    sched = rs.schedule_route(
        [poi, rest], depart_min=8 * 60, budget_min=800, commute_fn=commute
    )

    assert sched is not None
    ordered_ids = [sv.visit.target_id for sv in sched.scheduled]
    assert ordered_ids == ["P1", "R1"]


# ============================================================
# 5. 不可行四态
# ============================================================


def test_infeasible_when_budget_too_small():
    v = _visit(target_id="P1", duration_min=60, windows=WIDE_WINDOW)
    commute = _commute_table({("home", "P1"): 100, ("P1", "home"): 100})

    sched = rs.schedule_route(
        [v], depart_min=600, budget_min=50, commute_fn=commute
    )

    assert sched is None


def test_infeasible_when_window_already_missed():
    # 窗 9:00-10:00，depart 18:00 → 无论怎样都已经过了窗尾
    v = _visit(
        target_id="P1", duration_min=30, windows=[TimeWindow(9 * 60, 10 * 60)]
    )
    commute = _commute_table({("home", "P1"): 10, ("P1", "home"): 10})

    sched = rs.schedule_route(
        [v], depart_min=18 * 60, budget_min=600, commute_fn=commute
    )

    assert sched is None


def test_infeasible_when_windows_empty():
    v = _visit(target_id="P1", duration_min=30, windows=[])
    commute = _commute_table({("home", "P1"): 10, ("P1", "home"): 10})

    sched = rs.schedule_route(
        [v], depart_min=600, budget_min=600, commute_fn=commute
    )

    assert sched is None


def test_infeasible_when_crosses_midnight():
    # depart 23:20，通勤 10min，时长 60min → 结束 00:50，越过 23:59 上限
    v = _visit(
        target_id="P1", duration_min=60, windows=[TimeWindow(0, 23 * 60 + 59)]
    )
    commute = _commute_table({("home", "P1"): 10, ("P1", "home"): 5})

    sched = rs.schedule_route(
        [v], depart_min=23 * 60 + 20, budget_min=600, commute_fn=commute
    )

    assert sched is None


# ============================================================
# 6. 总时长含回家通勤 ≤ budget（边界两侧）
# ============================================================


def test_budget_boundary_includes_return_commute():
    v = _visit(target_id="P1", duration_min=60, windows=WIDE_WINDOW)
    commute = _commute_table({("home", "P1"): 20, ("P1", "home"): 20})
    # 总时长 = 20(去) + 60(活动) + 20(回) = 100 分钟

    ok = rs.schedule_route(
        [v], depart_min=600, budget_min=100, commute_fn=commute
    )
    assert ok is not None
    assert ok.total_minutes == 100

    too_tight = rs.schedule_route(
        [v], depart_min=600, budget_min=99, commute_fn=commute
    )
    assert too_tight is None


# ============================================================
# 7. 多窗餐厅（午/晚）按到达时刻落进可达的那个窗
# ============================================================


def _lunch_dinner_restaurant(duration_min: int = 60) -> Visit:
    # 故意乱序传入（晚窗在前、午窗在后），验证不依赖 windows 的列表顺序
    return _visit(
        kind="restaurant",
        target_id="R1",
        duration_min=duration_min,
        windows=[
            TimeWindow(17 * 60, 19 * 60),  # 晚窗
            TimeWindow(11 * 60, 13 * 60),  # 午窗
        ],
        category="粤菜",
    )


def test_multi_window_restaurant_picks_lunch_when_arriving_at_noon():
    v = _lunch_dinner_restaurant()
    commute = _commute_table({("home", "R1"): 10, ("R1", "home"): 10})

    sched = rs.schedule_route(
        [v], depart_min=11 * 60 + 50, budget_min=600, commute_fn=commute
    )

    assert sched is not None
    sv = sched.scheduled[0]
    assert sv.natural_arrival_min == 12 * 60
    assert sv.start_min == 12 * 60  # 落在午窗内，已在槽上


def test_multi_window_restaurant_picks_dinner_when_arriving_late_afternoon():
    v = _lunch_dinner_restaurant()
    commute = _commute_table({("home", "R1"): 10, ("R1", "home"): 10})

    # 15:30 到达：午窗（11:00-13:00）已错过，只能落进晚窗
    sched = rs.schedule_route(
        [v], depart_min=15 * 60 + 20, budget_min=600, commute_fn=commute
    )

    assert sched is not None
    sv = sched.scheduled[0]
    assert sv.natural_arrival_min == 15 * 60 + 30
    assert sv.start_min == 17 * 60  # 晚窗起点（要等）


# ============================================================
# 8. 顺序选择策略：可行顺序间选总 slack 最小、其次结束最早
# ============================================================


def test_order_selection_prefers_lower_total_slack():
    # A 宽窗（无强制等待）；B 只有 [700,720] 的窄窗。
    # 顺序 [A,B]：A 自然到达即走，B 等待 45 分钟。
    # 顺序 [B,A]：B 等待 90 分钟，A 自然到达即走。
    # 两个顺序都可行，但 [A,B] 总 slack 更低，应被选中。
    a = _visit(kind="poi", target_id="PA", duration_min=30, windows=WIDE_WINDOW)
    b = _visit(
        kind="poi",
        target_id="PB",
        duration_min=20,
        windows=[TimeWindow(700, 720)],
    )
    commute = _commute_table(
        {
            ("home", "PA"): 10,
            ("home", "PB"): 10,
            ("PA", "PB"): 10,
            ("PB", "PA"): 10,
            ("PA", "home"): 10,
            ("PB", "home"): 10,
        }
    )

    sched = rs.schedule_route(
        [a, b], depart_min=600, budget_min=1000, commute_fn=commute
    )

    assert sched is not None
    ordered_ids = [sv.visit.target_id for sv in sched.scheduled]
    assert ordered_ids == ["PA", "PB"]
    assert sched.total_slack_min == 45


def test_order_selection_tiebreaks_on_earliest_finish():
    # 两个活动窗都宽（两种顺序 slack 皆为 0），但 A↔B 通勤不对称，
    # 逼出总时长更短的顺序 [A,B]。
    a = _visit(kind="poi", target_id="PA", duration_min=10, windows=WIDE_WINDOW)
    b = _visit(kind="poi", target_id="PB", duration_min=10, windows=WIDE_WINDOW)
    commute = _commute_table(
        {
            ("home", "PA"): 5,
            ("home", "PB"): 5,
            ("PA", "PB"): 15,
            ("PB", "PA"): 60,
            ("PA", "home"): 5,
            ("PB", "home"): 5,
        }
    )

    sched = rs.schedule_route(
        [a, b], depart_min=600, budget_min=1000, commute_fn=commute
    )

    assert sched is not None
    ordered_ids = [sv.visit.target_id for sv in sched.scheduled]
    assert ordered_ids == ["PA", "PB"]
    assert sched.total_slack_min == 0


# ============================================================
# 9. try_insert：重跑调度
# ============================================================


def test_try_insert_matches_rerunning_schedule_route_on_combined_list():
    a = _visit(kind="poi", target_id="PA", duration_min=30, windows=WIDE_WINDOW)
    b = _visit(kind="poi", target_id="PB", duration_min=20, windows=WIDE_WINDOW)
    commute = _commute_table(
        {
            ("home", "PA"): 10,
            ("home", "PB"): 10,
            ("PA", "PB"): 10,
            ("PB", "PA"): 10,
            ("PA", "home"): 10,
            ("PB", "home"): 10,
        }
    )

    direct = rs.schedule_route(
        [a, b], depart_min=600, budget_min=500, commute_fn=commute
    )
    inserted = rs.try_insert(
        [a], b, depart_min=600, budget_min=500, commute_fn=commute
    )

    assert inserted is not None
    assert direct is not None
    assert inserted.total_minutes == direct.total_minutes
    assert [sv.visit.target_id for sv in inserted.scheduled] == [
        sv.visit.target_id for sv in direct.scheduled
    ]


def test_try_insert_returns_none_when_new_visit_infeasible():
    a = _visit(kind="poi", target_id="PA", duration_min=30, windows=WIDE_WINDOW)
    b_impossible = _visit(kind="poi", target_id="PB", duration_min=30, windows=[])
    commute = _commute_table({}, default=10)

    assert (
        rs.try_insert(
            [a], b_impossible, depart_min=600, budget_min=500, commute_fn=commute
        )
        is None
    )


# ============================================================
# 空集合边界（try_insert 从空集合开始搭建路线时的地基情形）
# ============================================================


def test_schedule_route_empty_visits_is_trivially_feasible():
    commute = _commute_table({})
    sched = rs.schedule_route(
        [], depart_min=600, budget_min=0, commute_fn=commute
    )
    assert sched is not None
    assert sched.scheduled == ()
    assert sched.return_arrival_min == 600
    assert sched.total_minutes == 0
