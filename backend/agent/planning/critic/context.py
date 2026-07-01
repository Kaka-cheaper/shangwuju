"""CriticContext —— 一次性载入并持有 13 个 check 所需的数据（ADR-0008 决策 5）。

【为什么需要它】

重构前每个 check 各自 `safe_load_*` 重复加载 mock（6 个 check 各建一份
`{r.id: r for r in safe_load_restaurants()}`）。CriticContext 把数据加载收口一次，
各 check 从 ctx 读，消除重复加载——**这是 Phase A 唯一有意的可观察变化**
（同样的数据、更少的加载；不改变任何违规输出）。

【两个数据源严格分开（ADR-0008 红队修订 G5）】

- **全量 mock**（`pois` / `restaurants`，经 `safe_load_*`）：距离 / 饮食 / 容量 /
  社交 / 餐时 / 满座等查询的真值来源。
- **`tool_results` 搜索快照**（`{"pois": [...], "restaurants": [...]}`）：**仅**反幻觉
  `check_tool_consistency` 使用，判 itinerary 里的 target_id 是否在「工具这次实际
  返回的候选」里。它与全量 mock 是**不同语义**——全量 mock 是「世界上存在哪些点」，
  快照是「这次搜索召回了哪些点」。混用会让幻觉检查失效。

ILS 路径无搜索快照 → `tool_results=None` → 反幻觉在该路径为 no-op（设计如此）。

【Phase A 边界】

本类只搬运数据，不含任何判定逻辑；CRITICAL/WARNING 语义、check 顺序、短路与否
全部不变（仍由 critics_v2 的 flat collect-all 决定）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from schemas.intent import IntentExtraction

from ._rules.helpers import (
    safe_load_pois,
    safe_load_restaurants,
    safe_load_user_profile,
)


@dataclass
class CriticContext:
    """承载一次校验所需的全部外部数据。

    字段：
    - `intent`：用户意图（duration / distance / dietary / capacity / social 等约束）
    - `profile`：UserProfile（home_location / transport_preference）；加载失败为 None
    - `pois` / `restaurants`：**全量 mock**（safe_load_* 结果，加载失败为空 list）
    - `tool_results`：**搜索快照** dict（仅反幻觉用）；None 时反幻觉跳过

    `pois_by_id` / `restaurants_by_id` 是从全量 mock 懒构建并缓存的 id→对象视图，
    与各 check 重构前自建的 `{x.id: x for x in safe_load_*()}` 逐字节等价。
    """

    intent: Optional[IntentExtraction] = None
    profile: Any = None
    # 全量 mock —— 与 tool_results 快照是不同数据源，勿混用
    pois: list = field(default_factory=list)
    restaurants: list = field(default_factory=list)
    # 搜索快照 —— 仅 check_tool_consistency 使用
    tool_results: Optional[dict] = None

    # 懒构建缓存（不参与构造 / repr）
    _pois_by_id: Optional[dict] = field(default=None, init=False, repr=False, compare=False)
    _restaurants_by_id: Optional[dict] = field(
        default=None, init=False, repr=False, compare=False
    )

    @classmethod
    def build(
        cls,
        intent: Optional[IntentExtraction],
        *,
        user_id: str = "demo_user",
        tool_results: Optional[dict] = None,
    ) -> "CriticContext":
        """从 validate_itinerary 的入参一次性载入全量 mock + profile + 快照。

        与旧 validate_itinerary 的加载路径等价：
        - profile = safe_load_user_profile(user_id)（旧代码每次也只加载一次）
        - pois / restaurants = safe_load_*()（旧代码每个 check 各加载一次，现收口一次）
        - tool_results 原样持有（不复制、不改形状）
        """
        return cls(
            intent=intent,
            profile=safe_load_user_profile(user_id),
            pois=safe_load_pois(),
            restaurants=safe_load_restaurants(),
            tool_results=tool_results,
        )

    @property
    def pois_by_id(self) -> dict:
        """全量 mock POI 的 id→对象视图（距离 / 社交 check 用）。"""
        if self._pois_by_id is None:
            self._pois_by_id = {p.id: p for p in self.pois}
        return self._pois_by_id

    @property
    def restaurants_by_id(self) -> dict:
        """全量 mock 餐厅的 id→对象视图（距离 / 饮食 / 容量 / 社交 / 餐时 / 满座 check 用）。"""
        if self._restaurants_by_id is None:
            self._restaurants_by_id = {r.id: r for r in self.restaurants}
        return self._restaurants_by_id
