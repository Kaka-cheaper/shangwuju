"""duration_helpers —— SuggestedDuration / typical_dining_min 投影 helper。

spec planning-quality-deep-review R1+R2 引入。

职责：
- 把 `Poi.suggested_duration_minutes`（int 或 SuggestedDuration dict）
  按 `IntentExtraction.companions` 投影为单值，让下游 `_poi_preview` 不暴露
  dict 结构给 LLM（design.md "不暴露字段名" 原则）。
- 推主导客群桶（multi-gen 取最严，含 ≤6 岁孩 → kid_3_6 优先等）。

不负责：
- 业务规则裁决（critic / planner 各自调用此 helper 后再做决策）。
- 加载 mock 数据（这是 backend/data/loader.py 的职责）。
"""

from __future__ import annotations

from typing import Iterable, Optional, Union

from schemas.domain import SuggestedDuration


def _has_kid_under_age(companions: Iterable[object], max_age: int) -> bool:
    """companions 是否含至少一个 age ≤ max_age 的成员。

    companions 形态由 IntentExtraction.companions 决定（每个含 role / age 等）。
    本 helper 仅消费 .age 属性（int 或 None）。
    """
    for c in companions:
        age = getattr(c, "age", None)
        if isinstance(age, int) and 0 <= age <= max_age:
            return True
    return False


def _has_senior(companions: Iterable[object], min_age: int = 75) -> bool:
    """companions 是否含至少一个 age ≥ min_age 的成员。"""
    for c in companions:
        age = getattr(c, "age", None)
        if isinstance(age, int) and age >= min_age:
            return True
    return False


def _has_multiple_generations(companions: Iterable[object]) -> bool:
    """companions 是否横跨多代际（含孩 + 含老人）。

    定义：同时存在 age ≤ 12 与 age ≥ 60 的成员。
    """
    has_kid = False
    has_old = False
    for c in companions:
        age = getattr(c, "age", None)
        if not isinstance(age, int):
            continue
        if age <= 12:
            has_kid = True
        if age >= 60:
            has_old = True
    return has_kid and has_old


def _pick_dominant_bucket(
    sd: SuggestedDuration, companions: Iterable[object]
) -> Optional[int]:
    """从 SuggestedDuration 按 companions 取主导桶值（None 表示该桶未填）。

    规则（按严格度优先级，含 → 取最严）：
    1) 含 ≤6 岁孩 → kid_3_6（婴幼儿/学龄前是最严约束）
    2) 含 7-12 岁孩 → kid_7_12
    3) 含 ≥75 岁老人 → senior
    4) 多代际（孩+老人）→ multi_gen（取最严）
    5) 其他 → default
    """
    comp_list = list(companions)
    if _has_kid_under_age(comp_list, 6) and sd.kid_3_6 is not None:
        return sd.kid_3_6
    if _has_kid_under_age(comp_list, 12) and sd.kid_7_12 is not None:
        return sd.kid_7_12
    if _has_senior(comp_list) and sd.senior is not None:
        return sd.senior
    if _has_multiple_generations(comp_list) and sd.multi_gen is not None:
        return sd.multi_gen
    return None  # 让调用方降级到 default


def get_duration_for_companions(
    suggested: Union[int, SuggestedDuration, None],
    companions: Iterable[object],
) -> Optional[int]:
    """投影 SuggestedDuration → 单值，按 companions 推主导桶。

    返回 int 或 None。约定：
    - int 旧形态 → 直接返回（向后兼容）
    - dict 新形态 → 按 _pick_dominant_bucket 投影；找不到主导桶时降级到 default
    - None → 返回 None

    Examples:
        >>> sd = SuggestedDuration(default=90, kid_3_6=60)
        >>> from collections import namedtuple
        >>> C = namedtuple('C', 'age role')
        >>> get_duration_for_companions(sd, [C(age=5, role='孩子')])
        60
        >>> get_duration_for_companions(sd, [C(age=30, role='妻子')])
        90
        >>> get_duration_for_companions(120, [C(age=5, role='孩子')])
        120
        >>> get_duration_for_companions(None, []) is None
        True
    """
    if suggested is None:
        return None
    if isinstance(suggested, int):
        return suggested
    if isinstance(suggested, SuggestedDuration):
        bucket_val = _pick_dominant_bucket(suggested, companions)
        if bucket_val is not None:
            return bucket_val
        return suggested.default
    # 兜底（dict 形态由 Pydantic 在 model_validate 时已转 SuggestedDuration）
    return None
