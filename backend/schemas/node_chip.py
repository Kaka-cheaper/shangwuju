"""schemas.node_chip —— 「定向调整按钮」的下发 + 回传载荷（ADR-0013 F-3）。

【这是什么问题】

ADR-0013 决策 4「节点交互三元素」把「定向调整按钮」定义为**结构化指令**，
绝不过 LLM 路由：用户在某个节点下方看到最多 3 个按钮（如「更便宜的」「安静点
的」），点一下就要能直接喂给 `agent.planning.planners.node_swap.resolve_node_
swap(target_node_id=..., adjustment=...)`，不需要任何自然语言理解。`NodeChip`
就是这条契约的**下发形状**：narrate 阶段（LLM 搭车产出或按 kind 模板兜底，
见 `agent.intent.narrator`）生成它、随 `ITINERARY_READY` 的 `node_actions`
兄弟字段推给前端；F-4 的点击回传就是原样把它（或至少 `node_id` + `adjustment`
两个字段）传回后端——不是另起一套点击 payload，展示与点击同一份形状。

【字段设计（为什么是这三个、不多不少）】

- `node_id`：**`ActivityNode.target_id`**（POI/Restaurant 的实体 id），
  **不是** `ActivityNode.node_id`（"n_0" 这种结构化定位 id）。选它的理由：
  `resolve_node_swap`/`feasible_alternatives` 的 `target_node_id` 形参本身
  就是按 `node.target_id` 匹配（见 `agent.planning.planners.node_swap.
  _find_target_node`），F-4 拿到 `NodeChip` 后可以**直接**把 `node_id` 透传
  进 `target_node_id`，不需要先拿它反查 `ActivityNode.node_id` 再翻回
  `target_id`——省一次没有必要的间接层，也避免 replan 之后 `node_id`（"n_0"）
  漂移但 `target_id`（实体 id）不变这种边界情况带来的对不上号。
- `label`：给用户看的按钮文案，**≤8 字**（卡片按钮的排版宽度约束，ADR-0013
  决策 4 原文点名的具体规格）；`Field(max_length=8)` 是硬校验，不是建议——
  LLM 产出超限或 `dimension`/`value` 不在受控词典时，消费方（`agent.intent.
  narrator._validate_llm_node_chips`）据此判定"这条不合法"，整体回落模板
  生成器（"不半信半用"，见该函数 docstring），不在这里做静默截断（截断会
  制造"看起来合法但其实是被裁剪过的"假象，与 D-7"绝不默默忽略"同一纪律）。
- `adjustment`：复用 `schemas.node_adjustment.NodeAdjustment`（dimension +
  value），不重新发明一套平行结构——与 F-1 的 `resolve_node_swap`/
  `ledger_slice` 消费的是同一个模型，三方（按钮展示 / 点击换菜 / 诉求台账）
  对齐同一份契约（见 `schemas/node_adjustment.py` 模块 docstring）。

**不含**：方案/节点的其它上下文（如目标节点当前 title/kind）——那些是展示
时前端自己从 itinerary.nodes 里查的，`NodeChip` 只携带"点了要做什么"，不携带
"点之前长什么样"（与 `NodeAdjustment` 本身"不含节点定位"同一设计取向，见该
模块 docstring）。

不负责：
- chip 的生成规则（模板生成器 / LLM 搭车解析，均在 `agent.intent.narrator`）。
- 具名备选（`AlternativeOption`，另一套形状，在 `agent.planning.planners.
  node_swap`）——chip 是"定向调整按钮"，备选是"具名候选列表"，ADR-0013 决策
  4 明确两者是节点行的左右两栏，不共用一个模型。
- 点击后的路由/接线（F-4）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from schemas.node_adjustment import NodeAdjustment


class NodeChip(BaseModel):
    """一个「定向调整按钮」的下发/回传载荷——展示与点击同一份形状。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(
        ...,
        min_length=1,
        description=(
            "ActivityNode.target_id（POI/Restaurant 实体 id），与 "
            "resolve_node_swap(target_node_id=...) 同一口径，可直接透传"
        ),
    )
    label: str = Field(
        ...,
        min_length=1,
        max_length=8,
        description="给用户看的按钮文案，≤8 字（卡片按钮排版约束，硬校验）",
    )
    adjustment: NodeAdjustment = Field(
        ..., description="点击后要执行的定向调整——维度 + 取值，复用 F-1 同一契约"
    )


__all__ = ["NodeChip"]
