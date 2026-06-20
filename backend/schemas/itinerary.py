"""itinerary —— 最终方案输出（edge_v1 模型）。

`Itinerary` 是 Agent 给前端的最终交付物，对应行程卡片的渲染数据。

【为什么是 ActivityNode + Hop 二元组？】

历史 v0（schema_version="legacy_v0"）用 `ItineraryStage` 把「在某地停留」与
「通勤过程」糅合成一个段，导致 LLM、critic、前端三方各自解读：
- LLM 把「转场」当独立活动随意延长
- critic 在「主活动→用餐」之间反复增删通勤段，触发死循环
- 前端时间轴渲染不一致

edge_v1 改为业内（Google Trips / Apple Maps / 携程）通用建模：

```
nodes:  [home] → [POI/Restaurant 1] → [POI/Restaurant 2] → ... → [home]
hops:        ↘ hop_0 ↗            ↘ hop_1 ↗               ↘ hop_n-1 ↗
```

- `ActivityNode`：在某地停留，含 duration_min；首尾固定 home（target_kind="home"，duration=0）
- `Hop`：相邻两节点之间的通勤过程（minutes / mode / path_type）
- `ScheduleEntry`：派生只读视图，把 nodes + hops 按时间序展平，方便前端时间轴

不变量（model_validator 强制）：

1. len(hops) == len(nodes) - 1
2. nodes[0].target_kind == "home"
3. nodes[-1].target_kind == "home"
4. nodes[0].duration_min == 0 且 nodes[-1].duration_min == 0
5. home 节点 target_id 固定为 "home"

不负责：
- 文案生成（在 generate_share_message Tool）
- UI 渲染（在前端组件）
- ScheduleEntry 的派生计算（在 agent/itinerary_builder.py 之类的下游模块；
  本 schema 仅声明结构，由生产者填充）
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, model_validator

from schemas.decision_trace import DecisionTrace

# ============================================================
# 类型别名
# ============================================================

NodeTargetKind = Literal["poi", "restaurant", "home"]
"""节点目标类型：POI / 餐厅 / 家。home 仅用于首尾节点。"""

HopMode = Literal["walking", "taxi", "bus", "haversine_estimated", "virtual"]
"""通勤方式。
- walking / taxi / bus：实际出行方式
- haversine_estimated：路网未命中时按直线距离 + 速度估算
- virtual：同地（minutes=0）或 home 起讫的占位 hop
"""

HopPathType = Literal["real_route", "estimated", "in_place"]
"""通勤路径类型。
- real_route：命中 Mock 路网（estimate_route_time 返回真实分钟）
- estimated：haversine 兜底估算
- in_place：同地（from_node_id 与 to_node_id 指向同一坐标 / 同一 POI）
"""


# ============================================================
# 节点 / 通勤
# ============================================================


class ActivityNode(BaseModel):
    """行程节点：在某地停留。

    一个行程由 N 个节点串联，首尾必为 home（虚拟节点，duration=0）；
    中间节点 target_kind ∈ {poi, restaurant}。
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(..., description='节点稳定 id，如 "n_0" / "n_home_start"')
    kind: str = Field(
        ...,
        description='节点性质中文标签：主活动 / 用餐 / 夜宵 / 自由 等（前端渲染图标用，不参与逻辑分支）',
    )
    target_kind: NodeTargetKind = Field(
        ..., description="节点目标类型：poi / restaurant / home"
    )
    target_id: str = Field(
        ...,
        description='POI/Restaurant id；target_kind="home" 时固定为 "home"',
    )
    start_time: str = Field(..., description='抵达 / 进入该节点的时刻，形如 "14:15"')
    duration_min: NonNegativeInt = Field(
        ...,
        description="在该节点停留时长（分钟，不含来去通勤）；home 节点固定 0",
    )
    title: str = Field(..., description="一行说明，如「亲子游玩 · 森林儿童探索乐园」")
    note: Optional[str] = Field(
        default=None, description='附加说明，如"已为你预约 17:00 三人位"'
    )
    lat: Optional[float] = Field(
        default=None, description="该节点纬度（home 可为 null，由前端从 user profile 取）"
    )
    lng: Optional[float] = Field(default=None, description="该节点经度")
    address: Optional[str] = Field(default=None, description="详细地址 / 地名")


