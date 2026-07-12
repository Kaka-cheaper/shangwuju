/**
 * 协作房间「约束流 + 台账」合并展示流（约束流合并 A1）。
 *
 * 【这是什么问题】
 * 房间里有两条独立的写侧数据流：
 * - `useCollabStore.constraints`（`CollabConstraint[]`）——自由打字/点踩广播/
 *   指名换店留痕的原始事件流，天然带 `user_id`/`nickname`/`text`/`timestamp`，
 *   不带"这条诉求现在生效没有"这个状态（它不是诉求，是一句话/一个动作）。
 * - `useChatStore.demandLedger`（`DemandLedgerEntry[]`）——诉求台账，天然带
 *   `member_id`/`nickname`/`dimension`/`value`/`status`（生效中/已满足/被顶替），
 *   只在用户点「定向调整按钮」时产生（`AdjustActionAdjust`）。
 *
 * 两者由完全不同、互斥的产生路径写入（打字/点踩 vs 点定向调整按钮），彼此
 * 之间**没有共享的 join key**、也不该发明一个——一段文字要等到被 refiner
 * 转译成一次结构化调整才会在台账里留痕，这个转译过程本身不确定、无反向
 * 指针，试图按内容/时间做模糊匹配等于系统在瞎猜哪条约束对应哪次调整，一
 * 旦猜错就是"展示层撒谎"（这与本代码库"诚实红线：只报真值，不美化、不
 * 补占位"的既有原则冲突）。
 *
 * 【成熟做法】这不是"两个数据源要 join"，是 activity feed 里标准的异构
 * 条目合并（heterogeneous / mixed-type feed items，见 getstream.io 对
 * flat feed 的定义：不折叠同类项，只按时间戳交织展示不同类型的条目，靠
 * 视觉/字段差异区分类型，而不是让每种条目都长出对方的字段）。本模块的
 * `mergeCollabFeed` 正是这个"按时间戳交织，各自保留原生字段"的读侧投影：
 * - 约束流来源的行：永远没有状态徽标（它的语义是"原话/动作"，不是"诉求
 *   生效状态"）。
 * - 台账来源的行：永远有状态徽标（active/superseded/satisfied，
 *   superseded 按既有惯例过滤掉不展示，同 `PreferencesPanel.tsx`/
 *   `MobileHomeView.tsx`（旧）/`UserSwitcher.tsx` 三处既有台账渲染的
 *   `.filter(e => e.status !== "superseded")` 先例）。
 * - 指名换店留痕（`CollabConstraint.source === "alternative_swap"`）单独
 *   处理：它结构上来自约束流（后端直接 append 进 `room.constraints`，绕开
 *   台账 schema——见 `collab/room.py::_resolve_and_broadcast_adjust` 对应
 *   分支注释，塞进 `NodeAdjustment` 会有幽灵约束污染候选池的真实风险），
 *   但语义上"已经是成事实"，展示层把它渲染成**带"已满足"徽标**的一行——
 *   这是本合并流对任务书"每条约束带上应用状态+归名"要求里，约束流来源
 *   条目唯一"天生就有确定状态"的例外（因为它不是待处理的诉求，是已完成
 *   的动作），不是"约束流条目也要有状态"这个规则的反例。
 */

import { ledgerEntryLine } from "./ledger-copy";
import type { CollabConstraint } from "./collab-store";
import type { DemandLedgerEntry, Itinerary } from "./types";

export type CollabFeedStatus = "active" | "satisfied";

