/**
 * 诉求台账人话化 helper——供 PreferencesPanel（桌面/移动共用）"本次调整"区
 * 消费，`ConstraintFeed.tsx` 的房间约束栏渲染不复用本文件（那边是纯房间
 * 约束栏，见该文件顶部 docstring；本次改动把台账收编进偏好面板，
 * `ConstraintFeed.tsx` 删除 demandLedger 渲染分支，退回纯房间约束栏）。
 *
 * 【这是什么问题】
 * `DemandLedgerEntry.value` 对 PRICE/DISTANCE 两个方向词维度是英文方向词
 * （"cheaper"/"pricier"/"closer"/"farther"，见 schemas/node_adjustment.py），
 * 直接渲染会漏出英文词（"价格：cheaper"）——不是人话。后端
 * `agent/context/types.py::ledger_value_clause` 已经解过这个问题（LLM
 * 上下文拼句场景），本文件把同一份措辞族搬到前端展示场景，词表保持同步
 * （不发明第三套词）：
 *   ("price","cheaper")→"更便宜" / ("price","pricier")→"更高档"
 *   ("distance","closer")→"更近" / ("distance","farther")→"更远"
 * 目标值维度（dietary/ambience/crowd_fit/cuisine_or_type）的 value 本身已是
 * 中文词典词/自由文本，不需要方向词映射，直接展示。
 *
 * 词典外的未知组合（枚举扩员但前端词表未同步）**不崩、不瞎猜**——退化为
 * "维度→原值"原样打印 + console.warn 一条（H3 哨兵同款纪律：能在控制台看到
 * "台账映射缺了一个词"，而不是静默展示错误的英文单词或崩渲染）。
 *
 * id→店名映射（UI 修复批·台账店名快照）：优先读 `node_ref.title`——后端在
 * **记账时刻**（`graph_adjust.py`/`room.py` 构造 `NodeRef` 那一刻）就把当时的
 * `node_title(itinerary, target_id)` 定格快照进这个字段，往后即便节点被换菜
 * （`resolve_node_swap` 换菜成功后 `itinerary.nodes` 里该位置的 `target_id`
 * 变成新实体、旧 id 从当前方案里彻底消失），台账历史行依然显示"当初是哪个
 * 店"，不退化成裸 id（如 `WJP062`）——这是台账"不压扁历史"的产品承诺真正
 * 兑现的地方。`title` 为 `null`（本字段新增前已落盘的旧条目，没有快照）时才
 * 退回旧路径：反查当前 `itinerary.nodes`（`ConstraintFeed.tsx::nodeTitleByTargetId`
 * 同一先例），查不到再兜底显示原始 target_id，不留空。
 */

import type { DemandLedgerEntry, Itinerary, NodeAdjustmentDimension, NodeRef } from "./types";

const _DIMENSION_LABELS: Record<NodeAdjustmentDimension, string> = {
  price: "价格",
  distance: "距离",
  cuisine_or_type: "类型",
  dietary: "口味",
  ambience: "氛围",
  crowd_fit: "适配",
};

/** 方向词维度的人话短语——与 `agent/context/types.py::_DIMENSION_VALUE_PHRASES`
 * 同一措辞族，不发明第三套词。 */
const _DIMENSION_VALUE_PHRASES: Record<string, string> = {
  "price:cheaper": "更便宜",
  "price:pricier": "更高档",
  "distance:closer": "更近",
  "distance:farther": "更远",
};

/** 台账一条的"调了什么"人话短语。词典外组合兜底原词 + console.warn（不崩）。 */
export function ledgerValuePhrase(entry: DemandLedgerEntry): string {
  const key = `${entry.dimension}:${entry.value}`;
  const phrase = _DIMENSION_VALUE_PHRASES[key];
  if (phrase) return phrase;
  if (entry.dimension === "price" || entry.dimension === "distance") {
    // 方向词维度但没命中词表——真正的"漏词"场景，需要留痕排查。
    console.warn(`[ledger-copy] 方向词维度缺映射，原样展示：${key}`);
  }
  const label = _DIMENSION_LABELS[entry.dimension] ?? entry.dimension;
  return `${label}「${entry.value}」`;
}

/** 按 target_id 查节点店名/标题；查不到兜底原始 id（同 ConstraintFeed.tsx
 * 既有先例 `nodeTitleByTargetId`，不重复但保持同一语义）。仅作 `node_ref.title`
 * 快照缺失（旧数据）时的退路——见本文件顶部 docstring。 */
export function nodeNameByTargetId(
  itinerary: Itinerary | null | undefined,
  targetId: string,
): string {
  const node = itinerary?.nodes?.find((n) => n.target_id === targetId);
  return node?.title ?? targetId;
}

/** 台账一条引用的节点店名——优先快照，快照缺失（旧数据）才反查当前方案。 */
function nodeDisplayName(
  nodeRef: NodeRef,
  itinerary: Itinerary | null | undefined,
): string {
  return nodeRef.title ?? nodeNameByTargetId(itinerary, nodeRef.target_id);
}

/** 台账一条的完整人话句子——"（谁）· 店名/全局 · 调了什么"，不含状态徽标
 * （状态由调用方按 entry.status 单独渲染徽标，保持"文案"与"状态视觉"分离）。 */
export function ledgerEntryLine(
  entry: DemandLedgerEntry,
  itinerary: Itinerary | null | undefined,
): string {
  const where = entry.node_ref ? nodeDisplayName(entry.node_ref, itinerary) : "全局";
  const who = entry.nickname ? `${entry.nickname} · ` : "";
  return `${who}${where} · ${ledgerValuePhrase(entry)}`;
}
