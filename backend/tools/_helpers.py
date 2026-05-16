"""tools._helpers —— Tool 内共享的 *只读* 辅助函数。

职责：
- 距离 / tag 求交集 / 路线查找等被多个 Tool 复用的纯函数
- 统一从 data.loader 取 mock，避免每个 Tool 重复 import

不负责：
- 业务过滤策略（每个 Tool 自己定）
- 任何业务决策 / 调用其他 Tool（违反 AGENTS.md §4.1）
"""

from __future__ import annotations

from typing import Iterable

from data.loader import load_routes
from schemas.domain import Route


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