/**
 * 合并流的一行——约束流来源（`kind: "constraint"`）或台账来源
 * （`kind: "ledger"`），前端渲染时按 `kind` 决定要不要出状态徽标。
 *
 * `attribution` 字段是否独立于 `text` 展示，两种来源不对称，这是刻意的、
 * 不是疏漏：
 * - 约束流来源：`CollabConstraint.text` 是原始输入/动作描述，天生不含
 *   归名，`attribution` 必须单独渲染在 `text` 前面（如"小明：换成了X店"）。
 * - 台账来源：复用既有 `ledger-copy.ts::ledgerEntryLine`（`PreferencesPanel.
 *   tsx`/`UserSwitcher.tsx` 等三处既有台账渲染的同一个 helper），它的既有
 *   契约是"整句话已经把归名焊进返回的字符串开头"（`who = entry.nickname
 *   ? "${entry.nickname} · " : ""`）——这不是本次改动引入的新约定，是这个
 *   helper 从诞生起就有的形状。若再单独渲染一次 `attribution`，会变成
 *   "小明：小明 · 全局 · 更近"这种重复。所以台账行的 `attribution` 留空
 *   （展示层判断 `attribution` 为空就不再渲染独立归名前缀），完整归名+内容
 *   已经在 `text` 里。
 */
export interface CollabFeedRow {
  kind: "constraint" | "ledger";
  key: string;
  /** 约束流行：谁提的/谁做的，展示层单独渲染在 `text` 前。台账行：留空
   * 字符串——归名已经焊在 `text` 开头（见本接口 docstring），展示层按
   * `attribution` 是否非空决定要不要单独渲染这段前缀。 */
  attribution: string;
  text: string;
  /** 状态徽标——只有 `kind==="ledger"` 或指名换店留痕（视为已满足）才有；
   * 真实约束流条目（打字/点踩）恒为 `null`，展示层据此不渲染徽标位。 */
  status: CollabFeedStatus | null;
  timestamp: number;
}

function constraintAttribution(c: CollabConstraint): string {
  return c.nickname || c.user_id;
}

/**
 * 把约束流 + 台账合并成一条按时间戳升序排列的展示行数组。
 *
 * @param constraints 房间约束流（`useCollabStore.constraints`）。
 * @param ledger 诉求台账（`useChatStore.demandLedger`），传 `null`/`undefined`
 *   等价于空数组（初次加载/尚无台账时的既有防御性写法，同各既有消费点）。
 * @param itinerary 台账人话化需要它解节点店名快照缺失时的兜底反查
 *   （`ledgerEntryLine` 既有签名，同 `PreferencesPanel.tsx` 既有调用）。
 */
export function mergeCollabFeed(
  constraints: CollabConstraint[],
  ledger: DemandLedgerEntry[] | null | undefined,
  itinerary: Itinerary | null | undefined,
): CollabFeedRow[] {
  const constraintRows: CollabFeedRow[] = constraints.map((c, i) => ({
    kind: "constraint",
    key: `c-${c.user_id}-${c.timestamp}-${i}`,
    attribution: constraintAttribution(c),
    text:
      c.source === "alternative_swap"
        ? c.text
        : c.source === "vote_dislike"
          ? `👎 ${c.text}`
          : c.text,
    // 指名换店留痕虽然物理上存在约束流里，语义上是"已完成的动作"——给它
    // 一个确定的"已满足"徽标，让合并流对这一种此前唯一没有状态展示的入口
    // 也做到"归名+状态一眼可见"（见本文件头部 docstring）。真实打字/点踩
    // 约束没有这个语义，状态位保持 null。
    status: c.source === "alternative_swap" ? "satisfied" : null,
    timestamp: c.timestamp,
  }));

  const visibleLedger = (ledger ?? []).filter((e) => e.status !== "superseded");
  const ledgerRows: CollabFeedRow[] = visibleLedger.map((entry, i) => ({
    kind: "ledger",
    key: `l-${entry.created_at}-${i}`,
    // 留空——`ledgerEntryLine` 已经把归名焊进 text 开头，见 CollabFeedRow
    // 接口 docstring；这里再填一次会重复展示同一个昵称。
    attribution: "",
    text: ledgerEntryLine(entry, itinerary),
    status: entry.status === "satisfied" ? "satisfied" : "active",
    timestamp: entry.created_at,
  }));

  return [...constraintRows, ...ledgerRows].sort((a, b) => a.timestamp - b.timestamp);
}
