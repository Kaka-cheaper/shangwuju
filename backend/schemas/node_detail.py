"""schemas.node_detail —— 行程卡片节点「真实数据详情」下发载荷。

【这是什么问题】

ADR-0015「事实/计算归确定性代码与数据，绝不让 LLM 编造」在前端 ItineraryCard
的现场体现：此前每个活动节点卡片右侧一大片空白——narrate 送给前端的 payload
（`ActivityNode`）只有 title/note/kind/时间/坐标，不带任何决策详情（评分/
人均/距离/可订/标签）。本模块补的正是这份详情——**只从真实 `Poi`/
`Restaurant` 实体字段派生，绝不由 LLM 生成**，与 `node_actions` 的
`alternatives` 必须「引擎预验证」同一纪律：所有展示值都能反查到某个具体的
真实字段，不存在无中生有的一项。

【和 node_actions / AlternativeOption 的关系——不是另起一套数据源】

`agent.graph.nodes.narrate._build_node_actions` 已经在反查同一批实体
（`data.loader.load_pois()/load_restaurants()`）算 `chips`/`alternatives`
（`agent.planning.planners.node_swap.AlternativeOption`）。`NodeDetail` 的
构造函数（`agent.graph.nodes.narrate._build_node_detail`）复用同一份实体
反查——不另起一份数据访问。两者服务不同消费者：`AlternativeOption` 喂
`feasible_alternatives` 的候选排序逻辑（决策引擎用的原始数值），
`NodeDetail` 只做人读展示（卡片用的精选文案），不参与任何排序/过滤判定。

【字段取舍（information scent，不堆全部原始字段）】

餐厅（restaurant）与 POI（poi）字段集不同，各自映射（派生细节见
`agent.graph.nodes.narrate` 模块内 `_build_node_detail` 及其子函数）：

| 展示位 | restaurant | poi |
|---|---|---|
| 评分 | rating | rating |
| 价钱 | avg_price → "¥N/人" | price_range → "¥min–max"；`None` → "免费"
  （`Poi.price_range` 字段文档原文「None 表示免费」，不是缺失，是真实语义） |
| 距离 | distance_km（原始数值，前端自行格式化） | 同 |
| 可订/余位 | reservation_slots 里离该节点排定 start_time 最近的一个
  available 槽 → "可订HH:MM"；一个可用槽都没有 → "需排队"（绝不冒充
  可订）；该店根本没有预约槽表 → 省略该展示位 | capacity.available_slots
  → "余N"；恰为 0 → "约满"（如实告知，不隐瞒） |
| 标签（0-2 个） | 桌型（capacity 派生：private_room → "有包间"，否则
  6 座/8 座 → "大圆桌"，否则省略）+ 1 个描述性 tag（优先氛围类）
  | 适龄（age_range → "适合X-Y岁"）+ 1 个描述性 tag（优先氛围类） |
| 营业 | opening_hours 派生 "营业至HH:MM"（可选展示位） | 同 |

【诚实红线——ADR-0015 招牌论点的具体落地，非可选项】

- 可订时段只挑真 `available=True` 的槽；一个可用槽都没有 → "需排队"，
  绝不输出一个 `available=False` 的时段冒充可订。
- POI `available_slots == 0` → 如实显示"约满"，不隐瞒、不省略。
- 任何字段确实无法从真实数据推出（不是"取到假值"，是"这项真的没有"）→
  优雅省略该展示位（消费方用 `model_dump(exclude_none=True)`），绝不用
  占位符/编造值填充。

不负责：
- 派生规则本身（在 `agent.graph.nodes.narrate`，本模块只声明下发形状）。
- SSE 组装（`agent.graph._emit_handlers.emit_narrate`，镜像 `node_actions`
  的既有路径：`AGENT_NARRATION` payload 的兄弟字段，"无内容不加字段"）。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class NodeDetail(BaseModel):
    """一个活动节点的真实数据详情——ItineraryCard 决策详情展示用。

    home 节点不产出本模型（无实体可反查，也无展示需要）。
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["poi", "restaurant"] = Field(
        ..., description="ActivityNode.target_kind（本模型不覆盖 home 节点）"
    )
    rating: Optional[float] = Field(
        default=None, description="实体原始评分（0-5），原样透传"
    )
    price_text: Optional[str] = Field(
        default=None,
        description='人均/门票展示文案，如 "¥75/人"（餐厅 avg_price）、'
        '"¥80–120"/"免费"（POI price_range）',
    )
    distance_km: Optional[float] = Field(
        default=None, description="距用户家预估直线距离（km），原始数值透传"
    )
    availability_text: Optional[str] = Field(
        default=None,
        description='可订/余位文案，如 "可订17:30"、"需排队"（餐厅 '
        'reservation_slots）、"余12"/"约满"（POI capacity.available_slots）',
    )
    tags: list[str] = Field(
        default_factory=list,
        description="0-2 个精选标签（餐厅：桌型+描述；POI：适龄+描述），不堆全部原始 tags",
    )
    open_until_text: Optional[str] = Field(
        default=None, description='营业时段派生文案，如 "营业至21:30"；可选展示位'
    )


__all__ = ["NodeDetail"]
