"""schemas.node_adjustment —— 节点级「定向调整」跨层契约（ADR-0013 决策 4/F-1）。

【这是什么问题】

ADR-0013「节点交互三元素」把「定向调整按钮」（如「微辣的」「安静点的」）定义为
结构化指令，不过 LLM 路由——用户点一下按钮，规划层要能读懂"这一下到底是要
调哪个维度、调成什么样"，然后去已召回候选池里找满足这个方向的替代实体。这条
契约必须跨 F 系列多片共用：F-3（narrate 搭车生成按钮 + 前端消费其形状）产出
`NodeAdjustment`，F-1（`agent.planning.planners.node_swap.resolve_node_swap`）
消费它去挑替代候选，F-2（诉求台账）把同一形状的「诉求载荷」记账——三方对齐
同一份 schema，故放 `schemas/`（跨层契约的唯一权威定义），与 `schemas/pin.py`
（D-7 的 `PinSpec`）同一先例/同一分层理由。

【维度表怎么来的（不发明数据里不存在的标签）】

调研 `schemas/domain.py`（`Poi`/`Restaurant`）与 `schemas/tags.py`（三本受控
词典）后，维度收敛为 mock 数据实际支持的 6 个，每个维度都能落到具体字段上的
一条可判定谓词（候选是否"满足"这个方向）：

| 维度            | 数据字段                              | value 取值                    | 满足谓词（候选 vs 原节点，除 CUISINE_OR_TYPE 外都是"原节点"作参照系的相对比较） |
|-----------------|----------------------------------------|--------------------------------|------|
| PRICE           | `Poi.price_range[0]` / `Restaurant.avg_price` | "cheaper" / "pricier"    | 候选单价 < / > 原节点单价 |
| DISTANCE        | `Poi.distance_km` / `Restaurant.distance_km`  | "closer" / "farther"     | 候选距离 < / > 原节点距离 |
| CUISINE_OR_TYPE | `Poi.type` / `Restaurant.cuisine`（自由文本，无受控词典——与 `activity_pool.poi_category`/`restaurant_category` 同一口径） | 目标类型/菜系原文（如"粤菜"） | 候选该字段 == value（精确匹配，不做模糊/同义） |
| DIETARY         | `Poi.tags` / `Restaurant.tags`         | `DIETARY_TAGS` 词典之一        | value ∈ 候选.tags |
| AMBIENCE        | `Poi.tags` / `Restaurant.tags`         | "安静聊天" / "热闹"（`EXPERIENCE_TAGS` 里表达"安静-热闹"两极的那一对，ADR 原文明确的轴） | value ∈ 候选.tags |
| CROWD_FIT       | `Poi.tags` / `Restaurant.tags`         | `PHYSICAL_TAGS` 词典之一        | value ∈ 候选.tags |

PRICE/DISTANCE 是**方向词**而非具体数值——调整按钮的 UX 是"更便宜的"/"更近的"，
不是"预算改成 200 元"，这与 `IntentExtraction` 没有绝对预算字段、只有
`distance_max_km` 上限的既有口径一致（本 schema 不新造一个"目标价位"概念）。
CUISINE_OR_TYPE 是**目标值**（换成哪个类型/菜系）——`Poi.type`/`Restaurant.
cuisine` 本身是自由文本字段、mock 数据无枚举词典，故不像 DIETARY/AMBIENCE/
CROWD_FIT 那样能在 schema 层做词典校验，只能校验非空。DIETARY/AMBIENCE/
CROWD_FIT 是**目标 tag**——必须属于 `schemas.tags` 对应词典的子集，防止
「发明数据里不存在的标签」（AMBIENCE 收窄到 `EXPERIENCE_TAGS` 里"安静聊天"/
"热闹"这一对，因为 ADR-0013 原文把这一维度点名为「氛围(安静-热闹)」单轴双极，
不是 `EXPERIENCE_TAGS` 全部 13 个标签都算"氛围"）。

【与「诉求台账」（F-2，尚未实现）的关系】

`agent.planning.planners.node_swap.resolve_node_swap` 的 `ledger_slice` 形参
（生效中诉求列表）复用**本模块同一个 `NodeAdjustment` 形状**作为诉求的核心
可满足载荷——F-2 的「谁 · 针对哪个节点 · 全局/局部语义 · 状态」是外层信封，
核心的"要调哪个维度、调成什么样"与按钮点击的载荷是同一件事，没有理由另建一套
平行结构。F-2 落地时负责按记名/节点归属/生效状态过滤出`Sequence[NodeAdjustment]`
切片喂给本模块的消费方；本模块不关心信封字段。

不负责：
- 按钮生成（narrate LLM 搭车 / kind 模板兜底，F-3）。
- 诉求的记账存储与生效状态机（F-2）。
- 候选是否满足的判定实现（在 `agent.planning.planners.node_swap`——本模块
  只声明契约形状 + 词典归属校验，不实现"怎么比较两个实体"的业务逻辑）。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.tags import DIETARY_TAGS, PHYSICAL_TAGS


class NodeAdjustmentDimension(str, Enum):
    """定向调整的 6 个受控维度（见模块 docstring 维度表）。"""

    PRICE = "price"
    DISTANCE = "distance"
    CUISINE_OR_TYPE = "cuisine_or_type"
    DIETARY = "dietary"
    AMBIENCE = "ambience"
    CROWD_FIT = "crowd_fit"


PRICE_DIRECTIONS: frozenset[str] = frozenset({"cheaper", "pricier"})
"""PRICE 维度合法 value：方向词，不是绝对价位（见模块 docstring）。"""

DISTANCE_DIRECTIONS: frozenset[str] = frozenset({"closer", "farther"})
"""DISTANCE 维度合法 value：方向词。"""

AMBIENCE_VALUES: frozenset[str] = frozenset({"安静聊天", "热闹"})
"""AMBIENCE 维度合法 value：`EXPERIENCE_TAGS` 里表达"安静-热闹"两极的那一对
（ADR-0013 原文明确的单轴双极，不是 `EXPERIENCE_TAGS` 全部 13 个标签）。"""


class NodeAdjustment(BaseModel):
    """一条节点级定向调整请求——「换成哪个方向/取值」的最小可执行载荷。

    `dimension` + `value` 两个字段就是全部：不含节点定位（由
    `resolve_node_swap(target_node_id=...)` 单独传入，不重复放进本模型）、
    不含来源/记账信息（那是 F-2 诉求台账信封的活）。`value` 的合法取值随
    `dimension` 变化，`model_validator` 强制校验（见模块 docstring 维度表），
    拦住"发明词典外标签"或"方向词拼错"这类问题在 schema 层就报错，不流到
    `node_swap.py` 的业务逻辑里才发现。
    """

    model_config = ConfigDict(extra="forbid")

    dimension: NodeAdjustmentDimension
    value: str = Field(
        ...,
        min_length=1,
        description="随 dimension 变化：price/distance 是方向词；"
        "dietary/ambience/crowd_fit 是对应受控词典里的一个 tag；"
        "cuisine_or_type 是目标菜系/类型原文（自由文本，无受控词典）",
    )

    @model_validator(mode="after")
    def _check_value_matches_dimension(self) -> "NodeAdjustment":
        dim, value = self.dimension, self.value
        if dim == NodeAdjustmentDimension.PRICE and value not in PRICE_DIRECTIONS:
            raise ValueError(
                f'dimension="price" 的 value 必须是 {sorted(PRICE_DIRECTIONS)} 之一，实际 {value!r}'
            )
        if dim == NodeAdjustmentDimension.DISTANCE and value not in DISTANCE_DIRECTIONS:
            raise ValueError(
                f'dimension="distance" 的 value 必须是 {sorted(DISTANCE_DIRECTIONS)} 之一，实际 {value!r}'
            )
        if dim == NodeAdjustmentDimension.DIETARY and value not in DIETARY_TAGS:
            raise ValueError(
                f'dimension="dietary" 的 value 必须属于 DIETARY_TAGS 词典，实际 {value!r}'
            )
        if dim == NodeAdjustmentDimension.AMBIENCE and value not in AMBIENCE_VALUES:
            raise ValueError(
                f'dimension="ambience" 的 value 必须是 {sorted(AMBIENCE_VALUES)} 之一'
                f"（EXPERIENCE_TAGS 里安静-热闹两极），实际 {value!r}"
            )
        if dim == NodeAdjustmentDimension.CROWD_FIT and value not in PHYSICAL_TAGS:
            raise ValueError(
                f'dimension="crowd_fit" 的 value 必须属于 PHYSICAL_TAGS 词典，实际 {value!r}'
            )
        # CUISINE_OR_TYPE：自由文本，无受控词典，min_length=1 已够（见模块 docstring）
        return self


__all__ = [
    "NodeAdjustment",
    "NodeAdjustmentDimension",
    "PRICE_DIRECTIONS",
    "DISTANCE_DIRECTIONS",
    "AMBIENCE_VALUES",
]
