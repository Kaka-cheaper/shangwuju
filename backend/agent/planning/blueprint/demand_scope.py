"""agent.planning.blueprint.demand_scope —— 需求尺度判定（单一消费 / 正餐分类）。

【背景（Bug B）】L1 修好后候选进池是对的，但「一句吃个烧烤」仍会被默认
`duration_hours=[4,6]` 撑成半日多站 / 两顿饭：两个 planner 都把区间当填充目标
（ILS 涌现循环追 lo_min、蓝图 LLM 向 duration 靠拢）。本模块提供两个**纯判定**，
供 Bug B 两分支根治消费：

- `is_single_consumption(intent)`：用户只点了餐饮、没点活动、没明说长时长 →
  蓝图侧 firm prompt + 确定性 trim 收紧到「1 顿 + 至多 1 轻活动」。
- `is_main_meal_cuisine(cuisine)`：区分正餐 vs 茶点（咖啡 / 下午茶 / 烘焙甜品不算
  正餐），供 ILS 侧 B3「用餐节点上限」计数——茶点不占正餐额度，避免把「烧烤 +
  一杯咖啡」误当「两顿饭」砍掉。

【数据源纪律】餐饮品类集从 `load_restaurants()` 的**真实 cuisine** 派生
（`load_restaurants` 已 `@lru_cache`，execute 阶段必已加载，近零成本），不维护
第二份词集、不引重加载——数据驱动、零漂移。判「preferred 是不是餐饮」用与 L1
锚豁免 / L3 / L4 同一把尺子 `restaurant_desire_match`。

不负责：
- 节点组成决策（`node_decider`）；trim 的实际裁剪（在 `graph/nodes/planner.py`）。
"""

from __future__ import annotations

from functools import lru_cache

from data.loader import load_restaurants
from schemas.category_vocab import restaurant_desire_match
from schemas.intent import IntentExtraction


# 茶点集（真实 cuisine 字面值）：这三类不算「正餐」，B3 计数时不占用餐上限。
# `健康轻食` **算正餐**（单人沙拉即一顿，症状里「七彩沙拉」正是被用户当成一顿
# 饭数的——计入正餐，B3 才能挡住「两份正餐」）；如需改判把它挪进本集即可。
TEAHOUSE_CUISINES: frozenset[str] = frozenset({"咖啡", "下午茶", "烘焙甜品"})


def is_main_meal_cuisine(cuisine: str | None) -> bool:
    """cuisine 是否算「正餐」（非茶点）。

    空 cuisine 保守判为正餐（宁可计数、不漏一顿）。
    """
    return (cuisine or "") not in TEAHOUSE_CUISINES


@lru_cache(maxsize=1)
def _dining_cuisines() -> frozenset[str]:
    """全库真实 cuisine 集（去空）。lru_cache 因 mock 稳定；`data.loader.reset_cache`
    会清 `load_restaurants` 的缓存但**不**清本函数——测试若切换 mock 数据集需
    显式 `_dining_cuisines.cache_clear()`（当前测试都用同一 mock，无需）。"""
    return frozenset(r.cuisine for r in load_restaurants() if r.cuisine)


def is_dining_desire(term: str) -> bool:
    """明示诉求词是否是「餐饮品类」（对全库真实 cuisine 用同一把词法尺子判）。

    例：「烧烤」命中 cuisine「烧烤」→ True；「看展」命不中任何 cuisine → False。
    LLM 未归一的口语词（如「撸串」原样留、未映射到「烧烤」）命不中 → False →
    退化为「不判单一消费」（安全：不 trim，只是没收紧），不误判。
    """
    t = (term or "").strip()
    if not t:
        return False
    return any(restaurant_desire_match([t], c) for c in _dining_cuisines())


def is_single_consumption(intent: IntentExtraction) -> bool:
    """用户是否「只点了一个吃的、没点活动、没明说长时长」（Bug B 蓝图侧收紧触发器）。

    全满足才 True：
    1. `preferred_poi_types` 非空，且**每一项都是餐饮品类**（任一是活动锚 →
       False，如「看展」→ 允许多活动局）；
    2. `duration_hours` 出处 != `user_stated`（用户没明确要长局；`field_provenance`
       为 None / 缺失时当「未明说」，允许收紧——trim 只会缩短，安全）。

    刻意**不 gate 同伴**（决策②）：「带娃只说吃个烧烤」也判单一消费。
    """
    prefs = [p for p in (intent.preferred_poi_types or []) if p and p.strip()]
    if not prefs:
        return False
    if not all(is_dining_desire(p) for p in prefs):
        return False
    prov = intent.field_provenance or {}
    if prov.get("duration_hours") == "user_stated":
        return False
    return True