class Hop(BaseModel):
    """通勤段：从 from_node 出发抵达 to_node 的过程。

    长度恒等于 len(nodes) - 1。同地（in_place）也用一个 minutes=0 的 hop 占位，
    避免下游遍历 hops 时出现索引错位。
    """

    model_config = ConfigDict(extra="forbid")

    hop_id: str = Field(..., description='通勤段稳定 id，如 "h_0"')
    from_node_id: str = Field(..., description="起点节点 node_id")
    to_node_id: str = Field(..., description="终点节点 node_id")
    start_time: str = Field(..., description='离开 from_node 的时刻 HH:MM')
    minutes: NonNegativeInt = Field(
        ..., description="通勤分钟；0 表示同地（in_place）"
    )
    mode: HopMode = Field(
        ...,
        description="通勤方式：walking / taxi / bus / haversine_estimated / virtual",
    )
    path_type: HopPathType = Field(
        ..., description="路径类型：real_route / estimated / in_place"
    )
    buffer_min: NonNegativeInt = Field(
        default=5,
        description="目标节点 start_time 与 (hop.start_time + minutes) 的缓冲分钟，默认 5",
    )


# ============================================================
# 派生视图
# ============================================================


class ScheduleEntry(BaseModel):
    """派生只读视图：把 nodes + hops 按时间序展平的一行。

    本对象**不**作为输入字段，由 agent 侧（assemble / executor）按业务规则
    从 nodes + hops 推导生成；前端时间轴直接消费 schedule，不再二次聚合。
    """

    model_config = ConfigDict(extra="forbid")

    entry_kind: Literal["node", "hop"] = Field(
        ..., description="本行类型：node（停留）/ hop（通勤）"
    )
    ref_id: str = Field(..., description="对应 ActivityNode.node_id 或 Hop.hop_id")
    start: str = Field(..., description='起始时刻 "HH:MM"')
    end: str = Field(..., description='结束时刻 "HH:MM"')
    title: str = Field(..., description="时间轴上显示的一行文字")
    minutes: int = Field(..., description="本段时长（分钟）；node=duration_min，hop=minutes")
    mode: Optional[HopMode] = Field(
        default=None, description="entry_kind='hop' 时填，方便前端图标"
    )
    hidden: bool = Field(
        default=False,
        description="是否在主时间轴隐藏（in_place / virtual hop 默认 True，避免视觉冗余）",
    )


# ============================================================
# 订单 / 行程主体
# ============================================================


class OrderRecord(BaseModel):
    """已为你预留清单中的一条。"""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(..., description='形如 "R20260507_001"')
    kind: str = Field(..., description="餐厅预约 / 门票 / 加购服务 之一")
    target_kind: Literal["poi", "restaurant"] = Field(
        ...,
        description='订单目标类型，限定 poi / restaurant；与 ActivityNode.target_kind 对齐（不含 home）',
    )
    target_id: str = Field(..., description="对应 poi_id 或 restaurant_id")
    target_name: str
    detail: str = Field(..., description='如 "17:00 三人位"')


class PendingAction(BaseModel):
    """规划期预生成的「确认时要执行的一次工具调用」（工具前移 · spec dialogue-act-routing）。

    plan-and-execute 分离：规划期就把 confirm 要调的工具 + 参数全定死挂进方案，
    confirm 时直接 replay（invoke_tool(tool, args)），不再调 LLM、不再从 intent 现算。
    好处：省一次 LLM、执行与所见一致、target_id 规划期锁死（天然防 confirm 时编造方案外目标）。
    """

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(
        ...,
        description='工具名，如 "reserve_restaurant" / "buy_ticket" / "order_extra_service" / "generate_share_message"',
    )
    args: dict[str, Any] = Field(
        ..., description="该工具的输入参数（规划期已定死，含 target_id / 时间 / 人数 / 加购项等）"
    )
    label: str = Field(
        default="", description='给前端 / trace 的人话标签，如「餐厅预约 · 轻语沙拉」'
    )


