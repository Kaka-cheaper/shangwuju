"""schemas.pin —— 用户「点名必去」的结构化跨层契约（ADR-0010 决策 11 / D-7）。

【这是什么问题】

ADR-0010「锚定谱：pinned → soft-anchored → emergent」（决策 3）把「用户明说的」
（"去 XX 馆" / "6 点吃饭"）定义为硬钉 pinned——必进方案、窗收窄到指定值。
D-4/D-5 已经把消费端建好（`route_builder.build_route(pinned=...)` 接受
`Sequence[Visit]`；`ils_planner.plan_hybrid` 本步新增同名参数）。本模块补的是
**生产端契约**：一个 pin 长什么样，好让「未来 intent 层从用户话里抽出 pins」
与「今天 planner 接受 pins」两头能对上同一个形状，不必等 intent 层落地才能开始
写 planner 侧的接受逻辑（ADR-0010 D-7 范围声明：本步只做 planner 接受 + advisory
产出，intent 层的 pin 抽取——schema+解析 prompt——是跨层依赖，单独立项）。

【为什么是这个形状（kind + target_id，不含时间）】

- `kind` + `target_id`：与 `Visit.kind`/`Visit.target_id`（`activity_pool.py`）、
  `BlueprintNode.target_kind`/`target_id`同一套「实体引用」词汇，resolve 时直接
  按 target_id 去查已召回的 `Poi`/`Restaurant`列表（`ils_planner._resolve_pinned`），
  不新造一套 id 体系。
- **不含时间字段**（如「6 点吃饭」的时间钉）：调研 `Visit`/`try_insert`/
  `build_poi_time_windows`/`build_restaurant_time_windows` 后确认——这些函数已有
  `pin: Optional[TimeWindow]` 形参（收窄候选窗，见 `activity_pool.py` 判断点 5），
  但**没有**把「时间点名」从 `IntentExtraction` 解析出来的机制（`IntentExtraction`
  本身无候选时间字段）。加时间字段进 `PinSpec` 而没有生产方填它，只会是个永远
  是 None 的装饰性字段——本模块选择诚实地不加，留给「intent 层 pin 抽取」立项
  时一并设计（时间钉与实体钉可能是一个字段还是两个字段，那时候一并决定），
  现在加了以后大概率要改形状，不如现在不猜。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PinSpec(BaseModel):
    """一条「点名必去」——用户明确要求必须进方案的一个实体引用。

    `kind`："poi" 或 "restaurant"，与 `Visit.kind`/`BlueprintTargetKind` 同一词汇。
    `target_id`：对应 `Poi.id` / `Restaurant.id`。

    resolve 语义（`ils_planner._resolve_pinned`）：按 `target_id` 在**已召回**的
    候选列表里查找；查不到 → `AdvisoryCode.NO_MATCHING_CANDIDATES`（`schemas.
    advisory`）；查到但排不进最终路线 → `AdvisoryCode.PINNED_UNSATISFIABLE`。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["poi", "restaurant"]
    target_id: str = Field(..., min_length=1, description="对应 Poi.id / Restaurant.id")


__all__ = ["PinSpec"]
