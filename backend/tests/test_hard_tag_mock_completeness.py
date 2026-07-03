"""tests.test_hard_tag_mock_completeness —— ADR-0014 决策 2（G-2）配套三件之一。

【这是什么问题】

hard tag（`schemas.tags.DIETARY_HARD_TAGS` / `PHYSICAL_HARD_TAGS`）一旦触发，
`tools._helpers.relax_tag_search` 永不放宽——这意味着如果 mock 数据里某个
hard tag 压根没有（或候选太少）满足它的实体，用户一旦命中这个 tag（比如
"无牛肉"/"轮椅可达"），搜索会一路降级到底仍然打空，最终整条规划管道
"hard 卡死"（give_up 疏导见 `test_give_up_hard_tag_exhaustion_chips.py`）——
这是演示可能踩中的死路，必须提前用真实数字量出来，而不是等评委现场撞见。

【红线：不改 mock 数据】

本测试只**测量**，不修复——`mock_data/` 是本任务的红线（红线清单：不改
mock_data/）。数量不够的 tag 登记进 `_KNOWN_INSUFFICIENT_HARD_TAGS`
白名单（附真实计数 + 理由），与 `test_consumption_completeness.py` 的
"登记 dict[码 -> 理由]，不许静默塞集合"同一纪律——不让这条测试红给
"演示会踩坑"这件事一个说了也白说的下场，而是显式记录、留给报告/后续排期
决定要不要真的补 mock 数据。

【阈值】

每个 hard tag 在其对应实体类型（dietary→restaurants，physical→pois）
里的满足候选数 ≥ 3（`_MIN_CANDIDATES`）——"3"不是随意取的：
`tools._helpers.relax_tag_search` 的 `additional_filter`（距离/排除已访问
等）还会再筛掉一部分，候选池太薄时哪怕 tag 本身满足也可能被其它维度过滤
到空，3 家留出一点缓冲。
"""

from __future__ import annotations

import collections

from data.loader import load_pois, load_restaurants
from schemas.tags import DIETARY_HARD_TAGS, PHYSICAL_HARD_TAGS

_MIN_CANDIDATES = 3

# 读码核实（2026-07-03，ADR-0014 决策 2 · G-2）：以下 hard tag 当前 mock 数据
# 候选数低于阈值——不改 mock 数据（红线），登记为已知缺口，附真实计数，交
# 报告/后续排期定夺是否需要补数据。
_KNOWN_INSUFFICIENT_HARD_TAGS: dict[str, str] = {
    "无障碍": (
        "physical hard tag，mock_data/pois.json 里挂此 tag 的 POI 实测 0 家——"
        "用户一旦要「轮椅可达」类无障碍诉求，search_pois 全程无候选可给，"
        "会直接触发 hard 卡死（give_up 疏导），需要产品侧决定是否补数据或"
        "接受当前 demo 不覆盖该诉求。"
    ),
    "无牛肉": (
        "dietary hard tag，mock_data/restaurants.json 里挂此 tag 的餐厅实测"
        "0 家——用户一旦要「无牛肉」忌口，search_restaurants 全程无候选可给，"
        "同上需要产品侧决定。"
    ),
}


def _tag_candidate_counts(entities, hard_tags: frozenset[str]) -> dict[str, int]:
    counter: dict[str, int] = {t: 0 for t in hard_tags}
    for e in entities:
        tags = set(getattr(e, "tags", None) or [])
        for t in hard_tags & tags:
            counter[t] += 1
    return counter


def test_dietary_hard_tags_mock_completeness():
    """每个 dietary hard tag 在 restaurants.json 里满足候选 ≥3，或登记已知缺口。"""
    restaurants = load_restaurants()
    counts = _tag_candidate_counts(restaurants, DIETARY_HARD_TAGS)

    insufficient = {
        tag: n for tag, n in counts.items() if n < _MIN_CANDIDATES
    }
    unregistered = {
        tag: n
        for tag, n in insufficient.items()
        if tag not in _KNOWN_INSUFFICIENT_HARD_TAGS
    }
    assert not unregistered, (
        f"以下 dietary hard tag 候选数 < {_MIN_CANDIDATES}（防演示死路），"
        f"且未登记进 _KNOWN_INSUFFICIENT_HARD_TAGS：{unregistered}"
        f"（全量计数：{counts}）"
    )


def test_physical_hard_tags_mock_completeness():
    """每个 physical hard tag 在 pois.json 里满足候选 ≥3，或登记已知缺口。"""
    pois = load_pois()
    counts = _tag_candidate_counts(pois, PHYSICAL_HARD_TAGS)

    insufficient = {
        tag: n for tag, n in counts.items() if n < _MIN_CANDIDATES
    }
    unregistered = {
        tag: n
        for tag, n in insufficient.items()
        if tag not in _KNOWN_INSUFFICIENT_HARD_TAGS
    }
    assert not unregistered, (
        f"以下 physical hard tag 候选数 < {_MIN_CANDIDATES}（防演示死路），"
        f"且未登记进 _KNOWN_INSUFFICIENT_HARD_TAGS：{unregistered}"
        f"（全量计数：{counts}）"
    )


def test_known_insufficient_whitelist_is_not_stale():
    """白名单条目若已达标（mock 数据后续被补充）需要移除登记，否则白名单本身在说谎。"""
    restaurants = load_restaurants()
    pois = load_pois()
    dietary_counts = _tag_candidate_counts(restaurants, DIETARY_HARD_TAGS)
    physical_counts = _tag_candidate_counts(pois, PHYSICAL_HARD_TAGS)
    all_counts = {**dietary_counts, **physical_counts}

    stale = {
        tag
        for tag in _KNOWN_INSUFFICIENT_HARD_TAGS
        if all_counts.get(tag, 0) >= _MIN_CANDIDATES
    }
    assert not stale, (
        f"以下 tag 已达到候选数阈值，_KNOWN_INSUFFICIENT_HARD_TAGS 登记已过期，"
        f"应删除对应条目：{stale}"
    )


def test_known_insufficient_whitelist_entries_have_nonempty_reason():
    """白名单每条必须有非空理由。"""
    empty_reason = [
        tag for tag, reason in _KNOWN_INSUFFICIENT_HARD_TAGS.items() if not reason.strip()
    ]
    assert not empty_reason, f"以下白名单 tag 缺少（非空）理由：{empty_reason}"


def test_report_full_hard_tag_candidate_counts(capsys):
    """跑出真实数字（非断言，纯留痕）——任务要求"跑出真实数字"，用一条恒过
    的测试把全量计数打进测试输出，供报告直接引用，不必额外跑脚本。
    """
    restaurants = load_restaurants()
    pois = load_pois()
    dietary_counts = _tag_candidate_counts(restaurants, DIETARY_HARD_TAGS)
    physical_counts = _tag_candidate_counts(pois, PHYSICAL_HARD_TAGS)
    lines = ["[G-2 hard tag mock 完备性] dietary（restaurants.json）："]
    for tag, n in sorted(dietary_counts.items(), key=lambda kv: kv[0]):
        lines.append(f"  {tag}: {n}")
    lines.append("[G-2 hard tag mock 完备性] physical（pois.json）：")
    for tag, n in sorted(physical_counts.items(), key=lambda kv: kv[0]):
        lines.append(f"  {tag}: {n}")
    print("\n".join(lines))
    assert True
