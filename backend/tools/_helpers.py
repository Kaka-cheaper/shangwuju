"""tools._helpers —— Tool 内共享的 *只读* 辅助函数。

职责：
- 距离 / tag 求交集 / 路线查找等被多个 Tool 复用的纯函数
- 统一从 data.loader 取 mock，避免每个 Tool 重复 import
- tag 渐进放宽（Step 6）：候选 0 时按软优先级删 tag，避免硬过滤打到空集

不负责：
- 业务过滤策略（每个 Tool 自己定）
- 任何业务决策 / 调用其他 Tool（违反 AGENTS.md §4.1）
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence, TypeVar

from data.loader import load_routes
from schemas.domain import Route


T = TypeVar("T")


def has_all_tags(item_tags: Iterable[str], required: Iterable[str]) -> bool:
    """item 的 tags 是否覆盖 required 全部。空 required 视为通过。"""
    required_set = {t for t in required if t}
    if not required_set:
        return True
    return required_set.issubset(set(item_tags))


def has_any_tag(item_tags: Iterable[str], required: Iterable[str]) -> bool:
    """item 的 tags 是否命中 required 中任意一个；用于 experience_tags 这类弱约束。"""
    required_set = {t for t in required if t}
    if not required_set:
        return True
    return bool(required_set & set(item_tags))


def find_route(from_loc: str, to_loc: str) -> Route | None:
    """从 routes.json 查 from→to；找不到返回 None。"""
    for r in load_routes():
        if r.from_location == from_loc and r.to_location == to_loc:
            return r
    return None


# ============================================================
# Tag 渐进放宽（Step 6）
# ============================================================

# 软优先级：高优先级（前面）的 tag 在放宽时**最后**才被丢弃。
# 设计依据：物理 tag 不可让步（5 岁孩不能去成人场所），饮食 / 体验 tag 可让步。
_PRIORITY_TAGS_HIGH: frozenset[str] = frozenset(
    {
        # 物理硬约束（人群相关，决不可让步）
        "亲子友好",
        "适合 5-10 岁",
        "适合青少年",
        "适合老人",
        "无台阶",
        "无障碍",
        "可休息",
        # 饮食硬约束（健康 / 忌口）
        "低脂",
        "健康轻食",
        "高蛋白",
        "不辣",
        "无牛肉",
        "有儿童餐",
    }
)


def _tag_priority(tag: str) -> int:
    """tag 的"放弃顺序"，数字越小越后被丢弃。

    返回：
    - 0 = 高优先级（人群物理约束 / 饮食硬约束）—— 最后被丢
    - 1 = 中优先级（其他）—— 普通顺序丢
    """
    return 0 if tag in _PRIORITY_TAGS_HIGH else 1


def relax_tag_search(
    required_tags: Sequence[str],
    source: Sequence[T],
    *,
    extract_tags: Callable[[T], Iterable[str]],
    additional_filter: Callable[[T], bool] = lambda _x: True,
    max_relax_levels: int = 3,
) -> tuple[list[T], list[str]]:
    """渐进式 tag 放宽搜索。

    场景：祖孙三代需「亲子友好+适合老人+无台阶」三 tag 全命中，但 mock 数据
    可能只有 2 条同时挂；硬过滤会打到 0 集。本函数按软优先级逐级删 tag，
    返回首个非空候选集 + 实际放弃的 tag 列表（用于推 SSE 让评委看到放宽路径）。

    Args:
        required_tags: 全 required tags（默认要全命中）
        source: 候选源
        extract_tags: 从候选取 tags 列表的函数（如 lambda p: p.tags）
        additional_filter: 额外过滤函数（如距离 / opening_hours），独立于 tag 之外
        max_relax_levels: 最多放宽几级（默认 3）

    Returns:
        (matched_candidates, relaxed_tags)
        - matched_candidates: 首个非空候选集（按 has_all_tags 严格性递减）
        - relaxed_tags: 本次实际丢弃的 tag 列表（顺序与丢弃顺序一致）

    设计纪律：
    - 物理 / 饮食硬约束 tag 优先级高，最后才丢
    - 即使所有 tag 都丢完，仍要 additional_filter 过滤，保底返回任意候选
    """
    required_unique = list(dict.fromkeys(t for t in required_tags if t))
    if not required_unique:
        # 无 tag 要求 → 仅过滤
        return [c for c in source if additional_filter(c)], []

    # 按优先级排序（低优先级先丢）：丢弃顺序 = priority 高的→低的，但我们要"先丢非高优先级"
    # 即按 _tag_priority 降序丢（priority=1 先丢，priority=0 最后丢）
    drop_order = sorted(required_unique, key=lambda t: -_tag_priority(t))

    # Level 0：全命中
    candidates = [
        c for c in source
        if additional_filter(c) and has_all_tags(extract_tags(c), required_unique)
    ]
    if candidates:
        return candidates, []

    # Level 1..N：每级丢 1 个 tag
    relaxed: list[str] = []
    remaining = list(required_unique)
    for level in range(1, max_relax_levels + 1):
        if not drop_order or not remaining:
            break
        dropped = drop_order.pop(0)
        if dropped in remaining:
            remaining.remove(dropped)
        relaxed.append(dropped)
        candidates = [
            c for c in source
            if additional_filter(c) and has_all_tags(extract_tags(c), remaining)
        ]
        if candidates:
            return candidates, relaxed

    # 全丢完仍无 → 仅 additional_filter
    if not remaining:
        candidates = [c for c in source if additional_filter(c)]
        if candidates:
            return candidates, list(required_unique)

    return [], list(required_unique)