class Itinerary(BaseModel):
    """完整方案（edge_v1）。

    前端按此渲染：
    - schedule（派生视图） → 时间轴
    - orders → 已为你预留清单
    - share_message → 一键复制转发文案
    - decision_trace → AI 思考折叠卡

    `nodes + hops` 是「源真值」（生产者写入），`schedule` 是派生视图（消费侧读）。
    invariant 校验仅强约束 nodes/hops；schedule 由下游 builder 填充，本 schema 不校验其
    与 nodes/hops 的一致性（避免在初次 model_validate 时强制下游必须先 build schedule）。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["edge_v1"] = Field(
        default="edge_v1",
        description='schema 版本号；当前固定 "edge_v1"，运行时不再支持 legacy_v0',
    )
    summary: str = Field(..., description="一句话方案摘要，如「家庭半日方案」")
    nodes: list[ActivityNode] = Field(
        ...,
        min_length=2,
        description="按时间排序的活动节点；至少 2 个（home → ... → home）",
    )
    hops: list[Hop] = Field(
        ...,
        description="相邻节点之间的通勤段；长度恒等于 len(nodes) - 1",
    )
    schedule: list[ScheduleEntry] = Field(
        default_factory=list,
        description="派生只读视图（生产者按需填充；消费者直接渲染）",
    )
    orders: list[OrderRecord] = Field(
        default_factory=list, description="已为你预留清单"
    )
    pending_actions: list[PendingAction] = Field(
        default_factory=list,
        description=(
            "规划期预生成的「确认动作清单」（工具前移）；confirm 时直接 replay，"
            "不再读 intent。为空时 confirm 退回旧的「从 intent 现算」逻辑（向后兼容）。"
        ),
    )
    share_message: Optional[str] = Field(
        default=None, description="可一键复制的转发文案"
    )
    total_minutes: NonNegativeInt = Field(
        ..., description="总耗时（分钟）；用于校验 4-6h 约束"
    )
    decision_trace: Optional[DecisionTrace] = Field(
        default=None,
        description=(
            "Agent 决策可解释性元数据（Step 4+7）。"
            "包含 blueprint rationale / 权重解释 / critic 修正历史 / "
            "考虑过的备选方案 / fallback 链。"
            "前端 DecisionTraceCard 默认折叠；None 或 is_empty() 时隐藏卡片。"
        ),
    )

    @model_validator(mode="after")
    def _check_invariants(self) -> "Itinerary":
        """edge_v1 五条不变量。"""
        # 1. hops 长度 = nodes - 1
        expected_hops = len(self.nodes) - 1
        if len(self.hops) != expected_hops:
            raise ValueError(
                f"hops 长度 {len(self.hops)} 应等于 nodes 长度 - 1 = {expected_hops}"
            )

        # 2. 首节点必为 home
        first = self.nodes[0]
        if first.target_kind != "home":
            raise ValueError(
                f"nodes[0] 必须是 home 节点（实际 target_kind={first.target_kind!r}）"
            )

        # 3. 尾节点必为 home
        last = self.nodes[-1]
        if last.target_kind != "home":
            raise ValueError(
                f"nodes[-1] 必须是 home 节点（实际 target_kind={last.target_kind!r}）"
            )

        # 4. home 节点 duration_min = 0
        if first.duration_min != 0:
            raise ValueError(
                f"nodes[0]（home）duration_min 必须为 0（实际 {first.duration_min}）"
            )
        if last.duration_min != 0:
            raise ValueError(
                f"nodes[-1]（home）duration_min 必须为 0（实际 {last.duration_min}）"
            )

        # 5. home 节点 target_id 固定 "home"
        if first.target_id != "home":
            raise ValueError(
                f'nodes[0]（home）target_id 必须为 "home"（实际 {first.target_id!r}）'
            )
        if last.target_id != "home":
            raise ValueError(
                f'nodes[-1]（home）target_id 必须为 "home"（实际 {last.target_id!r}）'
            )

        return self
